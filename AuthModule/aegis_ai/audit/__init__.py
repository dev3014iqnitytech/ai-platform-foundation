"""
Audit layer for secure, immutable logging and SIEM integration.

This module exposes components for tracking events securely across the platform.
OWASP Top 10 for LLM: LLM08: Excessive Agency (monitoring), LLM09: Overreliance (auditing).
"""

from aegis_ai.audit.audit_logger import AuditLogger
from aegis_ai.audit.audit_event import AuditEvent, EventType
from aegis_ai.audit.siem_exporter import SIEMExporter
from aegis_ai.audit.retention_policy import RetentionPolicy

__all__ = ["AuditLogger", "AuditEvent", "EventType", "SIEMExporter", "RetentionPolicy"]
