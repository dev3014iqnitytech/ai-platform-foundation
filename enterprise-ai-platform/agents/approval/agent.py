"""
Approval Agent — Manages workflow state transitions for the human review process.
Reads current approval status from state and routes accordingly.
"""
from __future__ import annotations

from structlog import get_logger
from agents.base.base_agent import BaseAgent

logger = get_logger(__name__)


class ApprovalAgent(BaseAgent):
    """
    Manages approval workflow state:
    - Validates that approval_status is a valid transition
    - Records reviewer decision and comments in audit trail
    - Routes to ADO update (APPROVED) or Test Creation (REJECTED)
    """

    name = "approval"
    model = "gpt-4o-mini"
    token_budget = 200  # Minimal LLM use — mostly state management

    VALID_STATUSES = {"APPROVED", "REJECTED", "IN_REVIEW"}

    async def _execute(self, state: dict) -> dict:
        approval_status = state.get("approval_status", "")
        session_id = state.get("session_id", "")
        reviewer_comments = state.get("reviewer_comments", [])

        if approval_status not in self.VALID_STATUSES:
            logger.warning(
                "approval_invalid_status",
                status=approval_status,
                session_id=session_id,
            )
            return {
                **state,
                "error": f"Invalid approval status: {approval_status}",
                "next_node": "error_handler",
            }

        # Add audit entry
        audit_entry = {
            "event": f"approval.{approval_status.lower()}",
            "session_id": session_id,
            "comment_count": len(reviewer_comments),
            "agent": self.name,
        }

        audit_trail = list(state.get("audit_trail", []))
        audit_trail.append(audit_entry)

        logger.info(
            "approval_decision_recorded",
            status=approval_status,
            session_id=session_id,
            comments=len(reviewer_comments),
        )

        # Route based on decision
        if approval_status == "APPROVED":
            next_node = "ado_update"
        elif approval_status == "REJECTED":
            # Increment revision count for re-generation
            revision_count = state.get("revision_count", 0) + 1
            return {
                **state,
                "audit_trail": audit_trail,
                "revision_count": revision_count,
                "next_node": "test_creation",
                "token_usage": {"total": 0, "prompt": 0, "completion": 0},
            }
        else:
            next_node = "approval_queue"  # Still waiting

        return {
            **state,
            "audit_trail": audit_trail,
            "next_node": next_node,
            "token_usage": {"total": 0, "prompt": 0, "completion": 0},
        }
