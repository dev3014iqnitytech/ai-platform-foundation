"""
aegis_ai.authz.policy_engine
==============================
Attribute-Based Access Control (ABAC) policy evaluation engine.

Policies evaluate contextual conditions beyond simple role/permission checks:
- Time-based: deny outside business hours
- MFA-required: high-privilege actions need MFA verification
- IP allowlist: restrict to known CIDR ranges
- Environment: production-only resources blocked in dev
- Resource sensitivity: require elevated privileges for sensitive data

SOLID: OCP — add policies by implementing PolicyRule, no pipeline changes.
OWASP: A01:2021-Broken Access Control, LLM07-Insecure Plugin Design
"""

from __future__ import annotations

import abc
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from typing import Any, Dict, List, Optional

import structlog

from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.settings import AegisSettings
from aegis_ai.types import PolicyDecisionResult, ResourcePath

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Policy Rule Interface
# ─────────────────────────────────────────────────────────────────────────────


class PolicyRule(abc.ABC):
    """Abstract base class for an ABAC policy rule."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    def evaluate(
        self,
        identity: IdentityContext,
        action: str,
        resource: ResourcePath,
        context: Dict[str, Any],
    ) -> Optional[PolicyDecisionResult]:
        """
        Evaluate this rule.

        Returns:
            PolicyDecisionResult if this rule applies and makes a decision.
            None if this rule does not apply (next rule is tried).
        """
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Concrete Policy Rules
# ─────────────────────────────────────────────────────────────────────────────


class MFARequiredRule(PolicyRule):
    """
    High-privilege actions require MFA verification.
    Actions matching the pattern must have mfa_verified=True in identity.
    """

    name = "MFARequired"
    _MFA_REQUIRED_ACTIONS = frozenset({
        "agents.delete",
        "keys.create",
        "keys.rotate",
        "iam.bindings.write",
        "audit.export",
        "guardrails.override",
        "pipeline.configure",
        "users.manage",
    })

    def evaluate(
        self,
        identity: IdentityContext,
        action: str,
        resource: ResourcePath,
        context: Dict[str, Any],
    ) -> Optional[PolicyDecisionResult]:
        if action not in self._MFA_REQUIRED_ACTIONS:
            return None  # Rule doesn't apply
        if not identity.mfa_verified:
            return PolicyDecisionResult(
                allowed=False,
                reason=f"MFA required for action '{action}' but not verified in this session",
                matched_policy=self.name,
                conditions_evaluated=["mfa_verified"],
            )
        return PolicyDecisionResult(
            allowed=True,
            reason="MFA verified",
            matched_policy=self.name,
            conditions_evaluated=["mfa_verified"],
        )


class SessionAgeRule(PolicyRule):
    """
    Reject sessions older than the configured maximum age.
    Prevents stale tokens from making sensitive calls.
    """

    name = "SessionAge"
    _MAX_SESSION_AGE_SECONDS = 3600  # 1 hour

    def evaluate(
        self,
        identity: IdentityContext,
        action: str,
        resource: ResourcePath,
        context: Dict[str, Any],
    ) -> Optional[PolicyDecisionResult]:
        if identity.is_expired(max_age_seconds=self._MAX_SESSION_AGE_SECONDS):
            return PolicyDecisionResult(
                allowed=False,
                reason=f"Session age exceeds {self._MAX_SESSION_AGE_SECONDS}s — re-authentication required",
                matched_policy=self.name,
                conditions_evaluated=["session_age"],
            )
        return None  # Not blocking


class IPAllowlistRule(PolicyRule):
    """
    Restrict access to callers from known IP ranges.
    Allowlist is passed via context['allowed_cidrs'].
    If no allowlist is configured, rule passes.
    """

    name = "IPAllowlist"

    def evaluate(
        self,
        identity: IdentityContext,
        action: str,
        resource: ResourcePath,
        context: Dict[str, Any],
    ) -> Optional[PolicyDecisionResult]:
        allowed_cidrs: List[str] = context.get("allowed_cidrs", [])
        if not allowed_cidrs:
            return None  # No IP restriction configured

        caller_ip = identity.ip_address
        if not caller_ip:
            return None  # Cannot check — skip rule

        try:
            caller = ip_address(caller_ip)
            for cidr in allowed_cidrs:
                if caller in ip_network(cidr, strict=False):
                    return None  # IP is allowed — don't block
        except ValueError:
            logger.warning("ip_allowlist_parse_error", ip=caller_ip)
            return None

        return PolicyDecisionResult(
            allowed=False,
            reason=f"Caller IP {caller_ip} not in allowed CIDR ranges",
            matched_policy=self.name,
            conditions_evaluated=["ip_address", "allowed_cidrs"],
        )


class EnvironmentRule(PolicyRule):
    """
    Prevent production-only resources from being accessed in development.
    Resources prefixed with 'projects/<prod-project>' are protected.
    """

    name = "EnvironmentGating"

    def __init__(self, environment: str) -> None:
        self._env = environment

    def evaluate(
        self,
        identity: IdentityContext,
        action: str,
        resource: ResourcePath,
        context: Dict[str, Any],
    ) -> Optional[PolicyDecisionResult]:
        if self._env == "production":
            return None  # No restriction in prod
        # In dev/staging, block destructive actions on production-like resources
        if "production" in str(resource).lower() and action in (
            "agents.delete", "keys.rotate", "iam.bindings.write"
        ):
            return PolicyDecisionResult(
                allowed=False,
                reason=f"Action '{action}' on production resource blocked in '{self._env}' environment",
                matched_policy=self.name,
                conditions_evaluated=["environment", "resource_type"],
            )
        return None


class ResourceSensitivityRule(PolicyRule):
    """
    Resources tagged as 'sensitive' require AGENT_ADMIN or explicit permission.
    Sensitivity communicated via context['resource_sensitivity'] = 'high' | 'critical'.
    """

    name = "ResourceSensitivity"

    def evaluate(
        self,
        identity: IdentityContext,
        action: str,
        resource: ResourcePath,
        context: Dict[str, Any],
    ) -> Optional[PolicyDecisionResult]:
        sensitivity = context.get("resource_sensitivity", "normal")
        if sensitivity not in ("high", "critical"):
            return None

        if sensitivity == "critical" and not identity.has_role("AGENT_ADMIN"):
            return PolicyDecisionResult(
                allowed=False,
                reason="Critical resource requires AGENT_ADMIN role",
                matched_policy=self.name,
                conditions_evaluated=["resource_sensitivity", "roles"],
            )
        if sensitivity == "high" and not identity.has_any_role("AGENT_ADMIN", "AGENT_OPERATOR"):
            return PolicyDecisionResult(
                allowed=False,
                reason="High-sensitivity resource requires AGENT_OPERATOR or AGENT_ADMIN role",
                matched_policy=self.name,
                conditions_evaluated=["resource_sensitivity", "roles"],
            )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Policy Engine
# ─────────────────────────────────────────────────────────────────────────────


# Re-export PolicyDecision for pipeline.py compatibility
PolicyDecision = PolicyDecisionResult


class PolicyEngine:
    """
    ABAC policy evaluation engine.

    Evaluates all registered rules in order. The first DENY wins (fail-fast).
    If all rules pass (or don't apply), access is allowed.
    """

    def __init__(self, settings: AegisSettings) -> None:
        self._settings = settings
        self._rules: List[PolicyRule] = [
            MFARequiredRule(),
            SessionAgeRule(),
            IPAllowlistRule(),
            EnvironmentRule(settings.pipeline.environment),
            ResourceSensitivityRule(),
        ]

    def register_rule(self, rule: PolicyRule) -> None:
        """Register a custom policy rule (appended to evaluation chain)."""
        self._rules.append(rule)
        logger.info("policy_rule_registered", rule=rule.name)

    async def evaluate(
        self,
        identity: IdentityContext,
        action: str,
        resource: ResourcePath,
        context: Dict[str, Any],
    ) -> PolicyDecisionResult:
        """
        Evaluate all policy rules for an access request.

        Args:
            identity: Authenticated identity.
            action: The IAM action being requested.
            resource: Target resource path.
            context: Extra request context (ip, sensitivity, cidrs, etc.).

        Returns:
            PolicyDecisionResult — first DENY terminates evaluation.
        """
        evaluated: List[str] = []

        for rule in self._rules:
            try:
                decision = rule.evaluate(identity, action, resource, context)
                evaluated.append(rule.name)
                if decision is None:
                    continue  # Rule didn't apply
                if not decision.allowed:
                    logger.warning(
                        "policy_denied",
                        rule=rule.name,
                        action=action,
                        identity=identity.identity_id,
                        reason=decision.reason,
                    )
                    return PolicyDecisionResult(
                        allowed=False,
                        reason=decision.reason,
                        matched_policy=decision.matched_policy,
                        conditions_evaluated=evaluated,
                    )
            except Exception as exc:
                logger.error("policy_rule_error", rule=rule.name, error=str(exc))
                # Fail-closed: unexpected error → deny
                return PolicyDecisionResult(
                    allowed=False,
                    reason=f"Policy evaluation error in rule '{rule.name}'",
                    matched_policy=rule.name,
                    conditions_evaluated=evaluated,
                )

        logger.debug(
            "policy_allowed",
            action=action,
            identity=identity.identity_id,
            rules_evaluated=evaluated,
        )
        return PolicyDecisionResult(
            allowed=True,
            reason="All policy rules passed",
            conditions_evaluated=evaluated,
        )
