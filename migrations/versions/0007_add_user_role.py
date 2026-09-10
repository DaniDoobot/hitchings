"""Add role column to users table.

Revision ID: 0007_add_user_role
Revises: 0006_add_linkedin_source_type
Create Date: 2026-09-10 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0007_add_user_role"
down_revision: Union[str, None] = "0006_add_linkedin_source_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.String(length=50),
            server_default=sa.text("'user'"),
            nullable=False,
        ),
    )
    op.create_index("ix_users_role", "users", ["role"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_role", table_name="users")
    op.drop_column("users", "role")
