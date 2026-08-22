"""
aegis_ai.auth.base
==================
Abstract base class for all authentication providers.

SOLID: Open/Closed Principle — new auth methods extend AuthProvider
without modifying the pipeline.
"""

from __future__ import annotations

import abc
from typing import Any, Dict

from aegis_ai.auth.identity_context import IdentityContext


class AuthProvider(abc.ABC):
    """
    Abstract authentication provider interface.

    Every concrete provider (JWT, SSO, API Key) must implement:
    - validate_token: Validates an opaque token string → IdentityContext
    - authenticate: Validates structured credentials → IdentityContext
    - revoke_token: Revokes an active token/session
    """

    @abc.abstractmethod
    async def validate_token(self, token: str) -> IdentityContext:
        """
        Validate an opaque token (JWT string, API key, SSO token).

        Args:
            token: The raw token string (Bearer prefix already stripped).

        Returns:
            Authenticated IdentityContext.

        Raises:
            AuthenticationError: Token is invalid.
            TokenExpiredError: Token has expired.
            TokenInvalidError: Token fails signature/claim validation.
        """
        ...

    @abc.abstractmethod
    async def authenticate(self, credentials: Dict[str, Any]) -> IdentityContext:
        """
        Authenticate using structured credentials (e.g., OAuth code flow).

        Args:
            credentials: Provider-specific credential dict.

        Returns:
            Authenticated IdentityContext.
        """
        ...

    @abc.abstractmethod
    async def revoke_token(self, token: str) -> None:
        """
        Revoke a token / session.

        Args:
            token: The token to invalidate.
        """
        ...
