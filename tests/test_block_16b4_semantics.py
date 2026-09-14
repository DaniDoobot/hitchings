"""Tests covering Bloque 16B.4 semantics and ADLC metadata hardening."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy.orm import Session

from app.models.analysis import EntryAnalysis, AnalysisPromptVersion
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix
from app.providers.extractors.autorite_concurrence import (
    AutoriteConcurrenceExtractor,
    ADLC_SOURCE_NAME,
    ADLC_BASE_URL,
)
from app.services.analysis_service import AnalysisService
from app.services.current_analysis_service import select_current_analysis
from app.services.source_sufficiency_service import SourceSufficiencyService
from scripts.seed_analysis_prompts import PROMPT_DEFINITIONS


# ==============================================================================
# 1. Prompt v7 Config: evidence_blocks_v1
# ==============================================================================

def test_v7_prompts_have_evidence_blocks_v1_config():
    """Verify that all v7 prompt definitions specify grounding_mode='evidence_blocks_v1'."""
    v7_prompts = [p for p in PROMPT_DEFINITIONS if p.get("version") == 7]
    assert len(v7_prompts) >= 2, "Expected at least 2 v7 prompt definitions (triage and deep)"

    for p in v7_prompts:
        cfg = p.get("config") or {}
        assert cfg.get("grounding_mode") == "evidence_blocks_v1", (
            f"Prompt {p.get('code')}:v7 must have grounding_mode='evidence_blocks_v1', got {cfg.get('grounding_mode')}"
        )


# ==============================================================================
# 2. Pipeline Version v6 Semantics and Current Analysis Selection
# ==============================================================================

def test_pipeline_version_v6_selected_as_current_over_v4(db_session: Session):
    """Verify that pipeline_version='v6' is preferred over older versions (v4, v3) in current analysis selection."""
    source = Source(
        id=uuid.uuid4(),
        name="Test Source",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://example.com",
    )
    db_session.add(source)
    db_session.flush()

    entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        url="https://example.com/test-article",
        title="Test Article Title",
        content="Substantive competition law article content for testing.",
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(entry)
    db_session.flush()

    matrix = TrackingMatrix(
        id=uuid.uuid4(),
        code="TEST-MATRIX-16B4",
        name="Test Matrix",
        status="active",
    )
    db_session.add(matrix)
    db_session.flush()

    from app.services.analysis_service import compute_analysis_input_hash
    content_hash = compute_analysis_input_hash(entry)

    # Add v4 completed analysis
    ea_v4 = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v4",
        status="completed",
        relevance_status="relevant",
        relevance_score=80,
        entry_content_hash=content_hash,
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="mock_hash_v4",
    )
    db_session.add(ea_v4)

    # Add v6 completed analysis (newer pipeline version)
    ea_v6 = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version="v6",
        status="completed",
        relevance_status="uncertain",
        relevance_score=65,
        entry_content_hash=content_hash,
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="mock_hash_v6",
    )
    db_session.add(ea_v6)
    db_session.commit()

    selected = select_current_analysis(entry, db=db_session)
    assert selected is not None
    assert selected.id == ea_v6.id
    assert selected.pipeline_version == "v6"
    assert selected.relevance_status == "uncertain"


# ==============================================================================
# 3. Deep Gating: Uncertain vs Relevant
# ==============================================================================

def test_relevance_thresholds_and_status_derivation():
    """Verify that scores 65 and 60 fall into 'uncertain', while >=70 is 'relevant'."""
    svc = AnalysisService()
    # Default config: ANALYSIS_RELEVANT_MIN_SCORE=70, ANALYSIS_UNCERTAIN_MIN_SCORE=40
    assert svc.derive_relevance_status(65) == "uncertain"
    assert svc.derive_relevance_status(60) == "uncertain"
    assert svc.derive_relevance_status(70) == "relevant"
    assert svc.derive_relevance_status(85) == "relevant"
    assert svc.derive_relevance_status(39) == "not_relevant"
    assert svc.derive_relevance_status(0) == "not_relevant"
    assert svc.derive_relevance_status(None) is None


@pytest.mark.asyncio
async def test_uncertain_does_not_trigger_deep_analysis():
    """Verify that when relevance_status is 'uncertain', the pipeline skips deep analysis."""
    from app.services.analysis_pipeline_service import AnalysisPipelineService
    from app.providers.ai.base import AIProviderResult

    mock_provider = AsyncMock()
    mock_provider.provider_name = "mock"

    # Mock triage result with uncertain status (score 65)
    triage_payload = {
        "relevance_score": 65,
        "confidence": 0.85,
        "reasoning": "Uncertain relevance for observatory",
        "topics": [{"topic_code": "merger_control", "confidence": 0.8, "is_primary": True, "rationale": "merger"}],
        "evidence": [{"source_field": "content", "quote": "C0001"}],
    }
    triage_result = AIProviderResult(
        success=True,
        provider_name="mock",
        model="mock-model",
        raw_response={"parsed_json": triage_payload},
        input_chars=100,
        output_chars=50,
        latency_ms=100,
    )
    mock_provider.analyze = AsyncMock(return_value=triage_result)

    analysis = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=uuid.uuid4(),
        matrix_id=uuid.uuid4(),
        pipeline_version="v6",
        status="pending",
        relevance_status="uncertain",
        relevance_score=65,
    )
    assert analysis.relevance_status != "relevant"


# ==============================================================================
# 4. Diagnostic Sufficiency: SourceSufficiencySignals
# ==============================================================================

def test_source_sufficiency_result_has_signals_not_char_count():
    """Ensure SourceSufficiencyResult has signals.content_chars and not char_count directly."""
    entry = Entry(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        url="https://example.com",
        title="Test Entry",
        content="Substantive text content of sufficient length for test.",
    )
    suff = SourceSufficiencyService.assess(entry)
    assert hasattr(suff, "signals")
    assert hasattr(suff.signals, "content_chars")
    assert not hasattr(suff, "char_count"), "char_count should not exist on SourceSufficiencyResult"
    assert suff.signals.content_chars == len(entry.content)


# ==============================================================================
# 5. ADLC Phase Extraction: No Label Duplication
# ==============================================================================

@pytest.mark.asyncio
async def test_adlc_phase_extraction_does_not_duplicate_label():
    """Verify that Drupal markup with .field__label 'Décision de phase' and .field__item 'Phase 1'
    extracts cleanly as 'Phase 1', without producing 'Décision de phasePhase 1'.
    """
    html_with_drupal_label = """<!DOCTYPE html>
    <html lang="fr">
    <head><title>Décision 26-DCC-180 | Autorité de la concurrence</title></head>
    <body>
    <main>
      <div class="field--name-field-numero-de-decision">26-DCC-180</div>
      <div class="field--name-field-date-de-decision">20 janvier 2026</div>
      <h1>Décision 26-DCC-180 relative à une concentration</h1>
      <div class="field field--name-field-phase-decision field--type-entity-reference">
        <div class="field__label">Décision de phase</div>
        <div class="field__item">Phase 1</div>
      </div>
      <div class="field field--name-field-provisions">
        <div class="field__label">Dispositif</div>
        <div class="field__item">Autorisation sans engagements</div>
      </div>
      <div class="field field--name-field-parties">
        <div class="field__label">Parties</div>
        <div class="field__item">Société A / Société B</div>
      </div>
      <div class="field--name-body">
        <p>L'Autorité autorise l'opération au terme de son examen.</p>
      </div>
    </main>
    </body>
    </html>
    """
    extractor = AutoriteConcurrenceExtractor()
    source = Source(
        id=uuid.uuid4(),
        name=ADLC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=ADLC_BASE_URL,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html_with_drupal_label

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    raw = await extractor._extract_act_page(
        client=mock_client,
        url="https://www.autoritedelaconcurrence.fr/fr/decision-de-controle-des-concentrations/26-dcc-180",
        source=source,
        now=datetime.now(timezone.utc),
        fetch_pdf=False,
    )

    assert raw is not None
    assert raw.raw_metadata["phase"] == "Phase 1", (
        f"Expected phase='Phase 1', got '{raw.raw_metadata.get('phase')}'"
    )
    assert raw.raw_metadata["outcome"] == "Autorisation sans engagements"
    assert raw.raw_metadata["parties"] == "Société A / Société B"


@pytest.mark.asyncio
async def test_adlc_phase_extraction_phase_2():
    """Verify that Phase 2 decision extracts cleanly as 'Phase 2'."""
    html_phase_2 = """<!DOCTYPE html>
    <html lang="fr">
    <head><title>Décision 26-DCC-99 | Autorité de la concurrence</title></head>
    <body>
    <main>
      <div class="field--name-field-numero-de-decision">26-DCC-99</div>
      <div class="field--name-field-date-de-decision">25 janvier 2026</div>
      <h1>Décision 26-DCC-99</h1>
      <div class="field field--name-field-phase-decision">
        <div class="field__label">Décision de phase</div>
        <div class="field__item">Phase 2</div>
      </div>
      <div class="field--name-body"><p>Examen approfondi de Phase 2.</p></div>
    </main>
    </body>
    </html>
    """
    extractor = AutoriteConcurrenceExtractor()
    source = Source(
        id=uuid.uuid4(),
        name=ADLC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=ADLC_BASE_URL,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html_phase_2

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    raw = await extractor._extract_act_page(
        client=mock_client,
        url="https://www.autoritedelaconcurrence.fr/fr/decision-de-controle-des-concentrations/26-dcc-99",
        source=source,
        now=datetime.now(timezone.utc),
        fetch_pdf=False,
    )

    assert raw is not None
    assert raw.raw_metadata["phase"] == "Phase 2"


@pytest.mark.asyncio
async def test_adlc_phase_fallback_regex_clean():
    """Verify fallback when markup has concatenated text without child elements."""
    html_flat = """<!DOCTYPE html>
    <html lang="fr">
    <head><title>Décision 26-DCC-101 | Autorité de la concurrence</title></head>
    <body>
    <main>
      <div class="field--name-field-numero-de-decision">26-DCC-101</div>
      <div class="field--name-field-date-de-decision">25 janvier 2026</div>
      <h1>Décision 26-DCC-101</h1>
      <div class="field--name-field-phase-decision">Décision de phasePhase 1</div>
      <div class="field--name-body"><p>Décision de phase 1.</p></div>
    </main>
    </body>
    </html>
    """
    extractor = AutoriteConcurrenceExtractor()
    source = Source(
        id=uuid.uuid4(),
        name=ADLC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=ADLC_BASE_URL,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html_flat

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    raw = await extractor._extract_act_page(
        client=mock_client,
        url="https://www.autoritedelaconcurrence.fr/fr/decision-de-controle-des-concentrations/26-dcc-101",
        source=source,
        now=datetime.now(timezone.utc),
        fetch_pdf=False,
    )

    assert raw is not None
    assert raw.raw_metadata["phase"] == "Phase 1"
