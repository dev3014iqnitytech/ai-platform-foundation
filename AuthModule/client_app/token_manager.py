"""
client_app.token_manager
==========================
Mock SSO Token Manager for Agent A.

Generates a single RSA-2048 key pair once at startup (or loads from a PEM file)
and caches a signed JWT.  The cached token is transparently refreshed 60 s before
it expires, so callers always receive a fresh, valid token without the overhead of
per-request key generation.

Why this matters
----------------
The original demo generated a *new* private key on every request.  Because the
Aegis Gateway validates the SSO token against the provider's JWKS endpoint, a key
that changes every request is cryptographically unverifiable.  This module fixes
that by ensuring one stable key pair is used for the entire process lifetime.

In production you would replace this with a real OAuth2 Client-Credentials flow
against your IdP (Okta / Azure AD / Auth0).  The interface is identical — callers
just call ``await manager.get_token()`` and receive a valid Bearer token.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

import jwt
import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

try:
    from client_app.config import ClientSettings
except ImportError:
    from config import ClientSettings  # type: ignore[no-redef]

logger = structlog.get_logger(__name__)


class MockSSOTokenManager:
    """
    Stable mock SSO token manager.

    Key lifecycle:
      - At init, load an RSA-2048 key from ``settings.mock_key_path`` if set,
        otherwise generate one in memory.
      - ``get_token()`` returns a cached JWT, refreshing when it is within
        ``settings.token_refresh_buffer_seconds`` of expiry.
      - Thread-safe: an asyncio.Lock guards the refresh path.

    In production, replace the body of ``_fetch_token_from_idp()`` with a real
    ``httpx.AsyncClient.post()`` to your IdP token endpoint using the
    Client Credentials grant.
    """

    def __init__(self, settings: ClientSettings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._cached_token: Optional[str] = None
        self._token_expiry: float = 0.0  # Unix timestamp

        # Load or generate the signing key
        self._private_key, self._public_key = self._init_key()
        logger.info(
            "sso_token_manager_initialized",
            key_source="file" if settings.mock_key_path else "generated",
            issuer=settings.sso_issuer,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def get_token(self) -> str:
        """
        Return a valid, cached SSO token for Agent A.

        Refreshes automatically if the cached token is within the configured
        buffer window of expiry.
        """
        now = time.time()
        buffer = self._settings.token_refresh_buffer_seconds

        if self._cached_token and (self._token_expiry - buffer) > now:
            return self._cached_token

        async with self._lock:
            # Double-checked locking — re-check after acquiring the lock
            now = time.time()
            if self._cached_token and (self._token_expiry - buffer) > now:
                return self._cached_token

            token, expiry = await self._fetch_token_from_idp()
            self._cached_token = token
            self._token_expiry = expiry
            logger.info(
                "sso_token_refreshed",
                expires_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expiry)),
            )
            return token

    def get_public_key_pem(self) -> bytes:
        """Return the PEM-encoded public key (for a mock JWKS endpoint)."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _init_key(self) -> Tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
        """Load from file or generate a fresh RSA-2048 key pair."""
        path = self._settings.mock_key_path
        if path:
            key_file = Path(path)
            if key_file.exists():
                private_key = serialization.load_pem_private_key(
                    key_file.read_bytes(),
                    password=None,
                    backend=default_backend(),
                )
                logger.info("sso_key_loaded_from_file", path=str(key_file))
                return private_key, private_key.public_key()
            logger.warning(
                "sso_key_file_not_found",
                path=path,
                fallback="generating in-memory key",
            )

        # Generate once in memory (not per-request)
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        logger.info("sso_key_generated_in_memory")
        return private_key, private_key.public_key()

    async def _fetch_token_from_idp(self) -> Tuple[str, float]:
        """
        Mint a signed mock M2M token for Agent A.

        Production replacement:
            Replace this with a real Client Credentials grant::

                resp = await httpx.AsyncClient().post(
                    self._settings.idp_token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._settings.sso_client_id,
                        "client_secret": <from secret manager>,
                        "scope": "agents.call",
                    },
                )
                data = resp.json()
                return data["access_token"], time.time() + data["expires_in"]
        """
        cfg = self._settings
        now = int(time.time())
        ttl = cfg.token_ttl_minutes * 60
        expiry_ts = now + ttl

        payload = {
            "sub": cfg.sso_subject,
            "email": f"{cfg.sso_subject}@enterprise.com",
            "tenant_id": cfg.sso_tenant_id,
            "roles": cfg.sso_roles,
            "permissions": cfg.sso_permissions,
            "iat": now,
            "nbf": now - 5,  # 5-second clock-skew allowance
            "exp": expiry_ts,
            "iss": cfg.sso_issuer,
            "aud": cfg.sso_audience,
            "jti": str(uuid.uuid4()),
        }

        token = jwt.encode(
            payload,
            self._private_key,
            algorithm="RS256",
            headers={"kid": "mock-sso-kid", "typ": "JWT"},
        )
        return token, float(expiry_ts)


# Module-level singleton (initialised lazily so tests can inject a custom manager)
_manager: Optional[MockSSOTokenManager] = None


def get_token_manager(settings: Optional[ClientSettings] = None) -> MockSSOTokenManager:
    """
    Return the module-level token manager singleton.

    Args:
        settings: If provided on first call, used to initialise the singleton.
                  Subsequent calls ignore this argument.
    """
    global _manager
    if _manager is None:
        if settings is None:
            try:
                from client_app.config import get_client_settings
            except ImportError:
                from config import get_client_settings  # type: ignore[no-redef]
            settings = get_client_settings()
        _manager = MockSSOTokenManager(settings)
    return _manager
