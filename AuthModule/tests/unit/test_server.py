"""
tests/unit/test_server.py
=========================
Unit tests for the FastAPI HTTP API server in aegis_ai.server.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

import aegis_ai.server as server_module
from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.exceptions import (
    AuthorizationError,
    GuardRailViolationError,
    RateLimitExceededError,
)
from aegis_ai.types import AuthMethod, Permission, TenantID, UserID


@pytest.fixture(autouse=True)
def setup_server_state(test_settings, jwt_handler):
    """Initialise server globals for testing without full startup."""
    server_module._jwt_handler = jwt_handler
    pipeline_mock = MagicMock()
    pipeline_mock.circuit_breaker_state = "CLOSED"
    pipeline_mock.authenticate_only = AsyncMock(
        return_value=IdentityContext(
            identity_id=UserID("test-user"),
            tenant_id=TenantID("test-tenant"),
            auth_method=AuthMethod.JWT,
            session_id=str(uuid.uuid4()),
            roles={"USER"},
            permissions=frozenset([Permission("agents.call")]),
        )
    )
    pipeline_mock.secure_agent_call = AsyncMock()
    server_module._pipeline = pipeline_mock
    server_module._startup_time = 100.0


@pytest.fixture
def client():
    return TestClient(server_module.app, raise_server_exceptions=False)


class TestHealthEndpoints:
    def test_liveness(self, client):
        resp = client.get("/health/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "alive"
        assert "uptime_seconds" in data

    def test_startup_probe(self, client):
        resp = client.get("/health/startup")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "started"


class TestAuthEndpoints:
    def test_auth_me_missing_header(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_auth_me_invalid_token(self, client):
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer invalid.token.value"})
        assert resp.status_code == 401

    def test_auth_me_success(self, client, mock_identity, jwt_handler):
        token = jwt_handler.create_access_token(mock_identity)
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["sub"] == str(mock_identity.identity_id)
        assert data["email"] == (mock_identity.email or "")
        assert data["tenant_id"] == str(mock_identity.tenant_id)

    def test_verify_token_endpoint(self, client, mock_identity, jwt_handler):
        token = jwt_handler.create_access_token(mock_identity)
        resp = client.get("/v1/auth/token/verify", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["identity_id"] == "test-user"
        assert data["tenant_id"] == "test-tenant"


class TestExceptionHandlers:
    def test_rate_limit_exception(self, client):
        server_module._pipeline.authenticate_only.side_effect = RateLimitExceededError(
            message="Rate limit exceeded",
            details={"retry_after_seconds": 30},
        )
        resp = client.get("/v1/auth/token/verify", headers={"Authorization": "Bearer some-token"})
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") == "30"
        assert resp.json()["error"] == "RATE_LIMIT_EXCEEDED"

    def test_guardrail_violation_exception(self, client):
        server_module._pipeline.authenticate_only.side_effect = GuardRailViolationError(
            message="Guardrail triggered",
            details={"guard": "injection"},
        )
        resp = client.get("/v1/auth/token/verify", headers={"Authorization": "Bearer some-token"})
        assert resp.status_code == 422
        assert resp.json()["error"] == "GUARDRAIL_VIOLATION"

    def test_authorization_exception(self, client):
        server_module._pipeline.authenticate_only.side_effect = AuthorizationError(
            message="Access denied",
            details={"permission": "agents.call"},
        )
        resp = client.get("/v1/auth/token/verify", headers={"Authorization": "Bearer some-token"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "AUTHZ_ERROR"
