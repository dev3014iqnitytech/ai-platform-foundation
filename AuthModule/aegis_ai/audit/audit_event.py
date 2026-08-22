"""
aegis_ai.audit.audit_event
============================
Immutable, HMAC-signed audit event model.

Every security-relevant action produces an AuditEvent that is:
- Frozen (immutable after creation)
- HMAC-SHA256 signed before storage
- Verified on read (tamper detection)

Event types cover the full EVLAS lifecycle:
  Evaluate → Validate → Log → Audit → Safety

OWASP: A09:2021-Security Logging and Monitoring Failures
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    """Security event categories."""

    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    TOKEN_ISSUED = "TOKEN_ISSUED"
    TOKEN_REVOKED = "TOKEN_REVOKED"
    LLM_CALL = "LLM_CALL"
    GUARD_RAIL_PASSED = "GUARD_RAIL_PASSED"
    GUARD_RAIL_TRIGGERED = "GUARD_RAIL_TRIGGERED"
    SECURITY_ALERT = "SECURITY_ALERT"
    KEY_ROTATION = "KEY_ROTATION"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    PII_DETECTED = "PII_DETECTED"
    PROMPT_INJECTION = "PROMPT_INJECTION"
    TOXIC_CONTENT = "TOXIC_CONTENT"
    ZERO_RETENTION_CHECK = "ZERO_RETENTION_CHECK"
    MFA_VERIFIED = "MFA_VERIFIED"
    SESSION_CREATED = "SESSION_CREATED"
    SESSION_EXPIRED = "SESSION_EXPIRED"


class AuditEvent(BaseModel):
    """
    Immutable, HMAC-signed audit event.

    All sensitive fields (prompts, responses) are stored as SHA-256 hashes.
    The hmac_signature field is populated by AuditLogger before storage.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Identity context (no PII)
    identity_id: Optional[str] = None
    agent_id: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None

    # Action context
    action: str = ""
    resource: Optional[str] = None
    outcome: str = ""  # "SUCCESS", "FAILURE", "BLOCKED", "WARNED"
    severity: str = "INFO"  # "INFO", "MEDIUM", "HIGH", "CRITICAL"

    # Hashed payload (NEVER store raw prompts/responses)
    prompt_hash: Optional[str] = None
    response_hash: Optional[str] = None

    # Extra details (must NOT contain PII)
    details: Dict[str, Any] = Field(default_factory=dict)

    # Integrity
    hmac_signature: Optional[str] = None

    # ─────────────────────────────────────────────────────────────────
    # Signing
    # ─────────────────────────────────────────────────────────────────

    def _signable_payload(self) -> bytes:
        """
        Produce a deterministic canonical bytes representation for signing.

        All fields except hmac_signature are included.
        """
        data = {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "identity_id": self.identity_id,
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "action": self.action,
            "resource": self.resource,
            "outcome": self.outcome,
            "severity": self.severity,
            "prompt_hash": self.prompt_hash,
            "response_hash": self.response_hash,
            "details": self.details,
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def compute_hmac(self, signing_key: bytes) -> str:
        """
        Compute HMAC-SHA256 signature for this event.

        Args:
            signing_key: 32-byte HMAC signing key.

        Returns:
            Hex-encoded HMAC-SHA256 digest.
        """
        payload = self._signable_payload()
        return hmac.HMAC(signing_key, payload, hashlib.sha256).hexdigest()

    def verify_hmac(self, signing_key: bytes) -> bool:
        """
        Verify the HMAC signature (tamper detection).

        Args:
            signing_key: 32-byte HMAC signing key.

        Returns:
            True if the signature is valid, False if tampered.
        """
        if not self.hmac_signature:
            return False
        expected = self.compute_hmac(signing_key)
        return hmac.compare_digest(expected, self.hmac_signature)

    def to_log_dict(self) -> Dict[str, Any]:
        """Return a log-safe dict (all fields, signature included)."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "identity_id": self.identity_id,
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "action": self.action,
            "resource": self.resource,
            "outcome": self.outcome,
            "severity": self.severity,
            "details": self.details,
            "hmac_signature": self.hmac_signature,
        }
