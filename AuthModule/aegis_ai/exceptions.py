"""
Aegis AI Exception Definitions.

Defines all custom exceptions for the Aegis AI SDK.
Maps to OWASP Top 10 LLM vulnerabilities such as LLM01 (Prompt Injection),
LLM02 (Insecure Output Handling), and LLM06 (Sensitive Information Disclosure).
"""

import time
from typing import Any, Dict, Optional


class AegisBaseError(Exception):
    """Base exception for all Aegis AI errors."""

    def __init__(
        self,
        message: str,
        error_code: str,
        http_status: int,
        details: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize base error.

        Args:
            message (str): Human-readable error description.
            error_code (str): Machine-readable error code.
            http_status (int): Associated HTTP status code.
            details (Optional[Dict[str, Any]]): Additional error context.
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.http_status = http_status
        self.details = details or {}
        self.timestamp = time.time()


class AuthenticationError(AegisBaseError):
    """Raised when authentication fails."""

    def __init__(self, message: str, error_code: str = "AUTH_ERROR", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, error_code, 401, details)


class AuthorizationError(AegisBaseError):
    """Raised when authorization fails."""

    def __init__(self, message: str, error_code: str = "AUTHZ_ERROR", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, error_code, 403, details)


class TokenExpiredError(AuthenticationError):
    """Raised when a token has expired."""
    
    def __init__(self, message: str = "Token has expired", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "TOKEN_EXPIRED", details)


class TokenInvalidError(AuthenticationError):
    """Raised when a token is invalid."""
    
    def __init__(self, message: str = "Token is invalid", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "TOKEN_INVALID", details)


class MFARequiredError(AuthenticationError):
    """Raised when Multi-Factor Authentication is required."""
    
    def __init__(self, message: str = "MFA is required", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "MFA_REQUIRED", details)


class PermissionDeniedError(AuthorizationError):
    """Raised when the user lacks required permissions."""
    
    def __init__(self, message: str = "Permission denied", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "PERMISSION_DENIED", details)


class PolicyViolationError(AuthorizationError):
    """Raised when an action violates a defined policy."""
    
    def __init__(self, message: str = "Policy violation", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "POLICY_VIOLATION", details)


class GuardRailViolationError(AegisBaseError):
    """Base class for guard rail violations."""

    def __init__(self, message: str, error_code: str = "GUARDRAIL_VIOLATION", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, error_code, 422, details)


class PromptInjectionError(GuardRailViolationError):
    """Raised when a prompt injection attempt is detected."""
    
    def __init__(self, message: str = "Prompt injection detected", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "PROMPT_INJECTION", details)


class ToxicContentError(GuardRailViolationError):
    """Raised when toxic content is detected in the payload."""
    
    def __init__(self, message: str = "Toxic content detected", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "TOXIC_CONTENT", details)


class PIIDetectedError(GuardRailViolationError):
    """Raised when unmasked Personally Identifiable Information is detected."""
    
    def __init__(self, message: str = "PII detected", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "PII_DETECTED", details)


class RateLimitExceededError(AegisBaseError):
    """Raised when rate limits are exceeded."""
    
    def __init__(self, message: str = "Rate limit exceeded", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "RATE_LIMIT_EXCEEDED", 429, details)


class LLMGatewayError(AegisBaseError):
    """Raised when there is an error communicating with the LLM Gateway."""
    
    def __init__(self, message: str = "LLM gateway error", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "LLM_GATEWAY_ERROR", 502, details)


class ZeroRetentionViolationError(AegisBaseError):
    """Raised when zero retention policies are violated by the LLM Provider."""
    
    def __init__(self, message: str = "Zero retention policy violated", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "ZERO_RETENTION_VIOLATION", 502, details)


class EncryptionError(AegisBaseError):
    """Raised when an error occurs during encryption or decryption."""
    
    def __init__(self, message: str = "Encryption error", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "ENCRYPTION_ERROR", 500, details)


class AuditError(AegisBaseError):
    """Raised when audit logging fails."""
    
    def __init__(self, message: str = "Audit logging error", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "AUDIT_ERROR", 500, details)


class ConfigurationError(AegisBaseError):
    """Raised when there is a configuration error."""
    
    def __init__(self, message: str = "Configuration error", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "CONFIGURATION_ERROR", 500, details)
