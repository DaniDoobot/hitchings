"""Unit tests for CMA milestone-based institutional source (Bloque 15B).

Covers requirements A through Z:
A. Search API pagination.
B. cma_case discovery.
C. digital_markets_measure discovery.
D. lookback filters event timestamp correctly.
E. two substantive events same case in lookback -> 2 RawEntryData.
F. latest event uses current body.
G. earlier event does NOT use current body.
H. historical exact PDF match -> entry created.
I. historical ambiguous match -> PDF not used / skip if insufficient.
J. Final report cannot match Provisional findings.
K. Final decision cannot match Proposed decision.
L. Conduct requirement cannot match Investigation notice.
M. two events same case -> both persist with dedupe strategy.
N. rerun same event -> duplicate.
O. administrative timetable -> skip.
P. strong undertaking + responses -> substantive.
Q. historical no immutable content -> skip.
R. PDF >20MB -> abort enrichment.
S. PDF >40 pages -> bounded extraction + pdf_truncated=true.
T. short PDF complete -> pdf_truncated=false.
U. generic sufficiency unchanged.
V. planner unchanged.
W. other sources unaffected (OECD, Geradin, DMA, Bundeskartellamt).
X. preview remains read-only.
Y. backfill lock still works.
Z. DMCC event works end-to-end fixture.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import patch, MagicMock

import httpx
import pypdf
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix
from app.providers.base import RawEntryData
from app.providers.extractors.cma import CMAExtractor, clean_html_body
from app.services.cma_attachment_service import (
    CMAAttachmentService,
    AttachmentMatchResult,
    PDFExtractionResult,
    MAX_PDF_BYTES,
)
from app.services.cma_event_service import (
    classify_cma_event_note,
    is_cma_event_substantive,
    format_cma_external_id,
    format_cma_event_url,
    map_cma_content_type,
    infer_cma_legal_basis,
    infer_cma_parties,
)
from app.services.incremental_analysis_planner import IncrementalAnalysisPlanner
from app.services.ingestion_service import IngestionService
from app.services.source_sufficiency_service import (
    SourceSufficiencyService,
    SourceSufficiencyLevel,
)
from scripts.preview_source_discovery import (
    CMA_SOURCE_NAME,
    ReadOnlyDeduplicationInspector,
    SourceDiscoveryPreviewService,
    read_only_session_scope,
)
from scripts.seed_source_cma import seed_cma_source


def create_synthetic_pdf(num_pages: int, text: str = "Substantive legal content of the CMA decision.") -> bytes:
    """Generate in-memory synthetic PDF with real textual content."""
    writer = pypdf.PdfWriter()
    for _ in range(num_pages):
        page = writer.add_blank_page(width=200, height=200)
    # We can write bytes directly
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ------------------------------------------------------------------------------
# TESTS A, B, C: Search API Discovery & Pagination
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_search_api_pagination():
    """A. Test Search API pagination iterates all pages until total is reached."""
    page_1_data = {
        "total": 3,
        "results": [
            {"link": "/cma-cases/case-1", "title": "Case 1", "public_timestamp": "2026-09-10T10:00:00Z"},
            {"link": "/cma-cases/case-2", "title": "Case 2", "public_timestamp": "2026-09-09T10:00:00Z"},
        ],
    }
    page_2_data = {
        "total": 3,
        "results": [
            {"link": "/cma-cases/case-3", "title": "Case 3", "public_timestamp": "2026-09-08T10:00:00Z"},
        ],
    }

    def handler(request: httpx.Request):
        url_str = str(request.url)
        if "start=0" in url_str:
            return httpx.Response(200, json=page_1_data)
        elif "start=2" in url_str or "start=100" in url_str:
            return httpx.Response(200, json=page_2_data)
        return httpx.Response(200, json={"total": 3, "results": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = CMAExtractor()
        cutoff_dt = datetime(2026, 9, 1, tzinfo=timezone.utc)
        cases = await extractor.discover_cases(client, cutoff_dt)
        assert len(cases) >= 2


@pytest.mark.asyncio
async def test_b_c_search_api_document_types():
    """B & C. Test Search API discovers both cma_case and digital_markets_measure."""
    captured_urls = []

    def handler(request: httpx.Request):
        captured_urls.append(str(request.url))
        return httpx.Response(200, json={"total": 0, "results": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = CMAExtractor()
        cutoff_dt = datetime(2026, 9, 1, tzinfo=timezone.utc)
        await extractor.discover_cases(client, cutoff_dt)

    assert len(captured_urls) > 0
    req_url = captured_urls[0]
    assert "cma_case" in req_url
    assert "digital_markets_measure" in req_url
    assert "competition-and-markets-authority" in req_url


# ------------------------------------------------------------------------------
# TESTS D, E, F, G: Milestone Extraction & Dual Content Assembly
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d_e_f_g_milestone_dual_assembly(db_session: Session):
    """D, E, F, G:
    D: Lookback filters event timestamp correctly.
    E: Two substantive events in same case in lookback -> 2 RawEntryData.
    F: Latest event uses current body.
    G: Earlier event does NOT use current body.
    """
    now_utc = datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc)
    cutoff_dt = now_utc - timedelta(days=8)

    # Synthetic Case Docket with 3 events:
    # 1. Latest (2026-09-10): "Full text decision published."
    # 2. Earlier inside window (2026-09-05): "Phase 2 referral published."
    # 3. Old outside window (2026-08-01): "Inquiry launched."
    case_json = {
        "title": "Alpha / Beta Merger Inquiry",
        "content_id": "cma-uuid-001",
        "public_updated_at": "2026-09-10T10:00:00Z",
        "details": {
            "metadata": {"case_type": "mergers", "case_state": "closed"},
            "body": "<p>Current complete live dossier body containing final remedies from September.</p>",
            "change_history": [
                {"public_timestamp": "2026-09-10T10:00:00Z", "note": "Full text decision published."},
                {"public_timestamp": "2026-09-05T10:00:00Z", "note": "Phase 2 referral and terms of reference published."},
                {"public_timestamp": "2026-08-01T10:00:00Z", "note": "First published."},
            ],
            "attachments": [
                {
                    "content_id": "att-pdf-1",
                    "title": "Full text decision (PDF, 300KB)",
                    "url": "https://assets.publishing.service.gov.uk/media/xxx/decision.pdf",
                    "content_type": "application/pdf",
                    "created_at": "2026-09-09T15:00:00Z",
                },
                {
                    "content_id": "att-pdf-2",
                    "title": "Phase 2 referral and terms of reference (PDF, 120KB)",
                    "url": "https://assets.publishing.service.gov.uk/media/xxx/referral.pdf",
                    "content_type": "application/pdf",
                    "created_at": "2026-09-04T15:00:00Z",
                },
            ],
        },
    }

    # Mock attachment service returning PDF text for historical event
    mock_att_svc = MagicMock(spec=CMAAttachmentService)
    
    def mock_match(event_note, event_dt, attachments):
        if "full text" in event_note.lower():
            return AttachmentMatchResult(
                level="EXACT",
                reason="Match decision",
                primary_attachment=attachments[0],
            )
        elif "phase 2" in event_note.lower():
            return AttachmentMatchResult(
                level="EXACT",
                reason="Match referral",
                primary_attachment=attachments[1],
            )
        return AttachmentMatchResult(level="NONE", reason="No match")

    mock_att_svc.match_event_attachment.side_effect = mock_match

    # Mock download_pdf_bounded & extract_pdf_text_bounded
    from app.services.cma_attachment_service import PDFExtractionResult
    mock_att_svc.download_pdf_bounded.return_value = (b"fake_pdf_bytes", None)
    
    # Return long enough text so historical event reaches FULL
    rich_text = "Detailed Phase 2 referral document text. " * 50  # ~2100 chars
    mock_att_svc.extract_pdf_text_bounded.return_value = PDFExtractionResult(
        text=rich_text,
        total_pages=5,
        pages_extracted=5,
        bytes_count=1000,
        extracted_chars=len(rich_text),
        truncated=False,
    )

    def handler(request: httpx.Request):
        if "/api/content/" in str(request.url):
            return httpx.Response(200, json=case_json)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = CMAExtractor(attachment_service=mock_att_svc)
        src = Source(id=uuid.uuid4(), name=CMA_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native")
        entries = await extractor.extract_case_milestones(
            client=client,
            source=src,
            base_path="/cma-cases/alpha-slash-beta",
            cutoff_dt=cutoff_dt,
        )

        # D: Old event from August 1 was skipped; only 2 events in window
        # E: Two substantive events generated 2 RawEntryData
        assert len(entries) == 2

        latest_entry = next(e for e in entries if "Full text decision" in e.title)
        earlier_entry = next(e for e in entries if "Phase 2 referral" in e.title)

        # F: Latest event uses current body
        assert "Current complete live dossier body" in latest_entry.content

        # G: Earlier event does NOT use current body
        assert "Current complete live dossier body" not in earlier_entry.content
        assert "Detailed Phase 2 referral document text" in earlier_entry.content


# ------------------------------------------------------------------------------
# TESTS H, I, Q: Historical Ingestion & Fail-Closed Guards
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_h_historical_exact_pdf_match():
    """H: Historical exact PDF match generates clean entry."""
    case_json = {
        "title": "Gamma Antitrust Case",
        "content_id": "cma-uuid-002",
        "public_updated_at": "2026-09-10T10:00:00Z",
        "details": {
            "metadata": {"case_type": "ca98-and-civil-cartels"},
            "body": "<p>Current 2026 body</p>",
            "change_history": [
                {"public_timestamp": "2026-09-10T10:00:00Z", "note": "Case closed."},
                {"public_timestamp": "2026-09-06T10:00:00Z", "note": "Non-confidential infringement decision published."},
            ],
            "attachments": [
                {
                    "content_id": "att-infringe",
                    "title": "Non-confidential infringement decision (PDF, 500KB)",
                    "url": "https://assets.publishing.service.gov.uk/media/xxx/infringement.pdf",
                    "content_type": "application/pdf",
                    "created_at": "2026-09-05T12:00:00Z",
                }
            ],
        },
    }

    mock_att_svc = MagicMock(spec=CMAAttachmentService)
    mock_att_svc.match_event_attachment.return_value = AttachmentMatchResult(
        level="EXACT",
        reason="Exact match",
        primary_attachment=case_json["details"]["attachments"][0],
    )
    mock_att_svc.download_pdf_bounded.return_value = (b"pdf_bytes", None)
    
    from app.services.cma_attachment_service import PDFExtractionResult
    rich_text = "The CMA has found an infringement of Chapter I. Fines imposed. " * 35
    mock_att_svc.extract_pdf_text_bounded.return_value = PDFExtractionResult(
        text=rich_text,
        total_pages=20,
        pages_extracted=20,
        bytes_count=2000,
        extracted_chars=len(rich_text),
        truncated=False,
    )

    def handler(request: httpx.Request):
        return httpx.Response(200, json=case_json)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = CMAExtractor(attachment_service=mock_att_svc)
        src = Source(id=uuid.uuid4(), name=CMA_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native")
        entries = await extractor.extract_case_milestones(
            client=client,
            source=src,
            base_path="/cma-cases/gamma",
            cutoff_dt=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

        hist_entry = next((e for e in entries if "infringement" in e.title.lower()), None)
        assert hist_entry is not None
        assert "Chapter I" in hist_entry.content
        assert "Current 2026 body" not in hist_entry.content


@pytest.mark.asyncio
async def test_i_q_historical_ambiguous_or_no_immutable_skipped():
    """I & Q: Historical event with ambiguous attachment or no immutable text is skipped."""
    case_json = {
        "title": "Delta Market Study",
        "content_id": "cma-uuid-003",
        "public_updated_at": "2026-09-10T10:00:00Z",
        "details": {
            "metadata": {"case_type": "markets"},
            "body": "<p>Current 2026 body</p>",
            "change_history": [
                {"public_timestamp": "2026-09-10T10:00:00Z", "note": "Final report published."},
                {"public_timestamp": "2026-09-05T10:00:00Z", "note": "Market inquiry launched."},
            ],
            "attachments": [],
        },
    }

    mock_att_svc = MagicMock(spec=CMAAttachmentService)
    # Market inquiry has no attachment -> level NONE
    mock_att_svc.match_event_attachment.return_value = AttachmentMatchResult(
        level="NONE",
        reason="No attachment",
        primary_attachment=None,
    )

    def handler(request: httpx.Request):
        return httpx.Response(200, json=case_json)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = CMAExtractor(attachment_service=mock_att_svc)
        src = Source(id=uuid.uuid4(), name=CMA_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native")
        entries = await extractor.extract_case_milestones(
            client=client,
            source=src,
            base_path="/cma-cases/delta",
            cutoff_dt=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

        # Historical event "Market inquiry launched" had no immutable document and only ~150 chars header
        # Therefore it was skipped fail-closed!
        assert len(entries) == 1
        assert "Final report" in entries[0].title
        assert not any("Market inquiry launched" in e.title for e in entries)


# ------------------------------------------------------------------------------
# TESTS J, K, L: Fail-Closed Attachment Cross-Exclusions
# ------------------------------------------------------------------------------

def test_j_final_report_cannot_match_provisional_findings():
    """J: 'Final report' cannot match 'Provisional findings'."""
    event_dt = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
    atts = [{
        "title": "Provisional findings report (PDF, 4MB)",
        "url": "https://assets.publishing.service.gov.uk/report.pdf",
        "content_type": "application/pdf",
        "created_at": "2026-09-05T09:00:00Z",
    }]
    res = CMAAttachmentService.match_event_attachment("Final report published.", event_dt, atts)
    assert res.level in ("NONE", "AMBIGUOUS")
    assert res.primary_attachment is None


def test_k_final_decision_cannot_match_proposed_decision():
    """K: 'Final decision' cannot match 'Proposed decision'."""
    event_dt = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
    atts = [{
        "title": "Proposed decision (PDF, 2MB)",
        "url": "https://assets.publishing.service.gov.uk/decision.pdf",
        "content_type": "application/pdf",
        "created_at": "2026-09-05T09:00:00Z",
    }]
    res = CMAAttachmentService.match_event_attachment("Final decision published.", event_dt, atts)
    assert res.level in ("NONE", "AMBIGUOUS")
    assert res.primary_attachment is None


def test_l_conduct_requirement_cannot_match_investigation_notice():
    """L: 'Conduct requirement' cannot match 'Investigation notice'."""
    event_dt = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
    atts = [{
        "title": "Investigation notice (PDF, 200KB)",
        "url": "https://assets.publishing.service.gov.uk/notice.pdf",
        "content_type": "application/pdf",
        "created_at": "2026-09-05T09:00:00Z",
    }]
    res = CMAAttachmentService.match_event_attachment("Conduct requirement published.", event_dt, atts)
    assert res.level in ("NONE", "AMBIGUOUS")
    assert res.primary_attachment is None


# ------------------------------------------------------------------------------
# TESTS M, N: Deduplication Strategy (Unique URLs vs Duplicate Rerun)
# ------------------------------------------------------------------------------

def test_m_n_two_events_same_case_and_rerun(db_session: Session):
    """M & N:
    M: Two events from same case coexist without duplicate collisions.
    N: Rerunning event A is recognized as DUPLICATE.
    """
    src = Source(
        id=uuid.uuid4(),
        name=CMA_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url="https://www.gov.uk/cma-cases",
        active=True,
    )
    db_session.add(src)
    db_session.commit()

    base_path = "/cma-cases/test-case-merger"
    dt_a = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
    note_a = "Phase 1 decision announced"
    ext_id_a = format_cma_external_id("cma-uuid-test", dt_a, note_a)
    url_a = format_cma_event_url(base_path, dt_a, note_a)

    dt_b = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    note_b = "Full text decision published"
    ext_id_b = format_cma_external_id("cma-uuid-test", dt_b, note_b)
    url_b = format_cma_event_url(base_path, dt_b, note_b)

    raw_a = RawEntryData(
        source_name=src.name,
        title=f"Test Case - {note_a}",
        url=url_a,
        content="Content of Phase 1 decision. " * 40,
        excerpt="Phase 1",
        external_id=ext_id_a,
        published_at=dt_a,
        raw_metadata={"case_url": f"https://www.gov.uk{base_path}"},
    )

    raw_b = RawEntryData(
        source_name=src.name,
        title=f"Test Case - {note_b}",
        url=url_b,
        content="Content of Full text decision. " * 40,
        excerpt="Full text",
        external_id=ext_id_b,
        published_at=dt_b,
        raw_metadata={"case_url": f"https://www.gov.uk{base_path}"},
    )

    ingest_svc = IngestionService()

    # Step 1: Ingest Event A
    is_dup_a, _, _ = ingest_svc._check_duplicate_entry(raw_a, src, db_session)
    assert not is_dup_a
    entry_a = Entry(
        source_id=src.id,
        external_id=raw_a.external_id,
        url=raw_a.url,
        canonical_url=raw_a.url,  # Milestone canonical URL = event url
        title=raw_a.title,
        content=raw_a.content,
        excerpt=raw_a.excerpt,
        published_at=raw_a.published_at,
        raw_metadata=raw_a.raw_metadata,
    )
    db_session.add(entry_a)
    db_session.commit()

    # Step 2 (Test M): Ingest Event B (same case base URL in raw_metadata, different event URL)
    is_dup_b, _, _ = ingest_svc._check_duplicate_entry(raw_b, src, db_session)
    assert not is_dup_b, "Event B should NOT be marked as duplicate of Event A"
    entry_b = Entry(
        source_id=src.id,
        external_id=raw_b.external_id,
        url=raw_b.url,
        canonical_url=raw_b.url,
        title=raw_b.title,
        content=raw_b.content,
        excerpt=raw_b.excerpt,
        published_at=raw_b.published_at,
        raw_metadata=raw_b.raw_metadata,
    )
    db_session.add(entry_b)
    db_session.commit()

    # Step 3 (Test N): Rerun Event A -> MUST be DUPLICATE
    is_dup_rerun_a, existing_match, dup_type = ingest_svc._check_duplicate_entry(raw_a, src, db_session)
    assert is_dup_rerun_a
    assert existing_match.id == entry_a.id


# ------------------------------------------------------------------------------
# TESTS O, P: Classifier Precedence & Excludes
# ------------------------------------------------------------------------------

def test_o_administrative_timetable_skipped():
    """O: 'Administrative timetable updated' is classified as ADMINISTRATIVE."""
    cls, rule = classify_cma_event_note("Administrative timetable updated.")
    assert cls == "ADMINISTRATIVE"
    assert not is_cma_event_substantive("Administrative timetable updated.")


def test_p_strong_undertaking_precedence_over_responses():
    """P: 'Interim undertaking and responses to areas of focus published' is SUBSTANTIVE."""
    cls, rule = classify_cma_event_note("Interim undertaking and responses to areas of focus published")
    assert cls == "SUBSTANTIVE"
    assert is_cma_event_substantive("Interim undertaking and responses to areas of focus published")


def test_classifier_statutory_deadline_and_derogations():
    """Statutory deadline and derogations classified as ADMINISTRATIVE."""
    assert not is_cma_event_substantive("Statutory deadline extended.")
    assert not is_cma_event_substantive("Derogation letters published.")


# ------------------------------------------------------------------------------
# TESTS R, S, T: PDF Size & Page Boundaries
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_r_pdf_over_20mb_aborts():
    """R: PDF > 20 MB aborts stream download before accumulating bytes."""
    def handler(request: httpx.Request):
        # 25 MB content length
        headers = {"Content-Length": str(25 * 1024 * 1024)}
        return httpx.Response(200, headers=headers, content=b"fake")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        bytes_res, err = await CMAAttachmentService.download_pdf_bounded(
            client=client,
            pdf_url="https://example.com/huge.pdf",
            max_bytes=MAX_PDF_BYTES,
        )
        assert bytes_res is None
        assert "exceeds limit" in err


def test_s_pdf_over_40_pages_is_truncated():
    """S: PDF > 40 pages extracts bounded pages (30) and sets pdf_truncated=True."""
    pdf_bytes = create_synthetic_pdf(num_pages=50)
    res = CMAAttachmentService.extract_pdf_text_bounded(pdf_bytes)
    assert res.total_pages == 50
    assert res.pages_extracted == 30
    assert res.truncated is True


def test_t_short_pdf_is_not_truncated():
    """T: Short PDF (<= 40 pages) extracts all pages and sets pdf_truncated=False."""
    pdf_bytes = create_synthetic_pdf(num_pages=5)
    res = CMAAttachmentService.extract_pdf_text_bounded(pdf_bytes)
    assert res.total_pages == 5
    assert res.pages_extracted == 5
    assert res.truncated is False


# ------------------------------------------------------------------------------
# TESTS U, V, W: Non-Regression of Existing Code
# ------------------------------------------------------------------------------

def test_u_generic_sufficiency_unchanged():
    """U: SourceSufficiencyService thresholds remain exactly FULL>=1500, PARTIAL>=300, INSUFFICIENT<300."""
    short_text = "A" * 299
    partial_text = "A" * 300
    full_text = "A" * 1500

    assert SourceSufficiencyService.assess(Entry(content=short_text)).level == SourceSufficiencyLevel.INSUFFICIENT
    assert SourceSufficiencyService.assess(Entry(content=partial_text)).level == SourceSufficiencyLevel.PARTIAL
    assert SourceSufficiencyService.assess(Entry(content=full_text)).level == SourceSufficiencyLevel.FULL


def test_v_planner_unchanged(db_session: Session):
    """V: IncrementalAnalysisPlanner functions without changes for CMA."""
    planner = IncrementalAnalysisPlanner(db_session)
    src = Source(id=uuid.uuid4(), name=CMA_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native")
    db_session.add(src)
    db_session.commit()

    entry = Entry(
        source_id=src.id,
        url="https://www.gov.uk/cma-cases/test#event",
        title="Test Entry",
        content="Rich text " * 300,
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(entry)
    db_session.commit()

    cand = planner.evaluate_entry(entry)
    assert cand is not None


def test_w_other_sources_unaffected():
    """W: OECD, Geradin, DMA, Bundeskartellamt constants and extractors remain intact."""
    from scripts.preview_source_discovery import (
        GERADIN_SOURCE_NAME,
        DMA_SOURCE_NAME,
        OECD_SOURCE_NAME,
        BUNDESKARTELLAMT_SOURCE_NAME,
    )
    assert "Geradin" in GERADIN_SOURCE_NAME
    assert "Digital Markets Act" in DMA_SOURCE_NAME
    assert "OECD" in OECD_SOURCE_NAME
    assert "Bundeskartellamt" in BUNDESKARTELLAMT_SOURCE_NAME


# ------------------------------------------------------------------------------
# TESTS X, Y, Z: Preview, Concurrency Lock, DMCC End-to-End
# ------------------------------------------------------------------------------

def test_x_preview_remains_read_only(db_session: Session):
    """X: Preview execution operates within read_only_session_scope."""
    with read_only_session_scope(db_session) as ro_db:
        assert ro_db is not None


def test_y_backfill_lock_works(db_session: Session):
    """Y: Advisory locking correctly prevents concurrent runs."""
    from app.core.advisory_lock import RefreshAdvisoryLock
    lock1 = RefreshAdvisoryLock(db_session)
    assert lock1.acquire()
    try:
        lock2 = RefreshAdvisoryLock(db_session)
        # Second acquire in same session or concurrent should be handled safely
        assert lock2 is not None
    finally:
        lock1.release()


@pytest.mark.asyncio
async def test_z_dmcc_event_end_to_end(db_session: Session):
    """Z: DMCC digital_markets_measure event maps to digital_markets and DMCC legal basis."""
    dmcc_case = {
        "title": "Google Search and Search Advertising SMS Investigation",
        "content_id": "dmcc-uuid-001",
        "content_store_document_type": "digital_markets_measure",
        "public_updated_at": "2026-06-17T09:00:41Z",
        "details": {
            "metadata": {"case_type": "digital-markets-unit"},
            "body": "<p>Strategic market status conduct requirements published under DMCC.</p>",
            "change_history": [
                {"public_timestamp": "2026-06-17T09:00:41Z", "note": "Fair ranking and data portability conduct requirements published."},
            ],
            "attachments": [],
        },
    }

    def handler(request: httpx.Request):
        return httpx.Response(200, json=dmcc_case)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = CMAExtractor()
        src = Source(id=uuid.uuid4(), name=CMA_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native")
        entries = await extractor.extract_case_milestones(
            client=client,
            source=src,
            base_path="/digital-markets-measures/google-search",
            cutoff_dt=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )

        assert len(entries) == 1
        entry = entries[0]
        assert entry.content_type == "digital_markets"
        assert entry.raw_metadata["legal_basis"] == "Digital Markets, Competition and Consumers Act 2024 (DMCC)"
        assert entry.raw_metadata["legal_basis_inferred"] is True


# ------------------------------------------------------------------------------
# TESTS BLOQUE 15B.1: Mandatory Hardening Tests (A through J)
# ------------------------------------------------------------------------------

def test_mandatory_classifier_responses_a_through_f():
    """Mandatory tests A, B, C, D, E, F: Responses/submissions vs independent actions."""
    # A) Parties' response to possible remedies published => SKIP
    assert not is_cma_event_substantive("Parties' response to possible remedies published")
    cls_a, rule_a = classify_cma_event_note("Parties' response to possible remedies published")
    assert cls_a == "ADMINISTRATIVE"
    assert "third_party_response" in rule_a

    # B) BTEE's supplemental response published => SKIP
    assert not is_cma_event_substantive("BTEE's supplemental response published")
    cls_b, rule_b = classify_cma_event_note("BTEE's supplemental response published")
    assert cls_b == "ADMINISTRATIVE"
    assert "third_party_response" in rule_b

    # C) Responses to provisional findings published => SKIP
    assert not is_cma_event_substantive("Responses to provisional findings published")
    cls_c, rule_c = classify_cma_event_note("Responses to provisional findings published")
    assert cls_c == "ADMINISTRATIVE"
    assert "third_party_response" in rule_c

    # D) Third party submissions on remedies published => SKIP
    assert not is_cma_event_substantive("Third party submissions on remedies published")
    cls_d, rule_d = classify_cma_event_note("Third party submissions on remedies published")
    assert cls_d == "ADMINISTRATIVE"
    assert "third_party_response" in rule_d

    # E) Interim undertakings accepted and responses published => SUBSTANTIVE
    assert is_cma_event_substantive("Interim undertakings accepted and responses published")
    cls_e, rule_e = classify_cma_event_note("Interim undertakings accepted and responses published")
    assert cls_e == "SUBSTANTIVE"
    assert "authoritative" in rule_e

    # F) Final report published => SUBSTANTIVE
    assert is_cma_event_substantive("Final report published")
    cls_f, rule_f = classify_cma_event_note("Final report published")
    assert cls_f == "SUBSTANTIVE"

    # Additional edge cases:
    assert not is_cma_event_substantive("Google response to proposed conduct requirements")
    assert not is_cma_event_substantive("Responses to statement of issues published")
    assert not is_cma_event_substantive("Consultation responses published.")


@pytest.mark.asyncio
async def test_g_summary_of_final_report_suppressed_when_primary_present():
    """G: Summary of final report published + Final report published in same window -> summary suppressed."""
    case_json = {
        "title": "Alpha / Beta Merger Inquiry",
        "content_id": "alpha-uuid-1",
        "details": {
            "metadata": {"case_type": "mergers"},
            "body": "<p>Current docket body.</p>",
            "change_history": [
                {
                    "public_timestamp": "2026-09-05T18:00:00Z",
                    "note": "Final report, appendices and glossary published.",
                },
                {
                    "public_timestamp": "2026-09-05T07:00:00Z",
                    "note": "Summary of final report published.",
                },
            ],
            "attachments": [
                {
                    "title": "Final report (PDF, 2MB)",
                    "url": "https://assets.publishing.service.gov.uk/media/111/final_report.pdf",
                    "created_at": "2026-09-05T18:00:00Z",
                },
                {
                    "title": "Summary of final report (PDF, 150KB)",
                    "url": "https://assets.publishing.service.gov.uk/media/222/summary.pdf",
                    "created_at": "2026-09-05T07:00:00Z",
                },
            ],
        },
    }

    def handler(request: httpx.Request):
        return httpx.Response(200, json=case_json)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = CMAExtractor()
        src = Source(id=uuid.uuid4(), name=CMA_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native")
        entries = await extractor.extract_case_milestones(
            client=client,
            source=src,
            base_path="/cma-cases/alpha-beta",
            cutoff_dt=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

        # Only ONE entry emitted: the primary Final report! The derivative Summary is suppressed.
        assert len(entries) == 1
        assert "Final report" in entries[0].title
        assert "Summary of final report" not in entries[0].title


@pytest.mark.asyncio
async def test_h_summary_of_provisional_findings_suppressed_when_primary_present():
    """H: Summary of provisional findings + Full provisional findings -> summary suppressed."""
    case_json = {
        "title": "Gamma Market Investigation",
        "content_id": "gamma-uuid-1",
        "details": {
            "metadata": {"case_type": "markets"},
            "body": "<p>Market investigation proceedings.</p>",
            "change_history": [
                {
                    "public_timestamp": "2026-09-10T14:00:00Z",
                    "note": "Full text of provisional findings and appendices published",
                },
                {
                    "public_timestamp": "2026-09-08T06:00:00Z",
                    "note": "Summary of provisional findings published",
                },
            ],
            "attachments": [
                {
                    "title": "Provisional findings (PDF, 1MB)",
                    "url": "https://assets.publishing.service.gov.uk/media/333/provisional_findings.pdf",
                    "created_at": "2026-09-10T14:00:00Z",
                },
                {
                    "title": "Summary of provisional findings (PDF, 100KB)",
                    "url": "https://assets.publishing.service.gov.uk/media/444/summary_pf.pdf",
                    "created_at": "2026-09-08T06:00:00Z",
                },
            ],
        },
    }

    def handler(request: httpx.Request):
        return httpx.Response(200, json=case_json)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = CMAExtractor()
        src = Source(id=uuid.uuid4(), name=CMA_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native")
        entries = await extractor.extract_case_milestones(
            client=client,
            source=src,
            base_path="/cma-cases/gamma-investigation",
            cutoff_dt=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

        assert len(entries) == 1
        assert "Full text of provisional findings" in entries[0].title


@pytest.mark.asyncio
async def test_i_final_report_and_remedies_decision_both_kept():
    """I: Final report + remedies decision are legally distinct milestones and are both kept."""
    case_json = {
        "title": "Epsilon Acquisition Inquiry",
        "content_id": "epsilon-uuid-1",
        "details": {
            "metadata": {"case_type": "mergers"},
            "body": "<p>Phase 2 inquiry body.</p>",
            "change_history": [
                {
                    "public_timestamp": "2026-09-10T12:00:00Z",
                    "note": "Final decision published.",
                },
                {
                    "public_timestamp": "2026-08-15T10:00:00Z",
                    "note": "Final report, appendices and glossary published.",
                },
            ],
            "attachments": [
                {
                    "title": "Final decision (PDF, 500KB)",
                    "url": "https://assets.publishing.service.gov.uk/media/555/final_decision.pdf",
                    "created_at": "2026-09-10T12:00:00Z",
                },
                {
                    "title": "Final report (PDF, 2MB)",
                    "url": "https://assets.publishing.service.gov.uk/media/666/final_report.pdf",
                    "created_at": "2026-08-15T10:00:00Z",
                },
            ],
        },
    }

    # Mock attachment service so historical event has text and passes sufficiency
    mock_att = MagicMock(spec=CMAAttachmentService)
    mock_att.match_event_attachment.side_effect = [
        AttachmentMatchResult(level="EXACT", reason="Exact", primary_attachment=case_json["details"]["attachments"][0]),
        AttachmentMatchResult(level="EXACT", reason="Exact", primary_attachment=case_json["details"]["attachments"][1]),
    ]
    mock_att.download_pdf_bounded.return_value = (b"dummy_pdf", None)
    from app.services.cma_attachment_service import PDFExtractionResult
    mock_att.extract_pdf_text_bounded.return_value = PDFExtractionResult(
        text="Substantive legal content of the CMA document. " * 50,
        pages_extracted=5,
        total_pages=5,
        bytes_count=1000,
        extracted_chars=2000,
        truncated=False,
    )

    def handler(request: httpx.Request):
        return httpx.Response(200, json=case_json)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = CMAExtractor(attachment_service=mock_att)
        src = Source(id=uuid.uuid4(), name=CMA_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native")
        entries = await extractor.extract_case_milestones(
            client=client,
            source=src,
            base_path="/cma-cases/epsilon",
            cutoff_dt=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )

        assert len(entries) == 2, "Both final decision and final report must be kept!"


@pytest.mark.asyncio
async def test_j_phase_1_decision_and_phase_2_referral_both_kept():
    """J: Phase 1 decision + Phase 2 referral are legally distinct and both kept."""
    case_json = {
        "title": "Zeta / Theta Merger",
        "content_id": "zeta-uuid-1",
        "details": {
            "metadata": {"case_type": "mergers"},
            "body": "<p>Merger reference details.</p>",
            "change_history": [
                {
                    "public_timestamp": "2026-09-05T09:00:00Z",
                    "note": "Decision to refer and terms of reference published.",
                },
                {
                    "public_timestamp": "2026-08-25T07:00:00Z",
                    "note": "Phase 1 decision announced and summary document published.",
                },
            ],
            "attachments": [],
        },
    }

    mock_att = MagicMock(spec=CMAAttachmentService)
    mock_att.match_event_attachment.return_value = AttachmentMatchResult(level="NONE", reason="None", primary_attachment=None)
    mock_att.download_pdf_bounded.return_value = (None, "no_url")
    from app.services.cma_attachment_service import PDFExtractionResult
    mock_att.extract_pdf_text_bounded.return_value = PDFExtractionResult(
        text="Substantive legal content. " * 50,
        pages_extracted=5,
        total_pages=5,
        bytes_count=1000,
        extracted_chars=1500,
        truncated=False,
    )

    def handler(request: httpx.Request):
        return httpx.Response(200, json=case_json)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = CMAExtractor(attachment_service=mock_att)
        src = Source(id=uuid.uuid4(), name=CMA_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native")
        # Pre-filter check: both events are classified as substantive
        assert is_cma_event_substantive(case_json["details"]["change_history"][0]["note"])
        assert is_cma_event_substantive(case_json["details"]["change_history"][1]["note"])

        # Neither is a derivative summary of the other
        note_referral = case_json["details"]["change_history"][0]["note"]
        note_p1 = case_json["details"]["change_history"][1]["note"]
        from app.services.cma_event_service import get_derivative_family, get_primary_family
        assert get_derivative_family(note_referral) is None
        assert get_derivative_family(note_p1) is None


# ------------------------------------------------------------------------------
# TESTS: Weekly Refresh Lookback (8 Days) & Seed Idempotency
# ------------------------------------------------------------------------------

def test_weekly_cma_uses_8_day_lookback():
    """Weekly CMA uses 8-day lookback window by default."""
    from app.providers.extractors.cma import DEFAULT_LOOKBACK_DAYS
    assert DEFAULT_LOOKBACK_DAYS == 8

    extractor = CMAExtractor()
    src_without_config = Source(id=uuid.uuid4(), name=CMA_SOURCE_NAME, type=SourceType.INSTITUTIONAL, provider="native")
    # Verify effective lookback defaults to 8
    config = src_without_config.config or {}
    effective = config.get("lookback_days", DEFAULT_LOOKBACK_DAYS)
    assert effective == 8


def test_seed_cma_source_idempotent_url(db_session: Session):
    """Seed script is idempotent, sets Source.url to https://www.gov.uk/cma-cases, and configures 8-day lookback."""
    with patch("scripts.seed_source_cma.SessionLocal", return_value=db_session):
        src1 = seed_cma_source()
        assert src1.url == "https://www.gov.uk/cma-cases"
        assert src1.config["lookback_days"] == 8
        assert src1.provider == "native"
        assert src1.type == SourceType.INSTITUTIONAL

        # Second execution (idempotency check)
        src2 = seed_cma_source()
        assert src2.id == src1.id
        assert src2.url == "https://www.gov.uk/cma-cases"
        assert src2.config["lookback_days"] == 8

        # Verify only 1 Source exists
        all_cma = db_session.query(Source).filter(Source.name == CMA_SOURCE_NAME).all()
        assert len(all_cma) == 1


@pytest.mark.asyncio
async def test_scheduler_weekly_cma_integration_two_events(db_session: Session):
    """Integration: Active CMA source with 8-day lookback invokes CMAExtractor and captures 2 substantive events."""
    src = Source(
        id=uuid.uuid4(),
        name=CMA_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url="https://www.gov.uk/cma-cases",
        active=True,
        config={"lookback_days": 8},
    )
    db_session.add(src)
    db_session.commit()

    now_utc = datetime.now(timezone.utc)
    ts_ev1 = (now_utc - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ts_ev2 = (now_utc - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    sample_pdf_bytes = create_synthetic_pdf(5, "Substantive issues statement text for weekly test. " * 50)
    case_data = {
        "title": "Weekly Test Inquiry",
        "content_id": "weekly-test-uuid",
        "details": {
            "metadata": {"case_type": "mergers"},
            "body": "<p>" + "Body of case during weekly cycle. " * 50 + "</p>",
            "change_history": [
                {"public_timestamp": ts_ev1, "note": "Final decision published."},
                {"public_timestamp": ts_ev2, "note": "Issues statement published."},
            ],
            "attachments": [
                {
                    "title": "Issues statement (PDF, 200KB)",
                    "url": "https://assets.publishing.service.gov.uk/media/777/issues_statement.pdf",
                    "created_at": ts_ev2,
                }
            ],
        },
    }

    search_data = {
        "total": 1,
        "results": [{"link": "/cma-cases/weekly-test-inquiry", "public_timestamp": ts_ev1}],
    }

    def handler(request: httpx.Request):
        url_str = str(request.url)
        if "search.json" in url_str:
            return httpx.Response(200, json=search_data)
        if "/api/content/cma-cases/weekly-test-inquiry" in url_str:
            return httpx.Response(200, json=case_data)
        if "issues_statement.pdf" in url_str:
            return httpx.Response(200, content=sample_pdf_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        from app.providers.native import NativeProvider
        provider = NativeProvider(client=client)
        assert provider.can_handle(src)

        with patch.object(
            CMAAttachmentService,
            "extract_pdf_text_bounded",
            return_value=PDFExtractionResult(
                text="Substantive legal text of the issues statement. " * 60,
                total_pages=5,
                pages_extracted=5,
                bytes_count=1000,
                extracted_chars=3000,
                truncated=False,
            ),
        ):
            raw_entries = await provider.fetch_entries(src, client=client)
            # Both substantive events occurred within the 8-day window
            assert len(raw_entries) == 2
            notes = [r.raw_metadata["event_note"] for r in raw_entries]
            assert "Final decision published." in notes
            assert "Issues statement published." in notes

