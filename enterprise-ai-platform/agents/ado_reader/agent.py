"""
ADO Reader Agent — Fetches Azure DevOps User Stories.
Strategy: MCP server first (credential-free), direct REST fallback.
Uses GPT-4o-mini (cheap) since work is primarily API orchestration, not reasoning.
"""
from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from structlog import get_logger

from agents.base.base_agent import BaseAgent
from app.core.llm_factory import get_mini_llm

if TYPE_CHECKING:
    from mcp.client import MCPClient

logger = get_logger(__name__)

# Only fetch these fields — minimizes token consumption
REQUIRED_ADO_FIELDS = [
    "System.Id",
    "System.Title",
    "System.Description",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
    "System.AreaPath",
    "System.Tags",
    "System.State",
    "System.WorkItemType",
    "System.TeamProject",
]


class UserStoryData(BaseModel):
    id: str
    title: str
    description: str | None = None
    acceptance_criteria: str | None = None
    area_path: str | None = None
    tags: list[str] = Field(default_factory=list)
    state: str | None = None
    work_item_type: str | None = None
    linked_items: list[dict] = Field(default_factory=list)
    existing_test_cases: list[dict] = Field(default_factory=list)


PARSE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are a structured data extractor. Extract Azure DevOps work item data into JSON.
Be precise. Return only valid JSON. Do not add fields not present in the source.
Never fabricate acceptance criteria — if missing, return null.""",
    ),
    (
        "human",
        """Extract the following Azure DevOps work item data into structured JSON:

Raw ADO Data:
{raw_story}

Return JSON matching this schema:
{{
  "id": "string",
  "title": "string",
  "description": "string or null",
  "acceptance_criteria": "string or null",
  "area_path": "string or null",
  "tags": ["list", "of", "strings"],
  "state": "string or null",
  "work_item_type": "string or null",
  "linked_items": [],
  "existing_test_cases": []
}}""",
    ),
])


class ADOReaderAgent(BaseAgent):
    """
    ADO Reader Agent — MCP-first, direct REST fallback.

    Token optimization:
    - Uses GPT-4o-mini (not GPT-4o) — parsing only, not reasoning
    - Fetches only REQUIRED_ADO_FIELDS — no full work item dumps
    - PII detection before sending to LLM
    """

    name = "ado_reader"
    # model = "gpt-4o-mini"
    model = "gemini-3.1-pro-preview"

    def __init__(self, mcp_client: "MCPClient | None" = None):
        self.llm = get_mini_llm(temperature=0.0, max_tokens=1000)
        self.chain = PARSE_PROMPT | self.llm | JsonOutputParser()
        self._mcp: MCPClient | None = mcp_client
        self._ado_headers: dict | None = None

    def _get_ado_headers(self) -> dict:
        """Build Azure DevOps PAT auth headers."""
        if not self._ado_headers:
            from app.core.config import settings
            if not settings.ADO_PAT:
                raise RuntimeError(
                    "ADO_PAT is not configured. Set ADO_PAT in your .env file "
                    "or use LOCAL_MODE with a mock story ID."
                )
            pat = settings.ADO_PAT.get_secret_value()
            encoded = base64.b64encode(f":{pat}".encode()).decode()
            self._ado_headers = {
                "Authorization": f"Bearer {pat}",
                "Content-Type": "application/json",
            }
        return self._ado_headers

    async def fetch_story(self, story_id: str) -> dict:
        """Public method for direct API calls (non-LangGraph usage)."""
        state = {"session_id": "direct", "user_story_id": story_id}
        result = await self.run(state)
        return result.get("user_story", {})

    async def _execute(self, state: dict) -> dict:
        story_id = state["user_story_id"]

        # ── Fetch via MCP server (preferred) or direct REST (fallback) ──
        raw_data = await self._fetch_raw(story_id)

        # ── PII Sanitization ──
        try:
            from security.pii_detector import PIIDetector
            sanitized_text = PIIDetector().anonymize(str(raw_data))
        except Exception:
            sanitized_text = str(raw_data)

        # ── Parse and structure with mini-LLM ──
        parsed = await self.chain.ainvoke({"raw_story": sanitized_text})

        relations = raw_data.get("relations", [])
        test_case_links = [
            r for r in relations if "Test" in r.get("attributes", {}).get("name", "")
        ]
        parsed["existing_test_cases"] = test_case_links[:10]

        return {
            **state,
            "user_story": parsed,
            "token_usage": {
                "ado_reader": {
                    "model": self.model,
                    "estimated_prompt_tokens": len(sanitized_text) // 4,
                }
            },
            "next_node": "ac_analyzer",
        }

    async def _fetch_raw(self, story_id: str) -> dict:
        """MCP-first fetch with direct REST fallback."""
        from mcp.client import build_mcp_client, MCPToolError
        if self._mcp is None:
            try:
                self._mcp = await build_mcp_client()
                self._mcp.ensure_healthy("azure_devops")
            except Exception as e:
                logger.warning("mcp_client_build_failed", reason=str(e))
        if self._mcp and self._mcp.has_server("azure_devops"):
            try:
                result = await self._mcp.call_tool(
                    "azure_devops",
                    "get_work_item",
                    {
                        "id": story_id,
                        "fields": REQUIRED_ADO_FIELDS,
                    },
                )
                logger.info("ado_reader_via_mcp", story_id=story_id)
                return result
            except MCPToolError as e:
                logger.warning("ado_mcp_fallback_to_rest", story_id=story_id, reason=str(e))

        return await self._fetch_via_rest(story_id)

    async def _fetch_via_rest(self, story_id: str) -> dict:
        """Direct ADO REST call — used when MCP server is unavailable.

        ADO API does not allow `fields` and `$expand` in the same request,
        so two calls are made and merged.
        """
        import urllib.parse
        import httpx
        from app.core.config import settings

        org = str(settings.ADO_ORGANIZATION).rstrip("/")
        project = urllib.parse.quote(
            urllib.parse.unquote(settings.ADO_PROJECT or ""), safe=""
        )
        base = f"{org}/{project}/_apis/wit/workitems/{story_id}"
        api_ver = f"api-version={settings.ADO_API_VERSION}"
        headers = self._get_ado_headers()

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Call 1: selected fields
            fields_url = f"{base}?fields={','.join(REQUIRED_ADO_FIELDS)}&{api_ver}"
            r1 = await client.get(fields_url, headers=headers)
            if r1.status_code == 404:
                raise ValueError(f"User Story {story_id} not found in Azure DevOps")
            if r1.status_code != 200:
                raise RuntimeError(f"ADO API error {r1.status_code}: {r1.text[:200]}")

            # Call 2: relations only
            relations_url = f"{base}?$expand=relations&{api_ver}"
            r2 = await client.get(relations_url, headers=headers)

        data = r1.json()
        if r2.status_code == 200:
            data["relations"] = r2.json().get("relations", [])
        else:
            data["relations"] = []

        return data