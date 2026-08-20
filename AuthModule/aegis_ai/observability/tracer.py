"""
aegis_ai.observability.tracer
================================
OpenTelemetry distributed tracing for the Security Pipeline.

Creates spans for each pipeline stage:
- pipeline (root)
  └─ auth
  └─ authz
  └─ guard_rails
  └─ llm_call
  └─ response_validation
  └─ audit
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Optional

import structlog

from aegis_ai.settings import AegisSettings

logger = structlog.get_logger(__name__)


class AegisTracer:
    """
    OpenTelemetry tracer for the Aegis AI SDK.

    Gracefully degrades to no-op context managers when OTel is unavailable.
    """

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        self._tracer: Optional[object] = None
        self._init_otel()

    def _init_otel(self) -> None:
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
                ConsoleSpanExporter,
            )

            provider = TracerProvider()
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer("aegis-ai", "1.0.0")
            logger.info("otel_tracer_initialized")
        except Exception as exc:
            logger.warning("otel_tracer_init_failed", error=str(exc))

    @contextmanager
    def trace_pipeline(self, agent_id: str) -> Generator[None, None, None]:
        """Start a root 'pipeline' span."""
        if self._tracer is None:
            yield
            return
        from opentelemetry import trace
        with self._tracer.start_as_current_span(  # type: ignore[union-attr]
            "aegis.pipeline",
            attributes={"agent_id": agent_id},
        ):
            yield

    @contextmanager
    def trace_span(self, name: str, **attributes: object) -> Generator[None, None, None]:
        """Start a named child span."""
        if self._tracer is None:
            yield
            return
        from opentelemetry import trace
        with self._tracer.start_as_current_span(  # type: ignore[union-attr]
            f"aegis.{name}",
            attributes={str(k): str(v) for k, v in attributes.items()},
        ):
            yield
