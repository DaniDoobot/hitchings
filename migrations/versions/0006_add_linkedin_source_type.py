"""Add linkedin value to source_type enum.

Revision ID: 0006_add_linkedin_source_type
Revises: 0005_users_and_auth_sessions
Create Date: 2026-09-09 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006_add_linkedin_source_type"
down_revision: Union[str, None] = "0005_users_and_auth_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 'linkedin' value to PostgreSQL enum type 'source_type'
    op.execute(sa.text("COMMIT"))
    op.execute(sa.text("ALTER TYPE source_type ADD VALUE IF NOT EXISTS 'linkedin'"))


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type directly
    pass
