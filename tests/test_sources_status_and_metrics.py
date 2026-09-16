"""Tests for /api/v1/sources/status and /api/v1/sources/metrics endpoints."""

import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models.analysis import EntryAnalysis, AnalysisCall, AnalysisPromptVersion
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix
from app.models.ingestion_run import IngestionRun, IngestionRunStatus


@pytest.fixture
def client(db_session: Session):
    from app.db.session import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_sources_status_endpoint(client: TestClient, db_session: Session):
    """Verify GET /api/v1/sources/status returns health and execution stats for all sources."""
    # 1. Create healthy active source with an ingestion run
    src_active = Source(
        name="CNMC Test Status",
        type=SourceType.WEBSITE,
        active=True,
    )
    src_disabled = Source(
        name="Inactive Feed",
        type=SourceType.RSS,
        active=False,
    )
    db_session.add_all([src_active, src_disabled])
    db_session.flush()

    now = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    run = IngestionRun(
        source_id=src_active.id,
        started_at=now,
        finished_at=now,
        status="success",
        fetched_count=10,
        created_count=3,
        duplicate_count=7,
        failed_count=0,
    )
    db_session.add(run)
    db_session.commit()

    resp = client.get("/api/v1/sources/status")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)

    active_item = next((item for item in data if item["source_id"] == str(src_active.id)), None)
    disabled_item = next((item for item in data if item["source_id"] == str(src_disabled.id)), None)

    assert active_item is not None
    assert active_item["enabled"] is True
    assert active_item["status"] == "healthy"
    assert active_item["entries_created"] == 3
    assert active_item["errors"] == 0

    assert disabled_item is not None
    assert disabled_item["enabled"] is False
    assert disabled_item["status"] == "disabled"


def test_sources_metrics_endpoint(client: TestClient, db_session: Session):
    """Verify GET /api/v1/sources/metrics computes aggregated quality and cost indicators."""
    src_li = Source(
        name="LinkedIn Observatory",
        type=SourceType.LINKEDIN,
        active=True,
    )
    db_session.add(src_li)
    db_session.flush()

    # Ingestion run with provider cost
    now = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    run = IngestionRun(
        source_id=src_li.id,
        started_at=now,
        finished_at=now,
        status="success",
        fetched_count=20,
        created_count=1,
        duplicate_count=19,
        failed_count=0,
        run_metadata={"estimated_provider_cost": 0.0050},
    )
    db_session.add(run)

    entry = Entry(
        id=uuid.uuid4(),
        source_id=src_li.id,
        url="https://www.linkedin.com/posts/test-metrics-activity-9988",
        canonical_url="https://www.linkedin.com/posts/test-metrics-activity-9988",
        title="Test Metrics Post",
        content="Post sobre litigación antitrust y cárteles.",
        content_type="social_post",
        raw_metadata={"origin_source": "linkedin"},
    )
    db_session.add(entry)
    db_session.flush()

    # Prompt version
    prompt = AnalysisPromptVersion(
        code="observatory_deep_analysis",
        version=7,
        stage="deep_analysis",
        name="Deep Analysis v7",
        system_prompt="system",
        user_prompt_template="user",
        response_schema_version="v7",
        active=True,
    )
    db_session.add(prompt)
    db_session.flush()

    # Matrix
    matrix = TrackingMatrix(code="TEST-METRICS-MTRX", name="Metrics Matrix", status="active")
    db_session.add(matrix)
    db_session.flush()

    # Completed analysis with deep call
    analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        status="completed",
        relevance_status="relevant",
        relevance_score=88,
        confidence=0.92,
        pipeline_version="v6",
        entry_content_hash="abc",
        matrix_snapshot_hash="def",
    )
    db_session.add(analysis)
    db_session.flush()

    call = AnalysisCall(
        entry_analysis_id=analysis.id,
        prompt_version_id=prompt.id,
        stage="deep_analysis",
        provider="gemini_api",
        model="gemini-2.5",
        status="completed",
        latency_ms=180,
        estimated_cost_usd=0.0035,
    )
    db_session.add(call)
    db_session.commit()

    resp = client.get("/api/v1/sources/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data
    assert "generated_at" in data

    li_metric = next((m for m in data["sources"] if m["source_id"] == str(src_li.id)), None)
    assert li_metric is not None
    assert li_metric["is_linkedin"] is True
    assert li_metric["posts_captured"] == 20
    assert li_metric["entries_created"] == 1
    assert li_metric["total_analyzed"] == 1
    assert li_metric["relevant_count"] == 1
    assert li_metric["relevant_pct"] == 100.0
    assert li_metric["deep_analysis_count"] == 1
    assert li_metric["deep_analysis_pct"] == 100.0
    assert li_metric["avg_analysis_time_ms"] == 180.0
    assert li_metric["estimated_gemini_cost_usd"] == 0.0035
    assert li_metric["estimated_provider_cost_usd"] == 0.0050
