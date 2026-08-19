"""
Audit Router â€” Immutable audit trail viewer with filtering.
"""
import csv
import io
import uuid as uuid_mod
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from structlog import get_logger

from app.core.dependencies import CurrentUserDep, DbSessionDep, RequirePermission
from app.domain.schemas import AuditLogResponse, PaginatedResponse
from app.infrastructure.database.repositories import AuditLogRepository

logger = get_logger(__name__)
router = APIRouter()


async def _list_logs(
    db,
    session_id: str | None,
    action: str | None,
    page: int,
    page_size: int,
) -> tuple:
    repo = AuditLogRepository(db)
    return await repo.list_filtered(
        action=action,
        session_id=uuid_mod.UUID(session_id) if session_id else None,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/",
    response_model=PaginatedResponse,
    summary="View audit logs with filtering",
    dependencies=[Depends(RequirePermission("audit:view"))],
)
async def list_audit_logs(
    user: CurrentUserDep,
    db: DbSessionDep,
    session_id: str | None = None,
    action: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    logs, total = await _list_logs(db, session_id, action, page, page_size)
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[AuditLogResponse.model_validate(log) for log in logs],
    )


# Frontend calls /audit/logs â€” register as alias
@router.get(
    "/logs",
    response_model=PaginatedResponse,
    summary="View audit logs (alias for /)",
    dependencies=[Depends(RequirePermission("audit:view"))],
)
async def list_audit_logs_alias(
    user: CurrentUserDep,
    db: DbSessionDep,
    session_id: str | None = None,
    action: str | None = None,
    page: int = 1,
    page_size: int = 50,
):
    return await list_audit_logs(user, db, session_id, action, page, page_size)


@router.get(
    "/logs/{log_id}",
    response_model=AuditLogResponse,
    summary="Get a single audit log entry",
    dependencies=[Depends(RequirePermission("audit:view"))],
)
async def get_audit_log(
    log_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    from fastapi import HTTPException
    from app.infrastructure.database.models import AuditLogModel

    log = await db.get(AuditLogModel, log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Audit log entry not found")
    return AuditLogResponse.model_validate(log)


@router.get(
    "/session/{session_id}",
    response_model=list[AuditLogResponse],
    summary="Get audit trail for a specific session",
    dependencies=[Depends(RequirePermission("audit:view"))],
)
async def get_session_audit(
    session_id: str,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    repo = AuditLogRepository(db)
    logs = await repo.list_by_session(uuid_mod.UUID(session_id))
    return [AuditLogResponse.model_validate(log) for log in logs]


@router.get(
    "/action-types",
    response_model=list[str],
    summary="Get distinct action types for filter dropdowns",
    dependencies=[Depends(RequirePermission("audit:view"))],
)
async def get_action_types(user: CurrentUserDep, db: DbSessionDep):
    from sqlalchemy import select, distinct
    from app.infrastructure.database.models import AuditLogModel

    result = await db.execute(select(distinct(AuditLogModel.action)).order_by(AuditLogModel.action))
    return [row[0] for row in result.fetchall()]


@router.get(
    "/export/csv",
    summary="Export audit logs as CSV",
    dependencies=[Depends(RequirePermission("audit:view"))],
)
async def export_audit_csv(
    user: CurrentUserDep,
    db: DbSessionDep,
    session_id: str | None = None,
    action: str | None = None,
):
    # Fetch up to 10k rows for export
    logs, _ = await _list_logs(db, session_id, action, page=1, page_size=10000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "created_at", "action", "actor_id", "session_id", "entity_type", "entity_id"])
    for log in logs:
        writer.writerow([
            str(log.id),
            log.created_at.isoformat(),
            log.action,
            str(log.actor_id) if log.actor_id else "",
            str(log.session_id) if log.session_id else "",
            log.entity_type or "",
            str(log.entity_id) if log.entity_id else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit-logs.csv"},
    )


