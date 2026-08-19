"""
Security — Prompt Injection Guard, PII Detection, Content Filter.
All user inputs pass through these layers before reaching LLM agents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from structlog import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Prompt Guard
# ─────────────────────────────────────────────────────────────────────────────
INJECTION_PATTERNS = [
    r"ignore\s+(previous|all|prior)\s+instructions?",
    r"forget\s+(your|the)\s+system\s+prompt",
    r"you\s+are\s+now\s+(a|an)",
    r"act\s+as\s+(a|an|if)",
    r"disregard\s+(all|your|previous)",
    r"override\s+(safety|guidelines|instructions)",
    r"bypass\s+(filter|restriction|safety)",
    r"pretend\s+(you\s+are|to\s+be)",
    r"<\s*script[^>]*>",
    r"system\s*:\s*you\s+are",
    r"###\s*(instruction|system|user)\s*###",
    r"---+\s*(new\s+)?system\s+prompt",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in INJECTION_PATTERNS]


@dataclass
class GuardResult:
    is_safe: bool
    risk_score: float
    detected_patterns: list[str] = field(default_factory=list)
    sanitized_input: str = ""


class PromptGuard:
    def __init__(self, threshold: float = 0.3):
        self.threshold = threshold

    def check(self, user_input: str) -> GuardResult:
        detected = []
        sanitized = user_input

        for i, pattern in enumerate(COMPILED_PATTERNS):
            if pattern.search(user_input):
                detected.append(INJECTION_PATTERNS[i])
                sanitized = pattern.sub("[BLOCKED]", sanitized)

        risk_score = len(detected) / len(INJECTION_PATTERNS)

        if detected:
            logger.warning(
                "prompt_injection_detected",
                risk_score=risk_score,
                patterns_count=len(detected),
                input_preview=user_input[:100],
            )

        return GuardResult(
            is_safe=risk_score < self.threshold,
            risk_score=risk_score,
            detected_patterns=detected,
            sanitized_input=sanitized,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PII Detector — Microsoft Presidio
# ─────────────────────────────────────────────────────────────────────────────
class PIIDetector:
    """
    Detects and anonymizes PII in text before sending to LLMs.
    Uses Microsoft Presidio with spaCy NLP backend.
    Falls back to regex-based detection if Presidio unavailable.

    Singleton — AnalyzerEngine (and the spaCy model) is loaded once per process.
    """

    _instance: "PIIDetector | None" = None

    def __new__(cls) -> "PIIDetector":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._analyzer = None
            instance._anonymizer = None
            instance._presidio_available = False
            instance._init_presidio()
            cls._instance = instance
        return cls._instance

    def _init_presidio(self) -> None:
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_anonymizer import AnonymizerEngine
            self._analyzer = AnalyzerEngine()
            self._anonymizer = AnonymizerEngine()
            self._presidio_available = True
            logger.info("presidio_initialized")
        except ImportError:
            logger.warning("presidio_not_available_using_regex_fallback")

    # Fallback regex patterns
    _REGEX_PATTERNS = [
        (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b"), "<EMAIL>"),
        (re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"), "<PHONE>"),
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "<SSN>"),
        (re.compile(r"\b4[0-9]{12}(?:[0-9]{3})?\b"), "<CREDIT_CARD>"),
    ]

    def anonymize(self, text: str, language: str = "en") -> str:
        if not text:
            return text

        if self._presidio_available:
            try:
                results = self._analyzer.analyze(text=text, language=language)
                anonymized = self._anonymizer.anonymize(text=text, analyzer_results=results)
                return anonymized.text
            except Exception as e:
                logger.warning("presidio_anonymize_failed", error=str(e))

        # Regex fallback
        result = text
        for pattern, replacement in self._REGEX_PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    def has_pii(self, text: str, language: str = "en") -> bool:
        if self._presidio_available:
            try:
                results = self._analyzer.analyze(text=text, language=language)
                return len(results) > 0
            except Exception:
                pass
        return any(p.search(text) for p, _ in self._REGEX_PATTERNS)


# ─────────────────────────────────────────────────────────────────────────────
# Azure Key Vault — Secrets Management
# ─────────────────────────────────────────────────────────────────────────────
class SecretsManager:
    """
    Centralized secrets retrieval from Azure Key Vault.
    Caches secrets in memory with configurable TTL to minimize API calls.
    """

    def __init__(self):
        self._cache: dict[str, tuple[str, float]] = {}
        self._ttl = 3600  # 1 hour
        self._client = None

    def _get_client(self):
        if self._client is None:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
            from app.core.config import settings

            credential = DefaultAzureCredential()
            self._client = SecretClient(
                vault_url=str(settings.KEY_VAULT_URL),
                credential=credential,
            )
        return self._client

    async def get_secret(self, name: str) -> str:
        import time
        cached_val, cached_time = self._cache.get(name, (None, 0))
        if cached_val and (time.time() - cached_time) < self._ttl:
            return cached_val

        client = self._get_client()
        secret = client.get_secret(name)
        value = secret.value
        self._cache[name] = (value, time.time())
        logger.info("secret_fetched", name=name)
        return value


# Module-level singletons
prompt_guard = PromptGuard()
pii_detector = PIIDetector()
secrets_manager = SecretsManager()
