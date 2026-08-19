"""
Unit tests for LangGraph conditional edge routing functions.
No LLMs, no DB, no MCP — pure routing logic only.
"""
from __future__ import annotations

import pytest
from langgraph.graph import END

from workflows.langgraph.graph import (
    route_after_ac,
    route_after_approval,
    route_after_test_creation,
    route_after_validation,
)


# ─────────────────────────────────────────────────────────────────────────────
# route_after_ac
# ─────────────────────────────────────────────────────────────────────────────

def test_route_after_ac_happy_path():
    state = {"error": None}
    assert route_after_ac(state) == "knowledge_enrichment"


def test_route_after_ac_retries_on_first_error():
    state = {"error": "Gherkin parse failed", "revision_count": 0}
    assert route_after_ac(state) == "ac_analyzer"


def test_route_after_ac_retries_on_second_error():
    state = {"error": "Still failing", "revision_count": 2}
    assert route_after_ac(state) == "ac_analyzer"


def test_route_after_ac_escalates_after_three_failures():
    state = {"error": "Still failing", "revision_count": 3}
    assert route_after_ac(state) == "error_handler"


def test_route_after_ac_missing_error_key():
    # total=False TypedDict means missing keys should behave like None
    assert route_after_ac({}) == "knowledge_enrichment"


# ─────────────────────────────────────────────────────────────────────────────
# route_after_test_creation
# ─────────────────────────────────────────────────────────────────────────────

def test_route_after_test_creation_happy_path():
    state = {"error": None}
    assert route_after_test_creation(state) == "output_validation"


def test_route_after_test_creation_retries_within_budget():
    state = {"error": "No test cases returned", "revision_count": 1, "max_revisions": 3}
    assert route_after_test_creation(state) == "test_creation"


def test_route_after_test_creation_escalates_at_max():
    state = {"error": "Persistent failure", "revision_count": 3, "max_revisions": 3}
    assert route_after_test_creation(state) == "error_handler"


def test_route_after_test_creation_uses_default_max_revisions():
    # max_revisions defaults to 3 inside the function when key missing
    state = {"error": "Fail", "revision_count": 3}
    assert route_after_test_creation(state) == "error_handler"


def test_route_after_test_creation_retries_before_default_max():
    state = {"error": "Fail", "revision_count": 2}
    assert route_after_test_creation(state) == "test_creation"


# ─────────────────────────────────────────────────────────────────────────────
# route_after_validation
# ─────────────────────────────────────────────────────────────────────────────

def test_route_after_validation_happy_path():
    state = {"error": None}
    assert route_after_validation(state) == "approval_queue"


def test_route_after_validation_sends_back_on_error():
    state = {"error": "Missing titles on 3 test cases"}
    assert route_after_validation(state) == "test_creation"


def test_route_after_validation_empty_error_string_treated_as_no_error():
    # Empty string is falsy in Python — treated as no error
    state = {"error": ""}
    assert route_after_validation(state) == "approval_queue"


# ─────────────────────────────────────────────────────────────────────────────
# route_after_approval
# ─────────────────────────────────────────────────────────────────────────────

def test_route_after_approval_approved():
    state = {"approval_status": "APPROVED"}
    assert route_after_approval(state) == "ado_update"


def test_route_after_approval_rejected_sends_to_test_creation():
    state = {"approval_status": "REJECTED"}
    assert route_after_approval(state) == "test_creation"


def test_route_after_approval_unknown_status_ends():
    state = {"approval_status": "DRAFT"}
    assert route_after_approval(state) == END


def test_route_after_approval_missing_status_ends():
    assert route_after_approval({}) == END


def test_route_after_approval_in_review_ends():
    # IN_REVIEW means the interrupt fired but approval_status wasn't updated —
    # should fall through to END to avoid infinite loop
    state = {"approval_status": "IN_REVIEW"}
    assert route_after_approval(state) == END


# ─────────────────────────────────────────────────────────────────────────────
# Revision budget boundary cases
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("revision,expected", [
    (0, "ac_analyzer"),
    (1, "ac_analyzer"),
    (2, "ac_analyzer"),
    (3, "error_handler"),
    (10, "error_handler"),
])
def test_ac_retry_budget_parametrized(revision, expected):
    state = {"error": "fail", "revision_count": revision}
    assert route_after_ac(state) == expected


@pytest.mark.parametrize("revision,max_rev,expected", [
    (0, 3, "test_creation"),
    (2, 3, "test_creation"),
    (3, 3, "error_handler"),
    (0, 1, "test_creation"),
    (1, 1, "error_handler"),
])
def test_test_creation_retry_budget_parametrized(revision, max_rev, expected):
    state = {"error": "fail", "revision_count": revision, "max_revisions": max_rev}
    assert route_after_test_creation(state) == expected
