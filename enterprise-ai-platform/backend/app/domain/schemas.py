"""
Pydantic Schemas — Request/Response models for the API layer.
Separated from ORM models to maintain clean boundaries (DDD).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.domain.enums import (
    ApprovalStatus,
    AuditAction,
    DocumentCategory,
    TestCasePriority,
    TestCaseType,
)


# ─────────────────────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────────────────────
class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PaginationParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class PaginatedResponse(BaseSchema):
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[Any]


# ─────────────────────────────────────────────────────────────────────────────
# User Story (from ADO)
# ─────────────────────────────────────────────────────────────────────────────
class UserStoryRequest(BaseModel):
    """Input: user provides a story ID."""
    user_story_id: str = Field(
        ..., pattern=r"^[A-Za-z]+-\d+$|^\d+$",
        examples=["US-12345", "12345"],
        description="Azure DevOps work item ID or prefixed story ID",
    )


class UserStoryResponse(BaseSchema):
    id: str
    title: str
    description: str | None = None
    acceptance_criteria: str | None = None
    area_path: str | None = None
    tags: list[str] = []
    state: str | None = None
    work_item_type: str | None = None
    linked_items: list[dict] = []
    existing_test_cases: list[dict] = []


# ─────────────────────────────────────────────────────────────────────────────
# Gherkin
# ─────────────────────────────────────────────────────────────────────────────
class GherkinScenario(BaseSchema):
    feature: str
    scenario: str
    given_steps: list[str]
    when_steps: list[str]
    then_steps: list[str]
    and_steps: list[str] = []
    but_steps: list[str] = []
    tags: list[str] = []


class GherkinResponse(BaseSchema):
    user_story_id: str
    scenarios: list[GherkinScenario]
    was_already_gherkin: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Test Cases
# ─────────────────────────────────────────────────────────────────────────────
class TestCaseCreate(BaseSchema):
    title: str
    type: TestCaseType
    priority: TestCasePriority = TestCasePriority.MEDIUM
    gherkin_text: str | None = None
    steps: list[TestStep] | None = None
    expected_result: str | None = None
    preconditions: str | None = None
    tags: list[str] = []


class TestStep(BaseSchema):
    step_number: int
    action: str
    expected_result: str
    test_data: str | None = None


class TestCaseResponse(BaseSchema):
    id: UUID
    session_id: UUID
    title: str
    type: TestCaseType
    priority: TestCasePriority
    gherkin_text: str | None = None
    steps: list[TestStep] | None = None
    expected_result: str | None = None
    preconditions: str | None = None
    tags: list[str] = []
    ado_test_case_id: str | None = None
    version: int = 1
    created_at: datetime


class TestGenerationRequest(BaseModel):
    user_story_id: str = Field(..., examples=["US-12345"])
    include_types: list[TestCaseType] | None = None
    max_test_cases: int = Field(30, ge=1, le=100)
    knowledge_filters: dict | None = None


class TestGenerationSessionResponse(BaseSchema):
    id: UUID
    user_story_id: str
    project_key: str
    status: ApprovalStatus
    revision_count: int
    test_cases: list[TestCaseResponse] = []
    gherkin_scenarios: list[GherkinScenario] = []
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# Approval Workflow
# ─────────────────────────────────────────────────────────────────────────────
class ApprovalRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    session_id: UUID
    action: str = Field(..., pattern=r"^(approve|reject)$")
    comment: str | None = Field(default=None, alias="comments")  # frontend sends "comments"


class ReviewComment(BaseSchema):
    id: UUID | None = None
    session_id: UUID
    test_case_id: UUID | None = None
    author_id: UUID
    author_name: str
    comment: str
    resolved: bool = False
    created_at: datetime | None = None

    @computed_field
    @property
    def content(self) -> str:
        return self.comment


class ApprovalLogEntry(BaseSchema):
    id: UUID
    session_id: UUID
    action: AuditAction
    actor_id: UUID
    actor_name: str
    comment: str | None = None
    previous_status: ApprovalStatus
    new_status: ApprovalStatus
    created_at: datetime


class ApprovalQueueItem(BaseSchema):
    session_id: UUID
    user_story_id: str
    story_title: str | None = None
    project_key: str
    status: ApprovalStatus
    test_case_count: int
    revision_count: int
    created_by: str | None = None        # aliased from created_by_name for frontend compat
    created_by_name: str | None = None   # kept for internal use
    pending_comments: int = 0
    created_at: datetime
    updated_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Base
# ─────────────────────────────────────────────────────────────────────────────
class KBDocumentUpload(BaseModel):
    category: DocumentCategory
    tags: list[str] = []
    metadata: dict = {}


class KBDocumentResponse(BaseSchema):
    id: UUID
    filename: str
    category: DocumentCategory
    version: int
    chunk_count: int | None = None
    embedding_model: str | None = None
    uploaded_by: UUID | None = None
    is_active: bool
    file_size_bytes: int | None = None
    mime_type: str | None = None
    metadata: dict = {}
    created_at: datetime


class KBSearchRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=500)
    category: DocumentCategory | None = None
    top_k: int = Field(10, ge=1, le=50)
    filters: dict | None = None


class KBSearchResult(BaseSchema):
    content: str
    source_document: str
    score: float
    metadata: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# Audit
# ─────────────────────────────────────────────────────────────────────────────
class AuditLogResponse(BaseSchema):
    id: UUID
    session_id: UUID | None = None
    actor_id: UUID | None = None
    actor_email: str | None = None
    action: AuditAction
    entity_type: str | None = None
    entity_id: UUID | None = None
    payload: dict | None = None
    ip_address: str | None = None
    created_at: datetime


class AuditLogFilter(BaseModel):
    session_id: UUID | None = None
    actor_id: UUID | None = None
    action: AuditAction | None = None
    entity_type: str | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────
class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str
    environment: str
    checks: dict[str, str] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Token Usage (internal tracking)
# ─────────────────────────────────────────────────────────────────────────────
class TokenUsage(BaseSchema):
    agent: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached: bool = False
    cost_estimate_usd: float | None = None
