"""
JWT Handler — Azure AD token validation and custom JWT issuance.
Validates incoming Bearer tokens against Azure AD JWKS endpoint.
Issues internal service tokens for inter-service communication.
"""
from __future__ import annotations

import time
from typing import Any
from structlog import get_logger

logger = get_logger(__name__)


class AzureADTokenValidator:
    """
    Validates Azure AD access tokens (JWT) against the JWKS endpoint.
    Caches JWKS keys to avoid repeated HTTP calls.
    """

    def __init__(self, tenant_id: str, client_id: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.jwks_uri = (
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        )
        self._jwks_cache: dict | None = None
        self._jwks_fetched_at: float = 0
        self._jwks_ttl: int = 3600  # Refresh keys every hour

    async def _get_jwks(self) -> dict:
        """Fetch and cache Azure AD public keys."""
        now = time.time()
        if self._jwks_cache and (now - self._jwks_fetched_at) < self._jwks_ttl:
            return self._jwks_cache

        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(self.jwks_uri, timeout=10)
            response.raise_for_status()
            self._jwks_cache = response.json()
            self._jwks_fetched_at = now
            logger.info("jwks_refreshed", tenant_id=self.tenant_id)
            return self._jwks_cache

    async def validate(self, token: str) -> dict[str, Any]:
        """
        Validate the token and return its claims.
        Raises ValueError on invalid/expired tokens.
        """
        try:
            import jwt
            from jwt import PyJWKClient

            jwks_client = PyJWKClient(self.jwks_uri, cache_keys=True, cache_jwk_set=True)
            signing_key = jwks_client.get_signing_key_from_jwt(token)

            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.client_id,
                options={"verify_exp": True},
            )
            return claims
        except Exception as e:
            logger.warning("token_validation_failed", error=str(e))
            raise ValueError(f"Invalid token: {e}") from e

    def extract_roles(self, claims: dict) -> list[str]:
        """Extract roles from token claims (Azure AD app roles)."""
        # Azure AD injects roles as 'roles' claim
        roles = claims.get("roles", [])
        # Also check groups if role-group mapping is used
        groups = claims.get("groups", [])
        return list(roles) + list(groups)

    def extract_user_info(self, claims: dict) -> dict[str, str]:
        """Extract standard user fields from Azure AD claims."""
        return {
            "azure_oid": claims.get("oid", ""),
            "email": claims.get("preferred_username", claims.get("upn", "")),
            "display_name": claims.get("name", ""),
            "tenant_id": claims.get("tid", ""),
        }


class InternalJWTHandler:
    """
    Issues short-lived internal service tokens for inter-service calls.
    Uses a shared symmetric key stored in Key Vault.
    """

    ALGORITHM = "HS256"
    DEFAULT_TTL = 300  # 5 minutes

    def __init__(self, signing_key: str):
        self.signing_key = signing_key

    def issue(self, service_name: str, scopes: list[str], ttl: int = DEFAULT_TTL) -> str:
        import jwt
        now = int(time.time())
        payload = {
            "iss": "eatap-internal",
            "sub": service_name,
            "scopes": scopes,
            "iat": now,
            "exp": now + ttl,
        }
        return jwt.encode(payload, self.signing_key, algorithm=self.ALGORITHM)

    def validate_internal(self, token: str) -> dict[str, Any]:
        import jwt
        return jwt.decode(token, self.signing_key, algorithms=[self.ALGORITHM])


# Module-level singleton factory
_validator: AzureADTokenValidator | None = None


def get_token_validator() -> AzureADTokenValidator:
    global _validator
    if _validator is None:
        try:
            from app.core.config import settings
            _validator = AzureADTokenValidator(
                tenant_id=settings.AZURE_AD_TENANT_ID,
                client_id=settings.AZURE_AD_CLIENT_ID,
            )
        except Exception as e:
            logger.warning("token_validator_init_failed", error=str(e))
            _validator = AzureADTokenValidator(tenant_id="dev", client_id="dev")
    return _validator
