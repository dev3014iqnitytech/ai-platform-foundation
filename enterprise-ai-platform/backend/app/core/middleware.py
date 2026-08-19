"""
Enterprise Middleware Stack
RequestId, Audit Logging, Rate Limiting, Security Headers.
All middleware is ASGI-compatible and runs on every request.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from structlog import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Request ID — Correlation header for distributed tracing
# ─────────────────────────────────────────────────────────────────────────────
class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Injects a unique X-Request-Id into every request/response for
    end-to-end correlation across services and log aggregation.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(
            "X-Request-Id", str(uuid.uuid4())
        )
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


# ─────────────────────────────────────────────────────────────────────────────
# Audit Middleware — Logs every API call for compliance
# ─────────────────────────────────────────────────────────────────────────────
class AuditMiddleware(BaseHTTPMiddleware):
    """
    Structured audit log for every HTTP request.
    Captures: method, path, user, IP, status, latency.
    Writes to structlog → forwarded to Azure Monitor.
    """

    SKIP_PATHS = {"/health", "/ready", "/api/docs", "/api/openapi.json", "/favicon.ico"}

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        start_time = time.perf_counter()
        user_id = getattr(request.state, "user_id", "anonymous")
        request_id = getattr(request.state, "request_id", "unknown")

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            user_id=user_id,
            request_id=request_id,
            client_ip=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent", ""),
        )
        return response


# ─────────────────────────────────────────────────────────────────────────────
# Rate Limiting — Redis-backed sliding window
# ─────────────────────────────────────────────────────────────────────────────
class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Redis-backed rate limiter with configurable per-user limits.
    Returns 429 Too Many Requests when limit exceeded.
    Identified by Azure AD `oid` claim or client IP as fallback.
    """

    def __init__(self, app: Any, redis_url: str | None = None) -> None:
        super().__init__(app)
        self.redis_url = redis_url
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self.redis_url or "redis://localhost:6379/0",
                decode_responses=True,
            )
        return self._redis

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip rate limiting for health checks
        if request.url.path in {"/health", "/ready"}:
            return await call_next(request)

        try:
            redis = await self._get_redis()
            identifier = getattr(request.state, "user_id", None)
            if not identifier:
                identifier = request.client.host if request.client else "unknown"

            key = f"rate_limit:{identifier}:{request.url.path}"
            current = await redis.incr(key)
            if current == 1:
                await redis.expire(key, 60)

            from app.core.config import settings
            limit = settings.RATE_LIMIT_REQUESTS_PER_MINUTE

            if current > limit:
                logger.warning(
                    "rate_limit_exceeded",
                    identifier=identifier,
                    path=request.url.path,
                    count=current,
                )
                return Response(
                    content='{"detail": "Rate limit exceeded. Retry after 60 seconds."}',
                    status_code=429,
                    media_type="application/json",
                    headers={"Retry-After": "60"},
                )

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(max(0, limit - current))
            return response

        except Exception:
            # Fail open — don't block requests if Redis is down
            logger.warning("rate_limiter_redis_unavailable", exc_info=True)
            return await call_next(request)


# ─────────────────────────────────────────────────────────────────────────────
# Security Headers — OWASP recommended
# ─────────────────────────────────────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Injects OWASP-recommended security headers on every response.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://*.openai.azure.com https://*.search.windows.net"
        )

        return response
