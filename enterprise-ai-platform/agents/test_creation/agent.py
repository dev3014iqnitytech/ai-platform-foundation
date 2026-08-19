"""
Test Creation Agent — Core reasoning agent.
Generates all test case types from Gherkin + RAG knowledge context.
Uses GPT-4o with JSON mode and structured Pydantic validation.
"""
from __future__ import annotations
from collections.abc import Mapping
from typing import Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from structlog import get_logger

from agents.base.base_agent import BaseAgent
from app.core.llm_factory import get_chat_llm
from app.core.llm_factory import get_openchatai_llm

logger = get_logger(__name__)

TEST_CREATION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are a Senior QA Architect with 20+ years experience designing enterprise test suites.
Generate comprehensive, production-ready test cases following:
- Organization testing standards and templates from the provided context
- BDD/Gherkin format for UI/functional tests
- Step-by-step format for API tests
- Proper naming conventions (TC-FUNCTIONAL-001, TC-NEGATIVE-001, etc.)
- IEEE 829 test case documentation standard

CRITICAL RULES:
- Generate ONLY from the provided Gherkin and context — no hallucination
- Every test case must be independently executable
- Include specific test data, not generic placeholders
- Mark regression candidates based on business criticality
- Incorporate reviewer feedback if provided

Return ONLY valid JSON — no markdown, no prose.""",
    ),
    (
        "human",
        """User Story: {story_title}
Story ID: {story_id}

Gherkin Scenarios:
{gherkin_scenarios}

RAG Knowledge Context (Organization Standards):
{knowledge_context}

Reviewer Feedback (if revision):
{reviewer_feedback}

Generate ALL of the following test case types:
1. Functional (positive happy-path)
2. Negative (invalid inputs, unauthorized access)
3. Boundary (min/max values, edge of valid range)
4. Edge Cases (unusual but valid scenarios)
5. Error Handling (system errors, timeouts, network failures)
6. API Test Cases (REST endpoints, request/response validation)
7. UI Test Cases (user interactions, visual elements)
8. Regression Candidates (flag tests critical for regression suite)
9. Smoke Candidates (flag minimal tests for smoke suite)
10. Integration Tests (cross-system, end-to-end flows)

Return JSON:
{{
  "test_cases": [
    {{
      "title": "TC-FUNCTIONAL-001: Verify successful login with valid credentials",
      "type": "functional",
      "priority": "critical",
      "tags": ["@smoke", "@regression"],
      "preconditions": "User account exists and is active",
      "gherkin_text": "Given... When... Then...",
      "steps": [
        {{
          "step_number": 1,
          "action": "Navigate to login page",
          "expected_result": "Login form is displayed",
          "test_data": "URL: https://app.example.com/login"
        }}
      ],
      "expected_result": "User is authenticated and redirected to dashboard",
      "is_regression_candidate": true,
      "is_smoke_candidate": true
    }}
  ],
  "total_count": 0,
  "coverage_summary": {{
    "functional": 0,
    "negative": 0,
    "boundary": 0,
    "edge_case": 0,
    "error_handling": 0,
    "api": 0,
    "ui": 0,
    "regression_candidates": 0,
    "smoke_candidates": 0,
    "integration": 0
  }}
}}

Generate up to {max_test_cases} test cases total. Prioritize quality over quantity.""",
    ),
])


class TestCreationAgent(BaseAgent):
    """
    Test Creation Agent — the primary value-generating agent.

    Combines:
    - Gherkin scenarios (from AC Analyzer)
    - RAG knowledge context (from Knowledge Agent)
    - Reviewer feedback (on revisions)
    - Structured JSON output with Pydantic validation
    """

    name = "test_creation"
    model = "gpt-4o"

    def __init__(self):
        self.llm = get_openchatai_llm(
            temperature=0.3,
            max_tokens=4000,
            json_mode=True,
        )
        
        self.chain = TEST_CREATION_PROMPT | self.llm | JsonOutputParser()


    async def _execute(self, state: Mapping[str, Any]) -> dict[str, Any]:
        user_story = state.get("user_story", {})
        gherkin_scenarios = state.get("gherkin_scenarios", [])
        knowledge_context = state.get("knowledge_context", [])
        reviewer_comments = state.get("reviewer_comments", [])
        max_test_cases = state.get("max_test_cases", 30)

        # Format knowledge context (top 5 most relevant chunks)
        formatted_context = "\n\n---\n\n".join([
            f"Source: {chunk.get('source', 'unknown')}\n{chunk.get('content', '')}"
            for chunk in knowledge_context[:5]
        ]) if knowledge_context else "No organization-specific standards loaded."

        # Format reviewer feedback for revisions
        reviewer_feedback = ""
        if reviewer_comments:
            reviewer_feedback = "\n".join([
                f"- {c.get('comment', '')}" for c in reviewer_comments[-3:]
            ])

        # Format Gherkin
        gherkin_text = "\n\n".join([
            f"Feature: {s.get('feature', '')}\n"
            f"  Scenario: {s.get('scenario', '')}\n"
            + "\n".join([f"    Given {g}" for g in s.get("given_steps", [])])
            + "\n"
            + "\n".join([f"    When {w}" for w in s.get("when_steps", [])])
            + "\n"
            + "\n".join([f"    Then {t}" for t in s.get("then_steps", [])])
            for s in gherkin_scenarios
        ])

        import json
        json_string = json.dumps(gherkin_text)

        result = await self.chain.ainvoke({
            "story_title": user_story.get("title", ""),
            "story_id": user_story.get("id", state.get("user_story_id")),
            "gherkin_scenarios": json_string or "No Gherkin scenarios available",
            "knowledge_context": formatted_context,
            "reviewer_feedback": reviewer_feedback or "First generation — no reviewer feedback",
            "max_test_cases": max_test_cases,
        })

        test_cases = result.get("test_cases", [])
        if not test_cases:
            raise ValueError("Test creation agent returned no test cases — retry required")

        logger.info(
            "test_cases_generated",
            session_id=state.get("session_id"),
            count=len(test_cases),
            coverage=result.get("coverage_summary", {}),
        )

        return {
            **state,
            "test_cases": test_cases,
            "coverage_summary": result.get("coverage_summary", {}),
            "revision_count": state.get("revision_count", 0) + 1,
            "next_node": "knowledge_enrichment",
        }
