"""
aegis_ai.auth.identity_context
================================
Immutable representation of an authenticated identity.

All pipeline stages operate on this object after authentication succeeds.
Sensitive fields (email, ip_address) are stripped from audit exports.

SOLID: SRP — holds identity state only; no auth logic here.
OWASP: A07:2021-Identification and Authentication Failures.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Mapping, Optional
from types import MappingProxyType
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator
from aegis_ai.types import AgentID, AuthMethod, Permission, TenantID, UserID


class IdentityContext(BaseModel):
    """
    Immutable, validated identity context.

    Created by an AuthProvider and threaded through the entire pipeline.
    Frozen to prevent mutation after authentication.
    """

    model_config = ConfigDict(frozen=True)

    # ── Core Identity ──────────────────────────────────────────────────────────
    identity_id: UserID = Field(..., description="Unique user or service account ID")
    agent_id: Optional[AgentID] = Field(None, description="Agent ID if calling on behalf of agent")
    tenant_id: TenantID = Field(TenantID("default"), description="Tenant / organisation ID")
    email: Optional[str] = Field(None, description="User email (NOT included in audit exports)")

    # ── Auth Metadata ──────────────────────────────────────────────────────────
    auth_method: AuthMethod = Field(..., description="Method used to authenticate")
    auth_time: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When authentication occurred (UTC)",
    )
    expires_at: Optional[datetime] = Field(None, description="Token expiry time (UTC)")
    session_id: str = Field(..., description="Unique session identifier (UUID string)")
    mfa_verified: bool = Field(False, description="Whether MFA was verified in this session")

    # ── Authorization ──────────────────────────────────────────────────────────
    roles: FrozenSet[str] = Field(default_factory=frozenset, description="Assigned roles")
    permissions: FrozenSet[Permission] = Field(
        default_factory=frozenset, description="Explicit permission grants"
    )

    # ── Network Context (sensitive — audit-stripped) ───────────────────────────
    ip_address: Optional[str] = Field(None, description="Client IP (NOT in audit exports)")
    user_agent: Optional[str] = Field(None, description="Client user agent")

    # ── Extra Metadata ─────────────────────────────────────────────────────────
    metadata: Mapping[str, Any] = Field(default_factory=lambda: MappingProxyType({}))

    @model_validator(mode="after")
    def _freeze_metadata(self) -> IdentityContext:
        """Ensure metadata dict is deeply immutable."""
        if isinstance(self.metadata, dict):
            # Bypass frozen=True just for validation
            object.__setattr__(self, "metadata", MappingProxyType(self.metadata))
        return self

    # ─────────────────────────────────────────────────────────────────
    # Query Methods
    # ─────────────────────────────────────────────────────────────────

    def has_permission(self, permission: Permission) -> bool:
        """Return True if the identity holds the specified permission."""
        return permission in self.permissions

    def has_any_permission(self, *permissions: Permission) -> bool:
        """Return True if the identity holds at least one of the given permissions."""
        return bool(self.permissions.intersection(permissions))

    def has_all_permissions(self, *permissions: Permission) -> bool:
        """Return True if the identity holds ALL of the given permissions."""
        return set(permissions).issubset(self.permissions)

    def has_role(self, role: str) -> bool:
        """Return True if the identity has the specified role."""
        return role in self.roles

    def has_any_role(self, *roles: str) -> bool:
        """Return True if the identity holds at least one of the given roles."""
        return bool(self.roles.intersection(roles))

    def is_expired(self, max_age_seconds: int = 3600) -> bool:
        """
        Return True if the authentication session exceeds max_age_seconds.

        Args:
            max_age_seconds: Maximum session age before re-authentication is required.
        """
        now = datetime.now(timezone.utc)
        age = (now - self.auth_time).total_seconds()
        return age > max_age_seconds

    def requires_mfa(self) -> bool:
        """Return True if MFA is expected but not yet verified."""
        return not self.mfa_verified

    # ─────────────────────────────────────────────────────────────────
    # Audit / Logging
    # ─────────────────────────────────────────────────────────────────

    def to_audit_dict(self) -> Dict[str, Any]:
        """
        Return a sanitised dict safe for audit logs.

        Sensitive fields (email, ip_address, user_agent) are EXCLUDED.
        Only include what's needed for forensic analysis.
        """
        return {
            "identity_id": self.identity_id,
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "auth_method": self.auth_method.value,
            "auth_time": self.auth_time.isoformat(),
            "session_id": self.session_id,
            "mfa_verified": self.mfa_verified,
            "roles": sorted(self.roles),
            "permissions_count": len(self.permissions),
        }

    def __str__(self) -> str:
        return (
            f"IdentityContext(id={self.identity_id}, "
            f"tenant={self.tenant_id}, "
            f"method={self.auth_method.value}, "
            f"roles={sorted(self.roles)})"
        )

    def __repr__(self) -> str:
        return self.__str__()
