"""
aegis_ai.crypto.key_manager
==============================
Cryptographic key management via GCP Secret Manager and KMS.

Key types managed:
- JWT private/public RSA keys (from Secret Manager)
- Audit HMAC signing key (from Secret Manager)
- Data Encryption Keys (DEK) via KMS envelope encryption

Security:
- Keys never cached longer than 5 minutes
- Key bytes zeroized after use where possible
- Local PEM fallback for dev/test ONLY (logged as warning)

OWASP: A02:2021-Cryptographic Failures
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

import structlog

from aegis_ai.settings import AegisSettings

logger = structlog.get_logger(__name__)

_CACHE_TTL = 300  # 5 minutes


class _CacheEntry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float = _CACHE_TTL) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl

    def is_valid(self) -> bool:
        return time.monotonic() < self.expires_at


class KeyManager:
    """
    Manages all cryptographic keys for the Aegis AI SDK.

    In production: fetches from GCP Secret Manager.
    In development (use_gcp=False): reads from local files or env vars.
    """

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        self._cache: Dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._sync_lock = threading.Lock()
        self._sm_client: Optional[Any] = None
        self._kms_client: Optional[Any] = None
        self._init_clients()

    def _init_clients(self) -> None:
        """Initialise GCP clients (best-effort)."""
        if not self._settings.gcp.use_gcp:
            logger.info("key_manager_local_mode")
            return
        try:
            from google.cloud import secretmanager
            self._sm_client = secretmanager.SecretManagerServiceClient()
            logger.info("secret_manager_client_initialized")
        except Exception as exc:
            logger.warning("secret_manager_init_failed", error=str(exc))

        try:
            from google.cloud import kms
            self._kms_client = kms.KeyManagementServiceClient()
            logger.info("kms_client_initialized")
        except Exception as exc:
            logger.warning("kms_init_failed", error=str(exc))

    # ─────────────────────────────────────────────────────────────────
    # JWT Keys
    # ─────────────────────────────────────────────────────────────────

    async def get_jwt_private_key(self) -> bytes:
        """Fetch RSA private key PEM bytes."""
        return await self._get_key(
            cache_key="jwt_private",
            secret_name=self._settings.jwt.private_key_secret_name,
            env_var="AEGIS_JWT_PRIVATE_KEY_PATH",
            local_path=self._settings.jwt.local_private_key_path,
        )

    async def get_jwt_public_key(self) -> bytes:
        """Fetch RSA public key PEM bytes."""
        return await self._get_key(
            cache_key="jwt_public",
            secret_name=self._settings.jwt.public_key_secret_name,
            env_var="AEGIS_JWT_PUBLIC_KEY_PATH",
            local_path=self._settings.jwt.local_public_key_path,
        )

    # ─────────────────────────────────────────────────────────────────
    # Audit Signing Key
    # ─────────────────────────────────────────────────────────────────

    async def get_audit_signing_key(self) -> bytes:
        """Fetch HMAC signing key for audit events."""
        return await self._get_key(
            cache_key="audit_signing",
            secret_name=self._settings.audit.signing_key_secret_name,
            env_var="AEGIS_AUDIT_SIGNING_KEY",
        )

    # ─────────────────────────────────────────────────────────────────
    # Encryption Key (via KMS envelope)
    # ─────────────────────────────────────────────────────────────────

    async def get_encryption_key(self) -> bytes:
        """
        Fetch a Data Encryption Key (DEK) via KMS.

        In production: uses GCP KMS to decrypt an encrypted DEK.
        In dev: returns a random 32-byte key from env.
        """
        cache_key = "encryption_dek"
        async with self._lock:
            entry = self._cache.get(cache_key)
            if entry and entry.is_valid():
                return entry.value

        if self._kms_client and self._settings.gcp.use_gcp:
            key_bytes = await asyncio.to_thread(self._fetch_kms_dek)
        else:
            # Dev mode: use env var or generate ephemeral key
            raw = os.environ.get("AEGIS_ENCRYPTION_KEY_HEX", "")
            if raw:
                key_bytes = bytes.fromhex(raw)
            else:
                import secrets as _sec
                key_bytes = _sec.token_bytes(32)
                logger.warning("encryption_ephemeral_key_generated",
                               note="Use AEGIS_ENCRYPTION_KEY_HEX in dev; KMS in production")

        async with self._lock:
            self._cache[cache_key] = _CacheEntry(key_bytes, ttl=_CACHE_TTL)
        return key_bytes

    def get_encryption_key_sync(self) -> bytes:
        """Fetch a Data Encryption Key (DEK) synchronously."""
        cache_key = "encryption_dek"
        
        with self._sync_lock:
            entry = self._cache.get(cache_key)
            if entry and entry.is_valid():
                return entry.value

            if self._kms_client and self._settings.gcp.use_gcp:
                key_bytes = self._fetch_kms_dek()
            else:
                raw = os.environ.get("AEGIS_ENCRYPTION_KEY_HEX", "")
                if raw:
                    key_bytes = bytes.fromhex(raw)
                else:
                    import secrets as _sec
                    key_bytes = _sec.token_bytes(32)
                    self._cache[cache_key] = _CacheEntry(key_bytes, ttl=_CACHE_TTL)
            return key_bytes

    # ─────────────────────────────────────────────────────────────────
    # LLM API Keys
    # ─────────────────────────────────────────────────────────────────

    async def get_llm_api_key(self, provider: str) -> str:
        """Fetch LLM provider API key."""
        secret_map = {
            "openai": self._settings.llm.openai_api_key_secret,
            "anthropic": self._settings.llm.anthropic_api_key_secret,
            "google": self._settings.llm.google_api_key_secret,
        }
        secret_name = secret_map.get(provider.lower(), f"aegis-ai-{provider}-key")
        env_var = f"AEGIS_{provider.upper()}_API_KEY"
        raw = await self._get_key(
            cache_key=f"llm_{provider}",
            secret_name=secret_name,
            env_var=env_var,
        )
        return raw.decode("utf-8").strip()

    # ─────────────────────────────────────────────────────────────────
    # Generic Secret Fetch
    # ─────────────────────────────────────────────────────────────────

    async def _get_key(
        self,
        cache_key: str,
        secret_name: str,
        env_var: str = "",
        local_path: Optional[str] = None,
    ) -> bytes:
        """Fetch a key with caching: GCP SM → env var → local file → error."""
        async with self._lock:
            entry = self._cache.get(cache_key)
            if entry and entry.is_valid():
                return entry.value

        value = await asyncio.to_thread(
            self._sync_fetch,
            secret_name=secret_name,
            env_var=env_var,
            local_path=local_path,
        )

        async with self._lock:
            self._cache[cache_key] = _CacheEntry(value, ttl=_CACHE_TTL)
        return value

    def _sync_fetch(
        self,
        secret_name: str,
        env_var: str = "",
        local_path: Optional[str] = None,
    ) -> bytes:
        """Synchronous fetch chain: GCP SM → env → local file."""
        # 1. GCP Secret Manager
        if self._sm_client and self._settings.gcp.use_gcp and self._settings.gcp.project_id:
            try:
                project_id = self._settings.gcp.project_id
                name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
                response = self._sm_client.access_secret_version(request={"name": name})
                data = response.payload.data
                logger.debug("key_fetched_from_secret_manager", secret=secret_name)
                return data
            except Exception as exc:
                logger.warning("secret_manager_fetch_failed", secret=secret_name, error=str(exc))

        # 2. Environment variable (raw value or file path)
        if env_var:
            env_val = os.environ.get(env_var, "")
            if env_val:
                # Could be a file path or raw key value
                if os.path.isfile(env_val):
                    with open(env_val, "rb") as f:
                        logger.debug("key_loaded_from_env_file", env=env_var)
                        return f.read()
                logger.debug("key_loaded_from_env_var", env=env_var)
                return env_val.encode("utf-8")

        # 3. Local file path (dev/test only)
        if local_path and os.path.isfile(local_path):
            logger.warning(
                "key_loaded_from_local_file",
                path=local_path,
                note="NOT suitable for production",
            )
            with open(local_path, "rb") as f:
                return f.read()

        raise RuntimeError(
            f"Cannot fetch key '{secret_name}'. "
            "Configure GCP Secret Manager, set the env var, or provide a local path."
        )

    def _fetch_kms_dek(self) -> bytes:
        """Fetch and decrypt DEK from GCP KMS (synchronous)."""
        cfg = self._settings.gcp
        encrypted_dek_env = os.environ.get("AEGIS_ENCRYPTED_DEK_B64", "")
        if not encrypted_dek_env:
            raise RuntimeError("AEGIS_ENCRYPTED_DEK_B64 must be set when using KMS")

        import base64
        encrypted_dek = base64.b64decode(encrypted_dek_env)
        key_name = (
            f"projects/{cfg.project_id}/locations/{cfg.kms_location}"
            f"/keyRings/{cfg.kms_key_ring}/cryptoKeys/{cfg.kms_crypto_key}"
        )
        response = self._kms_client.decrypt(  # type: ignore[union-attr]
            request={"name": key_name, "ciphertext": encrypted_dek}
        )
        return response.plaintext
