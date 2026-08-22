"""
aegis_ai.guardrails.data_masker
==================================
PII Data Masking using Presidio Anonymizer — OWASP LLM06.

Replaces detected PII with typed placeholders:
  "john@example.com" → "<EMAIL_ADDRESS_1>"

Maintains a per-call masking_map for response de-anonymization.
Never logs or stores original PII values — only SHA-256 hashes.

OWASP: LLM06-Sensitive Information Disclosure
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Dict, List, Optional

import structlog

from aegis_ai.guardrails.pii_detector import PIIEntity
from aegis_ai.settings import AegisSettings
from aegis_ai.types import MaskingResult

logger = structlog.get_logger(__name__)


class DataMasker:
    """
    Masks PII in text using Presidio Anonymizer with typed placeholders.

    Usage::

        masker = DataMasker(settings)
        pii_entities = await pii_detector.analyze(prompt)
        result = masker.mask(prompt, pii_entities)
        # result.masked_text: safe to send to LLM
        # result.masking_map: {placeholder → original} for de-anonymization
    """

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        self._anonymizer: Optional[object] = None
        self._lock = asyncio.Lock()

    async def _get_anonymizer(self) -> Optional[object]:
        """Lazy-load Presidio AnonymizerEngine."""
        async with self._lock:
            if self._anonymizer is not None:
                return self._anonymizer
            self._anonymizer = await asyncio.to_thread(self._load_anonymizer)
        return self._anonymizer

    @staticmethod
    def _load_anonymizer() -> Optional[object]:
        try:
            from presidio_anonymizer import AnonymizerEngine
            engine = AnonymizerEngine()
            logger.info("presidio_anonymizer_loaded")
            return engine
        except ImportError:
            logger.warning("presidio_anonymizer_not_installed")
            return None

    def mask(self, text: str, entities: List[PIIEntity], operator: Optional[str] = None) -> MaskingResult:
        """
        Mask PII entities in text with typed placeholders.

        Args:
            text: Original text.
            entities: List of PIIEntity objects (from PIIDetector.analyze).
            operator: Masking operator ("replace", "redact", "hash"). Defaults to settings.

        Returns:
            MaskingResult with masked_text, masking_map, and entity count.
        """
        if operator is None:
            operator = "replace"

        if not entities:
            return MaskingResult(
                original_hash=self._sha256(text),
                masked_text=text,
                entity_count=0,
                masking_map={},
                entities_found=[],
            )

        masked_text = text
        masking_map: Dict[str, str] = {}
        entity_counters: Dict[str, int] = {}
        entities_found = []

        # Process in reverse order to preserve character positions
        for entity in sorted(entities, key=lambda e: e.start, reverse=True):
            etype = entity.entity_type
            entity_counters[etype] = entity_counters.get(etype, 0) + 1

            original_value = masked_text[entity.start:entity.end]

            if operator == "redact":
                placeholder = "[REDACTED]"
            elif operator == "hash":
                placeholder = f"<HASH_{self._sha256(original_value)[:12]}>"
            else:
                placeholder = f"<{etype}_{entity_counters[etype]}>"

            masking_map[placeholder] = original_value
            masked_text = masked_text[:entity.start] + placeholder + masked_text[entity.end:]

            if etype not in entities_found:
                entities_found.append(etype)

        logger.info(
            "pii_masked",
            entity_count=len(entities),
            entity_types=entities_found,
            # Never log original values, only hash
            original_hash=self._sha256(text),
        )

        return MaskingResult(
            original_hash=self._sha256(text),
            masked_text=masked_text,
            entity_count=len(entities),
            masking_map=masking_map,
            entities_found=entities_found,
        )

    def unmask(self, text: str, masking_map: Dict[str, str]) -> str:
        """
        Reverse masking: replace placeholders with original values.

        Used for response de-anonymization if needed.
        Never called on prompts — only on safe, validated responses.

        Args:
            text: Masked text containing placeholders.
            masking_map: Map of placeholder → original value.

        Returns:
            Text with placeholders replaced by originals.
        """
        result = text
        for placeholder, original in masking_map.items():
            result = result.replace(placeholder, original)
        return result

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
