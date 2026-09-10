"""Comprehensive automated tests for Weekly Source Refresh (Bloque 11C)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.orm import Session

from app.core.advisory_lock import RefreshAdvisoryLock, _MEMORY_LOCKS, _MEMORY_LOCK_MUTEX
from app.core.config import Settings
from app.models.analysis import AnalysisCall, EntryAnalysis
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.schemas.ingestion import IngestionResult
from app.scheduler import compute_next_run, run_scheduler_loop
from app.services.direct_web_ingestion_service import DirectWebIngestionReport
from app.services.google_news_ingestion_service import GoogleNewsIngestionReport
from app.services.linkedin_ingestion_service import LinkedInIngestionReport
from app.services.weekly_refresh_service import WeeklyRefreshService


@pytest.fixture(autouse=True)
def clean_advisory_locks():
    """Ensure in-memory locks are completely clear before and after each test."""
    with _MEMORY_LOCK_MUTEX:
        _MEMORY_LOCKS.clear()
    yield
    with _MEMORY_LOCK_MUTEX:
        _MEMORY_LOCKS.clear()


@pytest.fixture
def active_matrix(db_session: Session) -> TrackingMatrix:
    """Ensure an active tracking matrix exists."""
    matrix = db_session.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    if not matrix:
        matrix = TrackingMatrix(
            code=f"HITCHINGS-v0.{uuid.uuid4().hex[:4]}",
            name="Matriz de Seguimiento HITCHINGS",
            status="active",
        )
        db_session.add(matrix)
        db_session.flush()

        area = TrackingTopic(
            matrix_id=matrix.id,
            parent_id=None,
            code="competencia_general",
            name="Competencia General",
            priority=1,
            active=True,
        )
        db_session.add(area)
        db_session.flush()

        topic = TrackingTopic(
            matrix_id=matrix.id,
            parent_id=area.id,
            code="carteles",
            name="Cárteles",
            priority=1,
            active=True,
        )
        db_session.add(topic)
        db_session.commit()

    return matrix


@pytest.fixture
def test_sources(db_session: Session) -> dict[str, Source]:
    """Ensure test sources for each family exist in the test DB."""
    sources: dict[str, Source] = {}

    def get_or_create(name: str, stype: SourceType, provider: str, url: str, active: bool = True) -> Source:
        s = db_session.query(Source).filter(Source.name == name).first()
        if not s:
            s = Source(
                name=name,
                type=stype,
                provider=provider,
                url=url,
                active=active,
            )
            db_session.add(s)
            db_session.flush()
        else:
            s.active = active
            db_session.flush()
        return s

    sources["cnmc"] = get_or_create("CNMC Test", SourceType.WEBSITE, "native", "https://www.cnmc.es/prensa/noticias")
    sources["cat"] = get_or_create("CAT Test", SourceType.WEBSITE, "native", "https://www.catribunal.org.uk/judgments")
    sources["curia"] = get_or_create("CURIA Test", SourceType.WEBSITE, "native", "https://curia.europa.eu/juris/recherche.jsf")
    sources["ec"] = get_or_create("EC Test", SourceType.RSS, "native", "https://competition-policy.ec.europa.eu/feed_en.xml")
    sources["google_news"] = get_or_create("Google News Test", SourceType.GOOGLE_NEWS, "native", "https://news.google.com")
    sources["linkedin"] = get_or_create("LinkedIn Test", SourceType.LINKEDIN, "external", "https://www.linkedin.com")
    sources["chillin"] = get_or_create(
        "Chillin'Competition Test",
        SourceType.BLOG,
        "native",
        "https://chillingcompetition.com/",
    )
    sources["chillin"].config = {"adapter": "chillin_competition", "feed_url": "https://chillingcompetition.com/feed/"}

    db_session.commit()
    return sources


def test_weekly_refresh_runs_all_active_sources(
    db_session: Session, active_matrix: TrackingMatrix, test_sources: dict[str, Source]
):
    """Weekly refresh processes all active sources and reports per-source metrics."""
    # Mock all external provider services to guarantee zero network/API calls
    now = datetime.now(timezone.utc)
    mock_ingest = MagicMock()
    mock_ingest.ingest_source.return_value = IngestionResult(
        ingestion_run_id=uuid.uuid4(),
        source_id=test_sources["cnmc"].id,
        status="success",
        fetched=5,
        created=2,
        duplicates=3,
        started_at=now,
        finished_at=now,
    )

    mock_dw = MagicMock()
    mock_dw.execute_ingestion.return_value = DirectWebIngestionReport(
        is_dry_run=False,
        total_discovered=4,
        total_created=1,
        total_duplicates=3,
        total_failed=0,
    )

    mock_gn = MagicMock()
    mock_gn.execute_ingestion.return_value = GoogleNewsIngestionReport(
        run_id=uuid.uuid4(),
        source_id=test_sources["google_news"].id,
        status="success",
        is_dry_run=False,
        queries_planned=2,
        queries_executed=2,
        items_seen=6,
        entries_created=1,
        duplicates_count=5,
        failed_queries=0,
        stopped_by_cap=False,
        publishers_found=["Expansión"],
        sample_created_entries=[],
        latest_published_at=datetime.now(timezone.utc),
        oldest_published_at=datetime.now(timezone.utc),
    )

    mock_li = MagicMock()
    mock_li.execute_discovery.return_value = LinkedInIngestionReport(
        run_id=uuid.uuid4(),
        primary_provider="brightdata",
        fallback_provider="apify",
        posts_seen=3,
        entries_created=1,
        duplicates=2,
    )

    settings = Settings(
        LINKEDIN_DISCOVERY_ENABLED=True,
        BRIGHTDATA_API_TOKEN="mock-token",
        GOOGLE_NEWS_ENABLED=True,
        DIRECT_WEB_INGESTION_ENABLED=True,
    )

    service = WeeklyRefreshService(
        settings=settings,
        ingestion_service=mock_ingest,
        direct_web_service=mock_dw,
        google_news_service=mock_gn,
        linkedin_service=mock_li,
    )

    report = service.run_weekly_refresh(
        db=db_session,
        lookback_days=8,
        confirm_real_calls=True,
    )

    assert report.status == "completed"
    assert report.sources_attempted >= len(test_sources)
    assert report.total_found > 0
    assert report.total_duplicates > 0
    assert report.active_matrix_code == active_matrix.code


def test_inactive_source_not_run(
    db_session: Session, active_matrix: TrackingMatrix, test_sources: dict[str, Source]
):
    """An inactive source (active=False) is ignored and never attempted."""
    test_sources["curia"].active = False
    db_session.commit()

    service = WeeklyRefreshService()
    report = service.run_weekly_refresh(
        db=db_session,
        confirm_real_calls=False,
    )

    attempted_names = {s.source_name for s in report.per_source}
    assert "CURIA Test" not in attempted_names


def test_linkedin_skipped_when_provider_not_configured(
    db_session: Session, active_matrix: TrackingMatrix, test_sources: dict[str, Source]
):
    """LinkedIn is cleanly marked skipped without error when API credentials are not set."""
    settings = Settings(
        LINKEDIN_DISCOVERY_ENABLED=False,
        BRIGHTDATA_API_TOKEN="",
        BRIGHTDATA_API_KEY="",
        APIFY_API_TOKEN="",
        APIFY_API_KEY="",
    )

    service = WeeklyRefreshService(settings=settings)
    report = service.run_weekly_refresh(
        db=db_session,
        confirm_real_calls=True,
        sources_filter=["LinkedIn Test"],
    )

    assert report.sources_attempted == 1
    assert report.sources_skipped == 1
    assert report.sources_failed == 0
    assert report.per_source[0].status == "skipped"
    assert "provider_not_configured" in report.per_source[0].errors


def test_source_failure_isolation(
    db_session: Session, active_matrix: TrackingMatrix, test_sources: dict[str, Source]
):
    """Failure in one source does NOT abort processing of the remaining sources."""
    mock_ingest = MagicMock()

    now = datetime.now(timezone.utc)

    # Make CURIA fail while other sources succeed
    def side_effect(source_id, db):
        if source_id == test_sources["curia"].id:
            raise ConnectionError("CURIA server timeout 504")
        return IngestionResult(
            ingestion_run_id=uuid.uuid4(),
            source_id=source_id,
            status="success",
            fetched=3,
            created=1,
            duplicates=2,
            started_at=now,
            finished_at=now,
        )

    mock_ingest.ingest_source.side_effect = side_effect

    mock_dw = MagicMock()
    mock_dw.execute_ingestion.return_value = DirectWebIngestionReport(
        is_dry_run=False,
        total_discovered=2,
        total_created=1,
        total_duplicates=1,
    )

    mock_gn = MagicMock()
    mock_gn.execute_ingestion.return_value = GoogleNewsIngestionReport(
        run_id=uuid.uuid4(),
        source_id=test_sources["google_news"].id,
        status="success",
        is_dry_run=False,
        queries_planned=1,
        queries_executed=1,
        items_seen=2,
        entries_created=1,
        duplicates_count=1,
        failed_queries=0,
        stopped_by_cap=False,
        publishers_found=[],
        sample_created_entries=[],
        latest_published_at=None,
        oldest_published_at=None,
    )

    service = WeeklyRefreshService(
        ingestion_service=mock_ingest,
        direct_web_service=mock_dw,
        google_news_service=mock_gn,
    )

    report = service.run_weekly_refresh(
        db=db_session,
        confirm_real_calls=True,
        sources_filter=["CURIA Test", "CNMC Test", "Chillin'Competition Test"],
    )

    assert report.sources_attempted == 3
    assert report.sources_failed == 1
    assert report.sources_successful == 2
    curia_detail = next(s for s in report.per_source if s.source_name == "CURIA Test")
    assert curia_detail.status == "failed"
    assert "CURIA server timeout" in curia_detail.errors[0]

    cnmc_detail = next(s for s in report.per_source if s.source_name == "CNMC Test")
    assert cnmc_detail.status == "success"


def test_advisory_lock_prevents_simultaneous_runs(
    db_session: Session, active_matrix: TrackingMatrix
):
    """When the advisory lock is already held, a concurrent call returns status='already_running'."""
    lock = RefreshAdvisoryLock(db_session)
    assert lock.acquire() is True

    try:
        service = WeeklyRefreshService()
        report = service.run_weekly_refresh(
            db=db_session,
            confirm_real_calls=False,
        )
        assert report.status == "already_running"
        assert report.sources_attempted == 0
    finally:
        lock.release()


def test_idempotent_deduplication_and_zero_reevaluations(
    db_session: Session, active_matrix: TrackingMatrix, test_sources: dict[str, Source]
):
    """Running refresh consecutively over already-seen entries results in 0 new entries and 0 calls."""
    # 1. Manually insert an entry for CNMC
    now = datetime.now(timezone.utc)
    entry = Entry(
        id=uuid.uuid4(),
        source_id=test_sources["cnmc"].id,
        external_id="cnmc-news-001",
        url="https://www.cnmc.es/prensa/noticias/001",
        canonical_url="https://www.cnmc.es/prensa/noticias/001",
        title="CNMC Resolución Cártel 2026",
        content="Texto íntegro de la resolución del cártel de transportes con fundamentación legal...",
        published_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(entry)
    db_session.commit()

    initial_analysis_calls = db_session.query(AnalysisCall).count()
    initial_entry_analyses = db_session.query(EntryAnalysis).count()

    # Mock ingestion service to return this duplicate item
    mock_ingest = MagicMock()
    mock_ingest.ingest_source.return_value = IngestionResult(
        ingestion_run_id=uuid.uuid4(),
        source_id=test_sources["cnmc"].id,
        status="success",
        fetched=1,
        created=0,
        duplicates=1,
        started_at=now,
        finished_at=now,
    )

    service = WeeklyRefreshService(ingestion_service=mock_ingest)
    report = service.run_weekly_refresh(
        db=db_session,
        confirm_real_calls=True,
        sources_filter=["CNMC Test"],
    )

    assert report.total_new_entries == 0
    assert report.total_analyzed == 0
    assert report.total_duplicates == 1

    # Invariance check: zero new calls and zero new analyses created
    assert db_session.query(AnalysisCall).count() == initial_analysis_calls
    assert db_session.query(EntryAnalysis).count() == initial_entry_analyses


def test_scheduler_compute_next_run_and_tz():
    """Verify compute_next_run schedules correctly for Europe/Madrid in the future."""
    # Monday 05:00 UTC (07:00 Madrid in summer CEST, or 06:00 Madrid in winter CET)
    ref_time = datetime(2026, 9, 14, 10, 0, 0, tzinfo=timezone.utc)  # A Monday afternoon
    next_run = compute_next_run(
        now_dt=ref_time,
        target_day="monday",
        target_hour=6,
        target_minute=0,
        tz_str="Europe/Madrid",
    )

    # Next run must be in the future
    assert next_run.astimezone(timezone.utc) > ref_time
    assert next_run.weekday() == 0  # Monday
    assert next_run.hour == 6
    assert next_run.minute == 0


def test_scheduler_loop_disabled_idles():
    """When WEEKLY_REFRESH_ENABLED=false, scheduler loop idles without executing."""
    import threading

    settings = Settings(WEEKLY_REFRESH_ENABLED=False)
    stop_event = threading.Event()

    # Trigger stop after a brief timeout
    threading.Timer(0.1, stop_event.set).start()

    with patch("app.scheduler.get_settings", return_value=settings):
        # Should return quickly when stop_event is set without calling any services
        run_scheduler_loop(stop_event=stop_event)
