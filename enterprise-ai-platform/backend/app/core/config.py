"""
Enterprise AI Test Automation Platform — Core Configuration
Typed settings with validation, sourced from environment variables and Azure Key Vault.
"""
from __future__ import annotations
from pathlib import Path

from enum import Enum
from functools import lru_cache
from typing import Any

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """
    Central configuration for all platform services.
    Values are loaded from environment variables (.env in dev, K8s secrets in prod).
    """

    model_config = SettingsConfigDict(
        # resolve .env relative to this file so it works regardless of cwd
        env_file=str(Path(__file__).parents[2] / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─────────────────────────────────────────────────────────────
    # Application
    # ─────────────────────────────────────────────────────────────
    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    APP_NAME: str = "Enterprise AI Test Automation Platform"
    APP_VERSION: str = "1.0.0"
    APP_SECRET_KEY: SecretStr = Field(..., min_length=32)
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ─────────────────────────────────────────────────────────────
    # Local Mode — replaces all Azure services with OpenAI-compatible providers.
    # Set LOCAL_MODE=true to run without any Azure dependencies.
    # Compatible with: GitHub Copilot API, GitHub Models, Ollama, LM Studio.
    # ─────────────────────────────────────────────────────────────
    LOCAL_MODE: bool = False
    # Chat/completion endpoint (OpenAI-compatible)
    # e.g. https://api.githubcopilot.com  or  http://localhost:11434/v1
    OPENAI_API_BASE: str | None = None
    # Separate embedding endpoint — defaults to OPENAI_API_BASE when not set.
    # GitHub Copilot does not serve embeddings; use GitHub Models for those:
    # e.g. https://models.inference.ai.azure.com
    EMBED_API_BASE: str | None = None
    OPENAI_API_KEY: str = "local"
    LOCAL_LLM_MODEL: str = "gpt-4o"
    LOCAL_MINI_LLM_MODEL: str = "gpt-4o-mini"
    LOCAL_EMBED_MODEL: str = "text-embedding-3-small"
    # HS256 secret for local JWT issuance (no Azure AD required)
    LOCAL_JWT_SECRET: SecretStr = SecretStr("local-dev-jwt-secret-must-be-32chars!!")
    LOCAL_JWT_ALGORITHM: str = "HS256"

    # ─────────────────────────────────────────────────────────────
    # Azure OpenAI (required when LOCAL_MODE=false)
    # ─────────────────────────────────────────────────────────────
    AZURE_OPENAI_ENDPOINT: AnyHttpUrl | None = None
    AZURE_OPENAI_API_KEY: SecretStr | None = None
    AZURE_OPENAI_CHAT_DEPLOYMENT: str = "gpt-4o"
    AZURE_OPENAI_MINI_DEPLOYMENT: str = "gpt-4o-mini"
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT: str = "text-embedding-3-large"
    AZURE_OPENAI_API_VERSION: str = "2024-08-01-preview"

    # ─────────────────────────────────────────────────────────────
    # Azure AI Search (optional — falls back to FAISS when unset)
    # ─────────────────────────────────────────────────────────────
    AZURE_SEARCH_ENDPOINT: AnyHttpUrl | None = None
    AZURE_SEARCH_API_KEY: SecretStr | None = None
    AZURE_SEARCH_INDEX_NAME: str = "eatap-knowledge-base"

    # ─────────────────────────────────────────────────────────────
    # Azure AD / Authentication (required when LOCAL_MODE=false)
    # ─────────────────────────────────────────────────────────────
    AZURE_TENANT_ID: str | None = None
    AZURE_CLIENT_ID: str | None = None
    AZURE_CLIENT_SECRET: SecretStr | None = None
    AZURE_AUDIENCE: str | None = None

    # ─────────────────────────────────────────────────────────────
    # Azure DevOps (optional — agents return mock data when unset)
    # ─────────────────────────────────────────────────────────────
    ADO_ORGANIZATION: AnyHttpUrl | None = None
    ADO_PROJECT: str | None = None
    ADO_PAT: SecretStr | None = None
    ADO_API_VERSION: str = "7.1"

    # ─────────────────────────────────────────────────────────────
    # Database
    # ─────────────────────────────────────────────────────────────
    DATABASE_URL: SecretStr = Field(...)
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    DATABASE_POOL_TIMEOUT: int = 30

    # ─────────────────────────────────────────────────────────────
    # Redis
    # ─────────────────────────────────────────────────────────────
    REDIS_URL: SecretStr = Field(...)
    REDIS_PASSWORD: SecretStr | None = None
    REDIS_MAX_CONNECTIONS: int = 50

    # ─────────────────────────────────────────────────────────────
    # Azure Service Bus (optional — events are dropped when unset)
    # ─────────────────────────────────────────────────────────────
    SERVICE_BUS_CONNECTION_STRING: SecretStr | None = None
    SERVICE_BUS_NAMESPACE: str | None = None

    # ─────────────────────────────────────────────────────────────
    # Azure Key Vault (optional — env vars used directly when unset)
    # ─────────────────────────────────────────────────────────────
    KEY_VAULT_URL: AnyHttpUrl | None = None

    # ─────────────────────────────────────────────────────────────
    # Azure Blob Storage
    # ─────────────────────────────────────────────────────────────
    AZURE_STORAGE_CONNECTION_STRING: SecretStr | None = Field(default=None)
    AZURE_STORAGE_CONTAINER: str = "eatap-documents"

    # ─────────────────────────────────────────────────────────────
    # JWT
    # ─────────────────────────────────────────────────────────────
    JWT_ALGORITHM: str = "RS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ─────────────────────────────────────────────────────────────
    # CORS
    # ─────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: list[str] = ["http://localhost:5173"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> "Settings":
        """Enforce required fields based on LOCAL_MODE."""
        if self.LOCAL_MODE:
            if not self.OPENAI_API_BASE:
                raise ValueError(
                    "OPENAI_API_BASE is required when LOCAL_MODE=true "
                    "(e.g. http://localhost:11434/v1 for Ollama)"
                )
        else:
            cloud_required = {
                "AZURE_OPENAI_ENDPOINT": self.AZURE_OPENAI_ENDPOINT,
                "AZURE_OPENAI_API_KEY": self.AZURE_OPENAI_API_KEY,
                "AZURE_TENANT_ID": self.AZURE_TENANT_ID,
                "AZURE_CLIENT_ID": self.AZURE_CLIENT_ID,
                "AZURE_CLIENT_SECRET": self.AZURE_CLIENT_SECRET,
                "AZURE_AUDIENCE": self.AZURE_AUDIENCE,
                "ADO_ORGANIZATION": self.ADO_ORGANIZATION,
                "ADO_PROJECT": self.ADO_PROJECT,
                "ADO_PAT": self.ADO_PAT,
            }
            missing = [k for k, v in cloud_required.items() if v is None]
            if missing:
                raise ValueError(
                    f"Missing required cloud settings: {', '.join(missing)}. "
                    "Set LOCAL_MODE=true to run without Azure dependencies."
                )
        return self

    # ─────────────────────────────────────────────────────────────
    # Observability
    # ─────────────────────────────────────────────────────────────
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"
    OTEL_SERVICE_NAME: str = "eatap-backend"
    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: SecretStr | None = None
    LANGCHAIN_PROJECT: str = "eatap-production"

    # ─────────────────────────────────────────────────────────────
    # Cohere
    # ─────────────────────────────────────────────────────────────
    COHERE_API_KEY: SecretStr | None = None

    # ─────────────────────────────────────────────────────────────
    # Feature Flags
    # ─────────────────────────────────────────────────────────────
    ENABLE_SEMANTIC_CACHE: bool = True
    ENABLE_MULTI_QUERY_RETRIEVAL: bool = True
    MAX_REVISION_COUNT: int = 3
    TOKEN_BUDGET_PER_SESSION: int = 50_000

    # ─────────────────────────────────────────────────────────────
    # Notifications
    # ─────────────────────────────────────────────────────────────
    TEAMS_WEBHOOK_URL: AnyHttpUrl | None = None   # Teams Incoming Webhook URL
    FRONTEND_URL: str = "http://localhost:5173"    # used in notification deep-links

    # ─────────────────────────────────────────────────────────────
    # MCP Server URLs (optional — agents fall back to direct REST if unset)
    # ─────────────────────────────────────────────────────────────
    MCP_ADO_URL: AnyHttpUrl | None = None          # e.g. http://ado-mcp:8001
    MCP_KB_URL: AnyHttpUrl | None = None           # e.g. http://kb-mcp:8002
    MCP_SHAREPOINT_URL: AnyHttpUrl | None = None   # e.g. http://sp-mcp:8003

    # ─────────────────────────────────────────────────────────────
    # Rate Limiting
    # ─────────────────────────────────────────────────────────────
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 60
    RATE_LIMIT_BURST: int = 20

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == Environment.PRODUCTION

    @property
    def database_url_str(self) -> str:
        return self.DATABASE_URL.get_secret_value()

    @property
    def redis_url_str(self) -> str:
        return self.REDIS_URL.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings instance — call this everywhere via DI.
    Use lru_cache to avoid re-parsing env on every import.
    """
    return Settings()


# Convenience singleton
settings = get_settings()
