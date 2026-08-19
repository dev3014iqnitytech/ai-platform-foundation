"""
RBAC — Role-Based Access Control.
Defines roles, permissions, and the role hierarchy.
Used by FastAPI dependency injection for route-level authorization.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache
from structlog import get_logger

logger = get_logger(__name__)


class Role(str, Enum):
    SYSTEM_ADMIN = "system_admin"
    QA_MANAGER = "qa_manager"
    SENIOR_TESTER = "senior_tester"
    TESTER = "tester"
    DEVELOPER = "developer"
    APPROVER = "approver"
    ARCHITECT = "architect"
    READ_ONLY = "read_only"


class Permission(str, Enum):
    # Story operations
    FETCH_STORY = "fetch_story"
    GENERATE_TEST_CASES = "generate_test_cases"

    # Approval workflow
    APPROVE_TEST_CASES = "approve_test_cases"
    REJECT_TEST_CASES = "reject_test_cases"
    UPDATE_ADO = "update_ado"

    # Knowledge Base
    MANAGE_KNOWLEDGE_BASE = "manage_knowledge_base"
    UPLOAD_DOCUMENTS = "upload_documents"
    SEARCH_KNOWLEDGE_BASE = "search_knowledge_base"

    # Audit
    VIEW_AUDIT_LOGS = "view_audit_logs"

    # Prompt management
    MANAGE_PROMPTS = "manage_prompts"
    VIEW_PROMPTS = "view_prompts"

    # Admin
    CONFIGURE_AGENTS = "configure_agents"
    MANAGE_USERS = "manage_users"
    VIEW_METRICS = "view_metrics"


# Role → Permission mapping
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.SYSTEM_ADMIN: set(Permission),  # All permissions

    Role.QA_MANAGER: {
        Permission.FETCH_STORY,
        Permission.GENERATE_TEST_CASES,
        Permission.APPROVE_TEST_CASES,
        Permission.REJECT_TEST_CASES,
        Permission.UPDATE_ADO,
        Permission.MANAGE_KNOWLEDGE_BASE,
        Permission.UPLOAD_DOCUMENTS,
        Permission.SEARCH_KNOWLEDGE_BASE,
        Permission.VIEW_AUDIT_LOGS,
        Permission.MANAGE_PROMPTS,
        Permission.VIEW_PROMPTS,
        Permission.VIEW_METRICS,
    },

    Role.SENIOR_TESTER: {
        Permission.FETCH_STORY,
        Permission.GENERATE_TEST_CASES,
        Permission.APPROVE_TEST_CASES,
        Permission.REJECT_TEST_CASES,
        Permission.UPDATE_ADO,
        Permission.UPLOAD_DOCUMENTS,
        Permission.SEARCH_KNOWLEDGE_BASE,
        Permission.VIEW_AUDIT_LOGS,
        Permission.VIEW_PROMPTS,
        Permission.VIEW_METRICS,
    },

    Role.TESTER: {
        Permission.FETCH_STORY,
        Permission.GENERATE_TEST_CASES,
        Permission.SEARCH_KNOWLEDGE_BASE,
        Permission.VIEW_PROMPTS,
    },

    Role.DEVELOPER: {
        Permission.FETCH_STORY,
        Permission.GENERATE_TEST_CASES,
        Permission.SEARCH_KNOWLEDGE_BASE,
        Permission.VIEW_AUDIT_LOGS,
        Permission.VIEW_METRICS,
    },

    Role.APPROVER: {
        Permission.FETCH_STORY,
        Permission.GENERATE_TEST_CASES,
        Permission.APPROVE_TEST_CASES,
        Permission.REJECT_TEST_CASES,
        Permission.UPDATE_ADO,
        Permission.SEARCH_KNOWLEDGE_BASE,
        Permission.VIEW_AUDIT_LOGS,
        Permission.VIEW_PROMPTS,
    },

    Role.ARCHITECT: {
        Permission.FETCH_STORY,
        Permission.GENERATE_TEST_CASES,
        Permission.MANAGE_KNOWLEDGE_BASE,
        Permission.UPLOAD_DOCUMENTS,
        Permission.SEARCH_KNOWLEDGE_BASE,
        Permission.VIEW_AUDIT_LOGS,
        Permission.MANAGE_PROMPTS,
        Permission.VIEW_PROMPTS,
        Permission.CONFIGURE_AGENTS,
        Permission.VIEW_METRICS,
    },

    Role.READ_ONLY: {
        Permission.SEARCH_KNOWLEDGE_BASE,
        Permission.VIEW_AUDIT_LOGS,
        Permission.VIEW_PROMPTS,
        Permission.VIEW_METRICS,
    },
}


class RBACEngine:
    """Evaluates whether a user (with roles) has the required permission."""

    def has_permission(self, roles: list[str], permission: Permission) -> bool:
        for role_str in roles:
            try:
                role = Role(role_str)
            except ValueError:
                continue
            if permission in ROLE_PERMISSIONS.get(role, set()):
                return True
        return False

    def has_any_permission(self, roles: list[str], permissions: list[Permission]) -> bool:
        return any(self.has_permission(roles, p) for p in permissions)

    def has_all_permissions(self, roles: list[str], permissions: list[Permission]) -> bool:
        return all(self.has_permission(roles, p) for p in permissions)

    def get_permissions(self, roles: list[str]) -> set[Permission]:
        perms: set[Permission] = set()
        for role_str in roles:
            try:
                role = Role(role_str)
                perms |= ROLE_PERMISSIONS.get(role, set())
            except ValueError:
                pass
        return perms


@lru_cache(maxsize=1)
def get_rbac_engine() -> RBACEngine:
    return RBACEngine()
