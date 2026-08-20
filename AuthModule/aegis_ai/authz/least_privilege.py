"""
aegis_ai.authz.least_privilege
================================
Least-Privilege enforcement: prune excess permissions from identities.

Ensures agents operate with the minimum permission set required for their
declared task — preventing privilege escalation (OWASP LLM08).

SOLID: SRP — scope enforcement only; no auth/authz decision logic.
OWASP: A01:2021-Broken Access Control, LLM08-Excessive Agency
"""

from __future__ import annotations

from typing import FrozenSet, List, Optional

import structlog

from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.settings import AegisSettings
from aegis_ai.types import Permission, TenantID, UserID

logger = structlog.get_logger(__name__)


class LeastPrivilegeEnforcer:
    """
    Enforces least-privilege access by restricting agent permissions
    to the declared required scope for a task.

    Usage::

        enforcer = LeastPrivilegeEnforcer(settings)

        # Reduce identity to only what the task needs
        scoped_identity = enforcer.reduce_to_scope(
            identity=identity,
            required_permissions=["agents.call", "agents.read"],
        )
    """

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings

    def reduce_to_scope(
        self,
        identity: IdentityContext,
        required_permissions: List[str],
    ) -> IdentityContext:
        """
        Return a new IdentityContext with permissions reduced to the required scope.

        Any permission held by the identity but NOT in required_permissions
        is stripped. This limits blast radius if an agent is compromised.

        Args:
            identity: The fully-authenticated identity.
            required_permissions: Minimum permissions the task needs.

        Returns:
            New IdentityContext with only the intersection of permissions.

        Raises:
            ValueError: If required_permissions is empty (would be a no-op).
        """
        if not required_permissions:
            raise ValueError("required_permissions must not be empty")

        required_set = frozenset(Permission(p) for p in required_permissions)

        # Intersect: only keep permissions the identity actually has AND needs
        effective = identity.permissions & required_set

        dropped = identity.permissions - effective
        if dropped:
            logger.info(
                "least_privilege_scope_reduced",
                identity_id=identity.identity_id,
                dropped_permissions=sorted(dropped),
                retained_permissions=sorted(effective),
            )
        else:
            logger.debug(
                "least_privilege_no_excess",
                identity_id=identity.identity_id,
            )

        return identity.model_copy(update={"permissions": effective})

    def validate_scope(
        self,
        identity: IdentityContext,
        required_permissions: List[str],
    ) -> bool:
        """
        Check that the identity holds ALL required permissions (pre-flight check).

        Args:
            identity: Authenticated identity.
            required_permissions: Permissions that must all be present.

        Returns:
            True if all required permissions are held.
        """
        required_set = frozenset(Permission(p) for p in required_permissions)
        missing = required_set - identity.permissions

        if missing:
            logger.warning(
                "least_privilege_scope_violation",
                identity_id=identity.identity_id,
                missing_permissions=sorted(missing),
            )
            return False
        return True

    def audit_excess_permissions(
        self,
        identity: IdentityContext,
        required_permissions: List[str],
    ) -> FrozenSet[Permission]:
        """
        Return permissions held by the identity but NOT required for this task.

        Useful for generating audit alerts about over-privileged identities.

        Args:
            identity: Authenticated identity.
            required_permissions: What the task actually needs.

        Returns:
            Frozenset of excess permissions (should be empty in ideal system).
        """
        required_set = frozenset(Permission(p) for p in required_permissions)
        excess = identity.permissions - required_set

        if excess:
            logger.warning(
                "least_privilege_excess_detected",
                identity_id=identity.identity_id,
                excess_permissions=sorted(excess),
                recommendation="Review role assignments for this identity",
            )
        return excess
