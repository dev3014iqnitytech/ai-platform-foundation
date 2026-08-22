"""
aegis_ai.builder
=================
Fluent Builder for constructing a customised SecurityPipeline.

Design Pattern: Builder
  Separates the construction of a complex object (SecurityPipeline) from its
  representation. Consumers assemble the pipeline step-by-step using a
  readable, validated fluent API — no need to know about PipelineConfig internals.

Usage::

    from aegis_ai.builder import PipelineBuilder

    pipeline = (
        PipelineBuilder()
        .with_environment("development")
        .with_auth("jwt")
        .with_guardrails(["injection", "toxicity", "pii"])
        .with_rate_limiter("memory")
        .with_audit_logger("stdout")
        .build()
    )

    # Or fully customised with your own implementations:
    pipeline = (
        PipelineBuilder()
        .with_settings(my_settings)
        .with_auth_provider(my_custom_jwt_handler)
        .with_custom_guard_rail(MySpecialGuardRail())
        .build()
    )

SOLID: OCP — add build steps without modifying existing pipeline code.
"""

from __future__ import annotations

from typing import Any, List, Optional, Type

import structlog

from aegis_ai.settings import AegisSettings, get_settings

log = structlog.get_logger(__name__)


class PipelineBuilder:
    """
    Fluent builder for ``SecurityPipeline``.

    All ``with_*`` methods return ``self`` for chaining.
    Call ``.build()`` to produce the final ``SecurityPipeline``.

    Validation is performed in ``.build()`` — missing required components
    raise ``ValueError`` with an actionable message.
    """

    def __init__(self) -> None:
        self._settings: Optional[AegisSettings] = None
        self._auth_provider: Optional[Any] = None
        self._auth_provider_type: str = "jwt"
        self._guard_rail_names: List[str] = [
            "injection", "toxicity", "pii", "prompt_defender", "dynamic_grounder"
        ]
        self._custom_guard_rails: List[Any] = []
        self._rate_limiter: Optional[Any] = None
        self._rate_limiter_backend: str = "memory"
        self._audit_logger: Optional[Any] = None
        self._audit_mode: str = "auto"  # auto | gcp | splunk | stdout | composite
        self._llm_gateway: Optional[Any] = None
        self._metrics: Optional[Any] = None
        self._tracer: Optional[Any] = None
        self._event_bus: Optional[Any] = None

    # ── Settings ─────────────────────────────────────────────────────────────

    def with_settings(self, settings: AegisSettings) -> "PipelineBuilder":
        """Supply a pre-constructed AegisSettings instance."""
        self._settings = settings
        return self

    def with_environment(self, env: str) -> "PipelineBuilder":
        """
        Set the deployment environment by name.

        Equivalent to ``with_settings(AegisSettings.for_environment(env))``.

        Args:
            env: 'development', 'staging', or 'production'.
        """
        self._settings = AegisSettings.for_environment(env)
        return self

    # ── Auth ─────────────────────────────────────────────────────────────────

    def with_auth(self, provider: str = "jwt") -> "PipelineBuilder":
        """
        Select the authentication strategy by name.

        Args:
            provider: 'jwt', 'sso', or 'api_key'.
        """
        self._auth_provider_type = provider
        return self

    def with_auth_provider(self, provider: Any) -> "PipelineBuilder":
        """Supply a fully-constructed AuthProvider instance."""
        self._auth_provider = provider
        return self

    # ── GuardRails ────────────────────────────────────────────────────────────

    def with_guardrails(self, names: List[str]) -> "PipelineBuilder":
        """
        Select guardrails by name.

        Available names:
          - ``injection``       : Prompt injection detector (OWASP LLM01)
          - ``toxicity``        : Toxicity detector (OWASP LLM06)
          - ``pii``             : PII detector (OWASP LLM06)
          - ``prompt_defender`` : Structural prompt defense
          - ``dynamic_grounder``: Dynamic grounding (OWASP LLM09)

        Args:
            names: Ordered list of guardrail names to enable.
        """
        self._guard_rail_names = names
        return self

    def with_custom_guard_rail(self, guardrail: Any) -> "PipelineBuilder":
        """Append a custom GuardRail instance to the chain."""
        self._custom_guard_rails.append(guardrail)
        return self

    # ── Rate Limiter ──────────────────────────────────────────────────────────

    def with_rate_limiter(self, backend: str = "memory") -> "PipelineBuilder":
        """
        Configure the rate limiter backend.

        Args:
            backend: 'memory' or 'redis'.
        """
        self._rate_limiter_backend = backend
        return self

    def with_rate_limiter_instance(self, limiter: Any) -> "PipelineBuilder":
        """Supply a fully-constructed RateLimiter instance."""
        self._rate_limiter = limiter
        return self

    # ── Audit Logger ──────────────────────────────────────────────────────────

    def with_audit_logger(self, mode: str = "auto") -> "PipelineBuilder":
        """
        Configure the audit logger mode.

        Args:
            mode: 'auto' (env-based), 'gcp', 'splunk', 'stdout', or 'composite'.
        """
        self._audit_mode = mode
        return self

    def with_audit_logger_instance(self, logger_instance: Any) -> "PipelineBuilder":
        """Supply a fully-constructed AuditLogger instance."""
        self._audit_logger = logger_instance
        return self

    # ── LLM Gateway ──────────────────────────────────────────────────────────

    def with_llm_gateway(self, gateway: Any) -> "PipelineBuilder":
        """Supply a custom LLMGateway (e.g. a mock for testing)."""
        self._llm_gateway = gateway
        return self

    # ── Observability ─────────────────────────────────────────────────────────

    def with_metrics(self, collector: Any) -> "PipelineBuilder":
        """Supply a custom MetricsCollector."""
        self._metrics = collector
        return self

    def with_tracer(self, tracer: Any) -> "PipelineBuilder":
        """Supply a custom AegisTracer."""
        self._tracer = tracer
        return self

    def with_event_bus(self, bus: Any) -> "PipelineBuilder":
        """Attach a SecurityEventBus for audit/metrics fan-out."""
        self._event_bus = bus
        return self

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self):
        """
        Validate the builder state and produce a SecurityPipeline.

        Returns:
            A fully configured ``SecurityPipeline`` instance.

        Raises:
            ValueError: If required settings are missing or invalid.
        """
        settings = self._settings or get_settings()

        log.info(
            "pipeline_builder_build",
            environment=settings.environment.value,
            auth=self._auth_provider_type,
            guardrails=self._guard_rail_names,
            rate_limiter=self._rate_limiter_backend,
        )

        # Resolve auth provider
        auth_provider = self._auth_provider
        if auth_provider is None:
            from aegis_ai.factory import AuthProviderFactory
            auth_provider = AuthProviderFactory.create(settings, self._auth_provider_type)

        # Resolve guardrail chain
        guard_rails = self._build_guard_rails(settings)

        # Resolve rate limiter
        rate_limiter = self._rate_limiter or self._build_rate_limiter(settings)

        # Resolve audit logger
        audit_logger = self._audit_logger or self._build_audit_logger(settings)

        # Build PipelineConfig with resolved components
        from aegis_ai.pipeline import PipelineConfig, SecurityPipeline
        kwargs = dict(
            settings=settings,
            auth_provider=auth_provider,
            guard_rails=guard_rails if guard_rails else None,
            rate_limiter=rate_limiter,
            audit_logger=audit_logger,
        )
        if self._llm_gateway:
            kwargs["llm_gateway"] = self._llm_gateway
        if self._metrics:
            kwargs["metrics"] = self._metrics
        if self._tracer:
            kwargs["tracer"] = self._tracer

        config = PipelineConfig(**kwargs)
        return SecurityPipeline(config)

    # ── Private Helpers ───────────────────────────────────────────────────────

    def _build_guard_rails(self, settings: AegisSettings) -> List[Any]:
        """Instantiate requested guardrails in order, then append custom ones."""
        _registry = {
            "prompt_defender": "aegis_ai.guardrails.prompt_defender.PromptDefender",
            "injection":       "aegis_ai.guardrails.injection_detector.InjectionDetector",
            "toxicity":        "aegis_ai.guardrails.toxicity_detector.ToxicityDetector",
            "pii":             "aegis_ai.guardrails.pii_detector.PIIDetector",
            "dynamic_grounder":"aegis_ai.guardrails.dynamic_grounder.DynamicGrounder",
        }

        rails: List[Any] = []
        for name in self._guard_rail_names:
            if name not in _registry:
                raise ValueError(
                    f"Unknown guardrail '{name}'. "
                    f"Available: {list(_registry.keys())}"
                )
            module_path, class_name = _registry[name].rsplit(".", 1)
            import importlib
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            rails.append(cls(settings))

        rails.extend(self._custom_guard_rails)
        return rails

    def _build_rate_limiter(self, settings: AegisSettings) -> Any:
        """Create a RateLimiter with the configured backend."""
        # Override backend in settings copy if explicitly set
        from aegis_ai.guardrails.rate_limiter import RateLimiter
        if self._rate_limiter_backend != "memory" and settings.rate_limit.backend != self._rate_limiter_backend:
            updated = settings.rate_limit.model_copy(update={"backend": self._rate_limiter_backend})
            new_settings = settings.model_copy(update={"rate_limit": updated})
            return RateLimiter(new_settings)
        return RateLimiter(settings)

    def _build_audit_logger(self, settings: AegisSettings) -> Any:
        """Create an AuditLogger based on the configured mode."""
        if self._audit_mode == "auto":
            from aegis_ai.factory import AuditLoggerFactory
            return AuditLoggerFactory.create(settings)

        from aegis_ai.audit.audit_logger import AuditLogger
        if self._audit_mode == "stdout":
            dev_settings = settings.model_copy(
                update={"audit": settings.audit.model_copy(update={
                    "use_gcp_logging": False, "use_structured_stdout": True
                })}
            )
            return AuditLogger(dev_settings)

        if self._audit_mode in ("gcp", "composite"):
            return AuditLogger(settings)

        if self._audit_mode == "splunk":
            from aegis_ai.audit.splunk_audit_logger import SplunkAuditLogger
            return SplunkAuditLogger(settings)

        raise ValueError(
            f"Unknown audit mode '{self._audit_mode}'. "
            "Choose from: 'auto', 'gcp', 'splunk', 'stdout', 'composite'."
        )
