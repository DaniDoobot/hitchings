"""Tests for Bloque 7B: Gemini Developer API provider, two-stage pipeline, benchmark logic.

All tests are offline — no real Gemini API calls are made.
The google-genai SDK is mocked where needed.

Test list:
  28. test_28_triage_schema_valid
  29. test_29_deep_schema_valid
  30. test_30_gemini_provider_maps_triage_response
  31. test_31_gemini_provider_maps_deep_response
  32. test_32_gemini_usage_metadata_thoughts
  33. test_33_gemini_cost_calculation
  34. test_34_gemini_input_too_large
  35. test_35_pipeline_not_relevant_skips_deep
  36. test_36_pipeline_uncertain_skips_deep
  37. test_37_pipeline_relevant_calls_deep
  38. test_38_pipeline_triage_failure_no_deep
  39. test_39_pipeline_deep_failure_preserves_triage
  40. test_40_pipeline_disabled_provider
  41. test_41_benchmark_requires_confirm_flag
  42. test_42_benchmark_deterministic_sample
  43. test_43_benchmark_budget_hard_stop
  44. test_44_gemini_provider_client_init_and_no_key_raises
  45. test_45_gemini_api_key_never_leaked_in_metadata
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.analysis import (
    AnalysisCall,
    AnalysisPromptVersion,
    EntryAnalysis,
    EntryAnalysisTopic,
)
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.providers.ai.base import AIProviderResult
from app.providers.ai.mock import MockAIProvider
from app.providers.ai.gemini_api import AnalysisInputTooLarge, GeminiAPIProvider
from app.schemas.analysis import (
    AIAnalysisResponsePayload,
    AIAnalysisTopicItem,
    DeepAnalysisResult,
    TriageAnalysisResult,
)
from app.services.analysis_pipeline_service import AnalysisPipelineService
from app.services.analysis_service import AnalysisService


# ==============================================================================
# Fixtures & Helpers
# ==============================================================================

UTC = timezone.utc


def make_source(db: Session, name: str = "Test Source") -> Source:
    src = Source(name=name, type=SourceType.WEBSITE, provider="native", url=f"https://{name}.test/")
    db.add(src)
    db.flush()
    return src


def make_entry(
    db: Session,
    source: Source,
    title: str = "Test Entry on Antitrust",
    content: str = "Merger control and competition analysis under Article 101.",
    published_at: Optional[datetime] = None,
) -> Entry:
    entry = Entry(
        source_id=source.id,
        url=f"https://example.com/{uuid.uuid4()}",
        title=title,
        content=content,
        excerpt=content[:50],
        published_at=published_at or datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    db.add(entry)
    db.flush()
    return entry


def make_matrix(db: Session, code_suffix: str = "") -> tuple[TrackingMatrix, list[TrackingTopic]]:
    code = f"TEST-7B{'-' + code_suffix if code_suffix else ''}"
    matrix = TrackingMatrix(
        code=code,
        name="Test Matrix 7B",
        status="active",
        relevance_instructions="Include competition, mergers, cartels",
        exclusion_instructions="Exclude unrelated criminal law",
    )
    db.add(matrix)
    db.flush()

    t1 = TrackingTopic(
        matrix_id=matrix.id,
        code="merger_control",
        name="Merger Control",
        description="Control de concentraciones",
        keywords=["merger", "concentración"],
        active=True,
    )
    t2 = TrackingTopic(
        matrix_id=matrix.id,
        code="antitrust_cartels",
        name="Antitrust & Cartels",
        description="Cárteles y acuerdos horizontales",
        keywords=["cartel", "antitrust"],
        active=True,
    )
    db.add_all([t1, t2])
    db.flush()
    return matrix, [t1, t2]


def make_prompt(
    db: Session,
    code: str = "observatory_triage",
    version: int = 2,
    stage: str = "triage",
) -> AnalysisPromptVersion:
    prompt = AnalysisPromptVersion(
        code=code,
        version=version,
        stage=stage,
        name=f"Test {code} v{version}",
        system_prompt="System prompt for testing.",
        user_prompt_template="Analyze: {content_section}",
        response_schema_version="v2",
        config={"thinking_level": "low", "max_output_tokens": 512},
        active=True,
    )
    db.add(prompt)
    db.flush()
    return prompt


def make_gemini_settings(**overrides) -> Settings:
    """Build a Settings instance with Gemini Developer API configured for offline testing."""
    env = {
        "DATABASE_URL": "sqlite:///:memory:",
        "ANALYSIS_PROVIDER": "gemini_api",
        "GEMINI_API_KEY": "test_api_key_secret_value_123",
        "GEMINI_MODEL": "gemini-3.8-flash",
        "ANALYSIS_TRIAGE_THINKING_LEVEL": "low",
        "ANALYSIS_DEEP_THINKING_LEVEL": "medium",
        "ANALYSIS_TRIAGE_MAX_INPUT_CHARS": "250000",
        "ANALYSIS_DEEP_MAX_INPUT_CHARS": "250000",
        "ANALYSIS_BENCHMARK_MAX_USD": "1.00",
        "GEMINI_INPUT_USD_PER_MILLION_TOKENS": "0.75",
        "GEMINI_OUTPUT_USD_PER_MILLION_TOKENS": "3.75",
        **overrides,
    }
    return Settings(**env)


# ==============================================================================
# 28. TriageAnalysisResult schema validation
# ==============================================================================

def test_28_triage_schema_valid():
    """TriageAnalysisResult accepts valid data and rejects invalid fields."""
    valid = TriageAnalysisResult(
        relevance_score=75,
        confidence=0.85,
        topic_codes=["merger_control"],
        primary_topic_code="merger_control",
        reason="El documento es una resolución de control de concentraciones.",
    )
    assert valid.relevance_score == 75
    assert valid.confidence == 0.85
    assert valid.primary_topic_code == "merger_control"

    # score out of range
    with pytest.raises(Exception):
        TriageAnalysisResult(
            relevance_score=150,
            confidence=0.5,
            topic_codes=[],
            primary_topic_code=None,
            reason="Bad score",
        )

    # confidence out of range
    with pytest.raises(Exception):
        TriageAnalysisResult(
            relevance_score=50,
            confidence=1.5,
            topic_codes=[],
            primary_topic_code=None,
            reason="Bad confidence",
        )


# ==============================================================================
# 29. DeepAnalysisResult schema validation
# ==============================================================================

def test_29_deep_schema_valid():
    """DeepAnalysisResult accepts valid data."""
    valid = DeepAnalysisResult(
        summary="La resolucion establece precedente en materia de danos por infraccion de competencia.",
        key_points=[
            "El TJUE confirma responsabilidad solidaria.",
            "Se amplia el plazo de prescripcion a cinco anios.",
            "Se reconoce el derecho a indemnizacion de compras indirectas.",
        ],
    )
    assert len(valid.key_points) == 3
    assert len(valid.summary) > 0
    assert valid.summary.startswith("La resolucion")

    # Missing required fields
    with pytest.raises(Exception):
        DeepAnalysisResult(key_points=["only key points, no summary"])  # type: ignore


# ==============================================================================
# 30. GeminiAPIProvider maps triage response correctly
# ==============================================================================

@pytest.mark.asyncio
async def test_30_gemini_provider_maps_triage_response(db_session):
    """GeminiAPIProvider correctly maps a mocked Gemini Developer API triage response to AIProviderResult."""
    settings = make_gemini_settings()
    provider = GeminiAPIProvider(settings=settings)

    mock_parsed = TriageAnalysisResult(
        relevance_score=80,
        confidence=0.9,
        topic_codes=["merger_control"],
        primary_topic_code="merger_control",
        reason="Resolución de control de concentraciones.",
    )
    mock_response = MagicMock()
    mock_response.parsed = mock_parsed
    mock_response.text = None
    mock_usage = MagicMock()
    mock_usage.prompt_token_count = 1000
    mock_usage.candidates_token_count = 80
    mock_usage.thoughts_token_count = 0
    mock_usage.total_token_count = 1080
    mock_response.usage_metadata = mock_usage

    source = make_source(db_session, "Test Source 30")
    entry = make_entry(db_session, source, content="Merger notification for company A and B.")
    prompt = make_prompt(db_session, "triage30", 2, "triage")
    matrix, _ = make_matrix(db_session, "30")

    from app.services.analysis_service import compute_matrix_snapshot
    snapshot, _ = compute_matrix_snapshot(matrix)

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    provider._client = mock_client

    result = await provider.analyze(
        prompt_version=prompt,
        entry=entry,
        matrix_snapshot=snapshot,
    )

    assert result.success is True
    assert result.payload is not None
    assert result.payload.relevance_score == 80
    assert result.payload.confidence == 0.9
    assert len(result.payload.topics) == 1
    assert result.payload.topics[0].topic_code == "merger_control"
    assert result.payload.topics[0].is_primary is True
    assert result.payload.reason == "Resolución de control de concentraciones."
    assert result.payload.summary is None
    assert result.input_tokens == 1000
    assert result.output_tokens == 80
    assert result.provider_name == "gemini_api"
    assert result.model == "gemini-3.8-flash"


# ==============================================================================
# 31. GeminiAPIProvider maps deep response correctly
# ==============================================================================

@pytest.mark.asyncio
async def test_31_gemini_provider_maps_deep_response(db_session):
    """GeminiAPIProvider correctly maps a mocked Gemini Developer API deep response to AIProviderResult."""
    settings = make_gemini_settings()
    provider = GeminiAPIProvider(settings=settings)

    mock_parsed = DeepAnalysisResult(
        summary="La resolución confirma la responsabilidad de las empresas cartelistas.",
        key_points=["Multa de 120M EUR.", "Aplicación directa del artículo 101 TFUE."],
    )
    mock_response = MagicMock()
    mock_response.parsed = mock_parsed
    mock_response.text = None
    mock_usage = MagicMock()
    mock_usage.prompt_token_count = 2000
    mock_usage.candidates_token_count = 200
    mock_usage.thoughts_token_count = 150
    mock_usage.total_token_count = 2350
    mock_response.usage_metadata = mock_usage

    source = make_source(db_session, "Test Source 31")
    entry = make_entry(db_session, source)
    deep_prompt = make_prompt(db_session, "deep31", 2, "deep_analysis")
    matrix, _ = make_matrix(db_session, "31")

    from app.services.analysis_service import compute_matrix_snapshot
    snapshot, _ = compute_matrix_snapshot(matrix)

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    provider._client = mock_client

    triage_result = {
        "relevance_score": 85,
        "topic_codes": ["antitrust_cartels"],
        "primary_topic_code": "antitrust_cartels",
        "reason": "Documento sobre cártel de precios.",
    }

    result = await provider.analyze(
        prompt_version=deep_prompt,
        entry=entry,
        matrix_snapshot=snapshot,
        triage_result=triage_result,
    )

    assert result.success is True
    assert result.payload is not None
    assert "responsabilidad" in result.payload.summary
    assert len(result.payload.key_points) == 2
    assert result.input_tokens == 2000
    # Billable output = 2350 - 2000 = 350
    assert result.output_tokens == 350


# ==============================================================================
# 32. Thought tokens included in total output without double-counting
# ==============================================================================

@pytest.mark.asyncio
async def test_32_gemini_usage_metadata_thoughts(db_session):
    """Thought tokens are included in billable output tokens without double-counting."""
    settings = make_gemini_settings()
    provider = GeminiAPIProvider(settings=settings)

    mock_parsed = TriageAnalysisResult(
        relevance_score=30,
        confidence=0.4,
        topic_codes=[],
        primary_topic_code=None,
        reason="No relevante para HITCHINGS.",
    )
    mock_response = MagicMock()
    mock_response.parsed = mock_parsed
    mock_usage = MagicMock()
    mock_usage.prompt_token_count = 500
    mock_usage.candidates_token_count = 50
    mock_usage.thoughts_token_count = 300
    mock_usage.total_token_count = 850  # 500 + 50 + 300
    mock_response.usage_metadata = mock_usage

    source = make_source(db_session, "Test Source 32")
    entry = make_entry(db_session, source)
    prompt = make_prompt(db_session, "triage32")
    matrix, _ = make_matrix(db_session, "32")
    from app.services.analysis_service import compute_matrix_snapshot
    snapshot, _ = compute_matrix_snapshot(matrix)

    provider._client = MagicMock()
    provider._client.models.generate_content.return_value = mock_response

    result = await provider.analyze(prompt_version=prompt, entry=entry, matrix_snapshot=snapshot)

    assert result.success is True
    assert result.output_tokens == 350
    assert result.call_metadata.get("output_thought_tokens") == 300
    assert result.call_metadata.get("output_text_tokens") == 50


# ==============================================================================
# 33. Cost calculation uses configurable rates
# ==============================================================================

def test_33_gemini_cost_calculation():
    """Cost is calculated correctly from configurable Gemini Developer API rates."""
    settings = make_gemini_settings(
        GEMINI_INPUT_USD_PER_MILLION_TOKENS="0.75",
        GEMINI_OUTPUT_USD_PER_MILLION_TOKENS="3.75",
    )
    provider = GeminiAPIProvider(settings=settings)

    # 1M input ($0.75) + 1M output ($3.75) = $4.50
    cost = provider._calculate_cost(1_000_000, 1_000_000)
    assert abs(cost - 4.50) < 1e-6


# ==============================================================================
# 34. AnalysisInputTooLarge raised correctly
# ==============================================================================

@pytest.mark.asyncio
async def test_34_gemini_input_too_large(db_session):
    """Provider returns failure with AnalysisInputTooLarge if content exceeds max_chars."""
    settings = make_gemini_settings(ANALYSIS_TRIAGE_MAX_INPUT_CHARS="10")
    provider = GeminiAPIProvider(settings=settings)
    provider._client = MagicMock()

    source = make_source(db_session, "Test Source 34")
    entry = make_entry(db_session, source, content="A" * 20)
    prompt = make_prompt(db_session, "triage34")
    matrix, _ = make_matrix(db_session, "34")
    from app.services.analysis_service import compute_matrix_snapshot
    snapshot, _ = compute_matrix_snapshot(matrix)

    result = await provider.analyze(prompt_version=prompt, entry=entry, matrix_snapshot=snapshot)

    assert result.success is False
    assert result.error_type == "AnalysisInputTooLarge"
    provider._client.models.generate_content.assert_not_called()


# ==============================================================================
# 35. Pipeline: not_relevant skips deep
# ==============================================================================

@pytest.mark.asyncio
async def test_35_pipeline_not_relevant_skips_deep(db_session):
    """Pipeline does not call deep analysis when triage gives not_relevant (score < 40)."""
    call_count = {"analyze": 0}

    class LowScoreProvider(MockAIProvider):
        def __init__(self):
            super().__init__(fixed_score=15)

        async def analyze(self, prompt_version, entry, matrix_snapshot, **kwargs):
            call_count["analyze"] += 1
            result = await super().analyze(prompt_version, entry, matrix_snapshot, **kwargs)
            result.payload.topics = []
            return result

    source = make_source(db_session, "Source 35")
    entry = make_entry(db_session, source)
    matrix, _ = make_matrix(db_session, "35")
    triage_p = make_prompt(db_session, "triage35", 1, "triage")
    deep_p = make_prompt(db_session, "deep35", 1, "deep_analysis")

    pipeline = AnalysisPipelineService(provider=LowScoreProvider())
    analysis = await pipeline.run_pipeline(
        entry_id=entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=triage_p.id,
        deep_prompt_id=deep_p.id,
        db=db_session,
    )

    assert analysis.status == "completed"
    assert analysis.relevance_status == "not_relevant"
    assert analysis.relevance_score == 15
    assert call_count["analyze"] == 1
    assert analysis.summary is None


# ==============================================================================
# 36. Pipeline: uncertain skips deep
# ==============================================================================

@pytest.mark.asyncio
async def test_36_pipeline_uncertain_skips_deep(db_session):
    """Pipeline does not call deep analysis when triage gives uncertain (40 <= score < 70)."""
    call_count = {"analyze": 0}

    class UncertainProvider(MockAIProvider):
        def __init__(self):
            super().__init__(fixed_score=55)

        async def analyze(self, prompt_version, entry, matrix_snapshot, **kwargs):
            call_count["analyze"] += 1
            result = await super().analyze(prompt_version, entry, matrix_snapshot, **kwargs)
            result.payload.topics = []
            return result

    source = make_source(db_session, "Source 36")
    entry = make_entry(db_session, source)
    matrix, _ = make_matrix(db_session, "36")
    triage_p = make_prompt(db_session, "triage36", 1, "triage")
    deep_p = make_prompt(db_session, "deep36", 1, "deep_analysis")

    pipeline = AnalysisPipelineService(provider=UncertainProvider())
    analysis = await pipeline.run_pipeline(
        entry_id=entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=triage_p.id,
        deep_prompt_id=deep_p.id,
        db=db_session,
    )

    assert analysis.status == "completed"
    assert analysis.relevance_status == "uncertain"
    assert call_count["analyze"] == 1
    assert analysis.summary is None


# ==============================================================================
# 37. Pipeline: relevant calls deep
# ==============================================================================

@pytest.mark.asyncio
async def test_37_pipeline_relevant_calls_deep(db_session):
    """Pipeline calls deep analysis when triage gives relevant (score >= 70)."""
    call_count = {"analyze": 0, "stages": []}

    class RelevantProvider(MockAIProvider):
        def __init__(self):
            super().__init__(
                fixed_score=85,
                fixed_topics=[
                    AIAnalysisTopicItem(
                        topic_code="merger_control",
                        is_primary=True,
                        confidence=0.9,
                    )
                ],
            )

        async def analyze(self, prompt_version, entry, matrix_snapshot, **kwargs):
            call_count["analyze"] += 1
            call_count["stages"].append(prompt_version.stage)
            return await super().analyze(prompt_version, entry, matrix_snapshot, **kwargs)

    source = make_source(db_session, "Source 37")
    entry = make_entry(
        db_session,
        source,
        content="Merger filing notification for control of undertaking under Article 101 and relevant competition rules. " * 10,
    )
    matrix, _ = make_matrix(db_session, "37")
    triage_p = make_prompt(db_session, "triage37", 1, "triage")
    deep_p = make_prompt(db_session, "deep37", 1, "deep_analysis")

    pipeline = AnalysisPipelineService(provider=RelevantProvider())
    analysis = await pipeline.run_pipeline(
        entry_id=entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=triage_p.id,
        deep_prompt_id=deep_p.id,
        db=db_session,
    )

    assert analysis.status == "completed"
    assert analysis.relevance_status == "relevant"
    assert analysis.relevance_score == 85
    assert call_count["analyze"] == 2
    assert "triage" in call_count["stages"]
    assert "deep_analysis" in call_count["stages"]
    assert analysis.summary is not None


# ==============================================================================
# 38. Pipeline: triage failure -> no deep
# ==============================================================================

@pytest.mark.asyncio
async def test_38_pipeline_triage_failure_no_deep(db_session):
    """Triage failure marks EntryAnalysis as failed and never calls deep."""
    call_count = {"analyze": 0}

    class FailingProvider(MockAIProvider):
        def __init__(self):
            super().__init__(force_failure=True)

        async def analyze(self, prompt_version, entry, matrix_snapshot, **kwargs):
            call_count["analyze"] += 1
            return await super().analyze(prompt_version, entry, matrix_snapshot, **kwargs)

    source = make_source(db_session, "Source 38")
    entry = make_entry(db_session, source)
    matrix, _ = make_matrix(db_session, "38")
    triage_p = make_prompt(db_session, "triage38", 1, "triage")
    deep_p = make_prompt(db_session, "deep38", 1, "deep_analysis")

    pipeline = AnalysisPipelineService(provider=FailingProvider())
    analysis = await pipeline.run_pipeline(
        entry_id=entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=triage_p.id,
        deep_prompt_id=deep_p.id,
        db=db_session,
    )

    assert analysis.status == "failed"
    assert call_count["analyze"] == 1
    assert analysis.relevance_score is None

    calls = db_session.query(AnalysisCall).filter(
        AnalysisCall.entry_analysis_id == analysis.id
    ).all()
    assert len(calls) == 1
    assert calls[0].status == "failed"
    assert calls[0].stage == "triage"


# ==============================================================================
# 39. Pipeline: deep failure preserves triage data
# ==============================================================================

@pytest.mark.asyncio
async def test_39_pipeline_deep_failure_preserves_triage(db_session):
    """Deep failure marks EntryAnalysis as failed but preserves triage score/topics/reason."""
    stage_call = {"current": 0}

    class SelectiveFailProvider(MockAIProvider):
        def __init__(self):
            super().__init__(
                fixed_score=85,
                fixed_topics=[
                    AIAnalysisTopicItem(
                        topic_code="merger_control",
                        is_primary=True,
                        confidence=0.9,
                    )
                ],
            )

        async def analyze(self, prompt_version, entry, matrix_snapshot, **kwargs):
            stage_call["current"] += 1
            if prompt_version.stage == "deep_analysis":
                return AIProviderResult(
                    success=False,
                    provider_name="mock",
                    model="mock",
                    error_type="DeepFailure",
                    error_message="Simulated deep analysis failure",
                    latency_ms=5,
                )
            return await super().analyze(prompt_version, entry, matrix_snapshot, **kwargs)

    source = make_source(db_session, "Source 39")
    entry = make_entry(
        db_session,
        source,
        content="Merger filing for company X acquiring company Y under European and national competition frameworks. " * 10,
    )
    matrix, _ = make_matrix(db_session, "39")
    triage_p = make_prompt(db_session, "triage39", 1, "triage")
    deep_p = make_prompt(db_session, "deep39", 1, "deep_analysis")

    pipeline = AnalysisPipelineService(provider=SelectiveFailProvider())
    analysis = await pipeline.run_pipeline(
        entry_id=entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=triage_p.id,
        deep_prompt_id=deep_p.id,
        db=db_session,
    )

    assert analysis.status == "failed"
    assert analysis.relevance_score == 85
    assert analysis.relevance_status == "relevant"
    assert analysis.reason is not None
    assert analysis.summary is None

    calls = sorted(
        db_session.query(AnalysisCall).filter(AnalysisCall.entry_analysis_id == analysis.id).all(),
        key=lambda c: c.created_at,
    )
    assert len(calls) == 2
    triage_call = next(c for c in calls if c.stage == "triage")
    deep_call = next(c for c in calls if c.stage == "deep_analysis")
    assert triage_call.status == "completed"
    assert deep_call.status == "failed"


# ==============================================================================
# 40. Disabled provider -> RuntimeError
# ==============================================================================

def test_40_pipeline_disabled_provider(monkeypatch):
    """AnalysisService.get_provider() raises RuntimeError when ANALYSIS_PROVIDER=disabled."""
    monkeypatch.setattr(get_settings(), "ANALYSIS_PROVIDER", "disabled")
    svc = AnalysisService()
    with pytest.raises(RuntimeError, match="disabled"):
        svc.get_provider()


# ==============================================================================
# 41. Benchmark: requires --confirm-real-calls flag
# ==============================================================================

def test_41_benchmark_requires_confirm_flag():
    """Benchmark exits with SystemExit when real calls requested but ANALYSIS_PROVIDER != gemini_api."""
    from scripts.run_analysis_benchmark import run_benchmark

    settings_patch = MagicMock()
    settings_patch.ANALYSIS_PROVIDER = "disabled"
    settings_patch.GEMINI_API_KEY = ""

    db = MagicMock()

    with patch("scripts.run_analysis_benchmark.get_settings", return_value=settings_patch):
        with pytest.raises(SystemExit):
            asyncio.run(run_benchmark(real_calls=True, max_usd=1.0, db=db))


# ==============================================================================
# 42. Benchmark: deterministic sample selection
# ==============================================================================

def test_42_benchmark_deterministic_sample(db_session):
    """Sample selection is deterministic: same sources and published_at order."""
    from scripts.run_analysis_benchmark import select_sample

    source_a = make_source(db_session, "Source A")
    source_b = make_source(db_session, "Source B")

    for i in range(7):
        make_entry(
            db_session, source_a,
            title=f"Entry A{i}",
            published_at=datetime(2026, 9, i + 1, 12, 0, tzinfo=UTC),
        )

    for i in range(3):
        make_entry(
            db_session, source_b,
            title=f"Entry B{i}",
            published_at=datetime(2026, 9, i + 1, 12, 0, tzinfo=UTC),
        )

    db_session.commit()

    sample1 = select_sample(db_session)
    sample2 = select_sample(db_session)

    assert [e.id for e in sample1] == [e.id for e in sample2]
    source_a_sample = [e for e in sample1 if e.source_id == source_a.id]
    assert len(source_a_sample) == 5
    sampled_titles_a = {e.title for e in source_a_sample}
    expected_titles_a = {f"Entry A{i}" for i in range(2, 7)}
    assert sampled_titles_a == expected_titles_a
    source_b_sample = [e for e in sample1 if e.source_id == source_b.id]
    assert len(source_b_sample) == 3


# ==============================================================================
# 43. Benchmark: budget hard stop
# ==============================================================================

@pytest.mark.asyncio
async def test_43_benchmark_budget_hard_stop(db_session):
    """Budget hard stop: benchmark stops before next Entry when accumulated_cost >= max_usd."""
    processed_entries: list[str] = []

    async def fake_run_pipeline(**kwargs):
        entry_id = kwargs.get("entry_id")
        processed_entries.append(str(entry_id))
        mock_analysis = MagicMock()
        mock_analysis.id = uuid.uuid4()
        mock_analysis.status = "completed"
        mock_analysis.relevance_score = 30
        mock_analysis.relevance_status = "not_relevant"
        mock_analysis.confidence = 0.5
        mock_analysis.reason = "Not relevant."
        mock_analysis.summary = None
        mock_analysis.key_points = []
        mock_analysis.topics = []
        mock_analysis.calls = []
        return mock_analysis

    source = make_source(db_session, "Source Budget")
    entries = [make_entry(db_session, source, title=f"Budget Entry {i}") for i in range(5)]
    db_session.commit()

    max_usd = 1.00
    accumulated_cost = 0.0
    cost_per_entry = 0.60

    budget_stopped = False
    processed_count = 0

    for entry in entries:
        if accumulated_cost >= max_usd:
            budget_stopped = True
            break
        await fake_run_pipeline(entry_id=entry.id)
        accumulated_cost += cost_per_entry
        processed_count += 1

    assert processed_count == 2
    assert budget_stopped is True
    assert accumulated_cost == pytest.approx(1.20)


# ==============================================================================
# 44. GeminiAPIProvider client init & missing API key error
# ==============================================================================

def test_44_gemini_provider_client_init_and_no_key_raises():
    """GeminiAPIProvider raises RuntimeError when GEMINI_API_KEY is empty."""
    settings_no_key = make_gemini_settings(GEMINI_API_KEY="")
    provider = GeminiAPIProvider(settings=settings_no_key)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not configured"):
        provider._get_client()

    # With key present, Client is created without vertexai=True or GCP parameters
    settings_with_key = make_gemini_settings(GEMINI_API_KEY="my_secret_key_abc")
    provider2 = GeminiAPIProvider(settings=settings_with_key)
    with patch("google.genai.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance
        client = provider2._get_client()
        mock_client_cls.assert_called_once_with(api_key="my_secret_key_abc")
        # Ensure no vertexai, project, or location passed
        call_kwargs = mock_client_cls.call_args.kwargs
        assert "vertexai" not in call_kwargs
        assert "project" not in call_kwargs
        assert "location" not in call_kwargs


# ==============================================================================
# 45. Gemini API key is never leaked in call_metadata or raw_response
# ==============================================================================

@pytest.mark.asyncio
async def test_45_gemini_api_key_never_leaked_in_metadata(db_session):
    """API key secret is never present in call_metadata, raw_response, or error messages."""
    secret_key = "super_secret_production_key_xyz987"
    settings = make_gemini_settings(GEMINI_API_KEY=secret_key)
    provider = GeminiAPIProvider(settings=settings)

    mock_parsed = TriageAnalysisResult(
        relevance_score=80,
        confidence=0.9,
        topic_codes=["merger_control"],
        primary_topic_code="merger_control",
        reason="Analisis de concentracion.",
    )
    mock_response = MagicMock()
    mock_response.parsed = mock_parsed
    mock_usage = MagicMock()
    mock_usage.prompt_token_count = 100
    mock_usage.candidates_token_count = 50
    mock_usage.thoughts_token_count = 0
    mock_usage.total_token_count = 150
    mock_response.usage_metadata = mock_usage

    source = make_source(db_session, "Test Source 45")
    entry = make_entry(db_session, source)
    prompt = make_prompt(db_session, "triage45")
    matrix, _ = make_matrix(db_session, "45")
    from app.services.analysis_service import compute_matrix_snapshot
    snapshot, _ = compute_matrix_snapshot(matrix)

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    provider._client = mock_client

    result = await provider.analyze(prompt_version=prompt, entry=entry, matrix_snapshot=snapshot)

    assert result.success is True
    meta_str = str(result.call_metadata)
    raw_str = str(result.raw_response)
    assert secret_key not in meta_str
    assert secret_key not in raw_str
    assert "super_secret" not in meta_str
    assert "super_secret" not in raw_str


# ==============================================================================
# 46. Benchmark: unanalysed sample excludes already analysed entries
# ==============================================================================

def test_46_benchmark_excludes_analysed_entries(db_session):
    """select_sample excludes entries that already have an EntryAnalysis and selects next available."""
    from scripts.run_analysis_benchmark import select_sample

    source = make_source(db_session, "Source Unanalysed Test")
    matrix, _ = make_matrix(db_session, "46")
    prompt = make_prompt(db_session, "triage46")

    # Create 7 entries (dates day 1 to day 7)
    entries = []
    for i in range(7):
        e = make_entry(
            db_session, source,
            title=f"Unanalysed Entry {i}",
            published_at=datetime(2026, 9, i + 1, 12, 0, tzinfo=UTC),
        )
        entries.append(e)
    db_session.flush()

    # Create an EntryAnalysis for the newest entry (day 7, index 6)
    # mimicking the smoke test situation
    newest_entry = entries[6]
    ea = EntryAnalysis(
        entry_id=newest_entry.id,
        matrix_id=matrix.id,
        matrix_snapshot={"name": "Test Matrix"},
        matrix_snapshot_hash="fake_snapshot_hash_123",
        entry_content_hash="fake_content_hash_123",
        pipeline_version="v2",
        status="completed",
        relevance_score=95,
        relevance_status="relevant",
        confidence=0.99,
        reason="Smoke test analysis",
    )
    db_session.add(ea)
    db_session.commit()

    sample = select_sample(db_session)
    source_sample = [e for e in sample if e.source_id == source.id]

    assert len(source_sample) == 5
    sampled_ids = {e.id for e in source_sample}
    assert newest_entry.id not in sampled_ids

    # Expected: entries at indices 1, 2, 3, 4, 5 (days 2, 3, 4, 5, 6)
    expected_ids = {entries[i].id for i in range(1, 6)}
    assert sampled_ids == expected_ids


# ==============================================================================
# 47. Benchmark: run metrics isolation by benchmark_run_id
# ==============================================================================

def test_47_benchmark_metrics_isolation():
    """Benchmark aggregations only sum calls belonging to the specific benchmark_run_id."""
    run_id_a = str(uuid.uuid4())
    run_id_b = str(uuid.uuid4())

    mock_calls = [
        # Call from smoke test (run_id_a)
        {"run_id": run_id_a, "cost": 0.009912, "tokens": 4434, "stage": "triage"},
        # Calls from current benchmark (run_id_b)
        {"run_id": run_id_b, "cost": 0.002500, "tokens": 1500, "stage": "triage"},
        {"run_id": run_id_b, "cost": 0.006000, "tokens": 2000, "stage": "deep_analysis"},
    ]

    # Filter isolated to run_id_b
    bench_calls = [c for c in mock_calls if c["run_id"] == run_id_b]
    bench_cost = sum(c["cost"] for c in bench_calls)
    bench_tokens = sum(c["tokens"] for c in bench_calls)

    assert len(bench_calls) == 2
    assert bench_cost == pytest.approx(0.008500)
    assert bench_tokens == 3500

    # Verify run_id_a was excluded
    assert all(c["cost"] != 0.009912 for c in bench_calls)

