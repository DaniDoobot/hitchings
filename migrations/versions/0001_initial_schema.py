"""Initial schema for sources, entries and provider_usage.

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-09-07 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create source_type enum
    source_type_enum = postgresql.ENUM(
        "website",
        "rss",
        "google_news",
        "institutional",
        "blog",
        "linkedin_profile",
        "linkedin_company",
        "linkedin_search",
        name="source_type",
        create_type=False
    )
    source_type_enum.create(op.get_bind(), checkfirst=True)

    # 2. Create sources table
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", postgresql.ENUM(
            "website",
            "rss",
            "google_news",
            "institutional",
            "blog",
            "linkedin_profile",
            "linkedin_company",
            "linkedin_search",
            name="source_type",
            create_type=False
        ), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default="native"),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("schedule_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id")
    )

    # 3. Create entries table
    op.create_table(
        "entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("content_type", sa.String(length=50), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("raw_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id")
    )
    op.create_index("ix_entries_source_id", "entries", ["source_id"])
    op.create_index("ix_entries_external_id", "entries", ["external_id"])
    op.create_index("ix_entries_url", "entries", ["url"])
    op.create_index("ix_entries_canonical_url", "entries", ["canonical_url"])
    op.create_index("ix_entries_content_hash", "entries", ["content_hash"])

    # 4. Create provider_usage table
    op.create_table(
        "provider_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("period", sa.String(length=20), nullable=False),
        sa.Column("records_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("soft_limit", sa.Integer(), nullable=True),
        sa.Column("hard_limit", sa.Integer(), nullable=True),
        sa.Column("allow_paid_usage", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "period", name="uq_provider_usage_provider_period")
    )


def downgrade() -> None:
    op.drop_table("provider_usage")
    op.drop_index("ix_entries_content_hash", table_name="entries")
    op.drop_index("ix_entries_canonical_url", table_name="entries")
    op.drop_index("ix_entries_url", table_name="entries")
    op.drop_index("ix_entries_external_id", table_name="entries")
    op.drop_index("ix_entries_source_id", table_name="entries")
    op.drop_table("entries")
    op.drop_table("sources")
    
    source_type_enum = postgresql.ENUM(name="source_type")
    source_type_enum.drop(op.get_bind(), checkfirst=True)
