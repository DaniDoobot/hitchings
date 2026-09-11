"""Tests for Bloque 7E: Strict evidence grounding, v3 schemas, validation, sufficiency gate, and pipeline integration.

All tests run offline without external API calls.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.main import app
from app.models.analysis import (
    AnalysisCall,
    AnalysisPromptVersion,
    EntryAnalysis,
    EntryAnalysisTopic,
)
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.providers.ai.gemini_api import GeminiAPIProvider
from app.schemas.analysis import (
    AIAnalysisResponsePayload,
    DeepAnalysisResultV3,
    GroundingEvidence,
    KeyPointV3,
    TriageAnalysisResultV3,
)
from app.services.analysis_pipeline_service import AnalysisPipelineService
from app.services.grounding_validator import (
    AnalysisGroundingError,
    clean_quote_wrapper,
    normalize_text_for_matching,
    validate_deep_evidence,
    validate_grounding_quote,
    validate_triage_evidence,
)

UTC = timezone.utc


# ==============================================================================
# Fixtures & Helpers
# ==============================================================================

def make_source(db_session: Session, name: str = "Test Source 7E") -> Source:
    src = Source(name=name, type=SourceType.WEBSITE, provider="native", url=f"https://{name}.test/")
    db_session.add(src)
    db_session.flush()
    return src


def make_entry(
    db_session: Session,
    source: Source,
    title: str = "Commission fines cartel participants €45 million",
    content: str = "The European Commission has fined three producers of chemical products a total of €45 million for operating a price-fixing cartel in violation of Article 101.",
    excerpt: str = "The European Commission has fined three producers...",
    published_at: Optional[datetime] = None,
) -> Entry:
    entry = Entry(
        source_id=source.id,
        url=f"https://example.com/{uuid.uuid4()}",
        title=title,
        content=content,
        excerpt=excerpt,
        published_at=published_at or datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def make_matrix(db_session: Session, code_suffix: str = "") -> tuple[TrackingMatrix, list[TrackingTopic]]:
    code = f"TEST-7E-{code_suffix}" if code_suffix else "TEST-7E"
    matrix = TrackingMatrix(
        code=code,
        name="Test Matrix 7E",
        status="active",
        relevance_instructions="Include competition, cartels, antitrust",
        exclusion_instructions="Exclude unrelated criminal matters",
    )
    db_session.add(matrix)
    db_session.flush()

    topics = [
        TrackingTopic(
            matrix_id=matrix.id,
            code="cartels_horizontal",
            name="Horizontal Cartels",
            description="Price fixing and market sharing agreements",
        ),
        TrackingTopic(
            matrix_id=matrix.id,
            code="damages_actions",
            name="Damages Actions",
            description="Private enforcement and follow-on damages claims",
        ),
    ]
    for t in topics:
        db_session.add(t)
    db_session.flush()
    return matrix, topics


def make_v3_prompts(db_session: Session) -> tuple[AnalysisPromptVersion, AnalysisPromptVersion]:
    triage_pv = AnalysisPromptVersion(
        code="observatory_triage",
        version=3,
        stage="triage",
        name="Observatory Triage v3 Test",
        system_prompt="Strict grounding instructions v3",
        user_prompt_template="Analyze: {content_section}",
        response_schema_version="v3",
        config={"thinking_level": "low", "max_output_tokens": 2048},
        active=True,
    )
    deep_pv = AnalysisPromptVersion(
        code="observatory_deep_analysis",
        version=3,
        stage="deep_analysis",
        name="Observatory Deep Analysis v3 Test",
        system_prompt="Strict grounding instructions v3 deep",
        user_prompt_template="Deep analyze: {content_section}",
        response_schema_version="v3",
        config={"thinking_level": "medium", "max_output_tokens": 4096},
        active=True,
    )
    db_session.add(triage_pv)
    db_session.add(deep_pv)
    db_session.flush()
    return triage_pv, deep_pv


# ==============================================================================
# Unit Tests
# ==============================================================================

def test_v3_schemas():
    """GroundingEvidence, TriageAnalysisResultV3, DeepAnalysisResultV3 validate properly."""
    ev = GroundingEvidence(source_field="title", quote="Commission fines cartel")
    assert ev.source_field == "title"
    assert ev.quote == "Commission fines cartel"

    triage = TriageAnalysisResultV3(
        relevance_score=85,
        relevance_status="relevant",
        confidence=0.9,
        reason="Relevant antitrust decision",
        evidence=[ev],
        topic_codes=["cartels_horizontal"],
    )
    assert triage.relevance_score == 85
    assert len(triage.evidence) == 1

    kp = KeyPointV3(
        point="Total fines amounted to €45 million.",
        evidence=[GroundingEvidence(source_field="content", quote="total of €45 million")],
    )
    deep = DeepAnalysisResultV3(
        summary="Commission sanctioned chemical producers.",
        summary_evidence=[GroundingEvidence(source_field="content", quote="chemical products")],
        key_points=[kp],
    )
    assert len(deep.key_points) == 1
    assert deep.key_points[0].evidence[0].quote == "total of €45 million"


def test_grounding_validator_exact_match(db_session: Session):
    """Quotes matching title, content, or excerpt validate without error."""
    src = make_source(db_session)
    entry = make_entry(db_session, src)

    ev_title = GroundingEvidence(source_field="title", quote="Commission fines cartel participants €45 million")
    ev_content = GroundingEvidence(source_field="content", quote="fined three producers of chemical products")

    validate_grounding_quote(ev_title, entry)
    validate_grounding_quote(ev_content, entry)

    # Excerpt is valid when entry has excerpt and no content (input contract)
    entry_excerpt_only = make_entry(db_session, src, content="", excerpt="fined three producers...")
    ev_excerpt = GroundingEvidence(source_field="excerpt", quote="fined three producers...")
    validate_grounding_quote(ev_excerpt, entry_excerpt_only)


def test_grounding_validator_normalization(db_session: Session):
    """Matching handles curly quotes, NFKC, CRLF, and multiple whitespace."""
    src = make_source(db_session)
    entry = make_entry(
        db_session,
        src,
        title="Court's judgment in Case T-123/24",
        content="The \"General Court\" held that the agreement was illegal.\r\nNext line here.",
    )

    ev1 = GroundingEvidence(source_field="title", quote="Court’s   judgment in Case T-123/24")
    ev2 = GroundingEvidence(source_field="content", quote="The “General Court” held that\nthe agreement was illegal.")

    validate_grounding_quote(ev1, entry)
    validate_grounding_quote(ev2, entry)


def test_grounding_validator_invalid_field(db_session: Session):
    """Invalid source_field raises AnalysisGroundingError."""
    src = make_source(db_session)
    entry = make_entry(db_session, src)
    ev = GroundingEvidence(source_field="summary", quote="Commission fines")

    with pytest.raises(AnalysisGroundingError) as exc:
        validate_grounding_quote(ev, entry)
    assert "which was not present in the model input for this entry" in str(exc.value) or "unrecognized source_field" in str(exc.value)


def test_grounding_validator_hallucinated_quote(db_session: Session):
    """Hallucinated quote not in source text raises AnalysisGroundingError."""
    src = make_source(db_session)
    entry = make_entry(db_session, src)
    ev = GroundingEvidence(source_field="content", quote="The Tribunal awarded £100,000 in damages")

    with pytest.raises(AnalysisGroundingError) as exc:
        validate_grounding_quote(ev, entry)
    assert "not found in entry" in str(exc.value)


def test_grounding_validator_translated_quote(db_session: Session):
    """Translated or paraphrased quote raises AnalysisGroundingError."""
    src = make_source(db_session)
    entry = make_entry(db_session, src)
    ev = GroundingEvidence(source_field="content", quote="La Comisión Europea ha multado a tres productores")

    with pytest.raises(AnalysisGroundingError) as exc:
        validate_grounding_quote(ev, entry)
    assert "not found in entry" in str(exc.value)


def test_grounding_validator_empty_evidence_list(db_session: Session):
    """Empty evidence list for triage raises AnalysisGroundingError, and empty key points in deep raises error."""
    src = make_source(db_session)
    entry = make_entry(db_session, src)

    with pytest.raises(AnalysisGroundingError) as exc:
        validate_triage_evidence([], entry)
    assert "at least one evidence quote is required" in str(exc.value)

    # Empty key_points list in deep validation raises AnalysisGroundingError
    with pytest.raises(AnalysisGroundingError) as exc2:
        validate_deep_evidence(
            summary_evidence=[GroundingEvidence(source_field="title", quote="Commission fines cartel")],
            key_points=[],
            entry=entry,
        )
    assert "key_points list cannot be empty" in str(exc2.value)


def test_grounding_validator_deep_summary_quote_missing(db_session: Session):
    """Summary evidence with missing quote raises AnalysisGroundingError."""
    src = make_source(db_session)
    entry = make_entry(db_session, src)
    kp = KeyPointV3(
        point="Valid point",
        evidence=[GroundingEvidence(source_field="title", quote="Commission fines cartel participants €45 million")],
    )

    with pytest.raises(AnalysisGroundingError) as exc:
        validate_deep_evidence(
            summary_evidence=[GroundingEvidence(source_field="content", quote="Hallucinated summary fact")],
            key_points=[kp],
            entry=entry,
        )
    assert "not found in entry" in str(exc.value)


from app.providers.ai.base import AIProviderResult, BaseAIProvider


class StubProvider(BaseAIProvider):
    def __init__(self, responses: list[AIProviderResult]):
        self._responses = list(responses)
        self._provider_name = "gemini_api"

    @property
    def provider_name(self) -> str:
        return self._provider_name

    async def analyze(
        self,
        prompt_version: AnalysisPromptVersion,
        entry: Entry,
        matrix_snapshot: dict[str, Any],
        extra_call_metadata: Optional[dict[str, Any]] = None,
        triage_result: Optional[dict[str, Any]] = None,
    ) -> AIProviderResult:
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_pipeline_v3_sufficiency_gate_insufficient_skips_deep(db_session: Session):
    """When source is insufficient, triage executes but deep is skipped."""
    src = make_source(db_session, name="Competition Appeal Tribunal")
    entry = make_entry(db_session, src, title="CAT Cost Ruling", content="Ruling of the Tribunal on costs.")
    matrix, topics = make_matrix(db_session, "insufficient-test")
    triage_pv, deep_pv = make_v3_prompts(db_session)

    raw_resp = {
        "result": {
            "relevance_score": 80,
            "relevance_status": "relevant",
            "confidence": 0.85,
            "reason": "Relevant CAT ruling on costs",
            "evidence": [{"source_field": "content", "quote": "Ruling of the Tribunal on costs."}],
            "topic_codes": ["cartels_horizontal"],
        }
    }
    triage_payload = AIAnalysisResponsePayload(
        relevance_score=80,
        relevance_status="relevant",
        confidence=0.85,
        reason="Relevant CAT ruling on costs",
        evidence=[{"source_field": "content", "quote": "Ruling of the Tribunal on costs."}],
        topic_codes=["cartels_horizontal"],
    )
    result = AIProviderResult(
        success=True,
        payload=triage_payload,
        raw_response=raw_resp,
        provider_name="gemini_api",
        model="gemini-3.8-flash",
        estimated_cost_usd=0.0001,
        latency_ms=150,
    )
    provider = StubProvider([result])

    pipeline = AnalysisPipelineService(provider=provider)
    analysis = await pipeline.run_pipeline(
        entry_id=entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=triage_pv.id,
        deep_prompt_id=deep_pv.id,
        db=db_session,
    )

    assert analysis.status == "completed"
    assert analysis.relevance_status == "relevant"
    assert analysis.summary is None
    assert analysis.key_points == []
    calls = db_session.query(AnalysisCall).filter(AnalysisCall.entry_analysis_id == analysis.id).all()
    assert len(calls) == 1
    assert calls[0].stage == "triage"
    assert calls[0].call_metadata.get("deep_skipped") is True
    assert calls[0].call_metadata.get("deep_skipped_reason") == "insufficient_source"


@pytest.mark.asyncio
async def test_pipeline_v3_triage_grounding_error_fails_analysis_and_call(db_session: Session):
    """If triage returns hallucinated quote, analysis and call fail with AnalysisGroundingError."""
    src = make_source(db_session)
    entry = make_entry(db_session, src)
    matrix, topics = make_matrix(db_session, "triage-grounding-fail")
    triage_pv, deep_pv = make_v3_prompts(db_session)

    raw_resp = {"result": {"fake": True}}
    triage_payload = AIAnalysisResponsePayload(
        relevance_score=85,
        relevance_status="relevant",
        confidence=0.85,
        reason="Reason with fake quote",
        evidence=[{"source_field": "content", "quote": "Totally fabricated quotation"}],
        topic_codes=["cartels_horizontal"],
    )
    result = AIProviderResult(
        success=True,
        payload=triage_payload,
        raw_response=raw_resp,
        provider_name="gemini_api",
        model="gemini-3.8-flash",
        estimated_cost_usd=0.0001,
        latency_ms=100,
    )
    provider = StubProvider([result])

    pipeline = AnalysisPipelineService(provider=provider)
    analysis = await pipeline.run_pipeline(
        entry_id=entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=triage_pv.id,
        deep_prompt_id=deep_pv.id,
        db=db_session,
    )

    assert analysis.status == "failed"
    call = db_session.query(AnalysisCall).filter(AnalysisCall.entry_analysis_id == analysis.id).first()
    assert call.status == "failed"
    assert call.error_type == "AnalysisGroundingError"
    assert "not found in entry" in call.error_message


@pytest.mark.asyncio
async def test_pipeline_v3_deep_grounding_error_fails_analysis_and_call(db_session: Session):
    """If deep returns hallucinated quote, deep call and analysis fail."""
    src = make_source(db_session, name="European Commission")
    content = (
        "The European Commission has fined three producers of chemical products a total of €45 million "
        "for operating a price-fixing cartel in violation of Article 101 of the Treaty on the Functioning "
        "of the European Union. The companies involved coordinated prices and allocated market quotas across "
        "the internal market over a duration of four years. Today's decision demonstrates our unwavering commitment "
        "to enforcing competition law and protecting consumers from unlawful collusive arrangements."
    )
    entry = make_entry(db_session, src, content=content)
    matrix, topics = make_matrix(db_session, "deep-grounding-fail")
    triage_pv, deep_pv = make_v3_prompts(db_session)

    valid_triage_payload = AIAnalysisResponsePayload(
        relevance_score=90,
        relevance_status="relevant",
        confidence=0.9,
        reason="Valid triage",
        evidence=[{"source_field": "title", "quote": "Commission fines cartel participants €45 million"}],
        topic_codes=["cartels_horizontal"],
    )
    invalid_deep_payload = AIAnalysisResponsePayload(
        relevance_score=90,
        relevance_status="relevant",
        reason="Valid triage",
        summary="Some summary",
        summary_evidence=[{"source_field": "title", "quote": "Commission fines cartel participants €45 million"}],
        key_point_items=[{
            "point": "Invalid key point",
            "evidence": [{"source_field": "content", "quote": "Nonexistent quotation in text"}],
        }],
        key_points=["Invalid key point"],
    )

    triage_res = AIProviderResult(
        success=True,
        payload=valid_triage_payload,
        raw_response={"result": {"stage": "triage"}},
        provider_name="gemini_api",
        model="gemini-3.8-flash",
        estimated_cost_usd=0.0001,
        latency_ms=100,
    )
    deep_res = AIProviderResult(
        success=True,
        payload=invalid_deep_payload,
        raw_response={"result": {"stage": "deep"}},
        provider_name="gemini_api",
        model="gemini-3.8-flash",
        estimated_cost_usd=0.0002,
        latency_ms=200,
    )

    provider = StubProvider([triage_res, deep_res])

    pipeline = AnalysisPipelineService(provider=provider)
    analysis = await pipeline.run_pipeline(
        entry_id=entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=triage_pv.id,
        deep_prompt_id=deep_pv.id,
        db=db_session,
    )

    assert analysis.status == "failed"
    calls = db_session.query(AnalysisCall).filter(AnalysisCall.entry_analysis_id == analysis.id).order_by(AnalysisCall.created_at).all()
    assert len(calls) == 2
    assert calls[0].status == "completed"
    assert calls[1].status == "failed"
    assert calls[1].error_type == "AnalysisGroundingError"


def test_reanalysis_creates_new_record_without_overwriting(db_session: Session):
    """Reanalyzing an entry creates a new EntryAnalysis record pointing to the same entry."""
    src = make_source(db_session)
    entry = make_entry(db_session, src)
    matrix, topics = make_matrix(db_session, "reanalysis")
    triage_pv, deep_pv = make_v3_prompts(db_session)

    v2_analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="hash-v2",
        entry_content_hash="content-hash-v2",
        pipeline_version="observatory-v2",
        status="completed",
        relevance_score=75,
        relevance_status="relevant",
        reason="Historical v2 reason",
        summary="Historical summary",
        key_points=["Point 1"],
    )
    db_session.add(v2_analysis)
    db_session.flush()

    v3_analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="hash-v3",
        entry_content_hash="content-hash-v3",
        pipeline_version="observatory-v3",
        status="completed",
        relevance_score=85,
        relevance_status="relevant",
        reason="V3 grounded reason",
        summary="V3 grounded summary",
        key_points=["Point 1 grounded"],
    )
    db_session.add(v3_analysis)
    db_session.flush()

    records = db_session.query(EntryAnalysis).filter(EntryAnalysis.entry_id == entry.id).all()
    assert len(records) == 2
    assert {r.pipeline_version for r in records} == {"observatory-v2", "observatory-v3"}
    assert v2_analysis.id != v3_analysis.id


def test_api_endpoint_returns_grounding_evidence_for_v3_and_none_for_v2(db_session: Session, client: TestClient):
    """GET /api/v1/entry-analyses/{id} populates grounding_evidence for v3, None for v2."""
    src = make_source(db_session)
    entry = make_entry(db_session, src)
    matrix, topics = make_matrix(db_session, "api-endpoint")
    triage_pv, deep_pv = make_v3_prompts(db_session)

    v2_analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="hash-v2",
        entry_content_hash="content-hash-v2",
        pipeline_version="observatory-v2",
        status="completed",
        relevance_score=70,
        relevance_status="relevant",
        reason="v2 reason",
    )
    db_session.add(v2_analysis)
    db_session.flush()

    call_v2 = AnalysisCall(
        entry_analysis_id=v2_analysis.id,
        stage="triage",
        provider="gemini_api",
        model="gemini-3.8-flash",
        prompt_version_id=triage_pv.id,
        status="completed",
        raw_response={"result": {"score": 70}},
    )
    db_session.add(call_v2)

    v3_analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="hash-v3",
        entry_content_hash="content-hash-v3",
        pipeline_version="observatory-v3",
        status="completed",
        relevance_score=90,
        relevance_status="relevant",
        reason="v3 reason",
    )
    db_session.add(v3_analysis)
    db_session.flush()

    call_v3_triage = AnalysisCall(
        entry_analysis_id=v3_analysis.id,
        stage="triage",
        provider="gemini_api",
        model="gemini-3.8-flash",
        prompt_version_id=triage_pv.id,
        status="completed",
        raw_response={
            "result": {
                "relevance_score": 90,
                "relevance_status": "relevant",
                "evidence": [{"source_field": "title", "quote": "Commission fines cartel"}],
            }
        },
    )
    call_v3_deep = AnalysisCall(
        entry_analysis_id=v3_analysis.id,
        stage="deep_analysis",
        provider="gemini_api",
        model="gemini-3.8-flash",
        prompt_version_id=deep_pv.id,
        status="completed",
        raw_response={
            "result": {
                "summary": "Summary text",
                "summary_evidence": [{"source_field": "content", "quote": "chemical products"}],
                "key_points": [
                    {
                        "point": "Point 1",
                        "evidence": [{"source_field": "title", "quote": "Commission fines cartel"}],
                    }
                ],
            }
        },
    )
    db_session.add(call_v3_triage)
    db_session.add(call_v3_deep)
    db_session.commit()

    res_v2 = client.get(f"/api/v1/entry-analyses/{v2_analysis.id}")
    assert res_v2.status_code == 200
    data_v2 = res_v2.json()
    assert data_v2["pipeline_version"] == "observatory-v2"
    assert data_v2["grounding_evidence"] is None

    res_v3 = client.get(f"/api/v1/entry-analyses/{v3_analysis.id}")
    assert res_v3.status_code == 200
    data_v3 = res_v3.json()
    assert data_v3["pipeline_version"] == "observatory-v3"
    assert data_v3["grounding_evidence"] is not None
    assert len(data_v3["grounding_evidence"]["triage_evidence"]) == 1
    assert data_v3["grounding_evidence"]["triage_evidence"][0]["quote"] == "Commission fines cartel"
    assert len(data_v3["grounding_evidence"]["summary_evidence"]) == 1
    assert len(data_v3["grounding_evidence"]["key_points_evidence"]) == 1


# ==============================================================================
# Bloque 7E.1: Grounding Input-Aware Field Availability Tests
# ==============================================================================

def test_grounding_input_aware_content_present_rejects_excerpt(db_session: Session):
    """Test A: When entry.content is present, model input uses content but NOT excerpt.

    Evidence with source_field='excerpt' must be REJECTED even if the quote is in entry.excerpt.
    """
    src = make_source(db_session, "FieldAvailA")
    entry = make_entry(
        db_session,
        src,
        title="Valid Title",
        content="Full text of the antitrust decision with secret cartel evidence.",
        excerpt="An excerpt containing unique excerpt text only.",
    )

    # Valid quote in content
    ev_content = GroundingEvidence(source_field="content", quote="secret cartel evidence")
    assert validate_grounding_quote(ev_content, entry) is True

    # Quote from excerpt: must be REJECTED because excerpt was not supplied to model
    ev_excerpt = GroundingEvidence(source_field="excerpt", quote="unique excerpt text only")
    with pytest.raises(AnalysisGroundingError) as exc_info:
        validate_grounding_quote(ev_excerpt, entry)
    assert "which was not present in the model input for this entry" in str(exc_info.value)
    assert "Allowed source fields: ['content', 'title']" in str(exc_info.value)


def test_grounding_input_aware_content_absent_allows_excerpt(db_session: Session):
    """Test B: When entry.content is absent/empty, model input uses excerpt.

    Evidence with source_field='excerpt' must be VALID.
    Evidence with source_field='content' must be REJECTED.
    """
    src = make_source(db_session, "FieldAvailB")
    entry = make_entry(
        db_session,
        src,
        title="Valid Title Without Content",
        content="",
        excerpt="The official summary describes the merger remedies accepted by CNMC.",
    )

    # Excerpt quote is valid
    ev_excerpt = GroundingEvidence(source_field="excerpt", quote="merger remedies accepted by CNMC")
    assert validate_grounding_quote(ev_excerpt, entry) is True

    # Content quote is rejected because content was not provided
    ev_content = GroundingEvidence(source_field="content", quote="merger remedies")
    with pytest.raises(AnalysisGroundingError) as exc_info:
        validate_grounding_quote(ev_content, entry)
    assert "which was not present in the model input for this entry" in str(exc_info.value)
    assert "Allowed source fields: ['excerpt', 'title']" in str(exc_info.value)


def test_grounding_input_aware_title_always_valid(db_session: Session):
    """Test C: Title is always sent to model in both triage and deep prompts.

    Evidence with source_field='title' matching the title must be VALID.
    """
    src = make_source(db_session, "FieldAvailC")
    entry = make_entry(
        db_session,
        src,
        title="Antitrust Authority fines Google €100M",
        content="Substantive decision text.",
    )

    ev_title = GroundingEvidence(source_field="title", quote="Antitrust Authority fines Google")
    assert validate_grounding_quote(ev_title, entry) is True


def test_grounding_input_aware_raw_metadata_rejected(db_session: Session):
    """Test D: raw_metadata is NOT an allowed evidence field for legal grounding.

    Evidence specifying source_field='raw_metadata' must be REJECTED.
    """
    src = make_source(db_session, "FieldAvailD")
    entry = make_entry(
        db_session,
        src,
        title="Some Title",
        content="Some Content",
    )

    ev_meta = GroundingEvidence(source_field="raw_metadata", quote="Some Metadata Quote")
    with pytest.raises(AnalysisGroundingError) as exc_info:
        validate_grounding_quote(ev_meta, entry)
    assert "which was not present in the model input for this entry" in str(exc_info.value)


def test_prompt_immutability_enforced_on_seed_conflict(db_session: Session):
    """Bloque 7E.1: Seed must reject material modification of existing prompt version.

    Attempting to seed an existing prompt with a different config (e.g. max_output_tokens)
    must raise PromptVersionImmutabilityError (ValueError) and leave DB row intact.
    """
    from scripts.seed_analysis_prompts import (
        PromptVersionImmutabilityError,
        PROMPT_DEFINITIONS,
        seed_analysis_prompts,
    )

    # 1. First ensure prompts are seeded
    seeded = seed_analysis_prompts(db=db_session)
    assert len(seeded) >= 6

    # 2. Find observatory_deep_analysis:v3 in DB
    existing_p = (
        db_session.query(AnalysisPromptVersion)
        .filter(AnalysisPromptVersion.code == "observatory_deep_analysis", AnalysisPromptVersion.version == 3)
        .first()
    )
    assert existing_p is not None
    original_config = dict(existing_p.config)

    # 3. Temporarily tamper with PROMPT_DEFINITIONS to simulate a developer trying to mutate v3 config
    deep_def_idx = next(
        i for i, p in enumerate(PROMPT_DEFINITIONS)
        if p["code"] == "observatory_deep_analysis" and p["version"] == 3
    )
    saved_def = dict(PROMPT_DEFINITIONS[deep_def_idx])
    tampered_def = dict(saved_def)
    tampered_def["config"] = {**saved_def["config"], "max_output_tokens": 9999}
    PROMPT_DEFINITIONS[deep_def_idx] = tampered_def

    try:
        with pytest.raises(PromptVersionImmutabilityError) as exc_info:
            seed_analysis_prompts(db=db_session)
        assert "Immutability conflict for prompt version 'observatory_deep_analysis:v3'" in str(exc_info.value)
        assert "Prompt versions are strictly immutable" in str(exc_info.value)
    finally:
        # Restore definition
        PROMPT_DEFINITIONS[deep_def_idx] = saved_def

    # 4. Verify DB row was NOT modified
    db_session.refresh(existing_p)
    assert existing_p.config == original_config


def test_v4_prompts_seeded_with_correct_configuration(db_session: Session):
    """Bloque 7F: Prompts v4 must be properly seeded with extract-first protocol and correct configs."""
    from scripts.seed_analysis_prompts import seed_analysis_prompts

    seeded = seed_analysis_prompts(db=db_session)
    assert len(seeded) >= 8

    triage_v4 = (
        db_session.query(AnalysisPromptVersion)
        .filter(AnalysisPromptVersion.code == "observatory_triage", AnalysisPromptVersion.version == 4)
        .first()
    )
    assert triage_v4 is not None
    assert triage_v4.stage == "triage"
    assert triage_v4.response_schema_version == "v3"
    assert triage_v4.active is True
    assert triage_v4.config == {
        "thinking_level": "low",
        "max_output_tokens": 1024,
        "temperature": 0.0,
        "structured_output_schema": "TriageAnalysisResultV3",
    }
    assert "EXTRACT-FIRST" in triage_v4.system_prompt
    assert "COPIA LITERAL VERBATIM AL 100%" in triage_v4.system_prompt
    assert "CLÁUSULAS CORTAS" in triage_v4.system_prompt

    deep_v4 = (
        db_session.query(AnalysisPromptVersion)
        .filter(AnalysisPromptVersion.code == "observatory_deep_analysis", AnalysisPromptVersion.version == 4)
        .first()
    )
    assert deep_v4 is not None
    assert deep_v4.stage == "deep_analysis"
    assert deep_v4.response_schema_version == "v3"
    assert deep_v4.active is True
    assert deep_v4.config == {
        "thinking_level": "medium",
        "max_output_tokens": 4096,
        "temperature": 0.0,
        "structured_output_schema": "DeepAnalysisResultV3",
    }
    assert "EXTRACT-FIRST" in deep_v4.system_prompt
    assert "COPIA LITERAL VERBATIM AL 100%" in deep_v4.system_prompt
    assert "CLÁUSULAS CORTAS" in deep_v4.system_prompt


def test_v4_prompt_immutability_enforced_on_seed_conflict(db_session: Session):
    """Bloque 7F: Prompt immutability guard must protect v4 prompts from tampering."""
    from scripts.seed_analysis_prompts import (
        PromptVersionImmutabilityError,
        PROMPT_DEFINITIONS,
        seed_analysis_prompts,
    )

    seed_analysis_prompts(db=db_session)
    deep_v4 = (
        db_session.query(AnalysisPromptVersion)
        .filter(AnalysisPromptVersion.code == "observatory_deep_analysis", AnalysisPromptVersion.version == 4)
        .first()
    )
    assert deep_v4 is not None
    original_config = dict(deep_v4.config)

    deep_def_idx = next(
        i for i, p in enumerate(PROMPT_DEFINITIONS)
        if p["code"] == "observatory_deep_analysis" and p["version"] == 4
    )
    saved_def = dict(PROMPT_DEFINITIONS[deep_def_idx])
    tampered_def = dict(saved_def)
    tampered_def["config"] = {**saved_def["config"], "thinking_level": "high"}
    PROMPT_DEFINITIONS[deep_def_idx] = tampered_def

    try:
        with pytest.raises(PromptVersionImmutabilityError) as exc_info:
            seed_analysis_prompts(db=db_session)
        assert "Immutability conflict for prompt version 'observatory_deep_analysis:v4'" in str(exc_info.value)
    finally:
        PROMPT_DEFINITIONS[deep_def_idx] = saved_def

    db_session.refresh(deep_v4)
    assert deep_v4.config == original_config


# ==============================================================================
# Bloque 14B.5: Hardened Grounding Tests (PDF Hyphenation & Trailing Ellipsis)
# ==============================================================================

def test_grounding_line_wrap_hyphenation_normalization():
    """Line-wrap hyphenation is resolved (gesetzli-\\nchen and -\\r\\n -> gesetzlichen)."""
    source_lf = "Vorliegen der gesetzli-\nchen Voraussetzungen"
    source_crlf = "Vorliegen der gesetzli-\r\nchen Voraussetzungen"
    quote = "Vorliegen der gesetzlichen Voraussetzungen"

    # Both source formats match normalized quote
    ev = GroundingEvidence(source_field="content", quote=quote)
    assert validate_grounding_quote(ev, Entry(title="T", content=source_lf)) is True
    assert validate_grounding_quote(ev, Entry(title="T", content=source_crlf)) is True


def test_grounding_intra_word_hyphen_preserved():
    """Intra-word hyphens on a single line are NOT collapsed (e.g. private-enforcement)."""
    source = "This directive promotes private-enforcement mechanisms across Member States."
    entry = Entry(title="T", content=source)

    ev_valid = GroundingEvidence(source_field="content", quote="promotes private-enforcement mechanisms")
    ev_collapsed = GroundingEvidence(source_field="content", quote="promotes privateenforcement mechanisms")

    assert validate_grounding_quote(ev_valid, entry) is True
    with pytest.raises(AnalysisGroundingError):
        validate_grounding_quote(ev_collapsed, entry)


def test_grounding_trailing_ellipsis_long_quote():
    """Trailing ellipsis ('...' or '…') on quotes with prefix >= 40 chars is safely stripped and passes."""
    source = "Personen, denen aus dem Verstoß ein Schaden entstanden ist, können diesen bei Vorliegen der Voraussetzungen geltend machen."
    entry = Entry(title="T", content=source)

    ev_dots = GroundingEvidence(source_field="content", quote="Personen, denen aus dem Verstoß ein Schaden entstanden ist...")
    ev_unicode = GroundingEvidence(source_field="content", quote="Personen, denen aus dem Verstoß ein Schaden entstanden ist…")

    assert validate_grounding_quote(ev_dots, entry) is True
    assert validate_grounding_quote(ev_unicode, entry) is True


def test_grounding_trailing_ellipsis_short_quote_fails():
    """Trailing ellipsis on short quotes (< 40 chars) is NOT stripped, preserving strict security guard."""
    source = "Personen, denen aus dem Verstoß ein Schaden entstanden ist."
    entry = Entry(title="T", content=source)
    ev_short = GroundingEvidence(source_field="content", quote="Personen, denen...")  # prefix is only 15 chars (< 40)

    with pytest.raises(AnalysisGroundingError):
        validate_grounding_quote(ev_short, entry)


def test_grounding_internal_ellipsis_not_stripped():
    """Internal ellipses in quotes are NOT stripped and must fail exact match if not in source."""
    source = "Personen, denen aus dem Verstoß ein Schaden entstanden ist, können diesen geltend machen."
    entry = Entry(title="T", content=source)
    ev_internal = GroundingEvidence(source_field="content", quote="Personen, denen ... können diesen geltend machen")

    with pytest.raises(AnalysisGroundingError):
        validate_grounding_quote(ev_internal, entry)


def test_grounding_real_bundeskartellamt_b12_21_23_fixture():
    """Real B12-21/23 fixture quote that failed in production due to line-wrap hyphenation and trailing ellipsis."""
    source_content = (
        "Fallbericht B12-21/23 - Zusammenfassung\n\n"
        "Personen, denen aus dem Verstoß ein Schaden entstanden ist,\n"
        "können diesen bei Vorliegen der gesetzli-\n"
        "chen Voraussetzungen vor den Zivilgerichten geltend machen.\n"
        "Hierbei können sie sich auf die Feststellungen der Entscheidung stützen."
    )
    entry = Entry(title="B12-21/23", content=source_content)
    # The exact quote Gemini produced
    gemini_quote = (
        "Personen, denen aus dem Verstoß ein Schaden entstanden ist, "
        "können diesen bei Vorliegen der gesetzlichen Voraussetzungen..."
    )
    ev = GroundingEvidence(source_field="content", quote=gemini_quote)

    # Must pass cleanly without error
    assert validate_grounding_quote(ev, entry) is True


def test_grounding_hallucinated_quote_fails():
    """Substantively altered or completely fabricated quotes must fail strict grounding."""
    source_content = (
        "Personen, denen aus dem Verstoß ein Schaden entstanden ist, "
        "können diesen bei Vorliegen der gesetzlichen Voraussetzungen vor den Zivilgerichten geltend machen."
    )
    entry = Entry(title="B12-21/23", content=source_content)
    hallucinated_quote = (
        "Personen haben keinen Anspruch auf Schadensersatz vor den Zivilgerichten."
    )
    ev = GroundingEvidence(source_field="content", quote=hallucinated_quote)

    with pytest.raises(AnalysisGroundingError) as exc_info:
        validate_grounding_quote(ev, entry)
    assert "verbatim quote not found" in str(exc_info.value)

