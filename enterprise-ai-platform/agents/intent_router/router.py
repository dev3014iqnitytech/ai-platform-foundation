"""
Intent Router — Zero-shot classifier that routes user intent to the right agent.
Uses GPT-4o-mini to avoid expensive model calls for simple routing.
This is the entry point for all conversational/chat interactions.
"""
from __future__ import annotations

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from structlog import get_logger

from agents.base.base_agent import BaseAgent
from app.core.llm_factory import get_mini_llm

logger = get_logger(__name__)

ROUTING_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are an intent classifier for an Enterprise AI Test Automation Platform.
Classify the user's intent into EXACTLY ONE of these routes.
Return JSON only — no prose.

Routes and their keywords:
- "ado_reader": fetch story, user story, work item, ADO, azure devops, US-XXXX
- "acceptance_criteria": gherkin, BDD, acceptance criteria, feature, scenario, given when then
- "test_creation": generate tests, test cases, regression, smoke, functional, api testing, ui testing
- "knowledge": standards, templates, guidelines, qa documents, policies, naming
- "approval": approve, reject, review, tester, queue, pending
- "ado_update": publish, sync, update ADO, create test plan, push to devops
- "unknown": anything else""",
    ),
    (
        "human",
        """User message: {user_message}

Context (current workflow state): {context}

Return JSON: {{"route": "route_name", "confidence": 0.95, "reason": "brief reason"}}""",
    ),
])


class IntentRouter(BaseAgent):
    """
    Intent Router — routes to the appropriate agent with minimal token cost.

    Design principles:
    - GPT-4o-mini only (50-token output max)
    - Falls back to keyword matching if LLM fails (zero-cost fallback)
    - Never loads context that isn't needed
    """

    name = "intent_router"
    model = "gpt-4o-mini"

    KEYWORD_MAP = {
        "ado_reader": [
            "user story", "work item", "us-", "fetch story", "azure devops story",
        ],
        "acceptance_criteria": [
            "gherkin", "bdd", "given", "when then", "acceptance criteria",
            "feature:", "scenario:",
        ],
        "test_creation": [
            "generate test", "test case", "regression", "smoke test", "functional test",
            "api test", "ui test", "edge case",
        ],
        "knowledge": [
            "standard", "template", "guideline", "policy", "qa document",
            "naming convention", "checklist",
        ],
        "approval": [
            "approve", "reject", "review", "pending", "queue",
        ],
        "ado_update": [
            "publish", "sync", "update ado", "push to", "create test plan",
        ],
    }

    def __init__(self):
        self.llm = get_mini_llm(
            temperature=0.0,
            max_tokens=60,
            json_mode=True,
        )
        self.chain = ROUTING_PROMPT | self.llm | JsonOutputParser()

    def _keyword_route(self, message: str) -> str | None:
        """Zero-cost fallback routing using keyword matching."""
        lower = message.lower()
        for route, keywords in self.KEYWORD_MAP.items():
            if any(kw in lower for kw in keywords):
                return route
        return None

    async def route(self, user_message: str, context: str = "") -> str:
        """Returns the route name for a given user message."""
        # Try keyword match first (free)
        keyword_route = self._keyword_route(user_message)

        # Only call LLM if keyword match is ambiguous
        if not keyword_route:
            try:
                result = await self.chain.ainvoke({
                    "user_message": user_message,
                    "context": context or "No prior context",
                })
                return result.get("route", "test_creation")
            except Exception as e:
                logger.warning("intent_router_llm_failed", error=str(e))
                return "test_creation"

        return keyword_route

    async def _execute(self, state: dict) -> dict:
        """LangGraph node execution."""
        # In workflow mode, routing is determined by state, not user message
        # This node validates state and determines initial route
        if state.get("user_story_id") and not state.get("user_story"):
            next_node = "ado_reader"
        elif state.get("user_story") and not state.get("gherkin_scenarios"):
            next_node = "ac_analyzer"
        elif state.get("gherkin_scenarios") and not state.get("knowledge_context"):
            next_node = "knowledge_enrichment"
        elif state.get("knowledge_context") and not state.get("test_cases"):
            next_node = "test_creation"
        else:
            next_node = "ado_reader"

        return {**state, "next_node": next_node}
