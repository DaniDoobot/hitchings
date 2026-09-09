"""Unit tests for resumable v4 analysis backfill runner (Bloque 7H)."""

import argparse
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.models.analysis import AnalysisCall, AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.source import Source
from app.models.tracking import TrackingMatrix
from app.services.analysis_pipeline_service import (
    PipelineBudgetExceededError,
    PipelineStopRequestedError,
)
from app.services.analysis_service import compute_analysis_input_hash
from scripts.run_v4_backfill import (
    get_source_priority,
    get_run_cost_usd,
    calculate_conservative_reservation,
    max_estimated_triage_call_cost,
    max_estimated_deep_call_cost,
    main_async,
)


def test_deterministic_source_ordering():
    """Verify source ordering priority: CNMC (1), EC (2), CAT (3), CURIA (4)."""
    assert get_source_priority("CNMC - Noticias") == 1
    assert get_source_priority("European Commission - Competition Policy") == 2
    assert get_source_priority("Competition Appeal Tribunal - Judgments") == 3
    assert get_source_priority("Court of Justice of the European Union - Case Law") == 4
    assert get_source_priority("Unknown Source") == 99


def test_conservative_reservation_calculation():
    """Verify conservative reservation calculates safe upper bounds for triage and deep dynamically."""
    entry_short = Entry(id=uuid.uuid4(), title="Short", content="Brief text content")
    entry_long = Entry(id=uuid.uuid4(), title="Long", content="Long content " * 5000)

    prompt_triage = AnalysisPromptVersion(id=uuid.uuid4(), name="Triage", code="observatory_triage", version=4, stage="triage", config={"max_output_tokens": 1024})
    prompt_deep = AnalysisPromptVersion(id=uuid.uuid4(), name="Deep", code="observatory_deep_analysis", version=4, stage="deep_analysis", config={"max_output_tokens": 4096})

    res_triage_short = max_estimated_triage_call_cost(entry_short, prompt_triage, 0.75, 3.75)
    res_triage_long = max_estimated_triage_call_cost(entry_long, prompt_triage, 0.75, 3.75)
    res_deep_short = max_estimated_deep_call_cost(entry_short, prompt_deep, 0.75, 3.75)
    res_deep_long = max_estimated_deep_call_cost(entry_long, prompt_deep, 0.75, 3.75)

    # 1. Non-zero and reasonable bounds
    assert res_triage_short > 0.003
    # 2. Input size increase strictly increases reservation
    assert res_triage_long > res_triage_short
    assert res_deep_long > res_deep_short
    # 3. Deep reservation > triage reservation for short entry
    assert res_deep_short > res_triage_short
    # 4. Pricing increase increases reservation proportionally
    res_triage_2027 = max_estimated_triage_call_cost(entry_short, prompt_triage, 1.50, 7.50)
    assert res_triage_2027 > res_triage_short
    assert pytest.approx(res_triage_2027, rel=1e-4) == res_triage_short * 2.0
    # 5. max_output_tokens increase increases reservation
    prompt_deep_8k = AnalysisPromptVersion(id=uuid.uuid4(), name="Deep 8k", code="observatory_deep_analysis", version=4, stage="deep_analysis", config={"max_output_tokens": 8192})
    res_deep_8k = max_estimated_deep_call_cost(entry_short, prompt_deep_8k, 0.75, 3.75)
    assert res_deep_8k > res_deep_short


def test_get_run_cost_usd_sums_completed_and_failed_calls(db_session):
    """Verify get_run_cost_usd includes both completed and failed calls for the given run_id."""
    run_id = uuid.uuid4()
    other_run_id = uuid.uuid4()

    source = Source(id=uuid.uuid4(), name="Test Source", type="website", url="https://example.com")
    db_session.add(source)
    matrix = TrackingMatrix(id=uuid.uuid4(), name="Test Matrix", code=f"TEST-{uuid.uuid4().hex[:8]}", status="active")
    db_session.add(matrix)
    entry = Entry(id=uuid.uuid4(), source_id=source.id, title="Test Entry", url="https://example.com/1", content="Text", content_hash="h1")
    db_session.add(entry)
    pv = AnalysisPromptVersion(id=uuid.uuid4(), name="Test Prompt", code="triage", version=4, stage="triage", system_prompt="s", user_prompt_template="u", response_schema_version="v4")
    db_session.add(pv)

    ea = EntryAnalysis(id=uuid.uuid4(), entry_id=entry.id, matrix_id=matrix.id, pipeline_version="v4", status="completed", matrix_snapshot={}, matrix_snapshot_hash="m1")
    db_session.add(ea)

    call1 = AnalysisCall(
        id=uuid.uuid4(),
        entry_analysis_id=ea.id,
        prompt_version_id=pv.id,
        stage="triage",
        provider="gemini_api",
        model="gemini-3.8-flash",
        status="completed",
        estimated_cost_usd=0.005,
        call_metadata={"run_id": str(run_id)},
    )
    call2 = AnalysisCall(
        id=uuid.uuid4(),
        entry_analysis_id=ea.id,
        prompt_version_id=pv.id,
        stage="deep_analysis",
        provider="gemini_api",
        model="gemini-3.8-flash",
        status="failed",
        estimated_cost_usd=0.003,
        call_metadata={"run_id": str(run_id)},
    )
    call_other = AnalysisCall(
        id=uuid.uuid4(),
        entry_analysis_id=ea.id,
        prompt_version_id=pv.id,
        stage="triage",
        provider="gemini_api",
        model="gemini-3.8-flash",
        status="completed",
        estimated_cost_usd=0.010,
        call_metadata={"run_id": str(other_run_id)},
    )
    db_session.add_all([call1, call2, call_other])
    db_session.commit()

    cost = get_run_cost_usd(db_session, run_id)
    assert pytest.approx(cost, rel=1e-5) == 0.008


@pytest.mark.asyncio
async def test_dry_run_never_calls_provider_and_exits_zero():
    """Verify that default dry-run mode completes preflight, prints plan, and makes 0 calls."""
    args = argparse.Namespace(confirm_real_calls=False, max_usd=2.00, resume_run_id=None)
    with patch("scripts.run_v4_backfill.GeminiAPIProvider") as mock_provider:
        ret = await main_async(args)
        assert ret == 0
        mock_provider.assert_not_called()


@pytest.mark.asyncio
async def test_budget_guard_before_deep_marks_analysis_incomplete(db_session):
    """Verify that when budget reservation fails before deep, analysis is marked incomplete and deep skipped."""
    from app.services.analysis_pipeline_service import AnalysisPipelineService
    from app.models.tracking import TrackingTopic
    from app.schemas.analysis import AIAnalysisResponsePayload, AIAnalysisTopicItem
    from app.providers.ai.base import AIProviderResult, BaseAIProvider

    source = Source(id=uuid.uuid4(), name="Test Source", type="website", url="https://example.com")
    db_session.add(source)
    matrix = TrackingMatrix(id=uuid.uuid4(), name="Test Matrix", code=f"TEST-{uuid.uuid4().hex[:8]}", status="active")
    db_session.add(matrix)
    topic = TrackingTopic(id=uuid.uuid4(), matrix_id=matrix.id, code="cartels_horizontal", name="Horizontal Cartels")
    db_session.add(topic)
    entry_content = "Substantive test content for competition law case regarding cartels. " * 20
    entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        title="Test Entry",
        url="https://example.com/1",
        content=entry_content,
        content_hash="h1",
        raw_metadata={"content_source": "full_text"},
    )
    db_session.add(entry)
    pv_triage = AnalysisPromptVersion(id=uuid.uuid4(), name="Triage Prompt", code="observatory_triage", version=4, stage="triage", system_prompt="s", user_prompt_template="u", response_schema_version="v4")
    pv_deep = AnalysisPromptVersion(id=uuid.uuid4(), name="Deep Prompt", code="observatory_deep_analysis", version=4, stage="deep_analysis", system_prompt="s", user_prompt_template="u", response_schema_version="v4")
    db_session.add_all([pv_triage, pv_deep])
    db_session.commit()

    class GroundedStubProvider(BaseAIProvider):
        @property
        def provider_name(self) -> str:
            return "mock"

        async def analyze(self, *args, **kwargs) -> AIProviderResult:
            payload = AIAnalysisResponsePayload(
                relevance_score=85,
                relevance_status="relevant",
                confidence=0.90,
                reason="Substantive test case",
                evidence=[{"source_field": "content", "quote": "Substantive test content"}],
                topics=[AIAnalysisTopicItem(topic_code="cartels_horizontal", confidence=0.90, is_primary=True, rationale="Horizontal issue")],
            )
            return AIProviderResult(
                success=True,
                payload=payload,
                raw_response={"result": payload.model_dump()},
                provider_name="mock",
                model="mock-v4",
                estimated_cost_usd=0.001,
            )

    pipeline = AnalysisPipelineService(provider=GroundedStubProvider())

    def mock_hook(stage, entry, prompt, analysis):
        if stage == "deep_analysis":
            raise PipelineBudgetExceededError("Budget exceeded before deep")

    analysis = await pipeline.run_pipeline(
        entry_id=entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=pv_triage.id,
        deep_prompt_id=pv_deep.id,
        db=db_session,
        pipeline_version="v4",
        before_stage_hook=mock_hook,
    )

    assert analysis.status == "incomplete"
    assert "DEEP SKIPPED" in (analysis.reason or "")
    calls = db_session.query(AnalysisCall).filter(AnalysisCall.entry_analysis_id == analysis.id).all()
    assert len(calls) == 1  # Only triage was called
    assert calls[0].stage == "triage"
    assert calls[0].call_metadata.get("deep_skipped") is True


@pytest.mark.asyncio
async def test_hard_stop_before_triage_rolls_back_and_makes_zero_calls(db_session):
    """Verify that when budget reservation fails before triage, transaction is rolled back and no calls are recorded."""
    from app.services.analysis_pipeline_service import AnalysisPipelineService
    from app.models.tracking import TrackingTopic

    source = Source(id=uuid.uuid4(), name="Test Source", type="website", url="https://example.com")
    db_session.add(source)
    matrix = TrackingMatrix(id=uuid.uuid4(), name="Test Matrix", code=f"TEST-{uuid.uuid4().hex[:8]}", status="active")
    db_session.add(matrix)
    topic = TrackingTopic(id=uuid.uuid4(), matrix_id=matrix.id, code="cartels_horizontal", name="Horizontal Cartels")
    db_session.add(topic)
    entry = Entry(id=uuid.uuid4(), source_id=source.id, title="Test Entry", url="https://example.com/1", content="Content", content_hash="h1")
    db_session.add(entry)
    pv_triage = AnalysisPromptVersion(id=uuid.uuid4(), name="Triage", code="observatory_triage", version=4, stage="triage", system_prompt="s", user_prompt_template="u", response_schema_version="v4")
    pv_deep = AnalysisPromptVersion(id=uuid.uuid4(), name="Deep", code="observatory_deep_analysis", version=4, stage="deep_analysis", system_prompt="s", user_prompt_template="u", response_schema_version="v4")
    db_session.add_all([pv_triage, pv_deep])
    db_session.commit()

    pipeline = AnalysisPipelineService(provider=MagicMock())

    def mock_hook(stage, entry, prompt, analysis):
        if stage == "triage":
            raise PipelineBudgetExceededError("Budget exceeded before triage")

    with pytest.raises(PipelineBudgetExceededError):
        await pipeline.run_pipeline(
            entry_id=entry.id,
            matrix_id=matrix.id,
            triage_prompt_id=pv_triage.id,
            deep_prompt_id=pv_deep.id,
            db=db_session,
            pipeline_version="v4",
            before_stage_hook=mock_hook,
        )

    # 0 calls recorded and 0 EntryAnalysis committed
    calls = db_session.query(AnalysisCall).all()
    assert len(calls) == 0
    eas = db_session.query(EntryAnalysis).all()
    assert len(eas) == 0
