"""Comprehensive tests for Scheduler Cadence and Daily/Weekly Refresh Pipeline (Bloque 10)."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.advisory_lock import RefreshAdvisoryLock, _MEMORY_LOCKS, _MEMORY_LOCK_MUTEX
from app.core.config import Settings
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.schemas.ingestion import IngestionResult
from app.scheduler import compute_next_run, run_scheduler_loop
from app.services.direct_web_ingestion_service import DirectWebIngestionReport
from app.services.google_news_ingestion_service import GoogleNewsIngestionReport
from app.services.incremental_analysis_service import IncrementalAnalysisReport
from app.services.linkedin_ingestion_service import LinkedInIngestionReport
from app.services.weekly_refresh_service import WeeklyRefreshReport, WeeklyRefreshService


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
    """Ensure test sources exist in the test DB."""
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

    sources["cnmc"] = get_or_create("CNMC Daily Test", SourceType.WEBSITE, "native", "https://www.cnmc.es/prensa/noticias")
    sources["chillin"] = get_or_create("Chillin Daily Test", SourceType.BLOG, "native", "https://chillingcompetition.com/")
    sources["chillin"].config = {"adapter": "chillin_competition", "feed_url": "https://chillingcompetition.com/feed/"}
    db_session.commit()
    return sources


# =============================================================================
# A. Scheduler daily & E. Next run calculation
# =============================================================================

def test_compute_next_run_daily_before_target_hour():
    """When now is before target hour on the current day, next run is today at target hour."""
    madrid_tz = ZoneInfo("Europe/Madrid")
    ref_time = datetime(2026, 10, 7, 4, 30, 0, tzinfo=madrid_tz)

    next_run = compute_next_run(
        now_dt=ref_time,
        cadence="daily",
        target_hour=6,
        target_minute=0,
        tz_str="Europe/Madrid",
    )

    assert next_run > ref_time
    assert next_run.date() == ref_time.date()
    assert next_run.hour == 6
    assert next_run.minute == 0
    assert next_run.tzinfo == madrid_tz


def test_compute_next_run_daily_after_target_hour():
    """When now is after target hour on the current day, next run is tomorrow at target hour."""
    madrid_tz = ZoneInfo("Europe/Madrid")
    ref_time = datetime(2026, 10, 7, 7, 15, 0, tzinfo=madrid_tz)

    next_run = compute_next_run(
        now_dt=ref_time,
        cadence="daily",
        target_hour=6,
        target_minute=0,
        tz_str="Europe/Madrid",
    )

    assert next_run > ref_time
    assert next_run.date() == ref_time.date() + timedelta(days=1)
    assert next_run.hour == 6
    assert next_run.minute == 0


def test_compute_next_run_daily_exact_moment_and_restart():
    """When now is exactly at target time (or seconds after), next run is strictly scheduled for tomorrow."""
    madrid_tz = ZoneInfo("Europe/Madrid")
    exact_time = datetime(2026, 10, 7, 6, 0, 0, tzinfo=madrid_tz)

    next_run = compute_next_run(
        now_dt=exact_time,
        cadence="daily",
        target_hour=6,
        target_minute=0,
        tz_str="Europe/Madrid",
    )

    assert next_run > exact_time
    assert next_run.date() == exact_time.date() + timedelta(days=1)
    assert next_run.hour == 6

    # Scheduler restarts 5 seconds after run completed
    restart_time = datetime(2026, 10, 7, 6, 0, 5, tzinfo=madrid_tz)
    next_run_restart = compute_next_run(
        now_dt=restart_time,
        cadence="daily",
        target_hour=6,
        target_minute=0,
        tz_str="Europe/Madrid",
    )
    assert next_run_restart > restart_time
    assert next_run_restart.date() == exact_time.date() + timedelta(days=1)


# =============================================================================
# B. Scheduler weekly & K. Weekly compatibility
# =============================================================================

def test_compute_next_run_weekly_preserves_behaviour():
    """Weekly cadence maintains exact original behavior (schedules for next target weekday)."""
    madrid_tz = ZoneInfo("Europe/Madrid")
    # Wednesday 2026-10-07
    ref_time = datetime(2026, 10, 7, 10, 0, 0, tzinfo=madrid_tz)
    assert ref_time.weekday() == 2  # Wednesday

    next_run = compute_next_run(
        now_dt=ref_time,
        cadence="weekly",
        target_day="monday",
        target_hour=6,
        target_minute=0,
        tz_str="Europe/Madrid",
    )

    assert next_run > ref_time
    assert next_run.weekday() == 0  # Monday
    assert next_run.date() == datetime(2026, 10, 12).date()
    assert next_run.hour == 6
    assert next_run.minute == 0


def test_compute_next_run_weekly_on_target_day_before_target_time():
    """Weekly on Monday 05:00 schedules for today (Monday) 06:00."""
    madrid_tz = ZoneInfo("Europe/Madrid")
    ref_time = datetime(2026, 10, 12, 5, 0, 0, tzinfo=madrid_tz)  # Monday 05:00
    assert ref_time.weekday() == 0

    next_run = compute_next_run(
        now_dt=ref_time,
        cadence="weekly",
        target_day="monday",
        target_hour=6,
        target_minute=0,
        tz_str="Europe/Madrid",
    )

    assert next_run > ref_time
    assert next_run.date() == ref_time.date()
    assert next_run.hour == 6


def test_compute_next_run_backward_compatibility_positional_args():
    """compute_next_run accepts target_day as second positional argument without cadence kwarg."""
    ref_time = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)
    # Called with original signature: now_dt, target_day, target_hour, target_minute, tz_str
    next_run = compute_next_run(ref_time, "monday", 6, 0, "Europe/Madrid")
    assert next_run.weekday() == 0
    assert next_run.hour == 6


def test_compute_next_run_positional_daily_auto_detected():
    """compute_next_run detects 'daily' passed positionally as target_day."""
    madrid_tz = ZoneInfo("Europe/Madrid")
    ref_time = datetime(2026, 10, 7, 5, 0, 0, tzinfo=madrid_tz)
    next_run = compute_next_run(ref_time, "daily", 6, 0, "Europe/Madrid")
    assert next_run.date() == ref_time.date()
    assert next_run.hour == 6


# =============================================================================
# C. Invalid configuration
# =============================================================================

def test_invalid_scheduler_cadence_setting_raises_validation_error():
    """Settings raises ValidationError when SCHEDULER_CADENCE is invalid."""
    with pytest.raises(ValidationError):
        Settings(SCHEDULER_CADENCE="monthly")

    with pytest.raises(ValidationError):
        Settings(SCHEDULER_CADENCE="hourly")


def test_invalid_cadence_in_compute_next_run_raises_value_error():
    """compute_next_run raises ValueError for unsupported cadence."""
    ref_time = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="Invalid cadence"):
        compute_next_run(ref_time, cadence="biweekly")


def test_invalid_target_hour_and_minute_raises_value_error():
    """compute_next_run raises ValueError for out-of-range hours and minutes."""
    ref_time = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="Invalid target_hour"):
        compute_next_run(ref_time, target_hour=24)

    with pytest.raises(ValueError, match="Invalid target_minute"):
        compute_next_run(ref_time, target_minute=60)


def test_invalid_target_day_in_weekly_raises_value_error():
    """compute_next_run raises ValueError for invalid weekday name in weekly cadence."""
    ref_time = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="Invalid target_day"):
        compute_next_run(ref_time, cadence="weekly", target_day="funday")


# =============================================================================
# D. Configured hour & Daylight Saving Time (DST)
# =============================================================================

def test_configurable_daily_hour_and_minute():
    """compute_next_run honors custom target hour and minute."""
    madrid_tz = ZoneInfo("Europe/Madrid")
    ref_time = datetime(2026, 10, 7, 10, 0, 0, tzinfo=madrid_tz)

    next_run = compute_next_run(
        now_dt=ref_time,
        cadence="daily",
        target_hour=14,
        target_minute=30,
        tz_str="Europe/Madrid",
    )

    assert next_run.date() == ref_time.date()
    assert next_run.hour == 14
    assert next_run.minute == 30


def test_compute_next_run_dst_transition_preserves_target_hour():
    """During DST clock change (spring CEST transition), the local hour 06:00 is preserved."""
    madrid_tz = ZoneInfo("Europe/Madrid")
    # Saturday before spring DST change (last Sunday of March 2027: March 28)
    ref_time = datetime(2027, 3, 27, 20, 0, 0, tzinfo=madrid_tz)

    next_run = compute_next_run(
        now_dt=ref_time,
        cadence="daily",
        target_hour=6,
        target_minute=0,
        tz_str="Europe/Madrid",
    )

    assert next_run.date() == datetime(2027, 3, 28).date()
    assert next_run.hour == 6
    assert next_run.minute == 0


# =============================================================================
# F. Daily window & G. Weekly window
# =============================================================================

def test_daily_lookback_default_is_3(db_session: Session):
    """When cadence is 'daily', WeeklyRefreshService defaults lookback to 3 days."""
    settings = Settings(SCHEDULER_CADENCE="daily")
    service = WeeklyRefreshService(settings=settings)
    report = service.run_weekly_refresh(
        db=db_session,
        lookback_days=None,
        confirm_real_calls=False,
    )
    assert report.cadence == "daily"
    assert report.lookback_days == 3


def test_weekly_lookback_default_is_8(db_session: Session):
    """When cadence is 'weekly', WeeklyRefreshService defaults lookback to 8 days."""
    settings = Settings(SCHEDULER_CADENCE="weekly")
    service = WeeklyRefreshService(settings=settings)
    report = service.run_weekly_refresh(
        db=db_session,
        lookback_days=None,
        confirm_real_calls=False,
    )
    assert report.cadence == "weekly"
    assert report.lookback_days == 8


def test_explicit_lookback_overrides_cadence_defaults(db_session: Session):
    """Explicit lookback_days takes precedence over both daily and weekly defaults."""
    settings = Settings(SCHEDULER_CADENCE="daily")
    service = WeeklyRefreshService(settings=settings)
    report = service.run_weekly_refresh(
        db=db_session,
        lookback_days=10,
        confirm_real_calls=False,
    )
    assert report.lookback_days == 10


# =============================================================================
# H. Full pipeline using daily & L. Analysis of new entries
# =============================================================================

def test_daily_pipeline_full_orchestration_with_analysis(
    db_session: Session, active_matrix: TrackingMatrix, test_sources: dict[str, Source]
):
    """Complete daily pipeline runs sources, creates entries, deduplicates, and passes new entries to AI analysis."""
    now = datetime.now(timezone.utc)
    source_cnmc = test_sources["cnmc"]

    created_entry_id = uuid.uuid4()

    def mock_ingest_side_effect(source_id, db, *args, **kwargs):
        now_entry = datetime.now(timezone.utc)
        entry = Entry(
            id=created_entry_id,
            source_id=source_cnmc.id,
            external_id=f"cnmc-daily-{uuid.uuid4().hex[:6]}",
            url="https://www.cnmc.es/prensa/noticias/daily-001",
            title="CNMC Resolución Diaria 2026",
            content="Contenido extenso con suficiente fundamentación jurídica para análisis " * 30,
            published_at=now_entry,
            created_at=now_entry,
            captured_at=now_entry,
            updated_at=now_entry,
        )
        db.add(entry)
        db.commit()
        return IngestionResult(
            ingestion_run_id=uuid.uuid4(),
            source_id=source_cnmc.id,
            status="success",
            fetched=3,
            created=1,
            duplicates=2,
            started_at=now_entry,
            finished_at=now_entry,
        )

    mock_ingest = MagicMock()
    mock_ingest.ingest_source.side_effect = mock_ingest_side_effect

    mock_analysis_svc = MagicMock()
    mock_analysis_svc.execute_incremental_run.return_value = IncrementalAnalysisReport(
        run_id="test-daily-run",
        completed=1,
        failed=0,
        skipped_budget=0,
    )

    settings = Settings(
        SCHEDULER_CADENCE="daily",
        SCHEDULER_DAILY_LOOKBACK_DAYS=3,
    )
    service = WeeklyRefreshService(
        settings=settings,
        ingestion_service=mock_ingest,
        incremental_analysis_service=mock_analysis_svc,
    )

    report = service.run_weekly_refresh(
        db=db_session,
        confirm_real_calls=True,
        sources_filter=["CNMC Daily Test"],
        cadence="daily",
    )

    assert report.status == "completed"
    assert report.cadence == "daily"
    assert report.lookback_days == 3
    assert report.total_new_entries == 1
    assert report.total_duplicates == 2
    assert report.total_analyzed == 1
    assert report.summary_text().startswith("Daily refresh completed")

    # Verify lookback was passed down to native source
    mock_ingest.ingest_source.assert_called_once_with(
        source_cnmc.id,
        db_session,
        lookback_days=3,
    )

    # Verify analysis service was called for the new entry
    mock_analysis_svc.execute_incremental_run.assert_called_once()


# =============================================================================
# I. Concurrency protection (RefreshAdvisoryLock)
# =============================================================================

def test_advisory_lock_prevents_simultaneous_daily_runs(db_session: Session):
    """When RefreshAdvisoryLock is already acquired, a daily refresh run exits cleanly as 'already_running'."""
    lock = RefreshAdvisoryLock(db_session)
    assert lock.acquire() is True

    try:
        settings = Settings(SCHEDULER_CADENCE="daily")
        service = WeeklyRefreshService(settings=settings)
        report = service.run_weekly_refresh(
            db=db_session,
            cadence="daily",
            confirm_real_calls=False,
        )
        assert report.status == "already_running"
        assert report.cadence == "daily"
        assert report.sources_attempted == 0
    finally:
        lock.release()


# =============================================================================
# J. Resumption after failure
# =============================================================================

def test_source_failure_isolation_in_daily_mode(
    db_session: Session, active_matrix: TrackingMatrix, test_sources: dict[str, Source]
):
    """Failure in one source does not crash the daily pipeline; other sources succeed."""
    mock_ingest = MagicMock()
    mock_ingest.ingest_source.side_effect = ConnectionError("Source network timeout")

    mock_dw = MagicMock()
    mock_dw.execute_ingestion.return_value = DirectWebIngestionReport(
        is_dry_run=False,
        total_discovered=2,
        total_created=0,
        total_duplicates=2,
        total_failed=0,
    )

    service = WeeklyRefreshService(
        settings=Settings(SCHEDULER_CADENCE="daily"),
        ingestion_service=mock_ingest,
        direct_web_service=mock_dw,
    )

    report = service.run_weekly_refresh(
        db=db_session,
        confirm_real_calls=True,
        sources_filter=["CNMC Daily Test", "Chillin Daily Test"],
        cadence="daily",
    )

    assert report.status == "completed"
    assert report.sources_attempted == 2
    assert report.sources_failed == 1
    assert report.sources_successful == 1
    assert report.total_errors >= 1


def test_scheduler_loop_catches_run_exception_and_resumes():
    """In run_scheduler_loop, a critical exception during a refresh run is caught and does not crash the loop."""
    settings = Settings(
        SCHEDULER_CADENCE="daily",
        WEEKLY_REFRESH_ENABLED=True,
    )
    stop_event = threading.Event()

    with patch("app.scheduler.get_settings", return_value=settings), \
         patch("app.scheduler.compute_next_run") as mock_compute, \
         patch("app.scheduler.WeeklyRefreshService.run_weekly_refresh", side_effect=RuntimeError("Transient DB connection drop")):

        # Immediately trigger
        now = datetime.now(timezone.utc)
        mock_compute.return_value = now

        # Run once to test the execution block
        run_scheduler_loop(stop_event=stop_event, run_once=True)

        # Loop executed and caught the exception cleanly without raising
        assert True


def test_scheduler_loop_daily_executes_run_once():
    """run_scheduler_loop in daily mode triggers refresh with daily lookback (3 days) and cadence='daily'."""
    settings = Settings(
        SCHEDULER_CADENCE="daily",
        SCHEDULER_DAILY_HOUR=6,
        SCHEDULER_DAILY_MINUTE=0,
        SCHEDULER_DAILY_LOOKBACK_DAYS=3,
        WEEKLY_REFRESH_ENABLED=True,
    )
    stop_event = threading.Event()
    mock_service_instance = MagicMock()
    mock_service_instance.run_weekly_refresh.return_value = WeeklyRefreshReport(
        status="completed",
        cadence="daily",
        lookback_days=3,
    )

    with patch("app.scheduler.get_settings", return_value=settings), \
         patch("app.scheduler.compute_next_run") as mock_compute, \
         patch("app.scheduler.WeeklyRefreshService", return_value=mock_service_instance), \
         patch("app.scheduler.SessionLocal") as mock_session_local:

        now = datetime.now(timezone.utc)
        mock_compute.return_value = now
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        run_scheduler_loop(stop_event=stop_event, run_once=True)

        mock_service_instance.run_weekly_refresh.assert_called_once_with(
            db=mock_db,
            lookback_days=3,
            confirm_real_calls=True,
            cadence="daily",
        )
        mock_db.close.assert_called_once()


def test_cli_weekly_refresh_cadence_flag():
    """CLI script weekly_refresh parses --cadence daily and executes cleanly."""
    from scripts.weekly_refresh import main as cli_main

    mock_report = WeeklyRefreshReport(
        status="completed",
        cadence="daily",
        lookback_days=3,
        sources_attempted=1,
        sources_successful=1,
    )

    with patch("sys.argv", ["scripts.weekly_refresh", "--cadence", "daily", "--dry-run"]), \
         patch("scripts.weekly_refresh.WeeklyRefreshService.run_weekly_refresh", return_value=mock_report) as mock_run:
        exit_code = cli_main()
        assert exit_code == 0
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cadence"] == "daily"

