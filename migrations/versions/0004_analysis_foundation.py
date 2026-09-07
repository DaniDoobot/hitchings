"""Create AI analysis tables: prompt versions, entry analyses, topics, and audit calls.

Revision ID: 0004_analysis_foundation
Revises: 0003_ingestion_runs
Create Date: 2026-09-07 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_analysis_foundation"
down_revision: Union[str, None] = "0003_ingestion_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. analysis_prompt_versions
    op.create_table(
        "analysis_prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_prompt_template", sa.Text(), nullable=False),
        sa.Column("response_schema_version", sa.String(length=50), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "version", name="uq_prompt_code_version"),
    )
    op.create_index(
        "ix_analysis_prompt_versions_code",
        "analysis_prompt_versions",
        ["code"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_prompt_versions_stage",
        "analysis_prompt_versions",
        ["stage"],
        unique=False,
    )

    # 2. entry_analyses
    op.create_table(
        "entry_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("matrix_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("entry_content_hash", sa.String(length=64), nullable=True),
        sa.Column("matrix_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("matrix_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("relevance_status", sa.String(length=50), nullable=True),
        sa.Column("relevance_score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("key_points", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["entries.id"],
            name="fk_entry_analyses_entry_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["matrix_id"],
            ["tracking_matrices.id"],
            name="fk_entry_analyses_matrix_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entry_analyses_entry_id", "entry_analyses", ["entry_id"], unique=False)
    op.create_index("ix_entry_analyses_matrix_id", "entry_analyses", ["matrix_id"], unique=False)
    op.create_index("ix_entry_analyses_status", "entry_analyses", ["status"], unique=False)
    op.create_index("ix_entry_analyses_relevance_status", "entry_analyses", ["relevance_status"], unique=False)
    op.create_index("ix_entry_analyses_matrix_snapshot_hash", "entry_analyses", ["matrix_snapshot_hash"], unique=False)
    op.create_index("ix_entry_analyses_entry_content_hash", "entry_analyses", ["entry_content_hash"], unique=False)

    # 3. entry_analysis_topics
    op.create_table(
        "entry_analysis_topics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["analysis_id"],
            ["entry_analyses.id"],
            name="fk_entry_analysis_topics_analysis_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["tracking_topics.id"],
            name="fk_entry_analysis_topics_topic_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_id", "topic_id", name="uq_analysis_topic"),
    )
    op.create_index(
        "ix_entry_analysis_topics_analysis_id",
        "entry_analysis_topics",
        ["analysis_id"],
        unique=False,
    )
    op.create_index(
        "ix_entry_analysis_topics_topic_id",
        "entry_analysis_topics",
        ["topic_id"],
        unique=False,
    )

    # 4. analysis_calls
    op.create_table(
        "analysis_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("input_chars", sa.Integer(), nullable=True),
        sa.Column("output_chars", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_type", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("call_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["entry_analysis_id"],
            ["entry_analyses.id"],
            name="fk_analysis_calls_entry_analysis_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_version_id"],
            ["analysis_prompt_versions.id"],
            name="fk_analysis_calls_prompt_version_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_calls_entry_analysis_id",
        "analysis_calls",
        ["entry_analysis_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_calls_prompt_version_id",
        "analysis_calls",
        ["prompt_version_id"],
        unique=False,
    )
    op.create_index("ix_analysis_calls_status", "analysis_calls", ["status"], unique=False)


def downgrade() -> None:
    op.drop_table("analysis_calls")
    op.drop_table("entry_analysis_topics")
    op.drop_table("entry_analyses")
    op.drop_table("analysis_prompt_versions")
