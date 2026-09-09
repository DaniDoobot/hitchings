"""Unit tests for v6 contiguous-evidence hotfix and Gormsen repair (Bloque 7H.3)."""

import argparse
import inspect
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
from app.services import grounding_validator
from scripts.seed_analysis_prompts import seed_analysis_prompts
from scripts.run_v4_backfill import (
    max_estimated_triage_call_cost,
    max_estimated_deep_call_cost,
)
from scripts.run_v6_gormsen_repair import (
    GORMSEN_ENTRY_ID,
    ALLOWED_ENTRY_IDS,
    main_async,
)


def test_v6_gormsen_whitelist_contract():
    """Verify runner permits strictly Gormsen UUID and rejects any other UUID."""
    expected_id = uuid.UUID("ada5d125-a861-4ff8-bcf6-2b2607afd834")
    assert GORMSEN_ENTRY_ID == expected_id
    assert ALLOWED_ENTRY_IDS == {expected_id}
    assert len(ALLOWED_ENTRY_IDS) == 1

    # Any other UUID must not be allowed
    assert uuid.uuid4() not in ALLOWED_ENTRY_IDS
    assert uuid.UUID("4db3fa9a-280c-4250-b758-97a2c233e1a5") not in ALLOWED_ENTRY_IDS
    assert uuid.UUID("14e036d2-78c9-456b-a633-3796b1710778") not in ALLOWED_ENTRY_IDS


def test_v6_prompt_contracts_and_generic_rules(db_session):
    """Verify v6 prompt configuration, immutability of v5, and generic non-overfitted rules."""
    seed_analysis_prompts(db=db_session)

    triage_v6 = db_session.query(AnalysisPromptVersion).filter(
        AnalysisPromptVersion.code == "observatory_triage", AnalysisPromptVersion.version == 6
    ).first()
    deep_v6 = db_session.query(AnalysisPromptVersion).filter(
        AnalysisPromptVersion.code == "observatory_deep_analysis", AnalysisPromptVersion.version == 6
    ).first()
    triage_v5 = db_session.query(AnalysisPromptVersion).filter(
        AnalysisPromptVersion.code == "observatory_triage", AnalysisPromptVersion.version == 5
    ).first()
    deep_v5 = db_session.query(AnalysisPromptVersion).filter(
        AnalysisPromptVersion.code == "observatory_deep_analysis", AnalysisPromptVersion.version == 5
    ).first()

    assert triage_v6 is not None
    assert deep_v6 is not None
    assert triage_v5 is not None
    assert deep_v5 is not None

    # Triage v6: identical to v5 except version=6
    assert triage_v6.config.get("max_output_tokens") == 1024
    assert triage_v6.config.get("thinking_level") == "low"
    assert triage_v6.system_prompt == triage_v5.system_prompt
    assert triage_v6.user_prompt_template == triage_v5.user_prompt_template

    # Deep v6: maintains 8192 max_output_tokens and medium thinking
    assert deep_v6.config.get("max_output_tokens") == 8192
    assert deep_v6.config.get("thinking_level") == "medium"
    assert deep_v6.user_prompt_template == deep_v5.user_prompt_template

    # V5 prompts preserved intact
    assert deep_v5.config.get("max_output_tokens") == 8192
    assert "SALTO DE ARTEFACTOS O ENCABEZADOS DE" not in deep_v5.system_prompt

    # Deep v6 contains generic contiguous evidence instruction
    deep_prompt_text = deep_v6.system_prompt
    assert "NUNCA construyas una cita uniendo fragmentos" in deep_prompt_text

    # NON-OVERFITTING CHECK: must NOT mention Gormsen, Meta, Devenish, or specific header text
    forbidden_terms = [
        "gormsen", "meta", "devenish", "judgment approved by the court for handing down"
    ]
    for term in forbidden_terms:
        assert term not in deep_prompt_text.lower(), f"Overfitting detected: '{term}' found in deep v6 prompt!"
        assert term not in triage_v6.system_prompt.lower(), f"Overfitting detected: '{term}' found in triage v6 prompt!"


def test_grounding_validator_remains_strict_and_unmodified():
    """Verify GroundingValidator functions remain strict without wildcard/ellipsis support."""
    from app.services.grounding_validator import validate_grounding_quote, AnalysisGroundingError
    from app.schemas.analysis import GroundingEvidence

    # Text with page-break artifact in between
    source_text = """The CAT held that user damages apply.

Judgment Approved by court

Indeed this was confirmed."""
    test_entry = Entry(id=uuid.uuid4(), title="Test", content=source_text)

    # A stitched quote omitting the header must strictly FAIL validation (raise AnalysisGroundingError)
    stitched_ev = GroundingEvidence(quote="The CAT held that user damages apply. Indeed this was confirmed.", source_field="content")
    with pytest.raises(AnalysisGroundingError) as exc_info:
        validate_grounding_quote(stitched_ev, test_entry)
    assert "verbatim quote not found" in str(exc_info.value)

    # Exact contiguous substring before header SUCCEEDS (returns True)
    clean_ev = GroundingEvidence(quote="The CAT held that user damages apply.", source_field="content")
    assert validate_grounding_quote(clean_ev, test_entry) is True


@pytest.mark.asyncio
async def test_dry_run_v6_makes_zero_calls():
    """Verify Gormsen v6 runner dry-run mode completes preflight and never calls Gemini."""
    args = argparse.Namespace(confirm_real_calls=False, max_usd=0.12)
    with patch("scripts.run_v6_gormsen_repair.GeminiAPIProvider") as mock_provider:
        ret = await main_async(args)
        assert ret == 0
        mock_provider.assert_not_called()


def test_select_current_analysis_gormsen_lifecycle(db_session):
    """Verify select_current_analysis correctly updates when v6 completed is added, but stays None if v6 failed."""
    source = Source(id=uuid.uuid4(), name="Test CAT", type="website", url="https://example.com")
    db_session.add(source)
    matrix = TrackingMatrix(id=uuid.uuid4(), name="Test Matrix", code=f"TEST-{uuid.uuid4().hex[:8]}", status="active")
    db_session.add(matrix)
    content = "Legal text for Gormsen lifecycle test"
    entry = Entry(
        id=GORMSEN_ENTRY_ID,
        source_id=source.id,
        title="Gormsen v Meta",
        url="https://example.com/item",
        content=content,
        content_hash="h_gormsen",
        raw_metadata={"content_source": "cat_judgment_pdf_text"},
    )
    db_session.add(entry)
    db_session.commit()

    # Step 1: Only failed v4 and failed v5 exist
    ea_v4_failed = EntryAnalysis(
        id=uuid.uuid4(), entry_id=entry.id, matrix_id=matrix.id, pipeline_version="v4",
        status="failed", reason="Grounding failed", matrix_snapshot={}, matrix_snapshot_hash="m1"
    )
    ea_v5_failed = EntryAnalysis(
        id=uuid.uuid4(), entry_id=entry.id, matrix_id=matrix.id, pipeline_version="v5",
        status="failed", reason="Grounding failed", matrix_snapshot={}, matrix_snapshot_hash="m1"
    )
    db_session.add_all([ea_v4_failed, ea_v5_failed])
    db_session.commit()

    assert select_current_analysis(entry, db=db_session) is None

    # Step 2: If v6 also fails, current remains None
    ea_v6_failed = EntryAnalysis(
        id=uuid.uuid4(), entry_id=entry.id, matrix_id=matrix.id, pipeline_version="v6",
        status="failed", reason="Failed again", matrix_snapshot={}, matrix_snapshot_hash="m1"
    )
    db_session.add(ea_v6_failed)
    db_session.commit()

    assert select_current_analysis(entry, db=db_session) is None

    # Step 3: When v6 succeeds, it becomes current
    from app.services.analysis_service import compute_analysis_input_hash
    ea_v6_completed = EntryAnalysis(
        id=uuid.uuid4(), entry_id=entry.id, matrix_id=matrix.id, pipeline_version="v6",
        status="completed", relevance_score=95, relevance_status="relevant",
        summary="Gormsen v6 analysis summary",
        entry_content_hash=compute_analysis_input_hash(entry),
        matrix_snapshot={}, matrix_snapshot_hash="m1"
    )
    db_session.add(ea_v6_completed)
    db_session.commit()

    current_res = select_current_analysis(entry, db=db_session)
    assert current_res is not None
    assert current_res.id == ea_v6_completed.id
    assert current_res.pipeline_version == "v6"
    assert current_res.status == "completed"
