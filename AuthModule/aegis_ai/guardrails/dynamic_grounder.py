"""
aegis_ai.guardrails.dynamic_grounder
=========================================
Dynamic Grounding — OWASP LLM09 (Overreliance).

Validates that the prompt's intent aligns with provided grounding context
using cosine similarity between TF-IDF vectors (no external API required).

If the prompt asks about topics NOT covered by grounding docs, the agent
may hallucinate — this guard blocks or warns on low similarity scores.

OWASP: LLM09-Overreliance, LLM03-Training Data Poisoning
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from typing import Dict, List, Optional, Set

import structlog

from aegis_ai.guardrails.base import GuardRail, GuardRailContext
from aegis_ai.settings import AegisSettings
from aegis_ai.types import GuardRailAction, GuardRailResult

logger = structlog.get_logger(__name__)

_STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "this", "that", "these",
    "those", "i", "you", "he", "she", "we", "they", "it", "me", "him",
    "her", "us", "them", "my", "your", "his", "its", "our", "their",
}


def _tokenize(text: str) -> List[str]:
    """Simple whitespace + punctuation tokenizer."""
    tokens = re.findall(r"\b[a-zA-Z]{2,}\b", text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


def _tf(tokens: List[str]) -> Dict[str, float]:
    """Compute term frequency."""
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {term: count / total for term, count in counts.items()}


def _cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
    """Compute cosine similarity between two TF vectors."""
    if not vec_a or not vec_b:
        return 0.0
    common = set(vec_a) & set(vec_b)
    if not common:
        return 0.0
    dot = sum(vec_a[t] * vec_b[t] for t in common)
    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


class DynamicGrounder(GuardRail):
    """
    RAG-based groundedness guardrail.

    Computes TF cosine similarity between the user prompt and the
    provided grounding documents. If similarity is below the configured
    threshold, the prompt is flagged as potentially hallucinatory.

    No external API required — pure Python computation.
    Context docs are provided per-call via GuardRailContext.metadata['context_docs'].
    """

    name = "DynamicGrounder"
    description = "Validates prompt alignment with grounding documents to prevent hallucination."
    owasp_ref = "LLM09"
    can_auto_remediate = False

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        self._min_similarity = settings.guardrails.grounding_min_similarity
        self._enabled = settings.guardrails.enable_dynamic_grounding

    def add_grounding_prefix(self, system_prompt: str, context: List[str]) -> str:
        """Prepend grounding context to system prompt."""
        context_text = "\n".join(context)
        return (
            "Answer only based on the following context:\n"
            f"{context_text}\n\n"
            f"System Prompt:\n{system_prompt}"
        )

    class GroundingResult:
        def __init__(self, is_grounded: bool, confidence: float, ungrounded_claims: List[str]):
            self.is_grounded = is_grounded
            self.confidence = confidence
            self.ungrounded_claims = ungrounded_claims

    def validate_response_grounding(self, response: str, context_docs: List[str]) -> GroundingResult:
        """Validate response groundedness against context documents synchronously."""
        similarity = self._compute_max_similarity(response, context_docs)
        is_grounded = similarity >= self._min_similarity
        confidence = similarity
        ungrounded_claims = [] if is_grounded else ["Response lacks context alignment."]
        return self.GroundingResult(is_grounded, confidence, ungrounded_claims)

    async def check(self, prompt: str, context: GuardRailContext) -> GuardRailResult:
        """
        Check groundedness of prompt against context documents.

        If no context docs are provided, this guardrail passes (no grounding to check against).
        """
        if not self._enabled:
            return GuardRailResult(
                name=self.name, passed=True, score=0.0,
                action=GuardRailAction.PASS, owasp_ref=self.owasp_ref,
            )

        context_docs: List[str] = context.metadata.get("context_docs", [])
        if not context_docs:
            # No grounding docs provided — pass (can't check without reference)
            return GuardRailResult(
                name=self.name, passed=True, score=0.0,
                action=GuardRailAction.PASS,
                details={"reason": "no_context_docs_provided"},
                owasp_ref=self.owasp_ref,
            )

        similarity = await asyncio.to_thread(
            self._compute_max_similarity, prompt, context_docs
        )

        passed = similarity >= self._min_similarity
        # score: 0 = perfectly grounded, 1 = totally ungrounded
        guard_score = round(1.0 - similarity, 4)

        details = {
            "similarity": round(similarity, 4),
            "threshold": self._min_similarity,
            "docs_checked": len(context_docs),
        }

        if not passed:
            logger.warning(
                "grounding_check_failed",
                similarity=similarity,
                threshold=self._min_similarity,
                identity=getattr(context.identity, "identity_id", "unknown"),
            )

        return GuardRailResult(
            name=self.name,
            passed=passed,
            score=guard_score,
            action=GuardRailAction.PASS if passed else GuardRailAction.WARN,
            details=details,
            owasp_ref=self.owasp_ref,
        )

    @staticmethod
    def _compute_max_similarity(prompt: str, docs: List[str]) -> float:
        """Return the highest cosine similarity between prompt and any document."""
        prompt_tokens = _tokenize(prompt)
        prompt_tf = _tf(prompt_tokens)
        if not prompt_tf:
            return 0.0

        max_sim = 0.0
        for doc in docs:
            doc_tokens = _tokenize(doc)
            doc_tf = _tf(doc_tokens)
            sim = _cosine_similarity(prompt_tf, doc_tf)
            max_sim = max(max_sim, sim)
            if max_sim >= 0.9:
                break  # Early exit — close enough

        return max_sim
