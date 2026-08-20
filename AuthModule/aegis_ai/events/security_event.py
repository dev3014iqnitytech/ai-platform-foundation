"""
aegis_ai.events.security_event
================================
Immutable security event dataclass — the payload published to SecurityEventBus.

All events emitted by the pipeline are typed, timestamped, and correlation-ID
tagged for end-to-end traceability across audit sinks.

Design Pattern: Value Object (immutable, equality by value)
OWASP: A09:2021 (Security Logging and Monitoring Failures)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class EventCategory(str, Enum):
    """High-level categories for security event routing."""

    # Authentication
    AUTH_SUCCESS = "auth.success"
    AUTH_FAILURE = "auth.failure"
    TOKEN_EXPIRED = "auth.token_expired"
    TOKEN_REVOKED = "auth.token_revoked"
    MFA_REQUIRED = "auth.mfa_required"
    MFA_SUCCESS = "auth.mfa_success"

    # Authorization
    AUTHZ_GRANTED = "authz.granted"
    AUTHZ_DENIED = "authz.denied"
    POLICY_VIOLATION = "authz.policy_violation"

    # Rate limiting
    RATE_LIMIT_EXCEEDED = "ratelimit.exceeded"
    RATE_LIMIT_WARNING = "ratelimit.warning"

    # GuardRails
    GUARDRAIL_PASS = "guardrail.pass"
    GUARDRAIL_BLOCK = "guardrail.block"
    GUARDRAIL_REMEDIATE = "guardrail.remediate"
    PROMPT_INJECTION = "guardrail.prompt_injection"
    TOXIC_CONTENT = "guardrail.toxic_content"
    PII_DETECTED = "guardrail.pii_detected"

    # LLM / Pipeline
    LLM_CALL_SUCCESS = "llm.call_success"
    LLM_CALL_FAILURE = "llm.call_failure"
    LLM_RESPONSE_BLOCKED = "llm.response_blocked"
    ZERO_RETENTION_VIOLATION = "llm.zero_retention_violation"
    PIPELINE_SUCCESS = "pipeline.success"
    PIPELINE_BLOCKED = "pipeline.blocked"

    # Audit
    AUDIT_SIGNED = "audit.signed"
    AUDIT_FLUSH = "audit.flush"

    # System
    STARTUP = "system.startup"
    HEALTH_DEGRADED = "system.health_degraded"
    SECRET_ACCESSED = "system.secret_accessed"


class EventSeverity(str, Enum):
    """Event severity aligned with NIST SP 800-53 and SIEM conventions."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SecurityEvent(BaseModel):
    """
    Immutable security event published to the SecurityEventBus.

    All fields except ``details`` are structured for index-friendly
    SIEM ingestion (Splunk, Elastic, GCP Cloud Logging).

    Attributes:
        event_id:       Unique event identifier (UUID4).
        correlation_id: Pipeline/request correlation ID for tracing.
        category:       Event category enum for routing.
        severity:       Event severity level.
        timestamp:      UTC timestamp of event occurrence.
        identity_id:    Identity that triggered the event (or 'anonymous').
        agent_id:       Calling agent identifier.
        tenant_id:      Tenant/organisation identifier.
        session_id:     Session identifier.
        source:         Component that emitted the event.
        message:        Human-readable event description.
        details:        Arbitrary structured data (never store PII here).
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = Field(None, description="Pipeline correlation ID")
    category: EventCategory
    severity: EventSeverity = EventSeverity.INFO
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    identity_id: str = Field("anonymous")
    agent_id: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None
    source: str = Field("pipeline", description="Component that emitted the event")
    message: str = Field("")
    details: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def auth_success(
        cls,
        identity_id: str,
        agent_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> "SecurityEvent":
        """Factory method for authentication success events."""
        return cls(
            category=EventCategory.AUTH_SUCCESS,
            severity=EventSeverity.INFO,
            identity_id=identity_id,
            agent_id=agent_id,
            correlation_id=correlation_id,
            message=f"Authentication successful for identity '{identity_id}'",
            details=details or {},
        )

    @classmethod
    def auth_failure(
        cls,
        reason: str,
        correlation_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> "SecurityEvent":
        """Factory method for authentication failure events."""
        return cls(
            category=EventCategory.AUTH_FAILURE,
            severity=EventSeverity.HIGH,
            correlation_id=correlation_id,
            message=f"Authentication failed: {reason}",
            details=details or {},
        )

    @classmethod
    def guardrail_block(
        cls,
        guardrail_name: str,
        identity_id: str,
        score: float,
        owasp_ref: str = "",
        correlation_id: Optional[str] = None,
    ) -> "SecurityEvent":
        """Factory method for guardrail block events."""
        return cls(
            category=EventCategory.GUARDRAIL_BLOCK,
            severity=EventSeverity.HIGH,
            identity_id=identity_id,
            correlation_id=correlation_id,
            message=f"GuardRail '{guardrail_name}' blocked request (score={score:.3f})",
            details={"guardrail": guardrail_name, "score": score, "owasp_ref": owasp_ref},
            source=guardrail_name,
        )

    @classmethod
    def pipeline_blocked(
        cls,
        error_code: str,
        identity_id: str = "anonymous",
        correlation_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> "SecurityEvent":
        """Factory method for pipeline-blocked events."""
        return cls(
            category=EventCategory.PIPELINE_BLOCKED,
            severity=EventSeverity.HIGH,
            identity_id=identity_id,
            correlation_id=correlation_id,
            message=f"Pipeline blocked: {error_code}",
            details=details or {},
        )
