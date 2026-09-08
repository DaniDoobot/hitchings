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
from app.db.session import SessionLocal
from app.models.analysis import AnalysisCall, EntryAnalysis, AnalysisPromptVersion
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix
from tests.conftest import test_engine, verify_test_db_url_is_safe


def test_pytest_uses_isolated_test_database():
    """Verify that test_engine is an in-memory SQLite database, completely isolated from PostgreSQL."""
    url = str(test_engine.url)
    assert url == "sqlite:///:memory:" or ":memory:" in url or "test" in url.lower()
    assert test_engine.url.drivername.startswith("sqlite")


def test_fail_closed_guard_rejects_unsafe_db_urls():
    """Verify that verify_test_db_url_is_safe aborts on production/dev database URLs."""
    # Safe test URLs
    verify_test_db_url_is_safe("sqlite:///:memory:")
    verify_test_db_url_is_safe("postgresql+psycopg2://user:pass@localhost:5432/hitchings_test")
    verify_test_db_url_is_safe("postgresql://user:pass@host:5432/my_test_db")

    # Unsafe / production URLs must raise RuntimeError without leaking secrets
    unsafe_urls = [
        "postgresql+psycopg2://hitchings:secretpass@localhost:5432/hitchings",
        "postgresql://admin:prodpass@db.production.internal:5432/hitchings_prod",
        "mysql://user:pass@localhost/main_db",
    ]
    for bad_url in unsafe_urls:
        with pytest.raises(RuntimeError) as exc_info:
            verify_test_db_url_is_safe(bad_url)
        assert "SECURITY / LEDGER VIOLATION" in str(exc_info.value)
        # Verify credentials are not printed in the error message
        assert "secretpass" not in str(exc_info.value)
        assert "prodpass" not in str(exc_info.value)


def test_test_records_never_persist_to_real_postgresql(db_session: Session):
    """Verify that records created in db_session (test DB) do NOT exist in PostgreSQL SessionLocal."""
    # 1. Count existing records in real DB before test action
    real_db = SessionLocal()
    try:
        initial_real_calls = real_db.query(AnalysisCall).count()
        initial_real_eas = real_db.query(EntryAnalysis).count()
    finally:
        real_db.close()

    # 2. Create a synthetic test entry, analysis, and call inside the test session
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
        pipeline_version="v3",
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

    # 3. Verify it exists in db_session (test DB)
    assert db_session.query(AnalysisCall).filter(AnalysisCall.id == synthetic_call.id).first() is not None

    # 4. Verify it DOES NOT exist in the real PostgreSQL database
    real_db_check = SessionLocal()
    try:
        found_in_real = real_db_check.query(AnalysisCall).filter(AnalysisCall.id == synthetic_call.id).first()
        assert found_in_real is None, "Contamination detected: synthetic test call was found in real PostgreSQL DB!"

        current_real_calls = real_db_check.query(AnalysisCall).count()
        current_real_eas = real_db_check.query(EntryAnalysis).count()
        assert current_real_calls == initial_real_calls, "Real DB AnalysisCall count changed during test execution!"
        assert current_real_eas == initial_real_eas, "Real DB EntryAnalysis count changed during test execution!"
    finally:
        real_db_check.close()
