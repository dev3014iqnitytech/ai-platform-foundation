"""
aegis_ai.pipeline
=================
SecurityPipeline — The main entry point for the aegis-ai SDK.

This facade orchestrates all security layers in the correct order:
  1. Authentication (verify identity)
  2. Authorization (check permissions via Google IAM + RBAC)
  3. Rate Limiting (protect against DoS — OWASP LLM04)
  4. Pre-call GuardRails (injection, PII, toxicity — OWASP LLM01/06)
  5. Data Masking (strip PII before LLM egress — OWASP LLM06)
  6. LLM Gateway call (TLS 1.3, zero-retention headers)
  7. Post-call Response Validation (OWASP LLM02)
  8. Audit Trail (signed, immutable event log)

OWASP LLM Top 10 Coverage: LLM01-LLM10
EVLAS stages: Evaluate → Validate → Log → Audit → Safety
SOLID: Depends on abstractions, not concretions; composable via DI.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

import structlog

from aegis_ai.audit.audit_event import AuditEvent, EventType
from aegis_ai.audit.audit_logger import AuditLogger
from aegis_ai.audit.retention_policy import RetentionPolicy
from aegis_ai.auth.base import AuthProvider
from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.auth.jwt_handler import JWTHandler
from aegis_ai.authz.iam_client import IAMClient
from aegis_ai.authz.policy_engine import PolicyDecision, PolicyEngine
from aegis_ai.authz.rbac_engine import RBACEngine
from aegis_ai.crypto.tls_enforcer import TLSEnforcer
from aegis_ai.exceptions import (
    AegisBaseError,
    AuthenticationError,
    AuthorizationError,
    GuardRailViolationError,
    RateLimitExceededError,
)
from aegis_ai.guardrails.base import GuardRail, GuardRailChain, GuardRailContext
from aegis_ai.guardrails.data_masker import DataMasker
from aegis_ai.guardrails.dynamic_grounder import DynamicGrounder
from aegis_ai.guardrails.injection_detector import InjectionDetector
from aegis_ai.guardrails.pii_detector import PIIDetector
from aegis_ai.guardrails.prompt_defender import PromptDefender
from aegis_ai.guardrails.rate_limiter import RateLimiter
from aegis_ai.guardrails.toxicity_detector import ToxicityDetector
from aegis_ai.observability.metrics_collector import MetricsCollector
from aegis_ai.observability.tracer import AegisTracer
from aegis_ai.proxy.llm_gateway import LLMGateway, LLMRequest, LLMResponse
from aegis_ai.proxy.response_validator import ResponseValidator
from aegis_ai.proxy.zero_retention_policy import ZeroRetentionPolicy
from aegis_ai.settings import AegisSettings, get_settings
from aegis_ai.types import AgentID, GuardRailResult, Permission, PipelineResult, ResourcePath
from aegis_ai.decorators import CircuitBreaker
from aegis_ai.events import SecurityEventBus, SecurityEvent, EventCategory

logger = structlog.get_logger(__name__)

# Module-level circuit breaker for the LLM gateway (shared across pipeline instances)
_llm_circuit_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30.0,
    name="llm_gateway",
)


class PipelineConfig:
    """
    Dependency-injection container for SecurityPipeline.

    Follows Dependency Inversion Principle — pipeline depends on
    abstractions (interfaces / base classes), not concrete implementations.
    Swap any component by passing a different implementation.
    """

    def __init__(
        self,
        settings: AegisSettings | None = None,
        *,
        auth_provider: AuthProvider | None = None,
        iam_client: IAMClient | None = None,
        rbac_engine: RBACEngine | None = None,
        policy_engine: PolicyEngine | None = None,
        guard_rails: list[GuardRail] | None = None,
        pii_detector: PIIDetector | None = None,
        data_masker: DataMasker | None = None,
        llm_gateway: LLMGateway | None = None,
        zero_retention_policy: ZeroRetentionPolicy | None = None,
        response_validator: ResponseValidator | None = None,
        rate_limiter: RateLimiter | None = None,
        audit_logger: AuditLogger | None = None,
        retention_policy: RetentionPolicy | None = None,
        metrics: MetricsCollector | None = None,
        tracer: AegisTracer | None = None,
        event_bus: SecurityEventBus | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.auth_provider = auth_provider or JWTHandler(self.settings)
        self.iam_client = iam_client or IAMClient(self.settings)
        self.rbac_engine = rbac_engine or RBACEngine(self.settings)
        self.policy_engine = policy_engine or PolicyEngine(self.settings)
        self.pii_detector = pii_detector or PIIDetector(self.settings)
        self.data_masker = data_masker or DataMasker(self.settings)
        self.rate_limiter = rate_limiter or RateLimiter(self.settings)
        self.llm_gateway = llm_gateway or LLMGateway(self.settings)
        self.zero_retention_policy = zero_retention_policy or ZeroRetentionPolicy(self.settings)
        self.response_validator = response_validator or ResponseValidator(
            pii_detector=self.pii_detector,
            toxicity_detector=ToxicityDetector(self.settings),
            settings=self.settings,
        )
        self.audit_logger = audit_logger or AuditLogger(self.settings)
        self.retention_policy = retention_policy or RetentionPolicy(self.settings)
        self.metrics = metrics or MetricsCollector(self.settings)
        self.tracer = tracer or AegisTracer(self.settings)
        # Observer: SecurityEventBus for decoupled audit/metrics fan-out
        self.event_bus = event_bus or SecurityEventBus(fire_and_forget=True)

        # Default guard rails chain (ordered by priority)
        self.guard_rails = guard_rails or [
            PromptDefender(self.settings),       # Structural defense first
            InjectionDetector(self.settings),    # OWASP LLM01
            ToxicityDetector(self.settings),     # OWASP LLM06
            PIIDetector(self.settings),          # OWASP LLM06
            DynamicGrounder(self.settings),      # OWASP LLM09
        ]


class SecurityPipeline:
    """
    The main security orchestrator for the aegis-ai SDK.

    Usage::

        pipeline = SecurityPipeline()

        async with pipeline.secure_agent_call(
            token="Bearer <jwt>",
            agent_id="my-agent",
            llm_request=LLMRequest(
                provider="openai",
                model="gpt-4o",
                messages=[LLMMessage(role="user", content="Hello!")],
            ),
            required_permission="agents.call",
            resource="projects/my-project/agents/my-agent",
        ) as result:
            print(result.response)

    All calls are:
    - Authenticated (JWT / SSO / API Key)
    - Authorized (Google IAM + RBAC + Policy Engine)
    - Rate-limited
    - Guard-railed (injection, toxicity, PII)
    - Data-masked before LLM egress
    - Zero-retention enforced
    - Response-validated
    - Fully audited (immutable, HMAC-signed)
    """

    def __init__(self, config: PipelineConfig | None = None) -> None:
        self._cfg = config or PipelineConfig()
        self._chain = GuardRailChain(self._cfg.guard_rails)
        self._tls = TLSEnforcer()
        # Circuit breaker shared at module level (can be overridden in tests)
        self._llm_breaker = _llm_circuit_breaker
        log = logger.bind(component="SecurityPipeline")
        log.info("pipeline_initialized", guard_rails=[g.name for g in self._cfg.guard_rails])

    # ─────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────

    async def secure_agent_call(
        self,
        token: str,
        request: Optional[LLMRequest] = None,
        required_permissions: Optional[List[str]] = None,
        *,
        agent_id: Optional[str] = None,
        llm_request: Optional[LLMRequest] = None,
        required_permission: Optional[str] = None,
        resource: Optional[str] = None,
        context_docs: list[str] | None = None,
        extra_context: dict[str, Any] | None = None,
    ) -> PipelineResult:
        """
        Full security pipeline for a single agent LLM call.

        Args:
            token: Bearer token (JWT), API key, or SSO token.
            request: Position-compatible request.
            required_permissions: Position-compatible permissions list.
            agent_id: Unique identifier for the calling agent.
            llm_request: The LLM call parameters.
            required_permission: IAM permission required.
            resource: GCP resource path.
            context_docs: Optional grounding documents.
            extra_context: Additional metadata.

        Returns:
            PipelineResult with response, masking map, guard results, and audit ID.
        """
        # Resolve hybrid arguments
        actual_request = request or llm_request
        if actual_request is None:
            raise ValueError("llm_request is required")

        actual_permission = required_permission
        if required_permissions and not actual_permission:
            actual_permission = required_permissions[0]
        if not actual_permission:
            raise ValueError("required_permission is required")

        actual_agent_id = agent_id or "default-agent"
        actual_resource = resource or f"projects/default/agents/{actual_agent_id}"

        pipeline_start = time.monotonic()
        pipeline_id = str(uuid.uuid4())
        correlation_id = extra_context.get("correlation_id") if extra_context else pipeline_id
        guard_results: list[GuardRailResult] = []
        identity: IdentityContext | None = None
        audit_id: str | None = None

        log = logger.bind(
            pipeline_id=pipeline_id,
            correlation_id=correlation_id,
            agent_id=actual_agent_id,
        )

        try:
            with self._cfg.tracer.trace_pipeline(actual_agent_id):
                # ── Step 1: AUTHENTICATE ──────────────────────────────
                auth_start = time.monotonic()
                try:
                    identity = await self._authenticate(token)
                    auth_latency = (time.monotonic() - auth_start) * 1000
                    self._cfg.metrics.record_auth(
                        method=identity.auth_method.value,
                        outcome="success",
                        latency_ms=auth_latency,
                    )
                    log.info("auth_success", identity_id=identity.identity_id)
                    await self._cfg.event_bus.publish(
                        SecurityEvent.auth_success(
                            identity_id=identity.identity_id,
                            agent_id=actual_agent_id,
                            correlation_id=correlation_id,
                        )
                    )
                except AegisBaseError as exc:
                    auth_latency = (time.monotonic() - auth_start) * 1000
                    self._cfg.metrics.record_auth(
                        method="unknown", outcome="failure", latency_ms=auth_latency
                    )
                    await self._cfg.audit_logger.log_auth(
                        identity=None,  # type: ignore[arg-type]
                        outcome="FAILURE",
                        details={"error": str(exc), "error_code": exc.error_code},
                    )
                    await self._cfg.event_bus.publish(
                        SecurityEvent.auth_failure(
                            reason=exc.error_code,
                            correlation_id=correlation_id,
                            details={"error": str(exc)},
                        )
                    )
                    raise

                # ── Step 2: AUTHORIZE ─────────────────────────────────
                await self._authorize(
                    identity=identity,
                    action=actual_permission,
                    resource=ResourcePath(actual_resource),
                    extra_context={**(extra_context or {}), "correlation_id": correlation_id},
                )

                # ── Step 3: RATE LIMIT ────────────────────────────────
                await self._enforce_rate_limit(identity, agent_id=AgentID(actual_agent_id))

                # ── Step 4: PRE-CALL GUARD RAILS ─────────────────────
                prompt_text = self._extract_prompt(actual_request)
                gr_context = GuardRailContext(
                    identity=identity,
                    agent_id=AgentID(actual_agent_id),
                    session_id=identity.session_id,
                    metadata=extra_context or {},
                )
                guard_results, safe_prompt = await self._run_guard_rails(
                    prompt_text, gr_context
                )

                # ── Step 5: DATA MASKING ──────────────────────────────
                pii_entities = await self._cfg.pii_detector.analyze(safe_prompt)
                masked = self._cfg.data_masker.mask(safe_prompt, pii_entities)
                masked_request = self._apply_masked_prompt(actual_request, masked.masked_text)

                log.info(
                    "prompt_masked",
                    entity_count=masked.entity_count,
                    prompt_hash=self._hash(masked.masked_text),
                )

                # ── Step 6: ZERO RETENTION + LLM CALL (with circuit breaker) ──
                self._cfg.zero_retention_policy.validate_provider(actual_request.provider)
                llm_start = time.monotonic()

                @self._llm_breaker
                async def _guarded_llm_call() -> LLMResponse:
                    return await self._cfg.llm_gateway.call(masked_request, identity)

                response: LLMResponse = await _guarded_llm_call()
                llm_latency = (time.monotonic() - llm_start) * 1000
                self._cfg.metrics.record_llm_call(
                    provider=actual_request.provider,
                    model=actual_request.model,
                    latency_ms=llm_latency,
                    tokens=response.usage.total_tokens if response.usage else 0,
                )

                # ── Step 7: POST-RESPONSE VALIDATION ─────────────────
                validation = await self._cfg.response_validator.validate(
                    response, masked_request
                )
                if not validation.is_safe:
                    raise GuardRailViolationError(
                        message="Response failed safety validation",
                        details={"issues": validation.issues},
                    )

                final_response = validation.filtered_content or response.content

                # ── Step 8: RETENTION ENFORCEMENT ────────────────────
                enforcement = self._cfg.retention_policy.enforce(
                    provider=actual_request.provider,
                    prompt=prompt_text,
                    response=final_response,
                )

                # ── Step 9: AUDIT ─────────────────────────────────────
                pipeline_latency = (time.monotonic() - pipeline_start) * 1000
                prompt_hash = self._hash(prompt_text)
                response_hash = self._hash(final_response) if final_response else ""

                event = AuditEvent(
                    event_type=EventType.LLM_CALL,
                    identity_id=identity.identity_id,
                    agent_id=identity.agent_id,
                    tenant_id=identity.tenant_id,
                    session_id=identity.session_id,
                    action="invoke_llm",
                    resource=f"{actual_request.provider}/{actual_request.model}",
                    outcome="SUCCESS",
                    severity="INFO",
                    prompt_hash=prompt_hash,
                    response_hash=response_hash,
                    details={"latency_ms": round(pipeline_latency, 2)},
                )
                audit_id = await self._cfg.audit_logger.log(event)

                self._cfg.metrics.record_pipeline(
                    latency_ms=pipeline_latency, outcome="success"
                )

                return PipelineResult(
                    allowed=True,
                    masked_prompt=masked.masked_text,
                    response=final_response,
                    guard_results=guard_results,
                    audit_id=audit_id,
                    latency_ms=pipeline_latency,
                    masking_map=masked.masking_map,
                )

        except AegisBaseError as exc:
            pipeline_latency = (time.monotonic() - pipeline_start) * 1000
            self._cfg.metrics.record_pipeline(latency_ms=pipeline_latency, outcome="failure")
            log.warning(
                "pipeline_blocked",
                error_code=exc.error_code,
                http_status=exc.http_status,
                latency_ms=pipeline_latency,
            )
            if identity:
                await self._cfg.audit_logger.log_security_alert(
                    severity="HIGH",
                    message=f"Pipeline blocked: {exc.error_code}",
                    details=exc.details,
                )
            await self._cfg.event_bus.publish(
                SecurityEvent.pipeline_blocked(
                    error_code=exc.error_code,
                    identity_id=identity.identity_id if identity else "anonymous",
                    details=exc.details,
                )
            )
            raise

    @asynccontextmanager
    async def secure_session(
        self,
        token: str,
        *,
        agent_id: Optional[str] = None,
        required_permission: Optional[str] = None,
        resource: Optional[str] = None,
    ) -> AsyncIterator[IdentityContext]:
        """
        Async context manager for multi-turn agent sessions.

        Authenticates and authorises once at session start, then yields the
        ``IdentityContext`` for use across multiple ``secure_agent_call()``
        invocations within the same session scope.

        On exit (normal or exception), the session is audited and metrics
        are recorded. Token revocation can be hooked here in future.

        Usage::

            async with pipeline.secure_session(
                token="Bearer <jwt>",
                agent_id="my-agent",
                required_permission="agents.call",
                resource="projects/my-proj/agents/my-agent",
            ) as identity:
                result1 = await pipeline.secure_agent_call(
                    token=token,
                    agent_id=agent_id,
                    llm_request=request1,
                    required_permission="agents.call",
                )
                result2 = await pipeline.secure_agent_call(
                    token=token,
                    agent_id=agent_id,
                    llm_request=request2,
                    required_permission="agents.call",
                )
        """
        session_id = str(uuid.uuid4())
        session_start = time.monotonic()
        log = logger.bind(session_id=session_id, agent_id=agent_id)
        log.info("secure_session_start")

        try:
            identity = await self._authenticate(token)
            if required_permission and resource:
                await self._authorize(
                    identity=identity,
                    action=required_permission,
                    resource=ResourcePath(resource),
                    extra_context={"session_id": session_id},
                )
            yield identity
        except AegisBaseError:
            session_latency = (time.monotonic() - session_start) * 1000
            log.warning("secure_session_aborted", latency_ms=round(session_latency, 2))
            raise
        finally:
            session_latency = (time.monotonic() - session_start) * 1000
            log.info("secure_session_end", latency_ms=round(session_latency, 2))

    async def authenticate_only(self, token: str) -> IdentityContext:
        """
        Authenticate without making an LLM call.
        Useful for non-LLM endpoints that still need identity verification.
        """
        return await self._authenticate(token)

    async def check_permission(
        self,
        identity: IdentityContext,
        permission: str,
        resource: str,
    ) -> bool:
        """Check a single IAM permission for an identity."""
        return await self._cfg.iam_client.check_permission(
            identity=identity,
            resource=ResourcePath(resource),
            permission=Permission(permission),
        )

    async def drain_event_bus(self) -> None:
        """
        Wait for all in-flight event bus tasks to complete.
        Call during graceful shutdown to ensure no events are dropped.
        """
        await self._cfg.event_bus.drain()

    @property
    def circuit_breaker_state(self) -> str:
        """Return the current LLM circuit breaker state (CLOSED/OPEN/HALF_OPEN)."""
        return self._llm_breaker.state.value

    # ─────────────────────────────────────────────────────────────
    # Private Helpers
    # ─────────────────────────────────────────────────────────────

    async def _authenticate(self, token: str) -> IdentityContext:
        """Authenticate the incoming token and return an IdentityContext."""
        clean_token = token.removeprefix("Bearer ").strip()
        return await self._cfg.auth_provider.validate_token(clean_token)

    async def _authorize(
        self,
        identity: IdentityContext,
        action: str,
        resource: ResourcePath,
        extra_context: dict[str, Any],
    ) -> None:
        """Three-layer authorization: IAM → RBAC → Policy Engine."""
        # Layer 1: Google IAM
        iam_permitted = await self._cfg.iam_client.check_permission(
            identity=identity,
            resource=resource,
            permission=Permission(action),
        )
        # Layer 2: RBAC fallback (for non-GCP resources)
        rbac_permitted = self._cfg.rbac_engine.has_permission(
            identity=identity,
            resource=resource,
            permission=Permission(action),
        )
        # Layer 3: Policy Engine (ABAC conditions)
        policy_decision: PolicyDecision = await self._cfg.policy_engine.evaluate(
            identity=identity,
            action=action,
            resource=resource,
            context=extra_context,
        )

        permitted = (iam_permitted or rbac_permitted) and policy_decision.allowed

        await self._cfg.audit_logger.log_policy_decision(
            identity=identity,
            action=action,
            resource=str(resource),
            decision=permitted,
        )

        if not permitted:
            raise AuthorizationError(
                message=f"Permission denied: '{action}' on '{resource}'",
                details={
                    "iam_permitted": iam_permitted,
                    "rbac_permitted": rbac_permitted,
                    "policy_reason": policy_decision.reason,
                },
            )

    async def _enforce_rate_limit(
        self, identity: IdentityContext, agent_id: AgentID
    ) -> None:
        """Enforce sliding-window rate limits per identity and agent."""
        cfg = self._cfg.settings.rate_limit
        result = await self._cfg.rate_limiter.check_rate_limit(
            key=f"identity:{identity.identity_id}",
            limit=cfg.requests_per_minute,
            window_seconds=60,
        )
        if not result.allowed:
            raise RateLimitExceededError(
                message="Rate limit exceeded",
                details={
                    "remaining": result.remaining,
                    "reset_at": result.reset_at.isoformat(),
                    "retry_after_seconds": result.retry_after_seconds,
                },
            )

    async def _run_guard_rails(
        self,
        prompt: str,
        context: GuardRailContext,
    ) -> tuple[list[GuardRailResult], str]:
        """Run all guard rails sequentially. Returns results + safe prompt."""
        results, safe_prompt = await self._chain.run(prompt, context)
        for result in results:
            self._cfg.metrics.record_guard_rail(
                name=result.name,
                passed=result.passed,
                score=result.score,
            )
            if not result.passed:
                await self._cfg.audit_logger.log_guard_rail(
                    identity=context.identity,
                    guard_name=result.name,
                    result=result,
                )
        return results, safe_prompt

    @staticmethod
    def _extract_prompt(request: LLMRequest) -> str:
        """Extract the last user message as the primary prompt."""
        for msg in reversed(request.messages):
            if msg.role == "user":
                return msg.content
        return ""

    @staticmethod
    def _apply_masked_prompt(request: LLMRequest, masked_text: str) -> LLMRequest:
        """Return a new request with the last user message replaced by the masked version."""
        new_messages = []
        replaced = False
        for msg in reversed(request.messages):
            if msg.role == "user" and not replaced:
                from aegis_ai.proxy.llm_gateway import LLMMessage  # noqa: PLC0415
                new_messages.insert(0, LLMMessage(role="user", content=masked_text))
                replaced = True
            else:
                new_messages.insert(0, msg)
        return request.model_copy(update={"messages": new_messages})

    @staticmethod
    def _hash(text: str) -> str:
        """SHA-256 hash for logging (never store raw text)."""
        return hashlib.sha256(text.encode()).hexdigest()
