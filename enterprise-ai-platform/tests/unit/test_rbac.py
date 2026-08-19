"""
Unit tests for RBAC engine.
"""
import pytest
from auth.rbac import RBACEngine, Role, Permission


@pytest.fixture
def rbac():
    return RBACEngine()


def test_system_admin_has_all_permissions(rbac):
    roles = [Role.SYSTEM_ADMIN.value]
    for permission in Permission:
        assert rbac.has_permission(roles, permission), f"Admin should have {permission}"


def test_read_only_cannot_approve(rbac):
    roles = [Role.READ_ONLY.value]
    assert rbac.has_permission(roles, Permission.APPROVE_TEST_CASES) is False


def test_tester_can_generate_but_not_approve(rbac):
    roles = [Role.TESTER.value]
    assert rbac.has_permission(roles, Permission.GENERATE_TEST_CASES) is True
    assert rbac.has_permission(roles, Permission.APPROVE_TEST_CASES) is False


def test_approver_can_approve_and_update_ado(rbac):
    roles = [Role.APPROVER.value]
    assert rbac.has_permission(roles, Permission.APPROVE_TEST_CASES) is True
    assert rbac.has_permission(roles, Permission.UPDATE_ADO) is True
    assert rbac.has_permission(roles, Permission.MANAGE_USERS) is False


def test_qa_manager_can_manage_knowledge_base(rbac):
    roles = [Role.QA_MANAGER.value]
    assert rbac.has_permission(roles, Permission.MANAGE_KNOWLEDGE_BASE) is True
    assert rbac.has_permission(roles, Permission.MANAGE_USERS) is False


def test_architect_can_configure_agents(rbac):
    roles = [Role.ARCHITECT.value]
    assert rbac.has_permission(roles, Permission.CONFIGURE_AGENTS) is True
    assert rbac.has_permission(roles, Permission.APPROVE_TEST_CASES) is False


def test_unknown_role_has_no_permissions(rbac):
    roles = ["unknown_role"]
    assert rbac.has_permission(roles, Permission.GENERATE_TEST_CASES) is False


def test_has_any_permission(rbac):
    roles = [Role.TESTER.value]
    assert rbac.has_any_permission(roles, [Permission.GENERATE_TEST_CASES, Permission.MANAGE_USERS]) is True


def test_has_all_permissions(rbac):
    roles = [Role.TESTER.value]
    assert rbac.has_all_permissions(roles, [Permission.GENERATE_TEST_CASES, Permission.MANAGE_USERS]) is False


def test_get_permissions_returns_set(rbac):
    roles = [Role.APPROVER.value]
    perms = rbac.get_permissions(roles)
    assert isinstance(perms, set)
    assert Permission.APPROVE_TEST_CASES in perms


def test_multiple_roles_union(rbac):
    # Tester + Approver should have combined permissions
    roles = [Role.TESTER.value, Role.APPROVER.value]
    assert rbac.has_permission(roles, Permission.GENERATE_TEST_CASES) is True
    assert rbac.has_permission(roles, Permission.APPROVE_TEST_CASES) is True
