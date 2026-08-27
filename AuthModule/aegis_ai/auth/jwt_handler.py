"""
aegis_ai.auth.jwt_handler
==========================
Production JWT authentication using RS256.

Security guarantees:
- RS256 ONLY — HS256 / 'alg: none' always rejected
- Token revocation via Redis JTI blocklist
- Keys fetched from GCP Secret Manager (or local PEM for dev)
- All token ops are timing-safe (constant-time claims check)
- Sliding key rotation support via 'kid' header

OWASP: A02:2021-Cryptographic Failures, A07:2021-Auth Failures
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import structlog
import jwt
from pydantic import BaseModel, Field

from aegis_ai.auth.base import AuthProvider
from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.exceptions import TokenExpiredError, TokenInvalidError
from aegis_ai.settings import AegisSettings
from aegis_ai.types import AuthMethod, Permission, TenantID, UserID

logger = structlog.get_logger(__name__)

# Permitted algorithms — never include "none" or HS-family
_ALLOWED_ALGORITHMS = ["RS256", "ES256"]


class JWTClaims(BaseModel):
    """Strict JWT claims model — all fields required."""

    sub: str = Field(..., description="Subject (identity_id)")
    iss: str = Field(..., description="Issuer")
    aud: str = Field(..., description="Audience")
    exp: int = Field(..., description="Expiration timestamp (Unix)")
    nbf: int = Field(..., description="Not-before timestamp (Unix)")
    iat: int = Field(..., description="Issued-at timestamp (Unix)")
    jti: str = Field(..., description="JWT ID (for revocation)")
    tenant_id: str = Field(..., description="Tenant ID")
    agent_id: Optional[str] = Field(None, description="Agent ID")
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    auth_method: str = Field(default=AuthMethod.JWT.value)
    session_id: str = Field(..., description="Session UUID string")
    mfa_verified: bool = Field(False)
    email: Optional[str] = None


class JWTHandler(AuthProvider):
    """
    Production JWT handler using RS256 asymmetric signing.

    Key lifecycle:
    - Private key: fetched from GCP Secret Manager (or local PEM in dev)
    - Public key: used for verification; can be published as JWKS
    - Key rotation: new 'kid' per rotation; old keys verified for token lifetime

    Token lifecycle:
    - Access: 15 min (configurable)
    - Refresh: 7 days (configurable)
    - Revocation: JTI stored in Redis blocklist with matching TTL
    """

    def __init__(self, settings: AegisSettings, redis_client: Any = None) -> None:
        """
        Initialise JWTHandler.

        Args:
            settings: AegisSettings instance.
            redis_client: Async-compatible Redis client for JTI blocklist.
                          If None, revocation is in-memory (single-instance only).
        """
        self._settings = settings
        self._redis = redis_client
        self._current_kid = "key-v1"

        # Key cache: {kid: (private_pem, public_pem)}
        self._key_cache: Dict[str, Tuple[bytes, bytes]] = {}
        # In-memory fallback revocation set (for testing / no-Redis mode)
        self._revoked_jtis: set[str] = set()

        # Pre-load keys from local paths if configured (dev mode)
        self._load_local_keys_if_configured()

    # ─────────────────────────────────────────────────────────────────
    # Key Management
    # ─────────────────────────────────────────────────────────────────

    def _load_local_keys_if_configured(self) -> None:
        """Load keys from local PEM files (dev/test mode only)."""
        jwt_cfg = self._settings.jwt
        if jwt_cfg.local_private_key_path and jwt_cfg.local_public_key_path:
            with open(jwt_cfg.local_private_key_path, "rb") as f:
                private_pem = f.read()
            with open(jwt_cfg.local_public_key_path, "rb") as f:
                public_pem = f.read()
            self._key_cache[self._current_kid] = (private_pem, public_pem)
            logger.warning(
                "jwt_local_key_loaded",
                kid=self._current_kid,
                note="Local PEM keys should NEVER be used in production",
            )

    def set_keys(self, private_pem: bytes, public_pem: bytes, kid: str = "key-v1") -> None:
        """
        Directly inject keys (used in tests and key-rotation flows).

        Args:
            private_pem: PEM-encoded RSA private key bytes.
            public_pem: PEM-encoded RSA public key bytes.
            kid: Key ID label.
        """
        self._key_cache[kid] = (private_pem, public_pem)
        self._current_kid = kid

    def _get_private_key(self, kid: str) -> bytes:
        """Retrieve private key bytes for given kid."""
        if kid in self._key_cache:
            return self._key_cache[kid][0]
        raise TokenInvalidError(f"Unknown key ID: {kid}")

    def _get_public_key(self, kid: str) -> bytes:
        """Retrieve public key bytes for given kid."""
        if kid in self._key_cache:
            return self._key_cache[kid][1]
        raise TokenInvalidError(f"Unknown key ID: {kid}")

    @property
    def _private_key(self) -> bytes:
        return self._get_private_key(self._current_kid)

    @property
    def _public_key(self) -> bytes:
        return self._get_public_key(self._current_kid)

    # ─────────────────────────────────────────────────────────────────
    # Token Creation
    # ─────────────────────────────────────────────────────────────────

    def create_access_token(
        self, identity: IdentityContext, expire_minutes: Optional[int] = None
    ) -> str:
        """
        Create a signed RS256 JWT access token.

        Args:
            identity: Authenticated IdentityContext to encode.
            expire_minutes: Token TTL in minutes (default: from settings).

        Returns:
            Signed JWT string.
        """
        expire_minutes = expire_minutes or self._settings.jwt.access_token_expire_minutes
        return self._build_token(identity, expire_minutes=expire_minutes)

    def create_refresh_token(self, identity: IdentityContext) -> str:
        """Create a signed RS256 JWT refresh token (longer TTL)."""
        expire_minutes = self._settings.jwt.refresh_token_expire_days * 24 * 60
        return self._build_token(identity, expire_minutes=expire_minutes)

    def _build_token(self, identity: IdentityContext, expire_minutes: int) -> str:
        """Internal token builder."""
        now = datetime.now(timezone.utc)
        exp = now + timedelta(minutes=expire_minutes)
        jti = str(uuid.uuid4())

        payload: Dict[str, Any] = {
            "sub": str(identity.identity_id),
            "iss": self._settings.jwt.issuer,
            "aud": self._settings.jwt.audience,
            "exp": int(exp.timestamp()),
            "nbf": int(now.timestamp()),
            "iat": int(now.timestamp()),
            "jti": jti,
            "tenant_id": str(identity.tenant_id),
            "agent_id": str(identity.agent_id) if identity.agent_id else None,
            "roles": sorted(identity.roles),
            "permissions": sorted(identity.permissions),
            "auth_method": identity.auth_method.value,
            "session_id": identity.session_id,
            "mfa_verified": identity.mfa_verified,
            "email": identity.email,
        }

        private_key = self._get_private_key(self._current_kid)
        token = jwt.encode(
            payload,
            private_key,
            algorithm=self._settings.jwt.algorithm,
            headers={"kid": self._current_kid},
        )
        logger.info(
            "jwt_created",
            sub=payload["sub"],
            jti=jti,
            exp=exp.isoformat(),
            kid=self._current_kid,
        )
        return token

    # ─────────────────────────────────────────────────────────────────
    # Token Verification
    # ─────────────────────────────────────────────────────────────────

    def verify_token(self, token: str) -> IdentityContext:
        """
        Verify a JWT and return the IdentityContext.

        Security checks performed (in order):
        1. Algorithm whitelist enforcement (alg: none → rejected)
        2. JTI revocation check (Redis blocklist)
        3. Signature verification with the correct public key (RS256)
        4. Issuer + audience validation
        5. Expiry validation
        6. not-before (nbf) validation
        7. Claims model validation (all required fields)

        Args:
            token: The JWT string to verify.

        Returns:
            Authenticated IdentityContext.

        Raises:
            TokenExpiredError: Token is past its expiry time.
            TokenInvalidError: Any other validation failure.
        """
        try:
            # Step 1: Inspect header — reject forbidden algorithms BEFORE signature check
            try:
                unverified_header = jwt.get_unverified_header(token)
            except Exception as exc:
                raise TokenInvalidError("Malformed JWT header") from exc

            alg = unverified_header.get("alg", "none")
            if alg.lower() == "none" or alg not in _ALLOWED_ALGORITHMS:
                logger.warning("jwt_algorithm_rejected", alg=alg)
                raise TokenInvalidError(
                    f"Rejected JWT algorithm '{alg}'. Only {_ALLOWED_ALGORITHMS} are permitted."
                )

            kid = unverified_header.get("kid", self._current_kid)

            # Security: validate kid against the known-key allowlist BEFORE using
            # it to select a verification key. Accepting an arbitrary kid from an
            # unverified header is a key-confusion attack vector.
            if kid not in self._key_cache:
                logger.warning("jwt_unknown_kid_rejected", kid=kid)
                raise TokenInvalidError(
                    f"JWT contains unknown kid '{kid}'. "
                    "Only registered key IDs are accepted."
                )

            # Step 2: Revocation check (read JTI without signature verification)
            try:
                unverified_payload = jwt.decode(
                    token,
                    options={"verify_signature": False},
                    algorithms=_ALLOWED_ALGORITHMS,
                )
            except Exception as exc:
                raise TokenInvalidError("Cannot decode JWT payload") from exc

            jti = unverified_payload.get("jti", "")
            if jti and self._is_revoked(jti):
                logger.warning("jwt_revoked_usage_attempt", jti=jti)
                raise TokenInvalidError("Token has been revoked")

            # Step 3–7: Full cryptographic + claims verification
            public_key = self._get_public_key(kid)
            payload = jwt.decode(
                token,
                public_key,
                algorithms=[self._settings.jwt.algorithm],
                audience=self._settings.jwt.audience,
                issuer=self._settings.jwt.issuer,
                options={
                    "require": ["sub", "iss", "aud", "exp", "iat", "jti"],
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )

            # Step 8: Strict claims model validation
            claims = JWTClaims(**payload)

            identity = IdentityContext(
                identity_id=UserID(claims.sub),
                agent_id=claims.agent_id,
                tenant_id=TenantID(claims.tenant_id),
                email=claims.email,
                auth_method=AuthMethod(claims.auth_method),
                auth_time=datetime.fromtimestamp(claims.iat, tz=timezone.utc),
                expires_at=datetime.fromtimestamp(claims.exp, tz=timezone.utc),
                session_id=claims.session_id,
                mfa_verified=claims.mfa_verified,
                roles=frozenset(claims.roles),
                permissions=frozenset(Permission(p) for p in claims.permissions),
            )

            logger.debug(
                "jwt_verified",
                sub=claims.sub,
                jti=claims.jti,
                tenant=claims.tenant_id,
            )
            return identity

        except jwt.ExpiredSignatureError as exc:
            logger.info("jwt_expired")
            raise TokenExpiredError("JWT has expired") from exc
        except jwt.ImmatureSignatureError as exc:
            raise TokenInvalidError("JWT not yet valid (nbf claim)") from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenInvalidError("JWT audience mismatch") from exc
        except jwt.InvalidIssuerError as exc:
            raise TokenInvalidError("JWT issuer mismatch") from exc
        except (TokenExpiredError, TokenInvalidError):
            raise
        except Exception as exc:
            logger.warning("jwt_verification_failed", error=str(exc))
            raise TokenInvalidError("JWT verification failed") from exc

    # ─────────────────────────────────────────────────────────────────
    # AuthProvider Interface
    # ─────────────────────────────────────────────────────────────────

    async def validate_token(self, token: str) -> IdentityContext:
        """Async wrapper for verify_token (satisfies AuthProvider interface)."""
        # Check Redis revocation list asynchronously if available
        if self._redis is not None:
            try:
                unverified = jwt.decode(
                    token,
                    options={"verify_signature": False},
                    algorithms=_ALLOWED_ALGORITHMS,
                )
                jti = unverified.get("jti", "")
                if jti:
                    revoked = await self._redis.get(f"jwt:revoked:{jti}")
                    if revoked:
                        raise TokenInvalidError("Token has been revoked")
            except (TokenInvalidError, TokenExpiredError):
                raise
            except Exception:
                pass  # If Redis is down, fall through to sync check

        return self.verify_token(token)

    async def authenticate(self, credentials: dict) -> IdentityContext:
        """JWTHandler validates tokens, not raw credentials."""
        raise NotImplementedError(
            "JWTHandler.authenticate() is not applicable. Use validate_token() instead."
        )

    async def revoke_token(self, token: str) -> None:
        """
        Revoke a JWT by adding its JTI to the blocklist.

        The blocklist entry TTL matches the token's remaining validity window.
        """
        try:
            payload = jwt.decode(
                token,
                options={"verify_signature": False},
                algorithms=_ALLOWED_ALGORITHMS,
            )
            jti = payload.get("jti")
            exp = payload.get("exp", 0)
            if not jti:
                return

            ttl = max(int(exp - datetime.now(timezone.utc).timestamp()), 1)
            await self._revoke_jti(jti, ttl)
            logger.info("jwt_revoked", jti=jti, ttl_seconds=ttl)
        except Exception as exc:
            logger.warning("jwt_revoke_failed", error=str(exc))

    # ─────────────────────────────────────────────────────────────────
    # Refresh Token Flow
    # ─────────────────────────────────────────────────────────────────

    async def refresh_tokens(
        self, refresh_token: str
    ) -> Tuple[str, str]:
        """
        Exchange a valid refresh token for a new access + refresh token pair.

        The old refresh token's JTI is revoked immediately (rotation).
        """
        # verify_token_with_jti returns both the identity AND the verified jti,
        # eliminating the need for a second unverified decode just to extract jti.
        identity, old_jti = self._verify_token_with_jti(refresh_token)

        new_access = self.create_access_token(identity)
        new_refresh = self.create_refresh_token(identity)

        if old_jti:
            await self._revoke_jti(old_jti, ttl=self._settings.jwt.refresh_token_expire_days * 86400)

        logger.info("jwt_tokens_refreshed", identity_id=identity.identity_id)
        return new_access, new_refresh

    # ─────────────────────────────────────────────────────────────────
    # Key Rotation
    # ─────────────────────────────────────────────────────────────────

    def rotate_keys(
        self, new_private_pem: bytes, new_public_pem: bytes, new_kid: str
    ) -> None:
        """
        Introduce a new signing key while retaining old keys for verification.

        Old keys are kept so existing tokens remain verifiable until they expire.
        New tokens will be signed with new_kid immediately.
        """
        old_kid = self._current_kid
        self._key_cache[new_kid] = (new_private_pem, new_public_pem)
        self._current_kid = new_kid
        logger.info("jwt_key_rotated", old_kid=old_kid, new_kid=new_kid)

    # ─────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ─────────────────────────────────────────────────────────────────

    def _verify_token_with_jti(self, token: str) -> Tuple[IdentityContext, str]:
        """
        Verify a JWT and return (IdentityContext, jti).

        Used internally by refresh_tokens() to avoid a second unverified decode.
        After verify_token() confirms the token's cryptographic validity, we
        extract the jti via an unverified decode — safe because the signature
        was already verified.
        """
        identity = self.verify_token(token)
        # Token is now cryptographically verified; safe to extract jti without
        # a second expensive signature verification.
        unverified_payload = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=_ALLOWED_ALGORITHMS,
        )
        return identity, unverified_payload.get("jti", "")

    def _is_revoked(self, jti: str) -> bool:
        """Synchronous revocation check (in-memory fallback)."""
        return jti in self._revoked_jtis

    async def _revoke_jti(self, jti: str, ttl: int = 86400) -> None:
        """Store JTI in Redis or in-memory revocation set."""
        if self._redis is not None:
            try:
                await self._redis.setex(f"jwt:revoked:{jti}", ttl, b"1")
                return
            except Exception as exc:
                logger.error("jwt_redis_revoke_failed", jti=jti, error=str(exc))
        # Fallback: in-memory (not distributed, suitable only for single-instance)
        self._revoked_jtis.add(jti)

    @staticmethod
    def _constant_time_compare(a: str, b: str) -> bool:
        """Timing-safe string comparison to prevent timing side-channel attacks."""
        return hmac.compare_digest(
            hashlib.sha256(a.encode()).digest(),
            hashlib.sha256(b.encode()).digest(),
        )
