"""Tests for Bloque 7E.2: Test Isolation, Fail-Closed Guards, and Ledger Non-Contamination.

Guarantees:
1. Pytest suite strictly targets an isolated test database (SQLite in-memory).
2. Creating AnalysisCall and EntryAnalysis inside test fixtures NEVER contaminates development/production PostgreSQL.
3. The fail-closed guard strictly aborts if a non-test database URL is supplied.
4. No external Gemini calls can be made during tests.
"""

import uuid
import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import engine as app_engine
from app.models.analysis import AnalysisCall, EntryAnalysis, AnalysisPromptVersion
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix
from tests.conftest import test_engine, verify_test_db_url_is_safe


def test_pytest_uses_isolated_test_database():
    """Verify that test_engine is an in-memory SQLite database, completely isolated from PostgreSQL."""
    url = str(test_engine.url)
    assert url == "sqlite:///:memory:" or ":memory:" in url
    assert test_engine.url.drivername.startswith("sqlite")


def test_fail_closed_guard_rejects_unsafe_db_urls():
    """Verify that verify_test_db_url_is_safe performs structural validation on database name."""
    # 1. Safe test URLs
    verify_test_db_url_is_safe("sqlite:///:memory:")
    verify_test_db_url_is_safe("sqlite://")
    verify_test_db_url_is_safe("postgresql+psycopg2://user:pass@localhost:5432/hitchings_test")
    verify_test_db_url_is_safe("postgresql://user:pass@host:5432/test_hitchings")
    verify_test_db_url_is_safe("postgresql://user:pass@host:5432/hitchings_tests")

    # 2. Unsafe production/development URLs must be strictly rejected
    unsafe_urls = [
        "postgresql+psycopg2://hitchings:secretpass@localhost:5432/hitchings",
        "postgresql://admin:prodpass@db.production.internal:5432/hitchings_prod",
        "mysql://user:pass@localhost/main_db",
    ]
    for bad_url in unsafe_urls:
        with pytest.raises(RuntimeError) as exc_info:
            verify_test_db_url_is_safe(bad_url)
        assert "SECURITY / LEDGER VIOLATION" in str(exc_info.value)
        # Verify credentials are not leaked in error messages
        assert "secretpass" not in str(exc_info.value)
        assert "prodpass" not in str(exc_info.value)

    # 3. URLs where 'test' is only in username, password, host, or query param must be REJECTED
    deceptive_urls = [
        "postgresql://test_user:pass@localhost:5432/hitchings",
        "postgresql://user:test_pass@localhost:5432/hitchings",
        "postgresql://user:pass@test-host.internal:5432/hitchings",
        "postgresql://user:pass@localhost:5432/hitchings?env=test",
    ]
    for dec_url in deceptive_urls:
        with pytest.raises(RuntimeError) as exc_info:
            verify_test_db_url_is_safe(dec_url)
        assert "SECURITY / LEDGER VIOLATION" in str(exc_info.value)


def test_test_session_is_strictly_bound_to_isolated_engine(db_session: Session):
    """Verify that db_session is bound strictly to test_engine and NOT the production/dev app_engine."""
    # Ensure db_session is bound to test_engine
    bind = db_session.get_bind()
    assert bind is not app_engine
    bind_engine = getattr(bind, "engine", bind)
    assert str(bind_engine.url).startswith("sqlite")
    assert bind.dialect.name == "sqlite"

    # Create synthetic test entities inside test_engine
    src = Source(name="Synthetic Test Source", type=SourceType.WEBSITE, provider="native", url="https://synthetic.test/")
    db_session.add(src)
    db_session.flush()

    entry = Entry(
        source_id=src.id,
        url=f"https://synthetic.test/{uuid.uuid4()}",
        title="Synthetic Title",
        content="Synthetic Content",
    )
    db_session.add(entry)
    db_session.flush()

    matrix = TrackingMatrix(code=f"SYNTH-{uuid.uuid4().hex[:6]}", name="Synthetic Matrix", status="active")
    db_session.add(matrix)
    db_session.flush()

    ea = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v4",
        status="completed",
        matrix_snapshot={"code": matrix.code, "name": matrix.name},
        matrix_snapshot_hash="synth_hash",
    )
    db_session.add(ea)
    db_session.flush()

    pv = AnalysisPromptVersion(
        code="synth_triage",
        version=1,
        stage="triage",
        name="Synth Triage",
        system_prompt="Synth prompt",
        user_prompt_template="Template",
        response_schema_version="v2",
        active=True,
    )
    db_session.add(pv)
    db_session.flush()

    synthetic_call = AnalysisCall(
        entry_analysis_id=ea.id,
        prompt_version_id=pv.id,
        stage="triage",
        provider="mock",
        model="mock-model",
        status="completed",
        estimated_cost_usd=0.005,
    )
    db_session.add(synthetic_call)
    db_session.commit()

    # Verify it exists in db_session (test DB)
    saved = db_session.query(AnalysisCall).filter(AnalysisCall.id == synthetic_call.id).first()
    assert saved is not None
    assert saved.provider == "mock"
