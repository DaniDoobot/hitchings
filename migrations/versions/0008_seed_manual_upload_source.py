"""Seed canonical manual upload source for document library (Bloque 11C).

Revision ID: 0008_seed_manual_upload_source
Revises: 0007_add_user_role
Create Date: 2026-10-13 10:00:00.000000

"""
from typing import Sequence, Union
from datetime import datetime, timezone
import json
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0008_seed_manual_upload_source"
down_revision: Union[str, None] = "0007_add_user_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MANUAL_SOURCE_ID = uuid.UUID("a0000000-0000-4000-a000-000000000001")
MANUAL_SOURCE_NAME = "Aportación Manual / Fondo Documental"
MANUAL_SOURCE_CODE = "manual_upload"
MANUAL_SOURCE_URL = "https://hitchings.internal/manual-upload"


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    # Check if source already exists by logical code, name or fixed ID
    if dialect == "postgresql":
        check_sql = sa.text(
            """
            SELECT id FROM sources
            WHERE (config IS NOT NULL AND config->>'code' = :code)
               OR name = :name
               OR id = :source_id
            LIMIT 1
            """
        )
    else:
        check_sql = sa.text(
            """
            SELECT id FROM sources
            WHERE name = :name
               OR id = :source_id
            LIMIT 1
            """
        )

    existing = conn.execute(
        check_sql,
        {
            "code": MANUAL_SOURCE_CODE,
            "name": MANUAL_SOURCE_NAME,
            "source_id": str(MANUAL_SOURCE_ID),
        },
    ).fetchone()

    if existing is None:
        now = datetime.now(timezone.utc)
        config_payload = {"code": MANUAL_SOURCE_CODE, "ingestion_mode": "manual"}

        if dialect == "postgresql":
            insert_sql = sa.text(
                """
                INSERT INTO sources (
                    id, name, type, url, active, category, provider, config, created_at, updated_at
                ) VALUES (
                    :id, :name, 'website'::source_type, :url, :active, :category, :provider, CAST(:config AS jsonb), :created_at, :updated_at
                )
                """
            )
        else:
            insert_sql = sa.text(
                """
                INSERT INTO sources (
                    id, name, type, url, active, category, provider, config, created_at, updated_at
                ) VALUES (
                    :id, :name, 'website', :url, :active, :category, :provider, :config, :created_at, :updated_at
                )
                """
            )

        conn.execute(
            insert_sql,
            {
                "id": str(MANUAL_SOURCE_ID),
                "name": MANUAL_SOURCE_NAME,
                "url": MANUAL_SOURCE_URL,
                "active": True,
                "category": "manual_document",
                "provider": "native",
                "config": json.dumps(config_payload),
                "created_at": now,
                "updated_at": now,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    if dialect == "postgresql":
        conn.execute(
            sa.text(
                """
                DELETE FROM sources
                WHERE (config IS NOT NULL AND config->>'code' = :code)
                   OR id = :source_id
                """
            ),
            {
                "code": MANUAL_SOURCE_CODE,
                "source_id": str(MANUAL_SOURCE_ID),
            },
        )
    else:
        conn.execute(
            sa.text(
                """
                DELETE FROM sources
                WHERE name = :name
                   OR id = :source_id
                """
            ),
            {
                "name": MANUAL_SOURCE_NAME,
                "source_id": str(MANUAL_SOURCE_ID),
            },
        )
