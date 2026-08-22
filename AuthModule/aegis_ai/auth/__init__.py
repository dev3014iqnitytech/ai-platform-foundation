"""Authentication module for aegis-ai.

OWASP Mapping:
- A01:2021 - Broken Access Control
- A07:2021 - Identification and Authentication Failures
"""

from aegis_ai.auth.base import AuthProvider
from aegis_ai.auth.jwt_handler import JWTHandler
from aegis_ai.auth.sso_provider import SSOProvider
from aegis_ai.auth.api_key_manager import APIKeyManager
from aegis_ai.auth.mfa_verifier import MFAVerifier
from aegis_ai.auth.identity_context import IdentityContext


__all__ = [
    "AuthProvider",
    "JWTHandler",
    "SSOProvider",
    "APIKeyManager",
    "MFAVerifier",
    "IdentityContext",
]
