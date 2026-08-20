"""
aegis_ai.guardrails.base
==========================
Abstract base classes for all GuardRail implementations.

GuardRailChain orchestrates sequential evaluation, stopping on first
unrecoverable violation or collecting all results for audit.

SOLID: OCP — add guardrails by implementing GuardRail without pipeline changes.
OWASP: LLM01 (injection), LLM02 (output), LLM04 (DoS), LLM06 (PII)
"""

from __future__ import annotations

import abc
import time
from typing import List, Optional, Tuple

import structlog

from aegis_ai.exceptions import GuardRailViolationError
from aegis_ai.types import AgentID, GuardRailAction, GuardRailResult

logger = structlog.get_logger(__name__)


class GuardRailContext:
    """Context passed to every GuardRail.check() call."""

    __slots__ = ("identity", "agent_id", "session_id", "metadata")

    def __init__(
        self,
        identity: object,
        agent_id: AgentID,
        session_id: str,
        metadata: dict,
    ) -> None:
        self.identity = identity
        self.agent_id = agent_id
        self.session_id = session_id
        self.metadata = metadata


class GuardRail(abc.ABC):
    """
    Abstract base class for a single security guardrail.

    Each GuardRail checks one specific threat category and returns
    a GuardRailResult. If can_auto_remediate is True, a safe
    alternative prompt is returned via remediate().
    """

    name: str = "GuardRail"
    description: str = ""
    owasp_ref: str = ""
    can_auto_remediate: bool = False

    @abc.abstractmethod
    async def check(
        self, prompt: str, context: GuardRailContext
    ) -> GuardRailResult:
        """
        Evaluate the prompt for this guardrail's threat class.

        Args:
            prompt: The raw (or pre-processed) prompt text.
            context: Runtime context (identity, agent, session).

        Returns:
            GuardRailResult with pass/fail, score, and details.
        """
        ...

    async def remediate(
        self, prompt: str, context: GuardRailContext
    ) -> Optional[str]:
        """
        Return a sanitised version of the prompt (if can_auto_remediate is True).

        Args:
            prompt: The original prompt text.
            context: Runtime context.

        Returns:
            Sanitised prompt string, or None if remediation is not possible.
        """
        return None  # Default: no remediation


class GuardRailChain:
    """
    Orchestrates sequential evaluation of multiple GuardRails.

    Execution order matters — structural checks (PromptDefender, InjectionDetector)
    run before semantic checks (ToxicityDetector, PIIDetector) for efficiency.

    On BLOCK result:
    - If can_auto_remediate=True, remediated prompt replaces original and chain continues.
    - If can_auto_remediate=False, GuardRailViolationError is raised immediately.
    """

    def __init__(self, guardrails: List[GuardRail]) -> None:
        self._guardrails = guardrails
        logger.info(
            "guardrail_chain_built",
            chain=[g.name for g in guardrails],
        )

    async def run(
        self,
        prompt: str,
        context: GuardRailContext,
    ) -> Tuple[List[GuardRailResult], str]:
        """
        Run all guardrails on the prompt.

        Args:
            prompt: Input prompt text.
            context: Runtime context.

        Returns:
            Tuple of (list of GuardRailResults, final safe prompt).

        Raises:
            GuardRailViolationError: If a non-remediable guardrail blocks.
        """
        results: List[GuardRailResult] = []
        current_prompt = prompt

        for guardrail in self._guardrails:
            start = time.monotonic()
            try:
                result = await guardrail.check(current_prompt, context)
            except Exception as exc:
                logger.error(
                    "guardrail_check_error",
                    guardrail=guardrail.name,
                    error=str(exc),
                )
                # Fail-closed: treat exception as a block
                result = GuardRailResult(
                    name=guardrail.name,
                    passed=False,
                    score=1.0,
                    action=GuardRailAction.BLOCK,
                    details={"error": str(exc)},
                    owasp_ref=guardrail.owasp_ref,
                )

            latency_ms = (time.monotonic() - start) * 1000
            result = result.model_copy(update={"name": guardrail.name, "owasp_ref": guardrail.owasp_ref})
            results.append(result)

            logger.debug(
                "guardrail_evaluated",
                name=guardrail.name,
                passed=result.passed,
                score=result.score,
                latency_ms=round(latency_ms, 2),
            )

            if not result.passed:
                if guardrail.can_auto_remediate:
                    remediated = await guardrail.remediate(current_prompt, context)
                    if remediated is not None:
                        logger.info(
                            "guardrail_auto_remediated",
                            name=guardrail.name,
                            original_len=len(current_prompt),
                            remediated_len=len(remediated),
                        )
                        current_prompt = remediated
                        result = result.model_copy(
                            update={
                                "action": GuardRailAction.REMEDIATE,
                                "remediated_text": remediated,
                            }
                        )
                        results[-1] = result
                        continue  # Continue chain with remediated prompt
                # No remediation available — block
                raise GuardRailViolationError(
                    message=f"GuardRail '{guardrail.name}' blocked the request",
                    details={
                        "guardrail": guardrail.name,
                        "score": result.score,
                        "owasp_ref": guardrail.owasp_ref,
                        "details": result.details,
                    },
                )

        return results, current_prompt
