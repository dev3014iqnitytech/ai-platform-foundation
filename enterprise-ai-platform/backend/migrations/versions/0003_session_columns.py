from __future__ import annotations

import sqlalchemy as sa
from alembic import op
import sqlalchemy.dialects.postgresql as pg

revision = "0003_session_columns"
down_revision = "0002_approval_logs"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("test_generation_sessions",
        sa.Column("gherkin_data", pg.JSONB, nullable=True))
    op.add_column("test_generation_sessions",
        sa.Column("knowledge_context", pg.JSONB, nullable=True))
    op.add_column("test_generation_sessions",
        sa.Column("token_usage", pg.JSONB, server_default="{}"))
        # add missing test_cases columns (from your SELECT)
    op.add_column("test_cases",
        sa.Column("title", sa.String(length=500), nullable=False))
    op.add_column("test_cases",
        sa.Column("type", sa.String(length=50), nullable=False))
    op.add_column("test_cases",
        sa.Column("priority", sa.String(length=20), server_default=sa.text("'medium'")))
    op.add_column("test_cases",
        sa.Column("gherkin_text", sa.Text(), nullable=True))
    op.add_column("test_cases",
        sa.Column("steps", pg.JSONB, nullable=True))
    op.add_column("test_cases",
        sa.Column("expected_result", sa.Text(), nullable=True))
    op.add_column("test_cases",
        sa.Column("preconditions", sa.Text(), nullable=True))
    op.add_column("test_cases",
        sa.Column("tags", pg.JSONB, server_default=sa.text("'[]'::jsonb")))
    op.add_column("test_cases",
        sa.Column("ado_test_case_id", sa.String(length=50), nullable=True))
    op.add_column("test_cases",
        sa.Column("version", sa.Integer(), server_default=sa.text("1")))
    op.add_column("test_cases",
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")))

def downgrade():
    op.drop_column("test_generation_sessions", "token_usage")
    op.drop_column("test_generation_sessions", "knowledge_context")
    op.drop_column("test_generation_sessions", "gherkin_data")
    op.drop_column("test_cases", "created_at")
    op.drop_column("test_cases", "version")
    op.drop_column("test_cases", "ado_test_case_id")
    op.drop_column("test_cases", "tags")
    op.drop_column("test_cases", "preconditions")
    op.drop_column("test_cases", "expected_result")
    op.drop_column("test_cases", "steps")
    op.drop_column("test_cases", "gherkin_text")
    op.drop_column("test_cases", "priority")
    op.drop_column("test_cases", "type")
    op.drop_column("test_cases", "title")
    