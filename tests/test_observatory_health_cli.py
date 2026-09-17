"""Tests for scripts.diagnose_observatory_health CLI."""

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.analysis import EntryAnalysis, AnalysisCall, AnalysisPromptVersion
from app.models.ingestion_run import IngestionRun
from app.models.tracking import TrackingMatrix
from scripts.diagnose_observatory_health import (
    gather_health_from_db,
    detect_anomalies,
    format_health_report,
    ObservatoryHealthReport,
    PipelineMetricsRecord,
    CostMetricsRecord,
    SourceHealthRecord,
)


def test_source_health_states_and_linkedin_isolation(db_session: Session):
    """Test 1, 2, 3, 9, 10: healthy, disabled, degraded, no runs, and LinkedIn isolation."""
    # 1. Healthy source with successful run
    src_healthy = Source(
        name="Healthy Regulator",
        type=SourceType.RSS,
        active=True,
    )
    # 2. Disabled source
    src_disabled = Source(
        name="Disabled Blog",
        type=SourceType.WEBSITE,
        active=False,
    )
    # 3. Degraded source with failed latest run
    src_degraded = Source(
        name="Degraded Source",
        type=SourceType.RSS,
        active=True,
    )
    # 9. Source with no executions
    src_no_runs = Source(
        name="Brand New Source",
        type=SourceType.RSS,
        active=True,
    )
    # 10. LinkedIn source
    src_linkedin = Source(
        name="LinkedIn",
        type=SourceType.LINKEDIN,
        active=True,
        config={"enabled": True},
    )
    db_session.add_all([src_healthy, src_disabled, src_degraded, src_no_runs, src_linkedin])
    db_session.flush()

    now = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)
    run_succ = IngestionRun(
        source_id=src_healthy.id,
        started_at=now,
        finished_at=now,
        status="success",
        fetched_count=10,
        created_count=2,
        failed_count=0,
    )
    run_fail = IngestionRun(
        source_id=src_degraded.id,
        started_at=now,
        finished_at=now,
        status="failed",
        fetched_count=0,
        created_count=0,
        failed_count=1,
    )
    db_session.add_all([run_succ, run_fail])
    db_session.commit()

    report = gather_health_from_db(db_session)
    formatted = format_health_report(report)

    # 1. Healthy
    rec_h = next(s for s in report.sources if s.source_name == "Healthy Regulator")
    assert rec_h.status == "healthy"
    assert rec_h.enabled is True
    assert rec_h.entries_created == 2

    # 2. Disabled
    rec_dis = next(s for s in report.sources if s.source_name == "Disabled Blog")
    assert rec_dis.status == "disabled"
    assert rec_dis.enabled is False

    # 3. Degraded
    rec_deg = next(s for s in report.sources if s.source_name == "Degraded Source")
    assert rec_deg.status == "degraded"
    assert rec_deg.enabled is True
    assert rec_deg.errors == 1

    # 9. No runs
    rec_new = next(s for s in report.sources if s.source_name == "Brand New Source")
    assert rec_new.status == "healthy"
    assert rec_new.last_execution is None
    assert rec_new.entries_created == 0

    # 10. LinkedIn
    rec_li = next(s for s in report.sources if s.is_linkedin)
    assert rec_li.is_linkedin is True
    assert "LinkedIn" in formatted


def test_pipeline_aggregation_and_conversion(db_session: Session):
    """Test 4, 5: Captured -> Entries -> Triage -> Deep aggregation."""
    source = Source(name="Test Aggregator", type=SourceType.RSS, active=True)
    db_session.add(source)
    db_session.flush()

    run = IngestionRun(
        source_id=source.id,
        started_at=datetime.now(timezone.utc),
        fetched_count=10,
        created_count=3,
        status="success",
    )
    db_session.add(run)

    # Add 3 entries
    entries = []
    for i in range(3):
        e = Entry(
            id=uuid.uuid4(),
            source_id=source.id,
            url=f"https://test.com/post/{i}",
            canonical_url=f"https://test.com/post/{i}",
            title=f"Post {i}",
            content="Antitrust and damages claim content for testing.",
            content_type="article",
        )
        entries.append(e)
    db_session.add_all(entries)
    db_session.flush()

    # Matrix & Prompt
    matrix = TrackingMatrix(code="TEST-HEALTH-MTRX", name="Health Matrix", status="active")
    prompt = AnalysisPromptVersion(
        code="observatory_deep_analysis",
        version=7,
        stage="deep_analysis",
        name="Deep Analysis v7",
        system_prompt="sys",
        user_prompt_template="usr",
        response_schema_version="v7",
        active=True,
    )
    db_session.add_all([matrix, prompt])
    db_session.flush()

    # Entry 0: Relevant + Deep Analysis
    a0 = EntryAnalysis(
        entry_id=entries[0].id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        status="completed",
        relevance_status="relevant",
        relevance_score=90,
        pipeline_version="v6",
        entry_content_hash="h0",
        matrix_snapshot_hash="m0",
    )
    # Entry 1: Not relevant
    a1 = EntryAnalysis(
        entry_id=entries[1].id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        status="completed",
        relevance_status="not_relevant",
        relevance_score=20,
        pipeline_version="v6",
        entry_content_hash="h1",
        matrix_snapshot_hash="m1",
    )
    # Entry 2: Failed analysis
    a2 = EntryAnalysis(
        entry_id=entries[2].id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        status="failed",
        reason="Timeout in Gemini",
        pipeline_version="v6",
        entry_content_hash="h2",
        matrix_snapshot_hash="m2",
    )
    db_session.add_all([a0, a1, a2])
    db_session.flush()

    # Deep call for a0
    c0 = AnalysisCall(
        entry_analysis_id=a0.id,
        prompt_version_id=prompt.id,
        stage="deep_analysis",
        provider="gemini_api",
        model="gemini-3.8-flash",
        status="completed",
        latency_ms=250,
        estimated_cost_usd=0.0040,
    )
    db_session.add(c0)
    db_session.commit()

    report = gather_health_from_db(db_session)
    assert report.pipeline.posts_captured == 10
    assert report.pipeline.entries_created == 3
    assert report.pipeline.total_analyzed == 2
    assert report.pipeline.relevant_count == 1
    assert report.pipeline.not_relevant_count == 1
    assert report.pipeline.deep_analysis_count == 1
    assert report.pipeline.failed_analysis_count == 1


def test_costs_and_telemetry_presentation():
    """Test 6, 7: Costs properly displayed when present, N/A when missing."""
    # With costs
    rep_with_cost = ObservatoryHealthReport()
    rep_with_cost.cost.has_gemini_cost_data = True
    rep_with_cost.cost.gemini_estimated_cost_usd = 0.0525
    rep_with_cost.cost.has_provider_cost_data = True
    rep_with_cost.cost.provider_estimated_cost_usd = 0.0100
    rep_with_cost.cost.total_estimated_cost_usd = 0.0625

    text_with_cost = format_health_report(rep_with_cost)
    assert "$0.0525" in text_with_cost
    assert "$0.0100" in text_with_cost
    assert "$0.0625" in text_with_cost

    # Without costs
    rep_no_cost = ObservatoryHealthReport()
    text_no_cost = format_health_report(rep_no_cost)
    assert "Gemini estimated cost          : N/A — insufficient telemetry" in text_no_cost
    assert "Provider estimated cost        : N/A — insufficient telemetry" in text_no_cost
    assert "Total estimated cost           : N/A — insufficient telemetry" in text_no_cost


def test_anomaly_detection_without_breaking():
    """Test 8: Inconsistencies detected cleanly in report."""
    rep = ObservatoryHealthReport()
    # Inversion: entries > captured
    rep.pipeline.posts_captured = 5
    rep.pipeline.entries_created = 10
    # Inversion: deep > relevant
    rep.pipeline.relevant_count = 1
    rep.pipeline.deep_analysis_count = 3
    rep.pipeline.total_analyzed = 2
    # Negative cost
    rep.cost.gemini_estimated_cost_usd = -0.5

    detect_anomalies(rep)
    assert len(rep.pipeline.anomalies) == 4
    formatted = format_health_report(rep)
    assert "Anomalies & Warnings" in formatted
    assert "exceeds items captured" in formatted
    assert "Negative estimated costs" in formatted
