"""
tests/unit/test_client_app.py
=============================
Unit tests for the Client API (Agent A) application and token manager.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from client_app.config import ClientSettings, get_client_settings
from client_app.main import app
from client_app.token_manager import MockSSOTokenManager


@pytest.fixture
def client_settings():
    return ClientSettings(
        environment="development",
        aegis_gateway_url="http://localhost:8080",
        sso_issuer="https://mock-sso.local",
        sso_audience="aegis-ai-gateway",
        sso_subject="agent-a-client-id",
        sso_tenant_id="enterprise-tenant-01",
    )


@pytest.fixture
def token_manager(client_settings):
    return MockSSOTokenManager(client_settings)


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestClientConfig:
    def test_settings_initialization(self, client_settings):
        assert client_settings.is_development() is True
        assert client_settings.is_production() is False
        assert client_settings.invoke_url == "http://localhost:8080/v1/agent/invoke"


class TestTokenManager:
    @pytest.mark.asyncio
    async def test_get_token_and_decode(self, token_manager):
        token = await token_manager.get_token()
        assert isinstance(token, str)
        assert len(token) > 20

        # Decode without signature verification to inspect payload
        import jwt
        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["sub"] == "agent-a-client-id"
        assert payload["iss"] == "https://mock-sso.local"
        assert payload["aud"] == "aegis-ai-gateway"
        assert payload["tenant_id"] == "enterprise-tenant-01"
        assert "AGENT_CALLER" in payload["roles"]
        assert "agents.call" in payload["permissions"]

    @pytest.mark.asyncio
    async def test_token_caching(self, token_manager):
        token1 = await token_manager.get_token()
        token2 = await token_manager.get_token()
        assert token1 == token2


class TestClientAppEndpoints:
    def test_root_endpoint(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "service" in data
        assert data["service"] == "Client API (Agent A)"

    def test_health_live(self, client):
        resp = client.get("/health/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "alive"

    def test_health_ready(self, client):
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"

    def test_secure_analyze_unauthorized(self, client):
        resp = client.post(
            "/api/secure/analyze",
            json={"prompt": "Hello AI", "agent_id": "agent-b"},
        )
        assert resp.status_code == 401

    def test_secure_token_info_unauthorized(self, client):
        resp = client.get("/api/secure/token-info")
        assert resp.status_code == 401
