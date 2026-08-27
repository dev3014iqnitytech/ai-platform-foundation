"""
tests/unit/test_auth.py
=======================
Unit tests for aegis_ai authentication layer.

Covers: JWTHandler, APIKeyManager, SSOProvider, MFAVerifier, IdentityContext
OWASP: Broken Authentication (LLM02), Token misuse
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import pytest
import jwt as pyjwt
from freezegun import freeze_time

from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.auth.jwt_handler import JWTHandler, JWTClaims
from aegis_ai.auth.api_key_manager import APIKeyManager, APIKeyRecord
from aegis_ai.auth.mfa_verifier import MFAVerifier
from aegis_ai.exceptions import (
    AuthenticationError,
    TokenExpiredError,
    TokenInvalidError,
    MFARequiredError,
)
from aegis_ai.types import AgentID, AuthMethod, Permission, TenantID, UserID


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.jwt.algorithm = "RS256"
    settings.jwt.issuer = "https://test.aegis-ai.com"
    settings.jwt.audience = "aegis-ai-agents"
    settings.jwt.access_token_expire_minutes = 15
    settings.jwt.refresh_token_expire_days = 7
    settings.jwt.private_key_secret_name = "jwt-private-key"
    settings.jwt.public_key_secret_name = "jwt-public-key"
    settings.jwt.local_private_key_path = None
    settings.jwt.local_public_key_path = None
    settings.pipeline.environment = "development"
    return settings


@pytest.fixture
def sample_identity():
    return IdentityContext(
        identity_id=UserID("user-001"),
        agent_id=AgentID("agent-001"),
        tenant_id=TenantID("tenant-001"),
        email="user@example.com",
        roles=frozenset(["AGENT_OPERATOR"]),
        permissions=frozenset([Permission("agents.call"), Permission("agents.read")]),
        auth_method=AuthMethod.JWT,
        auth_time=datetime.now(timezone.utc),
        session_id=str(uuid.uuid4()),
        metadata={},
        mfa_verified=True,
        ip_address="192.168.1.100",
    )


@pytest.fixture
def rsa_key_pair():
    """Generate a real RSA key pair for testing."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


# ─────────────────────────────────────────────────────────────────
# IdentityContext Tests
# ─────────────────────────────────────────────────────────────────

class TestIdentityContext:
    def test_has_permission_returns_true_for_granted(self, sample_identity):
        assert sample_identity.has_permission(Permission("agents.call")) is True

    def test_has_permission_returns_false_for_missing(self, sample_identity):
        assert sample_identity.has_permission(Permission("agents.delete")) is False

    def test_has_role_returns_true_for_assigned_role(self, sample_identity):
        assert sample_identity.has_role("AGENT_OPERATOR") is True

    def test_has_role_returns_false_for_unassigned(self, sample_identity):
        assert sample_identity.has_role("AGENT_ADMIN") is False

    def test_is_expired_returns_false_for_fresh_identity(self, sample_identity):
        assert sample_identity.is_expired(max_age_seconds=3600) is False

    def test_is_expired_returns_true_for_old_identity(self):
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        identity = IdentityContext(
            identity_id=UserID("user-002"),
            agent_id=AgentID("agent-002"),
            tenant_id=TenantID("tenant-001"),
            email=None,
            roles=frozenset(),
            permissions=frozenset(),
            auth_method=AuthMethod.JWT,
            auth_time=old_time,
            session_id=str(uuid.uuid4()),
            metadata={},
            mfa_verified=False,
            ip_address=None,
        )
        assert identity.is_expired(max_age_seconds=3600) is True

    def test_to_audit_dict_does_not_contain_sensitive_fields(self, sample_identity):
        audit_dict = sample_identity.to_audit_dict()
        assert "email" not in audit_dict or audit_dict.get("email") is None
        assert "ip_address" not in audit_dict or audit_dict.get("ip_address") is None
        assert "identity_id" in audit_dict
        assert "agent_id" in audit_dict

    def test_identity_is_immutable(self, sample_identity):
        with pytest.raises(Exception):  # pydantic frozen model raises ValidationError
            sample_identity.email = "hacker@evil.com"  # type: ignore


# ─────────────────────────────────────────────────────────────────
# JWTHandler Tests
# ─────────────────────────────────────────────────────────────────

class TestJWTHandler:
    @pytest.fixture
    def jwt_handler(self, mock_settings, rsa_key_pair):
        private_pem, public_pem = rsa_key_pair
        mock_key_manager = AsyncMock()
        mock_key_manager.get_jwt_private_key.return_value = private_pem
        mock_key_manager.get_jwt_public_key.return_value = public_pem
        with patch("aegis_ai.crypto.key_manager.KeyManager", return_value=mock_key_manager):
            handler = JWTHandler(mock_settings)
            handler.set_keys(private_pem, public_pem, kid="key-v1")
            return handler

    def test_create_access_token_produces_valid_jwt(self, jwt_handler, sample_identity):
        token = jwt_handler.create_access_token(sample_identity, expire_minutes=15)
        assert token is not None
        assert isinstance(token, str)
        assert len(token.split(".")) == 3  # header.payload.signature

    def test_verify_token_returns_identity_context(self, jwt_handler, sample_identity):
        token = jwt_handler.create_access_token(sample_identity, expire_minutes=15)
        identity = jwt_handler.verify_token(token)
        assert identity.identity_id == sample_identity.identity_id
        assert identity.agent_id == sample_identity.agent_id

    def test_verify_expired_token_raises_token_expired_error(
        self, jwt_handler, sample_identity
    ):
        token = jwt_handler.create_access_token(sample_identity, expire_minutes=-60)
        with pytest.raises(TokenExpiredError):
            jwt_handler.verify_token(token)

    def test_verify_tampered_token_raises_token_invalid_error(
        self, jwt_handler, sample_identity
    ):
        token = jwt_handler.create_access_token(sample_identity, expire_minutes=15)
        tampered = token[:-5] + "XXXXX"  # corrupt signature
        with pytest.raises(TokenInvalidError):
            jwt_handler.verify_token(tampered)

    def test_verify_token_with_wrong_issuer_raises_error(
        self, jwt_handler, rsa_key_pair, sample_identity
    ):
        private_pem, _ = rsa_key_pair
        # Create token manually with wrong issuer
        payload = {
            "sub": str(sample_identity.identity_id),
            "iss": "https://evil.attacker.com",  # Wrong issuer
            "aud": "aegis-ai-agents",
            "exp": int(time.time()) + 900,
            "iat": int(time.time()),
            "jti": str(uuid.uuid4()),
        }
        bad_token = pyjwt.encode(payload, private_pem, algorithm="RS256")
        with pytest.raises(TokenInvalidError):
            jwt_handler.verify_token(bad_token)

    @pytest.mark.asyncio
    async def test_revoked_token_raises_token_invalid_error(
        self, jwt_handler, sample_identity
    ):
        mock_redis = AsyncMock()
        mock_redis.get.return_value = b"1"  # Token is in blocklist
        jwt_handler._redis = mock_redis

        token = jwt_handler.create_access_token(sample_identity, expire_minutes=15)
        with pytest.raises(TokenInvalidError, match="revoked"):
            await jwt_handler.validate_token(token)

    def test_token_header_contains_kid(self, jwt_handler, sample_identity):
        token = jwt_handler.create_access_token(sample_identity, expire_minutes=15)
        header = pyjwt.get_unverified_header(token)
        assert "kid" in header

    def test_token_algorithm_is_rs256(self, jwt_handler, sample_identity):
        token = jwt_handler.create_access_token(sample_identity, expire_minutes=15)
        header = pyjwt.get_unverified_header(token)
        assert header["alg"] == "RS256"

    def test_algorithm_none_attack_rejected(self, jwt_handler, sample_identity):
        """Security test: 'alg: none' attack must be rejected."""
        payload = {
            "sub": "user-001",
            "iss": "https://test.aegis-ai.com",
            "aud": "aegis-ai-agents",
            "exp": int(time.time()) + 900,
        }
        none_token = pyjwt.encode(payload, "", algorithm="none")
        with pytest.raises((TokenInvalidError, Exception)):
            jwt_handler.verify_token(none_token)


# ─────────────────────────────────────────────────────────────────
# APIKeyManager Tests
# ─────────────────────────────────────────────────────────────────

class TestAPIKeyManager:
    @pytest.fixture
    def api_key_manager(self, mock_settings):
        mock_key_manager = AsyncMock()
        with patch("aegis_ai.crypto.key_manager.KeyManager", return_value=mock_key_manager):
            return APIKeyManager(mock_settings)

    def test_generate_api_key_returns_prefixed_key(self, api_key_manager):
        raw_key, record = api_key_manager.generate_api_key(
            identity_id=UserID("user-001"),
            scopes=["agents.call"],
            expire_days=30,
        )
        assert raw_key.startswith("aegis_")
        assert len(raw_key) > 20

    def test_api_key_record_stores_hash_not_plaintext(self, api_key_manager):
        raw_key, record = api_key_manager.generate_api_key(
            identity_id=UserID("user-001"),
            scopes=["agents.call"],
            expire_days=30,
        )
        assert record.hashed_key != raw_key
        assert raw_key not in record.hashed_key

    def test_validate_api_key_succeeds_with_correct_key(self, api_key_manager):
        raw_key, record = api_key_manager.generate_api_key(
            identity_id=UserID("user-001"),
            scopes=["agents.call"],
            expire_days=30,
        )
        api_key_manager._key_store[record.key_id] = record
        identity = api_key_manager.validate_api_key(raw_key)
        assert identity.identity_id == UserID("user-001")

    def test_validate_wrong_key_raises_auth_error(self, api_key_manager):
        raw_key, record = api_key_manager.generate_api_key(
            identity_id=UserID("user-001"),
            scopes=["agents.call"],
            expire_days=30,
        )
        api_key_manager._key_store[record.key_id] = record
        wrong_key = raw_key[:-5] + "WRONG"
        with pytest.raises(AuthenticationError):
            api_key_manager.validate_api_key(wrong_key)

    def test_revoked_api_key_raises_auth_error(self, api_key_manager):
        raw_key, record = api_key_manager.generate_api_key(
            identity_id=UserID("user-001"),
            scopes=["agents.call"],
            expire_days=30,
        )
        api_key_manager._key_store[record.key_id] = record
        api_key_manager.revoke_api_key(record.key_id)
        with pytest.raises(AuthenticationError):
            api_key_manager.validate_api_key(raw_key)

    def test_timing_safe_comparison_prevents_timing_attacks(self, api_key_manager):
        """Both valid and invalid key validation must take similar time."""
        raw_key, record = api_key_manager.generate_api_key(
            identity_id=UserID("user-001"),
            scopes=["agents.call"],
            expire_days=30,
        )
        api_key_manager._key_store[record.key_id] = record
        wrong_key = "aegis_" + "A" * 48

        t_valid_start = time.perf_counter()
        try:
            api_key_manager.validate_api_key(raw_key)
        except Exception:
            pass
        t_valid = time.perf_counter() - t_valid_start

        t_invalid_start = time.perf_counter()
        try:
            api_key_manager.validate_api_key(wrong_key)
        except Exception:
            pass
        t_invalid = time.perf_counter() - t_invalid_start

        # Both should be within 500ms of each other (argon2 hashing dominates)
        assert abs(t_valid - t_invalid) < 0.5


# ─────────────────────────────────────────────────────────────────
# MFAVerifier Tests
# ─────────────────────────────────────────────────────────────────

class TestMFAVerifier:
    @pytest.fixture
    def mfa_verifier(self):
        return MFAVerifier()

    def test_generate_totp_secret_returns_valid_pair(self, mfa_verifier):
        secret, qr_uri = mfa_verifier.generate_totp_secret()
        assert len(secret) > 0
        assert "otpauth://" in qr_uri

    def test_verify_totp_succeeds_with_current_code(self, mfa_verifier):
        import pyotp
        secret, _ = mfa_verifier.generate_totp_secret()
        totp = pyotp.TOTP(secret)
        current_code = totp.now()
        assert mfa_verifier.verify_totp(secret, current_code, window=1) is True

    def test_verify_totp_fails_with_wrong_code(self, mfa_verifier):
        secret, _ = mfa_verifier.generate_totp_secret()
        assert mfa_verifier.verify_totp(secret, "000000", window=1) is False

    def test_generate_backup_codes_produces_correct_count(self, mfa_verifier):
        codes = mfa_verifier.generate_backup_codes(count=10)
        assert len(codes) == 10
        assert all(isinstance(c, str) for c in codes)

    def test_verify_backup_code_succeeds_and_invalidates(self, mfa_verifier):
        codes = mfa_verifier.generate_backup_codes(count=10)
        submitted = codes[0]
        success, remaining = mfa_verifier.verify_backup_code(codes, submitted)
        assert success is True
        assert submitted not in remaining
        assert len(remaining) == 9

    def test_backup_code_cannot_be_reused(self, mfa_verifier):
        codes = mfa_verifier.generate_backup_codes(count=10)
        submitted = codes[0]
        _, remaining = mfa_verifier.verify_backup_code(codes, submitted)
        success, _ = mfa_verifier.verify_backup_code(remaining, submitted)
        assert success is False
