"""
aegis_ai.audit.audit_logger
==============================
Central audit logger — signs, buffers, and exports all security events.

Features:
- HMAC-SHA256 signing of every event before export
- Async buffer with configurable batch size and flush interval
- Background flush task (start()/stop() lifecycle)
- Dead-letter failover via SIEMExporter

OWASP: A09:2021-Security Logging and Monitoring Failures
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog

from aegis_ai.audit.audit_event import AuditEvent, EventType
from aegis_ai.audit.siem_exporter import SIEMExporter
from aegis_ai.settings import AegisSettings

logger = structlog.get_logger(__name__)


class AuditLogger:
    """
    Central audit logger for all Aegis AI security events.

    Usage::

        audit = AuditLogger(settings, signing_key=b"...", exporter=exporter)
        audit.start()  # Begin background flush task

        audit_id = await audit.log_auth(identity, "SUCCESS", {})

        await audit.stop()  # Flush remaining events on shutdown
    """

    def __init__(
        self,
        settings: AegisSettings,
        signing_key: Optional[bytes] = None,
        exporter: Optional[SIEMExporter] = None,
    ) -> None:
        self._settings = settings
        self._exporter = exporter or SIEMExporter(settings)
        self._buffer: List[AuditEvent] = []
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None

        if signing_key is not None:
            self._signing_key = signing_key
        else:
            import secrets as _secrets
            self._signing_key = _secrets.token_bytes(32)
            logger.warning(
                "audit_signing_key_ephemeral",
                note=(
                    "No signing key provided — using a random ephemeral key. "
                    "Audit signatures will NOT be verifiable across restarts. "
                    "Set signing_key from KeyManager in production."
                ),
            )

    # ─────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background buffer flush task."""
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_loop())
            logger.info("audit_logger_started")

    async def stop(self) -> None:
        """Cancel the flush task and flush remaining buffered events."""
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._flush_buffer()
        logger.info("audit_logger_stopped")

    async def _flush_loop(self) -> None:
        """Periodic buffer flush loop."""
        interval = self._settings.audit.flush_interval_seconds
        while True:
            await asyncio.sleep(interval)
            await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Flush all buffered events to the exporter."""
        async with self._lock:
            if not self._buffer:
                return
            to_export = self._buffer[:]
            self._buffer.clear()
        try:
            await self._exporter.export(to_export)
        except Exception as exc:
            logger.error("audit_flush_error", error=str(exc), count=len(to_export))

    # ─────────────────────────────────────────────────────────────────
    # Core Log Method
    # ─────────────────────────────────────────────────────────────────

    async def log(self, event: AuditEvent) -> str:
        """
        Sign and buffer an audit event.

        Args:
            event: Unsigned AuditEvent.

        Returns:
            The event_id of the signed event.
        """
        if not self._settings.audit.enabled:
            return event.event_id

        # Sign the event
        signature = event.compute_hmac(self._signing_key)
        signed = event.model_copy(update={"hmac_signature": signature})

        structlog.get_logger("aegis.audit").info(
            "audit_event",
            event_id=signed.event_id,
            event_type=signed.event_type.value,
            action=signed.action,
            outcome=signed.outcome,
            severity=signed.severity,
        )

        async with self._lock:
            self._buffer.append(signed)
            if len(self._buffer) >= self._settings.audit.batch_size:
                asyncio.create_task(self._flush_buffer())

        return signed.event_id

    # ─────────────────────────────────────────────────────────────────
    # Convenience Helpers
    # ─────────────────────────────────────────────────────────────────

    async def log_auth(
        self, identity: Any, outcome: str, details: Dict[str, Any]
    ) -> str:
        event = AuditEvent(
            event_type=EventType.AUTHENTICATION,
            identity_id=getattr(identity, "identity_id", None) if identity else None,
            tenant_id=getattr(identity, "tenant_id", None) if identity else None,
            session_id=getattr(identity, "session_id", None) if identity else None,
            action="authenticate",
            outcome=outcome,
            severity="INFO" if outcome == "SUCCESS" else "HIGH",
            details=details,
        )
        return await self.log(event)

    async def log_llm_call(
        self,
        identity: Any,
        provider: str,
        model: str,
        prompt_hash: str,
        response_hash: str,
        latency_ms: float,
    ) -> str:
        event = AuditEvent(
            event_type=EventType.LLM_CALL,
            identity_id=getattr(identity, "identity_id", None),
            agent_id=getattr(identity, "agent_id", None),
            tenant_id=getattr(identity, "tenant_id", None),
            session_id=getattr(identity, "session_id", None),
            action="invoke_llm",
            resource=f"{provider}/{model}",
            outcome="SUCCESS",
            severity="INFO",
            prompt_hash=prompt_hash,
            response_hash=response_hash,
            details={"latency_ms": round(latency_ms, 2)},
        )
        return await self.log(event)

    async def log_guard_rail(
        self, identity: Any, guard_name: str, result: Any
    ) -> str:
        passed = getattr(result, "passed", False)
        event = AuditEvent(
            event_type=EventType.GUARD_RAIL_PASSED if passed else EventType.GUARD_RAIL_TRIGGERED,
            identity_id=getattr(identity, "identity_id", None),
            agent_id=getattr(identity, "agent_id", None),
            action="evaluate_guard_rail",
            resource=guard_name,
            outcome="SUCCESS" if passed else "BLOCKED",
            severity="INFO" if passed else "HIGH",
            details={
                "score": getattr(result, "score", 0.0),
                "owasp_ref": getattr(result, "owasp_ref", ""),
            },
        )
        return await self.log(event)

    async def log_policy_decision(
        self, identity: Any, action: str, resource: str, decision: bool
    ) -> str:
        event = AuditEvent(
            event_type=EventType.POLICY_EVALUATED,
            identity_id=getattr(identity, "identity_id", None),
            action=action,
            resource=resource,
            outcome="SUCCESS" if decision else "BLOCKED",
            severity="INFO" if decision else "MEDIUM",
        )
        return await self.log(event)

    async def log_security_alert(
        self, severity: str, message: str, details: Dict[str, Any]
    ) -> str:
        event = AuditEvent(
            event_type=EventType.SECURITY_ALERT,
            action="security_alert",
            outcome="FAILURE",
            severity=severity,
            details={"message": message, **details},
        )
        return await self.log(event)

    async def log_rate_limit_exceeded(
        self, identity: Any, details: Dict[str, Any]
    ) -> str:
        event = AuditEvent(
            event_type=EventType.RATE_LIMIT_EXCEEDED,
            identity_id=getattr(identity, "identity_id", None),
            action="rate_limit_check",
            outcome="BLOCKED",
            severity="MEDIUM",
            details=details,
        )
        return await self.log(event)
