"""
Rate Limiter — Redis-backed sliding window rate limiter.
Applies per-user, per-endpoint limits with configurable windows.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from structlog import get_logger

logger = get_logger(__name__)


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: float          # Unix timestamp when window resets
    retry_after: int | None  # Seconds to wait if blocked

    @property
    def headers(self) -> dict[str, str]:
        """Standard RateLimit response headers (RFC 6585)."""
        h = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(int(self.reset_at)),
        }
        if self.retry_after is not None:
            h["Retry-After"] = str(self.retry_after)
        return h


# Default limits per endpoint group
DEFAULT_LIMITS: dict[str, tuple[int, int]] = {
    # (max_requests, window_seconds)
    "generate": (20, 60),          # 20 generations/min
    "approve": (100, 60),          # 100 approvals/min
    "knowledge": (50, 60),         # 50 KB ops/min
    "admin": (30, 60),             # 30 admin ops/min
    "default": (200, 60),          # 200 req/min general
}


class RateLimiter:
    """
    Sliding window rate limiter using Redis sorted sets.
    Falls back to in-memory (non-distributed) if Redis is unavailable.
    """

    def __init__(self, redis_url: str | None = None):
        self._redis = None
        self._in_memory: dict[str, list[float]] = {}  # fallback
        self._redis_available = False
        if redis_url:
            self._try_connect(redis_url)

    def _try_connect(self, redis_url: str) -> None:
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(redis_url, decode_responses=True)
            self._redis_available = True
            logger.info("rate_limiter_redis_connected")
        except Exception as e:
            logger.warning("rate_limiter_redis_failed", error=str(e), fallback="in-memory")

    async def check(
        self,
        user_id: str,
        endpoint_group: str = "default",
        custom_limit: int | None = None,
        custom_window: int | None = None,
    ) -> RateLimitResult:
        limit, window = DEFAULT_LIMITS.get(endpoint_group, DEFAULT_LIMITS["default"])
        if custom_limit:
            limit = custom_limit
        if custom_window:
            window = custom_window

        key = f"rate_limit:{user_id}:{endpoint_group}"
        now = time.time()
        window_start = now - window
        reset_at = now + window

        if self._redis_available and self._redis:
            return await self._check_redis(key, now, window_start, window, limit, reset_at)
        return self._check_memory(key, now, window_start, window, limit, reset_at)

    async def _check_redis(
        self, key: str, now: float, window_start: float, window: int, limit: int, reset_at: float
    ) -> RateLimitResult:
        try:
            pipe = self._redis.pipeline()
            # Remove expired entries
            pipe.zremrangebyscore(key, 0, window_start)
            # Count current entries
            pipe.zcard(key)
            # Add current request
            pipe.zadd(key, {str(now): now})
            # Set TTL
            pipe.expire(key, window + 1)
            results = await pipe.execute()

            current_count = results[1]
            allowed = current_count < limit
            remaining = max(0, limit - current_count - 1)

            if not allowed:
                logger.warning("rate_limit_exceeded", key=key, count=current_count, limit=limit)

            return RateLimitResult(
                allowed=allowed,
                limit=limit,
                remaining=remaining,
                reset_at=reset_at,
                retry_after=window if not allowed else None,
            )
        except Exception as e:
            logger.error("rate_limiter_redis_error", error=str(e))
            return RateLimitResult(allowed=True, limit=limit, remaining=limit, reset_at=reset_at, retry_after=None)

    def _check_memory(
        self, key: str, now: float, window_start: float, window: int, limit: int, reset_at: float
    ) -> RateLimitResult:
        timestamps = self._in_memory.get(key, [])
        timestamps = [t for t in timestamps if t > window_start]
        timestamps.append(now)
        self._in_memory[key] = timestamps[-limit - 1:]  # Trim

        count = len(timestamps)
        allowed = count <= limit
        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=max(0, limit - count),
            reset_at=reset_at,
            retry_after=window if not allowed else None,
        )


# Module-level singleton
_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        try:
            from app.core.config import settings
            _limiter = RateLimiter(redis_url=settings.REDIS_URL)
        except Exception:
            _limiter = RateLimiter()
    return _limiter
