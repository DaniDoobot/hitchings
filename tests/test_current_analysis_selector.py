"""Tests for CurrentAnalysisService and analysis state selection (Bloque 7G)."""

import uuid
from datetime import datetime, timezone, timedelta
import pytest

from app.models.analysis import EntryAnalysis
from app.models.entry import Entry
from app.services.analysis_service import compute_analysis_input_hash
from app.services.current_analysis_service import (
    select_current_analysis,
    is_analysis_stale,
    get_entry_analysis_state,
    parse_pipeline_version_num,
)


def test_parse_pipeline_version_num():
    assert parse_pipeline_version_num("v4") == 4
    assert parse_pipeline_version_num("v3") == 3
    assert parse_pipeline_version_num("v2") == 2
    assert parse_pipeline_version_num("v1") == 1
    assert parse_pipeline_version_num("v10") == 10
    assert parse_pipeline_version_num(None) == 0


def test_completed_latest_version_selected():
    """When multiple completed analyses exist on the same current content, highest numeric version wins."""
    entry = Entry(
        id=uuid.uuid4(),
        title="Sample Entry",
        content="Current content for analysis",
        content_hash="ingestion_hash",
    )
    current_hash = compute_analysis_input_hash(entry)

    ea_v2 = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v2",
        status="completed",
        entry_content_hash=current_hash,
        matrix_snapshot_hash="m_hash",
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    ea_v3 = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v3",
        status="completed",
        entry_content_hash=current_hash,
        matrix_snapshot_hash="m_hash",
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    ea_v4 = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v4",
        status="completed",
        entry_content_hash=current_hash,
        matrix_snapshot_hash="m_hash",
        created_at=datetime.now(timezone.utc),
    )

    selected = select_current_analysis(entry, [ea_v2, ea_v3, ea_v4])
    assert selected is not None
    assert selected.id == ea_v4.id
    assert selected.pipeline_version == "v4"


def test_failed_analysis_never_selected_as_current():
    """A failed analysis (e.g. Livronsa v3) must never be selected as current, even if latest version."""
    entry = Entry(
        id=uuid.uuid4(),
        title="Livronsa Sample",
        content="Livronsa content",
        content_hash="ingestion_hash",
    )
    current_hash = compute_analysis_input_hash(entry)

    ea_v2 = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v2",
        status="completed",
        entry_content_hash=current_hash,
        matrix_snapshot_hash="m_hash",
    )
    ea_v3_failed = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v3",
        status="failed",
        entry_content_hash=current_hash,
        matrix_snapshot_hash="m_hash",
    )

    # v3 is failed, so v2 is selected as current
    selected = select_current_analysis(entry, [ea_v2, ea_v3_failed])
    assert selected is not None
    assert selected.id == ea_v2.id
    assert selected.pipeline_version == "v2"

    # If only failed analyses exist, select returns None
    selected_failed_only = select_current_analysis(entry, [ea_v3_failed])
    assert selected_failed_only is None


def test_stale_analysis_never_selected_as_current():
    """An analysis performed on old content is stale and must not be selected as current."""
    entry = Entry(
        id=uuid.uuid4(),
        title="CAT Judgment",
        content="Enriched full PDF content with 15,000 characters",
        content_hash="ingestion_hash",
    )
    current_hash = compute_analysis_input_hash(entry)

    # Simulated v2 analysis run on original 32 characters
    ea_v2_stale = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v2",
        status="completed",
        entry_content_hash="old_32_chars_hash",
        matrix_snapshot_hash="m_hash",
    )

    assert is_analysis_stale(ea_v2_stale, entry) is True
    assert select_current_analysis(entry, [ea_v2_stale]) is None

    # Now add v3 analysis run on current 15,000 characters
    ea_v3_current = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v3",
        status="completed",
        entry_content_hash=current_hash,
        matrix_snapshot_hash="m_hash",
    )

    selected = select_current_analysis(entry, [ea_v2_stale, ea_v3_current])
    assert selected is not None
    assert selected.id == ea_v3_current.id
    assert selected.pipeline_version == "v3"


def test_tie_breaking_by_created_at():
    """If two completed analyses have the same version, more recent created_at wins."""
    entry = Entry(
        id=uuid.uuid4(),
        title="Tie breaker entry",
        content="Content text",
        content_hash="ingestion_hash",
    )
    current_hash = compute_analysis_input_hash(entry)

    now = datetime.now(timezone.utc)
    ea_older = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v4",
        status="completed",
        entry_content_hash=current_hash,
        matrix_snapshot_hash="m_hash",
        created_at=now - timedelta(hours=1),
    )
    ea_newer = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v4",
        status="completed",
        entry_content_hash=current_hash,
        matrix_snapshot_hash="m_hash",
        created_at=now,
    )

    selected = select_current_analysis(entry, [ea_older, ea_newer])
    assert selected is not None
    assert selected.id == ea_newer.id


def test_get_entry_analysis_state_diagnostics():
    """Verify diagnostic state flags (needs_v4_baseline, stale_count, etc.)."""
    entry = Entry(
        id=uuid.uuid4(),
        title="Diag Entry",
        content="Diag content",
        content_hash="ingestion_hash",
    )
    current_hash = compute_analysis_input_hash(entry)

    ea_v2_stale = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v2",
        status="completed",
        entry_content_hash="different_hash",
        matrix_snapshot_hash="m_hash",
    )
    ea_v3 = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        pipeline_version="v3",
        status="completed",
        entry_content_hash=current_hash,
        matrix_snapshot_hash="m_hash",
        relevance_score=85,
        relevance_status="relevant",
    )

    state = get_entry_analysis_state(entry, [ea_v2_stale, ea_v3])
    assert state["has_current"] is True
    assert state["current_version"] == "v3"
    assert state["stale_count"] == 1
    assert state["needs_v4_baseline"] is True
    assert state["has_v4_completed"] is False
