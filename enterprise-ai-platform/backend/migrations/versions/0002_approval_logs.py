"""
Add approval_logs table and CANCELLED session status.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
import sqlalchemy.dialects.postgresql as pg

revision = "0002_approval_logs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_logs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", pg.UUID(as_uuid=True), sa.ForeignKey("test_generation_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("actor_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("comment", sa.Text),
        sa.Column("previous_status", sa.String(20), nullable=False),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_approval_logs_session_id", "approval_logs", ["session_id"])
    op.create_index("ix_approval_logs_actor_id", "approval_logs", ["actor_id"])
    op.create_index("ix_approval_logs_created_at", "approval_logs", ["created_at"])

    # Widen status column to accommodate CANCELLED and PUBLISHED values
    op.alter_column(
        "test_generation_sessions",
        "status",
        type_=sa.String(20),
        existing_type=sa.String(20),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.drop_table("approval_logs")
