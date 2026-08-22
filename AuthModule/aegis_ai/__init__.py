"""
Aegis AI Enterprise AI Security & Governance SDK.

Public API surface — import only from here in application code.
All submodule internals are considered private.

OWASP LLM Top 10 Coverage: LLM01–LLM10
SOLID: Every public class depends on abstractions; implementations are injectable.
"""

__version__ = "1.1.0"
__author__ = "Enterprise AI Security Team"

# ── Patterns: Factory, Builder, Decorators ────────────────────────────────────
from aegis_ai.factory import PipelineFactory, AuthProviderFactory, AuditLoggerFactory, SecretRepositoryFactory
from aegis_ai.builder import PipelineBuilder
from aegis_ai.decorators import retry_on_transient, require_permission, audit_action, CircuitBreaker
from aegis_ai.startup import validate_production_config

# ── Events (Observer) ─────────────────────────────────────────────────────────
from aegis_ai.events import SecurityEventBus, SecurityEvent, EventCategory, EventSeverity

# ── Secrets (Strategy) ────────────────────────────────────────────────────────
from aegis_ai.secrets import SecretRepository, GCPSecretRepository, EnvSecretRepository

# ── Audit extensions ─────────────────────────────────────────────────────────
from aegis_ai.audit.composite_audit_logger import CompositeAuditLogger
from aegis_ai.audit.splunk_audit_logger import SplunkAuditLogger

from aegis_ai.pipeline import PipelineConfig, SecurityPipeline
from aegis_ai.auth.identity_context import IdentityContext
from aegis_ai.auth.jwt_handler import JWTHandler
from aegis_ai.auth.sso_provider import SSOProvider, SSOConfig
from aegis_ai.auth.api_key_manager import APIKeyManager
from aegis_ai.auth.mfa_verifier import MFAVerifier
from aegis_ai.authz.iam_client import IAMClient
from aegis_ai.authz.policy_engine import PolicyEngine
from aegis_ai.authz.rbac_engine import RBACEngine
from aegis_ai.proxy.llm_gateway import LLMGateway, LLMRequest, LLMMessage, LLMResponse
from aegis_ai.guardrails.injection_detector import InjectionDetector
from aegis_ai.guardrails.toxicity_detector import ToxicityDetector
from aegis_ai.guardrails.pii_detector import PIIDetector
from aegis_ai.guardrails.data_masker import DataMasker
from aegis_ai.guardrails.prompt_defender import PromptDefender
from aegis_ai.guardrails.dynamic_grounder import DynamicGrounder
from aegis_ai.audit.audit_logger import AuditLogger
from aegis_ai.crypto.encryption import Encryptor
from aegis_ai.crypto.key_manager import KeyManager
from aegis_ai.settings import AegisSettings, Environment, get_settings
from aegis_ai.types import (
    AuthMethod,
    AgentID,
    GuardRailResult,
    MaskingResult,
    PipelineResult,
    Permission,
    ResourcePath,
    Severity,
    TenantID,
    UserID,
)
from aegis_ai.exceptions import (
    AegisBaseError,
    AuthenticationError,
    AuthorizationError,
    GuardRailViolationError,
    LLMGatewayError,
    PromptInjectionError,
    RateLimitExceededError,
    ToxicContentError,
    TokenExpiredError,
    TokenInvalidError,
    ZeroRetentionViolationError,
)

__all__ = [
    # ── Patterns ─────────────────────────────────────────────────────────────
    # Factory
    "PipelineFactory",
    "AuthProviderFactory",
    "AuditLoggerFactory",
    "SecretRepositoryFactory",
    # Builder
    "PipelineBuilder",
    # Decorators
    "retry_on_transient",
    "require_permission",
    "audit_action",
    "CircuitBreaker",
    # Startup
    "validate_production_config",
    # Events
    "SecurityEventBus",
    "SecurityEvent",
    "EventCategory",
    "EventSeverity",
    # Secrets
    "SecretRepository",
    "GCPSecretRepository",
    "EnvSecretRepository",
    # Audit extensions
    "CompositeAuditLogger",
    "SplunkAuditLogger",
    # ── Core pipeline ─────────────────────────────────────────────────────────
    "SecurityPipeline",
    "PipelineConfig",
    # Auth
    "IdentityContext",
    "JWTHandler",
    "SSOProvider",
    "SSOConfig",
    "APIKeyManager",
    "MFAVerifier",
    # AuthZ
    "IAMClient",
    "PolicyEngine",
    "RBACEngine",
    # Proxy
    "LLMGateway",
    "LLMRequest",
    "LLMMessage",
    "LLMResponse",
    # GuardRails
    "InjectionDetector",
    "ToxicityDetector",
    "PIIDetector",
    "DataMasker",
    "PromptDefender",
    "DynamicGrounder",
    # Audit
    "AuditLogger",
    # Crypto
    "Encryptor",
    "KeyManager",
    # Settings
    "AegisSettings",
    "Environment",
    "get_settings",
    # Types
    "AuthMethod",
    "AgentID",
    "GuardRailResult",
    "MaskingResult",
    "PipelineResult",
    "Permission",
    "ResourcePath",
    "Severity",
    "TenantID",
    "UserID",
    # Exceptions
    "AegisBaseError",
    "AuthenticationError",
    "AuthorizationError",
    "GuardRailViolationError",
    "LLMGatewayError",
    "PromptInjectionError",
    "RateLimitExceededError",
    "ToxicContentError",
    "TokenExpiredError",
    "TokenInvalidError",
    "ZeroRetentionViolationError",
]
