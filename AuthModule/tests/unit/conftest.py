"""
tests/unit/conftest.py
======================
Unit-test-specific pytest fixtures.

Provides:
- RSA key pair for JWT authentication tests (generated once per session)
- Preconfigured JWTHandler with injected keys
- Pre-built IdentityContext ready for token encoding
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from aegis_ai.auth.jwt_handler import JWTHandler
from aegis_ai.settings import AegisSettings


# ─────────────────────────────────────────────────────────────────────────────
# RSA Key Fixtures (session-scoped — generated once, reused across all tests)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def rsa_private_key_obj():
    """Generate a 2048-bit RSA private key object (session-scoped)."""
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )


@pytest.fixture(scope="session")
def rsa_private_pem(rsa_private_key_obj) -> bytes:
    """PEM-encoded RSA private key bytes."""
    return rsa_private_key_obj.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="session")
def rsa_public_pem(rsa_private_key_obj) -> bytes:
    """PEM-encoded RSA public key bytes."""
    return rsa_private_key_obj.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


# ─────────────────────────────────────────────────────────────────────────────
# JWTHandler Fixture
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def unit_settings() -> AegisSettings:
    """AegisSettings tuned for unit tests (no GCP, no Redis)."""
    from aegis_ai.settings import get_settings
    get_settings.cache_clear()
    return AegisSettings(
        environment="test",
    )


@pytest.fixture
def jwt_handler(unit_settings, rsa_private_pem, rsa_public_pem) -> JWTHandler:
    """JWTHandler pre-loaded with the session RSA key pair."""
    handler = JWTHandler(settings=unit_settings)
    handler.set_keys(
        private_pem=rsa_private_pem,
        public_pem=rsa_public_pem,
        kid="test-key-v1",
    )
    return handler
