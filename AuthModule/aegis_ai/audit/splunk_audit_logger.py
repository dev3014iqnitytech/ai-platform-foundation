"""
aegis_ai.audit.splunk_audit_logger
=====================================
Splunk HEC (HTTP Event Collector) implementation of AuditLogger.

Design Pattern: Template Method (Concrete implementation)

Sends signed audit events to a Splunk HEC endpoint over TLS.
Batches events for efficiency; flushes on interval or when batch is full.

OWASP: A09:2021 (Security Logging and Monitoring Failures)
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import structlog

from aegis_ai.audit.audit_event import AuditEvent
from aegis_ai.audit.audit_logger import AuditLogger
from aegis_ai.settings import AegisSettings

log = structlog.get_logger(__name__)


class SplunkAuditLogger(AuditLogger):
    """
    Writes signed audit events to Splunk via HTTP Event Collector (HEC).

    Configuration is loaded from ``AegisSettings`` and process environment:
      - ``SPLUNK_HEC_URL``                   : Splunk HEC endpoint
      - ``SPLUNK_HEC_TOKEN_SECRET_NAME``      : Secret name for HEC token
      - ``SPLUNK_HEC_INDEX``                  : Splunk index (default: aegis_ai)
      - ``SPLUNK_HEC_SOURCE``                 : Source field (default: aegis-ai-sdk)
      - ``SPLUNK_HEC_SOURCETYPE``             : Sourcetype (default: _json)

    Args:
        settings:      SDK settings instance.
        hec_url:       Override Splunk HEC URL (reads env var if None).
        hec_token:     Override HEC token (reads Secret Manager if None).
        index:         Splunk index to write to.
        batch_size:    Events per HTTP batch.
        flush_interval: Seconds between flushes.
    """

    def __init__(
        self,
        settings: AegisSettings,
        *,
        hec_url: Optional[str] = None,
        hec_token: Optional[str] = None,
        index: str = "aegis_ai",
        source: str = "aegis-ai-sdk",
        sourcetype: str = "_json",
        batch_size: int = 100,
        flush_interval: float = 5.0,
    ) -> None:
        super().__init__(settings)
        import os
        self._hec_url = hec_url or os.getenv("SPLUNK_HEC_URL", "")
        self._hec_token = hec_token  # resolved lazily from Secret Manager
        self._hec_token_secret = os.getenv(
            "SPLUNK_HEC_TOKEN_SECRET_NAME", "aegis-splunk-hec-token"
        )
        self._index = index
        self._source = source
        self._sourcetype = sourcetype
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._buffer: List[Dict[str, Any]] = []
        self._last_flush = time.monotonic()
        self._lock = asyncio.Lock()

    async def _resolve_token(self) -> str:
        """Resolve HEC token from Secret Manager on first use."""
        if self._hec_token is None:
            try:
                from aegis_ai.secrets.gcp_repository import GCPSecretRepository
                repo = GCPSecretRepository(
                    project_id=self._settings.gcp.project_id,
                    prefix=self._settings.gcp.secret_manager_prefix,
                )
                self._hec_token = await repo.get_secret(self._hec_token_secret)
            except Exception as exc:
                log.error("splunk_token_resolution_failed", error=str(exc))
                raise
        return self._hec_token

    def _to_splunk_payload(self, event: AuditEvent) -> Dict[str, Any]:
        """Convert AuditEvent to a Splunk HEC event dict."""
        return {
            "time": event.timestamp.timestamp(),
            "host": "aegis-ai-sdk",
            "source": self._source,
            "sourcetype": self._sourcetype,
            "index": self._index,
            "event": {
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "identity_id": event.identity_id,
                "agent_id": event.agent_id,
                "tenant_id": event.tenant_id,
                "session_id": event.session_id,
                "action": event.action,
                "resource": event.resource,
                "outcome": event.outcome,
                "severity": event.severity,
                "details": event.details,
                "hmac_signature": getattr(event, "hmac_signature", ""),
            },
        }

    async def log(self, event: AuditEvent) -> str:
        """Buffer event and flush if batch is full or interval elapsed."""
        async with self._lock:
            self._buffer.append(self._to_splunk_payload(event))
            elapsed = time.monotonic() - self._last_flush
            should_flush = (
                len(self._buffer) >= self._batch_size
                or elapsed >= self._flush_interval
            )

        if should_flush:
            await self._flush()

        return event.event_id

    async def _flush(self) -> None:
        """Send buffered events to Splunk HEC."""
        async with self._lock:
            if not self._buffer:
                return
            batch = self._buffer.copy()
            self._buffer.clear()
            self._last_flush = time.monotonic()

        if not self._hec_url:
            log.warning("splunk_hec_url_not_configured", dropped=len(batch))
            return

        try:
            import httpx
            token = await self._resolve_token()
            payload = "\n".join(json.dumps(e) for e in batch)
            async with httpx.AsyncClient(
                verify=True,
                timeout=10.0,
            ) as client:
                resp = await client.post(
                    self._hec_url,
                    content=payload,
                    headers={
                        "Authorization": f"Splunk {token}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                log.debug("splunk_flush_success", count=len(batch))
        except Exception as exc:
            log.error("splunk_flush_failed", count=len(batch), error=str(exc))
            # Re-buffer on failure to avoid data loss (best-effort)
            async with self._lock:
                self._buffer = batch + self._buffer
