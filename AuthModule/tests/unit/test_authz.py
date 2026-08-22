"""
tests/unit/test_authz.py
==========================
Unit tests for the authorization layer.

Covers: IAMClient, RBACEngine, PolicyEngine, LeastPrivilegeEnforcer
OWASP: A01:2021-Broken Access Control, LLM08-Excessive Agency
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.authz.iam_client import IAMClient
from aegis_ai.authz.rbac_engine import RBACEngine, ROLE_PERMISSIONS
from aegis_ai.authz.policy_engine import PolicyEngine, PolicyDecisionResult
from aegis_ai.authz.least_privilege import LeastPrivilegeEnforcer
from aegis_ai.settings import AegisSettings
from aegis_ai.types import AgentID, AuthMethod, Permission, ResourcePath, TenantID, UserID


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.gcp.use_gcp = False
    settings.pipeline.environment = "development"
    settings.guardrails.max_prompt_length_chars = 32000
    return settings


@pytest.fixture
def admin_identity():
    return IdentityContext(
        identity_id=UserID("admin-001"),
        agent_id=AgentID("agent-001"),
        tenant_id=TenantID("tenant-001"),
        email=None,
        auth_method=AuthMethod.JWT,
        auth_time=datetime.now(timezone.utc),
        session_id="session-admin-001",
        mfa_verified=True,
        roles=frozenset(["AGENT_ADMIN"]),
        permissions=frozenset([
            Permission("agents.delete"),
            Permission("keys.rotate"),
            Permission("agents.call"),
        ]),
    )


@pytest.fixture
def reader_identity():
    return IdentityContext(
        identity_id=UserID("reader-001"),
        agent_id=AgentID("agent-002"),
        tenant_id=TenantID("tenant-001"),
        email=None,
        auth_method=AuthMethod.JWT,
        auth_time=datetime.now(timezone.utc),
        session_id="session-reader-001",
        mfa_verified=False,
        roles=frozenset(["AGENT_READER"]),
        permissions=frozenset([Permission("agents.call"), Permission("agents.read")]),
    )


# ─────────────────────────────────────────────────────────────────────────────
# RBAC Engine Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRBACEngine:
    @pytest.fixture
    def rbac(self, mock_settings):
        return RBACEngine(mock_settings)

    def test_admin_has_all_permissions(self, rbac):
        perms = rbac.resolve_permissions(frozenset(["AGENT_ADMIN"]))
        assert Permission("agents.delete") in perms
        assert Permission("keys.rotate") in perms
        assert Permission("iam.bindings.write") in perms

    def test_reader_has_limited_permissions(self, rbac):
        perms = rbac.resolve_permissions(frozenset(["AGENT_READER"]))
        assert Permission("agents.call") in perms
        assert Permission("agents.read") in perms
        assert Permission("agents.delete") not in perms
        assert Permission("keys.rotate") not in perms

    def test_viewer_cannot_call_agents(self, rbac):
        perms = rbac.resolve_permissions(frozenset(["AGENT_VIEWER"]))
        assert Permission("agents.call") not in perms
        assert Permission("agents.read") in perms

    def test_multiple_roles_union(self, rbac):
        perms = rbac.resolve_permissions(frozenset(["AGENT_READER", "AGENT_OPERATOR"]))
        # Should have all operator permissions
        assert Permission("agents.create") in perms

    def test_has_permission_explicit(self, rbac, admin_identity):
        assert rbac.has_permission(
            admin_identity, ResourcePath("projects/test"), Permission("agents.delete")
        ) is True

    def test_has_permission_role_expanded(self, rbac, reader_identity):
        # Reader doesn't have explicit 'agents.list' but role expands it
        assert rbac.has_permission(
            reader_identity, ResourcePath("projects/test"), Permission("agents.list")
        ) is True

    def test_unknown_role_is_ignored(self, rbac):
        perms = rbac.resolve_permissions(frozenset(["NONEXISTENT_ROLE"]))
        assert len(perms) == 0

    def test_custom_role_registration(self, rbac):
        rbac.register_custom_role("CUSTOM_ROLE", frozenset({"custom.action"}))
        perms = rbac.resolve_permissions(frozenset(["CUSTOM_ROLE"]))
        assert Permission("custom.action") in perms


# ─────────────────────────────────────────────────────────────────────────────
# Policy Engine Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyEngine:
    @pytest.fixture
    def policy_engine(self, mock_settings):
        mock_settings.pipeline.environment = "production"
        return PolicyEngine(mock_settings)

    @pytest.mark.asyncio
    async def test_admin_with_mfa_passes_all_rules(self, policy_engine, admin_identity):
        result = await policy_engine.evaluate(
            identity=admin_identity,
            action="agents.delete",
            resource=ResourcePath("projects/test"),
            context={},
        )
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_high_privilege_action_requires_mfa(self, policy_engine, reader_identity):
        result = await policy_engine.evaluate(
            identity=reader_identity,
            action="agents.delete",  # MFA-required action
            resource=ResourcePath("projects/test"),
            context={},
        )
        assert result.allowed is False
        assert "MFA" in result.reason

    @pytest.mark.asyncio
    async def test_expired_session_is_denied(self, policy_engine):
        old_identity = IdentityContext(
            identity_id=UserID("user-old"),
            auth_method=AuthMethod.JWT,
            auth_time=datetime.now(timezone.utc) - timedelta(hours=2),
            session_id="old-session",
            mfa_verified=True,
            roles=frozenset(["AGENT_ADMIN"]),
            permissions=frozenset([Permission("agents.call")]),
        )
        result = await policy_engine.evaluate(
            identity=old_identity,
            action="agents.call",
            resource=ResourcePath("projects/test"),
            context={},
        )
        assert result.allowed is False
        assert "session" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_ip_allowlist_blocks_unknown_ip(self, policy_engine, reader_identity):
        ip_identity = reader_identity.model_copy(update={"ip_address": "192.168.50.1"})
        result = await policy_engine.evaluate(
            identity=ip_identity,
            action="agents.call",
            resource=ResourcePath("projects/test"),
            context={"allowed_cidrs": ["10.0.0.0/8"]},
        )
        assert result.allowed is False
        assert "IP" in result.reason

    @pytest.mark.asyncio
    async def test_ip_allowlist_allows_known_ip(self, policy_engine, reader_identity):
        ip_identity = reader_identity.model_copy(update={"ip_address": "10.0.1.100"})
        result = await policy_engine.evaluate(
            identity=ip_identity,
            action="agents.call",
            resource=ResourcePath("projects/test"),
            context={"allowed_cidrs": ["10.0.0.0/8"]},
        )
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_critical_resource_requires_admin(self, policy_engine, reader_identity):
        result = await policy_engine.evaluate(
            identity=reader_identity,
            action="agents.call",
            resource=ResourcePath("projects/test"),
            context={"resource_sensitivity": "critical"},
        )
        assert result.allowed is False


# ─────────────────────────────────────────────────────────────────────────────
# IAM Client Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIAMClient:
    @pytest.fixture
    def iam_client(self, mock_settings):
        return IAMClient(mock_settings)

    @pytest.mark.asyncio
    async def test_check_permission_in_identity_perms(self, iam_client, admin_identity):
        result = await iam_client.check_permission(
            identity=admin_identity,
            resource=ResourcePath("projects/test"),
            permission=Permission("agents.call"),
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_check_permission_not_held(self, iam_client, reader_identity):
        result = await iam_client.check_permission(
            identity=reader_identity,
            resource=ResourcePath("projects/test"),
            permission=Permission("keys.rotate"),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_cache_invalidation(self, iam_client, admin_identity):
        await iam_client.check_permission(
            admin_identity, ResourcePath("projects/test"), Permission("agents.call")
        )
        await iam_client.invalidate_cache(str(admin_identity.identity_id))
        # Should not raise
        assert True


# ─────────────────────────────────────────────────────────────────────────────
# Least Privilege Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestLeastPrivilegeEnforcer:
    @pytest.fixture
    def enforcer(self, mock_settings):
        return LeastPrivilegeEnforcer(mock_settings)

    def test_reduce_to_scope_removes_excess(self, enforcer, admin_identity):
        scoped = enforcer.reduce_to_scope(
            admin_identity, required_permissions=["agents.call"]
        )
        assert Permission("agents.call") in scoped.permissions
        assert Permission("agents.delete") not in scoped.permissions
        assert Permission("keys.rotate") not in scoped.permissions

    def test_reduce_to_scope_with_missing_permission(self, enforcer, reader_identity):
        # Reader doesn't have keys.rotate — intersection should be empty for that perm
        scoped = enforcer.reduce_to_scope(
            reader_identity, required_permissions=["agents.call", "keys.rotate"]
        )
        assert Permission("agents.call") in scoped.permissions
        assert Permission("keys.rotate") not in scoped.permissions

    def test_validate_scope_passes_when_all_held(self, enforcer, admin_identity):
        assert enforcer.validate_scope(admin_identity, ["agents.call"]) is True

    def test_validate_scope_fails_on_missing_permission(self, enforcer, reader_identity):
        assert enforcer.validate_scope(reader_identity, ["keys.rotate"]) is False

    def test_audit_excess_permissions(self, enforcer, admin_identity):
        excess = enforcer.audit_excess_permissions(admin_identity, ["agents.call"])
        assert Permission("agents.delete") in excess
        assert Permission("keys.rotate") in excess

    def test_empty_required_permissions_raises(self, enforcer, admin_identity):
        with pytest.raises(ValueError):
            enforcer.reduce_to_scope(admin_identity, required_permissions=[])
