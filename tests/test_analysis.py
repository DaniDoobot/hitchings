"""Comprehensive tests for AI analysis foundation, models, provider abstraction, service, and APIs."""

import uuid
from datetime import datetime, timezone
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.analysis import (
    AnalysisPromptVersion,
    EntryAnalysis,
    EntryAnalysisTopic,
    AnalysisCall,
)
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.providers.ai.base import BaseAIProvider, AIProviderResult
from app.providers.ai.mock import MockAIProvider
from app.schemas.analysis import AIAnalysisResponsePayload, AIAnalysisTopicItem
from app.services.analysis_service import (
    AnalysisService,
    compute_matrix_snapshot,
    compute_content_hash,
)
from scripts.seed_analysis_prompts import seed_analysis_prompts


# ==============================================================================
# Helpers & Fixtures
# ==============================================================================

def create_test_source_and_entry(db: Session, title: str = "Test Antitrust Merger", content: str = "Details regarding competition and mergers.") -> tuple[Source, Entry]:
    source = Source(
        name="Test Source",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://example.com/source",
    )
    db.add(source)
    db.flush()

    entry = Entry(
        source_id=source.id,
        url="https://example.com/entry/1",
        title=title,
        content=content,
        excerpt=content[:50],
        published_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    db.add(entry)
    db.flush()
    return source, entry


def create_test_matrix_with_topics(db: Session) -> tuple[TrackingMatrix, list[TrackingTopic]]:
    matrix = TrackingMatrix(
        code="TEST-MATRIX-v1",
        name="Test Matrix",
        description="Testing matrix",
        status="active",
        relevance_instructions="Include competition, antitrust, state aid",
        exclusion_instructions="Exclude unrelated criminal matters",
    )
    db.add(matrix)
    db.flush()

    topic1 = TrackingTopic(
        matrix_id=matrix.id,
        code="antitrust_cartels",
        name="Antitrust & Cartels",
        description="Cartel investigations and horizontal agreements",
        keywords=["cartel", "antitrust", "competencia"],
        active=True,
    )
    topic2 = TrackingTopic(
        matrix_id=matrix.id,
        code="merger_control",
        name="Merger Control",
        description="Mergers and acquisitions scrutiny",
        keywords=["merger", "concentrac"],
        active=True,
    )
    topic_inactive = TrackingTopic(
        matrix_id=matrix.id,
        code="inactive_topic",
        name="Inactive Topic",
        active=False,
    )
    db.add_all([topic1, topic2, topic_inactive])
    db.flush()
    return matrix, [topic1, topic2]


def create_test_prompt_version(db: Session, code: str = "test_prompt", version: int = 1, active: bool = True) -> AnalysisPromptVersion:
    prompt = AnalysisPromptVersion(
        code=code,
        version=version,
        stage="triage",
        name="Test Prompt v1",
        description="Prompt for tests",
        system_prompt="You are a competition law expert.",
        user_prompt_template="Analyze {entry_title} with content {entry_content}",
        response_schema_version="v1",
        config={"temperature": 0.0},
        active=active,
    )
    db.add(prompt)
    db.flush()
    return prompt


# ==============================================================================
# Tests 1 - 4: Models & Integrity Constraints
# ==============================================================================

def test_1_analysis_prompt_version_model_and_uniqueness(db_session: Session) -> None:
    """Verify AnalysisPromptVersion model persistence and (code, version) uniqueness."""
    pv1 = create_test_prompt_version(db_session, code="prompt_uniq", version=1)
    db_session.commit()
    assert pv1.id is not None
    assert pv1.active is True
    assert repr(pv1) == "<AnalysisPromptVersion prompt_uniq:v1 stage=triage>"

    # Attempt duplicate (code, version)
    pv2 = AnalysisPromptVersion(
        code="prompt_uniq",
        version=1,
        stage="deep_analysis",
        name="Duplicate Prompt",
        system_prompt="sys",
        user_prompt_template="usr",
        response_schema_version="v1",
    )
    db_session.add(pv2)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.flush()
    db_session.rollback()


def test_2_entry_analysis_model_defaults_and_relationships(db_session: Session) -> None:
    """Verify EntryAnalysis fields, status defaults, and Entry/Matrix relationships."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, topics = create_test_matrix_with_topics(db_session)

    analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v1",
        matrix_snapshot={"code": matrix.code},
        matrix_snapshot_hash="hash123",
        relevance_score=85,
        relevance_status="relevant",
    )
    db_session.add(analysis)
    db_session.commit()
    db_session.refresh(analysis)

    assert analysis.id is not None
    assert analysis.status == "pending"
    assert analysis.entry.id == entry.id
    assert analysis.matrix.id == matrix.id
    assert analysis in entry.analyses
    assert repr(analysis).startswith("<EntryAnalysis")


def test_3_entry_analysis_topic_model_and_uniqueness(db_session: Session) -> None:
    """Verify EntryAnalysisTopic persistence, relationships, and uniqueness per analysis/topic."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, topics = create_test_matrix_with_topics(db_session)
    analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v1",
        matrix_snapshot={},
        matrix_snapshot_hash="hash",
    )
    db_session.add(analysis)
    db_session.flush()

    at1 = EntryAnalysisTopic(
        analysis_id=analysis.id,
        topic_id=topics[0].id,
        confidence=0.9,
        is_primary=True,
        rationale="Clear antitrust focus",
    )
    db_session.add(at1)
    db_session.commit()
    assert at1.id is not None
    assert at1.topic.code == "antitrust_cartels"
    assert at1 in analysis.topics

    # Attempt duplicate topic for same analysis
    at_dup = EntryAnalysisTopic(
        analysis_id=analysis.id,
        topic_id=topics[0].id,
        confidence=0.5,
        is_primary=False,
    )
    db_session.add(at_dup)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.flush()
    db_session.rollback()


def test_4_analysis_call_model_and_relationships(db_session: Session) -> None:
    """Verify AnalysisCall model audit fields and relationships with prompt and analysis."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, topics = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session, code="audit_prompt", version=1)

    analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v1",
        matrix_snapshot={},
        matrix_snapshot_hash="hash",
    )
    db_session.add(analysis)
    db_session.flush()

    call = AnalysisCall(
        entry_analysis_id=analysis.id,
        prompt_version_id=prompt.id,
        stage="triage",
        provider="mock",
        model="mock-v1",
        status="completed",
        input_tokens=120,
        output_tokens=45,
        estimated_cost_usd=0.00025,
        latency_ms=23,
    )
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    assert call.id is not None
    assert call.entry_analysis.id == analysis.id
    assert call.prompt_version.id == prompt.id
    assert repr(call).startswith("<AnalysisCall")


# ==============================================================================
# Tests 5 - 7: Snapshotting, Hashing, & Relevance Derivation
# ==============================================================================

def test_5_compute_matrix_snapshot_and_hash(db_session: Session) -> None:
    """Ensure matrix snapshot captures active topics, excludes inactive, and generates deterministic hash."""
    matrix, topics = create_test_matrix_with_topics(db_session)

    snapshot_1, hash_1 = compute_matrix_snapshot(matrix)
    snapshot_2, hash_2 = compute_matrix_snapshot(matrix)

    assert hash_1 == hash_2
    assert snapshot_1["code"] == matrix.code
    assert len(snapshot_1["topics"]) == 2  # 2 active, 1 inactive excluded
    topic_codes = [t["code"] for t in snapshot_1["topics"]]
    assert "antitrust_cartels" in topic_codes
    assert "merger_control" in topic_codes
    assert "inactive_topic" not in topic_codes


def test_6_compute_content_hash(db_session: Session) -> None:
    """Ensure content hash is deterministic and changes with title/content."""
    source, entry = create_test_source_and_entry(db_session, title="Title A", content="Content A")
    hash_a1 = compute_content_hash(entry)
    hash_a2 = compute_content_hash(entry)
    assert hash_a1 == hash_a2

    entry.title = "Title B"
    hash_b = compute_content_hash(entry)
    assert hash_a1 != hash_b


def test_7_derive_relevance_status() -> None:
    """Verify numerical score classification against configured thresholds."""
    service = AnalysisService()
    assert service.derive_relevance_status(100) == "relevant"
    assert service.derive_relevance_status(70) == "relevant"
    assert service.derive_relevance_status(69) == "uncertain"
    assert service.derive_relevance_status(40) == "uncertain"
    assert service.derive_relevance_status(39) == "not_relevant"
    assert service.derive_relevance_status(0) == "not_relevant"
    assert service.derive_relevance_status(None) is None


# ==============================================================================
# Tests 8 - 11: MockAIProvider Behavior
# ==============================================================================

@pytest.mark.asyncio
async def test_8_mock_ai_provider_default_behavior(db_session: Session) -> None:
    """MockAIProvider should detect keywords, score high, assign topics, and calculate costs."""
    source, entry = create_test_source_and_entry(
        db_session,
        title="CNMC sanciona cártel de transporte",
        content="La CNMC ha multado a varias empresas por infracción del artículo 101.",
    )
    matrix, topics = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)
    snapshot, _ = compute_matrix_snapshot(matrix)

    provider = MockAIProvider()
    result = await provider.analyze(prompt, entry, snapshot)

    assert result.success is True
    assert result.payload is not None
    assert result.payload.relevance_score == 85
    assert len(result.payload.topics) >= 1
    assert result.payload.topics[0].topic_code == "antitrust_cartels"
    assert result.payload.topics[0].is_primary is True
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.estimated_cost_usd > 0.0


@pytest.mark.asyncio
async def test_9_mock_ai_provider_fixed_score_and_topics(db_session: Session) -> None:
    """MockAIProvider supports fixed scores and custom topic overrides."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, topics = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)
    snapshot, _ = compute_matrix_snapshot(matrix)

    custom_topics = [
        AIAnalysisTopicItem(topic_code="merger_control", confidence=0.95, is_primary=True)
    ]
    provider = MockAIProvider(fixed_score=62, fixed_topics=custom_topics)
    result = await provider.analyze(prompt, entry, snapshot)

    assert result.success is True
    assert result.payload.relevance_score == 62
    assert result.payload.topics[0].topic_code == "merger_control"


@pytest.mark.asyncio
async def test_10_mock_ai_provider_force_failure(db_session: Session) -> None:
    """MockAIProvider returns structured failure when force_failure is enabled."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, _ = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)
    snapshot, _ = compute_matrix_snapshot(matrix)

    provider = MockAIProvider(force_failure=True, error_type="RateLimitError", error_message="Exceeded quota")
    result = await provider.analyze(prompt, entry, snapshot)

    assert result.success is False
    assert result.payload is None
    assert result.error_type == "RateLimitError"
    assert result.error_message == "Exceeded quota"


@pytest.mark.asyncio
async def test_11_mock_ai_provider_invalid_format(db_session: Session) -> None:
    """MockAIProvider returns validation failure when invalid_format is enabled."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, _ = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)
    snapshot, _ = compute_matrix_snapshot(matrix)

    provider = MockAIProvider(invalid_format=True)
    result = await provider.analyze(prompt, entry, snapshot)

    assert result.success is False
    assert result.error_type == "ValidationError"


# ==============================================================================
# Tests 12 - 13: AnalysisService Provider Resolution & Safety
# ==============================================================================

def test_12_analysis_service_provider_disabled() -> None:
    """AnalysisService halts with RuntimeError if ANALYSIS_PROVIDER='disabled'."""
    service = AnalysisService()
    # Default is 'disabled'
    assert service.settings.ANALYSIS_PROVIDER == "disabled"
    with pytest.raises(RuntimeError) as exc_info:
        service.get_provider()
    assert "disabled" in str(exc_info.value)


def test_13_analysis_service_provider_mock(monkeypatch) -> None:
    """AnalysisService returns MockAIProvider when ANALYSIS_PROVIDER='mock'."""
    monkeypatch.setattr(get_settings(), "ANALYSIS_PROVIDER", "mock")
    service = AnalysisService()
    provider = service.get_provider()
    assert isinstance(provider, MockAIProvider)


# ==============================================================================
# Tests 14 - 21: AnalysisService Execution, Validation & Quotas
# ==============================================================================

@pytest.mark.asyncio
async def test_14_analyze_entry_success(db_session: Session) -> None:
    """Full execution of analyze_entry with mock provider persisting analysis, topic, and call."""
    source, entry = create_test_source_and_entry(
        db_session,
        title="Merger clearance decision",
        content="Acquisition approved subject to remedies under merger control.",
    )
    matrix, topics = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)

    service = AnalysisService(provider=MockAIProvider())
    analysis = await service.analyze_entry(
        entry_id=entry.id,
        matrix_id=matrix.id,
        prompt_version_id=prompt.id,
        db=db_session,
        pipeline_version="v1",
    )

    assert analysis.status == "completed"
    assert analysis.relevance_score == 85
    assert analysis.relevance_status == "relevant"
    assert len(analysis.topics) >= 1
    assert len(analysis.calls) == 1
    assert analysis.calls[0].status == "completed"
    assert analysis.calls[0].provider == "mock"
    assert analysis.entry_content_hash is not None
    assert analysis.matrix_snapshot_hash is not None


@pytest.mark.asyncio
async def test_15_analyze_entry_multiple_primary_topics_rejected(db_session: Session) -> None:
    """Ensure service rejects responses with multiple primary topics without autocorrecting."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, topics = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)

    two_primaries = [
        AIAnalysisTopicItem(topic_code="antitrust_cartels", is_primary=True),
        AIAnalysisTopicItem(topic_code="merger_control", is_primary=True),
    ]
    service = AnalysisService(provider=MockAIProvider(fixed_topics=two_primaries))
    analysis = await service.analyze_entry(
        entry_id=entry.id,
        matrix_id=matrix.id,
        prompt_version_id=prompt.id,
        db=db_session,
    )

    assert analysis.status == "failed"
    assert "Multiple primary topics" in (analysis.reason or "")
    assert len(analysis.topics) == 0
    assert len(analysis.calls) == 1
    assert analysis.calls[0].status == "failed"
    assert analysis.calls[0].error_type == "AnalysisValidationError"


@pytest.mark.asyncio
async def test_16_analyze_entry_unrecognized_topic_rejected(db_session: Session) -> None:
    """Service rejects unknown or hallucinated topic codes and records validation failure."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, topics = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)

    unknown_topics = [
        AIAnalysisTopicItem(topic_code="non_existent_topic_code", is_primary=True),
        AIAnalysisTopicItem(topic_code="antitrust_cartels", is_primary=False),
    ]
    service = AnalysisService(provider=MockAIProvider(fixed_topics=unknown_topics))
    analysis = await service.analyze_entry(
        entry_id=entry.id,
        matrix_id=matrix.id,
        prompt_version_id=prompt.id,
        db=db_session,
    )

    assert analysis.status == "failed"
    assert "Unrecognized topic code" in (analysis.reason or "")
    assert len(analysis.topics) == 0
    assert len(analysis.calls) == 1
    assert analysis.calls[0].status == "failed"
    assert analysis.calls[0].error_type == "AnalysisValidationError"


@pytest.mark.asyncio
async def test_17_analyze_entry_inactive_prompt_raises(db_session: Session) -> None:
    """Attempting analysis with an inactive prompt raises ValueError."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, _ = create_test_matrix_with_topics(db_session)
    inactive_prompt = create_test_prompt_version(db_session, code="inactive_p", active=False)

    service = AnalysisService(provider=MockAIProvider())
    with pytest.raises(ValueError) as exc_info:
        await service.analyze_entry(entry.id, matrix.id, inactive_prompt.id, db_session)
    assert "inactive" in str(exc_info.value)


@pytest.mark.asyncio
async def test_18_analyze_entry_missing_entities_raises(db_session: Session) -> None:
    """Attempting analysis with invalid entry or matrix IDs raises ValueError."""
    source, entry = create_test_source_and_entry(db_session)
    prompt = create_test_prompt_version(db_session)
    service = AnalysisService(provider=MockAIProvider())

    # Invalid entry
    with pytest.raises(ValueError):
        await service.analyze_entry(uuid.uuid4(), uuid.uuid4(), prompt.id, db_session)


@pytest.mark.asyncio
async def test_19_analyze_entry_provider_failure_persists_audit(db_session: Session) -> None:
    """When provider fails, EntryAnalysis is marked failed and AnalysisCall is recorded with failed status."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, _ = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)

    failing_provider = MockAIProvider(force_failure=True, error_type="SimulatedTimeout", error_message="Gateway timeout")
    service = AnalysisService(provider=failing_provider)

    analysis = await service.analyze_entry(entry.id, matrix.id, prompt.id, db_session)

    assert analysis.status == "failed"
    assert "SimulatedTimeout" in (analysis.reason or "")
    assert len(analysis.calls) == 1
    assert analysis.calls[0].status == "failed"
    assert analysis.calls[0].error_type == "SimulatedTimeout"
    assert analysis.calls[0].error_message == "Gateway timeout"


@pytest.mark.asyncio
async def test_20_analysis_service_monthly_limit(db_session: Session, monkeypatch) -> None:
    """Monthly limit check helper raises when reached, but analyze_entry does not block in Bloque 7A."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, topics = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)

    # Set threshold low
    monkeypatch.setattr(get_settings(), "ANALYSIS_MONTHLY_ENTRY_LIMIT", 1)

    # Insert 1 completed analysis
    analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v1",
        status="completed",
        matrix_snapshot={},
        matrix_snapshot_hash="hash",
    )
    db_session.add(analysis)
    db_session.commit()

    service = AnalysisService(provider=MockAIProvider())
    # Direct helper raises RuntimeError
    with pytest.raises(RuntimeError) as exc_info:
        service.check_monthly_limit(db_session)
    assert "limit reached" in str(exc_info.value)

    # analyze_entry does NOT block (enforcement deactivated in 7A per guidelines)
    analysis2 = await service.analyze_entry(entry.id, matrix.id, prompt.id, db_session)
    assert analysis2.status == "completed"


def test_21_analysis_service_usage_summary(db_session: Session) -> None:
    """Verify aggregated usage statistics calculation across multiple audit calls with completed/failed."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, _ = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)

    analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v1",
        matrix_snapshot={},
        matrix_snapshot_hash="hash",
    )
    db_session.add(analysis)
    db_session.flush()

    call1 = AnalysisCall(
        entry_analysis_id=analysis.id,
        prompt_version_id=prompt.id,
        stage="triage",
        provider="mock",
        model="mock-v1",
        status="completed",
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.0002,
    )
    call2 = AnalysisCall(
        entry_analysis_id=analysis.id,
        prompt_version_id=prompt.id,
        stage="triage",
        provider="mock",
        model="mock-v1",
        status="failed",
        input_tokens=50,
        output_tokens=0,
        estimated_cost_usd=0.00005,
    )
    db_session.add_all([call1, call2])
    db_session.commit()

    service = AnalysisService()
    usage = service.get_usage_summary(db_session)

    assert usage.total_calls == 2
    assert usage.successful_calls == 1
    assert usage.failed_calls == 1
    assert usage.total_input_tokens == 150
    assert usage.total_output_tokens == 50
    assert usage.total_tokens == 200
    assert usage.total_estimated_cost_usd == pytest.approx(0.00025, abs=1e-5)
    assert "mock" in usage.by_provider
    assert "triage" in usage.by_stage


# ==============================================================================
# Test 22: API Endpoints Read Operations & Filters
# ==============================================================================

def test_22_analysis_api_endpoints(client: TestClient, db_session: Session) -> None:
    """Test REST API read endpoints: list (with matrix_id filter), detail with prompt info, usage, and prompts."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, topics = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)

    # 1. Create Analysis with Topic & Call
    analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v1",
        status="completed",
        matrix_snapshot={"name": matrix.name},
        matrix_snapshot_hash="hash-snap",
        relevance_status="relevant",
        relevance_score=90,
        summary="A summary of test decision.",
    )
    db_session.add(analysis)
    db_session.flush()

    at = EntryAnalysisTopic(
        analysis_id=analysis.id,
        topic_id=topics[0].id,
        confidence=0.92,
        is_primary=True,
    )
    call = AnalysisCall(
        entry_analysis_id=analysis.id,
        prompt_version_id=prompt.id,
        stage="triage",
        provider="mock",
        model="mock-v1",
        status="completed",
        input_tokens=80,
        output_tokens=30,
        estimated_cost_usd=0.0001,
    )
    db_session.add_all([at, call])
    db_session.commit()

    # 2. GET /api/v1/entry-analyses (with relevance_status and matrix_id filters)
    resp = client.get(f"/api/v1/entry-analyses?relevance_status=relevant&matrix_id={matrix.id}")
    assert resp.status_code == status.HTTP_200_OK
    analyses_list = resp.json()
    assert len(analyses_list) == 1
    assert analyses_list[0]["relevance_score"] == 90

    # Test matrix_id filter with non-matching ID
    resp_other = client.get(f"/api/v1/entry-analyses?matrix_id={uuid.uuid4()}")
    assert resp_other.status_code == status.HTTP_200_OK
    assert len(resp_other.json()) == 0

    # 3. GET /api/v1/entry-analyses/{id}
    resp_detail = client.get(f"/api/v1/entry-analyses/{analysis.id}")
    assert resp_detail.status_code == status.HTTP_200_OK
    detail_data = resp_detail.json()
    assert detail_data["matrix_snapshot"]["name"] == matrix.name
    assert len(detail_data["topics"]) == 1
    assert detail_data["topics"][0]["topic_code"] == "antitrust_cartels"
    assert len(detail_data["calls"]) == 1
    # Check identifying prompt information
    assert detail_data["calls"][0]["prompt_code"] == "test_prompt"
    assert detail_data["calls"][0]["prompt_version"] == 1
    assert detail_data["calls"][0]["prompt_stage"] == "triage"
    assert detail_data["calls"][0]["status"] == "completed"

    # 4. GET /api/v1/entries/{id}/analyses
    resp_entry = client.get(f"/api/v1/entries/{entry.id}/analyses")
    assert resp_entry.status_code == status.HTTP_200_OK
    assert len(resp_entry.json()) == 1

    # 5. GET /api/v1/analysis-usage
    resp_usage = client.get("/api/v1/analysis-usage")
    assert resp_usage.status_code == status.HTTP_200_OK
    usage_data = resp_usage.json()
    assert usage_data["total_calls"] == 1
    assert usage_data["successful_calls"] == 1
    assert usage_data["total_tokens"] == 110

    # 6. GET /api/v1/analysis-prompts
    resp_prompts = client.get("/api/v1/analysis-prompts")
    assert resp_prompts.status_code == status.HTTP_200_OK
    assert len(resp_prompts.json()) >= 1


# ==============================================================================
# Tests 23 - 27: Idempotency, Immutability & Strict Topic Validation
# ==============================================================================

def test_23_seed_analysis_prompts_idempotency(db_session: Session) -> None:
    """Verify that seed_analysis_prompts runs idempotently without duplicating prompt versions.

    After Bloque 7B, the seed creates 4 prompt versions:
      - observatory_triage v1, observatory_deep_analysis v1 (legacy/mock)
      - observatory_triage v2, observatory_deep_analysis v2 (Gemini API)
    """
    # Run 1
    prompts_run1 = seed_analysis_prompts(db_session)
    assert len(prompts_run1) == 4
    codes1 = {p.code for p in prompts_run1}
    assert codes1 == {"observatory_triage", "observatory_deep_analysis"}
    versions1 = {(p.code, p.version) for p in prompts_run1}
    assert ("observatory_triage", 1) in versions1
    assert ("observatory_triage", 2) in versions1
    assert ("observatory_deep_analysis", 1) in versions1
    assert ("observatory_deep_analysis", 2) in versions1

    # Run 2: content identical -> unchanged (same IDs)
    prompts_run2 = seed_analysis_prompts(db_session)
    assert len(prompts_run2) == 4
    assert {p.id for p in prompts_run1} == {p.id for p in prompts_run2}


def test_24_seed_analysis_prompts_immutability_conflict(db_session: Session, monkeypatch) -> None:
    """Attempting to seed a modified prompt with an existing (code, version) raises ValueError without modifying."""
    from scripts import seed_analysis_prompts as seed_module

    # Seed initial prompts
    prompts_init = seed_module.seed_analysis_prompts(db_session)
    triage_prompt = [p for p in prompts_init if p.code == "observatory_triage"][0]
    original_system_prompt = triage_prompt.system_prompt

    # Alter the definition for observatory_triage v1
    modified_definitions = [
        {
            "code": "observatory_triage",
            "version": 1,
            "stage": "triage",
            "name": "Modified Observatory Triage v1",
            "description": "Altered description",
            "system_prompt": "DIFFERENT SYSTEM PROMPT THAT VIOLATES IMMUTABILITY",
            "user_prompt_template": triage_prompt.user_prompt_template,
            "response_schema_version": "v1",
            "config": {"temperature": 0.1, "max_tokens": 1024},
            "active": True,
        }
    ]
    monkeypatch.setattr(seed_module, "PROMPT_DEFINITIONS", modified_definitions)

    # Attempt to seed with modified content
    with pytest.raises(ValueError) as exc_info:
        with db_session.begin_nested():
            seed_module.seed_analysis_prompts(db_session)

    assert "Immutability conflict" in str(exc_info.value)
    assert "version 2" in str(exc_info.value)

    # Confirm original prompt record is untouched
    db_session.refresh(triage_prompt)
    assert triage_prompt.system_prompt == original_system_prompt


def test_25_matrix_snapshot_comprehensive_structure(db_session: Session) -> None:
    """Ensure matrix_snapshot contains all required business fields and topic structures."""
    matrix, topics = create_test_matrix_with_topics(db_session)
    snapshot, s_hash = compute_matrix_snapshot(matrix)

    # Core matrix fields
    assert "matrix_id" in snapshot
    assert snapshot["code"] == "TEST-MATRIX-v1"
    assert snapshot["name"] == "Test Matrix"
    assert snapshot["status"] == "active"
    assert snapshot["relevance_instructions"] == "Include competition, antitrust, state aid"
    assert snapshot["exclusion_instructions"] == "Exclude unrelated criminal matters"
    assert "topics" in snapshot

    # Active topic items structure
    for t in snapshot["topics"]:
        assert "topic_id" in t
        assert "code" in t
        assert "name" in t
        assert "parent_code" in t
        assert "description" in t
        assert "relevance_instructions" in t
        assert "keywords" in t
        assert isinstance(t["keywords"], list)

    # Hash length
    assert len(s_hash) == 64


@pytest.mark.asyncio
async def test_26_invented_topic_marks_call_and_analysis_failed(db_session: Session) -> None:
    """Invented topic codes cause EntryAnalysis and AnalysisCall to fail with AnalysisValidationError."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, topics = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)

    # Provider hallucinates a topic
    hallucinated = [
        AIAnalysisTopicItem(topic_code="antitrust_cartels", is_primary=False),
        AIAnalysisTopicItem(topic_code="invented_topic", is_primary=True),
    ]
    provider = MockAIProvider(fixed_topics=hallucinated)
    service = AnalysisService(provider=provider)

    analysis = await service.analyze_entry(entry.id, matrix.id, prompt.id, db_session)

    # Both analysis and call must be marked failed
    assert analysis.status == "failed"
    assert len(analysis.topics) == 0  # No topics persisted
    assert len(analysis.calls) == 1

    call = analysis.calls[0]
    assert call.status == "failed"
    assert call.error_type == "AnalysisValidationError"
    assert "invented_topic" in (call.error_message or "")


@pytest.mark.asyncio
async def test_27_primary_topic_validation_failures(db_session: Session) -> None:
    """Reject responses where no primary topic is designated or primary topic is invalid."""
    source, entry = create_test_source_and_entry(db_session)
    matrix, topics = create_test_matrix_with_topics(db_session)
    prompt = create_test_prompt_version(db_session)

    # 1. No primary topic at all
    no_primary = [
        AIAnalysisTopicItem(topic_code="antitrust_cartels", is_primary=False),
        AIAnalysisTopicItem(topic_code="merger_control", is_primary=False),
    ]
    service = AnalysisService(provider=MockAIProvider(fixed_topics=no_primary))
    analysis1 = await service.analyze_entry(entry.id, matrix.id, prompt.id, db_session)
    assert analysis1.status == "failed"
    assert analysis1.calls[0].status == "failed"
    assert analysis1.calls[0].error_type == "AnalysisValidationError"
    assert "No primary topic designated" in analysis1.calls[0].error_message

    # 2. Inactive topic attempted
    inactive_attempt = [
        AIAnalysisTopicItem(topic_code="inactive_topic", is_primary=True),
    ]
    service2 = AnalysisService(provider=MockAIProvider(fixed_topics=inactive_attempt))
    analysis2 = await service2.analyze_entry(entry.id, matrix.id, prompt.id, db_session)
    assert analysis2.status == "failed"
    assert analysis2.calls[0].error_type == "AnalysisValidationError"
