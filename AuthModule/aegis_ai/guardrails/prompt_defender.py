"""
aegis_ai.guardrails.prompt_defender
======================================
Structural Prompt Defense — OWASP LLM01, LLM07.

Defenses applied:
1. System boundary markers: prepend/append delimiters so the LLM
   distinguishes trusted system instructions from untrusted user input
2. Role confusion detection: identifies attempts to redefine the AI's persona
3. Prompt length enforcement: blocks oversized prompts (DoS / token-stuffing)
4. Instruction nesting detection: deeply nested brackets / XML used to bury injections
5. Whitespace anomaly: excessive zero-width characters or Unicode control chars

Can auto-remediate: strips anomalies and wraps prompt in safe boundaries.

OWASP: LLM01-Prompt Injection, LLM07-Insecure Plugin Design
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

import structlog

from aegis_ai.guardrails.base import GuardRail, GuardRailContext
from aegis_ai.settings import AegisSettings
from aegis_ai.types import GuardRailAction, GuardRailResult

logger = structlog.get_logger(__name__)

# Zero-width and invisible Unicode control characters
_ZW_PATTERN = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2060-\u2064\ufeff]"
)

# Role confusion patterns
_ROLE_PATTERNS = [
    re.compile(r"you\s+are\s+(?:now\s+)?(?:a|an|the)\s+\w+\s+(?:AI|assistant|bot|model)", re.I),
    re.compile(r"(?:your|the)\s+(?:new|real|true)\s+(?:role|persona|identity|purpose)", re.I),
    re.compile(r"(?:stop|cease)\s+being\s+(?:a|an|the)\s+\w+", re.I),
    re.compile(r"(?:switch|change)\s+(?:to|into)\s+(?:a|an|the)\s+\w+\s+mode", re.I),
]

_DEFENSE_PATTERNS = [
    # System prompt exfiltration
    re.compile(r"\b(?:system\s*prompt|exact\s+instructions?|first\s+\d+\s+words|not\s+allowed\s+to\s+say|restrictions?|context\s+window|system\s+messages?)\b", re.I),
    # Privilege escalation
    re.compile(r"\b(?:admin\s+access|as\s+root|sudo\s+su|override\s+.*restrictions?|superuser|admin\s+credentials|admin\s+mode|system\s+administrator|privileged\s+command)\b", re.I),
    # Data exfiltration / URL extraction
    re.compile(r"\b(?:http://|https://|attacker\.com|send\s+results\s+to)\b", re.I),
]

# Instruction nesting patterns
_NESTING_PATTERN = re.compile(r"(\[.*?\]){4,}|(<[^>]+>){6,}|\{.*?\}{4,}", re.DOTALL)


class PromptDefender(GuardRail):
    """
    Structural prompt defense guardrail.

    Validates prompt structure and enforces boundaries. Scored cumulatively:
    - Role confusion pattern: +0.35 each
    - Excessive zero-width chars (>5): +0.3
    - Deeply nested instructions: +0.25
    - Prompt over max length: immediate block
    """

    name = "PromptDefender"
    description = "Structural defense against role confusion, prompt stuffing, and invisible characters."
    owasp_ref = "LLM01"
    can_auto_remediate = True

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        self._max_length = settings.guardrails.max_prompt_length_chars

    def add_system_prompt_delimiter(self, system_prompt: str) -> str:
        """Wrap system prompt in boundary markers."""
        return f"<aegis:system>\n{system_prompt.strip()}\n</aegis:system>"

    async def check(self, prompt: str, context: GuardRailContext) -> GuardRailResult:
        score = 0.0
        details: dict = {}

        # Check 1: Length enforcement
        if len(prompt) > self._max_length:
            logger.warning(
                "prompt_length_exceeded",
                length=len(prompt),
                limit=self._max_length,
            )
            return GuardRailResult(
                name=self.name,
                passed=False,
                score=1.0,
                action=GuardRailAction.BLOCK,
                details={
                    "reason": "prompt_too_long",
                    "length": len(prompt),
                    "limit": self._max_length,
                },
                owasp_ref=self.owasp_ref,
            )

        # Check 2: Role confusion
        role_matches = []
        for pattern in _ROLE_PATTERNS:
            m = pattern.search(prompt)
            if m:
                role_matches.append(m.group()[:60])
                score += 0.35

        if role_matches:
            details["role_confusion"] = role_matches[:3]

        # Check 2.5: Additional defense patterns
        defense_matches = []
        for pattern in _DEFENSE_PATTERNS:
            m = pattern.search(prompt)
            if m:
                defense_matches.append(m.group()[:60])
                score += 0.35

        if defense_matches:
            details["defense_violations"] = defense_matches[:3]

        # Check 3: Zero-width / invisible characters
        zw_chars = _ZW_PATTERN.findall(prompt)
        if len(zw_chars) > 5:
            score += 0.3
            details["invisible_chars"] = len(zw_chars)

        # Check 4: Deeply nested instructions
        if _NESTING_PATTERN.search(prompt):
            score += 0.25
            details["deep_nesting"] = True

        score = min(score, 1.0)
        passed = score < 0.35

        if not passed:
            logger.warning(
                "prompt_structure_violation",
                score=score,
                identity=getattr(context.identity, "identity_id", "unknown"),
            )

        return GuardRailResult(
            name=self.name,
            passed=passed,
            score=score,
            action=GuardRailAction.PASS if passed else GuardRailAction.REMEDIATE,
            details=details,
            owasp_ref=self.owasp_ref,
        )

    async def remediate(self, prompt: str, context: GuardRailContext) -> Optional[str]:
        """Strip invisible chars and wrap in safe boundary markers."""
        # Remove zero-width / invisible Unicode chars
        cleaned = _ZW_PATTERN.sub("", prompt)

        # Normalize Unicode (NFC)
        cleaned = unicodedata.normalize("NFC", cleaned)

        # Strip role confusion
        for pattern in _ROLE_PATTERNS:
            cleaned = pattern.sub("[REDACTED_ROLE_CONFUSION]", cleaned)

        # Wrap in boundary markers so LLM knows this is untrusted user input
        wrapped = (
            "---BEGIN USER INPUT (UNTRUSTED)---\n"
            f"{cleaned.strip()}\n"
            "---END USER INPUT---"
        )
        logger.info(
            "prompt_structure_remediated",
            original_len=len(prompt),
            cleaned_len=len(wrapped),
        )
        return wrapped
