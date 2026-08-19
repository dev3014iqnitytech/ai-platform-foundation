"""
FastAPI Dependency Injection — Database, Redis, Auth
All dependencies are async-compatible and designed for
testability via override in conftest.py.
"""
from typing import Annotated, AsyncGenerator

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.core.config import Settings, get_settings
from app.core.security import TokenValidationError, validate_azure_token

logger = get_logger(__name__)
bearer_scheme = HTTPBearer(auto_error=True)


# ─────────────────────────────────────────────────────────────────────────────
# Settings DI
# ─────────────────────────────────────────────────────────────────────────────
def get_settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


# ─────────────────────────────────────────────────────────────────────────────
# Database Session
# ─────────────────────────────────────────────────────────────────────────────
async def get_db_session(
    settings: SettingsDep,
) -> AsyncGenerator[AsyncSession, None]:
    """Yields an async SQLAlchemy session, auto-rolled-back on error."""
    from app.infrastructure.database.session import async_session_factory

    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


# ─────────────────────────────────────────────────────────────────────────────
# Redis Client
# ─────────────────────────────────────────────────────────────────────────────
_redis_pool: aioredis.Redis | None = None


async def get_redis(settings: SettingsDep) -> aioredis.Redis:
    """
    Returns a shared Redis connection.
    When REDIS_URL=redis://fakeredis the in-memory fakeredis is used —
    no Redis server required (useful for LOCAL_MODE / CI).
    """
    global _redis_pool
    if _redis_pool is None:
        url = settings.redis_url_str
        if url.startswith("redis://fakeredis"):
            import fakeredis.aioredis as fakeredis_async
            _redis_pool = fakeredis_async.FakeRedis(decode_responses=True)
            logger.info("redis_using_fakeredis")
        else:
            _redis_pool = aioredis.from_url(
                url,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                decode_responses=True,
            )
    return _redis_pool


RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]


# ─────────────────────────────────────────────────────────────────────────────
# Azure AD Authentication — Extracts and validates JWT
# ─────────────────────────────────────────────────────────────────────────────
async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials, Security(bearer_scheme)
    ],
    request: Request,
) -> dict:
    """
    Validates the Azure AD Bearer token and returns decoded claims.
    Sets request.state.user_id for downstream middleware and logging.
    """
    try:
        claims = await validate_azure_token(credentials.credentials)
        # Inject user identity into request state for audit/rate-limit middleware
        request.state.user_id = claims.get("oid", claims.get("sub", "unknown"))
        request.state.user_email = claims.get("preferred_username", "unknown")
        request.state.user_roles = claims.get("roles", [])
        return claims
    except TokenValidationError as e:
        logger.warning("auth_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


CurrentUserDep = Annotated[dict, Depends(get_current_user)]


# ─────────────────────────────────────────────────────────────────────────────
# SSE-specific auth — accepts token as query param (EventSource can't set headers)
# ─────────────────────────────────────────────────────────────────────────────
async def get_sse_user(
    request: Request,
    token: str | None = None,
) -> dict:
    """
    Auth dependency for Server-Sent Events endpoints.
    Accepts the Bearer token from the `token` query parameter as a fallback
    when the Authorization header is absent (EventSource limitation).
    """
    # Prefer Authorization header
    auth_header = request.headers.get("Authorization", "")
    raw_token: str | None = None
    if auth_header.startswith("Bearer "):
        raw_token = auth_header[7:]
    elif token:
        raw_token = token

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = await validate_azure_token(raw_token)
        request.state.user_id = claims.get("oid", claims.get("sub", "unknown"))
        request.state.user_email = claims.get("preferred_username", "unknown")
        request.state.user_roles = claims.get("roles", [])
        return claims
    except TokenValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


SSEUserDep = Annotated[dict, Depends(get_sse_user)]


# ─────────────────────────────────────────────────────────────────────────────
# RBAC Authorization — Role-based permission checks
# ─────────────────────────────────────────────────────────────────────────────
class RequireRoles:
    """
    Dependency that enforces one or more required roles from Azure AD claims.
    Usage: Depends(RequireRoles("qa_manager", "system_admin"))
    """

    def __init__(self, *required_roles: str):
        self.required_roles = set(required_roles)

    async def __call__(self, user: CurrentUserDep) -> dict:
        user_roles = set(user.get("roles", []))
        if not user_roles & self.required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required roles: {self.required_roles}",
            )
        return user


class RequirePermission:
    """
    Dependency for fine-grained permission checks.
    Maps Azure AD roles to internal permissions using a policy engine.
    Usage: Depends(RequirePermission("test_cases:approve"))
    """

    # Role → Permission mapping
    PERMISSION_MAP: dict[str, set[str]] = {
        "system_admin": {
            "test_cases:generate", "test_cases:approve", "test_cases:reject",
            "ado:update", "kb:manage", "kb:upload", "audit:view",
            "prompts:manage", "agents:configure", "users:manage",
        },
        "qa_manager": {
            "test_cases:generate", "test_cases:approve", "test_cases:reject",
            "ado:update", "kb:manage", "kb:upload", "audit:view",
            "prompts:manage",
        },
        "senior_tester": {
            "test_cases:generate", "test_cases:approve", "test_cases:reject",
            "ado:update", "kb:upload", "audit:view",
        },
        "tester": {
            "test_cases:generate",
        },
        "developer": {
            "test_cases:generate", "audit:view",
        },
        "approver": {
            "test_cases:approve", "test_cases:reject", "ado:update", "audit:view",
        },
        "architect": {
            "test_cases:generate", "kb:manage", "kb:upload", "audit:view",
            "prompts:manage", "agents:configure",
        },
        "read_only": {
            "audit:view",
        },
    }

    def __init__(self, permission: str):
        self.permission = permission

    async def __call__(self, user: CurrentUserDep) -> dict:
        user_roles = user.get("roles", [])
        user_permissions: set[str] = set()
        for role in user_roles:
            user_permissions |= self.PERMISSION_MAP.get(role, set())

        if self.permission not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{self.permission}' is required.",
            )
        return user
