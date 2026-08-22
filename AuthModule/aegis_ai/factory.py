"""
aegis_ai.factory
=================
Factory pattern for constructing SecurityPipeline and its dependencies.

Design Patterns: Factory Method, Abstract Factory
  - ``PipelineFactory``       : Creates fully-wired SecurityPipeline for an env
  - ``AuthProviderFactory``   : Creates the correct AuthProvider strategy
  - ``SecretRepositoryFactory``: Creates the correct SecretRepository strategy
  - ``AuditLoggerFactory``    : Creates audit logger(s) appropriate for env

This is the single authoritative place where concrete implementations are
wired together. Callers (app servers, tests) depend only on the abstractions.

SOLID:
  - DIP : Callers receive abstract types; concretes are resolved here only.
  - OCP : Add new environments by adding a new factory branch, not editing callers.

Usage::

    # Production (reads AEGIS_ENV from environment)
    pipeline = PipelineFactory.create()

    # Explicit environment (useful in tests and multi-tenant apps)
    pipeline = PipelineFactory.create("development")
"""

from __future__ import annotations

from typing import Optional

import structlog

from aegis_ai.settings import AegisSettings, Environment, get_settings

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SecretRepository Factory
# ─────────────────────────────────────────────────────────────────────────────


class SecretRepositoryFactory:
    """
    Selects the correct SecretRepository strategy based on settings.

    Strategy mapping:
      - ``gcp``  → GCPSecretRepository (production / staging)
      - ``env``  → EnvSecretRepository (development / CI)
    """

    @staticmethod
    def create(settings: Optional[AegisSettings] = None):
        """
        Create a SecretRepository appropriate for the active environment.

        Args:
            settings: SDK settings. Defaults to ``get_settings()``.

        Returns:
            A concrete ``SecretRepository`` implementation.
        """
        s = settings or get_settings()
        backend = getattr(s, "secret_backend", "gcp")

        if backend == "gcp":
            from aegis_ai.secrets.gcp_repository import GCPSecretRepository
            log.info("secret_repository_created", backend="gcp", project=s.gcp.project_id)
            return GCPSecretRepository(
                project_id=s.gcp.project_id,
                prefix=s.gcp.secret_manager_prefix,
                cache_ttl_seconds=s.encryption.key_cache_ttl_seconds,
            )

        # Development / CI
        from aegis_ai.secrets.env_repository import EnvSecretRepository
        log.info("secret_repository_created", backend="env")
        return EnvSecretRepository()


# ─────────────────────────────────────────────────────────────────────────────
# AuthProvider Factory
# ─────────────────────────────────────────────────────────────────────────────


class AuthProviderFactory:
    """
    Creates the correct AuthProvider strategy based on settings.

    Supported providers:
      - ``jwt``     → JWTHandler (default)
      - ``sso``     → SSOProvider (OIDC / OAuth2)
      - ``api_key`` → APIKeyManager
    """

    @staticmethod
    def create(settings: Optional[AegisSettings] = None, provider: str = "jwt"):
        """
        Create an AuthProvider for the given provider type.

        Args:
            settings: SDK settings. Defaults to ``get_settings()``.
            provider: One of 'jwt', 'sso', 'api_key'.

        Returns:
            A concrete ``AuthProvider`` implementation.
        """
        s = settings or get_settings()

        if provider == "jwt":
            from aegis_ai.auth.jwt_handler import JWTHandler
            log.info("auth_provider_created", provider="jwt")
            return JWTHandler(s)

        if provider == "sso":
            from aegis_ai.auth.sso_provider import SSOProvider
            log.info("auth_provider_created", provider="sso")
            return SSOProvider(s)

        if provider == "api_key":
            from aegis_ai.auth.api_key_manager import APIKeyManager
            log.info("auth_provider_created", provider="api_key")
            return APIKeyManager(s)

        raise ValueError(
            f"Unknown auth provider '{provider}'. "
            "Choose from: 'jwt', 'sso', 'api_key'."
        )


# ─────────────────────────────────────────────────────────────────────────────
# AuditLogger Factory
# ─────────────────────────────────────────────────────────────────────────────


class AuditLoggerFactory:
    """
    Creates an AuditLogger appropriate for the active environment.

    Environment → Audit Sink mapping:
      - development  → stdout-only (structured JSON)
      - staging      → GCP Cloud Logging + stdout
      - production   → GCP Cloud Logging (+ optional Splunk via composite)
    """

    @staticmethod
    def create(settings: Optional[AegisSettings] = None):
        """
        Create an AuditLogger for the active environment.

        Args:
            settings: SDK settings. Defaults to ``get_settings()``.

        Returns:
            A concrete ``AuditLogger`` (may be a ``CompositeAuditLogger``).
        """
        s = settings or get_settings()

        from aegis_ai.audit.audit_logger import AuditLogger

        if s.is_development():
            # Development: GCP logging disabled, stdout only
            dev_settings = s.model_copy(
                update={"audit": s.audit.model_copy(update={
                    "use_gcp_logging": False,
                    "use_structured_stdout": True,
                })}
            )
            log.info("audit_logger_created", mode="stdout_only", env="development")
            return AuditLogger(dev_settings)

        if s.is_staging():
            # Staging: GCP + stdout composite
            from aegis_ai.audit.composite_audit_logger import CompositeAuditLogger
            gcp_logger = AuditLogger(s)
            log.info("audit_logger_created", mode="gcp+stdout", env="staging")
            return gcp_logger  # AuditLogger already supports both via settings

        # Production: GCP primary; optionally add Splunk
        import os
        splunk_url = os.getenv("SPLUNK_HEC_URL", "")
        if splunk_url:
            from aegis_ai.audit.composite_audit_logger import CompositeAuditLogger
            from aegis_ai.audit.splunk_audit_logger import SplunkAuditLogger
            sinks = [AuditLogger(s), SplunkAuditLogger(s, hec_url=splunk_url)]
            log.info("audit_logger_created", mode="gcp+splunk", env="production")
            return CompositeAuditLogger(sinks, s)

        log.info("audit_logger_created", mode="gcp_only", env="production")
        return AuditLogger(s)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Factory  (main entry point)
# ─────────────────────────────────────────────────────────────────────────────


class PipelineFactory:
    """
    Creates a fully-wired ``SecurityPipeline`` for the given environment.

    This is the recommended entry point for application code.

    Examples::

        # Auto-detects AEGIS_ENV
        pipeline = PipelineFactory.create()

        # Explicit override
        pipeline = PipelineFactory.create("development")

        # Custom settings (e.g. in tests)
        settings = AegisSettings.for_environment("development")
        pipeline = PipelineFactory.create(settings=settings)
    """

    @staticmethod
    def create(
        env: Optional[str] = None,
        *,
        settings: Optional[AegisSettings] = None,
        auth_provider_type: str = "jwt",
    ):
        """
        Build a SecurityPipeline wired with environment-appropriate components.

        Args:
            env:               Override environment ('development', 'staging',
                               'production'). Reads ``AEGIS_ENV`` if None.
            settings:          Pre-built settings instance. Takes precedence
                               over ``env``.
            auth_provider_type: Auth strategy ('jwt', 'sso', 'api_key').

        Returns:
            A ready-to-use ``SecurityPipeline`` instance.
        """
        # Resolve settings
        if settings is None:
            if env is not None:
                settings = AegisSettings.for_environment(env)
            else:
                settings = get_settings()

        log.info(
            "pipeline_factory_create",
            environment=settings.environment.value,
            auth_provider=auth_provider_type,
        )

        # Wire dependencies using factories
        auth_provider = AuthProviderFactory.create(settings, auth_provider_type)
        audit_logger = AuditLoggerFactory.create(settings)

        # Build config and pipeline
        from aegis_ai.pipeline import PipelineConfig, SecurityPipeline

        config = PipelineConfig(
            settings=settings,
            auth_provider=auth_provider,
            audit_logger=audit_logger,
        )

        pipeline = SecurityPipeline(config)
        log.info(
            "pipeline_created_by_factory",
            environment=settings.environment.value,
        )
        return pipeline
