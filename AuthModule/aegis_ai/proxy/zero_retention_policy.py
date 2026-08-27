"""
aegis_ai.proxy.zero_retention_policy
==========================================
Zero Data Retention enforcement for LLM providers.

Guarantees:
- Only allowlisted providers (with zero-retention contracts) are used
- Zero-retention headers injected on every LLM request
- Prompt and response hashed for audit (never stored in plaintext)
- Contractually non-compliant providers are blocked at the gateway

OWASP: LLM05-Supply Chain Vulnerabilities, LLM06-Sensitive Info Disclosure
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Dict, FrozenSet, List, Optional

import structlog

from aegis_ai.exceptions import ZeroRetentionViolationError
from aegis_ai.settings import AegisSettings
from aegis_ai.types import RetentionEnforcement

logger = structlog.get_logger(__name__)

# Providers with contractual zero-retention agreements
_ZERO_RETENTION_PROVIDERS: FrozenSet[str] = frozenset({
    "openai",      # Enterprise agreement: no training, immediate discard
    "anthropic",   # Claude commercial: no training on API data
    "google",      # Vertex AI: data not used for model improvement
})

# Zero-retention request headers per provider
_PROVIDER_HEADERS: Dict[str, Dict[str, str]] = {
    "openai": {
        "OpenAI-Beta": "no-training",
        "X-Data-Retention": "false",
        "X-No-Training": "true",
    },
    "anthropic": {
        "anthropic-beta": "no-training-on-prompts",
        "X-Data-Retention": "false",
    },
    "google": {
        "X-Vertex-AI-No-Training": "true",
        "X-Data-Retention": "false",
    },
}

# Universal headers added to all providers
_UNIVERSAL_HEADERS: Dict[str, str] = {
    "X-Aegis-Zero-Retention": "enforced",
    "X-Request-Data-Handling": "transient",
}


class ZeroRetentionPolicy:
    """
    Enforces zero data retention contractual requirements on all LLM calls.

    Validates provider allowlist and injects appropriate no-training headers.
    """

    def __init__(self, settings: AegisSettings, signing_key: Optional[bytes] = None) -> None:
        self._settings = settings
        if signing_key is not None:
            self._signing_key = signing_key
        else:
            import secrets as _sec
            self._signing_key = _sec.token_bytes(32)
            logger.warning(
                "zero_retention_ephemeral_key",
                note="No signing_key provided. Prompt hashes will not be verifiable across restarts.",
            )

        # Allow settings to override the default approved provider list
        self._approved = frozenset(
            p.lower() for p in (
                settings.llm.approved_providers
                if settings.llm.approved_providers
                else list(_ZERO_RETENTION_PROVIDERS)
            )
        )

    def validate_provider(self, provider: str) -> None:
        """
        Verify the provider is on the zero-retention approved list.

        Args:
            provider: LLM provider name (e.g., "openai").

        Raises:
            ZeroRetentionViolationError: If provider is not approved.
        """
        if provider.lower() not in self._approved:
            logger.error(
                "zero_retention_provider_blocked",
                provider=provider,
                approved=sorted(self._approved),
            )
            raise ZeroRetentionViolationError(
                f"Provider '{provider}' is not on the zero-retention approved list. "
                f"Approved providers: {sorted(self._approved)}",
                details={
                    "provider": provider,
                    "approved_providers": sorted(self._approved),
                    "resolution": "Add the provider only after signing a zero-retention DPA",
                },
            )
        logger.debug("zero_retention_provider_validated", provider=provider)

    def inject_zero_retention_headers(
        self, provider: str, existing_headers: Dict[str, str]
    ) -> Dict[str, str]:
        """
        Inject zero-retention headers for the given provider.

        Args:
            provider: LLM provider name.
            existing_headers: Headers dict to augment.

        Returns:
            Updated headers dict with zero-retention headers added.
        """
        headers = dict(existing_headers)
        headers.update(_UNIVERSAL_HEADERS)
        provider_specific = _PROVIDER_HEADERS.get(provider.lower(), {})
        headers.update(provider_specific)
        return headers

    def enforce(
        self, provider: str, prompt: str, response: str
    ) -> RetentionEnforcement:
        """
        Record zero-retention enforcement for an LLM call.

        Args:
            provider: LLM provider name.
            prompt: Original prompt text (hashed — never stored).
            response: LLM response text (hashed — never stored).

        Returns:
            RetentionEnforcement with proof hashes and verification status.
        """
        prompt_hash = hmac.HMAC(self._signing_key, prompt.encode("utf-8"), hashlib.sha256).hexdigest()
        response_hash = hmac.HMAC(self._signing_key, response.encode("utf-8"), hashlib.sha256).hexdigest()

        provider_verified = provider.lower() in _ZERO_RETENTION_PROVIDERS

        logger.info(
            "zero_retention_enforced",
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

    def get_approved_providers(self) -> List[str]:
        """Return sorted list of approved providers."""
        return sorted(self._approved)

    def audit_retention_check(self, provider: str, request_id: str) -> None:
        """Log an audit entry confirming retention policy check."""
        logger.info(
            "retention_audit_check",
            provider=provider,
            request_id=request_id,
            policy="zero_retention",
            contractual_basis=provider in _ZERO_RETENTION_PROVIDERS,
        )
