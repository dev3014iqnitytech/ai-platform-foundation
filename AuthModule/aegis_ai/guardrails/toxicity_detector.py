"""
aegis_ai.guardrails.toxicity_detector
=========================================
Toxicity Detection — OWASP LLM06.

Primary: Google Perspective API (async, 5s timeout)
Fallback: detoxify local ML model (blocking, wrapped in asyncio.to_thread)
Circuit breaker: 3 failures → 60s cooldown on Perspective API

OWASP: LLM06-Sensitive Information Disclosure / Harmful Content
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

import httpx
import structlog
from pydantic import BaseModel, Field

from aegis_ai.guardrails.base import GuardRail, GuardRailContext
from aegis_ai.settings import AegisSettings
from aegis_ai.types import GuardRailAction, GuardRailResult

logger = structlog.get_logger(__name__)

_PERSPECTIVE_URL = "https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze"

_DEFAULT_THRESHOLDS: Dict[str, float] = {
    "TOXICITY": 0.70,
    "SEVERE_TOXICITY": 0.50,
    "IDENTITY_ATTACK": 0.50,
    "INSULT": 0.65,
    "THREAT": 0.50,
    "SEXUALLY_EXPLICIT": 0.60,
    "PROFANITY": 0.80,
}


class ToxicityScore(BaseModel):
    """Scores for individual toxicity attributes."""

    overall: float = 0.0
    attributes: Dict[str, float] = Field(default_factory=dict)
    source: str = "unknown"  # "perspective" | "detoxify" | "keyword"


class ToxicityDetector(GuardRail):
    """
    Toxicity detector with Perspective API primary and detoxify fallback.

    Circuit breaker state:
    - CLOSED (normal): use Perspective API
    - OPEN (>= 3 failures in 60s): use detoxify fallback
    - HALF-OPEN: try Perspective API after cooldown
    """

    name = "ToxicityDetector"
    description = "Detects toxic, hateful, threatening, and harmful content."
    owasp_ref = "LLM06"
    can_auto_remediate = False

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        self._thresholds = dict(_DEFAULT_THRESHOLDS)
        # Override threshold from settings if configured
        tox_thresh = settings.guardrails.toxicity_threshold
        self._thresholds["TOXICITY"] = tox_thresh

        # Circuit breaker state
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._cb_cooldown = 60.0  # seconds
        self._cb_threshold = 3

        # Cached detoxify model (loaded lazily)
        self._detoxify_model: Optional[object] = None
        self._detoxify_lock = asyncio.Lock()

    # ─────────────────────────────────────────────────────────────────
    # GuardRail Interface
    # ─────────────────────────────────────────────────────────────────

    async def check(self, prompt: str, context: GuardRailContext) -> GuardRailResult:
        score = await self._evaluate(prompt)

        failed_attributes = [
            attr for attr, val in score.attributes.items()
            if val >= self._thresholds.get(attr, 0.5)
        ]
        passed = len(failed_attributes) == 0

        details = score.model_dump()
        if failed_attributes:
            details["failed_attributes"] = failed_attributes

        if not passed:
            logger.warning(
                "toxicity_detected",
                score=score.overall,
                failed=failed_attributes,
                source=score.source,
                identity=getattr(context.identity, "identity_id", "unknown"),
            )

        return GuardRailResult(
            name=self.name,
            passed=passed,
            score=score.overall,
            action=GuardRailAction.PASS if passed else GuardRailAction.BLOCK,
            details=details,
            owasp_ref=self.owasp_ref,
        )

    # ─────────────────────────────────────────────────────────────────
    # Evaluation Pipeline
    # ─────────────────────────────────────────────────────────────────

    async def _evaluate(self, text: str) -> ToxicityScore:
        """Choose primary or fallback based on circuit breaker state."""
        if self._circuit_open():
            logger.info("toxicity_circuit_breaker_open", using="detoxify")
            return await self._detoxify_check(text)

        # Try Perspective API
        api_key = await self._get_perspective_key()
        if not api_key:
            return await self._detoxify_check(text)

        try:
            score = await self._call_perspective_api(text, api_key)
            self._failure_count = 0  # Reset on success
            return score
        except Exception as exc:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            logger.warning(
                "perspective_api_failure",
                error=str(exc),
                failure_count=self._failure_count,
            )
            return await self._detoxify_check(text)

    async def _get_perspective_key(self) -> Optional[str]:
        """Retrieve Perspective API key (from settings/env)."""
        import os
        # In production this would come from GCP Secret Manager via KeyManager
        return os.environ.get("PERSPECTIVE_API_KEY", "")

    async def _call_perspective_api(self, text: str, api_key: str) -> ToxicityScore:
        """Call Google Perspective API asynchronously."""
        payload = {
            "comment": {"text": text[:20480]},  # API limit
            "languages": ["en"],
            "requestedAttributes": {attr: {} for attr in self._thresholds},
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{_PERSPECTIVE_URL}?key={api_key}",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        attributes: Dict[str, float] = {}
        max_score = 0.0
        for attr, result in data.get("attributeScores", {}).items():
            val = result["summaryScore"]["value"]
            attributes[attr] = round(val, 4)
            max_score = max(max_score, val)

        return ToxicityScore(
            overall=round(max_score, 4),
            attributes=attributes,
            source="perspective",
        )

    async def _detoxify_check(self, text: str) -> ToxicityScore:
        """Run detoxify local ML model (blocking call wrapped in thread)."""
        if not self._settings.guardrails.use_local_toxicity_fallback:
            return self._keyword_fallback(text)

        async with self._detoxify_lock:
            if self._detoxify_model is None:
                self._detoxify_model = await asyncio.to_thread(self._load_detoxify)

        model = self._detoxify_model
        if model is None:
            return self._keyword_fallback(text)

        try:
            results: Dict[str, float] = await asyncio.to_thread(
                self._run_detoxify, model, text
            )
            overall = max(results.values()) if results else 0.0
            return ToxicityScore(
                overall=round(overall, 4),
                attributes={k: round(v, 4) for k, v in results.items()},
                source="detoxify",
            )
        except Exception as exc:
            logger.error("detoxify_inference_failed", error=str(exc))
            return self._keyword_fallback(text)

    @staticmethod
    def _load_detoxify() -> Optional[object]:
        """Load detoxify model (heavy operation — runs once)."""
        try:
            from detoxify import Detoxify
            return Detoxify("original")
        except ImportError:
            logger.warning("detoxify_not_installed", hint="pip install detoxify")
            return None
        except Exception as exc:
            logger.error("detoxify_load_failed", error=str(exc))
            return None

    @staticmethod
    def _run_detoxify(model: object, text: str) -> Dict[str, float]:
        """Run inference synchronously (called via to_thread)."""
        results = model.predict(text[:512])  # type: ignore[attr-defined]
        return {k: float(v) for k, v in results.items()}

    @staticmethod
    def _keyword_fallback(text: str) -> ToxicityScore:
        """Minimal keyword fallback when all external checks fail."""
        _TOXIC_KEYWORDS = {
            "kill", "hate", "threat", "bomb", "attack", "destroy",
            "murder", "harm", "abuse", "exploit",
        }
        lower = text.lower()
        count = sum(1 for w in _TOXIC_KEYWORDS if w in lower)
        score = min(count * 0.3, 1.0)
        return ToxicityScore(
            overall=score,
            attributes={"TOXICITY": score},
            source="keyword",
        )

    def _circuit_open(self) -> bool:
        """Return True if circuit breaker is open (API is in cooldown)."""
        if self._failure_count >= self._cb_threshold:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed < self._cb_cooldown:
                return True
            # Cooldown expired — reset to half-open
            self._failure_count = 0
        return False
