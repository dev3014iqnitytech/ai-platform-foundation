"""
LangGraph Platform Graph — Wires all agents into a stateful directed graph.
Includes conditional routing, human-in-the-loop interruption, and PostgreSQL checkpointing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, StateGraph
from structlog import get_logger

from workflows.langgraph.state import PlatformState, initial_state

if TYPE_CHECKING:
    from mcp.client import MCPClient

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Node Factories — close over the shared MCPClient so agents are credential-free
# One MCPClient instance is built per workflow run and shared across all nodes.
# ─────────────────────────────────────────────────────────────────────────────

def _make_nodes(mcp: "MCPClient"):
    """Returns a dict of node functions with the MCPClient baked in via closure."""

    async def intent_router_node(state: PlatformState) -> PlatformState:
        from agents.intent_router.router import IntentRouter
        return await IntentRouter().run(state)

    async def ado_reader_node(state: PlatformState) -> PlatformState:
        from agents.ado_reader.agent import ADOReaderAgent
        result = await ADOReaderAgent(mcp_client=mcp).run(state)
        await _persist_story(state["session_id"], result.get("user_story"))
        return result

    async def ac_analyzer_node(state: PlatformState) -> PlatformState:
        from agents.acceptance_criteria.agent import AcceptanceCriteriaAgent
        return await AcceptanceCriteriaAgent().run(state)

    async def knowledge_enrichment_node(state: PlatformState) -> PlatformState:
        from agents.knowledge.agent import KnowledgeAgent
        return await KnowledgeAgent(mcp_client=mcp).run(state)

    async def test_creation_node(state: PlatformState) -> PlatformState:
        from agents.test_creation.agent import TestCreationAgent
        result = await TestCreationAgent().run(state)
        await _persist_test_cases(state["session_id"], result.get("test_cases", []))
        return result

    async def ado_update_node(state: PlatformState) -> PlatformState:
        from agents.ado_update.agent import ADOUpdateAgent
        result = await ADOUpdateAgent(mcp_client=mcp).run(state)
        await _update_session_status(state["session_id"], "PUBLISHED")
        return result

    return {
        "intent_router":        intent_router_node,
        "ado_reader":           ado_reader_node,
        "ac_analyzer":          ac_analyzer_node,
        "knowledge_enrichment": knowledge_enrichment_node,
        "test_creation":        test_creation_node,
        "ado_update":           ado_update_node,
    }


async def output_validation_node(state: PlatformState) -> PlatformState:
    """Validates test case schema and sets status to IN_REVIEW if valid."""
    test_cases = state.get("test_cases", [])
    errors = []

    for i, tc in enumerate(test_cases):
        if not tc.get("title"):
            errors.append(f"Test case {i}: missing title")
        if not tc.get("type"):
            errors.append(f"Test case {i}: missing type")
        if not tc.get("steps") and not tc.get("gherkin_text"):
            errors.append(f"Test case {i}: missing steps or gherkin_text")

    if errors:
        logger.warning("output_validation_failed", errors=errors[:5])
        return {
            **state,
            "error": f"Validation failed: {'; '.join(errors[:3])}",
            "next_node": "test_creation",
        }

    # Move to approval queue
    await _update_session_status(state["session_id"], "IN_REVIEW")
    await _notify_reviewers(state["session_id"], state.get("user_story_id"))

    return {
        **state,
        "approval_status": "IN_REVIEW",
        "error": None,
        "next_node": "approval_queue",
    }


async def approval_queue_node(state: PlatformState) -> PlatformState:
    """
    INTERRUPT NODE — LangGraph pauses here for human review.
    When the API receives approve/reject, the graph resumes from this checkpoint.
    """
    logger.info(
        "approval_queue_reached",
        session_id=state["session_id"],
        test_case_count=len(state.get("test_cases", [])),
    )
    # Graph is interrupted here (interrupt_before=["approval_queue"])
    return {**state, "next_node": "approval_queue"}


async def error_handler_node(state: PlatformState) -> PlatformState:
    """Handles agent failures — logs and gracefully terminates."""
    logger.error(
        "workflow_error",
        session_id=state.get("session_id"),
        error=state.get("error"),
        agent=state.get("error_agent"),
    )
    await _update_session_status(state.get("session_id", ""), "DRAFT")
    return {**state, "next_node": "__end__"}


# ─────────────────────────────────────────────────────────────────────────────
# Conditional Edge Functions
# ─────────────────────────────────────────────────────────────────────────────

def route_after_ac(state: PlatformState) -> str:
    if state.get("error"):
        revision = state.get("revision_count", 0)
        return "ac_analyzer" if revision < 3 else "error_handler"
    return "knowledge_enrichment"


def route_after_test_creation(state: PlatformState) -> str:
    if state.get("error"):
        revision = state.get("revision_count", 0)
        return "test_creation" if revision < state.get("max_revisions", 3) else "error_handler"
    return "output_validation"


def route_after_validation(state: PlatformState) -> str:
    if state.get("error"):
        return "test_creation"
    return "approval_queue"


def route_after_approval(state: PlatformState) -> str:
    status = state.get("approval_status", "")
    if status == "APPROVED":
        return "ado_update"
    if status == "REJECTED":
        # Reset for revision
        return "test_creation"
    return END


# ─────────────────────────────────────────────────────────────────────────────
# Graph Builder
# ─────────────────────────────────────────────────────────────────────────────

def build_platform_graph(
    checkpointer=None,
    mcp: "MCPClient | None" = None,
) -> StateGraph:
    # Build node closures — each agent receives the shared MCPClient
    nodes = _make_nodes(mcp)

    graph = StateGraph(PlatformState)

    # Register agent nodes (MCP-injected via factory)
    for name, fn in nodes.items():
        graph.add_node(name, fn)

    # Register stateless inline nodes
    graph.add_node("output_validation", output_validation_node)
    graph.add_node("approval_queue", approval_queue_node)
    graph.add_node("error_handler", error_handler_node)

    # Entry point
    graph.set_entry_point("intent_router")

    # Fixed edges
    graph.add_edge("intent_router", "ado_reader")
    graph.add_edge("ado_reader", "ac_analyzer")
    graph.add_edge("knowledge_enrichment", "test_creation")
    graph.add_edge("ado_update", END)
    graph.add_edge("error_handler", END)

    # Conditional edges
    graph.add_conditional_edges("ac_analyzer", route_after_ac, {
        "ac_analyzer": "ac_analyzer",
        "knowledge_enrichment": "knowledge_enrichment",
        "error_handler": "error_handler",
    })
    graph.add_conditional_edges("test_creation", route_after_test_creation, {
        "test_creation": "test_creation",
        "output_validation": "output_validation",
        "error_handler": "error_handler",
    })
    graph.add_conditional_edges("output_validation", route_after_validation, {
        "test_creation": "test_creation",
        "approval_queue": "approval_queue",
    })
    graph.add_conditional_edges("approval_queue", route_after_approval, {
        "ado_update": "ado_update",
        "test_creation": "test_creation",
        END: END,
    })

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["approval_queue"],  # Human-in-the-loop pause point
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API — run the full workflow
# ─────────────────────────────────────────────────────────────────────────────

async def run_platform_workflow(
    session_id: str,
    user_story_id: str,
    user_id: str = "system",
    project_key: str = "DEFAULT",
    max_test_cases: int = 30,
    include_types: list | None = None,
    knowledge_filters: dict | None = None,
) -> dict:
    """
    Entry point for the full test generation workflow.
    Builds a shared MCPClient, runs until the approval_queue interrupt, then returns.
    The graph resumes when /api/v1/approvals/review is called.
    """
    from mcp.client import build_mcp_client
    from workflows.langgraph.checkpointer import get_postgres_checkpointer

    # One MCPClient per workflow run — shared across all nodes, closed on exit
    mcp = await build_mcp_client()
    try:
        checkpointer = await get_postgres_checkpointer()
        graph = build_platform_graph(checkpointer=checkpointer, mcp=mcp)

        state = initial_state(
            session_id=session_id,
            user_id=user_id,
            user_story_id=user_story_id,
            project_key=project_key,
            max_test_cases=max_test_cases,
            knowledge_filters=knowledge_filters,
            include_types=include_types,
        )

        config = {"configurable": {"thread_id": session_id}}
        final_state = await graph.ainvoke(state, config=config)
        logger.info(
            "workflow_paused_for_review",
            session_id=session_id,
            test_cases=len(final_state.get("test_cases", [])),
        )
        return final_state
    finally:
        await mcp.close()


async def resume_after_approval(
    session_id: str,
    approval_status: str,
    reviewer_comment: str | None = None,
) -> dict:
    """
    Resumes the paused graph after human approval/rejection.
    Called by the approvals API endpoint.
    """
    from mcp.client import build_mcp_client
    from workflows.langgraph.checkpointer import get_postgres_checkpointer

    mcp = await build_mcp_client()
    try:
        checkpointer = await get_postgres_checkpointer()
        graph = build_platform_graph(checkpointer=checkpointer, mcp=mcp)
        config = {"configurable": {"thread_id": session_id}}

        current = await graph.aget_state(config)
        updated: dict = {**current.values, "approval_status": approval_status}

        # Inject reviewer feedback so test_creation can act on it if rejected
        if reviewer_comment and approval_status == "REJECTED":
            existing = list(updated.get("reviewer_comments") or [])
            existing.append({"comment": reviewer_comment, "source": "human"})
            updated["reviewer_comments"] = existing

        await graph.aupdate_state(config, updated)
        final = await graph.ainvoke(None, config=config)
        return final
    finally:
        await mcp.close()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — DB persistence within graph nodes
# ─────────────────────────────────────────────────────────────────────────────

async def _persist_story(session_id: str, story: dict | None) -> None:
    if not story:
        return
    try:
        from app.infrastructure.database.session import async_session_factory
        from app.infrastructure.database.repositories import SessionRepository
        import uuid
        async with async_session_factory() as db:
            repo = SessionRepository(db)
            await repo.update_data(
                uuid.UUID(session_id), user_story_data=story
            )
            await db.commit()
    except Exception as e:
        logger.warning("persist_story_failed", error=str(e))


async def _persist_test_cases(session_id: str, test_cases: list) -> None:
    if not test_cases:
        return
    try:
        from app.infrastructure.database.session import async_session_factory
        from app.infrastructure.database.repositories import TestCaseRepository
        import uuid
        async with async_session_factory() as db:
            repo = TestCaseRepository(db)
            await repo.delete_by_session(uuid.UUID(session_id))
            await repo.bulk_create(uuid.UUID(session_id), test_cases)
            await db.commit()
    except Exception as e:
        logger.warning("persist_test_cases_failed", error=str(e))


async def _update_session_status(session_id: str, status: str) -> None:
    if not session_id:
        return
    try:
        from app.infrastructure.database.session import async_session_factory
        from app.infrastructure.database.repositories import SessionRepository
        import uuid
        async with async_session_factory() as db:
            repo = SessionRepository(db)
            await repo.update_status(uuid.UUID(session_id), status)
            await db.commit()
    except Exception as e:
        logger.warning("update_session_status_failed", error=str(e))


async def _notify_reviewers(session_id: str, story_id: str | None) -> None:
    try:
        from events.publishers.service_bus_publisher import publish_event
        await publish_event(
            topic="review-events",
            event_type="review.requested",
            payload={"session_id": session_id, "story_id": story_id},
        )
    except Exception as e:
        logger.warning("notify_reviewers_failed", error=str(e))
