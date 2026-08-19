"""
Azure AD integration — Primary authentication entry point.
Wraps token validation, user provisioning, and session management.
"""
from __future__ import annotations

from structlog import get_logger
from auth.jwt_handler import get_token_validator, AzureADTokenValidator
from auth.rbac import get_rbac_engine, Permission

logger = get_logger(__name__)


class AzureADAuth:
    """
    Orchestrates Azure AD authentication:
    1. Validate incoming Bearer token
    2. Extract user claims
    3. Provision user in database (first-time login)
    4. Return enriched user context
    """

    def __init__(self, validator: AzureADTokenValidator | None = None):
        self.validator = validator or get_token_validator()
        self.rbac = get_rbac_engine()

    async def authenticate(self, token: str) -> dict:
        """
        Validate token and return user context dict.
        Raises ValueError for invalid/expired tokens.
        """
        claims = await self.validator.validate(token)
        roles = self.validator.extract_roles(claims)
        user_info = self.validator.extract_user_info(claims)

        # Provision user on first login
        user = await self._get_or_create_user(user_info, roles)

        permissions = [p.value for p in self.rbac.get_permissions(roles)]

        return {
            "user_id": str(user.get("id", "")),
            "azure_oid": user_info["azure_oid"],
            "email": user_info["email"],
            "display_name": user_info["display_name"],
            "roles": roles,
            "permissions": permissions,
            "tenant_id": user_info.get("tenant_id", ""),
        }

    async def _get_or_create_user(self, user_info: dict, roles: list[str]) -> dict:
        """Upsert user in PostgreSQL on each login."""
        try:
            from app.infrastructure.database.session import async_session_factory
            from app.infrastructure.database.repositories import UserRepository

            async with async_session_factory() as db:
                repo = UserRepository(db)
                # get_or_create_by_oid updates roles on every login
                is_new = await repo.get_by_oid(user_info["azure_oid"]) is None
                user_model = await repo.get_or_create_by_oid(
                    azure_oid=user_info["azure_oid"],
                    email=user_info["email"],
                    display_name=user_info["display_name"],
                    roles=roles,
                )
                await db.commit()
                if is_new:
                    logger.info("user_provisioned", email=user_info["email"])
                return {"id": user_model.id, **user_info}
        except Exception as e:
            logger.warning("user_provision_failed", error=str(e))
            return user_info

    def can(self, user_ctx: dict, permission: Permission) -> bool:
        """Check if authenticated user has a specific permission."""
        return self.rbac.has_permission(user_ctx.get("roles", []), permission)

    def require(self, user_ctx: dict, permission: Permission) -> None:
        """Raise PermissionError if user lacks the required permission."""
        if not self.can(user_ctx, permission):
            raise PermissionError(
                f"Permission '{permission.value}' required. "
                f"User roles: {user_ctx.get('roles', [])}"
            )


# Module-level singleton
_auth: AzureADAuth | None = None


def get_azure_ad_auth() -> AzureADAuth:
    global _auth
    if _auth is None:
        _auth = AzureADAuth()
    return _auth
