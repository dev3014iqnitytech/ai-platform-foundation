"""
aegis_ai.guardrails.rate_limiter
===================================
Sliding-window rate limiter — OWASP LLM04 (Model DoS).

Primary: Redis sliding window (distributed, cluster-safe)
Fallback: In-memory sliding window (single-instance only)

Rate limits applied per:
- Identity ID (user/service account)
- Agent ID
- Tenant ID (burst protection)

OWASP: LLM04-Model Denial of Service
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

import structlog

from aegis_ai.exceptions import RateLimitExceededError
from aegis_ai.settings import AegisSettings
from aegis_ai.types import RateLimitResult

logger = structlog.get_logger(__name__)


class _InMemoryWindow:
    """Single-instance in-memory sliding window per key."""

    def __init__(self) -> None:
        self._windows: Dict[str, Deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        from datetime import datetime, timezone
        now = time.monotonic()
        cutoff = now - window_seconds

        async with self._lock:
            if key not in self._windows:
                self._windows[key] = deque()
            window = self._windows[key]

            # Evict expired timestamps
            while window and window[0] <= cutoff:
                window.popleft()

            count = len(window)
            if count >= limit:
                oldest = window[0]
                reset_in = int(oldest - cutoff) + 1
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    reset_at=datetime.fromtimestamp(
                        time.time() + reset_in, tz=timezone.utc
                    ),
                    retry_after_seconds=reset_in,
                )

            window.append(now)
            return RateLimitResult(
                allowed=True,
                remaining=limit - count - 1,
                reset_at=datetime.fromtimestamp(
                    time.time() + window_seconds, tz=timezone.utc
                ),
                retry_after_seconds=0,
            )


class RateLimiter:
    """
    Rate limiter with Redis primary and in-memory fallback.

    Uses a sliding window algorithm for accurate rate tracking.
    Redis implementation uses sorted sets (ZRANGEBYSCORE + ZADD + ZREMRANGEBYSCORE).
    """

    def __init__(self, settings: AegisSettings, redis_client: Any = None) -> None:
        self._settings = settings
        self._redis = redis_client
        self._memory = _InMemoryWindow()

    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> RateLimitResult:
        """
        Check and record a rate-limited request.

        Args:
            key: Unique rate limit key (e.g., "identity:user-001").
            limit: Maximum requests allowed in window_seconds.
            window_seconds: Sliding window duration in seconds.

        Returns:
            RateLimitResult with allowed flag and remaining quota.
        """
        if not self._settings.rate_limit.enabled:
            from datetime import datetime, timezone
            return RateLimitResult(
                allowed=True,
                remaining=limit,
                reset_at=datetime.now(timezone.utc),
            )

        res = None
        if self._redis is not None:
            try:
                res = await self._redis_check(key, limit, window_seconds)
            except Exception as exc:
                logger.warning("rate_limiter_redis_failed", error=str(exc), fallback="memory")

        if res is None:
            if self._settings.rate_limit.use_in_memory_fallback:
                res = await self._memory.check(key, limit, window_seconds)
            else:
                from datetime import datetime, timezone
                logger.error("rate_limiter_fully_degraded", key=key)
                res = RateLimitResult(
                    allowed=True,
                    remaining=limit,
                    reset_at=datetime.now(timezone.utc),
                )

        return res

    async def _redis_check(
        self, key: str, limit: int, window_seconds: int
    ) -> RateLimitResult:
        """Redis sliding window using sorted sets."""
        from datetime import datetime, timezone
        now = time.time()
        window_start = now - window_seconds
        redis_key = f"aegis:rl:{key}"

        # Atomic pipeline: remove expired, count, add current
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zcard(redis_key)
        pipe.zadd(redis_key, {str(now): now})
        pipe.expire(redis_key, window_seconds + 1)
        results = await pipe.execute()

        count_before_add = int(results[1])

        if count_before_add >= limit:
            # We added unnecessarily — remove it
            await self._redis.zrem(redis_key, str(now))
            reset_at = now + window_seconds
            return RateLimitResult(
                allowed=False,
                remaining=0,
                reset_at=datetime.fromtimestamp(reset_at, tz=timezone.utc),
                retry_after_seconds=int(window_seconds),
            )

        return RateLimitResult(
            allowed=True,
            remaining=limit - count_before_add - 1,
            reset_at=datetime.fromtimestamp(now + window_seconds, tz=timezone.utc),
            retry_after_seconds=0,
        )
