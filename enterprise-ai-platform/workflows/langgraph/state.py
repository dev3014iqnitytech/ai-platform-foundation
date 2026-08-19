"""
LangGraph Platform State — Typed state shared across all graph nodes.
Uses TypedDict for type safety + Annotated for message accumulation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional
from uuid import UUID

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class PlatformState(TypedDict, total=False):
    """
    Shared state flowing through the LangGraph pipeline.
    Every field is optional (total=False) to allow partial updates at each node.

    Token tracking: every agent appends to token_usage with its consumption.
    Audit trail: accumulated via add_messages reducer (append-only).
    """

    # ── Identity ──
    session_id: str
    user_id: str
    user_story_id: str
    project_key: str

    # ── Workflow Data ──
    user_story: Optional[dict]                 # Fetched from ADO
    gherkin_scenarios: Optional[list[dict]]    # Converted/parsed Gherkin
    was_already_gherkin: bool                  # Skip conversion cost flag
    knowledge_context: Optional[list[dict]]    # RAG retrieved chunks
    knowledge_filters: Optional[dict]          # Metadata filters for RAG
    test_cases: Optional[list[dict]]           # Generated test cases
    coverage_summary: Optional[dict]           # Type coverage breakdown

    # ── Control Flow ──
    approval_status: str                       # DRAFT|IN_REVIEW|APPROVED|REJECTED|PUBLISHED
    revision_count: int
    max_revisions: int
    max_test_cases: int
    include_types: Optional[list[str]]
    next_node: str

    # ── Human-in-the-Loop ──
    reviewer_comments: list[dict]
    approval_log: list[dict]

    # ── ADO Output ──
    ado_plan_id: Optional[int]
    ado_test_case_ids: Optional[list[int]]

    # ── Observability ──
    token_usage: dict[str, Any]                # Per-agent token consumption
    audit_trail: Annotated[list[dict], add_messages]  # Append-only audit log
    started_at: Optional[str]
    completed_at: Optional[str]

    # ── Error Handling ──
    error: Optional[str]
    error_agent: Optional[str]


def initial_state(
    session_id: str,
    user_id: str,
    user_story_id: str,
    project_key: str = "DEFAULT",
    max_test_cases: int = 30,
    knowledge_filters: dict | None = None,
    include_types: list[str] | None = None,
) -> PlatformState:
    """Factory function to create a clean initial state."""
    return PlatformState(
        session_id=session_id,
        user_id=user_id,
        user_story_id=user_story_id,
        project_key=project_key,
        user_story=None,
        gherkin_scenarios=None,
        was_already_gherkin=False,
        knowledge_context=None,
        knowledge_filters=knowledge_filters,
        test_cases=None,
        coverage_summary=None,
        approval_status="DRAFT",
        revision_count=0,
        max_revisions=3,
        max_test_cases=max_test_cases,
        include_types=include_types,
        next_node="ado_reader",
        reviewer_comments=[],
        approval_log=[],
        ado_plan_id=None,
        ado_test_case_ids=None,
        token_usage={},
        audit_trail=[],
        started_at=datetime.utcnow().isoformat(),
        completed_at=None,
        error=None,
        error_agent=None,
    )
