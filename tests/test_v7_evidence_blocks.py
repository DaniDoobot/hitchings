"""Tests for Bloque 15C.3 — Structural Grounding via Evidence Block IDs (v7)."""
import re
import uuid
import pytest
from sqlalchemy.orm import Session

from app.models.analysis import AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.schemas.analysis import (
    GroundingEvidence,
    AIAnalysisResponsePayload,
    KeyPointV3,
)
from app.services.grounding_validator import (
    AnalysisGroundingError,
    validate_grounding_quote,
    validate_triage_evidence,
    validate_deep_evidence,
)
from app.services.evidence_block_builder import (
    EvidenceBlock,
    EvidenceBlockSet,
    build_evidence_block_set,
    segment_text_into_spans,
    resolve_single_evidence_block,
    resolve_grounding_evidence_blocks,
)
from app.services.analysis_pipeline_service import AnalysisPipelineService
from app.providers.ai.mock import MockAIProvider
from scripts.seed_analysis_prompts import seed_analysis_prompts


@pytest.fixture
def sample_entry() -> Entry:
    """Fixture with a typical multi-paragraph antitrust document."""
    content = (
        "The Competition and Markets Authority (CMA) has published its Phase 1 decision. "
        "The inquiry investigated the anticipated acquisition by Parent Corp of Target Ltd. "
        "The Parties overlap in the commercial supply of specialised industrial equipment in the UK.\n\n"
        "The CMA found that the Merger does not give rise to a realistic prospect of a substantial "
        "lessening of competition (SLC) within any market or markets in the United Kingdom.\n\n"
        "Accordingly, the CMA has decided not to refer the Merger to a Phase 2 investigation."
    )
    return Entry(
        id=uuid.uuid4(),
        title="Anticipated acquisition by Parent Corp of Target Ltd",
        content=content,
        url="https://www.gov.uk/cma-cases/parent-target",
    )


# ==============================================================================
# A) DETERMINISMO
# ==============================================================================

def test_evidence_blocks_determinism(sample_entry: Entry):
    """Mismo source => mismos block IDs, spans y textos en ejecuciones sucesivas."""
    set1 = build_evidence_block_set(sample_entry)
    set2 = build_evidence_block_set(sample_entry)

    assert len(set1.blocks) == len(set2.blocks)
    for b1, b2 in zip(set1.blocks, set2.blocks):
        assert b1.id == b2.id
        assert b1.source_field == b2.source_field
        assert b1.start == b2.start
        assert b1.end == b2.end
        assert b1.text == b2.text


# ==============================================================================
# B) COBERTURA
# ==============================================================================

def test_evidence_blocks_coverage(sample_entry: Entry):
    """Concatenar los spans en orden debe preservar todo el contenido salvo whitespace exterior."""
    block_set = build_evidence_block_set(sample_entry)
    content = sample_entry.content or ""

    # Check non-whitespace preservation
    orig_non_ws = re.sub(r"\s+", "", content)
    spans_non_ws = "".join(re.sub(r"\s+", "", b.text) for b in block_set.content_blocks)
    assert orig_non_ws == spans_non_ws

    # Check title coverage
    title = sample_entry.title or ""
    title_non_ws = re.sub(r"\s+", "", title)
    blocks_title_non_ws = "".join(re.sub(r"\s+", "", b.text) for b in block_set.title_blocks)
    assert title_non_ws == blocks_title_non_ws


# ==============================================================================
# C) NO STITCHING
# ==============================================================================

def test_evidence_blocks_no_stitching(sample_entry: Entry):
    """Ningún bloque contiene texto de dos zonas no contiguas; cada bloque es un substring exacto."""
    block_set = build_evidence_block_set(sample_entry)
    content = sample_entry.content or ""

    for b in block_set.content_blocks:
        assert b.text == content[b.start:b.end]
        assert len(b.text) > 0


# ==============================================================================
# D) DANONE: RECONSTRUCTED QUOTE IMPOSSIBLE
# ==============================================================================

def test_evidence_blocks_danone_footnote_interruption():
    """En el caso Danone con notas al pie, ningún bloque puede contener conjuntamente

    'substantial lessening of' y 'competition (SLC)' cruzando las notas al pie.
    """
    danone_source = (
        "the CMA has found that the Merger would not give rise to a realistic prospect of a substantial lessening of\n\n"
        "35 FMN, paragraph 231. Third-party responses to the CMA market testing confirmed competitive constraints.\n"
        "36 See Document 1024, page 12.\n"
        "37 The parties submitted further economic analysis on market shares.\n"
        "38 Internal company presentations on RTD beverages.\n"
        "39 Retail sales data 2023-2025.\n"
        "40 Competitor capacity estimates.\n"
        "41 Supply agreements with co-manufacturers.\n"
        "42 In response to the CMA's questions...\n\n"
        "competition (SLC) as a result of horizontal unilateral effects in the supply of RTD protein drinks in the UK."
    )
    entry = Entry(id=uuid.uuid4(), title="Danone / Huel merger inquiry", content=danone_source)
    block_set = build_evidence_block_set(entry)

    # Verify no block contains both phrases
    for b in block_set.content_blocks:
        has_sl_of = "substantial lessening of" in b.text
        has_slc = "competition (SLC)" in b.text
        assert not (has_sl_of and has_slc), f"Block {b.id} illegally bridged footnotes!"

    # Verify the two distinct blocks exist
    before_block = next((b for b in block_set.content_blocks if "substantial lessening of" in b.text), None)
    after_block = next((b for b in block_set.content_blocks if "competition (SLC)" in b.text), None)
    assert before_block is not None
    assert after_block is not None
    assert before_block.id != after_block.id


# ==============================================================================
# E) VALID SELECTION: BLOCK ID HYDRATION & GROUNDING PASS
# ==============================================================================

def test_evidence_blocks_valid_selection_passes(sample_entry: Entry):
    """C0001 -> resuelve a quote exacta -> validate_grounding_quote PASS."""
    block_set = build_evidence_block_set(sample_entry)
    first_block = block_set.content_blocks[0]

    ev = GroundingEvidence(source_field="content", quote=first_block.id)
    resolve_single_evidence_block(ev, block_set)

    assert ev.quote == first_block.text
    assert validate_grounding_quote(ev, sample_entry) is True


# ==============================================================================
# F) INVALID ID: NONEXISTENT BLOCK ID FAILS
# ==============================================================================

def test_evidence_blocks_invalid_id_fails(sample_entry: Entry):
    """C9999 -> FAIL con AnalysisGroundingError."""
    block_set = build_evidence_block_set(sample_entry)

    ev = GroundingEvidence(source_field="content", quote="C9999")
    with pytest.raises(AnalysisGroundingError) as exc_info:
        resolve_single_evidence_block(ev, block_set)
    assert "not found in document evidence blocks" in str(exc_info.value)


# ==============================================================================
# G) FREE-FORM TEXT FAILS
# ==============================================================================

def test_evidence_blocks_free_form_text_fails(sample_entry: Entry):
    """Texto libre en modo evidence_blocks_v1 -> FAIL con AnalysisGroundingError."""
    block_set = build_evidence_block_set(sample_entry)

    ev = GroundingEvidence(source_field="content", quote="The CMA has published its Phase 1 decision.")
    with pytest.raises(AnalysisGroundingError) as exc_info:
        resolve_single_evidence_block(ev, block_set)
    assert "expected Evidence Block ID" in str(exc_info.value)


# ==============================================================================
# H) MULTIPLE IDS IN SINGLE QUOTE FAILS
# ==============================================================================

def test_evidence_blocks_multiple_ids_fails(sample_entry: Entry):
    """Múltiples IDs (ej. 'C0001 C0002' o 'C0001+C0002') -> FAIL con AnalysisGroundingError."""
    block_set = build_evidence_block_set(sample_entry)

    for invalid in ["C0001 C0002", "C0001+C0002", "C0001, C0002", "C0001; C0002"]:
        ev = GroundingEvidence(source_field="content", quote=invalid)
        with pytest.raises(AnalysisGroundingError) as exc_info:
            resolve_single_evidence_block(ev, block_set)
        assert "multiple block IDs" in str(exc_info.value) or "expected Evidence Block ID" in str(exc_info.value)


# ==============================================================================
# I) WRONG SOURCE_FIELD FAILS
# ==============================================================================

def test_evidence_blocks_wrong_source_field_fails(sample_entry: Entry):
    """Bloque C0001 con source_field='title' -> FAIL con AnalysisGroundingError."""
    block_set = build_evidence_block_set(sample_entry)
    first_block = block_set.content_blocks[0]

    ev = GroundingEvidence(source_field="title", quote=first_block.id)
    with pytest.raises(AnalysisGroundingError) as exc_info:
        resolve_single_evidence_block(ev, block_set)
    assert "mismatch" in str(exc_info.value)


# ==============================================================================
# J) EXISTING GROUNDING REGRESSIONS REMAIN VALID AFTER HYDRATION
# ==============================================================================

def test_evidence_blocks_existing_regressions_pass():
    """Bundeskartellamt gesetzli-\nchen, CMA B arriers, T here, [5-\n10]%, wholly-\nowned

    todos pasan validate_grounding_quote tras resolución de bloque.
    """
    cases = [
        ("Bundeskartellamt gesetzli-chen", "Ein Zusammenschluss ist nach den gesetzli-\nchen Vorschriften zu untersagen."),
        ("CMA B arriers", "The CMA considers that there are significant B arriers to entry and expansion."),
        ("CMA T here", "T here is no evidence of competitive pressure from adjacent markets."),
        ("CMA [5-10]%", "The Parties have a combined market share of [5-\n10]% in the market."),
        ("CMA wholly-owned", "Target is a wholly-\nowned subsidiary of Parent Corporation plc."),
    ]
    for label, text in cases:
        entry = Entry(id=uuid.uuid4(), title="Test", content=text)
        block_set = build_evidence_block_set(entry)
        assert len(block_set.content_blocks) >= 1

        for b in block_set.content_blocks:
            ev = GroundingEvidence(source_field="content", quote=b.id)
            resolve_single_evidence_block(ev, block_set)
            assert ev.quote == b.text
            assert validate_grounding_quote(ev, entry) is True, f"Failed on {label}"


# ==============================================================================
# K) TITLE BLOCK: T0001 RESOLVES AND PERSISTS REAL TITLE QUOTE
# ==============================================================================

def test_evidence_blocks_title_block(sample_entry: Entry):
    """T0001 resuelve a la cita real del título y pasa la validación de grounding."""
    block_set = build_evidence_block_set(sample_entry)
    assert len(block_set.title_blocks) >= 1

    ev = GroundingEvidence(source_field="title", quote="T0001")
    resolve_single_evidence_block(ev, block_set)

    assert ev.quote == block_set.title_blocks[0].text
    assert validate_grounding_quote(ev, sample_entry) is True


# ==============================================================================
# L) EXCERPT FALLBACK: E0001 WORKS WHEN CONTENT IS EMPTY
# ==============================================================================

def test_evidence_blocks_excerpt_fallback():
    """Si entry.content está vacío y excerpt presente, IDs Exxxx funcionan y validan contra excerpt."""
    entry = Entry(
        id=uuid.uuid4(),
        title="Summary publication",
        content=None,
        excerpt="The Authority has closed its investigation following commitments offered by the parties.",
    )
    block_set = build_evidence_block_set(entry)
    assert len(block_set.content_blocks) == 0
    assert len(block_set.excerpt_blocks) >= 1

    first_ex = block_set.excerpt_blocks[0]
    ev = GroundingEvidence(source_field="excerpt", quote=first_ex.id)
    resolve_single_evidence_block(ev, block_set)

    assert ev.quote == first_ex.text
    assert validate_grounding_quote(ev, entry) is True


# ==============================================================================
# PAYLOAD TRAVERSAL: TRIAGE AND DEEP RESOLUTION
# ==============================================================================

def test_resolve_grounding_evidence_blocks_triage_payload(sample_entry: Entry):
    """resolve_grounding_evidence_blocks hidrata payload.evidence en triage."""
    block_set = build_evidence_block_set(sample_entry)
    payload = AIAnalysisResponsePayload(
        relevance_score=85,
        confidence=0.9,
        evidence=[GroundingEvidence(source_field="content", quote="C0001")],
    )

    resolve_grounding_evidence_blocks(payload, block_set, stage="triage")

    assert payload.evidence[0].quote == block_set.content_blocks[0].text
    validate_triage_evidence(payload.evidence, sample_entry)


def test_resolve_grounding_evidence_blocks_deep_payload(sample_entry: Entry):
    """resolve_grounding_evidence_blocks hidrata summary_evidence y key_points[].evidence en deep."""
    block_set = build_evidence_block_set(sample_entry)
    payload = AIAnalysisResponsePayload(
        relevance_score=85,
        confidence=0.9,
        summary="Executive summary in Spanish.",
        summary_evidence=[GroundingEvidence(source_field="content", quote="C0001")],
        key_points=["Point 1"],
        key_point_items=[
            KeyPointV3(point="Point 1", evidence=[GroundingEvidence(source_field="title", quote="T0001")])
        ],
    )

    resolve_grounding_evidence_blocks(payload, block_set, stage="deep_analysis")

    assert payload.summary_evidence[0].quote == block_set.content_blocks[0].text
    assert payload.key_point_items[0].evidence[0].quote == block_set.title_blocks[0].text
    validate_deep_evidence(payload.summary_evidence, payload.key_point_items, sample_entry)


# ==============================================================================
# PIPELINE INTEGRATION WITH V7 PROMPTS (MOCK PROVIDER)
# ==============================================================================

@pytest.mark.asyncio
async def test_pipeline_v7_with_mock_provider(db_session: Session, sample_entry: Entry):
    """Pipeline completo con prompts v7 activos: Mock provider genera Block IDs,

    AnalysisPipelineService los hidrata a texto real y persiste en DB.
    """
    seed_analysis_prompts(db=db_session)

    # Retrieve active v7 prompts
    triage_v7 = db_session.query(AnalysisPromptVersion).filter(
        AnalysisPromptVersion.code == "observatory_triage",
        AnalysisPromptVersion.version == 7,
        AnalysisPromptVersion.active.is_(True),
    ).first()
    deep_v7 = db_session.query(AnalysisPromptVersion).filter(
        AnalysisPromptVersion.code == "observatory_deep_analysis",
        AnalysisPromptVersion.version == 7,
        AnalysisPromptVersion.active.is_(True),
    ).first()
    assert triage_v7 is not None
    assert deep_v7 is not None

    # Setup source and matrix
    source = Source(
        id=uuid.uuid4(),
        name="CMA UK",
        type=SourceType.INSTITUTIONAL,
        url="https://www.gov.uk/cma-cases",
    )
    db_session.add(source)
    sample_entry.source_id = source.id

    matrix = TrackingMatrix(
        id=uuid.uuid4(),
        code=f"HITCHINGS-V7-{uuid.uuid4().hex[:8]}",
        name="HITCHINGS Matrix v0.1",
        status="active",
        relevance_instructions="Antitrust and merger control relevance.",
        exclusion_instructions="Exclude unrelated criminal matters.",
    )
    topic = TrackingTopic(
        id=uuid.uuid4(),
        matrix_id=matrix.id,
        code="COMP_MERGERS",
        name="Merger Control",
        active=True,
        keywords=["merger", "acquisition", "phase 1"],
    )
    matrix.topics.append(topic)
    db_session.add(matrix)
    db_session.add(sample_entry)
    db_session.commit()

    # Run pipeline with MockAIProvider
    mock_provider = MockAIProvider(fixed_score=90)
    pipeline = AnalysisPipelineService(provider=mock_provider)

    analysis = await pipeline.run_pipeline(
        entry_id=sample_entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=triage_v7.id,
        deep_prompt_id=deep_v7.id,
        db=db_session,
        pipeline_version="v7",
    )

    assert analysis.status == "completed"
    assert analysis.relevance_status == "relevant"
    assert analysis.relevance_score == 90

    # Verify calls
    calls = analysis.calls
    assert len(calls) == 2
    for call in calls:
        assert call.status == "completed"
        assert call.error_type is None
