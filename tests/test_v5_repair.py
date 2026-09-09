"""Unit tests for v5 capacity hotfix and failed analysis repair (Bloque 7H.2)."""

import argparse
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.models.analysis import AnalysisCall, AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.source import Source
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.services.analysis_pipeline_service import (
    AnalysisPipelineService,
    PipelineBudgetExceededError,
)
from app.services.current_analysis_service import select_current_analysis
from scripts.seed_analysis_prompts import seed_analysis_prompts
from scripts.run_v4_backfill import (
    max_estimated_triage_call_cost,
    max_estimated_deep_call_cost,
)
from scripts.run_v5_failed_repair import (
    TARGET_ENTRY_IDS,
    ALLOWED_ENTRY_ID_SET,
    main_async,
)


def test_v5_target_whitelist_contract():
    """Verify whitelist contains exactly 3 expected UUIDs in deterministic order."""
    expected_order = [
        uuid.UUID("ada5d125-a861-4ff8-bcf6-2b2607afd834"),  # Gormsen / Meta
        uuid.UUID("4db3fa9a-280c-4250-b758-97a2c233e1a5"),  # CAT 56 / Sciallis v Fender
        uuid.UUID("14e036d2-78c9-456b-a633-3796b1710778"),  # EWCA Civ 814 / Rowntree v PRS
    ]
    assert TARGET_ENTRY_IDS == expected_order
    assert len(ALLOWED_ENTRY_ID_SET) == 3
    for uid in expected_order:
        assert uid in ALLOWED_ENTRY_ID_SET

    # Non-whitelisted UUIDs must NOT be in the set
    random_id = uuid.uuid4()
    assert random_id not in ALLOWED_ENTRY_ID_SET


def test_v5_prompt_contracts(db_session):
    """Verify v5 prompt configuration matches the Bloque 7H.2 specification."""
    # Seed prompts in isolated test db
    seed_analysis_prompts(db=db_session)

    triage_v5 = (
        db_session.query(AnalysisPromptVersion)
        .filter(
            AnalysisPromptVersion.code == "observatory_triage",
            AnalysisPromptVersion.version == 5,
        )
        .first()
    )
    deep_v5 = (
        db_session.query(AnalysisPromptVersion)
        .filter(
            AnalysisPromptVersion.code == "observatory_deep_analysis",
            AnalysisPromptVersion.version == 5,
        )
        .first()
    )
    triage_v4 = (
        db_session.query(AnalysisPromptVersion)
        .filter(
            AnalysisPromptVersion.code == "observatory_triage",
            AnalysisPromptVersion.version == 4,
        )
        .first()
    )
    deep_v4 = (
        db_session.query(AnalysisPromptVersion)
        .filter(
            AnalysisPromptVersion.code == "observatory_deep_analysis",
            AnalysisPromptVersion.version == 4,
        )
        .first()
    )

    assert triage_v5 is not None
    assert deep_v5 is not None
    assert triage_v4 is not None
    assert deep_v4 is not None

    # Triage v5: identical config to v4 (max_output_tokens=1024, thinking_level='low')
    assert triage_v5.config.get("max_output_tokens") == 1024
    assert triage_v5.config.get("thinking_level") == "low"
    assert triage_v5.config.get("structured_output_schema") == "TriageAnalysisResultV3"
    assert triage_v5.system_prompt == triage_v4.system_prompt
    assert triage_v5.user_prompt_template == triage_v4.user_prompt_template

    # Deep v5: identical system & user prompts to v4, but max_output_tokens=8192
    assert deep_v5.config.get("max_output_tokens") == 8192
    assert deep_v5.config.get("thinking_level") == "medium"
    assert deep_v5.config.get("structured_output_schema") == "DeepAnalysisResultV3"
    assert deep_v5.system_prompt == deep_v4.system_prompt
    assert deep_v5.user_prompt_template == deep_v4.user_prompt_template

    # Deep v4 preserved unchanged
    assert deep_v4.config.get("max_output_tokens") == 4096


def test_v5_reservation_accounts_for_8k_tokens():
    """Verify max_estimated_deep_call_cost calculates higher upper bound for 8k prompt."""
    entry = Entry(id=uuid.uuid4(), title="Test Judgement", content="Sample judgment text " * 1000)
    prompt_deep_v4 = AnalysisPromptVersion(
        id=uuid.uuid4(), name="Deep v4", code="observatory_deep_analysis",
        version=4, stage="deep_analysis", config={"max_output_tokens": 4096}
    )
    prompt_deep_v5 = AnalysisPromptVersion(
        id=uuid.uuid4(), name="Deep v5", code="observatory_deep_analysis",
        version=5, stage="deep_analysis", config={"max_output_tokens": 8192}
    )

    cost_v4 = max_estimated_deep_call_cost(entry, prompt_deep_v4, 0.75, 3.75)
    cost_v5 = max_estimated_deep_call_cost(entry, prompt_deep_v5, 0.75, 3.75)

    assert cost_v5 > cost_v4
    # Difference in output cost: (8192 - 4096) * 3.75 / 1e6 = 4096 * 3.75 / 1e6 = 0.01536
    expected_diff = (4096 * 3.75) / 1_000_000.0
    assert pytest.approx(cost_v5 - cost_v4, abs=1e-4) == expected_diff


@pytest.mark.asyncio
async def test_dry_run_v5_makes_zero_calls():
    """Verify v5 repair runner dry-run mode completes preflight and never calls Gemini."""
    args = argparse.Namespace(confirm_real_calls=False, max_usd=0.25)
    with patch("scripts.run_v5_failed_repair.GeminiAPIProvider") as mock_provider:
        ret = await main_async(args)
        assert ret == 0
        mock_provider.assert_not_called()


def test_select_current_analysis_selects_v5_completed_over_v4_failed(db_session):
    """Verify select_current_analysis prioritizes completed v5 over failed v4."""
    source = Source(id=uuid.uuid4(), name="Test Source", type="website", url="https://example.com")
    db_session.add(source)
    matrix = TrackingMatrix(id=uuid.uuid4(), name="Test Matrix", code=f"TEST-{uuid.uuid4().hex[:8]}", status="active")
    db_session.add(matrix)
    content = "Legal text for current analysis selection test"
    entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        title="Test Entry",
        url="https://example.com/item",
        content=content,
        content_hash="h123",
        raw_metadata={"content_source": "full_text"},
    )
    db_session.add(entry)
    db_session.commit()

    # Step 1: Only failed v4 exists
    ea_v4_failed = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v4",
        status="failed",
        reason="Quote mismatch: AnalysisGroundingError",
        matrix_snapshot={},
        matrix_snapshot_hash="m1",
    )
    db_session.add(ea_v4_failed)
    db_session.commit()

    current_res1 = select_current_analysis(entry, db=db_session)
    # Failed analysis is NOT selected as current
    assert current_res1 is None

    # Step 2: Completed v5 is added
    from app.services.analysis_service import compute_analysis_input_hash
    ea_v5_completed = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v5",
        status="completed",
        relevance_score=90,
        relevance_status="relevant",
        summary="Completed v5 analysis summary",
        entry_content_hash=compute_analysis_input_hash(entry),
        matrix_snapshot={},
        matrix_snapshot_hash="m1",
    )
    db_session.add(ea_v5_completed)
    db_session.commit()

    current_res2 = select_current_analysis(entry, db=db_session)
    assert current_res2 is not None
    assert current_res2.id == ea_v5_completed.id
    assert current_res2.pipeline_version == "v5"
    assert current_res2.status == "completed"
