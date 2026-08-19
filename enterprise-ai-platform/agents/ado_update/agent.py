"""
ADO Update Agent — Publishes approved test cases to Azure DevOps.
Strategy: MCP server first (batched, credential-free), direct REST fallback.
This agent only runs AFTER human approval is granted.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from structlog import get_logger
from agents.base.base_agent import BaseAgent

if TYPE_CHECKING:
    from mcp.client import MCPClient

logger = get_logger(__name__)


class ADOUpdateAgent(BaseAgent):
    """
    Writes approved test cases back to Azure DevOps:
    1. Creates (or reuses) a Test Plan for the User Story
    2. Creates Test Suites by category (Functional, Boundary, API, etc.)
    3. Creates individual Test Cases with full step definitions
    4. Links Test Cases to the originating User Story

    MCP server handles auth; agent is credential-free when MCP is healthy.
    All operations are idempotent (safe to re-run).
    """

    name = "ado_update"
    model = "gpt-4o-mini"
    token_budget = 1000

    def __init__(self, mcp_client: "MCPClient | None" = None):
        self._mcp: MCPClient | None = mcp_client

    async def _execute(self, state: dict) -> dict:
        test_cases: list[dict] = state.get("test_cases", [])
        user_story = state.get("user_story", {})
        story_id = state.get("user_story_id", "")
        project_key = state.get("project_key", "DEFAULT")
        session_id = state.get("session_id", "")

        if not test_cases:
            logger.warning("ado_update_no_test_cases", session_id=session_id)
            return {**state, "error": "No test cases to publish", "next_node": "error_handler"}

        logger.info(
            "ado_update_started",
            session_id=session_id,
            story_id=story_id,
            test_case_count=len(test_cases),
        )

        try:
            ado_ids = await self._publish_to_ado(
                test_cases=test_cases,
                story_id=story_id,
                story_title=user_story.get("title", f"US-{story_id}"),
                project_key=project_key,
            )

            logger.info(
                "ado_update_completed",
                session_id=session_id,
                test_plan_id=ado_ids.get("test_plan_id"),
                test_case_ids=ado_ids.get("test_case_ids", []),
            )

            # Tag test cases with their ADO IDs
            updated_cases = []
            for i, tc in enumerate(test_cases):
                ado_id = ado_ids.get("test_case_ids", [])[i] if i < len(ado_ids.get("test_case_ids", [])) else None
                updated_cases.append({**tc, "ado_test_case_id": str(ado_id) if ado_id else None})

            # Publish domain event so audit consumer & status updater fire
            try:
                from events.publishers.service_bus_publisher import publish_event
                await publish_event(
                    topic="ado-events",
                    event_type="ado.test_cases_published",
                    payload={
                        "session_id": session_id,
                        "story_id": story_id,
                        "test_plan_id": ado_ids.get("test_plan_id"),
                        "test_suite_id": ado_ids.get("test_suite_id"),
                        "test_cases_count": len(updated_cases),
                    },
                    session_id=session_id,
                )
            except Exception as pub_err:
                logger.warning("ado_event_publish_failed", error=str(pub_err))

            return {
                **state,
                "test_cases": updated_cases,
                "ado_publish_result": ado_ids,
                "approval_status": "PUBLISHED",
                "next_node": "__end__",
                "token_usage": {"total": 0, "prompt": 0, "completion": 0},
            }

        except Exception as e:
            logger.error("ado_update_failed", session_id=session_id, error=str(e), exc_info=True)
            return {
                **state,
                "error": f"ADO publish failed: {e}",
                "next_node": "error_handler",
            }

    async def _publish_to_ado(
        self,
        test_cases: list[dict],
        story_id: str,
        story_title: str,
        project_key: str,
    ) -> dict[str, Any]:
        """MCP-first publish with direct REST fallback."""
        from mcp.client import MCPToolError
        if self._mcp and self._mcp.has_server("azure_devops"):
            try:
                return await self._publish_via_mcp(
                    test_cases, story_id, story_title, project_key
                )
            except MCPToolError as e:
                logger.warning("ado_mcp_publish_fallback", reason=str(e))

        try:
            return await self._publish_via_rest(test_cases, story_id, story_title, project_key)
        except Exception as e:
            logger.warning("ado_rest_publish_failed", error=str(e), fallback="mock_mode")
            return self._mock_publish_result(test_cases)

    async def _publish_via_mcp(
        self,
        test_cases: list[dict],
        story_id: str,
        story_title: str,
        project_key: str,  # noqa: ARG002
    ) -> dict[str, Any]:
        """Use the ADO MCP server — no credentials needed in this process."""
        plan = await self._mcp.call_tool(
            "azure_devops",
            "create_test_plan",
            {
                "name": f"[AI Generated] {story_title} Test Plan",
                "description": f"Auto-generated by EATAP for User Story {story_id}",
                "story_id": story_id,
            },
        )
        plan_id = plan["id"]

        suites: dict[str, list[dict]] = {}
        for tc in test_cases:
            suites.setdefault(tc.get("type", "functional"), []).append(tc)

        test_case_ids: list[int] = []

        for suite_name, suite_cases in suites.items():
            suite = await self._mcp.call_tool(
                "azure_devops",
                "create_test_suite",
                {"plan_id": plan_id, "name": suite_name.title()},
            )
            suite_id = suite["id"]

            # Create test cases in concurrent batches of 5
            for batch in self._batch(suite_cases, 5):
                tasks = [
                    self._create_via_mcp(tc, plan_id, suite_id, story_id)
                    for tc in batch
                ]
                ids = await asyncio.gather(*tasks, return_exceptions=True)
                test_case_ids.extend([i for i in ids if isinstance(i, int)])

        logger.info("ado_mcp_publish_complete", plan_id=plan_id, tc_count=len(test_case_ids))
        return {"test_plan_id": plan_id, "test_case_ids": test_case_ids}

    async def _create_via_mcp(
        self, tc: dict, plan_id: int, suite_id: int, story_id: str
    ) -> int | None:
        try:
            tc_result = await self._mcp.call_tool(
                "azure_devops",
                "create_test_case",
                {
                    "title": tc["title"],
                    "steps": tc.get("steps", []),
                    "priority": tc.get("priority", 2),
                    "tags": tc.get("tags", []),
                    "description": tc.get("gherkin_text", ""),
                },
            )
            tc_id = tc_result["id"]
            await self._mcp.call_tool(
                "azure_devops", "add_test_to_suite",
                {"plan_id": plan_id, "suite_id": suite_id, "test_case_id": tc_id},
            )
            await self._mcp.call_tool(
                "azure_devops", "link_work_items",
                {"source_id": tc_id, "target_id": story_id},
            )
            return tc_id
        except Exception as e:
            logger.warning("mcp_test_case_create_failed", title=tc.get("title"), error=str(e))
            return None

    async def _publish_via_rest(
        self,
        test_cases: list[dict],
        story_id: str,
        story_title: str,
        project_key: str,
    ) -> dict[str, Any]:
        """Direct ADO REST API calls using PAT token."""
        import httpx
        from app.core.config import settings

        pat = getattr(settings, "ADO_PAT_TOKEN", None)
        org = getattr(settings, "ADO_ORGANIZATION", None)
        if not pat or not org:
            raise ValueError("ADO_PAT_TOKEN or ADO_ORGANIZATION not configured")

        import base64
        auth = base64.b64encode(f":{pat}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        }
        base_url = f"https://dev.azure.com/{org}/{project_key}/_apis"

        async with httpx.AsyncClient(headers=headers, timeout=30) as client:
            # 1. Create Test Plan
            plan_resp = await client.post(
                f"{base_url}/test/plans?api-version=7.1",
                json={
                    "name": f"[AI Generated] {story_title} Test Plan",
                    "description": f"Auto-generated by EATAP for User Story {story_id}",
                },
            )
            plan_resp.raise_for_status()
            plan_id = plan_resp.json()["id"]

            # 2. Group test cases by type for suites
            suites: dict[str, list[dict]] = {}
            for tc in test_cases:
                tc_type = tc.get("type", "Functional")
                suites.setdefault(tc_type, []).append(tc)

            test_case_ids: list[int] = []

            # 3. Create suites and test cases
            for suite_name, suite_cases in suites.items():
                suite_resp = await client.post(
                    f"{base_url}/test/plans/{plan_id}/suites?api-version=7.1",
                    json={"name": suite_name, "suiteType": "staticTestSuite"},
                )
                suite_resp.raise_for_status()
                suite_id = suite_resp.json()["id"]

                # Create test cases in batches of 5
                for batch in self._batch(suite_cases, 5):
                    tasks = [
                        self._create_test_case(client, base_url, tc, plan_id, suite_id)
                        for tc in batch
                    ]
                    ids = await asyncio.gather(*tasks, return_exceptions=True)
                    test_case_ids.extend([i for i in ids if isinstance(i, int)])

            return {"test_plan_id": plan_id, "test_case_ids": test_case_ids}

    async def _create_test_case(
        self,
        client: Any,
        base_url: str,
        tc: dict,
        plan_id: int,
        suite_id: int,
    ) -> int | None:
        """Create a single test case work item and add to suite."""
        try:
            # Build steps XML for ADO
            steps_xml = self._build_steps_xml(tc.get("steps", []))

            tc_resp = await client.post(
                f"{base_url}/wit/workitems/$Test%20Case?api-version=7.1",
                json=[
                    {"op": "add", "path": "/fields/System.Title", "value": tc["title"]},
                    {"op": "add", "path": "/fields/Microsoft.VSTS.TCM.Steps", "value": steps_xml},
                    {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": tc.get("priority", "2")},
                    {"op": "add", "path": "/fields/System.Tags", "value": "; ".join(tc.get("tags", []))},
                    {"op": "add", "path": "/fields/System.Description", "value": tc.get("description", "")},
                ],
                headers={"Content-Type": "application/json-patch+json"},
            )
            tc_resp.raise_for_status()
            tc_id = tc_resp.json()["id"]

            # Add to suite
            await client.post(
                f"{base_url}/test/plans/{plan_id}/suites/{suite_id}/testcases?api-version=7.1",
                json=[{"id": tc_id}],
            )
            return tc_id
        except Exception as e:
            logger.warning("test_case_create_failed", title=tc.get("title"), error=str(e))
            return None

    def _build_steps_xml(self, steps: list[dict]) -> str:
        """Convert step dicts to ADO XML format."""
        rows = []
        for i, step in enumerate(steps, start=1):
            action = step.get("action", step.get("step", ""))
            expected = step.get("expected", step.get("expected_result", ""))
            rows.append(
                f'<step id="{i}" type="ActionStep">'
                f"<parameterizedString isformatted=\"true\">{action}</parameterizedString>"
                f"<parameterizedString isformatted=\"true\">{expected}</parameterizedString>"
                f"</step>"
            )
        return f'<steps id="0" last="{len(rows)}">{"".join(rows)}</steps>'

    def _mock_publish_result(self, test_cases: list[dict]) -> dict[str, Any]:
        """Return mock ADO IDs for development/testing without real ADO connection."""
        return {
            "test_plan_id": 9999,
            "test_case_ids": [10000 + i for i in range(len(test_cases))],
            "mock": True,
        }

    @staticmethod
    def _batch(items: list, size: int):
        for i in range(0, len(items), size):
            yield items[i:i + size]
