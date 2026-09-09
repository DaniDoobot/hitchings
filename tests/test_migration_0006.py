"""Regression tests for Alembic migration 0006_add_linkedin_source_type (Bloque 9C.2)."""

from pathlib import Path
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.source import SourceType


def test_migration_0006_code_targets_correct_enum_name():
    """Verify 0006 migration targets the real PostgreSQL enum 'source_type' (with underscore)."""
    mig_path = Path("migrations/versions/0006_add_linkedin_source_type.py")
    assert mig_path.exists(), "Migration 0006 file must exist"

    content = mig_path.read_text(encoding="utf-8")

    # 1. Ensure it uses 'source_type' and NOT 'sourcetype'
    assert "ALTER TYPE source_type ADD VALUE IF NOT EXISTS 'linkedin'" in content, (
        "Migration 0006 must use exact enum type name 'source_type'"
    )
    assert "ALTER TYPE sourcetype" not in content, (
        "Migration 0006 must NOT use incorrect enum type 'sourcetype'"
    )

    # 2. Ensure down_revision points to 0005
    assert 'down_revision: Union[str, None] = "0005_users_and_auth_sessions"' in content


def test_migration_0001_defined_enum_name():
    """Verify 0001 migration defines the enum name as 'source_type'."""
    mig_path = Path("migrations/versions/0001_initial_schema.py")
    assert mig_path.exists()

    content = mig_path.read_text(encoding="utf-8")
    assert 'name="source_type"' in content


def test_real_database_has_source_type_enum_with_linkedin(db_session: Session):
    """Verify that SourceType has 'linkedin' and PostgreSQL enum in live database is 'source_type'."""
    assert SourceType.LINKEDIN.value == "linkedin"

    if db_session.bind.dialect.name == "postgresql":
        res = db_session.execute(text("""
            SELECT t.typname, e.enumlabel
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            WHERE t.typname = 'source_type'
            ORDER BY e.enumsortorder;
        """)).fetchall()

        labels = [row[1] for row in res]
        assert "linkedin" in labels
        assert "website" in labels
        assert "google_news" in labels
        assert len(labels) == 9
