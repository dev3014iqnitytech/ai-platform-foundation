"""
Secrets Manager — Azure Key Vault integration with local env fallback.
All sensitive credentials must be fetched through this module.
Never access os.environ directly for secrets in production code.
"""
from __future__ import annotations

import os
from functools import lru_cache
from structlog import get_logger

logger = get_logger(__name__)


class SecretsManager:
    """
    Fetches secrets from Azure Key Vault in production.
    Falls back to environment variables for local development.
    
    Secret names follow Azure Key Vault naming conventions (hyphens, not underscores).
    Example: "azure-openai-api-key" → env var fallback: AZURE_OPENAI_API_KEY
    """

    def __init__(self, vault_url: str | None = None):
        self.vault_url = vault_url
        self._kv_client = None
        self._cache: dict[str, str] = {}
        self._kv_available = False
        if vault_url:
            self._try_init_kv(vault_url)

    def _try_init_kv(self, vault_url: str) -> None:
        try:
            from azure.keyvault.secrets import SecretClient
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential()
            self._kv_client = SecretClient(vault_url=vault_url, credential=credential)
            self._kv_available = True
            logger.info("key_vault_connected", vault_url=vault_url)
        except ImportError:
            logger.warning("key_vault_unavailable", reason="azure-keyvault-secrets not installed")
        except Exception as e:
            logger.warning("key_vault_init_failed", error=str(e), fallback="environment variables")

    def get(self, secret_name: str, default: str | None = None) -> str | None:
        """Retrieve a secret by name. Checks cache → Key Vault → environment."""
        if secret_name in self._cache:
            return self._cache[secret_name]

        # Try Key Vault first
        if self._kv_available and self._kv_client:
            try:
                secret = self._kv_client.get_secret(secret_name)
                value = secret.value
                if value:
                    self._cache[secret_name] = value
                    return value
            except Exception as e:
                logger.warning("key_vault_secret_fetch_failed", secret=secret_name, error=str(e))

        # Fallback to environment variable
        env_name = secret_name.upper().replace("-", "_")
        value = os.environ.get(env_name, default)
        if value:
            self._cache[secret_name] = value
        return value

    def get_required(self, secret_name: str) -> str:
        """Retrieve a required secret. Raises ValueError if not found."""
        value = self.get(secret_name)
        if not value:
            raise ValueError(
                f"Required secret '{secret_name}' not found in Key Vault or environment. "
                f"Set env var '{secret_name.upper().replace('-', '_')}' for local development."
            )
        return value

    def invalidate(self, secret_name: str) -> None:
        """Remove a secret from cache (force re-fetch on next access)."""
        self._cache.pop(secret_name, None)

    def invalidate_all(self) -> None:
        """Clear entire cache."""
        self._cache.clear()


# Well-known secret names (match Azure Key Vault naming)
class SecretNames:
    AZURE_OPENAI_API_KEY = "azure-openai-api-key"
    AZURE_SEARCH_API_KEY = "azure-search-api-key"
    ADO_PAT_TOKEN = "ado-pat-token"
    DB_CONNECTION_STRING = "db-connection-string"
    REDIS_CONNECTION_STRING = "redis-connection-string"
    JWT_SIGNING_KEY = "jwt-signing-key"
    COHERE_API_KEY = "cohere-api-key"
    AZURE_CONTENT_SAFETY_KEY = "azure-content-safety-key"
    SERVICE_BUS_CONNECTION = "service-bus-connection-string"


# Module-level singleton
@lru_cache(maxsize=1)
def get_secrets_manager() -> SecretsManager:
    vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    return SecretsManager(vault_url=vault_url)
