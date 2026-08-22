"""
aegis_ai.auth.api_key_manager
================================
Production API Key authentication with argon2 hashing.

Security guarantees:
- Keys stored as argon2id hashes (never plaintext)
- Timing-safe comparison (constant-time, prevents oracle attacks)
- Key prefix `aegis_` + 48 URL-safe base64 chars (≈288 bits entropy)
- Scoped keys: each key grants specific permissions only
- Revocation: O(1) lookup by key_id
- Expiry enforcement: keys have configurable TTL

OWASP: A02:2021-Cryptographic Failures, A07:2021-Auth Failures
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from pydantic import BaseModel, Field, ConfigDict

from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.exceptions import AuthenticationError
from aegis_ai.settings import AegisSettings
from aegis_ai.types import AuthMethod, Permission, TenantID, UserID

logger = structlog.get_logger(__name__)

# Production argon2id parameters (OWASP recommended)
_PH = PasswordHasher(
    time_cost=3,        # 3 iterations
    memory_cost=65536,  # 64 MB
    parallelism=4,      # 4 threads
    hash_len=32,
    salt_len=16,
)

_KEY_PREFIX = "aegis_"
_KEY_ENTROPY_BYTES = 36  # 36 bytes = 48 base64 chars (288 bits entropy)


class APIKeyRecord(BaseModel):
    """Stored API key record — never contains the plaintext key."""

    model_config = ConfigDict(frozen=True)

    key_id: str = Field(..., description="Public key identifier (safe to log)")
    hashed_key: str = Field(..., description="Argon2id hash of the full key")
    identity_id: UserID
    tenant_id: TenantID = Field(TenantID("default"))
    scopes: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    revoked: bool = False
    description: str = ""


class APIKeyManager:
    """
    Manages API key lifecycle: generation, validation, and revocation.

    Keys are identified by a short `key_id` prefix embedded in the key itself.
    Format: `aegis_<key_id_b64>.<secret_b64>`

    Example: `aegis_YWJjMTIz.dGhpcyBpcyBhIHNhbXBsZSBrZXkgZm9yIGRlbW8=`
    """

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        # In-memory key store (production: use Cloud Firestore / Redis)
        self._key_store: Dict[str, APIKeyRecord] = {}

    # ─────────────────────────────────────────────────────────────────
    # Key Generation
    # ─────────────────────────────────────────────────────────────────

    def generate_api_key(
        self,
        identity_id: UserID,
        scopes: List[str],
        expire_days: int = 90,
        description: str = "",
        tenant_id: TenantID = TenantID("default"),
    ) -> Tuple[str, APIKeyRecord]:
        """
        Generate a new API key.

        Args:
            identity_id: The user / service account this key belongs to.
            scopes: Permission scopes granted by this key.
            expire_days: Key validity in days (default 90).
            description: Human-readable label for the key.
            tenant_id: Tenant scope for this key.

        Returns:
            Tuple of (raw_key_string, APIKeyRecord).
            The raw_key_string is shown ONCE and never stored.
        """
        # Generate random key material
        key_id = base64.urlsafe_b64encode(os.urandom(9)).decode("ascii").rstrip("=")
        secret_bytes = secrets.token_bytes(_KEY_ENTROPY_BYTES)
        secret_b64 = base64.urlsafe_b64encode(secret_bytes).decode("ascii").rstrip("=")

        # Full raw key (shown to user once)
        raw_key = f"{_KEY_PREFIX}{key_id}.{secret_b64}"

        # Hash the FULL raw key for storage
        hashed = _PH.hash(raw_key)

        now = datetime.now(timezone.utc)
        record = APIKeyRecord(
            key_id=key_id,
            hashed_key=hashed,
            identity_id=identity_id,
            tenant_id=tenant_id,
            scopes=sorted(set(scopes)),
            created_at=now,
            expires_at=now + timedelta(days=expire_days) if expire_days > 0 else None,
            description=description,
        )

        self._key_store[key_id] = record
        logger.info(
            "api_key_generated",
            key_id=key_id,
            identity_id=identity_id,
            scopes=scopes,
            expire_days=expire_days,
        )
        return raw_key, record

    # ─────────────────────────────────────────────────────────────────
    # Key Validation
    # ─────────────────────────────────────────────────────────────────

    def validate_api_key(self, raw_key: str) -> IdentityContext:
        """
        Validate a raw API key and return an IdentityContext.

        Security: always runs argon2 verification even for unknown keys
        to prevent timing-oracle attacks that reveal key existence.

        Args:
            raw_key: The full API key string presented by the caller.

        Returns:
            Authenticated IdentityContext scoped to the key's permissions.

        Raises:
            AuthenticationError: Key is invalid, expired, or revoked.
        """
        # Parse key_id from the key
        record = self._parse_and_lookup(raw_key)
        stored_hash = record.hashed_key if record else _PH.hash("dummy_for_timing")

        # Verify with argon2 (timing-safe by design)
        try:
            _PH.verify(stored_hash, raw_key)
            valid = True
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            valid = False

        if not valid or record is None:
            logger.warning("api_key_invalid", key_prefix=raw_key[:12] + "...")
            raise AuthenticationError("API key is invalid")

        # Check revocation
        if record.revoked:
            logger.warning("api_key_revoked", key_id=record.key_id)
            raise AuthenticationError("API key has been revoked")

        # Check expiry
        if record.expires_at and datetime.now(timezone.utc) > record.expires_at:
            logger.warning("api_key_expired", key_id=record.key_id)
            raise AuthenticationError("API key has expired")

        logger.info("api_key_validated", key_id=record.key_id, identity_id=record.identity_id)
        return IdentityContext(
            identity_id=record.identity_id,
            tenant_id=record.tenant_id,
            auth_method=AuthMethod.API_KEY,
            session_id=str(uuid.uuid4()),
            permissions=frozenset(Permission(s) for s in record.scopes),
            metadata={"key_id": record.key_id},
        )

    # ─────────────────────────────────────────────────────────────────
    # Revocation
    # ─────────────────────────────────────────────────────────────────

    def revoke_api_key(self, key_id: str) -> None:
        """
        Revoke an API key by key_id.

        Args:
            key_id: The public key identifier (embedded in the key string).
        """
        if key_id not in self._key_store:
            logger.warning("api_key_revoke_not_found", key_id=key_id)
            return
        record = self._key_store[key_id]
        self._key_store[key_id] = record.model_copy(update={"revoked": True})
        logger.info("api_key_revoked", key_id=key_id)

    def list_keys(self, identity_id: UserID) -> List[APIKeyRecord]:
        """List all (non-sensitive) key records for an identity."""
        return [
            r for r in self._key_store.values() if r.identity_id == identity_id
        ]

    # ─────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ─────────────────────────────────────────────────────────────────

    def _parse_and_lookup(self, raw_key: str) -> Optional[APIKeyRecord]:
        """
        Parse the key_id from the raw key and look up the record.

        Returns None (not raises) so that timing remains constant for
        both found and not-found cases.
        """
        if not raw_key.startswith(_KEY_PREFIX):
            return None
        rest = raw_key[len(_KEY_PREFIX):]
        parts = rest.split(".", 1)
        if len(parts) != 2:
            return None
        key_id = parts[0]
        return self._key_store.get(key_id)  # None if not found
