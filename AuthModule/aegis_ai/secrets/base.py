"""
aegis_ai.secrets.base
======================
Abstract base class for the Secret Repository strategy.

SOLID: Open/Closed — add new backends (Vault, AWS SSM, Azure KeyVault)
by implementing SecretRepository without touching callers.

Design Pattern: Strategy
  - Context     : AuthProvider, AuditLogger, LLMGateway (consumers)
  - Strategy    : SecretRepository (this interface)
  - ConcreteA   : GCPSecretRepository
  - ConcreteB   : EnvSecretRepository
  - ConcreteC   : (stub) VaultSecretRepository
"""

from __future__ import annotations

import abc
from typing import Optional


class SecretRepository(abc.ABC):
    """
    Abstract secret store interface.

    All concrete implementations must be safe for concurrent async access.
    Implementations should cache fetched secrets in memory with a short TTL
    to avoid repeated network round-trips on hot paths.
    """

    @abc.abstractmethod
    async def get_secret(self, name: str, version: str = "latest") -> str:
        """
        Fetch a secret by name.

        Args:
            name:    Secret identifier (e.g. 'aegis-jwt-private-key').
            version: Version label. Defaults to 'latest'.

        Returns:
            The secret payload as a UTF-8 string.

        Raises:
            SecretNotFoundError:    Secret does not exist.
            SecretAccessError:      Permission denied or backend unreachable.
            SecretDecryptionError:  Secret payload could not be decoded.
        """
        ...

    @abc.abstractmethod
    async def get_secret_bytes(self, name: str, version: str = "latest") -> bytes:
        """
        Fetch a secret as raw bytes (e.g. for binary keys / certificates).

        Args:
            name:    Secret identifier.
            version: Version label. Defaults to 'latest'.

        Returns:
            Raw secret bytes.
        """
        ...

    @abc.abstractmethod
    async def secret_exists(self, name: str) -> bool:
        """
        Check whether a secret exists without fetching its value.

        Args:
            name: Secret identifier.

        Returns:
            True if the secret exists and is accessible.
        """
        ...

    async def get_secret_optional(
        self, name: str, default: Optional[str] = None
    ) -> Optional[str]:
        """
        Fetch a secret, returning ``default`` if the secret does not exist.

        Concrete implementations may override for efficiency (e.g. avoid
        a round-trip by catching 404 directly in ``get_secret``).
        """
        try:
            return await self.get_secret(name)
        except SecretNotFoundError:
            return default


# ─────────────────────────────────────────────────────────────────────────────
# Secret-specific Exceptions
# ─────────────────────────────────────────────────────────────────────────────


class SecretError(Exception):
    """Base exception for all secret-related errors."""

    def __init__(self, message: str, secret_name: str) -> None:
        super().__init__(message)
        self.secret_name = secret_name


class SecretNotFoundError(SecretError):
    """Raised when the requested secret does not exist."""


class SecretAccessError(SecretError):
    """Raised when secret access is denied or the backend is unreachable."""


class SecretDecryptionError(SecretError):
    """Raised when the secret payload cannot be decoded or decrypted."""
