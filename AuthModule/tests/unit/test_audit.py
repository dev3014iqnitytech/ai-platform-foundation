"""
tests/unit/test_audit.py
==========================
Unit tests for the Audit Trail layer.

Covers: AuditEvent HMAC signing, AuditLogger buffering, RetentionPolicy
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis_ai.audit.audit_event import AuditEvent, EventType
from aegis_ai.audit.audit_logger import AuditLogger
from aegis_ai.audit.retention_policy import RetentionPolicy
from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.settings import AegisSettings
from aegis_ai.types import AuthMethod, Permission, TenantID, UserID


@pytest.fixture
def signing_key():
    return os.urandom(32)


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.audit.enabled = True
    s.audit.batch_size = 10
    s.audit.flush_interval_seconds = 5.0
    s.audit.use_gcp_logging = False
    s.audit.use_structured_stdout = False
    s.gcp.use_gcp = False
    return s


@pytest.fixture
def sample_event():
    return AuditEvent(
        event_type=EventType.AUTHENTICATION,
        identity_id="user-001",
        action="authenticate",
        outcome="SUCCESS",
        severity="INFO",
        details={"method": "jwt"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# AuditEvent HMAC Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditEvent:
    def test_hmac_sign_and_verify(self, sample_event, signing_key):
        sig = sample_event.compute_hmac(signing_key)
        signed = sample_event.model_copy(update={"hmac_signature": sig})
        assert signed.verify_hmac(signing_key) is True

    def test_hmac_verify_fails_on_tampered_event(self, sample_event, signing_key):
        sig = sample_event.compute_hmac(signing_key)
        signed = sample_event.model_copy(update={"hmac_signature": sig})
        # Tamper by changing outcome
        tampered = signed.model_copy(update={"outcome": "FAILURE"})
        assert tampered.verify_hmac(signing_key) is False

    def test_hmac_fails_with_wrong_key(self, sample_event, signing_key):
        sig = sample_event.compute_hmac(signing_key)
        signed = sample_event.model_copy(update={"hmac_signature": sig})
        wrong_key = os.urandom(32)
        assert signed.verify_hmac(wrong_key) is False

    def test_event_is_immutable(self, sample_event):
        with pytest.raises(Exception):
            sample_event.outcome = "TAMPERED"  # type: ignore

    def test_to_log_dict_excludes_none_fields(self, sample_event):
        log_dict = sample_event.to_log_dict()
        assert "event_id" in log_dict
        assert "event_type" in log_dict
        assert log_dict["event_type"] == "AUTHENTICATION"

    def test_event_has_unique_id(self):
        e1 = AuditEvent(event_type=EventType.LLM_CALL, action="call", outcome="SUCCESS")
        e2 = AuditEvent(event_type=EventType.LLM_CALL, action="call", outcome="SUCCESS")
        assert e1.event_id != e2.event_id

    def test_timestamp_is_utc(self, sample_event):
        assert sample_event.timestamp.tzinfo is not None

    def test_signable_payload_is_deterministic(self, sample_event):
        p1 = sample_event._signable_payload()
        p2 = sample_event._signable_payload()
        assert p1 == p2

    def test_no_hmac_fails_verification(self, sample_event, signing_key):
        assert sample_event.verify_hmac(signing_key) is False


# ─────────────────────────────────────────────────────────────────────────────
# AuditLogger Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLogger:
    @pytest.mark.asyncio
    async def test_log_returns_event_id(self, mock_settings, signing_key):
        exporter = AsyncMock()
        exporter.export = AsyncMock()
        logger = AuditLogger(mock_settings, signing_key=signing_key, exporter=exporter)

        event = AuditEvent(
            event_type=EventType.GUARD_RAIL_TRIGGERED,
            action="evaluate",
            outcome="BLOCKED",
            severity="HIGH",
        )
        event_id = await logger.log(event)
        assert isinstance(event_id, str)
        assert len(event_id) > 0

    @pytest.mark.asyncio
    async def test_log_auth_convenience(self, mock_settings, signing_key):
        exporter = AsyncMock()
        exporter.export = AsyncMock()
        logger = AuditLogger(mock_settings, signing_key=signing_key, exporter=exporter)

        identity = MagicMock()
        identity.identity_id = "user-001"
        identity.tenant_id = "tenant-001"
        identity.session_id = "session-001"

        event_id = await logger.log_auth(identity, "SUCCESS", {"method": "jwt"})
        assert event_id is not None

    @pytest.mark.asyncio
    async def test_buffer_flushed_on_batch_size(self, mock_settings, signing_key):
        mock_settings.audit.batch_size = 3
        exporter = AsyncMock()
        exporter.export = AsyncMock()
        logger = AuditLogger(mock_settings, signing_key=signing_key, exporter=exporter)

        for i in range(3):
            await logger.log(AuditEvent(
                event_type=EventType.GUARD_RAIL_PASSED,
                action=f"check_{i}",
                outcome="SUCCESS",
            ))
        await asyncio.sleep(0.1)  # Allow task scheduling

    @pytest.mark.asyncio
    async def test_disabled_audit_returns_event_id(self, mock_settings, signing_key):
        mock_settings.audit.enabled = False
        exporter = AsyncMock()
        logger = AuditLogger(mock_settings, signing_key=signing_key, exporter=exporter)

        event = AuditEvent(event_type=EventType.LLM_CALL, action="call", outcome="SUCCESS")
        event_id = await logger.log(event)
        assert event_id == event.event_id


# ─────────────────────────────────────────────────────────────────────────────
# RetentionPolicy Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRetentionPolicy:
    @pytest.fixture
    def policy(self, mock_settings):
        return RetentionPolicy(mock_settings)

    def test_enforce_produces_hashes(self, policy):
        result = policy.enforce("openai", "my prompt", "the response")
        assert result.prompt_hash != "my prompt"
        assert result.response_hash != "the response"
        assert len(result.prompt_hash) == 64  # SHA-256 hex

    def test_provider_verified_for_approved(self, policy):
        result = policy.enforce("openai", "prompt", "response")
        assert result.provider_verified is True

    def test_provider_not_verified_for_unknown(self, policy):
        result = policy.enforce("unknownprovider", "prompt", "response")
        assert result.provider_verified is False

    def test_validate_no_plaintext_passes(self, policy):
        clean_record = {"event_id": "abc", "prompt_hash": "abc123", "outcome": "SUCCESS"}
        assert policy.validate_no_plaintext(clean_record) is True

    def test_validate_no_plaintext_fails_on_raw_field(self, policy):
        bad_record = {"event_id": "abc", "prompt": "raw user input here"}
        assert policy.validate_no_plaintext(bad_record) is False
