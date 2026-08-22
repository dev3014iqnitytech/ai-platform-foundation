"""
aegis_ai.audit.siem_exporter
================================
SIEM export backend — GCP Cloud Logging + structured stdout.

Features:
- Async batch export with retry
- Dead-letter queue for failed events (in-memory buffer)
- GCP Cloud Logging as primary destination
- Structured stdout as secondary (always-on)

OWASP: A09:2021-Security Logging and Monitoring Failures
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from typing import Deque, List, Optional

import structlog

from aegis_ai.audit.audit_event import AuditEvent
from aegis_ai.settings import AegisSettings

logger = structlog.get_logger(__name__)

_MAX_DLQ_SIZE = 1000  # Max dead-letter queue size


class SIEMExporter:
    """
    Exports audit events to SIEM backends.

    Primary: GCP Cloud Logging
    Secondary: Structured stdout (always enabled)
    Dead-letter: In-memory deque for failed exports (up to 1000 events)
    """

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        self._gcp_logger: Optional[object] = None
        self._dlq: Deque[AuditEvent] = deque(maxlen=_MAX_DLQ_SIZE)
        self._retry_lock = asyncio.Lock()
        self._init_gcp_logger()

    def _init_gcp_logger(self) -> None:
        """Initialise GCP Cloud Logging client (best-effort)."""
        if not self._settings.audit.use_gcp_logging or not self._settings.gcp.use_gcp:
            return
        try:
            from google.cloud import logging as gcp_logging
            client = gcp_logging.Client(project=self._settings.gcp.project_id or None)
            self._gcp_logger = client.logger(self._settings.audit.log_name)
            logger.info("gcp_cloud_logging_initialized", log_name=self._settings.audit.log_name)
        except Exception as exc:
            logger.warning("gcp_cloud_logging_init_failed", error=str(exc))

    async def export(self, events: List[AuditEvent]) -> None:
        """
        Export a batch of audit events to all configured backends.

        Args:
            events: List of signed AuditEvent objects to export.
        """
        if not events:
            return

        # Always write to structured stdout
        if self._settings.audit.use_structured_stdout:
            for event in events:
                self._write_stdout(event)

        # Export to GCP Cloud Logging
        if self._gcp_logger is not None:
            await self._export_gcp(events)

    def _write_stdout(self, event: AuditEvent) -> None:
        """Emit event as structured JSON log line."""
        log_data = event.to_log_dict()
        structlog.get_logger("aegis.audit").info(
            "audit_event",
            **{k: v for k, v in log_data.items() if v is not None},
        )

    async def _export_gcp(self, events: List[AuditEvent]) -> None:
        """Export events to GCP Cloud Logging with retry."""
        try:
            await asyncio.to_thread(self._sync_export_gcp, events)
            # Attempt DLQ retry on successful export
            await self._retry_dlq()
        except Exception as exc:
            logger.error(
                "gcp_audit_export_failed",
                error=str(exc),
                event_count=len(events),
                dlq_size=len(self._dlq),
            )
            # Move to dead-letter queue
            for event in events:
                self._dlq.append(event)

    def _sync_export_gcp(self, events: List[AuditEvent]) -> None:
        """Synchronous GCP batch write (called via to_thread)."""
        for event in events:
            severity_map = {
                "INFO": "INFO",
                "MEDIUM": "WARNING",
                "HIGH": "ERROR",
                "CRITICAL": "CRITICAL",
            }
            severity = severity_map.get(event.severity, "INFO")
            self._gcp_logger.log_struct(  # type: ignore[union-attr]
                event.to_log_dict(),
                severity=severity,
                resource={"type": "global"},
            )

    async def _retry_dlq(self) -> None:
        """Try to flush the dead-letter queue."""
        if not self._dlq:
            return
        async with self._retry_lock:
            batch = list(self._dlq)
            self._dlq.clear()
        try:
            await asyncio.to_thread(self._sync_export_gcp, batch)
            logger.info("audit_dlq_flushed", count=len(batch))
        except Exception as exc:
            logger.error("audit_dlq_retry_failed", error=str(exc))
            for event in batch:
                self._dlq.append(event)

    def get_dlq_size(self) -> int:
        """Return number of events pending in the dead-letter queue."""
        return len(self._dlq)
