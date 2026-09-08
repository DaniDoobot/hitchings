"""Unit tests for SourceSufficiencyService and CatEnrichmentService.

Bloque 7D: Verifies deterministic source sufficiency assessment,
selective CAT PDF enrichment, idempotency, and provenance preservation.
"""

import io
import uuid
import pytest
import pypdf
from datetime import datetime, timezone

from app.models.entry import Entry
from app.models.source import Source
from app.models.analysis import EntryAnalysis
from app.services.source_sufficiency_service import (
    SourceSufficiencyLevel,
    SourceSufficiencyService,
    assess_source_sufficiency,
)
from app.services.cat_enrichment_service import CatEnrichmentService
from app.services.ingestion_service import compute_ingestion_dedupe_hash
from app.services.analysis_service import compute_analysis_input_hash


@pytest.fixture
def make_test_source():
    def _source(name: str):
        return Source(
            id=uuid.uuid4(),
            name=name,
            type="rss",
            url="https://example.com/feed",
            active=True,
        )
    return _source


def test_curia_full_sufficiency(make_test_source):
    source = make_test_source("Court of Justice of the European Union - Case Law")
    entry = Entry(
        id=uuid.uuid4(),
        source=source,
        url="https://curia.europa.eu/juris/document/123",
        title="Case C-60/25 [Livronsa] | Judgment",
        content="JUDGMENT OF THE COURT ... " * 200,
        raw_metadata={"full_text_available": True, "content_source": "infocuria_html"},
    )
    res = assess_source_sufficiency(entry)
    assert res.level == SourceSufficiencyLevel.FULL
    assert res.signals.full_text_available is True
    assert res.signals.substantive_content is True


def test_cnmc_full_sufficiency(make_test_source):
    source = make_test_source("CNMC - Noticias")
    entry = Entry(
        id=uuid.uuid4(),
        source=source,
        url="https://www.cnmc.es/noticias/test",
        title="La CNMC autoriza con compromisos la concentración",
        content="La Comisión Nacional de los Mercados y la Competencia ha autorizado..." * 20,
    )
    res = assess_source_sufficiency(entry)
    assert res.level == SourceSufficiencyLevel.FULL
    assert res.signals.content_chars > 300


def test_ec_full_sufficiency(make_test_source):
    source = make_test_source("European Commission - Competition Policy")
    entry = Entry(
        id=uuid.uuid4(),
        source=source,
        url="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_123",
        title="Commission sends Statement of Objections",
        content="The European Commission has sent a Statement of Objections..." * 20,
        raw_metadata={"publication_date_source": "presscorner_api"},
    )
    res = assess_source_sufficiency(entry)
    assert res.level == SourceSufficiencyLevel.FULL


def test_cat_substantive_summary_partial(make_test_source):
    source = make_test_source("Competition Appeal Tribunal - Judgments")
    entry = Entry(
        id=uuid.uuid4(),
        source=source,
        url="https://www.catribunal.org.uk/judgments/123",
        title="[2026] CAT 65 | Or Brook v Google - Judgment (Certification)",
        content="Judgment of the Tribunal further to an application for certification as class representative by Or Brook...",
        raw_metadata={"has_summary": True, "judgment_pdf_url": "https://example.com/cat65.pdf"},
    )
    res = assess_source_sufficiency(entry)
    assert res.level == SourceSufficiencyLevel.PARTIAL
    assert res.signals.substantive_content is True
    assert res.signals.official_summary_available is True


def test_cat_short_substantive_partial(make_test_source):
    source = make_test_source("Competition Appeal Tribunal - Judgments")
    entry = Entry(
        id=uuid.uuid4(),
        source=source,
        url="https://www.catribunal.org.uk/judgments/124",
        title="[2026] CAT 68 | Merchant Interchange Fee - Ruling (Cut-off Application)",
        content="A ruling of the President setting a cut-off date of 23 October 2026 for new claimants to apply to become Host Cases under the Umbrella Proceedings Order for the purposes of Trial 3.",
        raw_metadata={"has_summary": True, "judgment_pdf_url": "https://example.com/cat68.pdf"},
    )
    res = assess_source_sufficiency(entry)
    assert res.level == SourceSufficiencyLevel.PARTIAL
    assert res.signals.substantive_content is True


def test_cat_generic_one_line_insufficient(make_test_source):
    source = make_test_source("Competition Appeal Tribunal - Judgments")
    entry = Entry(
        id=uuid.uuid4(),
        source=source,
        url="https://www.catribunal.org.uk/judgments/125",
        title="[2026] CAT 67 | GLOBAL-365 plc v PayPoint plc - Ruling (Costs)",
        content="Ruling of the Tribunal on costs.",
        raw_metadata={"has_summary": True, "judgment_pdf_url": "https://example.com/cat67.pdf"},
    )
    res = assess_source_sufficiency(entry)
    assert res.level == SourceSufficiencyLevel.INSUFFICIENT
    assert "Texto genérico" in res.reason
    assert res.signals.substantive_content is False


def test_cat_empty_content_insufficient(make_test_source):
    source = make_test_source("Competition Appeal Tribunal - Judgments")
    entry = Entry(
        id=uuid.uuid4(),
        source=source,
        url="https://www.catribunal.org.uk/judgments/126",
        title="[2026] EWCA Civ 993 | Judgment of the Court of Appeal",
        content=None,
        raw_metadata={"has_summary": False, "judgment_pdf_url": "https://example.com/ewca.pdf"},
    )
    res = assess_source_sufficiency(entry)
    assert res.level == SourceSufficiencyLevel.INSUFFICIENT
    assert res.signals.content_chars == 0


def test_cat_enriched_pdf_full(make_test_source):
    source = make_test_source("Competition Appeal Tribunal - Judgments")
    entry = Entry(
        id=uuid.uuid4(),
        source=source,
        url="https://www.catribunal.org.uk/judgments/125",
        title="[2026] CAT 67 | GLOBAL-365 plc v PayPoint plc - Ruling (Costs)",
        content="Neutral citation [2026] CAT 67 ... Full judgment text ...",
        raw_metadata={
            "content_source": "cat_judgment_pdf_text",
            "full_text_available": True,
            "pdf_text_extracted": True,
        },
    )
    res = assess_source_sufficiency(entry)
    assert res.level == SourceSufficiencyLevel.FULL
    assert res.signals.full_text_available is True


def test_cat_enrichment_clean_pdf_text():
    service = CatEnrichmentService()
    raw = "Line 1   with   spaces\r\n\r\n\r\nLine 2\xa0with\u200b non-breaking\n\n\n\n\nLine 3"
    cleaned = service.clean_pdf_text(raw)
    assert "Line 1 with spaces" in cleaned
    assert "Line 2 with non-breaking" in cleaned
    assert "\n\n\n" not in cleaned


def test_cat_enrichment_validate_identity(make_test_source):
    service = CatEnrichmentService()
    source = make_test_source("Competition Appeal Tribunal - Judgments")
    entry = Entry(
        id=uuid.uuid4(),
        source=source,
        url="https://example.com/123",
        title="[2026] CAT 67 | GLOBAL-365 plc v PayPoint plc - Ruling (Costs)",
        raw_metadata={
            "neutral_citation": "[2026] CAT 67",
            "case_numbers": ["1597/5/7/23"],
            "case_names": ["GLOBAL-365 plc v PayPoint plc"],
        },
    )

    matching_text = "IN THE COMPETITION APPEAL TRIBUNAL Neutral citation [2026] CAT 67 Case No: 1597/5/7/23 GLOBAL-365 PLC v PAYPOINT PLC"
    assert service.validate_identity(entry, matching_text) is True

    mismatched_text = "IN THE COMPETITION APPEAL TRIBUNAL Neutral citation [2026] CAT 99 Case No: 9999/9/9/99 Completely Different Parties"
    assert service.validate_identity(entry, mismatched_text) is False


def test_cat_enrichment_mock_pdf_and_idempotency(db_session, make_test_source, monkeypatch):
    source = make_test_source("Competition Appeal Tribunal - Judgments")
    db_session.add(source)
    db_session.flush()

    entry = Entry(
        source_id=source.id,
        url="https://example.com/cat67",
        title="[2026] CAT 67 | GLOBAL-365 plc v PayPoint plc - Ruling (Costs)",
        content="Ruling of the Tribunal on costs.",
        raw_metadata={
            "judgment_pdf_url": "https://example.com/cat67.pdf",
            "neutral_citation": "[2026] CAT 67",
            "case_numbers": ["1597/5/7/23"],
        },
    )
    db_session.add(entry)
    db_session.flush()

    # Create dummy PDF bytes with pypdf
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = io.BytesIO()
    writer.write(stream)
    dummy_pdf_bytes = stream.getvalue()

    service = CatEnrichmentService()

    # Mock extract_text_from_pdf to return valid matching text
    def mock_extract(pdf_bytes):
        return (
            "Neutral citation [2026] CAT 67 Case No: 1597/5/7/23 GLOBAL-365 PLC v PAYPOINT PLC. "
            "The Tribunal rules that the payment of costs is to be made within 28 days.",
            1,
        )

    monkeypatch.setattr(service, "extract_text_from_pdf", mock_extract)

    class MockResponse:
        status_code = 200
        content = dummy_pdf_bytes

    class MockClient:
        def get(self, *args, **kwargs):
            return MockResponse()

    # Initial enrichment
    success, details = service.enrich_entry(entry, db_session, client=MockClient())
    assert success is True
    assert entry.raw_metadata["content_source"] == "cat_judgment_pdf_text"
    assert entry.raw_metadata["full_text_available"] is True
    assert entry.raw_metadata["previous_content"] == "Ruling of the Tribunal on costs."
    assert "costs is to be made within 28 days" in entry.content

    # Idempotent second call: must skip
    success_2, details_2 = service.enrich_entry(entry, db_session, client=MockClient())
    assert success_2 is False
    assert "idempotent" in details_2["reason"].lower()


def test_cat_enrichment_failure_leaves_entry_intact(db_session, make_test_source):
    source = make_test_source("Competition Appeal Tribunal - Judgments")
    db_session.add(source)
    db_session.flush()

    original_content = "Ruling of the Tribunal on costs."
    entry = Entry(
        source_id=source.id,
        url="https://example.com/cat67",
        title="[2026] CAT 67 | GLOBAL-365 plc v PayPoint plc - Ruling (Costs)",
        content=original_content,
        raw_metadata={"judgment_pdf_url": "https://example.com/not_found.pdf"},
    )
    db_session.add(entry)
    db_session.flush()

    service = CatEnrichmentService()

    class Mock404Response:
        status_code = 404
        content = b"Not found"

    class MockClient:
        def get(self, *args, **kwargs):
            return Mock404Response()

    success, details = service.enrich_entry(entry, db_session, client=MockClient())
    assert success is False
    assert entry.content == original_content
    assert entry.raw_metadata.get("content_source") != "cat_judgment_pdf_text"


def test_historical_analysis_hash_preserved_and_stale_detected(db_session, make_test_source):
    source = make_test_source("Competition Appeal Tribunal - Judgments")
    db_session.add(source)
    db_session.flush()

    initial_content = "Ruling of the Tribunal on costs."
    title = "[2026] CAT 67 | GLOBAL-365 plc v PayPoint plc - Ruling (Costs)"
    url = "https://example.com/cat67"
    excerpt = "Short excerpt"

    ingestion_hash = compute_ingestion_dedupe_hash(title, url, excerpt)
    entry = Entry(
        source_id=source.id,
        url=url,
        title=title,
        excerpt=excerpt,
        content=initial_content,
        content_hash=ingestion_hash,
    )
    db_session.add(entry)
    db_session.flush()

    # Initial analysis hash is computed from title + content at analysis time
    initial_analysis_hash = compute_analysis_input_hash(entry)

    analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=uuid.uuid4(),
        matrix_snapshot={"name": "Snapshot"},
        matrix_snapshot_hash="matrix_hash_1",
        entry_content_hash=initial_analysis_hash,
        pipeline_version="v2",
        status="completed",
        relevance_score=75,
        relevance_status="relevant",
    )
    db_session.add(analysis)
    db_session.commit()

    # Initially, entry is NOT stale: current analysis input hash matches analysis record
    assert compute_analysis_input_hash(entry) == analysis.entry_content_hash

    # Simulate enrichment modifying ONLY entry.content (PDF text layer)
    # Entry.content_hash MUST NOT change because URL, title, excerpt are unchanged!
    entry.content = "New rich full text from judgment PDF containing 15,000 characters..."
    db_session.commit()

    # Ingestion deduplication hash is strictly preserved
    assert entry.content_hash == ingestion_hash

    # Historical EntryAnalysis.entry_content_hash did NOT change!
    db_session.refresh(analysis)
    assert analysis.entry_content_hash == initial_analysis_hash

    # Current analysis input hash has changed with the new content
    new_analysis_hash = compute_analysis_input_hash(entry)
    assert new_analysis_hash != initial_analysis_hash

    # Stale analysis condition is satisfied:
    # compute_analysis_input_hash(current_entry) != analysis.entry_content_hash
    is_stale = compute_analysis_input_hash(entry) != analysis.entry_content_hash
    assert is_stale is True


def test_hash_computation_functions_distinction():
    """Verify compute_ingestion_dedupe_hash and compute_analysis_input_hash semantics."""
    title = "Test Judgment"
    url = "https://example.com/test?utm_source=rss"
    content = "Detailed judgment text..."

    dedupe_hash_1 = compute_ingestion_dedupe_hash("  Test Judgment  ", "https://example.com/test  ")
    dedupe_hash_2 = compute_ingestion_dedupe_hash("Test Judgment", "https://example.com/test")
    # Clean URL and title normalization (whitespace stripping) ensures matching dedupe hash
    assert dedupe_hash_1 == dedupe_hash_2

    entry = Entry(
        title=title,
        url=url,
        content=content,
        content_hash=dedupe_hash_1,
    )
    analysis_hash = compute_analysis_input_hash(entry)

    # Ingestion dedupe hash and analysis input hash serve different purposes and have different values
    assert dedupe_hash_1 != analysis_hash

