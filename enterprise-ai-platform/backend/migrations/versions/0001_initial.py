"""
Initial migration — creates all core tables:
users, test_generation_sessions, test_cases, audit_log, kb_documents
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
import sqlalchemy.dialects.postgresql as pg

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("azure_oid", sa.String(36), unique=True, nullable=False),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("roles", pg.JSONB, server_default="[]"),
        sa.Column("attributes", pg.JSONB, server_default="{}"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_azure_oid", "users", ["azure_oid"])

    # test_generation_sessions
    op.create_table(
        "test_generation_sessions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_story_id", sa.String(50), nullable=False),
        sa.Column("project_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="'DRAFT'"),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("revision_count", sa.Integer, server_default="0"),
        sa.Column("user_story_data", pg.JSONB, server_default="{}"),
        sa.Column("metadata", pg.JSONB, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_sessions_story_id", "test_generation_sessions", ["user_story_id"])
    op.create_index("ix_sessions_status", "test_generation_sessions", ["status"])
    op.create_index("ix_sessions_created_at", "test_generation_sessions", ["created_at"])

    # test_cases
    op.create_table(
        "test_cases",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", pg.UUID(as_uuid=True), sa.ForeignKey("test_generation_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("gherkin_text", sa.Text),
        sa.Column("steps", pg.JSONB, server_default="[]"),
        sa.Column("priority", sa.String(20), server_default="'2'"),
        sa.Column("tags", pg.JSONB, server_default="[]"),
        sa.Column("ado_test_case_id", sa.String(50)),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_test_cases_session_id", "test_cases", ["session_id"])
    op.create_index("ix_test_cases_type", "test_cases", ["type"])

    # audit_log (partitioned by created_at — range partitioning)
    op.create_table(
        "audit_log",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", pg.UUID(as_uuid=True)),
        sa.Column("actor_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50)),
        sa.Column("entity_id", pg.UUID(as_uuid=True)),
        sa.Column("payload", pg.JSONB, server_default="{}"),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("ix_audit_log_session_id", "audit_log", ["session_id"])
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])

    # kb_documents
    op.create_table(
        "kb_documents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("category", sa.String(100)),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("chunk_count", sa.Integer),
        sa.Column("embedding_model", sa.String(100)),
        sa.Column("uploaded_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("azure_blob_path", sa.String(1000)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("file_size_bytes", sa.BigInteger),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("metadata", pg.JSONB, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_kb_documents_category", "kb_documents", ["category"])
    op.create_index("ix_kb_documents_is_active", "kb_documents", ["is_active"])

    # review_comments
    op.create_table(
        "review_comments",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", pg.UUID(as_uuid=True), sa.ForeignKey("test_generation_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("test_case_id", pg.UUID(as_uuid=True), sa.ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=True),
        sa.Column("author_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("resolved", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_review_comments_session_id", "review_comments", ["session_id"])


def downgrade() -> None:
    op.drop_table("review_comments")
    op.drop_table("kb_documents")
    op.drop_table("audit_log")
    op.drop_table("test_cases")
    op.drop_table("test_generation_sessions")
    op.drop_table("users")
