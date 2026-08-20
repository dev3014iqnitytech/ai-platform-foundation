"""
aegis_ai.types
==============
Shared type definitions for the Aegis AI SDK.

NewTypes provide type-safety at annotation level while remaining plain str/UUID at runtime.
All result dataclasses are immutable (frozen=True or NamedTuple).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, NewType, Optional
from pydantic import BaseModel, ConfigDict


# ─────────────────────────────────────────────────────────────────────────────
# Semantic String NewTypes
# ─────────────────────────────────────────────────────────────────────────────

UserID = NewType("UserID", str)
AgentID = NewType("AgentID", str)
TenantID = NewType("TenantID", str)
Permission = NewType("Permission", str)
ResourcePath = NewType("ResourcePath", str)
SessionID = NewType("SessionID", str)


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────


class AuthMethod(str, Enum):
    """Authentication method used by the identity."""

    JWT = "jwt"
    SSO = "sso"
    API_KEY = "api_key"
    SERVICE_ACCOUNT = "service_account"
    MFA = "mfa"


class Severity(str, Enum):
    """Event severity levels aligned with NIST SP 800-53."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    INFO = "INFO"


class GuardRailAction(str, Enum):
    """Action taken by a guard rail."""

    PASS = "pass"
    BLOCK = "block"
    REMEDIATE = "remediate"
    WARN = "warn"


# ─────────────────────────────────────────────────────────────────────────────
# Guard Rail Results
# ─────────────────────────────────────────────────────────────────────────────


class GuardRailResult(BaseModel):
    """
    Result from a single GuardRail check.

    Immutable — produced by each GuardRail.check() call.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    score: float = 0.0  # 0.0 = clean, 1.0 = maximum violation
    action: GuardRailAction = GuardRailAction.PASS
    details: Dict[str, Any] = {}
    remediated_text: Optional[str] = None
    owasp_ref: str = ""

    @property
    def reason(self) -> str:
        return self.details.get("reason", "") or (self.details.get("matched_patterns", [""])[0] if self.details.get("matched_patterns") else "violation")


# ─────────────────────────────────────────────────────────────────────────────
# Masking Result
# ─────────────────────────────────────────────────────────────────────────────


class MaskingResult(BaseModel):
    """Result from a DataMasker.mask() call."""

    model_config = ConfigDict(frozen=True)

    original_hash: str  # SHA-256 of original text (never store plaintext)
    masked_text: str
    entity_count: int
    entities_found: List[str]  # entity types detected, e.g. ["EMAIL", "PHONE"]
    masking_map: Dict[str, str] = {}  # placeholder → original mapping (for unmasking)

    def __repr__(self) -> str:
        """Safe representation that redacts plaintext PII in the masking_map."""
        safe_map = {k: "[REDACTED]" for k in self.masking_map.keys()}
        return (
            f"MaskingResult(original_hash='{self.original_hash}', "
            f"entity_count={self.entity_count}, "
            f"entities_found={self.entities_found}, "
            f"masking_map={safe_map})"
        )

    def __str__(self) -> str:
        return self.__repr__()


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Result
# ─────────────────────────────────────────────────────────────────────────────


class PipelineResult(BaseModel):
    """
    Final result yielded by SecurityPipeline.secure_agent_call().

    Contains the safe response, audit metadata, and guard rail outcomes.
    """

    model_config = ConfigDict(frozen=True)

    allowed: bool
    masked_prompt: str
    response: Optional[str] = None
    guard_results: List[GuardRailResult]
    audit_id: str
    latency_ms: float
    masking_map: Dict[str, str] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Retention Enforcement Result
# ─────────────────────────────────────────────────────────────────────────────


class RetentionEnforcement(BaseModel):
    """Proof that zero-retention was enforced for an LLM call."""

    model_config = ConfigDict(frozen=True)

    provider: str
    prompt_hash: str
    response_hash: str
    provider_verified: bool  # Did the provider acknowledge zero-retention?
    enforcement_timestamp: datetime
    raw_stored: bool = False
    compliant: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Rate Limit Result
# ─────────────────────────────────────────────────────────────────────────────


class RateLimitResult(BaseModel):
    """Result from a rate limiter check."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    remaining: int
    reset_at: datetime
    retry_after_seconds: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Policy Decision
# ─────────────────────────────────────────────────────────────────────────────


class PolicyDecisionResult(BaseModel):
    """Result from policy engine evaluation."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str
    matched_policy: Optional[str] = None
    conditions_evaluated: List[str] = []
