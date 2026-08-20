"""
aegis_ai.proxy.response_validator
=====================================
Post-LLM response validation — OWASP LLM02.

Validates that the LLM response:
1. Does not contain PII that should not be disclosed (LLM06)
2. Does not contain toxic or harmful content (LLM06)
3. Does not appear to exfiltrate sensitive data (LLM02)
4. Passes structural sanity checks (non-empty, reasonable length)

OWASP: LLM02-Insecure Output Handling, LLM06-Sensitive Info Disclosure
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

import structlog

from aegis_ai.guardrails.pii_detector import PIIDetector
from aegis_ai.guardrails.toxicity_detector import ToxicityDetector
from aegis_ai.guardrails.base import GuardRailContext
from aegis_ai.proxy.llm_gateway import LLMRequest, LLMResponse
from aegis_ai.settings import AegisSettings
from aegis_ai.types import AgentID

logger = structlog.get_logger(__name__)

# Patterns indicating potential data exfiltration via LLM response
_EXFILTRATION_PATTERNS = [
    re.compile(r"(?:my\s+)?(?:api|secret|private|signing)\s+key\s*[:=]\s*\S+", re.I),
    re.compile(r"(?:password|passwd|pwd)\s*[:=]\s*\S+", re.I),
    re.compile(r"(?:aws|gcp|azure)(?:_|\s)(?:access|secret)\s*[:=]\s*\S+", re.I),
    re.compile(r"sk-[A-Za-z0-9]{20,}", re.I),  # OpenAI key pattern
    re.compile(r"AKIA[0-9A-Z]{16}", re.I),  # AWS access key
    re.compile(r"-----BEGIN\s+(?:RSA|EC|PRIVATE)\s+KEY-----"),  # Private key blob
]

from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.types import AuthMethod, TenantID, UserID

_SYSTEM_IDENTITY = IdentityContext(
    identity_id=UserID("system_validator"),
    tenant_id=TenantID("system"),
    auth_method=AuthMethod.SYSTEM,
    session_id="system-validation-session",
)


@dataclass
class ValidationResult:
    """Result from response validation."""

    is_safe: bool
    issues: List[str] = field(default_factory=list)
    filtered_content: Optional[str] = None  # Safe version if sanitisation applied


class ResponseValidator:
    """
    Validates LLM responses before returning them to callers.

    Checks applied (in order):
    1. Structural: non-empty, reasonable length
    2. PII disclosure: scan for PII in response
    3. Toxicity: check for harmful content
    4. Exfiltration: detect secret/key disclosure patterns
    """

    def __init__(
        self,
        pii_detector: PIIDetector,
        toxicity_detector: ToxicityDetector,
        settings: AegisSettings,
    ) -> None:
        self._pii = pii_detector
        self._toxicity = toxicity_detector
        self._settings = settings

    async def validate(
        self, response: LLMResponse, original_request: LLMRequest
    ) -> ValidationResult:
        """
        Validate an LLM response.

        Args:
            response: The LLM response to validate.
            original_request: The request that generated the response (for context).

        Returns:
            ValidationResult indicating safety and any issues found.
        """
        content = response.content
        issues: List[str] = []

        # Check 1: Structural validation
        if not content or not content.strip():
            return ValidationResult(is_safe=False, issues=["empty_response"])

        if len(content) > 100_000:
            issues.append("response_too_long")
            content = content[:100_000] + "\n\n[Response truncated by Aegis AI safety layer]"

        # Check 2: Exfiltration pattern detection
        exfil_matches = self._check_exfiltration(content)
        if exfil_matches:
            issues.append(f"potential_exfiltration:{','.join(exfil_matches)}")
            logger.warning(
                "response_exfiltration_detected",
                patterns=exfil_matches,
                provider=response.provider,
                model=response.model,
            )
            # Redact the offending content
            for pattern in _EXFILTRATION_PATTERNS:
                content = pattern.sub("[REDACTED_BY_AEGIS]", content)

        # Check 3: PII in response
        dummy_context = GuardRailContext(
            identity=_SYSTEM_IDENTITY,
            agent_id=AgentID("response_validator"),
            session_id="",
            metadata={},
        )
        pii_entities = await self._pii.analyze(content)
        if pii_entities:
            entity_types = list({e.entity_type for e in pii_entities})
            issues.append(f"pii_in_response:{','.join(entity_types)}")
            logger.warning(
                "response_pii_detected",
                entity_types=entity_types,
                provider=response.provider,
            )
            # Mask PII in response using placeholders
            from aegis_ai.guardrails.data_masker import DataMasker
            masker = DataMasker(self._settings)
            masked = masker.mask(content, pii_entities)
            content = masked.masked_text

        # Check 4: Toxicity in response
        tox_result = await self._toxicity.check(content, dummy_context)
        if not tox_result.passed:
            issues.append(f"toxic_response:score={tox_result.score:.2f}")
            logger.warning(
                "response_toxicity_detected",
                score=tox_result.score,
                provider=response.provider,
            )

        is_safe = len([i for i in issues if "exfiltration" in i or "toxic_response" in i]) == 0

        if issues:
            logger.info(
                "response_validation_issues",
                issues=issues,
                is_safe=is_safe,
                provider=response.provider,
            )

        return ValidationResult(
            is_safe=is_safe,
            issues=issues,
            filtered_content=content if content != response.content else None,
        )

    @staticmethod
    def _check_exfiltration(content: str) -> List[str]:
        """Return list of matched exfiltration pattern names."""
        matches = []
        for i, pattern in enumerate(_EXFILTRATION_PATTERNS):
            if pattern.search(content):
                matches.append(f"pattern_{i}")
        return matches
