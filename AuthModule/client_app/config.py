"""
client_app.config
==================
Centralised configuration for the Client API (Agent A).

Loads from environment variables and an optional .env file in this directory.
Never hardcode secrets here.

Environment variable reference:
    AEGIS_GATEWAY_URL           Aegis Security Gateway base URL (default: http://localhost:8080)
    CLIENT_HOST                 Host to bind the FastAPI server (default: 0.0.0.0)
    CLIENT_PORT                 Port to bind the FastAPI server (default: 8001)
    CLIENT_ENVIRONMENT          development | staging | production (default: development)
    CLIENT_LOG_LEVEL            debug | info | warning | error (default: info)

    # SSO / token settings
    CLIENT_SSO_ISSUER           JWT issuer the Gateway expects (default: https://mock-sso.local)
    CLIENT_SSO_AUDIENCE         JWT audience the Gateway expects (default: aegis-ai-gateway)
    CLIENT_SSO_SUBJECT          sub claim for Agent A's M2M token (default: agent-a-client-id)
    CLIENT_SSO_TENANT_ID        Tenant ID injected into the token (default: enterprise-tenant-01)
    CLIENT_TOKEN_TTL_MINUTES    Token validity window in minutes (default: 5)
    CLIENT_TOKEN_REFRESH_BUFFER Buffer in seconds before expiry to refresh token (default: 60)
    CLIENT_MOCK_KEY_PATH        Path to PEM private key for mock SSO signing.
                                If absent, a key is generated once at startup.

    # End-user token verification (for /api/secure/* endpoints)
    CLIENT_USER_JWT_SECRET      Shared HS256 secret for verifying user tokens (dev only).
                                Leave empty to skip user-token verification (NOT for prod).
    CLIENT_USER_JWT_ALGORITHM   Algorithm to verify user tokens (default: HS256)
    CLIENT_USER_JWT_ISSUER      Issuer to enforce on user tokens (default: "" = skip)
    CLIENT_USER_JWT_AUDIENCE    Audience to enforce on user tokens (default: "" = skip)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env in the same directory as this file
_CLIENT_DIR = Path(__file__).resolve().parent
_ENV_FILE = _CLIENT_DIR / ".env"


class ClientSettings(BaseSettings):
    """
    Client API (Agent A) configuration.

    Loaded in priority order (highest first):
      1. Process environment variables
      2. .env file in client_app/ directory (gitignored)
      3. Pydantic model defaults
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Server ────────────────────────────────────────────────────────────────
    client_host: str = Field("0.0.0.0", alias="CLIENT_HOST")
    client_port: int = Field(8001, alias="CLIENT_PORT")
    environment: str = Field("development", alias="CLIENT_ENVIRONMENT")
    log_level: str = Field("info", alias="CLIENT_LOG_LEVEL")

    # ── Gateway ───────────────────────────────────────────────────────────────
    aegis_gateway_url: str = Field(
        "http://localhost:8080",
        alias="AEGIS_GATEWAY_URL",
        description="Aegis Security Gateway base URL (no trailing slash)",
    )

    @field_validator("aegis_gateway_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def invoke_url(self) -> str:
        """Full URL for the /v1/agent/invoke endpoint."""
        return f"{self.aegis_gateway_url}/v1/agent/invoke"

    # ── SSO / M2M token settings (Agent A → Gateway) ─────────────────────────
    sso_issuer: str = Field("https://mock-sso.local", alias="CLIENT_SSO_ISSUER")
    sso_audience: str = Field("aegis-ai-gateway", alias="CLIENT_SSO_AUDIENCE")
    sso_subject: str = Field("agent-a-client-id", alias="CLIENT_SSO_SUBJECT")
    sso_tenant_id: str = Field("enterprise-tenant-01", alias="CLIENT_SSO_TENANT_ID")
    sso_roles: list[str] = Field(
        default_factory=lambda: ["AGENT_CALLER"],
        alias="CLIENT_SSO_ROLES",
    )
    sso_permissions: list[str] = Field(
        default_factory=lambda: ["agents.call"],
        alias="CLIENT_SSO_PERMISSIONS",
    )
    token_ttl_minutes: int = Field(5, alias="CLIENT_TOKEN_TTL_MINUTES", ge=1, le=60)
    token_refresh_buffer_seconds: int = Field(
        60,
        alias="CLIENT_TOKEN_REFRESH_BUFFER",
        description="Seconds before expiry to proactively refresh the token",
    )

    # Path to a PEM private key for signing mock SSO tokens.
    # If unset, a key is generated once in memory at startup (dev mode).
    mock_key_path: Optional[str] = Field(None, alias="CLIENT_MOCK_KEY_PATH")

    # ── End-user token verification (client → this service) ──────────────────
    user_jwt_secret: str = Field(
        "",
        alias="CLIENT_USER_JWT_SECRET",
        description="Shared HS256 secret for verifying end-user tokens (dev only).",
    )
    user_jwt_algorithm: str = Field("HS256", alias="CLIENT_USER_JWT_ALGORITHM")
    user_jwt_issuer: str = Field("", alias="CLIENT_USER_JWT_ISSUER")
    user_jwt_audience: str = Field("", alias="CLIENT_USER_JWT_AUDIENCE")

    # ── Convenience helpers ───────────────────────────────────────────────────

    def is_development(self) -> bool:
        return self.environment.lower() == "development"

    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def user_auth_enabled(self) -> bool:
        """True when end-user token verification is configured."""
        return bool(self.user_jwt_secret)


@lru_cache(maxsize=1)
def get_client_settings() -> ClientSettings:
    """
    Cached singleton settings instance.

    Call ``get_client_settings.cache_clear()`` in tests to reset.
    """
    return ClientSettings()
