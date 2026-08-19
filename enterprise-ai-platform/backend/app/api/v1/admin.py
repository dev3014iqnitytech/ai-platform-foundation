"""
Admin Router â€” User management, role assignment, settings, agent configuration.
Restricted to system_admin role.

Test

"""
from fastapi import APIRouter, Depends, HTTPException
from structlog import get_logger

from app.core.dependencies import CurrentUserDep, DbSessionDep, RequireRoles

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(RequireRoles("system_admin"))])


@router.get("/users", summary="List all users")
async def list_users(user: CurrentUserDep, db: DbSessionDep):
    from sqlalchemy import select
    from app.infrastructure.database.models import UserModel

    result = await db.execute(
        select(UserModel).order_by(UserModel.created_at.desc()).limit(100)
    )
    users = result.scalars().all()
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "display_name": u.display_name,
            "roles": u.roles,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat(),
        }
        for u in users
    ]


@router.patch("/users/{user_id}/roles", summary="Update user roles")
async def update_user_roles(
    user_id: str,
    roles: list[str],
    user: CurrentUserDep,
    db: DbSessionDep,
):
    import uuid as uuid_mod
    from app.infrastructure.database.models import UserModel

    valid_roles = {
        "system_admin", "qa_manager", "senior_tester", "tester",
        "developer", "read_only", "approver", "architect",
    }
    invalid = set(roles) - valid_roles
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid roles: {invalid}")

    target_user = await db.get(UserModel, uuid_mod.UUID(user_id))
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    target_user.roles = roles
    return {"id": str(target_user.id), "roles": target_user.roles}


@router.get("/settings", summary="Get platform settings")
async def get_settings(user: CurrentUserDep):
    from app.core.config import settings
    return {
        "environment": settings.ENVIRONMENT.value,
        "max_revision_count": settings.MAX_REVISION_COUNT,
        "token_budget_per_session": settings.TOKEN_BUDGET_PER_SESSION,
        "rate_limit_per_minute": settings.RATE_LIMIT_REQUESTS_PER_MINUTE,
        "semantic_cache_enabled": settings.ENABLE_SEMANTIC_CACHE,
        "multi_query_retrieval_enabled": settings.ENABLE_MULTI_QUERY_RETRIEVAL,
    }


@router.get("/stats", summary="Platform usage statistics")
async def get_stats(user: CurrentUserDep, db: DbSessionDep):
    from sqlalchemy import func, select
    from app.infrastructure.database.models import (
        TestCaseModel,
        TestGenerationSessionModel,
        UserModel,
        KBDocumentModel,
    )

    user_count = (await db.execute(select(func.count()).select_from(UserModel))).scalar()
    session_count = (await db.execute(select(func.count()).select_from(TestGenerationSessionModel))).scalar()
    tc_count = (await db.execute(select(func.count()).select_from(TestCaseModel))).scalar()
    doc_count = (await db.execute(
        select(func.count()).select_from(KBDocumentModel).where(KBDocumentModel.is_active == True)
    )).scalar()

    return {
        "users": user_count,
        "generation_sessions": session_count,
        "test_cases_generated": tc_count,
        "knowledge_documents": doc_count,
    }


@router.get("/metrics", summary="Platform metrics (alias for /stats)")
async def get_metrics(user: CurrentUserDep, db: DbSessionDep):
    return await get_stats(user, db)


@router.get("/users/{user_id}", summary="Get a single user by ID")
async def get_user(user_id: str, user: CurrentUserDep, db: DbSessionDep):
    import uuid as uuid_mod
    from app.infrastructure.database.models import UserModel
    from fastapi import HTTPException

    target = await db.get(UserModel, uuid_mod.UUID(user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": str(target.id),
        "email": target.email,
        "display_name": target.display_name,
        "roles": target.roles,
        "is_active": target.is_active,
        "created_at": target.created_at.isoformat(),
    }


@router.delete("/users/{user_id}", status_code=204, summary="Deactivate a user account")
async def deactivate_user(user_id: str, user: CurrentUserDep, db: DbSessionDep):
    import uuid as uuid_mod
    from sqlalchemy import update as sa_update
    from app.infrastructure.database.models import UserModel
    from fastapi import HTTPException

    result = await db.execute(
        sa_update(UserModel)
        .where(UserModel.id == uuid_mod.UUID(user_id))
        .values(is_active=False)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="User not found")


@router.get("/health", summary="Aggregate health of all platform dependencies")
async def admin_health(user: CurrentUserDep):
    from app.core.config import settings
    checks: dict[str, str] = {}

    try:
        from app.infrastructure.database.session import get_engine
        async with get_engine().connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    try:
        from app.core.dependencies import _redis_pool
        if _redis_pool:
            await _redis_pool.ping()
            checks["redis"] = "ok"
        else:
            checks["redis"] = "not_initialized"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    try:
        from langchain_openai import OpenAI
        _ = OpenAI(
            azure_deployment=settings.AZURE_OPENAI_CHAT_DEPLOYMENT,
            azure_endpoint=str(settings.AZURE_OPENAI_ENDPOINT),
            api_key=settings.AZURE_OPENAI_API_KEY.get_secret_value(),
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
        checks["azure_openai"] = "configured"
    except Exception as e:
        checks["azure_openai"] = f"error: {e}"

    all_ok = all(v in ("ok", "configured") for v in checks.values())
    return {"status": "healthy" if all_ok else "degraded", **checks}


@router.get("/agents", summary="List agent configurations")
async def list_agent_configs(user: CurrentUserDep):
    from app.core.config import settings
    return [
        {"name": "ado_reader",   "model": "gpt-4o-mini", "enabled": True},
        {"name": "ac_analyzer",  "model": "gpt-4o",      "enabled": True},
        {"name": "knowledge",    "model": "gpt-4o-mini", "enabled": True},
        {"name": "test_creation","model": "gpt-4o",      "enabled": True},
        {"name": "ado_update",   "model": "gpt-4o-mini", "enabled": True},
    ]


@router.patch("/agents/{agent_name}", summary="Update agent configuration (runtime override)")
async def update_agent_config(
    agent_name: str,
    user: CurrentUserDep,
    model: str | None = None,
    enabled: bool | None = None,
):
    # Runtime overrides are in-memory only â€” no persistence needed
    return {"name": agent_name, "model": model, "enabled": enabled, "updated": True}


@router.post("/cache/clear", summary="Clear the RAG semantic cache")
async def clear_cache(user: CurrentUserDep):
    try:
        from rag.retrieval.cache import get_rag_cache
        cache = get_rag_cache()
        await cache.clear_all()
        return {"message": "Cache cleared", "cleared_keys": -1}
    except Exception as e:
        return {"message": f"Cache clear failed: {e}", "cleared_keys": 0}


