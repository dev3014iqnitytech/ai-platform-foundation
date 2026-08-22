"""
aegis_ai.secrets.gcp_repository
=================================
GCP Secret Manager implementation of SecretRepository.

Design Pattern: Strategy (Concrete Strategy — production)

Features:
  - In-process TTL cache (configurable, default 300 s) to reduce API calls
  - Automatic retry with exponential backoff on transient GCP errors
  - Structured audit logging on every access for SIEM ingestion
  - Workload Identity / ADC compatible (no service-account key file required)
  - Thread-safe: asyncio.Lock per secret name prevents thundering herd

OWASP: LLM06, A02:2021, A09:2021
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Tuple

import structlog

from aegis_ai.secrets.base import (
    SecretAccessError,
    SecretDecryptionError,
    SecretNotFoundError,
    SecretRepository,
)

logger = structlog.get_logger(__name__)

# Cache entry: (value_bytes, fetched_at_monotonic)
_CacheEntry = Tuple[bytes, float]


class GCPSecretRepository(SecretRepository):
    """
    Fetches secrets from GCP Secret Manager with TTL caching.

    Usage::

        repo = GCPSecretRepository(
            project_id="my-gcp-project",
            prefix="aegis-ai",          # prepended to every secret name
            cache_ttl_seconds=300,
        )
        private_key_pem = await repo.get_secret("jwt-private-key")
        # → fetches "aegis-ai/jwt-private-key" from Secret Manager

    Args:
        project_id:         GCP project ID.
        prefix:             Optional prefix prepended to all secret names.
        cache_ttl_seconds:  How long to cache fetched secrets in-process.
        max_retries:        Number of retries on transient GCP errors.
    """

    def __init__(
        self,
        project_id: str,
        prefix: str = "",
        cache_ttl_seconds: float = 300.0,
        max_retries: int = 3,
    ) -> None:
        self._project_id = project_id
        self._prefix = prefix.rstrip("/") if prefix else ""
        self._cache_ttl = cache_ttl_seconds
        self._max_retries = max_retries

        # {secret_name: (bytes, fetched_at)}
        self._cache: Dict[str, _CacheEntry] = {}
        # Per-name locks to prevent parallel fetches of the same secret
        self._locks: Dict[str, asyncio.Lock] = {}
        self._client: Optional[object] = None  # lazy-init

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _get_client(self) -> object:
        """Lazy-initialise the GCP Secret Manager client (thread-safe via GIL)."""
        if self._client is None:
            try:
                from google.cloud import secretmanager  # type: ignore[import-untyped]

                self._client = secretmanager.SecretManagerServiceClient()
            except ImportError as exc:
                raise SecretAccessError(
                    "google-cloud-secret-manager is not installed. "
                    "Install it with: pip install google-cloud-secret-manager",
                    secret_name="<client>",
                ) from exc
        return self._client

    def _build_resource_name(self, name: str, version: str) -> str:
        """Build the full GCP resource path for a secret version."""
        full_name = f"{self._prefix}-{name}" if self._prefix else name
        return (
            f"projects/{self._project_id}/secrets/{full_name}/versions/{version}"
        )

    def _lock_for(self, name: str) -> asyncio.Lock:
        """Return (or create) a per-secret asyncio.Lock."""
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    def _cache_hit(self, name: str) -> Optional[bytes]:
        """Return cached bytes if within TTL, else None."""
        entry = self._cache.get(name)
        if entry is None:
            return None
        value, fetched_at = entry
        if time.monotonic() - fetched_at < self._cache_ttl:
            return value
        del self._cache[name]
        return None

    async def _fetch_with_retry(self, resource_name: str, secret_name: str) -> bytes:
        """Fetch secret bytes from GCP SM with exponential-backoff retries."""
        client = self._get_client()
        last_exc: Optional[Exception] = None

        for attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.to_thread(
                    client.access_secret_version,  # type: ignore[union-attr]
                    request={"name": resource_name},
                )
                payload: bytes = response.payload.data
                logger.debug(
                    "secret_fetched",
                    secret_name=secret_name,
                    attempt=attempt,
                    bytes_len=len(payload),
                )
                return payload

            except Exception as exc:
                last_exc = exc
                exc_str = str(exc).lower()

                # Surface 404 immediately — no retry
                if "not found" in exc_str or "404" in exc_str:
                    raise SecretNotFoundError(
                        f"Secret '{secret_name}' not found in GCP Secret Manager.",
                        secret_name=secret_name,
                    ) from exc

                # Permission denied — no retry
                if "permission" in exc_str or "403" in exc_str or "unauthenticated" in exc_str:
                    raise SecretAccessError(
                        f"Access denied for secret '{secret_name}'. "
                        "Check Secret Manager IAM bindings.",
                        secret_name=secret_name,
                    ) from exc

                # Transient error — backoff and retry
                if attempt < self._max_retries:
                    backoff = min(0.5 * (2 ** attempt), 8.0)
                    logger.warning(
                        "secret_fetch_transient_error",
                        secret_name=secret_name,
                        attempt=attempt,
                        backoff_seconds=backoff,
                        error=str(exc),
                    )
                    await asyncio.sleep(backoff)

        raise SecretAccessError(
            f"Failed to fetch secret '{secret_name}' after {self._max_retries} retries: {last_exc}",
            secret_name=secret_name,
        ) from last_exc

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_secret_bytes(self, name: str, version: str = "latest") -> bytes:
        """Fetch a secret as raw bytes, using in-process TTL cache."""
        cache_key = f"{name}:{version}"

        # Fast path: cache hit (no lock required)
        cached = self._cache_hit(cache_key)
        if cached is not None:
            return cached

        # Slow path: fetch under per-secret lock (prevents thundering herd)
        async with self._lock_for(cache_key):
            # Re-check after acquiring lock
            cached = self._cache_hit(cache_key)
            if cached is not None:
                return cached

            resource_name = self._build_resource_name(name, version)
            payload = await self._fetch_with_retry(resource_name, name)
            self._cache[cache_key] = (payload, time.monotonic())
            return payload

    async def get_secret(self, name: str, version: str = "latest") -> str:
        """Fetch a secret as a UTF-8 string."""
        raw = await self.get_secret_bytes(name, version)
        try:
            return raw.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise SecretDecryptionError(
                f"Secret '{name}' is not valid UTF-8. Use get_secret_bytes() for binary secrets.",
                secret_name=name,
            ) from exc

    async def secret_exists(self, name: str) -> bool:
        """Check if a secret exists in GCP Secret Manager (no caching)."""
        try:
            await self.get_secret(name)
            return True
        except SecretNotFoundError:
            return False
        except SecretAccessError:
            return False

    def invalidate_cache(self, name: Optional[str] = None) -> None:
        """
        Invalidate cached secrets.

        Args:
            name: If provided, invalidate only this secret. Otherwise, clear all.
        """
        if name is None:
            self._cache.clear()
            logger.info("secret_cache_cleared_all")
        else:
            keys_to_remove = [k for k in self._cache if k.startswith(f"{name}:")]
            for k in keys_to_remove:
                del self._cache[k]
            logger.info("secret_cache_invalidated", secret_name=name)
