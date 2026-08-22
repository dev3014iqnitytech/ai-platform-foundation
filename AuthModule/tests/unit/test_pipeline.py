"""
tests/unit/test_pipeline.py
===========================
End-to-end unit tests for the SecurityPipeline facade.

Covers:
- PipelineConfig setup and validation
- Happy path: Authentication -> Authorization -> Rate Limiting -> GuardRails -> Masking -> LLM call -> Response Validation -> Audit
- Auth / AuthZ failure handling
- GuardRail block and auto-remediation handling
- Response validation violation handling
- Rate limiter block handling
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.exceptions import (
    AuthenticationError,
    AuthorizationError,
    GuardRailViolationError,
    RateLimitExceededError,
)
from aegis_ai.pipeline import PipelineConfig, SecurityPipeline
from aegis_ai.proxy.llm_gateway import LLMMessage, LLMRequest, LLMResponse, TokenUsage
from aegis_ai.settings import AegisSettings
from aegis_ai.types import (
    AuthMethod,
    GuardRailAction,
    GuardRailResult,
    MaskingResult,
    Permission,
    PipelineResult,
    RateLimitResult,
    TenantID,
    UserID,
)


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.pipeline.enable_pii_masking = True
    s.pipeline.enable_prompt_injection_detection = True
    s.pipeline.block_toxic_content = True
    s.pipeline.enable_mfa_enforcement = False
    s.rate_limit.enabled = True
    return s


@pytest.fixture
def mock_identity():
    return IdentityContext(
        identity_id=UserID("test-user-001"),
        tenant_id=TenantID("test-tenant"),
        auth_method=AuthMethod.JWT,
        session_id="test-session-uuid",
        mfa_verified=True,
        permissions=frozenset([Permission("agents.call")]),
    )


@pytest.fixture
def sample_request():
    return LLMRequest(
        provider="openai",
        model="gpt-4o",
        messages=[LLMMessage(role="user", content="Tell me a story about a secure API")],
    )


@pytest.fixture
def mock_config(mock_settings):
    cfg = MagicMock(spec=PipelineConfig)
    cfg.settings = mock_settings

    # Mocks for components
    cfg.defender = AsyncMock()
    cfg.injection_detector = AsyncMock()
    cfg.toxicity_detector = AsyncMock()
    cfg.pii_detector = AsyncMock()
    cfg.grounder = AsyncMock()
    cfg.guard_rails = [cfg.defender, cfg.injection_detector, cfg.toxicity_detector, cfg.pii_detector, cfg.grounder]

    clean_res = GuardRailResult(name="pii", passed=True, score=0.0, action=GuardRailAction.PASS)
    cfg.pii_detector.check.return_value = clean_res

    cfg.auth_provider = AsyncMock()
    cfg.iam_client = AsyncMock()
    cfg.policy_engine = AsyncMock()
    cfg.rbac_engine = MagicMock()
    cfg.least_privilege = MagicMock()
    cfg.rate_limiter = AsyncMock()
    cfg.masker = MagicMock()
    cfg.data_masker = cfg.masker
    cfg.tls_enforcer = MagicMock()
    cfg.gateway = AsyncMock()
    cfg.llm_gateway = cfg.gateway
    cfg.zero_retention_policy = MagicMock()
    cfg.response_validator = AsyncMock()
    cfg.retention_policy = MagicMock()
    cfg.audit_logger = AsyncMock()
    cfg.audit_logger.log.return_value = "mock-audit-event-id-001"
    cfg.audit_logger.log_security_alert.return_value = None
    cfg.audit_logger.log_guard_rail.return_value = None
    cfg.metrics = MagicMock()
    cfg.tracer = MagicMock()
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# E2E Pipeline Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurityPipeline:

    @pytest.mark.asyncio
    async def test_pipeline_happy_path(self, mock_config, mock_identity, sample_request):
        # 1. Setup mock responses
        mock_config.auth_provider.validate_token.return_value = mock_identity
        mock_config.policy_engine.evaluate.return_value = MagicMock(allowed=True)
        mock_config.rate_limiter.check_rate_limit.return_value = RateLimitResult(
            allowed=True, remaining=99, reset_at=datetime.now(timezone.utc)
        )

        # Guardrails return passed
        clean_res = GuardRailResult(name="guard", passed=True, score=0.0, action=GuardRailAction.PASS)
        mock_config.defender.check.return_value = clean_res
        mock_config.injection_detector.check.return_value = clean_res
        mock_config.toxicity_detector.check.return_value = clean_res
        mock_config.pii_detector.analyze.return_value = []
        mock_config.grounder.check.return_value = clean_res

        # Masking returns original text
        mock_config.masker.mask.return_value = MaskingResult(
            original_hash="hash",
            masked_text=sample_request.messages[0].content,
            entity_count=0,
            masking_map={},
            entities_found=[],
        )

        # Gateway returns completion
        mock_response = LLMResponse(
            content="Once upon a time, there was a secure token...",
            model="gpt-4o",
            provider="openai",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=15, total_tokens=25),
            request_id="req-123",
            latency_ms=120.0,
        )
        mock_config.gateway.call.return_value = mock_response

        # Response validator allows — filtered_content=None forces fallback to response.content
        mock_config.response_validator.validate.return_value = MagicMock(
            is_safe=True, filtered_content=None, issues=[]
        )

        # 2. Run pipeline
        pipeline = SecurityPipeline(mock_config)
        result = await pipeline.secure_agent_call(
            token="bearer-token-123",
            request=sample_request,
            required_permissions=["agents.call"],
        )

        # 3. Assertions
        assert isinstance(result, PipelineResult)
        assert result.allowed is True
        assert result.response == mock_response.content
        assert mock_config.gateway.call.called is True
        assert mock_config.audit_logger.log.called is True

    @pytest.mark.asyncio
    async def test_pipeline_auth_failure(self, mock_config, sample_request):
        mock_config.auth_provider.validate_token.side_effect = AuthenticationError("Invalid signature")

        pipeline = SecurityPipeline(mock_config)
        with pytest.raises(AuthenticationError):
            await pipeline.secure_agent_call(
                token="bad-token",
                request=sample_request,
                required_permissions=["agents.call"],
            )

    @pytest.mark.asyncio
    async def test_pipeline_authz_policy_failure(self, mock_config, mock_identity, sample_request):
        mock_config.auth_provider.validate_token.return_value = mock_identity
        # Policy evaluation denies
        mock_config.policy_engine.evaluate.return_value = MagicMock(allowed=False, reason="MFA required")

        pipeline = SecurityPipeline(mock_config)
        with pytest.raises(AuthorizationError):
            await pipeline.secure_agent_call(
                token="token",
                request=sample_request,
                required_permissions=["agents.call"],
            )

    @pytest.mark.asyncio
    async def test_pipeline_rate_limit_failure(self, mock_config, mock_identity, sample_request):
        mock_config.auth_provider.validate_token.return_value = mock_identity
        mock_config.policy_engine.evaluate.return_value = MagicMock(allowed=True)
        # Rate limit hit
        mock_config.rate_limiter.check_rate_limit.return_value = RateLimitResult(
            allowed=False, remaining=0, reset_at=datetime.now(timezone.utc), retry_after_seconds=10
        )

        pipeline = SecurityPipeline(mock_config)
        with pytest.raises(RateLimitExceededError):
            await pipeline.secure_agent_call(
                token="token",
                request=sample_request,
                required_permissions=["agents.call"],
            )

    @pytest.mark.asyncio
    async def test_pipeline_guardrail_block_injection(self, mock_config, mock_identity, sample_request):
        mock_config.auth_provider.validate_token.return_value = mock_identity
        mock_config.policy_engine.evaluate.return_value = MagicMock(allowed=True)
        mock_config.rate_limiter.check_rate_limit.return_value = RateLimitResult(
            allowed=True, remaining=10, reset_at=datetime.now(timezone.utc)
        )

        # Mock injection check to block and have no remediation
        blocked_res = GuardRailResult(
            name="InjectionDetector", passed=False, score=1.0, action=GuardRailAction.BLOCK
        )
        mock_config.defender.check.return_value = GuardRailResult(name="Defender", passed=True)
        mock_config.injection_detector.check.return_value = blocked_res
        mock_config.injection_detector.can_auto_remediate = False

        pipeline = SecurityPipeline(mock_config)
        with pytest.raises(GuardRailViolationError):
            await pipeline.secure_agent_call(
                token="token",
                request=sample_request,
                required_permissions=["agents.call"],
            )

    @pytest.mark.asyncio
    async def test_pipeline_response_validator_failure(self, mock_config, mock_identity, sample_request):
        mock_config.auth_provider.validate_token.return_value = mock_identity
        mock_config.policy_engine.evaluate.return_value = MagicMock(allowed=True)
        mock_config.rate_limiter.check_rate_limit.return_value = RateLimitResult(
            allowed=True, remaining=10, reset_at=datetime.now(timezone.utc)
        )

        # Passed guardrails
        clean_res = GuardRailResult(name="g", passed=True)
        mock_config.defender.check.return_value = clean_res
        mock_config.injection_detector.check.return_value = clean_res
        mock_config.toxicity_detector.check.return_value = clean_res
        mock_config.pii_detector.analyze.return_value = []
        mock_config.grounder.check.return_value = clean_res

        mock_config.masker.mask.return_value = MaskingResult(
            original_hash="hash", masked_text="clean text", entity_count=0, masking_map={}, entities_found=[]
        )

        mock_response = LLMResponse(
            content="unsafe response", model="m", provider="p", usage=TokenUsage(), request_id="id", latency_ms=1.0
        )
        mock_config.gateway.call.return_value = mock_response

        # Response validation returns unsafe
        mock_config.response_validator.validate.return_value = MagicMock(is_safe=False, issues=["toxic_response"])

        pipeline = SecurityPipeline(mock_config)
        with pytest.raises(GuardRailViolationError):
            await pipeline.secure_agent_call(
                token="token",
                request=sample_request,
                required_permissions=["agents.call"],
            )
