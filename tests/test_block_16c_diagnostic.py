"""Tests for Bloque 16C: Diagnostic and surgical repair verification for ADLC failed analysis."""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from sqlalchemy.orm import Session

from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.models.analysis import (
    AnalysisCall,
    AnalysisPromptVersion,
    EntryAnalysis,
    EntryAnalysisTopic,
)
from app.providers.extractors.autorite_concurrence import (
    ADLC_SOURCE_NAME,
    ADLC_BASE_URL,
)
from app.providers.ai.mock import MockAIProvider
from app.services.incremental_analysis_planner import IncrementalAnalysisPlanner
from app.services.incremental_analysis_service import IncrementalAnalysisService
from app.services.analysis_pipeline_service import compute_analysis_input_hash


@pytest.fixture
def active_matrix(db_session: Session) -> TrackingMatrix:
    matrix = db_session.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    if not matrix:
        matrix = TrackingMatrix(
            code=f"HITCHINGS-TEST-{uuid.uuid4().hex[:4]}",
            name="Matriz Test 16C",
            status="active",
        )
        db_session.add(matrix)
        db_session.flush()
        topic = TrackingTopic(
            matrix_id=matrix.id,
            code="merger_control",
            name="Control de Concentraciones",
            priority=1,
            active=True,
        )
        db_session.add(topic)
        db_session.commit()
    return matrix


@pytest.fixture
def v7_prompts(db_session: Session) -> tuple[AnalysisPromptVersion, AnalysisPromptVersion]:
    triage = (
        db_session.query(AnalysisPromptVersion)
        .filter(AnalysisPromptVersion.code == "observatory_triage", AnalysisPromptVersion.version == 7)
        .first()
    )
    if not triage:
        triage = AnalysisPromptVersion(
            code="observatory_triage",
            version=7,
            stage="triage",
            name="Observatory Triage v7",
            system_prompt="System instructions",
            user_prompt_template="Triage template: {{entry.content}}",
            response_schema_version="v3",
            config={"grounding_mode": "evidence_blocks_v1"},
            active=True,
        )
        db_session.add(triage)

    deep = (
        db_session.query(AnalysisPromptVersion)
        .filter(AnalysisPromptVersion.code == "observatory_deep_analysis", AnalysisPromptVersion.version == 7)
        .first()
    )
    if not deep:
        deep = AnalysisPromptVersion(
            code="observatory_deep_analysis",
            version=7,
            stage="deep_analysis",
            name="Observatory Deep Analysis v7",
            system_prompt="System instructions",
            user_prompt_template="Deep template: {{entry.content}}",
            response_schema_version="v3",
            config={"grounding_mode": "evidence_blocks_v1"},
            active=True,
        )
        db_session.add(deep)

    db_session.commit()
    return triage, deep


def test_adlc_inventory_breakdown_48_completed_1_failed(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v7_prompts: tuple,
):
    """Verify inventory breakdown: 49 entries, 48 completed, exactly 1 failed."""
    triage_prompt, _ = v7_prompts

    source = Source(
        id=uuid.uuid4(),
        name=ADLC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=ADLC_BASE_URL,
        active=True,
    )
    db_session.add(source)
    db_session.flush()

    now = datetime.now(timezone.utc)

    # Create 48 completed entries
    completed_entries = []
    for i in range(1, 49):
        entry = Entry(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id=f"adlc:act:26-dcc-{100 + i}",
            url=f"https://www.autoritedelaconcurrence.fr/fr/decision/26-dcc-{100 + i}",
            title=f"Décision 26-DCC-{100 + i}",
            content="Contenu substantiel concentration " * 60,
            published_at=now - timedelta(days=i),
            raw_metadata={"official_id": f"26-DCC-{100 + i}"},
        )
        db_session.add(entry)
        db_session.flush()

        ea = EntryAnalysis(
            id=uuid.uuid4(),
            entry_id=entry.id,
            matrix_id=active_matrix.id,
            pipeline_version="v6",
            status="completed",
            relevance_status="uncertain",
            relevance_score=60,
            entry_content_hash=compute_analysis_input_hash(entry),
            matrix_snapshot={"topics": []},
            matrix_snapshot_hash="fakehash",
            started_at=now,
            completed_at=now,
        )
        db_session.add(ea)
        completed_entries.append(entry)

    # Create 1 failed entry
    failed_entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="adlc:act:26-dcc-199",
        url="https://www.autoritedelaconcurrence.fr/fr/decision/26-dcc-199",
        title="Décision 26-DCC-199 du 2 septembre 2026",
        content="Contenu substantiel décision 199 " * 60,
        published_at=now - timedelta(days=12),
        raw_metadata={
            "official_id": "26-DCC-199",
            "act_type": "decision",
            "phase": "phase_1",
            "pdf_url": "https://www.autoritedelaconcurrence.fr/sites/default/files/26-dcc-199.pdf",
            "pdf_extracted": True,
        },
    )
    db_session.add(failed_entry)
    db_session.flush()

    failed_ea = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=failed_entry.id,
        matrix_id=active_matrix.id,
        pipeline_version="v6",
        status="failed",
        reason="Rate limit exceeded: 429 ResourceExhausted",
        entry_content_hash=compute_analysis_input_hash(failed_entry),
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="fakehash",
        started_at=now,
        completed_at=now,
    )
    db_session.add(failed_ea)
    db_session.flush()

    failed_call = AnalysisCall(
        id=uuid.uuid4(),
        entry_analysis_id=failed_ea.id,
        prompt_version_id=triage_prompt.id,
        stage="triage",
        provider="gemini_api",
        model="gemini-2.5-flash",
        status="failed",
        input_chars=5000,
        output_chars=0,
        input_tokens=1250,
        output_tokens=0,
        error_type="ResourceExhausted",
        error_message="Resource has been exhausted (e.g. check quota): 429 Quota exceeded",
        call_metadata={"grounding_mode": "evidence_blocks_v1", "run_id": "bf-299e21c0"},
        started_at=now,
        completed_at=now,
    )
    db_session.add(failed_call)
    db_session.commit()

    # Verify inventory counts
    all_adlc_entries = db_session.query(Entry).filter(Entry.source_id == source.id).all()
    assert len(all_adlc_entries) == 49

    # Incremental Planner must find EXACTLY 1 eligible candidate (the failed one)
    planner = IncrementalAnalysisPlanner(db=db_session)
    plan = planner.plan(source_id=source.id)

    assert plan.total_entries_inspected == 49
    assert plan.already_current_count == 48
    assert plan.eligible_count == 1
    assert len(plan.candidates) == 1
    candidate = plan.candidates[0]
    assert candidate.entry_id == failed_entry.id
    assert candidate.reason == "eligible"


@pytest.mark.asyncio
async def test_surgical_retry_only_analyzes_failed_entry(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v7_prompts: tuple,
):
    """Verify surgical retry via IncrementalAnalysisService targets only the single failed entry."""
    triage_prompt, _ = v7_prompts

    source = Source(
        id=uuid.uuid4(),
        name=ADLC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=ADLC_BASE_URL,
        active=True,
    )
    db_session.add(source)
    db_session.flush()

    now = datetime.now(timezone.utc)

    # 1. Seed completed entry
    e_completed = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="adlc:act:26-dcc-180",
        url="https://www.autoritedelaconcurrence.fr/fr/decision/26-dcc-180",
        title="Décision 26-DCC-180",
        content="Contenu substantiel concentration 180 " * 60,
        published_at=now - timedelta(days=5),
        raw_metadata={"official_id": "26-DCC-180"},
    )
    db_session.add(e_completed)
    db_session.flush()
    ea_completed = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=e_completed.id,
        matrix_id=active_matrix.id,
        pipeline_version="v6",
        status="completed",
        relevance_status="uncertain",
        relevance_score=65,
        entry_content_hash=compute_analysis_input_hash(e_completed),
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="fakehash",
        started_at=now,
        completed_at=now,
    )
    db_session.add(ea_completed)

    # 2. Seed failed entry
    e_failed = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="adlc:act:26-dcc-179",
        url="https://www.autoritedelaconcurrence.fr/fr/decision/26-dcc-179",
        title="Décision 26-DCC-179 du 2 septembre 2026",
        content="Contenu substantiel concentration 179 " * 60,
        published_at=now - timedelta(days=12),
        raw_metadata={"official_id": "26-DCC-179"},
    )
    db_session.add(e_failed)
    db_session.flush()
    ea_failed = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=e_failed.id,
        matrix_id=active_matrix.id,
        pipeline_version="v6",
        status="failed",
        reason="ResourceExhausted",
        entry_content_hash=compute_analysis_input_hash(e_failed),
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="fakehash",
        started_at=now,
        completed_at=now,
    )
    db_session.add(ea_failed)
    db_session.commit()

    # 3. Execute targeted retry using IncrementalAnalysisService
    service = IncrementalAnalysisService(provider=MockAIProvider())
    report = await service.execute_incremental_analysis(
        db=db_session,
        confirm_real_calls=True,
        entry_id=e_failed.id,
        limit=1,
    )

    assert report.planned == 1
    assert report.attempted == 1
    assert report.completed == 1
    assert report.failed == 0

    # 4. Verify DB state:
    # e_completed analysis was NOT re-run, original ea_completed remains unchanged
    refreshed_ea_completed = db_session.get(EntryAnalysis, ea_completed.id)
    assert refreshed_ea_completed.status == "completed"
    assert refreshed_ea_completed.relevance_score == 65

    # e_failed now has a newly completed analysis
    new_analyses = (
        db_session.query(EntryAnalysis)
        .filter(EntryAnalysis.entry_id == e_failed.id)
        .order_by(EntryAnalysis.created_at.desc())
        .all()
    )
    assert len(new_analyses) == 2  # old failed + new completed
    latest = new_analyses[0]
    assert latest.status == "completed"


def test_run_diagnostic_function(
    db_session: Session,
    active_matrix: TrackingMatrix,
    v7_prompts: tuple,
):
    """Verify run_diagnostic() executes cleanly against DB and outputs expected structure."""
    from scripts.diagnose_adlc_failed_analysis import run_diagnostic

    triage_prompt, _ = v7_prompts
    now = datetime.now(timezone.utc)

    source = Source(
        id=uuid.uuid4(),
        name=ADLC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=ADLC_BASE_URL,
        active=True,
    )
    db_session.add(source)
    db_session.flush()

    e1 = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="adlc:act:26-dcc-180",
        url="https://www.autoritedelaconcurrence.fr/fr/decision/26-dcc-180",
        title="Décision 26-DCC-180",
        content="Contenu substantiel concentration 180 " * 60,
        published_at=now - timedelta(days=5),
        raw_metadata={"official_id": "26-DCC-180"},
    )
    db_session.add(e1)
    db_session.flush()
    ea1 = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=e1.id,
        matrix_id=active_matrix.id,
        pipeline_version="v6",
        status="completed",
        relevance_status="uncertain",
        relevance_score=65,
        entry_content_hash=compute_analysis_input_hash(e1),
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="fakehash",
        started_at=now,
        completed_at=now,
    )
    db_session.add(ea1)

    e2 = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="adlc:act:26-dcc-179",
        url="https://www.autoritedelaconcurrence.fr/fr/decision/26-dcc-179",
        title="Décision 26-DCC-179",
        content="Contenu substantiel concentration 179 " * 60,
        published_at=now - timedelta(days=12),
        raw_metadata={"official_id": "26-DCC-179"},
    )
    db_session.add(e2)
    db_session.flush()
    ea2 = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=e2.id,
        matrix_id=active_matrix.id,
        pipeline_version="v6",
        status="failed",
        reason="ResourceExhausted",
        entry_content_hash=compute_analysis_input_hash(e2),
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="fakehash",
        started_at=now,
        completed_at=now,
    )
    db_session.add(ea2)
    call = AnalysisCall(
        id=uuid.uuid4(),
        entry_analysis_id=ea2.id,
        prompt_version_id=triage_prompt.id,
        stage="triage",
        provider="gemini_api",
        model="gemini-2.5-flash",
        status="failed",
        error_type="ResourceExhausted",
        error_message="Resource has been exhausted: 429",
        call_metadata={"grounding_mode": "evidence_blocks_v1"},
        started_at=now,
        completed_at=now,
    )
    db_session.add(call)
    db_session.commit()

    with patch("scripts.diagnose_adlc_failed_analysis.SessionLocal", return_value=db_session):
        diag = run_diagnostic()
        assert diag["total_entries"] == 2
        assert diag["completed_count"] == 1
        assert diag["failed_only_count"] == 1
        assert diag["target_entry"]["external_id"] == "adlc:act:26-dcc-179"
        assert "ResourceExhausted" in diag["analyses"][0]["calls"][0]["error_type"]
        assert "A) Fallo transitorio" in diag["failure_classification"]

