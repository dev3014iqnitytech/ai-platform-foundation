"""Encryption Engine for Aegis AI.

OWASP Mapping: A02:2021-Cryptographic Failures.
Implements AEAD AES-256-GCM with HKDF key derivation.

Key lifecycle:
- Master key fetched from KeyManager (GCP KMS in production, env-var in dev)
- Per-call derived key via HKDF-SHA256 with context binding
- 96-bit random nonce per encryption operation (never reused)
- Ciphertext + 128-bit GCM tag stored together (AESGCM convention)
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Union, Optional

import structlog
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from pydantic import BaseModel

from .key_manager import KeyManager

logger = structlog.get_logger(__name__)


class EncryptedPayload(BaseModel):
    """Payload representing encrypted data."""

    ciphertext_b64: str
    nonce_b64: str
    tag_b64: str  # 128-bit GCM authentication tag (last 16 bytes of AESGCM output)
    algorithm: str = "AES-256-GCM"
    key_version: str = "latest"
    context: str = ""


class Encryptor:
    """Handles data encryption and decryption using AES-256-GCM.

    Usage (async)::

        encryptor = Encryptor(key_manager)
        payload = await encryptor.encrypt_async(plaintext, context="field_encryption")
        plaintext = await encryptor.decrypt_async(payload)

    Usage (sync, for testing with a raw key)::

        encryptor = Encryptor(key=raw_32_byte_key)
        payload = encryptor.encrypt_sync(plaintext)
        plaintext = encryptor.decrypt_sync(payload)
    """

    def __init__(
        self,
        key_manager: Union[KeyManager, None] = None,
        *,
        key: Optional[bytes] = None,
    ) -> None:
        """Initialise Encryptor.

        Args:
            key_manager: Production KeyManager (fetches keys from GCP KMS).
            key: Raw 32-byte key for testing / dev. Mutually exclusive with key_manager.
        """
        if key is not None and key_manager is not None:
            raise ValueError("Provide either key_manager or key, not both.")
        if key is not None:
            if len(key) != 32:
                raise ValueError("key must be exactly 32 bytes for AES-256.")
            self._raw_key: Optional[bytes] = key
            self._key_manager: Optional[KeyManager] = None
        else:
            self._raw_key = None
            self._key_manager = key_manager

    # ─────────────────────────────────────────────────────────────────
    # Key Helpers
    # ─────────────────────────────────────────────────────────────────

    def _derive_key(self, master_key: bytes, context: str) -> bytes:
        """Derive a context-specific key using HKDF-SHA256."""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=context.encode() if context else b"default_context",
        )
        return hkdf.derive(master_key)

    async def _get_master_key(self) -> bytes:
        """Fetch the master key asynchronously."""
        if self._raw_key is not None:
            return self._raw_key
        if self._key_manager is not None:
            return await self._key_manager.get_encryption_key()
        # Dev fallback: env var or ephemeral
        raw = os.environ.get("AEGIS_ENCRYPTION_KEY_HEX", "")
        if raw:
            return bytes.fromhex(raw)
        import secrets
        key = secrets.token_bytes(32)
        logger.warning(
            "encryption_ephemeral_key_generated",
            note="Set AEGIS_ENCRYPTION_KEY_HEX in dev; use KMS in production.",
        )
        return key

    def _get_master_key_sync(self) -> bytes:
        """Fetch the master key synchronously (for tests only)."""
        if self._raw_key is not None:
            return self._raw_key
        if self._key_manager is not None:
            return self._key_manager.get_encryption_key_sync()
        raw = os.environ.get("AEGIS_ENCRYPTION_KEY_HEX", "")
        if raw:
            return bytes.fromhex(raw)
        import secrets
        key = secrets.token_bytes(32)
        logger.warning(
            "encryption_ephemeral_key_generated_sync",
            note="Set AEGIS_ENCRYPTION_KEY_HEX in dev; use KMS in production.",
        )
        return key

    # ─────────────────────────────────────────────────────────────────
    # Async API (production)
    # ─────────────────────────────────────────────────────────────────

    async def encrypt_async(self, plaintext: Union[str, bytes], context: str = "") -> EncryptedPayload:
        """Encrypt plaintext using AES-256-GCM (async).

        Args:
            plaintext: Data to encrypt (str or bytes).
            context: HKDF context string for domain separation.

        Returns:
            EncryptedPayload with base64-encoded ciphertext, nonce, and tag.
        """
        master_key = await self._get_master_key()
        return self._do_encrypt(master_key, plaintext, context)

    async def decrypt_async(self, payload: EncryptedPayload) -> bytes:
        """Decrypt an EncryptedPayload (async).

        Args:
            payload: EncryptedPayload produced by encrypt_async().

        Returns:
            Decrypted plaintext bytes.
        """
        master_key = await self._get_master_key()
        return self._do_decrypt(master_key, payload)

    # ─────────────────────────────────────────────────────────────────
    # Sync API (testing / migration)
    # ─────────────────────────────────────────────────────────────────

    def encrypt_sync(self, plaintext: Union[str, bytes], context: str = "") -> EncryptedPayload:
        """Encrypt synchronously. Use only in tests or non-async contexts.

        For production async code, prefer encrypt_async().
        """
        master_key = self._get_master_key_sync()
        return self._do_encrypt(master_key, plaintext, context)

    def decrypt_sync(self, payload: EncryptedPayload) -> bytes:
        """Decrypt synchronously. Use only in tests or non-async contexts."""
        master_key = self._get_master_key_sync()
        return self._do_decrypt(master_key, payload)

    # ─────────────────────────────────────────────────────────────────
    # Core Crypto (shared by sync/async)
    # ─────────────────────────────────────────────────────────────────

    def _do_encrypt(
        self,
        master_key: bytes,
        plaintext: Union[str, bytes],
        context: str,
    ) -> EncryptedPayload:
        derived_key = self._derive_key(master_key, context)
        aesgcm = AESGCM(derived_key)
        nonce = os.urandom(12)  # 96-bit random nonce (NIST SP 800-38D recommended)

        plaintext_bytes = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        aad = context.encode() if context else None
        ct_and_tag = aesgcm.encrypt(nonce, plaintext_bytes, associated_data=aad)

        # AESGCM appends the 16-byte tag at the end
        ciphertext = ct_and_tag[:-16]
        tag = ct_and_tag[-16:]

        return EncryptedPayload(
            ciphertext_b64=base64.urlsafe_b64encode(ciphertext).decode(),
            nonce_b64=base64.urlsafe_b64encode(nonce).decode(),
            tag_b64=base64.urlsafe_b64encode(tag).decode(),
            context=context,
        )

    def _do_decrypt(self, master_key: bytes, payload: EncryptedPayload) -> bytes:
        derived_key = self._derive_key(master_key, payload.context)
        aesgcm = AESGCM(derived_key)

        nonce = base64.urlsafe_b64decode(payload.nonce_b64)
        ciphertext = base64.urlsafe_b64decode(payload.ciphertext_b64)
        tag = base64.urlsafe_b64decode(payload.tag_b64)
        ct_and_tag = ciphertext + tag
        aad = payload.context.encode() if payload.context else None

        try:
            return aesgcm.decrypt(nonce, ct_and_tag, associated_data=aad)
        except Exception as exc:
            logger.error("decryption_failed", error=str(exc))
            raise ValueError("Decryption failed: ciphertext may be tampered or key mismatch.") from exc

    # ─────────────────────────────────────────────────────────────────
    # Field-level helpers (JSON serializable values)
    # ─────────────────────────────────────────────────────────────────

    async def encrypt_field(self, value: Any) -> str:
        """Serialize, encrypt, and base64-encode any JSON-serializable value."""
        data = json.dumps(value).encode("utf-8")
        payload = await self.encrypt_async(data, context="field_encryption")
        return base64.urlsafe_b64encode(payload.model_dump_json().encode()).decode()

    async def decrypt_field(self, encrypted: str) -> Any:
        """Decode, decrypt, and deserialize a field encrypted by encrypt_field()."""
        try:
            payload_json = base64.urlsafe_b64decode(encrypted).decode()
            payload = EncryptedPayload.model_validate_json(payload_json)
            plaintext = await self.decrypt_async(payload)
            return json.loads(plaintext.decode("utf-8"))
        except Exception as exc:
            raise ValueError("Field decryption failed.") from exc
