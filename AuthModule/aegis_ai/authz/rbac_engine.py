"""
aegis_ai.authz.rbac_engine
============================
Role-Based Access Control engine with hierarchical role resolution.

Role hierarchy (top to bottom, inheriting downward):
  AGENT_ADMIN  →  AGENT_OPERATOR  →  AGENT_READER  →  AGENT_VIEWER

SOLID: Open/Closed — add roles by extending ROLE_HIERARCHY dict.
OWASP: A01:2021-Broken Access Control, LLM08-Excessive Agency
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Set

import structlog

from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.settings import AegisSettings
from aegis_ai.types import Permission, ResourcePath

logger = structlog.get_logger(__name__)

# ── Default role hierarchy (role → all permissions it grants) ──────────────
# Each role includes all permissions of roles below it in the hierarchy.
_VIEWER_PERMISSIONS: FrozenSet[str] = frozenset({
    "agents.read",
    "agents.list",
    "audit.read",
})

_READER_PERMISSIONS: FrozenSet[str] = _VIEWER_PERMISSIONS | frozenset({
    "agents.call",
    "agents.history.read",
})

_OPERATOR_PERMISSIONS: FrozenSet[str] = _READER_PERMISSIONS | frozenset({
    "agents.create",
    "agents.update",
    "agents.deploy",
    "keys.read",
    "guardrails.configure",
})

_ADMIN_PERMISSIONS: FrozenSet[str] = _OPERATOR_PERMISSIONS | frozenset({
    "agents.delete",
    "keys.create",
    "keys.rotate",
    "iam.bindings.write",
    "audit.export",
    "guardrails.override",
    "pipeline.configure",
    "users.manage",
})

_SERVICE_ACCOUNT_PERMISSIONS: FrozenSet[str] = frozenset({
    "agents.call",
    "agents.read",
})

# Role name → granted permissions (hierarchical)
ROLE_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    "AGENT_VIEWER": _VIEWER_PERMISSIONS,
    "AGENT_READER": _READER_PERMISSIONS,
    "AGENT_OPERATOR": _OPERATOR_PERMISSIONS,
    "AGENT_ADMIN": _ADMIN_PERMISSIONS,
    "SERVICE_ACCOUNT": _SERVICE_ACCOUNT_PERMISSIONS,
}


class RBACEngine:
    """
    RBAC engine for role-to-permission resolution.

    Resolves effective permissions from an identity's roles using the
    defined hierarchy. Acts as a fallback when Google IAM is unavailable
    or for non-GCP resources.
    """

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        # Allow custom role maps injected at runtime (e.g., from config)
        self._custom_roles: Dict[str, FrozenSet[str]] = {}

    def register_custom_role(
        self, role_name: str, permissions: FrozenSet[str]
    ) -> None:
        """Register a custom role with its permission set."""
        self._custom_roles[role_name.upper()] = permissions
        logger.info("rbac_custom_role_registered", role=role_name, permission_count=len(permissions))

    def resolve_permissions(self, roles: FrozenSet[str]) -> FrozenSet[Permission]:
        """
        Expand a set of role names into all effective permissions.

        Args:
            roles: Set of role names assigned to the identity.

        Returns:
            Combined frozenset of all permissions granted by those roles.
        """
        all_permissions: Set[str] = set()
        for role in roles:
            role_upper = role.upper()
            if role_upper in ROLE_PERMISSIONS:
                all_permissions.update(ROLE_PERMISSIONS[role_upper])
            elif role_upper in self._custom_roles:
                all_permissions.update(self._custom_roles[role_upper])
            else:
                logger.warning("rbac_unknown_role", role=role)
        return frozenset(Permission(p) for p in all_permissions)

    def has_permission(
        self,
        identity: IdentityContext,
        resource: ResourcePath,
        permission: Permission,
    ) -> bool:
        """
        Check if the identity has a permission on a resource.

        Combines explicit identity.permissions with role-resolved permissions.

        Args:
            identity: The authenticated identity.
            resource: Resource path (informational; RBAC is resource-agnostic).
            permission: The permission to check.

        Returns:
            True if granted by any role or explicit permission.
        """
        # Explicit permissions from token
        if permission in identity.permissions:
            return True

        # Role-expanded permissions
        role_perms = self.resolve_permissions(identity.roles)
        result = permission in role_perms

        logger.debug(
            "rbac_permission_check",
            identity_id=identity.identity_id,
            permission=permission,
            result=result,
            roles=sorted(identity.roles),
        )
        return result

    def get_all_permissions(self, identity: IdentityContext) -> FrozenSet[Permission]:
        """Return the full effective permission set for an identity (RBAC + explicit)."""
        role_perms = self.resolve_permissions(identity.roles)
        return role_perms | identity.permissions

    def get_role_hierarchy(self) -> Dict[str, FrozenSet[str]]:
        """Return the complete role → permissions map (for introspection/audit)."""
        return {**ROLE_PERMISSIONS, **self._custom_roles}
