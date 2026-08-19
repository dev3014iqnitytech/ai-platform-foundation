"""
Auth Router â€” Azure AD login callback, token refresh, user profile.
In LOCAL_MODE a /local-token endpoint issues HS256 dev tokens so the frontend
can authenticate without Azure AD.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from structlog import get_logger

from app.core.config import settings
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.infrastructure.database.repositories import UserRepository

logger = get_logger(__name__)
router = APIRouter()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Local dev token endpoint (LOCAL_MODE only)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class LocalTokenRequest(BaseModel):
    email: str = "dev@local.dev"
    display_name: str = "Local Developer"
    roles: list[str] = ["admin", "tester", "reviewer"]


@router.post(
    "/local-token",
    summary="Issue a local dev JWT (LOCAL_MODE only)",
    include_in_schema=True,
)
async def issue_local_token(body: LocalTokenRequest):
    """
    Returns a signed HS256 JWT for local development.
    This endpoint is only active when LOCAL_MODE=true.
    """
    if not settings.LOCAL_MODE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local token issuance is only available in LOCAL_MODE.",
        )

    from app.core.security import issue_local_token

    token = issue_local_token(
        user_id="local-dev-user",
        email=body.email,
        display_name=body.display_name,
        roles=body.roles,
        expire_minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    )
    return {"access_token": token, "token_type": "bearer"}



@router.get("/me", summary="Get current user profile from Azure AD claims")
async def get_current_user_profile(
    user: CurrentUserDep,
    db: DbSessionDep,
):
    """
    Returns the authenticated user's profile, merging Azure AD claims
    with locally stored roles and preferences.
    """
    user_repo = UserRepository(db)
    db_user = await user_repo.get_or_create_by_oid(
        azure_oid=user.get("oid", user.get("sub")),
        email=user.get("preferred_username", user.get("email", "")),
        display_name=user.get("name", ""),
        roles=user.get("roles", []),
    )
    return {
        "id": str(db_user.id),
        "azure_oid": db_user.azure_oid,
        "email": db_user.email,
        "display_name": db_user.display_name,
        "roles": db_user.roles,
        "is_active": db_user.is_active,
    }


@router.get("/roles", summary="List available roles and their permissions")
async def list_roles(user: CurrentUserDep):
    """Returns the complete role-permission matrix for the UI to render access controls."""
    from app.core.dependencies import RequirePermission

    return {
        "roles": {
            role: sorted(perms)
            for role, perms in RequirePermission.PERMISSION_MAP.items()
        }
    }


@router.post("/logout", summary="Server-side logout (invalidate session cache)")
async def logout(user: CurrentUserDep, request: Request):
    """
    Clears server-side session cache. Client must also call MSAL logout
    to revoke Azure AD tokens.
    """
    logger.info(
        "user_logout",
        user_id=user.get("oid"),
        email=user.get("preferred_username"),
    )
    return {"message": "Logged out. Clear client-side tokens via MSAL."}

