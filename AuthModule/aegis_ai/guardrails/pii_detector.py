"""
aegis_ai.guardrails.pii_detector
===================================
PII Detection using Microsoft Presidio — OWASP LLM06.

Detects: EMAIL, PHONE_NUMBER, CREDIT_CARD, SSN, PERSON, LOCATION,
         IBAN_CODE, PASSPORT, MEDICAL_LICENSE, IP_ADDRESS, URL,
         DATE_TIME, NRP (National Registration/Passport), CRYPTO

Returns structured PIIEntity list consumed by DataMasker.

OWASP: LLM06-Sensitive Information Disclosure
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import structlog

from aegis_ai.settings import AegisSettings
from aegis_ai.guardrails.base import GuardRail, GuardRailContext

logger = structlog.get_logger(__name__)

# PII entity types to detect
_DEFAULT_ENTITIES = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "PERSON",
    "LOCATION",
    "IBAN_CODE",
    "US_PASSPORT",
    "MEDICAL_LICENSE",
    "IP_ADDRESS",
    "URL",
    "DATE_TIME",
    "CRYPTO",
    "US_BANK_NUMBER",
    "US_DRIVER_LICENSE",
]


class PIIEntity:
    """A detected PII entity with its location and type."""

    __slots__ = ("entity_type", "start", "end", "score", "text")

    def __init__(
        self,
        entity_type: str,
        start: int,
        end: int,
        score: float,
        text: str,
    ) -> None:
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score
        self.text = text

    def __repr__(self) -> str:
        return f"PIIEntity(type={self.entity_type}, score={self.score:.2f})"


class PIIDetector(GuardRail):
    """
    PII detector backed by Microsoft Presidio.

    Falls back to regex-based detection if Presidio/spaCy is not installed.
    All detection runs in asyncio.to_thread to avoid blocking the event loop.
    """

    name = "PIIDetector"
    owasp_ref = "LLM06"

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        self._analyzer: Optional[object] = None
        self._lock = asyncio.Lock()

    async def check(self, prompt: str, context: GuardRailContext) -> GuardRailResult:
        """Evaluate prompt for PII (always passes, logging details)."""
        entities = await self.analyze(prompt)
        from aegis_ai.types import GuardRailAction, GuardRailResult
        return GuardRailResult(
            name=self.name,
            passed=True,
            score=0.0,
            action=GuardRailAction.PASS,
            details={"entities_count": len(entities)},
            owasp_ref=self.owasp_ref,
        )

    async def _get_analyzer(self) -> Optional[object]:
        """Lazy-load Presidio AnalyzerEngine (heavy init, runs once)."""
        async with self._lock:
            if self._analyzer is not None:
                return self._analyzer
            self._analyzer = await asyncio.to_thread(self._load_presidio)
        return self._analyzer

    @staticmethod
    def _load_presidio() -> Optional[object]:
        """Synchronously load Presidio (called via to_thread)."""
        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            # Try large model first, fallback to small
            for model in ("en_core_web_lg", "en_core_web_sm"):
                try:
                    provider = NlpEngineProvider(
                        nlp_configuration={
                            "nlp_engine_name": "spacy",
                            "models": [{"lang_code": "en", "model_name": model}],
                        }
                    )
                    engine = AnalyzerEngine(nlp_engine=provider.create_engine())
                    logger.info("presidio_loaded", model=model)
                    return engine
                except Exception:
                    continue
            logger.warning("presidio_spacy_model_unavailable", fallback="regex")
            return None
        except ImportError:
            logger.warning("presidio_not_installed", hint="pip install presidio-analyzer spacy")
            return None

    async def analyze(self, text: str) -> List[PIIEntity]:
        """
        Detect PII entities in text.

        Combines Presidio NLP detection with regex pattern matching
        to ensure both standard PII and custom secrets are detected.

        Args:
            text: Input text to scan.

        Returns:
            List of PIIEntity objects sorted by position.
        """
        if not text.strip():
            return []

        entities: List[PIIEntity] = []
        analyzer = await self._get_analyzer()
        if analyzer is not None:
            entities.extend(await asyncio.to_thread(self._run_presidio, analyzer, text))

        regex_entities = self._regex_fallback(text)
        for r_ent in regex_entities:
            overlap = any(
                not (r_ent.end <= existing.start or r_ent.start >= existing.end)
                for existing in entities
            )
            if not overlap:
                entities.append(r_ent)

        return sorted(entities, key=lambda e: e.start)

    @staticmethod
    def _run_presidio(analyzer: object, text: str) -> List[PIIEntity]:
        """Run Presidio synchronously (called via to_thread)."""
        from presidio_analyzer import AnalyzerEngine
        results = analyzer.analyze(  # type: ignore[attr-defined]
            text=text,
            entities=_DEFAULT_ENTITIES,
            language="en",
        )
        entities = []
        for r in results:
            if r.score >= 0.5:
                entities.append(
                    PIIEntity(
                        entity_type=r.entity_type,
                        start=r.start,
                        end=r.end,
                        score=r.score,
                        text=text[r.start:r.end],
                    )
                )
        return sorted(entities, key=lambda e: e.start)

    @staticmethod
    def _regex_fallback(text: str) -> List[PIIEntity]:
        """Minimal regex-based PII detection when Presidio is unavailable."""
        import re
        entities = []
        patterns = [
            ("EMAIL_ADDRESS", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
            ("PHONE_NUMBER", r"\b(?:\+\d{1,3}[\s-])?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"),
            ("CREDIT_CARD", r"\b(?:\d{4}[\s-]?){3}\d{4}\b"),
            ("US_SSN", r"\b\d{3}-\d{2}-\d{4}\b"),
            ("IP_ADDRESS", r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
            ("MEDICAL_RECORD", r"(?i)\bPatient\s+ID:\s*[\w-]+\b|Diagnosis:\s*[\w\s]+"),
            ("API_KEY", r"(?i)\bAPI\s+Key:\s*[a-zA-Z0-9._-]+\b|sk-[a-zA-Z0-9_-]+"),
            ("PASSWORD", r"(?i)\bPassword:\s*\S+"),
            ("AWS_SECRET", r"(?i)\bAWS\s+Secret:\s*\S+"),
            ("BEARER_TOKEN", r"(?i)\bBearer\s+(?:token:?\s*)?\S+"),
        ]
        for entity_type, pattern in patterns:
            for m in re.finditer(pattern, text):
                entities.append(
                    PIIEntity(
                        entity_type=entity_type,
                        start=m.start(),
                        end=m.end(),
                        score=0.8,
                        text=m.group(),
                    )
                )
        return sorted(entities, key=lambda e: e.start)
