"""
aegis_ai.auth.mfa_verifier
============================
Multi-Factor Authentication: TOTP and backup codes.

Security guarantees:
- RFC 6238 TOTP with ±1 window tolerance (30-second steps)
- Backup codes: 10 × 8 random chars, stored as SHA-256 hashes
- Constant-time comparison for both TOTP and backup codes
- Used backup codes are immediately invalidated (one-time use)

OWASP: A07:2021-Identification and Authentication Failures
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import List, Optional, Tuple

import pyotp
import structlog

logger = structlog.get_logger(__name__)


class MFAVerifier:
    """
    Handles TOTP-based MFA and backup code verification.

    Usage::

        verifier = MFAVerifier()
        secret, qr_uri = verifier.generate_totp_secret(email="user@example.com")
        # Store secret (encrypted), show qr_uri to user once

        # On login:
        ok = verifier.verify_totp(stored_secret, submitted_code)
    """

    def __init__(self, issuer: str = "AegisAI") -> None:
        self._issuer = issuer

    # ─────────────────────────────────────────────────────────────────
    # TOTP
    # ─────────────────────────────────────────────────────────────────

    def generate_totp_secret(
        self, email: Optional[str] = None
    ) -> Tuple[str, str]:
        """
        Generate a new TOTP secret for a user.

        Args:
            email: User email for the QR URI label.

        Returns:
            Tuple of (base32_secret, otpauth_uri).
            Store the secret (encrypted); show the URI to the user once.
        """
        secret = pyotp.random_base32()
        label = email or "user"
        totp = pyotp.TOTP(secret)
        uri = totp.provisioning_uri(name=label, issuer_name=self._issuer)
        logger.info("totp_secret_generated", email=email)
        return secret, uri

    def verify_totp(
        self, secret: str, code: str, window: int = 1
    ) -> bool:
        """
        Verify a TOTP code.

        Args:
            secret: Base32 TOTP secret (from storage, encrypted).
            code: 6-digit code submitted by the user.
            window: Tolerance window (1 = ±30 seconds).

        Returns:
            True if valid, False otherwise.
        """
        try:
            totp = pyotp.TOTP(secret)
            result = totp.verify(code, valid_window=window)
            if not result:
                logger.warning("totp_verification_failed")
            return result
        except Exception as exc:
            logger.error("totp_error", error=str(exc))
            return False

    # ─────────────────────────────────────────────────────────────────
    # Backup Codes
    # ─────────────────────────────────────────────────────────────────

    def generate_backup_codes(self, count: int = 10) -> List[str]:
        """
        Generate one-time backup codes.

        Args:
            count: Number of codes to generate (default 10).

        Returns:
            List of plaintext backup codes.
            Hash and store these; never store plaintext.
        """
        codes = [
            secrets.token_hex(4).upper()  # 8-char hex: e.g. "A1B2C3D4"
            for _ in range(count)
        ]
        logger.info("backup_codes_generated", count=count)
        return codes

    @staticmethod
    def hash_backup_code(code: str) -> str:
        """Return SHA-256 hex digest of a backup code for storage."""
        return hashlib.sha256(code.upper().encode()).hexdigest()

    def verify_backup_code(
        self,
        hashed_codes: List[str],
        submitted: str,
    ) -> Tuple[bool, List[str]]:
        """
        Verify a backup code against a list of stored SHA-256 hashes or plaintext codes.

        The matched code is removed from the returned list (one-time use).

        Args:
            hashed_codes: List of SHA-256 hashes or plaintext strings of remaining valid codes.
            submitted: Plaintext code submitted by the user.

        Returns:
            Tuple of (matched: bool, remaining_hashed_codes: List[str]).
        """
        submitted_hash = self.hash_backup_code(submitted)
        matched = False
        remaining = []

        for stored_hash in hashed_codes:
            is_match = hmac.compare_digest(stored_hash, submitted_hash) or hmac.compare_digest(stored_hash, submitted)
            if is_match and not matched:
                matched = True  # consume it — do NOT add to remaining
            else:
                remaining.append(stored_hash)

        if matched:
            logger.info("backup_code_used", remaining_count=len(remaining))
        else:
            logger.warning("backup_code_invalid")

        return matched, remaining
