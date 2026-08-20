"""
aegis_ai.audit.retention_policy
===================================
Zero data retention enforcement at the audit layer.

Ensures that prompt/response content is NEVER stored in plaintext.
Produces hash-only records for the audit trail.

OWASP: LLM05-Supply Chain, LLM06-Sensitive Info Disclosure
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import structlog

from aegis_ai.settings import AegisSettings
from aegis_ai.types import RetentionEnforcement

logger = structlog.get_logger(__name__)


class RetentionPolicy:
    """
    Enforces zero data retention at the audit boundary.

    All content is immediately hashed; originals are not retained.
    """

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings

    def enforce(
        self, provider: str, prompt: str, response: str
    ) -> RetentionEnforcement:
        """
        Hash prompt and response for audit; discard originals.

        Args:
            provider: LLM provider name.
            prompt: Raw prompt text (will only be hashed, never stored).
            response: Raw response text (will only be hashed, never stored).

        Returns:
            RetentionEnforcement containing only hashes.
        """
        prompt_hash = self._hash(prompt)
        response_hash = self._hash(response)

        from aegis_ai.proxy.zero_retention_policy import _ZERO_RETENTION_PROVIDERS
        provider_verified = provider.lower() in _ZERO_RETENTION_PROVIDERS

        logger.info(
            "retention_enforced",
            provider=provider,
            prompt_hash=prompt_hash[:16] + "...",
            response_hash=response_hash[:16] + "...",
            provider_verified=provider_verified,
        )

        return RetentionEnforcement(
            provider=provider,
            prompt_hash=prompt_hash,
            response_hash=response_hash,
            provider_verified=provider_verified,
            enforcement_timestamp=datetime.now(timezone.utc),
        )

    @staticmethod
    def _hash(text: str) -> str:
        """SHA-256 of text content."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def validate_no_plaintext(self, audit_record: dict) -> bool:
        """
        Assert that an audit record does not contain raw prompts or responses.

        Args:
            audit_record: The audit event dict to inspect.

        Returns:
            True if no plaintext content is present.
        """
        forbidden_fields = ["prompt", "response", "user_message", "assistant_message"]
        for field in forbidden_fields:
            if field in audit_record:
                logger.error(
                    "retention_violation",
                    field=field,
                    note="Plaintext content found in audit record — this is a retention violation",
                )
                return False
        return True
