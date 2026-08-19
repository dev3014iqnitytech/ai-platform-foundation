"""
MCP Client — Production-grade Model Context Protocol client.

Features per server:
- Per-server circuit breaker (fail-fast on repeated failures)
- Shared httpx connection pool (avoids TCP handshake overhead per call)
- Tenacity retry with exponential backoff (transient errors only)
- Health check endpoint (/health) before first call
- MCP-first with transparent REST fallback in agents
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from structlog import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 30
_CIRCUIT_THRESHOLD = 5       # failures before opening
_CIRCUIT_RECOVERY_SEC = 60   # seconds before auto-recovery attempt


@dataclass
class _CircuitBreaker:
    """Per-server circuit breaker — shared state across all calls to that server."""
    failure_count: int = 0
    last_failure_time: float = 0.0
    open: bool = False

    def check(self, server: str) -> None:
        if self.open:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed > _CIRCUIT_RECOVERY_SEC:
                self.open = False
                self.failure_count = 0
                logger.info("mcp_circuit_recovered", server=server)
            else:
                raise MCPCircuitOpenError(
                    f"MCP server '{server}' circuit open. "
                    f"Retry in {int(_CIRCUIT_RECOVERY_SEC - elapsed)}s"
                )

    def record_success(self) -> None:
        self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self, server: str) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= _CIRCUIT_THRESHOLD:
            self.open = True
            logger.error("mcp_circuit_opened", server=server, failures=self.failure_count)


class MCPClient:
    """
    Production MCP client — routes tool calls to specialised MCP servers.

    Registered servers:
    - azure_devops : read/write ADO work items, test plans, test cases
    - knowledge_base: hybrid RAG search over the enterprise KB
    - sharepoint    : read SharePoint documents for ingestion
    - prompt_library: fetch versioned, centralised prompts
    """

    def __init__(self, server_configs: dict[str, dict] | None = None):
        self._servers: dict[str, dict] = server_configs or {}
        # One persistent httpx.AsyncClient per server — avoids per-call TCP overhead
        self._http_clients: dict[str, httpx.AsyncClient] = {}
        self._circuits: dict[str, _CircuitBreaker] = {}
        self._healthy: dict[str, bool] = {}

    def register_server(self, name: str, config: dict) -> None:
        self._servers[name] = config
        self._circuits[name] = _CircuitBreaker()
        self._healthy[name] = False  # verified on first call
        logger.info("mcp_server_registered", name=name, transport=config.get("transport"))

    def has_server(self, name: str) -> bool:
        """Returns True only if the server is registered AND healthy."""
        return name in self._servers and self._healthy.get(name, False)

    async def ensure_healthy(self, name: str) -> bool:
        """Ping /health on HTTP servers; always True for STDIO."""
        if name not in self._servers:
            return False
        config = self._servers[name]
        if config.get("transport") != "http":
            self._healthy[name] = True
            return True
        try:
            client = await self._get_http_client(name)
            resp = await client.get("/health", timeout=5.0)
            healthy = resp.status_code == 200
            self._healthy[name] = healthy
            if not healthy:
                logger.warning("mcp_server_unhealthy", name=name, status=resp.status_code)
            return healthy
        except Exception as e:
            self._healthy[name] = False
            logger.warning("mcp_health_check_failed", name=name, error=str(e))
            return False

    async def _get_http_client(self, server: str) -> httpx.AsyncClient:
        """Returns the persistent connection pool for a server, creating it if needed."""
        if server not in self._http_clients:
            config = self._servers[server]
            self._http_clients[server] = httpx.AsyncClient(
                base_url=config["url"],
                headers=config.get("headers", {}),
                timeout=httpx.Timeout(DEFAULT_TIMEOUT),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._http_clients[server]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
        reraise=True,
    )
    async def call_tool(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        timeout: int = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """
        Call a tool on the specified MCP server.
        Retries on transient network errors (3 attempts, exponential backoff).
        Raises MCPToolError on permanent failure or open circuit.
        """
        if server not in self._servers:
            raise MCPServerNotFoundError(f"MCP server '{server}' not registered")

        circuit = self._circuits[server]
        circuit.check(server)  # raises MCPCircuitOpenError if open

        logger.debug("mcp_tool_call", server=server, tool=tool, args_keys=list(args.keys()))

        try:
            result = await asyncio.wait_for(
                self._dispatch(server, tool, args),
                timeout=timeout,
            )
            circuit.record_success()
            logger.debug("mcp_tool_success", server=server, tool=tool)
            return result
        except asyncio.TimeoutError:
            circuit.record_failure(server)
            raise MCPToolError(f"Tool {server}/{tool} timed out after {timeout}s")
        except (MCPToolError, MCPCircuitOpenError):
            raise
        except Exception as e:
            circuit.record_failure(server)
            raise MCPToolError(f"Tool {server}/{tool} failed: {e}") from e

    async def _dispatch(
        self, server: str, tool: str, args: dict
    ) -> dict[str, Any]:
        config = self._servers[server]
        transport = config.get("transport", "http")
        if transport == "http":
            return await self._call_http(server, tool, args)
        if transport == "stdio":
            return await self._call_stdio(config["command"], tool, args)
        raise MCPToolError(f"Unknown transport: {transport}")

    async def _call_http(self, server: str, tool: str, args: dict) -> dict[str, Any]:
        """HTTP/SSE MCP transport — reuses the server's connection pool."""
        client = await self._get_http_client(server)
        response = await client.post(
            f"/tools/{tool}",
            json={"arguments": args},
        )
        if response.status_code == 404:
            raise MCPToolError(f"Tool '{tool}' not found on server '{server}'")
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise MCPToolError(f"Tool error from {server}/{tool}: {payload['error']}")
        return payload.get("result", payload)

    async def _call_stdio(
        self, command: list[str], tool: str, args: dict
    ) -> dict[str, Any]:
        """STDIO MCP transport — spawns a subprocess per call (dev/local tools)."""
        import json as _json
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        request = _json.dumps({"tool": tool, "arguments": args}).encode()
        stdout, stderr = await proc.communicate(input=request)
        if proc.returncode != 0:
            raise MCPToolError(f"STDIO tool failed: {stderr.decode()[:500]}")
        return _json.loads(stdout)

    async def close(self) -> None:
        """Close all persistent connection pools gracefully."""
        for client in self._http_clients.values():
            await client.aclose()
        self._http_clients.clear()


class MCPToolError(Exception):
    """Permanent tool-level failure."""


class MCPServerNotFoundError(MCPToolError):
    """Server name not registered."""


class MCPCircuitOpenError(MCPToolError):
    """Circuit breaker is open — server is considered down."""


async def build_mcp_client() -> MCPClient:
    """
    Async factory — builds and health-checks all configured MCP servers.
    Servers that fail health checks are registered but marked unhealthy;
    agents fall back to direct REST for unhealthy servers.
    """
    try:
        from app.core.config import settings
        client = MCPClient()

        ado_url = getattr(settings, "MCP_ADO_URL", None)
        if ado_url:
            import base64
            pat = settings.ADO_PAT.get_secret_value()
            encoded = base64.b64encode(f":{pat}".encode()).decode()
            client.register_server("azure_devops", {
                "transport": "http",
                "url": str(ado_url),
                "headers": {
                    "Authorization": f"Basic {encoded}",
                    "Content-Type": "application/json",
                    "X-MCP-Version": "1.0",
                },
            })
            await client.ensure_healthy("azure_devops")

        kb_url = getattr(settings, "MCP_KB_URL", None)
        if kb_url:
            client.register_server("knowledge_base", {
                "transport": "http",
                "url": str(kb_url),
                "headers": {
                    "Authorization": f"Bearer {settings.AZURE_OPENAI_API_KEY.get_secret_value()}",
                    "Content-Type": "application/json",
                    "X-MCP-Version": "1.0",
                },
            })
            await client.ensure_healthy("knowledge_base")

        sp_url = getattr(settings, "MCP_SHAREPOINT_URL", None)
        if sp_url:
            client.register_server("sharepoint", {
                "transport": "http",
                "url": str(sp_url),
                "headers": {
                    "Authorization": f"Bearer {settings.AZURE_CLIENT_SECRET.get_secret_value()}",
                    "Content-Type": "application/json",
                    "X-MCP-Version": "1.0",
                },
            })
            await client.ensure_healthy("sharepoint")

        registered = list(client._servers.keys())
        healthy = [s for s in registered if client._healthy.get(s)]
        logger.info("mcp_client_built", registered=registered, healthy=healthy)
        return client

    except Exception as e:
        logger.warning("mcp_client_build_failed", error=str(e), fallback="empty_client")
        return MCPClient()
