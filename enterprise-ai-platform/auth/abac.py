"""
ABAC — Attribute-Based Access Control.
Evaluates fine-grained access policies based on user attributes,
resource attributes, and environmental conditions.
Complements RBAC for project-scoped and sensitivity-based access.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from structlog import get_logger

logger = get_logger(__name__)


@dataclass
class AccessContext:
    """All attributes needed to evaluate an ABAC policy."""
    # User attributes
    user_id: str
    user_roles: list[str]
    user_department: str = ""
    user_clearance_level: int = 1
    user_assigned_projects: list[str] | None = None

    # Resource attributes
    resource_type: str = ""
    resource_project: str = ""
    resource_sensitivity: int = 1   # 1=low, 2=medium, 3=high, 4=critical
    resource_owner_id: str = ""

    # Environmental attributes
    request_ip: str = ""
    hour_of_day: int = 12


@dataclass
class PolicyResult:
    allowed: bool
    policy_name: str
    reason: str

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "policy": self.policy_name, "reason": self.reason}


class ABACEngine:
    """
    Evaluates ABAC policies for fine-grained authorization.

    Policies are evaluated in order; first DENY wins.
    If no policy matches, default is DENY (fail-secure).
    """

    def evaluate(self, policy_name: str, ctx: AccessContext) -> PolicyResult:
        """Evaluate a named policy against the given context."""
        policy_fn = getattr(self, f"_policy_{policy_name}", None)
        if not policy_fn:
            logger.warning("abac_unknown_policy", policy=policy_name)
            return PolicyResult(
                allowed=False,
                policy_name=policy_name,
                reason=f"Policy '{policy_name}' not defined",
            )
        return policy_fn(ctx)

    def _policy_test_case_approval(self, ctx: AccessContext) -> PolicyResult:
        """
        A user can approve test cases if:
        - They are in the QA department or have an Approver/QA Manager role
        - Their clearance level >= 3
        - The resource belongs to one of their assigned projects
        - Current hour is within business hours (08:00–20:00)
        """
        approver_roles = {"qa_manager", "senior_tester", "approver", "system_admin"}
        has_role = bool(set(ctx.user_roles) & approver_roles)
        has_clearance = ctx.user_clearance_level >= 3
        assigned = ctx.user_assigned_projects or []
        in_project = not ctx.resource_project or ctx.resource_project in assigned
        business_hours = 8 <= ctx.hour_of_day <= 20

        if not has_role:
            return PolicyResult(False, "test_case_approval", "Insufficient role")
        if not has_clearance:
            return PolicyResult(False, "test_case_approval", f"Clearance {ctx.user_clearance_level} < 3")
        if not in_project:
            return PolicyResult(False, "test_case_approval", f"Not assigned to project {ctx.resource_project}")
        if not business_hours:
            return PolicyResult(False, "test_case_approval", f"Outside business hours (hour={ctx.hour_of_day})")

        return PolicyResult(True, "test_case_approval", "All conditions met")

    def _policy_knowledge_base_write(self, ctx: AccessContext) -> PolicyResult:
        """KB write requires QA Manager or Architect role AND sensitivity check."""
        write_roles = {"qa_manager", "architect", "system_admin"}
        has_role = bool(set(ctx.user_roles) & write_roles)
        within_sensitivity = ctx.resource_sensitivity <= ctx.user_clearance_level

        if not has_role:
            return PolicyResult(False, "knowledge_base_write", "Insufficient role for KB write")
        if not within_sensitivity:
            return PolicyResult(
                False, "knowledge_base_write",
                f"Resource sensitivity {ctx.resource_sensitivity} > clearance {ctx.user_clearance_level}"
            )
        return PolicyResult(True, "knowledge_base_write", "Authorized")

    def _policy_ado_publish(self, ctx: AccessContext) -> PolicyResult:
        """ADO publish is the most sensitive operation — requires clearance >= 4 or system_admin."""
        if "system_admin" in ctx.user_roles:
            return PolicyResult(True, "ado_publish", "System admin override")
        publish_roles = {"qa_manager", "senior_tester", "approver"}
        has_role = bool(set(ctx.user_roles) & publish_roles)
        has_clearance = ctx.user_clearance_level >= 3
        assigned = ctx.user_assigned_projects or []
        in_project = not ctx.resource_project or ctx.resource_project in assigned

        if not (has_role and has_clearance and in_project):
            return PolicyResult(
                False, "ado_publish",
                f"role={has_role}, clearance={has_clearance}, in_project={in_project}"
            )
        return PolicyResult(True, "ado_publish", "Authorized for ADO publish")

    def check_all(self, policies: list[str], ctx: AccessContext) -> dict[str, PolicyResult]:
        """Evaluate multiple policies and return all results."""
        return {p: self.evaluate(p, ctx) for p in policies}

    def is_allowed(self, policy_name: str, ctx: AccessContext) -> bool:
        return self.evaluate(policy_name, ctx).allowed


def get_abac_engine() -> ABACEngine:
    return ABACEngine()
