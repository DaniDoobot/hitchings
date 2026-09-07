"""Tests for IngestionRun traceability, status calculation, freshness, and observability endpoints."""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.providers.native import NativeProvider
from app.providers.base import RawEntryData, ProviderError
from app.services.ingestion_service import IngestionService
from app.schemas.ingestion import FreshnessStatus


# ==============================================================================
# 1. INGESTION RUN LIFECYCLE TESTS (SUCCESS & DUPLICATES)
# ==============================================================================

@pytest.mark.asyncio
async def test_ingestion_run_success(db_session: Session) -> None:
    """Test that a successful ingestion creates an IngestionRun and updates source timestamps."""
    source = Source(
        name="Test IngestionRun Source",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://example.com/news",
    )
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)

    mock_entries = [
        RawEntryData(
            url="https://example.com/news/1",
            title="News Item 1",
            published_at=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
        ),
        RawEntryData(
            url="https://example.com/news/2",
            title="News Item 2",
            published_at=datetime(2026, 8, 30, 10, 0, 0, tzinfo=timezone.utc),
        ),
    ]

    service = IngestionService()
    with patch.object(NativeProvider, "fetch_entries", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_entries
        result = await service.ingest_source(source.id, db_session)

    assert result.status == IngestionRunStatus.SUCCESS.value
    assert result.fetched == 2
    assert result.created == 2
    assert result.duplicates == 0
    assert result.failed == 0
    assert result.ingestion_run_id is not None
    assert result.duration_ms is not None

    # Verify run persisted in database
    run = db_session.get(IngestionRun, result.ingestion_run_id)
    assert run is not None
    assert run.source_id == source.id
    assert run.status == IngestionRunStatus.SUCCESS.value
    assert run.fetched_count == 2
    assert run.latest_published_at.year == 2026
    assert run.latest_published_at.month == 9
    assert run.latest_published_at.day == 1
    assert run.oldest_published_at.year == 2026
    assert run.oldest_published_at.month == 8
    assert run.oldest_published_at.day == 30

    # Verify source timestamps
    db_session.refresh(source)
    assert source.last_run_at is not None
    assert source.last_success_at is not None


@pytest.mark.asyncio
async def test_ingestion_run_duplicates_creates_independent_run(db_session: Session) -> None:
    """Test that consecutive ingestion creates a distinct IngestionRun with duplicates counted."""
    source = Source(
        name="Test Duplicates Run Source",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://example.com/dup-news",
    )
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)

    mock_entries = [
        RawEntryData(
            url="https://example.com/dup-news/1",
            title="Duplicate Item 1",
            published_at=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
    ]

    service = IngestionService()
    with patch.object(NativeProvider, "fetch_entries", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_entries

        # Run 1
        res1 = await service.ingest_source(source.id, db_session)
        assert res1.created == 1
        assert res1.duplicates == 0

        # Run 2
        res2 = await service.ingest_source(source.id, db_session)
        assert res2.created == 0
        assert res2.duplicates == 1
        assert res2.status == IngestionRunStatus.SUCCESS.value

    # Verify 2 distinct runs exist for this source
    runs = db_session.execute(
        select(IngestionRun)
        .where(IngestionRun.source_id == source.id)
        .order_by(IngestionRun.started_at.asc())
    ).scalars().all()

    assert len(runs) == 2
    assert runs[0].id != runs[1].id
    assert runs[0].created_count == 1
    assert runs[1].created_count == 0
    assert runs[1].duplicate_count == 1


# ==============================================================================
# 2. FAILURE & PARTIAL RUN TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_ingestion_run_failure_persists_and_preserves_last_success(db_session: Session) -> None:
    """Test that a fetch failure generates a FAILED IngestionRun and preserves last_success_at."""
    initial_success_time = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
    source = Source(
        name="Test Failing Source",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://example.com/fail",
        last_success_at=initial_success_time,
    )
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)

    service = IngestionService()
    with patch.object(NativeProvider, "fetch_entries", side_effect=ProviderError("HTTP 500 Server Error")):
        with pytest.raises(ProviderError):
            await service.ingest_source(source.id, db_session)

    # Verify IngestionRun was created despite exception
    run = db_session.execute(
        select(IngestionRun)
        .where(IngestionRun.source_id == source.id)
        .order_by(IngestionRun.started_at.desc())
        .limit(1)
    ).scalar_one()

    assert run.status == IngestionRunStatus.FAILED.value
    assert run.error_type == "ProviderError"
    assert "HTTP 500 Server Error" in run.error_message
    assert run.finished_at is not None

    # Verify source.last_success_at is strictly preserved
    db_session.refresh(source)
    assert source.last_success_at.year == initial_success_time.year
    assert source.last_success_at.month == initial_success_time.month
    assert source.last_success_at.day == initial_success_time.day
    assert source.last_success_at.hour == initial_success_time.hour
    assert source.last_run_at is not None


@pytest.mark.asyncio
async def test_ingestion_run_partial_status(db_session: Session) -> None:
    """Test that item-level failures produce status=partial and do NOT update last_success_at."""
    source = Source(
        name="Test Partial Source",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://example.com/partial",
    )
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)

    mock_entries = [
        RawEntryData(url="https://example.com/good", title="Good Item"),
        RawEntryData(url="https://example.com/bad", title="Bad Item"),
    ]

    service = IngestionService()

    # Simulate an error on the second entry during persistence
    original_compute = IngestionService.__module__

    call_count = 0

    def mock_hash(title, url, excerpt):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Corrupted encoding in entry")
        return "hash-" + str(call_count)

    with patch("app.services.ingestion_service.compute_content_hash", side_effect=mock_hash):
        with patch.object(NativeProvider, "fetch_entries", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = mock_entries
            result = await service.ingest_source(source.id, db_session)

    assert result.status == IngestionRunStatus.PARTIAL.value
    assert result.fetched == 2
    assert result.created == 1
    assert result.failed == 1

    # Verify last_success_at is NOT updated on partial
    db_session.refresh(source)
    assert source.last_success_at is None


# ==============================================================================
# 3. FRESHNESS EVALUATION TESTS
# ==============================================================================

def test_freshness_calculation_fresh(db_session: Session) -> None:
    """Test freshness calculation returns FRESH when publications are recent."""
    now_utc = datetime.now(timezone.utc)
    recent_pub = now_utc - timedelta(hours=48)  # 2 days ago

    source = Source(
        name="Fresh Source",
        type=SourceType.WEBSITE,
        config={"freshness_warning_hours": 168},
    )
    db_session.add(source)
    db_session.flush()

    run = IngestionRun(
        source_id=source.id,
        started_at=now_utc,
        finished_at=now_utc,
        status=IngestionRunStatus.SUCCESS.value,
        latest_published_at=recent_pub,
    )
    db_session.add(run)
    db_session.commit()

    status_resp = IngestionService.get_source_status(source, db_session)
    assert status_resp.freshness.status == FreshnessStatus.FRESH
    assert status_resp.freshness.warning_hours == 168
    assert status_resp.freshness.hours_since_latest is not None
    assert status_resp.freshness.hours_since_latest <= 50.0


def test_freshness_calculation_stale(db_session: Session) -> None:
    """Test freshness calculation returns STALE when publications exceed threshold."""
    now_utc = datetime.now(timezone.utc)
    old_pub = now_utc - timedelta(hours=200)  # > 168 hours

    source = Source(
        name="Stale Source",
        type=SourceType.WEBSITE,
        config={"freshness_warning_hours": 168},
    )
    db_session.add(source)
    db_session.flush()

    run = IngestionRun(
        source_id=source.id,
        started_at=now_utc,
        finished_at=now_utc,
        status=IngestionRunStatus.SUCCESS.value,
        latest_published_at=old_pub,
    )
    db_session.add(run)
    db_session.commit()

    status_resp = IngestionService.get_source_status(source, db_session)
    assert status_resp.freshness.status == FreshnessStatus.STALE
    assert status_resp.freshness.hours_since_latest > 168.0


def test_freshness_calculation_unknown(db_session: Session) -> None:
    """Test freshness calculation returns UNKNOWN when no publication dates exist."""
    source = Source(
        name="Unknown Freshness Source",
        type=SourceType.WEBSITE,
        config={"freshness_warning_hours": 72},
    )
    db_session.add(source)
    db_session.commit()

    status_resp = IngestionService.get_source_status(source, db_session)
    assert status_resp.freshness.status == FreshnessStatus.UNKNOWN
    assert status_resp.freshness.warning_hours == 72
    assert status_resp.freshness.hours_since_latest is None
    assert status_resp.last_run is None


# ==============================================================================
# 4. API ENDPOINTS TESTS (SOURCE STATUS & INGESTION RUNS)
# ==============================================================================

def test_source_status_endpoint(client: TestClient, db_session: Session) -> None:
    """Test GET /api/v1/sources/{source_id}/status endpoint."""
    source = Source(
        name="API Status Source",
        type=SourceType.WEBSITE,
        config={"freshness_warning_hours": 168},
    )
    db_session.add(source)
    db_session.commit()

    resp = client.get(f"/api/v1/sources/{source.id}/status")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    assert data["source_id"] == str(source.id)
    assert data["name"] == "API Status Source"
    assert data["active"] is True
    assert data["freshness"]["status"] == "unknown"


def test_list_and_get_ingestion_runs(client: TestClient, db_session: Session) -> None:
    """Test GET /api/v1/ingestion-runs and GET /api/v1/ingestion-runs/{id}."""
    source = Source(name="Runs List Source", type=SourceType.WEBSITE)
    db_session.add(source)
    db_session.flush()

    run1 = IngestionRun(
        source_id=source.id,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        status=IngestionRunStatus.SUCCESS.value,
        fetched_count=10,
        created_count=10,
    )
    run2 = IngestionRun(
        source_id=source.id,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        status=IngestionRunStatus.FAILED.value,
        error_type="ProviderError",
        error_message="Network failure",
    )
    db_session.add_all([run1, run2])
    db_session.commit()

    # 1. List runs filtered by source
    resp = client.get(f"/api/v1/ingestion-runs?source_id={source.id}")
    assert resp.status_code == status.HTTP_200_OK
    runs_list = resp.json()
    assert len(runs_list) == 2
    # Check descending order by started_at (run2 is more recent)
    assert runs_list[0]["id"] == str(run2.id)
    assert runs_list[1]["id"] == str(run1.id)

    # 2. Filter by status
    resp_filtered = client.get(f"/api/v1/ingestion-runs?source_id={source.id}&status=failed")
    assert resp_filtered.status_code == status.HTTP_200_OK
    assert len(resp_filtered.json()) == 1
    assert resp_filtered.json()[0]["id"] == str(run2.id)

    # 3. Get single run detail
    detail_resp = client.get(f"/api/v1/ingestion-runs/{run1.id}")
    assert detail_resp.status_code == status.HTTP_200_OK
    assert detail_resp.json()["id"] == str(run1.id)
    assert detail_resp.json()["status"] == "success"
    assert detail_resp.json()["fetched_count"] == 10

    # 4. Non-existent run
    not_found_resp = client.get(f"/api/v1/ingestion-runs/{uuid.uuid4()}")
    assert not_found_resp.status_code == status.HTTP_404_NOT_FOUND
