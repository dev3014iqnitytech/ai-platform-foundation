"""
aegis_ai.audit.composite_audit_logger
=======================================
Composite Audit Logger — fan-out to multiple sinks simultaneously.

Design Pattern: Composite (structural) + Template Method (behavioural)
  The composite logger treats a group of loggers as a single logger,
  allowing the pipeline to write to GCP + Splunk + stdout in one call.

Usage::

    logger = CompositeAuditLogger([
        GCPAuditLogger(settings),
        SplunkAuditLogger(settings),
        StdoutAuditLogger(settings),
    ])
    await logger.log(event)   # fans out to all three concurrently

OWASP: A09:2021 (Security Logging and Monitoring Failures)
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

import structlog

from aegis_ai.audit.audit_logger import AuditLogger
from aegis_ai.audit.audit_event import AuditEvent
from aegis_ai.settings import AegisSettings

log = structlog.get_logger(__name__)


class CompositeAuditLogger(AuditLogger):
    """
    Fan-out to multiple AuditLogger sinks concurrently.

    Failures in any individual sink are logged and swallowed — a broken
    Splunk forwarder should not block the primary GCP audit trail.

    Args:
        sinks:    List of AuditLogger implementations to write to.
        settings: SDK settings (used by the base class for signing key etc.).
    """

    def __init__(
        self,
        sinks: List[AuditLogger],
        settings: Optional[AegisSettings] = None,
    ) -> None:
        from aegis_ai.settings import get_settings
        super().__init__(settings or get_settings())
        self._sinks = sinks

    async def log(self, event: AuditEvent) -> str:
        """
        Write the event to all sinks concurrently.

        Returns the audit ID from the first sink that succeeds.
        """
        tasks = [asyncio.ensure_future(sink.log(event)) for sink in self._sinks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        audit_id: str = ""
        for sink, result in zip(self._sinks, results):
            if isinstance(result, Exception):
                log.error(
                    "composite_audit_sink_error",
                    sink=type(sink).__name__,
                    error=str(result),
                    event_id=event.event_id,
                )
            elif not audit_id:
                audit_id = result  # type: ignore[assignment]

        return audit_id or event.event_id

    def add_sink(self, sink: AuditLogger) -> None:
        """Dynamically add a new sink at runtime."""
        self._sinks.append(sink)
        log.info("composite_audit_sink_added", sink=type(sink).__name__)

    def remove_sink(self, sink_type: type) -> None:
        """Remove all sinks of a given type."""
        before = len(self._sinks)
        self._sinks = [s for s in self._sinks if not isinstance(s, sink_type)]
        log.info("composite_audit_sink_removed", sink_type=sink_type.__name__,
                 removed=before - len(self._sinks))
