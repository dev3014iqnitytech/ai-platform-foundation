"""
Approvals Router â€” Human-in-the-loop approval workflow.
All ADO updates require explicit approval through this endpoint.
Modify 
"""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, status
from structlog import get_logger

from app.core.dependencies import (
    CurrentUserDep,
    DbSessionDep,
    RequirePermission,
)
from app.domain.enums import ApprovalStatus, AuditAction
from app.domain.schemas import (
    ApprovalLogEntry,
    ApprovalQueueItem,
    ApprovalRequest,
    ReviewComment,
)
from app.infrastructure.database.repositories import (
    ApprovalLogRepository,
    AuditLogRepository,
    ReviewCommentRepository,
    SessionRepository,
    UserRepository,
)

logger = get_logger(__name__)
router = APIRouter()


@router.get(
    "/queue",
    response_model=list[ApprovalQueueItem],
    summary="Get the review queue â€” items awaiting approval",
    dependencies=[Depends(RequirePermission("test_cases:approve"))],
)
async def get_approval_queue(
    user: CurrentUserDep,
    db: DbSessionDep,
    status: str = ApprovalStatus.IN_REVIEW.value,
    page: int = 1,
    page_size: int = 20,
):
    session_repo = SessionRepository(db)
    sessions, total = await session_repo.list_by_status(
        status, page, page_size
    )

    items = []
    for s in sessions:
        creator_name = s.creator.display_name if s.creator else "Unknown"
        pending = sum(1 for c in (s.review_comments or []) if not getattr(c, "resolved", False))
        items.append(
            ApprovalQueueItem(
                session_id=s.id,
                user_story_id=s.user_story_id,
                story_title=getattr(s, "story_title", None),
                project_key=s.project_key,
                status=s.status,
                test_case_count=len(s.test_cases) if s.test_cases else 0,
                revision_count=s.revision_count,
                created_by=creator_name,
                created_by_name=creator_name,
                pending_comments=pending,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
        )

    return items


@router.post(
    "/review",
    summary="Approve or reject a test case batch",
    dependencies=[Depends(RequirePermission("test_cases:approve"))],
)
async def review_test_cases(
    request: ApprovalRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    """
    Handles approve/reject actions with mandatory comment on rejection.
    On approval â†’ triggers ADO Update Agent via Service Bus event.
    On rejection â†’ sends back to Test Creation Agent with feedback.
    """
    session_repo = SessionRepository(db)
    session = await session_repo.get_by_id(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status not in (
        ApprovalStatus.IN_REVIEW.value,
        ApprovalStatus.DRAFT.value,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot review session in status: {session.status}",
        )

    if request.action == "reject" and not request.comment:
        raise HTTPException(
            status_code=400,
            detail="Comment is required when rejecting",
        )

    # Resolve user
    user_repo = UserRepository(db)
    db_user = await user_repo.get_by_oid(user["oid"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found in system")

    previous_status = session.status

    if request.action == "approve":
        new_status = ApprovalStatus.APPROVED.value
        audit_action = AuditAction.APPROVED
    else:
        new_status = ApprovalStatus.REJECTED.value
        audit_action = AuditAction.REJECTED

    # Update session status
    await session_repo.update_status(request.session_id, new_status)

    # Record approval log
    approval_repo = ApprovalLogRepository(db)
    await approval_repo.create(
        session_id=request.session_id,
        action=request.action,
        actor_id=db_user.id,
        comment=request.comment,
        previous_status=previous_status,
        new_status=new_status,
    )

    # Audit trail
    audit_repo = AuditLogRepository(db)
    await audit_repo.create(
        actor_id=db_user.id,
        action=audit_action.value,
        session_id=request.session_id,
        entity_type="test_generation_session",
        entity_id=request.session_id,
        payload={"comment": request.comment, "action": request.action},
    )

    # Publish event for downstream processing
    try:
        from events.publishers.service_bus_publisher import publish_event

        if request.action == "approve":
            await publish_event(
                topic="approval-events",
                event_type="testcases.approved",
                payload={
                    "session_id": str(request.session_id),
                    "actor_id": str(db_user.id),
                },
            )
        else:
            await publish_event(
                topic="approval-events",
                event_type="testcases.rejected",
                payload={
                    "session_id": str(request.session_id),
                    "actor_id": str(db_user.id),
                    "feedback": request.comment,
                },
            )
    except Exception as e:
        logger.warning("event_publish_failed", error=str(e))

    # Resume the paused LangGraph in the background â€” DB/response must be committed first
    background_tasks.add_task(
        _resume_workflow,
        session_id=str(request.session_id),
        approval_status=new_status,
        reviewer_comment=request.comment,
    )

    return {
        "session_id": str(request.session_id),
        "action": request.action,
        "previous_status": previous_status,
        "new_status": new_status,
        "message": f"Test cases {request.action}d successfully",
    }


async def _resume_workflow(
    session_id: str,
    approval_status: str,
    reviewer_comment: str | None,
) -> None:
    """Resumes the paused LangGraph after human approval or rejection."""
    from workflows.langgraph.graph import resume_after_approval
    try:
        await resume_after_approval(
            session_id=session_id,
            approval_status=approval_status,
            reviewer_comment=reviewer_comment,
        )
    except Exception as e:
        logger.error(
            "workflow_resume_failed",
            session_id=session_id,
            approval_status=approval_status,
            error=str(e),
            exc_info=True,
        )


@router.post(
    "/{session_id}/comments",
    response_model=ReviewComment,
    summary="Add a review comment to a session or specific test case",
)
async def add_comment(
    session_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
    content: str = Body(...),
    test_case_id: UUID | None = Body(default=None),
):
    user_repo = UserRepository(db)
    db_user = await user_repo.get_by_oid(user["oid"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    comment_repo = ReviewCommentRepository(db)
    comment = await comment_repo.create(
        session_id=session_id,
        author_id=db_user.id,
        comment=content,
        test_case_id=test_case_id,
    )
    return ReviewComment(
        id=comment.id,
        session_id=comment.session_id,
        test_case_id=comment.test_case_id,
        author_id=comment.author_id,
        author_name=db_user.display_name or db_user.email,
        comment=comment.comment,
        created_at=comment.created_at,
    )


@router.get(
    "/{session_id}/comments",
    response_model=list[ReviewComment],
    summary="Get all review comments for a session",
)
async def get_comments(
    session_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    comment_repo = ReviewCommentRepository(db)
    comments = await comment_repo.list_by_session(session_id)
    return [
        ReviewComment(
            id=c.id,
            session_id=c.session_id,
            test_case_id=c.test_case_id,
            author_id=c.author_id,
            author_name=c.author.display_name if c.author else "Unknown",
            comment=c.comment,
            created_at=c.created_at,
        )
        for c in comments
    ]


@router.get(
    "/{session_id}/history",
    response_model=list[ApprovalLogEntry],
    summary="Get the full approval history for a session",
)
async def get_approval_history(
    session_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    approval_repo = ApprovalLogRepository(db)
    logs = await approval_repo.list_by_session(session_id)
    return [
        ApprovalLogEntry(
            id=log.id,
            session_id=log.session_id,
            action=log.action,
            actor_id=log.actor_id,
            actor_name=log.actor.display_name if log.actor else "Unknown",
            comment=log.comment,
            previous_status=log.previous_status,
            new_status=log.new_status,
            created_at=log.created_at,
        )
        for log in logs
    ]


@router.get(
    "/{session_id}/review",
    summary="Get full review context: session + test cases + comments",
)
async def get_review_details(
    session_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    from app.infrastructure.database.repositories import TestCaseRepository
    from app.domain.schemas import TestCaseResponse

    session_repo = SessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    tc_repo = TestCaseRepository(db)
    test_cases = await tc_repo.get_by_session(session_id)

    comment_repo = ReviewCommentRepository(db)
    comments = await comment_repo.list_by_session(session_id)

    return {
        "session": {
            "session_id": str(session.id),
            "user_story_id": session.user_story_id,
            "project_key": session.project_key,
            "status": session.status,
            "test_case_count": len(test_cases),
            "revision_count": session.revision_count,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        },
        "test_cases": [TestCaseResponse.model_validate(tc) for tc in test_cases],
        "comments": [
            ReviewComment(
                id=c.id,
                session_id=c.session_id,
                test_case_id=c.test_case_id,
                author_id=c.author_id,
                author_name=c.author.display_name if c.author else "Unknown",
                comment=c.comment,
                created_at=c.created_at,
            )
            for c in comments
        ],
    }


@router.post(
    "/bulk-review",
    summary="Approve or reject multiple sessions at once",
    dependencies=[Depends(RequirePermission("test_cases:approve"))],
)
async def bulk_review(
    background_tasks: BackgroundTasks,
    user: CurrentUserDep,
    db: DbSessionDep,
    session_ids: list[UUID] = Body(...),
    action: str = Body(..., pattern="^(approve|reject)$"),
    comments: str | None = Body(default=None),
):
    user_repo = UserRepository(db)
    db_user = await user_repo.get_by_oid(user["oid"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    succeeded, failed = [], []
    new_status = ApprovalStatus.APPROVED.value if action == "approve" else ApprovalStatus.REJECTED.value

    session_repo = SessionRepository(db)
    approval_repo = ApprovalLogRepository(db)
    audit_repo = AuditLogRepository(db)

    for sid in session_ids:
        try:
            session = await session_repo.get_by_id(sid)
            if not session or session.status not in (ApprovalStatus.IN_REVIEW.value,):
                failed.append(str(sid))
                continue
            await session_repo.update_status(sid, new_status)
            await approval_repo.create(
                session_id=sid,
                action=action,
                actor_id=db_user.id,
                comment=comments,
                previous_status=session.status,
                new_status=new_status,
            )
            await audit_repo.create(
                actor_id=db_user.id,
                action=f"bulk_{action}",
                session_id=sid,
                entity_type="test_generation_session",
                entity_id=sid,
            )
            background_tasks.add_task(
                _resume_workflow,
                session_id=str(sid),
                approval_status=new_status,
                reviewer_comment=comments,
            )
            succeeded.append(str(sid))
        except Exception as e:
            logger.warning("bulk_review_item_failed", session_id=str(sid), error=str(e))
            failed.append(str(sid))

    return {"succeeded": succeeded, "failed": failed}


@router.patch(
    "/{session_id}/comments/{comment_id}/resolve",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark a review comment as resolved",
)
async def resolve_comment(
    session_id: UUID,
    comment_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    from sqlalchemy import update as sa_update
    from app.infrastructure.database.models import ReviewCommentModel

    stmt = (
        sa_update(ReviewCommentModel)
        .where(ReviewCommentModel.id == comment_id)
        .where(ReviewCommentModel.session_id == session_id)
        .values(resolved=True)
    )
    result = await db.execute(stmt)
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Comment not found")


