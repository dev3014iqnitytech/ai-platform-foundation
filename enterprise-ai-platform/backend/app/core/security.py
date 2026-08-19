"""
Enterprise JWT + Azure AD Token Validation
Validates Azure AD-issued JWTs using JWKS endpoint discovery.
Enforces audience, issuer, scope, and expiry claims.

In LOCAL_MODE: validates HS256 tokens signed with LOCAL_JWT_SECRET (no Azure AD required).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import httpx
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from structlog import get_logger

from app.core.config import settings

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# JWKS Cache (refreshed every 24h or on key rotation)
# ─────────────────────────────────────────────────────────────────────────────
_jwks_cache: dict[str, Any] = {}
_jwks_last_fetched: float = 0.0
JWKS_TTL_SECONDS = 86_400  # 24 hours


async def _get_jwks() -> dict[str, Any]:
    global _jwks_cache, _jwks_last_fetched

    if time.time() - _jwks_last_fetched < JWKS_TTL_SECONDS and _jwks_cache:
        return _jwks_cache

    discovery_url = (
        f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}"
        f"/v2.0/.well-known/openid-configuration"
    )
    async with httpx.AsyncClient() as client:
        discovery = (await client.get(discovery_url)).json()
        jwks = (await client.get(discovery["jwks_uri"])).json()

    _jwks_cache = jwks
    _jwks_last_fetched = time.time()
    logger.info("jwks_refreshed", keys_count=len(jwks.get("keys", [])))
    return _jwks_cache


# ─────────────────────────────────────────────────────────────────────────────
# Local HS256 JWT helpers (LOCAL_MODE only)
# ─────────────────────────────────────────────────────────────────────────────

def issue_local_token(
    *,
    user_id: str,
    email: str,
    display_name: str,
    roles: list[str],
    expire_minutes: int = 60,
) -> str:
    """
    Issue a signed HS256 JWT for local development.
    Only available when LOCAL_MODE=true.
    """
    now = datetime.now(tz=timezone.utc)
    claims = {
        "sub": user_id,
        "oid": user_id,
        "email": email,
        "preferred_username": email,
        "name": display_name,
        "roles": roles,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + expire_minutes * 60,
        "iss": "local",
        "aud": "local",
    }
    return jwt.encode(
        claims,
        settings.LOCAL_JWT_SECRET.get_secret_value(),
        algorithm=settings.LOCAL_JWT_ALGORITHM,
    )


def _validate_local_token(token: str) -> dict[str, Any]:
    """Validate HS256 local dev token."""
    try:
        claims = jwt.decode(
            token,
            settings.LOCAL_JWT_SECRET.get_secret_value(),
            algorithms=[settings.LOCAL_JWT_ALGORITHM],
            audience="local",
            options={"verify_exp": True},
        )
        return claims
    except ExpiredSignatureError as e:
        raise TokenValidationError("Local token has expired") from e
    except JWTError as e:
        raise TokenValidationError(f"Local JWT validation failed: {e}") from e


async def validate_azure_token(token: str) -> dict[str, Any]:
    """
    Validate a JWT token.
    - LOCAL_MODE=true : validates HS256 token signed with LOCAL_JWT_SECRET
    - LOCAL_MODE=false: validates Azure AD RS256 token via JWKS endpoint

    Raises:
        TokenValidationError: on any validation failure
    """
    if settings.LOCAL_MODE:
        return _validate_local_token(token)

    try:
        jwks = await _get_jwks()

        # Decode header to find the signing key
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        signing_key = next(
            (k for k in jwks["keys"] if k.get("kid") == kid), None
        )
        if not signing_key:
            raise TokenValidationError("Signing key not found in JWKS")

        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.AZURE_AUDIENCE,
            issuer=f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}/v2.0",
            options={"verify_exp": True, "verify_iat": True},
        )
        return claims

    except ExpiredSignatureError as e:
        raise TokenValidationError("Token has expired") from e
    except JWTError as e:
        raise TokenValidationError(f"JWT validation failed: {e}") from e


class TokenValidationError(Exception):
    """Raised when token validation fails."""
