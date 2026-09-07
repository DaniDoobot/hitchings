"""Tracking configuration schema: matrices, topics, entities and associations.

Revision ID: 0002_tracking_configuration
Revises: 0001_initial_schema
Create Date: 2026-09-07 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_tracking_configuration"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create tracking_matrices table
    op.create_table(
        "tracking_matrices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("relevance_instructions", sa.Text(), nullable=True),
        sa.Column("exclusion_instructions", sa.Text(), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_tracking_matrices_code")
    )
    op.create_index("ix_tracking_matrices_code", "tracking_matrices", ["code"])

    # 2. Create tracking_topics table
    op.create_table(
        "tracking_topics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("matrix_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("relevance_instructions", sa.Text(), nullable=True),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("discovery_queries", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("provisional", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["matrix_id"], ["tracking_matrices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["tracking_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("matrix_id", "code", name="uq_tracking_topics_matrix_code")
    )
    op.create_index("ix_tracking_topics_matrix_id", "tracking_topics", ["matrix_id"])
    op.create_index("ix_tracking_topics_parent_id", "tracking_topics", ["parent_id"])
    op.create_index("ix_tracking_topics_code", "tracking_topics", ["code"])

    # 3. Create tracked_entities table
    op.create_table(
        "tracked_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("entity_type", sa.String(length=50), server_default="unknown", nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index("ix_tracked_entities_display_name", "tracked_entities", ["display_name"])

    # 4. Create tracked_entity_topics association table
    op.create_table(
        "tracked_entity_topics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tracked_entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tracking_topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tracked_entity_id"], ["tracked_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tracking_topic_id"], ["tracking_topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tracked_entity_id", "tracking_topic_id", name="uq_entity_topic")
    )
    op.create_index("ix_tracked_entity_topics_tracked_entity_id", "tracked_entity_topics", ["tracked_entity_id"])
    op.create_index("ix_tracked_entity_topics_tracking_topic_id", "tracked_entity_topics", ["tracking_topic_id"])

    # 5. Add tracked_entity_id to sources table
    op.add_column("sources", sa.Column("tracked_entity_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_sources_tracked_entity_id",
        "sources",
        "tracked_entities",
        ["tracked_entity_id"],
        ["id"],
        ondelete="SET NULL"
    )
    op.create_index("ix_sources_tracked_entity_id", "sources", ["tracked_entity_id"])


def downgrade() -> None:
    # 5. Revert sources alteration
    op.drop_index("ix_sources_tracked_entity_id", table_name="sources")
    op.drop_constraint("fk_sources_tracked_entity_id", "sources", type_="foreignkey")
    op.drop_column("sources", "tracked_entity_id")

    # 4. Drop tracked_entity_topics
    op.drop_index("ix_tracked_entity_topics_tracking_topic_id", table_name="tracked_entity_topics")
    op.drop_index("ix_tracked_entity_topics_tracked_entity_id", table_name="tracked_entity_topics")
    op.drop_table("tracked_entity_topics")

    # 3. Drop tracked_entities
    op.drop_index("ix_tracked_entities_display_name", table_name="tracked_entities")
    op.drop_table("tracked_entities")

    # 2. Drop tracking_topics
    op.drop_index("ix_tracking_topics_code", table_name="tracking_topics")
    op.drop_index("ix_tracking_topics_parent_id", table_name="tracking_topics")
    op.drop_index("ix_tracking_topics_matrix_id", table_name="tracking_topics")
    op.drop_table("tracking_topics")

    # 1. Drop tracking_matrices
    op.drop_index("ix_tracking_matrices_code", table_name="tracking_matrices")
    op.drop_table("tracking_matrices")
