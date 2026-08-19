"""
Acceptance Criteria Analyzer Agent.
Detects if AC is already Gherkin; if not, converts to proper BDD Gherkin format.
Uses GPT-4o for high-quality Gherkin generation.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any

import json
import re

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from structlog import get_logger

from agents.base.base_agent import BaseAgent
from app.core.llm_factory import get_chat_llm

import json as _json
from langchain_core.runnables import RunnableLambda

logger = get_logger(__name__)

GHERKIN_DETECTION_PATTERN = re.compile(
    r"^\s*(Feature:|Scenario:|Given |When |Then |And |But )",
    re.MULTILINE | re.IGNORECASE,
)

GHERKIN_CONVERSION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are an expert BDD/Gherkin writer with 15+ years in enterprise QA.
Convert acceptance criteria into proper Gherkin scenarios following:
- Industry-standard Feature/Scenario/Given/When/Then/And/But syntax
- One scenario per distinct behavior
- Use concrete examples (not abstract descriptions)
- Keep steps atomic and testable
- Include positive, negative, and boundary scenarios where applicable
- Use proper Gherkin tags (e.g., @smoke, @regression, @api)
Return ONLY valid JSON — no prose, no markdown code blocks.""",
    ),
    (
        "human",
        """User Story: {title}

Acceptance Criteria:
{acceptance_criteria}

Area Path: {area_path}
Tags: {tags}

Convert to Gherkin scenarios. Return JSON:
{{
  "was_already_gherkin": false,
  "scenarios": [
    {{
      "feature": "Feature name",
      "tags": ["@tag1", "@tag2"],
      "scenario": "Scenario title",
      "given_steps": ["the user is on the login page"],
      "when_steps": ["they enter valid credentials"],
      "then_steps": ["they should see the dashboard"],
      "and_steps": [],
      "but_steps": []
    }}
  ]
}}

Generate comprehensive scenarios covering:
1. Happy path (positive)
2. Invalid input (negative)
3. Boundary conditions
4. Error states""",
    ),
])

GHERKIN_PASSTHROUGH_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "Parse existing Gherkin into structured JSON. Extract all scenarios accurately.",
    ),
    (
        "human",
        """Parse this Gherkin into structured JSON:

{acceptance_criteria}

Return JSON with scenarios array (same schema as above) and set "was_already_gherkin": true.""",
    ),
])


class GherkinOutput(BaseModel):
    was_already_gherkin: bool = False
    scenarios: list[dict] = Field(default_factory=list)


class AcceptanceCriteriaAgent(BaseAgent):
    """
    Acceptance Criteria Analyzer Agent.

    Decision logic:
    1. Detect if AC is already Gherkin → parse and return (cheaper path)
    2. If not → convert to Gherkin using GPT-4o (full reasoning path)
    """

    name = "acceptance_criteria"
    model = "gemini-3.1-pro-preview"

    def __init__(self):
        self.llm = get_chat_llm(
            temperature=0.2,
            max_tokens=2000,
            # json_mode=True,
        )

        def _parse(output) -> dict:
            return self._strip_json_fences(output)
        
        self.conversion_chain = GHERKIN_CONVERSION_PROMPT | self.llm | RunnableLambda(_parse)
        self.parse_chain     = GHERKIN_PASSTHROUGH_PROMPT | self.llm | RunnableLambda(_parse)
    
    # def _strip_json_fences(self, text: str | dict) -> dict:
    #     # Some providers with json_mode return a parsed dict directly
    #     if isinstance(text, dict):
    #         return text
    #     logger.debug("raw_llm_output", text=text)
    #     stripped = re.sub(r"^```(?:json)?\s*", "", text.strip())
    #     stripped = re.sub(r"\s*```$", "", stripped.strip())
    #     if not stripped:
    #         raise ValueError("LLM returned an empty response — cannot parse JSON")
    #     logger.debug("stripped_json_fences", text=stripped)
    #     return json.loads(stripped)
    def _strip_json_fences(self, text: str | dict) -> dict:
        if isinstance(text, dict):
            return text
        if not isinstance(text, str):
            content = getattr(text, "content", None)
            text = content if isinstance(content, str) else str(text)
        logger.debug("raw_llm_output", text=text)
        # Extract the outermost JSON object regardless of surrounding prose or fences
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object in LLM response: {text[:300]!r}")
        return json.loads(match.group())
    
    def _is_gherkin(self, text: str) -> bool:
        return bool(GHERKIN_DETECTION_PATTERN.search(text or ""))

    async def _execute(self, state: Mapping[str, Any]) -> dict[str, Any]:
        user_story = state.get("user_story", {})
        ac = user_story.get("acceptance_criteria") or ""
        title = user_story.get("title", "")
        area_path = user_story.get("area_path", "")
        tags = ", ".join(user_story.get("tags", []))

        if not ac.strip():
            logger.warning(
                "no_acceptance_criteria",
                story_id=state.get("user_story_id"),
            )
            # Generate minimal Gherkin from title/description
            ac = f"As a user, I want {title} so that the feature works as expected."

        revision_count = state.get("revision_count", 0)
        reviewer_feedback = ""
        if revision_count > 0 and state.get("reviewer_comments"):
            last_comment = state["reviewer_comments"][-1]
            reviewer_feedback = f"\n\nReviewer feedback: {last_comment.get('comment', '')}"

        if self._is_gherkin(ac):
            logger.info("ac_already_gherkin", story_id=state.get("user_story_id"))
            result = await self.parse_chain.ainvoke(
                {"acceptance_criteria": ac}
            )
        else:
            try:
                result = await self.conversion_chain.ainvoke({
                    "title": title + reviewer_feedback,
                    "acceptance_criteria": ac,
                    "area_path": area_path,
                    "tags": tags,
                })
            except Exception as e:
                logger.error(
                    "gherkin_conversion_failed",
                    story_id=state.get("user_story_id"),
                    error=str(e),
                )
                raise
            # result = await self.conversion_chain.ainvoke({
            #     "title": title + reviewer_feedback,
            #     "acceptance_criteria": ac,
            #     "area_path": area_path,
            #     "tags": tags,
            # })

        scenarios = result.get("scenarios", [])
        if not scenarios:
            raise ValueError("Gherkin generation produced no scenarios — retry required")

        return {
            **state,
            "gherkin_scenarios": scenarios,
            "was_already_gherkin": result.get("was_already_gherkin", False),
            "next_node": "test_creation",
        }
