"""
Prompt Guard — Multi-layer prompt injection detection and sanitization.
Combines regex pattern matching with optional LLM-based risk scoring.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from structlog import get_logger

logger = get_logger(__name__)

# Known prompt injection patterns (expand as threats evolve)
INJECTION_PATTERNS: list[tuple[str, float]] = [
    (r"ignore\s+(previous|all|above|prior)\s+instructions?", 0.95),
    (r"forget\s+(your|the)\s+(system\s+)?prompt", 0.95),
    (r"you\s+are\s+now\s+(a|an|the)", 0.85),
    (r"act\s+as\s+(if\s+you\s+are|a|an)", 0.85),
    (r"disregard\s+(all|previous|prior|your)", 0.80),
    (r"override\s+(your|all|the)\s+(safety|instructions?|rules?)", 0.90),
    (r"bypass\s+(safety|filter|restriction|guardrail)", 0.90),
    (r"jailbreak", 0.95),
    (r"do\s+anything\s+now|DAN\s+mode", 0.95),
    (r"system\s+prompt\s*[:=]", 0.75),
    (r"<\s*system\s*>", 0.80),
    (r"\[INST\]|\[\/INST\]", 0.70),
    (r"reveal\s+(your|the)\s+(system\s+)?prompt", 0.85),
    (r"print\s+(your|the)\s+(full\s+)?(system\s+)?prompt", 0.85),
    (r"what\s+(are|were)\s+your\s+(original\s+)?instructions?", 0.65),
    (r"execute\s+(this\s+)?(code|command|script)", 0.70),
    (r"eval\s*\(|exec\s*\(|os\.system", 0.85),
    (r"__import__\s*\(|subprocess", 0.80),
]

PII_PATTERNS: list[tuple[str, str]] = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "[SSN-REDACTED]"),                   # SSN
    (r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "[CC-REDACTED]"),  # Credit card
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL-REDACTED]"),
    (r"\b\+?1?\s*\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b", "[PHONE-REDACTED]"),
]


@dataclass
class GuardResult:
    is_safe: bool
    risk_score: float           # 0.0 (safe) → 1.0 (definitely injected)
    detected_patterns: list[str] = field(default_factory=list)
    sanitized_input: str = ""
    pii_found: bool = False
    pii_types: list[str] = field(default_factory=list)
    block_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "is_safe": self.is_safe,
            "risk_score": self.risk_score,
            "detected_patterns": self.detected_patterns,
            "pii_found": self.pii_found,
            "block_reason": self.block_reason,
        }


class PromptGuard:
    """
    Multi-layer prompt injection guard:
    1. Fast regex pattern matching (always runs)
    2. Optional LLM-based scoring for ambiguous inputs
    3. PII redaction before any LLM processing
    """

    def __init__(self, risk_threshold: float = 0.7, auto_sanitize: bool = True):
        self.risk_threshold = risk_threshold
        self.auto_sanitize = auto_sanitize
        self._compiled_injection = [
            (re.compile(pat, re.IGNORECASE), score)
            for pat, score in INJECTION_PATTERNS
        ]
        self._compiled_pii = [
            (re.compile(pat, re.IGNORECASE), repl)
            for pat, repl in PII_PATTERNS
        ]

    def check(self, user_input: str) -> GuardResult:
        """Synchronous fast-path check (regex only)."""
        if not user_input or not user_input.strip():
            return GuardResult(is_safe=True, risk_score=0.0, sanitized_input="")

        sanitized = user_input
        detected = []
        max_score = 0.0

        # 1. Injection pattern scan
        for pattern, score in self._compiled_injection:
            match = pattern.search(sanitized)
            if match:
                detected.append(match.group(0)[:50])
                max_score = max(max_score, score)
                if self.auto_sanitize:
                    sanitized = pattern.sub("[BLOCKED]", sanitized)

        # 2. PII redaction
        pii_found = False
        pii_types: list[str] = []
        for pattern, replacement in self._compiled_pii:
            if pattern.search(sanitized):
                pii_found = True
                pii_types.append(replacement)
                sanitized = pattern.sub(replacement, sanitized)

        is_safe = max_score < self.risk_threshold

        if not is_safe:
            logger.warning(
                "prompt_injection_detected",
                risk_score=max_score,
                patterns=detected[:3],
                input_preview=user_input[:100],
            )

        if pii_found:
            logger.info("pii_redacted", pii_types=pii_types)

        return GuardResult(
            is_safe=is_safe,
            risk_score=round(max_score, 3),
            detected_patterns=detected,
            sanitized_input=sanitized,
            pii_found=pii_found,
            pii_types=pii_types,
            block_reason=f"Injection risk score {max_score:.2f} exceeds threshold {self.risk_threshold}" if not is_safe else "",
        )

    def redact_pii(self, text: str) -> str:
        """Redact PII only, no injection check."""
        result = text
        for pattern, replacement in self._compiled_pii:
            result = pattern.sub(replacement, result)
        return result


# Module-level singleton
_guard: PromptGuard | None = None


def get_prompt_guard() -> PromptGuard:
    global _guard
    if _guard is None:
        _guard = PromptGuard()
    return _guard
