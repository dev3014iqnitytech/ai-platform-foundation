"""
aegis_ai.secrets
=================
Secret Repository pattern — Strategy for loading secrets.

Provides a unified interface for fetching secrets from different backends:
  - GCP Secret Manager  (production / staging)
  - Environment variables / local files  (development)
  - HashiCorp Vault  (stub — extend for self-hosted deployments)

OWASP: LLM06 (Sensitive Info Disclosure), A02:2021 (Cryptographic Failures)
"""

from aegis_ai.secrets.base import SecretRepository
from aegis_ai.secrets.env_repository import EnvSecretRepository
from aegis_ai.secrets.gcp_repository import GCPSecretRepository

__all__ = [
    "SecretRepository",
    "EnvSecretRepository",
    "GCPSecretRepository",
]
