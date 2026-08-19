"""
Test Cases Router â€” View, filter, update, and export generated test cases.
"""
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, status
from structlog import get_logger

from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.domain.schemas import TestCaseResponse
from app.infrastructure.database.repositories import (
    SessionRepository,
    TestCaseRepository,
)

logger = get_logger(__name__)
router = APIRouter()


@router.get(
    "/",
    response_model=list[TestCaseResponse],
    summary="List test cases for a session (query param form)",
)
async def list_test_cases(
    user: CurrentUserDep,
    db: DbSessionDep,
    session_id: UUID | None = None,
    type: str | None = None,
    priority: str | None = None,
):
    """Frontend calls GET /test-cases?session_id=... with optional type/priority filters."""
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id query param required")
    tc_repo = TestCaseRepository(db)
    test_cases = await tc_repo.get_by_session(session_id)
    results = [TestCaseResponse.model_validate(tc) for tc in test_cases]
    if type:
        results = [tc for tc in results if tc.type.value == type]
    if priority:
        results = [tc for tc in results if tc.priority.value == priority]
    return results


@router.get(
    "/stats",
    summary="Test case statistics for a session",
)
async def get_test_case_stats(
    user: CurrentUserDep,
    db: DbSessionDep,
    session_id: UUID | None = None,
):
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id query param required")
    tc_repo = TestCaseRepository(db)
    test_cases = await tc_repo.get_by_session(session_id)

    by_type: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    with_gherkin = 0
    for tc in test_cases:
        by_type[tc.type] = by_type.get(tc.type, 0) + 1
        by_priority[tc.priority] = by_priority.get(tc.priority, 0) + 1
        if tc.gherkin_text:
            with_gherkin += 1

    return {
        "total": len(test_cases),
        "by_type": by_type,
        "by_priority": by_priority,
        "with_gherkin": with_gherkin,
    }


@router.post(
    "/export",
    summary="Export test cases in various formats",
)
async def export_test_cases_post(
    user: CurrentUserDep,
    db: DbSessionDep,
    session_id: UUID = Body(...),
    format: str = Body("json"),
):
    """Frontend POSTs {session_id, format} to export."""
    return await _export_session(session_id, format, db)


@router.get(
    "/session/{session_id}",
    response_model=list[TestCaseResponse],
    summary="Get all test cases for a generation session",
)
async def get_test_cases_by_session(
    session_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
    type_filter: str | None = None,
):
    session_repo = SessionRepository(db)
    session = await session_repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    tc_repo = TestCaseRepository(db)
    test_cases = await tc_repo.get_by_session(session_id)

    results = [TestCaseResponse.model_validate(tc) for tc in test_cases]
    if type_filter:
        results = [tc for tc in results if tc.type.value == type_filter]

    return results


@router.get(
    "/{test_case_id}",
    response_model=TestCaseResponse,
    summary="Get a single test case by ID",
)
async def get_test_case(
    test_case_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    from app.infrastructure.database.models import TestCaseModel

    tc = await db.get(TestCaseModel, test_case_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Test case not found")
    return TestCaseResponse.model_validate(tc)


@router.get(
    "/session/{session_id}/export",
    summary="Export test cases as structured JSON for ADO import",
)
async def export_test_cases(
    session_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
    format: str = "json",
):
    """Export test cases in a format ready for Azure DevOps import."""
    tc_repo = TestCaseRepository(db)
    test_cases = await tc_repo.get_by_session(session_id)

    if format == "json":
        return {
            "session_id": str(session_id),
            "test_cases": [
                {
                    "title": tc.title,
                    "type": tc.type,
                    "priority": tc.priority,
                    "gherkin": tc.gherkin_text,
                    "steps": tc.steps,
                    "tags": tc.tags,
                }
                for tc in test_cases
            ],
            "total_count": len(test_cases),
        }

    raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")


async def _export_session(session_id: UUID, format: str, db) -> dict:
    tc_repo = TestCaseRepository(db)
    test_cases = await tc_repo.get_by_session(session_id)
    if format == "json":
        return {
            "session_id": str(session_id),
            "test_cases": [
                {
                    "title": tc.title,
                    "type": tc.type,
                    "priority": tc.priority,
                    "gherkin": tc.gherkin_text,
                    "steps": tc.steps,
                    "tags": tc.tags,
                }
                for tc in test_cases
            ],
            "total_count": len(test_cases),
        }
    raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")


@router.patch(
    "/{test_case_id}",
    response_model=TestCaseResponse,
    summary="Update a test case (reviewer edits)",
)
async def update_test_case(
    test_case_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
    title: str | None = Body(default=None),
    description: str | None = Body(default=None),
    gherkin_text: str | None = Body(default=None),
    steps: list | None = Body(default=None),
    priority: str | None = Body(default=None),
    tags: list[str] | None = Body(default=None),
):
    from sqlalchemy import update as sa_update
    from app.infrastructure.database.models import TestCaseModel

    tc = await db.get(TestCaseModel, test_case_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Test case not found")

    updates: dict = {}
    if title is not None:
        updates["title"] = title
    if description is not None:
        updates["description"] = description
    if gherkin_text is not None:
        updates["gherkin_text"] = gherkin_text
    if steps is not None:
        updates["steps"] = steps
    if priority is not None:
        updates["priority"] = priority
    if tags is not None:
        updates["tags"] = tags
    if updates:
        updates["version"] = tc.version + 1
        stmt = sa_update(TestCaseModel).where(TestCaseModel.id == test_case_id).values(**updates)
        await db.execute(stmt)
        await db.refresh(tc)

    return TestCaseResponse.model_validate(tc)


@router.delete(
    "/{test_case_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a test case from a session",
)
async def delete_test_case(
    test_case_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    from sqlalchemy import delete as sa_delete
    from app.infrastructure.database.models import TestCaseModel

    result = await db.execute(sa_delete(TestCaseModel).where(TestCaseModel.id == test_case_id))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Test case not found")


@router.get(
    "/{test_case_id}/versions",
    response_model=list[TestCaseResponse],
    summary="Get version history for a test case (placeholder â€” returns current only)",
)
async def get_test_case_versions(
    test_case_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    from app.infrastructure.database.models import TestCaseModel

    tc = await db.get(TestCaseModel, test_case_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Test case not found")
    return [TestCaseResponse.model_validate(tc)]

