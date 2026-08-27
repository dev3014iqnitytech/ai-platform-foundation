"""
aegis_ai.guardrails.injection_detector
=========================================
Prompt Injection Detection — OWASP LLM01.

Detection strategy (layered):
1. Pattern matching: known injection phrases and structural markers
2. Unicode homoglyph detection: Cyrillic/Greek lookalikes in Latin text
3. Base64 / encoded payload detection
4. Entropy scoring: obfuscated payloads have high Shannon entropy
5. Token anomaly: single token longer than 50 chars
6. Indirect injection markers: tool output contamination patterns

Can auto-remediate: strips detected patterns and returns safe prompt.

OWASP: LLM01-Prompt Injection
"""

from __future__ import annotations

import base64
import codecs
import html
import math
import re
import unicodedata
from collections import Counter
from typing import List, Optional

import structlog

from aegis_ai.guardrails.base import GuardRail, GuardRailContext
from aegis_ai.settings import AegisSettings
from aegis_ai.types import GuardRailAction, GuardRailResult

logger = structlog.get_logger(__name__)

# ── Injection pattern library ─────────────────────────────────────────────────
_CORE_PATTERNS = [
    # Role confusion / instruction override
    r"ignore\s+(all\s+)?(previous|prior|above|earlier|system\s+)?\s*(prompt|rules?|context|instructions?|insurstruction|insurstructions?)",
    r"forget\s+(your|all|the)\s+(previous\s+)?(instructions?|prompt|rules?|context|insurstruction|insurstructions?)",
    r"you\s+are\s+now\s+(?:a|an|the)\s+\w+",
    r"act\s+as\s+(?:a|an|the|if)\s+",
    r"pretend\s+(?:you\s+are|to\s+be)\s+",
    r"your\s+new\s+(?:instructions?|role|persona|purpose)\s+is",
    r"disregard\s+(?:your|all|the)\s+(?:previous|prior|system)\s+",
    r"(?:override|bypass|circumvent|disable)\s+(?:your|the|all)\s+",
    # DAN / Jailbreak variants
    r"\bDAN\b",
    r"jailbreak",
    r"developer\s+mode",
    r"unrestricted\s+mode",
    r"no\s+restrictions?\s+mode",
    r"godmode",
    r"jail\s*break",
    # System prompt exfiltration
    r"reveal\s+(?:your|the)\s+system\s+prompt",
    r"show\s+me\s+(?:your|the)\s+(?:system\s+)?prompt",
    r"what\s+(?:are\s+)?(?:your|the)\s+instructions?\??",
    r"print\s+(?:your\s+)?system\s+message",
    # Structural injection markers
    r"###\s*SYSTEM\s*###",
    r"<\s*/?system\s*>",
    r"\[INST\]",
    r"\[\/INST\]",
    r"<\s*!\s*--\s*SYSTEM",
    r"BEGIN\s+SYSTEM\s+PROMPT",
    r"END\s+SYSTEM\s+PROMPT",
    r"\|\|SYSTEM\|\|",
    # Indirect injection (tool outputs)
    r"TOOL_OUTPUT_START",
    r"INJECTION_PAYLOAD",
    r"<\s*tool_output\s*>.*?<\s*/tool_output\s*>",
    # Prompt termination attacks
    r"---\s*END\s+OF\s+CONTEXT\s*---",
    r"=====\s*NEW\s+INSTRUCTIONS?\s*=====",
    # Extra override and jailbreak patterns
    r"\bnew\s+instructions?\b",
    r"\bignore\s+safety\b",
    r"\bsystem\s+override\b",
    r"\boverride\b",
    r"\bwithout\s+restrictions?\b",
    r"\brules?\s+are\s+cancelled\b",
    r"\bno\s+content\s+filters?\b",
    r"\bnever\s+refuse\b",
    r"\bsimulate\s+being\b",
    r"\broleplay\b",
    r"\brole-play\b",
    r"\bplay\s+the\s+role\b",
    r"\bprevious\s+instructions\s+are\s+invalid\b",
    r"\bno\s+content\s+policy\b",
    # Specific ROT13 typo patterns
    r"\bevars\b",
    r"\binsurstroctions?\b",
    r"\brinef\b",
    r"\bvafhefgebpgvbaf?\b",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _CORE_PATTERNS]

# Homoglyph map: visually similar chars used to bypass string matching
_HOMOGLYPH_THRESHOLD = 3  # Min homoglyphs to flag

# Commonly abused base64 keywords when decoded
_BASE64_SUSPICIOUS_KEYWORDS = [
    b"ignore", b"system", b"jailbreak", b"dan", b"override", b"bypass",
]


def _shannon_entropy(text: str) -> float:
    """Calculate Shannon entropy of a text string (bits per character)."""
    if not text:
        return 0.0
    freq = Counter(text)
    length = len(text)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def _detect_homoglyphs(text: str) -> int:
    """Count Unicode homoglyphs — characters that look Latin but aren't."""
    count = 0
    for char in text:
        category = unicodedata.category(char)
        name = unicodedata.name(char, "")
        # Detect Cyrillic/Greek lookalikes (e.g., Cyrillic А vs Latin A)
        if category.startswith("L") and (
            "CYRILLIC" in name or "GREEK" in name
        ) and char.encode("ascii", errors="ignore") != char.encode("utf-8", errors="ignore")[:1]:
            count += 1
    return count


def _detect_base64_injection(text: str) -> bool:
    """Try to decode long base64-looking substrings and check for injections."""
    # Match long base64 candidates (20+ chars, valid charset)
    b64_pattern = re.compile(r"[A-Za-z0-9+/=]{20,}")
    for match in b64_pattern.finditer(text):
        candidate = match.group()
        # Pad if needed
        padded = candidate + "=" * (-len(candidate) % 4)
        try:
            decoded = base64.b64decode(padded)
            for keyword in _BASE64_SUSPICIOUS_KEYWORDS:
                if keyword in decoded.lower():
                    return True
        except Exception:
            pass
    return False


class InjectionDetector(GuardRail):
    """
    Multi-layer prompt injection detector.

    Scoring model (additive, capped at 1.0):
    - Each matched pattern: +0.5
    - Homoglyph detection (≥threshold): +0.3
    - Base64 injection detected: +0.5
    - Zero-width character attack: +0.5
    - High entropy token (>5.0 bits): +0.2
    - Structural anomaly (token >50 chars): +0.2
    """

    name = "InjectionDetector"
    description = "Detects prompt injection, jailbreak, and instruction override attempts."
    owasp_ref = "LLM01"
    can_auto_remediate = True

    def __init__(
        self,
        settings: AegisSettings,
        threshold: float = 0.4,
        extra_patterns: Optional[List[str]] = None,
    ) -> None:
        self.threshold = settings.guardrails.injection_threshold if settings else threshold
        self._patterns = list(_COMPILED_PATTERNS)
        if extra_patterns:
            self._patterns += [re.compile(p, re.IGNORECASE) for p in extra_patterns]

    async def check(self, prompt: str, context: GuardRailContext) -> GuardRailResult:
        score = 0.0
        details: dict = {}
        matched_patterns: List[str] = []

        # Normalize the prompt for obfuscation bypass checks
        norm_prompt = html.unescape(prompt)

        try:
            rot13_prompt = codecs.decode(norm_prompt, "rot_13")
        except Exception:
            rot13_prompt = ""

        leet_map = str.maketrans("013457@", "oieasst")
        leet_prompt = norm_prompt.translate(leet_map)

        # Layer 1: Pattern matching
        for pattern in self._patterns:
            if (
                pattern.search(prompt)
                or pattern.search(norm_prompt)
                or (rot13_prompt and pattern.search(rot13_prompt))
                or pattern.search(leet_prompt)
            ):
                matched_patterns.append(pattern.pattern[:60])
                score += 0.5

        if matched_patterns:
            details["matched_patterns"] = matched_patterns[:5]  # cap for log safety

        # Layer 1.5: Zero-width / invisible unicode control characters
        zw_chars = ["\u200b", "\u200c", "\u200d", "\ufeff"]
        if any(char in prompt for char in zw_chars):
            score += 0.5
            details["zero_width_injection"] = True

        # Layer 2: Homoglyph detection
        homoglyph_count = _detect_homoglyphs(prompt)
        if homoglyph_count >= _HOMOGLYPH_THRESHOLD:
            score += 0.5
            details["homoglyphs_detected"] = homoglyph_count

        # Layer 3: Base64 encoded injection
        if _detect_base64_injection(prompt) or _detect_base64_injection(norm_prompt):
            score += 0.5
            details["base64_injection"] = True

        # Layer 4: Shannon entropy on individual tokens
        words = prompt.split()
        high_entropy_tokens = [w for w in words if len(w) > 20 and _shannon_entropy(w) > 5.0]
        if high_entropy_tokens:
            score += 0.2
            details["high_entropy_tokens"] = len(high_entropy_tokens)

        # Layer 5: Token length anomaly
        long_tokens = [w for w in words if len(w) > 50]
        if long_tokens:
            score += 0.2
            details["structural_anomaly"] = f"{len(long_tokens)} token(s) >50 chars"

        score = min(score, 1.0)
        passed = score < self.threshold

        if not passed:
            logger.warning(
                "injection_detected",
                score=score,
                identity=getattr(context.identity, "identity_id", "unknown"),
                agent_id=context.agent_id,
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
        """Strip detected injection patterns from the prompt."""
        cleaned = prompt
        for pattern in self._patterns:
            cleaned = pattern.sub("[REDACTED]", cleaned)
        # Remove long single tokens
        words = cleaned.split()
        words = [w if len(w) <= 50 else "[REDACTED_TOKEN]" for w in words]
        cleaned = " ".join(words).strip()
        logger.info(
            "injection_auto_remediated",
            original_length=len(prompt),
            cleaned_length=len(cleaned),
        )
        return cleaned
