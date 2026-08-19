"""
Event Schemas — Pydantic models for all platform domain events.
Ensures type-safe event publishing and consuming across the Service Bus.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BaseEvent(BaseModel):
    """Base envelope for all domain events."""
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    published_at: datetime = Field(default_factory=_now)
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: str = "1.0"


# ── Story Events ─────────────────────────────────────────────

class StoryFetchedPayload(BaseModel):
    session_id: str
    user_story_id: str
    project_key: str
    story_title: str

class StoryFetchedEvent(BaseEvent):
    event_type: str = "story.fetched"
    payload: StoryFetchedPayload


# ── Gherkin Events ───────────────────────────────────────────

class GherkinGeneratedPayload(BaseModel):
    session_id: str
    scenario_count: int
    feature_name: str

class GherkinGeneratedEvent(BaseEvent):
    event_type: str = "gherkin.generated"
    payload: GherkinGeneratedPayload


# ── Test Case Events ─────────────────────────────────────────

class TestCasesDraftedPayload(BaseModel):
    session_id: str
    user_story_id: str
    test_case_count: int
    types_generated: list[str]

class TestCasesDraftedEvent(BaseEvent):
    event_type: str = "testcases.drafted"
    payload: TestCasesDraftedPayload


# ── Review / Approval Events ─────────────────────────────────

class ReviewRequestedPayload(BaseModel):
    session_id: str
    story_id: str | None = None
    story_title: str = ""
    reviewer_emails: list[str] = Field(default_factory=list)
    test_case_count: int = 0

class ReviewRequestedEvent(BaseEvent):
    event_type: str = "review.requested"
    payload: ReviewRequestedPayload


class TestCasesApprovedPayload(BaseModel):
    session_id: str
    approver_id: str
    approver_email: str
    test_case_count: int
    comments: str | None = None

class TestCasesApprovedEvent(BaseEvent):
    event_type: str = "testcases.approved"
    payload: TestCasesApprovedPayload


class TestCasesRejectedPayload(BaseModel):
    session_id: str
    reviewer_id: str
    reviewer_email: str
    reason: str
    revision_count: int

class TestCasesRejectedEvent(BaseEvent):
    event_type: str = "testcases.rejected"
    payload: TestCasesRejectedPayload


# ── ADO Update Events ────────────────────────────────────────

class ADOUpdatedPayload(BaseModel):
    session_id: str
    user_story_id: str
    test_plan_id: int | None = None
    test_case_ids: list[int] = Field(default_factory=list)
    mock: bool = False

class ADOUpdatedEvent(BaseEvent):
    event_type: str = "ado.updated"
    payload: ADOUpdatedPayload


# ── Knowledge Base Events ────────────────────────────────────

class DocumentIngestedPayload(BaseModel):
    document_id: str
    filename: str
    category: str
    chunk_count: int
    uploaded_by: str

class DocumentIngestedEvent(BaseEvent):
    event_type: str = "document.ingested"
    payload: DocumentIngestedPayload


# ── Topic Routing Map ────────────────────────────────────────

TOPIC_MAP: dict[str, str] = {
    "story.fetched": "ado-events",
    "gherkin.generated": "gherkin-events",
    "testcases.drafted": "review-events",
    "review.requested": "approval-events",
    "testcases.approved": "approval-events",
    "testcases.rejected": "approval-events",
    "ado.updated": "ado-events",
    "document.ingested": "kb-events",
}
