"""
aegis_ai.settings
=================
Unified configuration for the Aegis AI SDK.

All secrets are loaded from environment variables or GCP Secret Manager.
Never hardcode secrets — this file configures WHERE to find them.

Multi-Environment loading strategy
------------------------------------
The settings system resolves configuration in priority order (highest → lowest):

  1. Process environment variables  (always wins)
  2. .env                           (user overrides — gitignored)
  3. envs/.env.{AEGIS_ENV}          (environment-specific defaults)
  4. Pydantic model defaults        (fallback)

Set ``AEGIS_ENV`` to ``development``, ``staging``, or ``production`` before
importing this module. If unset, defaults to ``production`` for safety.

Example::

    AEGIS_ENV=development python -m aegis_ai.cli serve

OWASP: LLM04 (DoS protection), LLM06 (Sensitive Info Disclosure)
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ─────────────────────────────────────────────────────────────────────────────
# Environment Resolution
# ─────────────────────────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parent.parent  # project root


class Environment(str, Enum):
    """Supported deployment environments."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"

    def is_production(self) -> bool:
        return self == Environment.PRODUCTION

    def is_staging(self) -> bool:
        return self == Environment.STAGING

    def is_development(self) -> bool:
        return self in (Environment.DEVELOPMENT, Environment.TEST)

    def is_test(self) -> bool:
        return self == Environment.TEST


def _resolve_env_files() -> List[str]:
    """
    Build the ordered list of .env files to load.

    Priority (pydantic-settings loads last file first, so we list highest
    priority last — then real env vars still win via env_prefix):

      envs/.env.{AEGIS_ENV}   ← lowest priority (environment defaults)
      .env                     ← highest priority file (developer overrides)
    """
    aegis_env = os.getenv("AEGIS_ENV", Environment.PRODUCTION.value).lower()

    env_specific = _BASE_DIR / "envs" / f".env.{aegis_env}"
    user_override = _BASE_DIR / ".env"

    # Return existing files only; pydantic-settings silently ignores missing files
    files: List[str] = []
    if env_specific.exists():
        files.append(str(env_specific))
    if user_override.exists():
        files.append(str(user_override))
    return files


# ─────────────────────────────────────────────────────────────────────────────
# Sub-config Models
# ─────────────────────────────────────────────────────────────────────────────


class GCPSettings(BaseModel):
    """Google Cloud Platform configuration."""

    project_id: str = Field("", description="GCP project ID")
    service_account_path: Optional[str] = Field(
        None, description="Path to service account JSON (uses ADC if None)"
    )
    secret_manager_prefix: str = Field(
        "aegis-ai", description="Prefix for secrets in GCP Secret Manager"
    )
    kms_key_ring: str = Field("aegis-ai-keyring", description="GCP KMS key ring name")
    kms_location: str = Field("global", description="GCP KMS key ring location")
    kms_crypto_key: str = Field("aegis-ai-key", description="GCP KMS crypto key name")
    use_gcp: bool = Field(True, description="Enable real GCP integrations (False for local dev)")


class JWTSettings(BaseModel):
    """JWT Authentication configuration."""

    algorithm: str = Field("RS256", description="JWT signing algorithm (RS256 only in production)")
    issuer: str = Field("https://auth.aegis-ai.internal", description="Expected JWT issuer")
    audience: str = Field("aegis-ai-agents", description="Expected JWT audience")
    access_token_expire_minutes: int = Field(15, ge=1, le=60)
    refresh_token_expire_days: int = Field(7, ge=1, le=30)
    private_key_secret_name: str = Field(
        "aegis-ai-jwt-private-key", description="GCP Secret Manager secret name for private key"
    )
    public_key_secret_name: str = Field(
        "aegis-ai-jwt-public-key", description="GCP Secret Manager secret name for public key"
    )
    # Fallback for local development ONLY — never set in production
    local_private_key_path: Optional[str] = Field(
        None, description="Path to PEM private key for local dev ONLY"
    )
    local_public_key_path: Optional[str] = Field(
        None, description="Path to PEM public key for local dev ONLY"
    )

    @field_validator("algorithm")
    @classmethod
    def algorithm_must_be_asymmetric(cls, v: str) -> str:
        if v not in ("RS256", "ES256", "RS384", "ES384", "RS512"):
            raise ValueError(
                "Only asymmetric algorithms (RS256, ES256, RS384, ES384, RS512) are permitted. "
                "HS256 is forbidden in production."
            )
        return v


class OIDCSettings(BaseModel):
    """OIDC / SSO Provider configuration."""

    provider_name: str = Field("google", description="SSO provider name (google, okta, azure)")
    client_id: str = Field("", description="OAuth2 client ID")
    client_secret_name: str = Field(
        "aegis-ai-oidc-secret", description="GCP Secret name for client secret"
    )
    discovery_url: str = Field("", description="OIDC discovery URL (/.well-known/openid-configuration)")
    jwks_cache_ttl_seconds: int = Field(300, description="JWKS cache TTL in seconds")
    allowed_issuers: List[str] = Field(default_factory=list)
    extra_scopes: List[str] = Field(default_factory=list)


class RateLimitSettings(BaseModel):
    """Rate Limiting configuration — OWASP LLM04."""

    enabled: bool = Field(True)
    requests_per_minute: int = Field(60, ge=1, le=10000)
    requests_per_hour: int = Field(1000, ge=1, le=100000)
    burst_multiplier: float = Field(1.5, description="Burst allowance multiplier")
    redis_url: Optional[str] = Field(None, description="Redis URL for distributed rate limiting")
    use_in_memory_fallback: bool = Field(
        True, description="Fall back to in-memory when Redis unavailable (not cluster-safe)"
    )
    backend: str = Field("memory", description="Backend type (memory, redis)")

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, v: str) -> str:
        allowed = {"memory", "redis"}
        if v not in allowed:
            raise ValueError(f"Rate limit backend must be one of {allowed}, got '{v}'")
        return v


class AuditSettings(BaseModel):
    """Audit Trail configuration."""

    enabled: bool = Field(True)
    log_name: str = Field("aegis-ai-audit", description="GCP Cloud Logging log name")
    signing_key_secret_name: str = Field(
        "aegis-ai-audit-signing-key", description="Secret name for HMAC signing key"
    )
    batch_size: int = Field(100, ge=1, le=1000)
    flush_interval_seconds: float = Field(5.0, ge=0.5, le=60.0)
    use_gcp_logging: bool = Field(True)
    use_structured_stdout: bool = Field(True, description="Also emit to structured stdout")


class EncryptionSettings(BaseModel):
    """Encryption configuration — OWASP A02."""

    kms_key_name: str = Field(
        "", description="Full GCP KMS key name (projects/.../cryptoKeyVersions/...)"
    )
    algorithm: str = Field("AES-256-GCM")
    key_cache_ttl_seconds: int = Field(300, description="DEK cache TTL")

    @field_validator("algorithm")
    @classmethod
    def validate_algorithm(cls, v: str) -> str:
        allowed = {"AES-256-GCM", "AES-128-GCM", "ChaCha20-Poly1305"}
        if v not in allowed:
            raise ValueError(f"Encryption algorithm must be one of {allowed}")
        return v


class TLSSettings(BaseModel):
    """TLS configuration."""

    minimum_version: str = Field("TLSv1.2", description="Minimum TLS version")
    preferred_version: str = Field("TLSv1.3")
    verify_certificates: bool = Field(True)
    ca_bundle_path: Optional[str] = Field(None, description="Custom CA bundle path")

    @field_validator("minimum_version", "preferred_version")
    @classmethod
    def validate_tls_version(cls, v: str) -> str:
        allowed = {"TLSv1.2", "TLSv1.3"}
        if v not in allowed:
            raise ValueError(f"TLS version must be one of {allowed}. TLSv1.0/1.1 are deprecated.")
        return v


class GuardRailSettings(BaseModel):
    """GuardRail tuning parameters."""

    injection_threshold: float = Field(0.4, ge=0.0, le=1.0)
    toxicity_threshold: float = Field(0.7, ge=0.0, le=1.0)
    pii_block_on_detection: bool = Field(False, description="Block vs. mask on PII detection")
    max_prompt_length_chars: int = Field(32000, description="Max prompt character length")
    enable_dynamic_grounding: bool = Field(True)
    grounding_min_similarity: float = Field(0.3, ge=0.0, le=1.0)
    perspective_api_key_secret: str = Field(
        "aegis-ai-perspective-key", description="Secret name for Google Perspective API key"
    )
    use_local_toxicity_fallback: bool = Field(
        True, description="Use detoxify when Perspective API unavailable"
    )


class PipelineSettings(BaseModel):
    """Top-level pipeline feature flags."""

    enable_pii_masking: bool = Field(True)
    enable_prompt_injection_detection: bool = Field(True)
    block_toxic_content: bool = Field(True)
    enable_mfa_enforcement: bool = Field(False, description="Require MFA for all agent calls")
    environment: str = Field("production")
    fail_open: bool = Field(
        False,
        description="If True, allow request on non-security errors (UNSAFE — dev only)",
    )


class LLMSettings(BaseModel):
    """LLM Provider configuration."""

    default_provider: str = Field("openai")
    default_model: str = Field("gpt-4o")
    request_timeout_seconds: float = Field(30.0, ge=1.0, le=300.0)
    max_retries: int = Field(3, ge=0, le=10)
    openai_api_key_secret: str = Field("aegis-ai-openai-key")
    anthropic_api_key_secret: str = Field("aegis-ai-anthropic-key")
    google_api_key_secret: str = Field("aegis-ai-google-key")
    approved_providers: List[str] = Field(
        default_factory=lambda: ["openai", "anthropic", "google"],
        description="Allowlist of zero-retention approved providers",
    )

    @field_validator("default_provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        allowed = {"openai", "anthropic", "google"}
        if v not in allowed:
            raise ValueError(f"LLM provider must be one of {allowed}")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Root Settings
# ─────────────────────────────────────────────────────────────────────────────


class AegisSettings(BaseSettings):
    """
    Root settings for the Aegis AI SDK.

    Loads configuration in priority order (highest → lowest):
      1. Process environment variables (``AEGIS__*``)
      2. ``.env`` file (developer overrides — gitignored)
      3. ``envs/.env.{AEGIS_ENV}`` (environment-specific defaults)
      4. Pydantic model defaults

    Set ``AEGIS_ENV=development|staging|production`` to select environment.
    Default is ``production`` (safe fallback).

    Example::

        AEGIS__GCP__PROJECT_ID=my-project
        AEGIS__JWT__ISSUER=https://auth.example.com
        AEGIS__RATE_LIMIT__REQUESTS_PER_MINUTE=100
    """

    model_config = SettingsConfigDict(
        env_file=_resolve_env_files(),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="AEGIS__",
        extra="ignore",
    )

    # Core identity
    environment: Environment = Field(
        Environment.PRODUCTION,
        description="Active deployment environment. Drives startup validation.",
    )
    log_level: str = Field("INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR)")
    secret_backend: str = Field(
        "gcp",
        description="Secret loading backend: 'gcp' (GCP Secret Manager) or 'env' (env-vars/files)",
    )

    # Sub-configs
    gcp: GCPSettings = Field(default_factory=GCPSettings)
    jwt: JWTSettings = Field(default_factory=JWTSettings)
    oidc: Optional[OIDCSettings] = None
    llm: LLMSettings = Field(default_factory=LLMSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    audit: AuditSettings = Field(default_factory=AuditSettings)
    encryption: EncryptionSettings = Field(default_factory=EncryptionSettings)
    tls: TLSSettings = Field(default_factory=TLSSettings)
    guardrails: GuardRailSettings = Field(default_factory=GuardRailSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)

    # ── Production Safety Validators ─────────────────────────────────────────

    @model_validator(mode="after")
    def enforce_production_constraints(self) -> "AegisSettings":
        """
        Reject obviously insecure configurations for production/staging.
        Raises ValueError at settings-load time so the process fails fast.
        """
        env = self.environment

        if env.is_production():
            # GCP must be enabled
            if not self.gcp.use_gcp:
                raise ValueError(
                    "AEGIS__GCP__USE_GCP must be true in production. "
                    "GCP Secret Manager is required for production key management."
                )
            # Local PEM key paths must not be set
            if self.jwt.local_private_key_path or self.jwt.local_public_key_path:
                raise ValueError(
                    "AEGIS__JWT__LOCAL_PRIVATE_KEY_PATH and LOCAL_PUBLIC_KEY_PATH "
                    "must NOT be set in production. Use GCP Secret Manager."
                )
            # TLS 1.3 enforced
            if self.tls.minimum_version != "TLSv1.3":
                raise ValueError(
                    "AEGIS__TLS__MINIMUM_VERSION must be TLSv1.3 in production."
                )
            # Certificate verification required
            if not self.tls.verify_certificates:
                raise ValueError(
                    "AEGIS__TLS__VERIFY_CERTIFICATES must be true in production."
                )
            # Rate limiting must use Redis, not in-memory
            if self.rate_limit.backend == "memory":
                raise ValueError(
                    "AEGIS__RATE_LIMIT__BACKEND must be 'redis' in production. "
                    "In-memory rate limiting is not cluster-safe."
                )
            # Audit must use GCP Cloud Logging
            if not self.audit.use_gcp_logging:
                raise ValueError(
                    "AEGIS__AUDIT__USE_GCP_LOGGING must be true in production."
                )
            # fail_open is strictly forbidden in production
            if self.pipeline.fail_open:
                raise ValueError(
                    "AEGIS__PIPELINE__FAIL_OPEN must be false in production. "
                    "fail_open=True means security errors are silently swallowed."
                )

        if env in (Environment.STAGING, Environment.PRODUCTION):
            if self.secret_backend == "env" and env.is_production():
                raise ValueError(
                    "AEGIS__SECRET_BACKEND cannot be 'env' in production. "
                    "Use 'gcp' to load secrets from GCP Secret Manager."
                )

        return self

    # ── Convenience Helpers ──────────────────────────────────────────────────

    def is_development(self) -> bool:
        """True when running in the development environment."""
        return self.environment.is_development()

    def is_staging(self) -> bool:
        """True when running in the staging environment."""
        return self.environment.is_staging()

    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.environment.is_production()

    def active_env_label(self) -> str:
        """Human-readable environment label for logging."""
        return self.environment.value.upper()

    @classmethod
    def for_environment(cls, env: str) -> "AegisSettings":
        """
        Construct settings for a specific environment, bypassing the cached singleton.

        Useful in tests and factory methods.

        Args:
            env: One of 'development', 'staging', 'production'.

        Returns:
            A new AegisSettings instance for the given environment.
        """
        old = os.environ.get("AEGIS_ENV")
        try:
            os.environ["AEGIS_ENV"] = env
            return cls()
        finally:
            if old is None:
                os.environ.pop("AEGIS_ENV", None)
            else:
                os.environ["AEGIS_ENV"] = old


@lru_cache(maxsize=1)
def get_settings() -> AegisSettings:
    """
    Returns a cached singleton AegisSettings instance.

    The environment is determined by the ``AEGIS_ENV`` environment variable.
    Defaults to ``production`` if not set.

    The cache is intentionally invalidated by calling ``get_settings.cache_clear()``
    in tests to allow per-test configuration.
    """
    return AegisSettings()
