"""
Unit tests for MCPClient — circuit breaker, retry, health check, and routing.
All HTTP calls are mocked with httpx.MockTransport so no real servers needed.
"""
from __future__ import annotations

import asyncio
import pytest
import httpx

from mcp.client import (
    MCPClient,
    MCPCircuitOpenError,
    MCPServerNotFoundError,
    MCPToolError,
    _CIRCUIT_THRESHOLD,
    _CIRCUIT_RECOVERY_SEC,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_client(responses: list[httpx.Response]) -> MCPClient:
    """Build an MCPClient whose HTTP transport returns the given responses in order."""
    call_index = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = call_index["i"]
        call_index["i"] += 1
        if idx < len(responses):
            return responses[idx]
        return httpx.Response(200, json={"result": {"ok": True}})

    client = MCPClient()
    client.register_server("test_server", {
        "transport": "http",
        "url": "http://test-server",
        "headers": {},
    })
    # Swap the persistent client for a mock-backed one
    client._http_clients["test_server"] = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test-server",
    )
    client._healthy["test_server"] = True  # bypass health check
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Server registration
# ─────────────────────────────────────────────────────────────────────────────

def test_register_server():
    client = MCPClient()
    client.register_server("my_server", {"transport": "http", "url": "http://x"})
    assert "my_server" in client._servers


def test_has_server_false_when_not_healthy():
    client = MCPClient()
    client.register_server("srv", {"transport": "http", "url": "http://x"})
    # registered but not yet health-checked → unhealthy
    assert client.has_server("srv") is False


def test_has_server_true_when_healthy():
    client = MCPClient()
    client.register_server("srv", {"transport": "http", "url": "http://x"})
    client._healthy["srv"] = True
    assert client.has_server("srv") is True


def test_has_server_unknown_returns_false():
    client = MCPClient()
    assert client.has_server("nonexistent") is False


# ─────────────────────────────────────────────────────────────────────────────
# call_tool — success path
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_tool_success():
    client = _make_client([
        httpx.Response(200, json={"result": {"id": 42, "name": "Test Plan"}}),
    ])
    result = await client.call_tool("test_server", "create_test_plan", {"name": "My Plan"})
    assert result["id"] == 42
    await client.close()


@pytest.mark.asyncio
async def test_call_tool_unknown_server_raises():
    client = MCPClient()
    with pytest.raises(MCPServerNotFoundError):
        await client.call_tool("ghost", "any_tool", {})


@pytest.mark.asyncio
async def test_call_tool_404_raises_mcp_error():
    client = _make_client([httpx.Response(404, json={"error": "not found"})])
    with pytest.raises(MCPToolError, match="not found"):
        await client.call_tool("test_server", "missing_tool", {})
    await client.close()


@pytest.mark.asyncio
async def test_call_tool_error_in_result_raises():
    client = _make_client([
        httpx.Response(200, json={"error": "ADO rate limit exceeded"}),
    ])
    with pytest.raises(MCPToolError, match="ADO rate limit exceeded"):
        await client.call_tool("test_server", "get_work_item", {"id": "1"})
    await client.close()


# ─────────────────────────────────────────────────────────────────────────────
# Circuit breaker
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_circuit_opens_after_threshold_failures():
    # Return HTTP 500s to drive failures (not network errors — those get retried)
    failures = [httpx.Response(500, json={}) for _ in range(_CIRCUIT_THRESHOLD + 2)]
    client = _make_client(failures)

    open_raised = False
    for _ in range(_CIRCUIT_THRESHOLD + 2):
        try:
            await client.call_tool("test_server", "any_tool", {})
        except MCPCircuitOpenError:
            open_raised = True
            break
        except MCPToolError:
            pass

    assert open_raised, "Circuit should have opened"
    assert client._circuits["test_server"].open is True
    await client.close()


@pytest.mark.asyncio
async def test_circuit_open_blocks_calls_immediately():
    client = MCPClient()
    client.register_server("srv", {"transport": "http", "url": "http://x"})
    client._healthy["srv"] = True
    circuit = client._circuits["srv"]
    circuit.open = True
    circuit.last_failure_time = asyncio.get_event_loop().time()

    with pytest.raises(MCPCircuitOpenError):
        await client.call_tool("srv", "any", {})


@pytest.mark.asyncio
async def test_circuit_recovers_after_timeout(monkeypatch):
    client = MCPClient()
    client.register_server("srv", {"transport": "http", "url": "http://x"})
    client._healthy["srv"] = True
    circuit = client._circuits["srv"]
    circuit.open = True
    # Simulate recovery timeout having elapsed
    circuit.last_failure_time = asyncio.get_event_loop().time() - (_CIRCUIT_RECOVERY_SEC + 1)

    # After check(), circuit should be closed
    circuit.check("srv")
    assert circuit.open is False


# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_healthy_marks_healthy_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    client = MCPClient()
    client.register_server("srv", {
        "transport": "http",
        "url": "http://srv",
        "headers": {},
    })
    client._http_clients["srv"] = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://srv",
    )
    result = await client.ensure_healthy("srv")
    assert result is True
    assert client._healthy["srv"] is True
    await client.close()


@pytest.mark.asyncio
async def test_ensure_healthy_marks_unhealthy_on_503():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "down"})

    client = MCPClient()
    client.register_server("srv", {
        "transport": "http",
        "url": "http://srv",
        "headers": {},
    })
    client._http_clients["srv"] = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://srv",
    )
    result = await client.ensure_healthy("srv")
    assert result is False
    assert client._healthy["srv"] is False
    await client.close()


@pytest.mark.asyncio
async def test_ensure_healthy_stdio_always_true():
    client = MCPClient()
    client.register_server("local", {"transport": "stdio", "command": ["echo"]})
    result = await client.ensure_healthy("local")
    assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# close
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_releases_all_connections():
    client = _make_client([httpx.Response(200, json={"result": {}})])
    # Force client creation
    await client._get_http_client("test_server")
    assert "test_server" in client._http_clients
    await client.close()
    assert client._http_clients == {}
