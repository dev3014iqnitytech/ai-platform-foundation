"""
Knowledge Agent — Enterprise RAG retrieval agent.
Strategy: KB MCP server first (centralised, cached), direct RAG fallback.
Retrieves relevant organizational standards, templates, and guidelines.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from structlog import get_logger

from agents.base.base_agent import BaseAgent

if TYPE_CHECKING:
    from mcp.client import MCPClient

logger = get_logger(__name__)


class KnowledgeAgent(BaseAgent):
    """
    Knowledge Agent — MCP-first RAG retrieval.

    Pipeline (MCP path):
    1. Build targeted query from user story + Gherkin features
    2. Call knowledge_base MCP server → search tool
       (server owns: multi-query expansion, hybrid search, reranking, cache)

    Pipeline (fallback path — MCP unavailable):
    1. Build query
    2. EnterpriseRAGRetriever directly (same pipeline, in-process)
    """

    name = "knowledge"
    model = "gpt-4o-mini"

    def __init__(self, mcp_client: "MCPClient | None" = None):
        self._mcp: MCPClient | None = mcp_client

    async def _build_query(self, state: dict) -> str:
        """Build a focused retrieval query from story context."""
        user_story = state.get("user_story", {})
        gherkin = state.get("gherkin_scenarios", [])

        features = [s.get("feature", "") for s in gherkin[:3]]
        title = user_story.get("title", "")
        tags = user_story.get("tags", [])
        area_path = user_story.get("area_path", "")

        parts = [
            f"Testing standards and templates for: {title}",
            f"Area: {area_path}",
            f"Features: {', '.join(features)}",
        ]
        if tags:
            parts.append(f"Tags: {', '.join(tags)}")

        return " | ".join(parts)

    async def _execute(self, state: dict) -> dict:
        query = await self._build_query(state)
        knowledge_filters = state.get("knowledge_filters")

        knowledge_context = await self._retrieve(query, knowledge_filters)

        logger.info(
            "knowledge_retrieved",
            session_id=state.get("session_id"),
            chunks_retrieved=len(knowledge_context),
            query_preview=query[:100],
        )

        return {
            **state,
            "knowledge_context": knowledge_context,
            "next_node": "test_creation",
        }

    async def _retrieve(self, query: str, filters: dict | None) -> list[dict]:
        """MCP-first retrieval with direct RAG fallback."""
        from mcp.client import MCPToolError
        # if self._mcp is None:
        #             try:
        #                 self._mcp = await build_mcp_client()
        #                 self._mcp.ensure_healthy("knowledge_base")
        #             except Exception as e:
        #                 logger.warning("mcp_client_build_failed", reason=str(e))
        if self._mcp and self._mcp.has_server("knowledge_base"):
            try:
                result = await self._mcp.call_tool(
                    "knowledge_base",
                    "search",
                    {"query": query, "filters": filters, "top_k": 10},
                )
                logger.info("knowledge_via_mcp", chunks=len(result.get("chunks", [])))
                return result.get("chunks", [])
            except MCPToolError as e:
                logger.warning("knowledge_mcp_fallback", reason=str(e))

        return await self._retrieve_direct(query, filters)

    async def _retrieve_direct(self, query: str, filters: dict | None) -> list[dict]:
        """In-process RAG — used when KB MCP server is unavailable."""
        try:
            from rag.retrieval.hybrid_search import EnterpriseRAGRetriever
            docs = await EnterpriseRAGRetriever().retrieve(
                query=query, filters=filters, top_k=10
            )
            return [
                {
                    "content": doc.page_content,
                    "source": doc.metadata.get("source", "unknown"),
                    "category": doc.metadata.get("category", "general"),
                    "score": float(doc.metadata.get("relevance_score", 0.0)),
                }
                for doc in docs
            ]
        except Exception as e:
            logger.warning("knowledge_retrieval_failed", error=str(e))
            return []


    # def __init__(self):
    #     import base64
    #     from app.core.config import settings
    #     pat = settings.ADO_PAT.get_secret_value()
    #     encoded = base64.b64encode(f":{pat}".encode()).decode()
    #     self._headers = {
    #         "Authorization": f"Basic {encoded}",
    #         "Content-Type": "application/json-patch+json",
    #     }
    #     self._org = str(settings.ADO_ORGANIZATION)
    #     self._project = settings.ADO_PROJECT
    #     self._api_version = settings.ADO_API_VERSION

    # async def _execute(self, state: dict) -> dict:
    #     """Creates Test Plan → Suites → Cases in Azure DevOps."""
    #     import httpx

    #     session_id = state["session_id"]
    #     user_story = state.get("user_story", {})
    #     test_cases = state.get("test_cases", [])
    #     story_id = state.get("user_story_id")

    #     if state.get("approval_status") != "APPROVED":
    #         raise PermissionError(
    #             "ADO Update Agent cannot run without APPROVED status. "
    #             "This is a safety guardrail — never bypass it."
    #         )

    #     async with httpx.AsyncClient(timeout=60.0) as client:
    #         # 1. Create Test Plan
    #         plan_name = f"[EATAP] {story_id}: {user_story.get('title', '')[:80]}"
    #         plan_url = f"{self._org}/{self._project}/_apis/test/plans?api-version={self._api_version}"
    #         plan_response = await client.post(
    #             plan_url,
    #             headers=self._headers,
    #             json={"name": plan_name},
    #         )
    #         plan = plan_response.json()
    #         plan_id = plan.get("id")

    #         logger.info("ado_test_plan_created", plan_id=plan_id, session_id=session_id)

    #         # 2. Group test cases by type → one suite per type
    #         suites_created = {}
    #         ado_test_case_ids = []

    #         type_groups: dict[str, list] = {}
    #         for tc in test_cases:
    #             tc_type = tc.get("type", "functional")
    #             type_groups.setdefault(tc_type, []).append(tc)

    #         for suite_type, cases in type_groups.items():
    #             # Create suite
    #             suite_url = (
    #                 f"{self._org}/{self._project}/_apis/test/Plans/{plan_id}/suites"
    #                 f"?api-version={self._api_version}"
    #             )
    #             suite_resp = await client.post(
    #                 suite_url,
    #                 headers=self._headers,
    #                 json={
    #                     "suiteType": "StaticTestSuite",
    #                     "name": f"{suite_type.title()} Tests",
    #                 },
    #             )
    #             suite_id = suite_resp.json().get("id")
    #             suites_created[suite_type] = suite_id

    #             # Create test cases in ADO
    #             for tc in cases:
    #                 wi_url = (
    #                     f"{self._org}/{self._project}/_apis/wit/workitems"
    #                     f"/$Test Case?api-version={self._api_version}"
    #                 )
    #                 fields = [
    #                     {"op": "add", "path": "/fields/System.Title", "value": tc["title"]},
    #                     {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority",
    #                      "value": {"critical": 1, "high": 2, "medium": 3, "low": 4}.get(
    #                          tc.get("priority", "medium"), 3
    #                      )},
    #                     {"op": "add", "path": "/fields/System.Tags",
    #                      "value": "; ".join(tc.get("tags", []))},
    #                 ]
    #                 tc_resp = await client.post(wi_url, headers=self._headers, json=fields)
    #                 ado_tc_id = tc_resp.json().get("id")
    #                 ado_test_case_ids.append(ado_tc_id)

    #                 # Add to suite
    #                 add_tc_url = (
    #                     f"{self._org}/{self._project}/_apis/test/Plans/{plan_id}"
    #                     f"/Suites/{suite_id}/testcases/{ado_tc_id}"
    #                     f"?api-version={self._api_version}"
    #                 )
    #                 await client.post(add_tc_url, headers=self._headers)

    #     logger.info(
    #         "ado_update_completed",
    #         session_id=session_id,
    #         plan_id=plan_id,
    #         test_cases_created=len(ado_test_case_ids),
    #     )

    #     return {
    #         **state,
    #         "ado_plan_id": plan_id,
    #         "ado_test_case_ids": ado_test_case_ids,
    #         "approval_status": "PUBLISHED",
    #         "next_node": "__end__",
    #     }
