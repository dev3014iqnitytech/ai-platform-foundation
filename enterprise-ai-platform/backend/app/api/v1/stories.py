"""
Stories Router â€” Fetch Azure DevOps User Stories and trigger test generation.
"""
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from structlog import get_logger

from app.core.dependencies import (
    CurrentUserDep,
    DbSessionDep,
    RequirePermission,
    SSEUserDep,
)
from app.domain.schemas import (
    TestGenerationRequest,
    TestGenerationSessionResponse,
    UserStoryRequest,
    UserStoryResponse,
)
from app.infrastructure.database.repositories import (
    AuditLogRepository,
    SessionRepository,
    UserRepository,
)

logger = get_logger(__name__)
router = APIRouter()


@router.post(
    "/fetch",
    response_model=UserStoryResponse,
    summary="Fetch a User Story from Azure DevOps",
)
async def fetch_user_story(
    request: UserStoryRequest,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    """
    Calls the ADO Reader Agent via MCP to fetch the story,
    returning structured data for preview before generation.
    """
    from agents.ado_reader.agent import ADOReaderAgent

    agent = ADOReaderAgent()
    try:
        story = await agent.fetch_story(request.user_story_id)
    except Exception as e:
        logger.error("story_fetch_failed", story_id=request.user_story_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch story from Azure DevOps: {e}",
        ) from e

    # Audit
    user_repo = UserRepository(db)
    db_user = await user_repo.get_by_oid(user["oid"])
    if db_user:
        audit_repo = AuditLogRepository(db)
        await audit_repo.create(
            actor_id=db_user.id,
            action="story_fetched",
            payload={"user_story_id": request.user_story_id},
        )

    return story


@router.post(
    "/generate",
    response_model=TestGenerationSessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger AI test case generation for a User Story",
    dependencies=[Depends(RequirePermission("test_cases:generate"))],
)
async def generate_test_cases(
    request: TestGenerationRequest,
    background_tasks: BackgroundTasks,
    user: CurrentUserDep,
    db: DbSessionDep,
    wait: bool = False,
    timeout: int = 120,
):
    """
    Kicks off the full LangGraph workflow:
    1. ADO Reader â†’ 2. AC Analyzer â†’ 3. Test Creation â†’ 4. Knowledge Enrichment
    â†’ 5. Validation â†’ Draft (awaiting review)

    Returns immediately with a session ID; processing continues in the background.
    """
    # Create session in DB
    user_repo = UserRepository(db)
    db_user = await user_repo.get_or_create_by_oid(
        azure_oid=user.get("oid", user.get("sub")),
        email=user.get("preferred_username", ""),
        display_name=user.get("name", ""),
        roles=user.get("roles", []),
    )

    session_repo = SessionRepository(db)
    session = await session_repo.create(
        user_story_id=request.user_story_id,
        project_key=request.user_story_id.split("-")[0] if "-" in request.user_story_id else "DEFAULT",
        created_by=db_user.id,
    )

    # Audit
    audit_repo = AuditLogRepository(db)
    await audit_repo.create(
        actor_id=db_user.id,
        action="session_created",
        session_id=session.id,
        payload={
            "user_story_id": request.user_story_id,
            "include_types": [t.value for t in (request.include_types or [])],
        },
    )

    # If caller wants synchronous behaviour (e.g. UI waiting for result),
    # run the workflow inline and return the final session. Otherwise
    # schedule background execution and return early (202 Accepted).
    if wait:
        import asyncio

        try:
            await asyncio.wait_for(
                _run_generation_workflow(
                    session_id=session.id,
                    user_id=str(db_user.id),
                    user_story_id=request.user_story_id,
                    include_types=request.include_types,
                    max_test_cases=request.max_test_cases,
                    knowledge_filters=request.knowledge_filters,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "generation_workflow_timeout",
                session_id=str(session.id),
                timeout=timeout,
            )

        # Reload session and return latest state
        session_repo = SessionRepository(db)
        session = await session_repo.get_by_id(session.id)
        return TestGenerationSessionResponse.model_validate(session)

    # Launch LangGraph workflow in background
    background_tasks.add_task(
        _run_generation_workflow,
        session_id=session.id,
        user_id=str(db_user.id),
        user_story_id=request.user_story_id,
        include_types=request.include_types,
        max_test_cases=request.max_test_cases,
        knowledge_filters=request.knowledge_filters,
    )

    return TestGenerationSessionResponse(
        id=session.id,
        user_story_id=session.user_story_id,
        project_key=session.project_key,
        status=session.status,
        revision_count=session.revision_count,
        created_by=session.created_by,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=TestGenerationSessionResponse,
    summary="Get generation session details with test cases",
)
async def get_session(
    session_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    session_repo = SessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return TestGenerationSessionResponse.model_validate(session)


@router.get(
    "/sessions",
    summary="List user's generation sessions",
)
async def list_sessions(
    user: CurrentUserDep,
    db: DbSessionDep,
    page: int = 1,
    page_size: int = 20,
):
    user_repo = UserRepository(db)
    db_user = await user_repo.get_by_oid(user["oid"])
    if not db_user:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    session_repo = SessionRepository(db)
    sessions, total = await session_repo.list_by_user(db_user.id, page, page_size)

    return {
        "items": [TestGenerationSessionResponse.model_validate(s) for s in sessions],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel and soft-delete a generation session",
)
async def cancel_session(
    session_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    session_repo = SessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    cancelable = {"DRAFT", "IN_REVIEW"}
    if session.status not in cancelable:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel session in status '{session.status}'",
        )

    await session_repo.update_status(session_id, "CANCELLED")


@router.post(
    "/sessions/{session_id}/retry",
    response_model=TestGenerationSessionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed or cancelled session",
    dependencies=[Depends(RequirePermission("test_cases:generate"))],
)
async def retry_session(
    session_id: UUID,
    background_tasks: BackgroundTasks,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    session_repo = SessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    retryable = {"DRAFT", "CANCELLED"}
    if session.status not in retryable:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot retry session in status '{session.status}'",
        )

    user_repo = UserRepository(db)
    db_user = await user_repo.get_by_oid(user["oid"])

    await session_repo.update_status(session_id, "DRAFT")

    background_tasks.add_task(
        _run_generation_workflow,
        session_id=session_id,
        user_id=str(db_user.id) if db_user else "system",
        user_story_id=session.user_story_id,
        include_types=None,
        max_test_cases=30,
        knowledge_filters=None,
    )

    session = await session_repo.get_by_id(session_id)
    return TestGenerationSessionResponse.model_validate(session)


@router.get(
    "/sessions/{session_id}/stream",
    summary="Stream session status updates via Server-Sent Events",
)
async def stream_session_progress(
    session_id: UUID,
    request: Request,
    user: SSEUserDep,
    db: DbSessionDep,
):
    """Polls session status every 2 seconds and streams updates as SSE."""
    import asyncio
    import json

    session_repo = SessionRepository(db)

    async def event_generator():
        terminal_statuses = {"PUBLISHED", "CANCELLED", "DRAFT"}
        poll_count = 0
        max_polls = 300  # 10 minutes at 2s intervals

        while not await request.is_disconnected() and poll_count < max_polls:
            session = await session_repo.get_by_id(session_id)
            if not session:
                yield f"data: {json.dumps({'error': 'session not found'})}\n\n"
                break

            payload = {
                "session_id": str(session.id),
                "status": session.status,
                "revision_count": session.revision_count,
                "test_case_count": len(session.test_cases) if session.test_cases else 0,
            }
            yield f"data: {json.dumps(payload)}\n\n"

            if session.status in terminal_statuses:
                break

            await asyncio.sleep(2)
            poll_count += 1

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_generation_workflow(
    session_id: UUID,
    user_id: str,
    user_story_id: str,
    include_types: list | None,
    max_test_cases: int,
    knowledge_filters: dict | None,
) -> None:
    """Background task: runs the full LangGraph generation pipeline."""
    try:
        from workflows.langgraph.graph import run_platform_workflow

        await run_platform_workflow(
            session_id=str(session_id),
            user_id=user_id,
            user_story_id=user_story_id,
            include_types=include_types,
            max_test_cases=max_test_cases,
            knowledge_filters=knowledge_filters,
        )
        logger.info("generation_workflow_completed", session_id=str(session_id))
    except Exception as e:
        logger.error(
            "generation_workflow_failed",
            session_id=str(session_id),
            error=str(e),
            exc_info=True,
        )

