"""
aegis_ai.crypto.token_signer
================================
HMAC-SHA256 token and event signing for audit trail integrity.

Security:
- HMAC-SHA256 with 256-bit keys (from Secret Manager)
- Constant-time comparison on verification (hmac.compare_digest)
- Separate sign/verify interface to prevent key confusion

OWASP: A02:2021-Cryptographic Failures
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict

import structlog

logger = structlog.get_logger(__name__)


class TokenSigner:
    """
    Signs and verifies data payloads using HMAC-SHA256.

    Used for:
    - Audit event integrity (sign before storage)
    - Inter-service tokens
    - Webhook payload verification
    """

    def __init__(self, signing_key: bytes) -> None:
        """
        Args:
            signing_key: 256-bit (32-byte) HMAC key bytes.
        """
        if len(signing_key) < 32:
            raise ValueError("Signing key must be at least 32 bytes (256 bits)")
        self._key = signing_key

    def sign(self, payload: bytes) -> str:
        """
        Compute HMAC-SHA256 signature for payload.

        Args:
            payload: Raw bytes to sign.

        Returns:
            URL-safe base64-encoded HMAC signature string.
        """
        mac = hmac.new(self._key, payload, hashlib.sha256)
        sig = base64.urlsafe_b64encode(mac.digest()).decode("ascii")
        return sig

    def verify(self, payload: bytes, signature: str) -> bool:
        """
        Verify an HMAC-SHA256 signature using constant-time comparison.

        Args:
            payload: Original payload bytes.
            signature: Expected signature (base64-encoded).

        Returns:
            True if signature is valid, False otherwise.
        """
        try:
            expected_bytes = base64.urlsafe_b64decode(signature + "==")
        except Exception:
            return False

        mac = hmac.new(self._key, payload, hashlib.sha256)
        return hmac.compare_digest(mac.digest(), expected_bytes)

    def sign_dict(self, data: Dict[str, Any]) -> str:
        """
        Sign a dict by deterministically serializing it as JSON.

        Keys are sorted to ensure consistent ordering.

        Args:
            data: Dictionary to sign.

        Returns:
            HMAC-SHA256 signature string.
        """
        payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self.sign(payload)

    def verify_dict(self, data: Dict[str, Any], signature: str) -> bool:
        """Verify a dict signature."""
        payload = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self.verify(payload, signature)

    @staticmethod
    def compute_hmac(key: bytes, data: bytes) -> str:
        """
        Standalone HMAC computation (class-level utility).

        Args:
            key: HMAC key bytes.
            data: Data to sign.

        Returns:
            Hex-encoded HMAC-SHA256 digest.
        """
        return hmac.new(key, data, hashlib.sha256).hexdigest()

    @staticmethod
    def compare_hmac(key: bytes, data: bytes, expected_hex: str) -> bool:
        """Constant-time HMAC comparison using hex-encoded digest."""
        actual = hmac.new(key, data, hashlib.sha256).hexdigest()
        return hmac.compare_digest(actual, expected_hex)
