"""
aegis_ai.observability.metrics_collector
==========================================
OpenTelemetry metrics for the Aegis AI SDK.

Instruments:
- aegis.auth.count: Authentication attempts (method, outcome)
- aegis.authz.count: Authorization decisions
- aegis.guardrail.count: GuardRail evaluations (name, passed)
- aegis.guardrail.score: GuardRail scores histogram
- aegis.llm.call.count: LLM invocations (provider, model)
- aegis.llm.latency: LLM call latency histogram (ms)
- aegis.pipeline.latency: End-to-end pipeline latency histogram (ms)
- aegis.rate_limit.exceeded: Rate limit breaches

OWASP: A09:2021-Security Logging and Monitoring Failures
"""

from __future__ import annotations

from typing import Optional

import structlog

from aegis_ai.settings import AegisSettings

logger = structlog.get_logger(__name__)


class MetricsCollector:
    """
    OpenTelemetry-based metrics collector.

    Gracefully degrades to no-op if opentelemetry-sdk is not available.
    """

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        self._meter: Optional[object] = None
        self._counters: dict = {}
        self._histograms: dict = {}
        self._init_otel()

    def _init_otel(self) -> None:
        """Initialise OpenTelemetry meter (best-effort)."""
        try:
            from opentelemetry import metrics
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import (
                ConsoleMetricExporter,
                PeriodicExportingMetricReader,
            )

            reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=60000)
            provider = MeterProvider(metric_readers=[reader])
            metrics.set_meter_provider(provider)
            meter = metrics.get_meter("aegis-ai", version="1.0.0")

            self._counters = {
                "auth": meter.create_counter("aegis.auth.count", description="Authentication attempts"),
                "authz": meter.create_counter("aegis.authz.count", description="Authorization decisions"),
                "guardrail": meter.create_counter("aegis.guardrail.count", description="GuardRail evaluations"),
                "llm_call": meter.create_counter("aegis.llm.call.count", description="LLM invocations"),
                "rate_limit": meter.create_counter("aegis.rate_limit.exceeded", description="Rate limit breaches"),
                "pipeline": meter.create_counter("aegis.pipeline.count", description="Pipeline executions"),
            }
            self._histograms = {
                "guardrail_score": meter.create_histogram("aegis.guardrail.score", description="GuardRail scores"),
                "llm_latency": meter.create_histogram("aegis.llm.latency_ms", description="LLM call latency (ms)"),
                "pipeline_latency": meter.create_histogram("aegis.pipeline.latency_ms", description="Pipeline latency (ms)"),
                "auth_latency": meter.create_histogram("aegis.auth.latency_ms", description="Auth latency (ms)"),
            }
            self._meter = meter
            logger.info("otel_metrics_initialized")
        except Exception as exc:
            logger.warning("otel_metrics_init_failed", error=str(exc), note="Metrics disabled")

    # ─────────────────────────────────────────────────────────────────
    # Recording Methods
    # ─────────────────────────────────────────────────────────────────

    def record_auth(self, method: str, outcome: str, latency_ms: float) -> None:
        self._increment("auth", {"method": method, "outcome": outcome})
        self._record("auth_latency", latency_ms, {"method": method})

    def record_authz(self, action: str, allowed: bool) -> None:
        self._increment("authz", {"action": action, "allowed": str(allowed)})

    def record_guard_rail(self, name: str, passed: bool, score: float) -> None:
        self._increment("guardrail", {"name": name, "passed": str(passed)})
        self._record("guardrail_score", score, {"name": name})

    def record_llm_call(
        self, provider: str, model: str, latency_ms: float, tokens: int
    ) -> None:
        self._increment("llm_call", {"provider": provider, "model": model})
        self._record("llm_latency", latency_ms, {"provider": provider})

    def record_pipeline(self, latency_ms: float, outcome: str) -> None:
        self._increment("pipeline", {"outcome": outcome})
        self._record("pipeline_latency", latency_ms, {"outcome": outcome})

    def record_rate_limit_exceeded(self, key: str) -> None:
        self._increment("rate_limit", {"key_type": key.split(":")[0]})

    # ─────────────────────────────────────────────────────────────────
    # Internal Helpers
    # ─────────────────────────────────────────────────────────────────

    def _increment(self, counter_name: str, attributes: dict) -> None:
        counter = self._counters.get(counter_name)
        if counter:
            try:
                counter.add(1, attributes)
            except Exception:
                pass

    def _record(self, histogram_name: str, value: float, attributes: dict) -> None:
        hist = self._histograms.get(histogram_name)
        if hist:
            try:
                hist.record(value, attributes)
            except Exception:
                pass
