"""
aegis_ai.observability.health_check
=====================================
Health Check module for platform stability.

Monitors connectivity to dependent services and infrastructure:
- Redis connection check (for sliding-window rate limits and token blocklist)
- GCP connectivity (IAM, Secret Manager, KMS APIs via Google SDK client check)
- LLM Provider endpoint reachability checks

OWASP: A09:2021-Security Logging and Monitoring Failures
"""

from __future__ import annotations

import asyncio
import socket
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

import structlog
from pydantic import BaseModel, ConfigDict

from aegis_ai.settings import AegisSettings

logger = structlog.get_logger(__name__)


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class ComponentHealth(BaseModel):
    """Represents the health of a specific system component."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: HealthStatus
    latency_ms: float
    error: Optional[str] = None
    last_checked: datetime


class SystemHealth(BaseModel):
    """Aggregate health of the entire system."""

    model_config = ConfigDict(frozen=True)

    overall_status: HealthStatus
    components: Dict[str, ComponentHealth]
    version: str
    timestamp: datetime


class HealthCheck:
    """
    Evaluates system readiness and liveness by testing dependencies.

    Performs active socket connect or client checks on key endpoints.
    """

    def __init__(self, settings: AegisSettings, version: str = "1.0.0") -> None:
        self._settings = settings
        self.version = version

    async def check_gcp_connectivity(self) -> ComponentHealth:
        """Checks connection to Google APIs (IAM/KMS/Secret Manager)."""
        start = time.monotonic()
        error_msg = None
        status = HealthStatus.HEALTHY

        if not self._settings.gcp.use_gcp:
            return ComponentHealth(
                name="gcp",
                status=HealthStatus.HEALTHY,
                latency_ms=0.0,
                error="GCP disabled in configuration (local mode)",
                last_checked=datetime.now(timezone.utc),
            )

        try:
            # Check DNS resolution / socket connectivity to googleapis.com
            await asyncio.to_thread(self._check_host_port, "oauth2.googleapis.com", 443)
        except Exception as exc:
            status = HealthStatus.UNHEALTHY
            error_msg = f"GCP OAuth2 endpoint unreachable: {exc}"

        latency = (time.monotonic() - start) * 1000
        return ComponentHealth(
            name="gcp",
            status=status,
            latency_ms=round(latency, 2),
            error=error_msg,
            last_checked=datetime.now(timezone.utc),
        )

    async def check_redis_connectivity(self) -> ComponentHealth:
        """Checks connection to Redis cache/store."""
        start = time.monotonic()
        error_msg = None
        status = HealthStatus.HEALTHY

        redis_url = self._settings.rate_limit.redis_url
        if not redis_url:
            return ComponentHealth(
                name="redis",
                status=HealthStatus.DEGRADED,
                latency_ms=0.0,
                error="Redis not configured; falling back to in-memory rates/blocklist",
                last_checked=datetime.now(timezone.utc),
            )

        try:
            # Attempt to parse connection details and do socket check
            # For simplicity: check host/port
            import urllib.parse
            parsed = urllib.parse.urlparse(redis_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 6379
            await asyncio.to_thread(self._check_host_port, host, port)
        except Exception as exc:
            status = HealthStatus.DEGRADED
            error_msg = f"Redis host unreachable: {exc}"

        latency = (time.monotonic() - start) * 1000
        return ComponentHealth(
            name="redis",
            status=status,
            latency_ms=round(latency, 2),
            error=error_msg,
            last_checked=datetime.now(timezone.utc),
        )

    async def check_secret_manager(self) -> ComponentHealth:
        """Checks connection to Google Secret Manager."""
        start = time.monotonic()
        error_msg = None
        status = HealthStatus.HEALTHY

        if not self._settings.gcp.use_gcp:
            return ComponentHealth(
                name="secret_manager",
                status=HealthStatus.HEALTHY,
                latency_ms=0.0,
                error="GCP disabled in configuration (local mode)",
                last_checked=datetime.now(timezone.utc),
            )

        try:
            await asyncio.to_thread(self._check_host_port, "secretmanager.googleapis.com", 443)
        except Exception as exc:
            status = HealthStatus.UNHEALTHY
            error_msg = f"Secret Manager unreachable: {exc}"

        latency = (time.monotonic() - start) * 1000
        return ComponentHealth(
            name="secret_manager",
            status=status,
            latency_ms=round(latency, 2),
            error=error_msg,
            last_checked=datetime.now(timezone.utc),
        )

    async def check_llm_provider(self, provider: str) -> ComponentHealth:
        """Checks connectivity with an LLM provider endpoint."""
        start = time.monotonic()
        error_msg = None
        status = HealthStatus.HEALTHY

        # Supported hosts
        hosts = {
            "openai": "api.openai.com",
            "anthropic": "api.anthropic.com",
            "google": "generativelanguage.googleapis.com",
        }

        host = hosts.get(provider.lower())
        if not host:
            return ComponentHealth(
                name=f"llm_{provider}",
                status=HealthStatus.UNHEALTHY,
                latency_ms=0.0,
                error=f"Unknown LLM provider: {provider}",
                last_checked=datetime.now(timezone.utc),
            )

        try:
            await asyncio.to_thread(self._check_host_port, host, 443)
        except Exception as exc:
            status = HealthStatus.UNHEALTHY
            error_msg = f"LLM provider host {host} unreachable: {exc}"

        latency = (time.monotonic() - start) * 1000
        return ComponentHealth(
            name=f"llm_{provider}",
            status=status,
            latency_ms=round(latency, 2),
            error=error_msg,
            last_checked=datetime.now(timezone.utc),
        )

    async def check_all(self) -> SystemHealth:
        """
        Runs all health checks concurrently and aggregates the result.

        Returns:
            The overall system health.
        """
        providers = self._settings.llm.approved_providers or ["openai", "anthropic", "google"]
        tasks = [
            self.check_gcp_connectivity(),
            self.check_redis_connectivity(),
            self.check_secret_manager(),
        ] + [self.check_llm_provider(p) for p in providers]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        components: Dict[str, ComponentHealth] = {}
        overall = HealthStatus.HEALTHY

        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                name = f"unknown_component_{idx}"
                comp = ComponentHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=0.0,
                    error=str(res),
                    last_checked=datetime.now(timezone.utc),
                )
                components[name] = comp
                overall = HealthStatus.UNHEALTHY
            else:
                components[res.name] = res
                if res.status == HealthStatus.UNHEALTHY:
                    overall = HealthStatus.UNHEALTHY
                elif res.status == HealthStatus.DEGRADED and overall == HealthStatus.HEALTHY:
                    overall = HealthStatus.DEGRADED

        return SystemHealth(
            overall_status=overall,
            components=components,
            version=self.version,
            timestamp=datetime.now(timezone.utc),
        )

    def is_ready(self) -> bool:
        """Synchronous readiness probe."""
        # Ready if settings are loaded
        return self._settings is not None

    def is_alive(self) -> bool:
        """Synchronous liveness probe."""
        # Always alive if Python interpreter is executing this logic
        return True

    @staticmethod
    def _check_host_port(host: str, port: int, timeout: float = 3.0) -> None:
        """Simple TCP socket check to test reachability."""
        with socket.create_connection((host, port), timeout=timeout):
            pass
