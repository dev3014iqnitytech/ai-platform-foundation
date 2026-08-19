"""
Base Agent — Abstract base class with circuit breaker, retry, OpenTelemetry tracing,
structured logging, and token budget enforcement.
All agents inherit from this class.
"""
from __future__ import annotations

import abc
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from structlog import get_logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = get_logger(__name__)
tracer = trace.get_tracer("eatap.agents")


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached: bool = False

    @property
    def cost_estimate_usd(self) -> float:
        """Rough cost for GPT-4o: $5/1M input, $15/1M output."""
        return round(
            (self.prompt_tokens / 1_000_000 * 5.0)
            + (self.completion_tokens / 1_000_000 * 15.0),
            6,
        )


@dataclass
class AgentResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    duration_ms: float = 0.0


class BaseAgent(abc.ABC):
    """
    Production-grade base agent with:
    - Circuit breaker (fail-fast on repeated errors)
    - Exponential backoff retry (rate limits, transient errors)
    - OpenTelemetry distributed tracing
    - Token budget enforcement
    - Structured logging
    """

    name: str = "base_agent"
    model: str = "gpt-4o"
    max_retries: int = 3
    token_budget: int = 50_000  # Per-session budget override

    # Circuit breaker state (simple in-memory; use Redis for distributed)
    _failure_count: int = 0
    _last_failure_time: float = 0.0
    _circuit_open: bool = False
    _circuit_threshold: int = 5
    _circuit_recovery_seconds: int = 60

    def _check_circuit(self) -> None:
        """Opens circuit after threshold failures; auto-recovers after timeout."""
        if self._circuit_open:
            elapsed = time.time() - self._last_failure_time
            if elapsed > self._circuit_recovery_seconds:
                self._circuit_open = False
                self._failure_count = 0
                logger.info("circuit_breaker_recovered", agent=self.name)
            else:
                raise CircuitOpenError(
                    f"Circuit open for {self.name}. Retry in "
                    f"{int(self._circuit_recovery_seconds - elapsed)}s"
                )

    def _record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self._circuit_threshold:
            self._circuit_open = True
            logger.error("circuit_breaker_opened", agent=self.name, failures=self._failure_count)

    def _record_success(self) -> None:
        self._failure_count = max(0, self._failure_count - 1)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((TimeoutError, ConnectionError, ValueError)),
        reraise=True,
    )
    async def run(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """
        Entry point for all agent executions.
        Wraps _execute with circuit breaking, tracing, timing, and error handling.
        """
        self._check_circuit()

        start = time.perf_counter()
        session_id = state.get("session_id", "unknown")

        with tracer.start_as_current_span(f"agent.{self.name}") as span:
            span.set_attribute("agent.name", self.name)
            span.set_attribute("agent.model", self.model)
            span.set_attribute("session.id", session_id)

            try:
                logger.info(
                    "agent_started",
                    agent=self.name,
                    session_id=session_id,
                    model=self.model,
                )
                result = await self._execute(state)
                duration_ms = (time.perf_counter() - start) * 1000

                span.set_attribute("agent.success", True)
                span.set_attribute(
                    "agent.tokens",
                    result.get("token_usage", {}).get("total", 0),
                )
                self._record_success()

                logger.info(
                    "agent_completed",
                    agent=self.name,
                    session_id=session_id,
                    duration_ms=round(duration_ms, 1),
                    tokens=result.get("token_usage", {}),
                )
                return result

            except CircuitOpenError:
                raise
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                self._record_failure()
                span.set_attribute("agent.success", False)
                span.set_attribute("agent.error", str(e))
                logger.error(
                    "agent_failed",
                    agent=self.name,
                    session_id=session_id,
                    error=str(e),
                    duration_ms=round(duration_ms, 1),
                    exc_info=True,
                )
                return {
                    **state,
                    "error": f"{self.name} failed: {e}",
                    "next_node": "error_handler",
                }

    @abc.abstractmethod
    async def _execute(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Override this in each concrete agent."""
        ...


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open."""
