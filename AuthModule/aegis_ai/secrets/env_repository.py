"""
aegis_ai.secrets.env_repository
=================================
Environment-variable / local-file implementation of SecretRepository.

Design Pattern: Strategy (Concrete Strategy — development / testing)

Lookup order for ``get_secret(name)``:
  1. Process environment variable matching ``{ENV_PREFIX}{name}`` (uppercased,
     hyphens → underscores). Default prefix: ``AEGIS_SECRET_``
  2. File at ``{secrets_dir}/{name}`` if ``secrets_dir`` is configured.
  3. ``SecretNotFoundError`` — never silently returns empty string.

WARNING: This implementation is ONLY suitable for development and CI.
         It MUST NOT be used in staging or production environments.
         The startup validator enforces this constraint.

OWASP: LLM06, A02:2021
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import structlog

from aegis_ai.secrets.base import (
    SecretNotFoundError,
    SecretRepository,
)

logger = structlog.get_logger(__name__)


class EnvSecretRepository(SecretRepository):
    """
    Loads secrets from process environment variables or local files.

    This is the development-mode strategy. It requires no cloud credentials
    and allows developers to run the full pipeline locally.

    Lookup order:
      1. Environment variable: ``{env_prefix}{normalized_name}``
         where ``normalized_name`` = ``name.upper().replace("-", "_")``
      2. File: ``{secrets_dir}/{name}`` (if ``secrets_dir`` is set)

    Args:
        env_prefix:  Prefix prepended to env-var names. Defaults to
                     ``AEGIS_SECRET_``.
        secrets_dir: Optional path to a directory containing secret files
                     (one secret per file, filename = secret name).

    Example::

        # In your shell:
        export AEGIS_SECRET_JWT_PRIVATE_KEY="$(cat dev_key.pem)"

        repo = EnvSecretRepository()
        pem = await repo.get_secret("jwt-private-key")
        # → reads AEGIS_SECRET_JWT_PRIVATE_KEY
    """

    def __init__(
        self,
        env_prefix: str = "AEGIS_SECRET_",
        secrets_dir: Optional[Path] = None,
    ) -> None:
        self._env_prefix = env_prefix
        self._secrets_dir = secrets_dir

    def _normalize_name(self, name: str) -> str:
        """Normalize secret name for env-var lookup."""
        return name.upper().replace("-", "_").replace("/", "_").replace(".", "_")

    def _lookup(self, name: str) -> Optional[bytes]:
        """Attempt all lookup sources, returning raw bytes or None."""
        # 1. Environment variable
        env_key = f"{self._env_prefix}{self._normalize_name(name)}"
        value = os.environ.get(env_key)
        if value is not None:
            logger.debug("secret_from_env_var", secret_name=name, env_key=env_key)
            return value.encode("utf-8")

        # 2. Secret file
        if self._secrets_dir is not None:
            secret_path = self._secrets_dir / name
            if secret_path.is_file():
                logger.debug("secret_from_file", secret_name=name, path=str(secret_path))
                return secret_path.read_bytes()

        return None

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_secret_bytes(self, name: str, version: str = "latest") -> bytes:
        """
        Return secret as raw bytes.

        The ``version`` parameter is ignored (env-vars have no versioning).
        """
        raw = self._lookup(name)
        if raw is None:
            raise SecretNotFoundError(
                f"Secret '{name}' not found. "
                f"Set env var '{self._env_prefix}{self._normalize_name(name)}' "
                f"or place the secret in '{self._secrets_dir}/{name}'.",
                secret_name=name,
            )
        return raw

    async def get_secret(self, name: str, version: str = "latest") -> str:
        """Return secret as a UTF-8 string."""
        raw = await self.get_secret_bytes(name, version)
        return raw.decode("utf-8").strip()

    async def secret_exists(self, name: str) -> bool:
        """Return True if the secret can be resolved from env or file."""
        return self._lookup(name) is not None
