"""Authorization module for aegis-ai.

OWASP Mapping: A01:2021-Broken Access Control.
Provides IAM integration, RBAC, Policy evaluation, and Least Privilege calculations.
"""

from .iam_client import IAMClient
from .rbac_engine import RBACEngine
from .policy_engine import PolicyEngine
from .least_privilege import LeastPrivilegeEnforcer

__all__ = [
    "IAMClient",
    "RBACEngine",
    "PolicyEngine",
    "LeastPrivilegeEnforcer",
]

