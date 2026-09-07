"""Tests for Court of Justice of the European Union (CJEU / CURIA) case law extractor and ingestion."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRunStatus
from app.models.tracking import TrackedEntity
from app.providers.native import NativeProvider
from app.providers.base import ProviderError
from app.providers.extractors.curia import (
    CuriaCaseLawExtractor,
    parse_curia_date,
    extract_year_from_procedure,
    select_best_language,
    clean_document_html,
    DOC_TYPE_MAP,
)
from app.services.ingestion_service import IngestionService


MOCK_SEARCH_RESPONSE = {
    "totalHits": 2,
    "searchHits": [
        {
            "id": "C/0380/25/00000000RP/01/P/01-3512336",
            "content": {
                "id": "C/0380/25/00000000RP/01/P/01-3512336",
                "logicDocId": "id_325979",
                "idProcedure": "C/0380/25/00000000RP/01/P/01",
                "idPublished": "C-380/25",
                "docType": "Conclusions",
                "docTypeCode": "CONCL",
                "docDate": "2026-09-03",
                "docNoPart": 1,
                "celex": "62025CC0380",
                "affairJurisdiction": "Cour de justice",
                "affairJurisdictionCode": "C",
                "ecli": "ECLI:EU:C:2026:702",
                "groupByLogicalId": [
                    {"id": "doc-en", "docLang": "EN"},
                    {"id": "doc-fr", "docLang": "FR"},
                ],
            },
        },
        {
            "id": "C/0320/25/00000000RP/01/P/01-3512200",
            "content": {
                "id": "C/0320/25/00000000RP/01/P/01-3512200",
                "logicDocId": "id_325980",
                "idProcedure": "C/0320/25/00000000RP/01/P/01",
                "idPublished": "C-320/25",
                "docType": "Arrêt",
                "docTypeCode": "ARRET",
                "docDate": "2026-09-03",
                "docNoPart": 1,
                "celex": "62025CJ0320",
                "affairJurisdiction": "Cour de justice",
                "affairJurisdictionCode": "C",
                "ecli": "ECLI:EU:C:2026:690",
                "groupByLogicalId": [
                    {"id": "doc-fr", "docLang": "FR"},
                ],
            },
        },
    ],
}

SAMPLE_OPINION_HTML = """<!DOCTYPE html>
<html>
<head><title>Document</title></head>
<body>
<p class="head">Provisional text</p>
<p>OPINION OF ADVOCATE GENERAL</p>
<p>NORKUS</p>
<p>delivered on 3 September 2026</p>
<p>Case C-380/25</p>
<p>A.M. v Bellavista Società Agricola Ss</p>
<p>(Reference for a preliminary ruling – Social policy – Directive 1999/70/EC – Fixed-term work – Clause 5 – Abuse of successive contracts)</p>
<p>1. By the present request for a preliminary ruling, the Corte suprema di cassazione asks about...</p>
</body>
</html>
"""

SAMPLE_JUDGMENT_HTML = """<!DOCTYPE html>
<html>
<head><title>Document</title></head>
<body>
<p class="head">Édition provisoire</p>
<p>JUDGMENT OF THE COURT (First Chamber)</p>
<p>3 September 2026</p>
<p>Case C-320/25 [Lertimene]</p>
<p>(Reference for a preliminary ruling – Approximation of laws – Directive 2004/38/EC – Freedom of movement)</p>
<p>1. This request for a preliminary ruling concerns the interpretation of Directive 2004/38/EC...</p>
</body>
</html>
"""


# ==================================================
# 1. Unit Tests for Extractor Helpers
# ==================================================


def test_parse_curia_date():
    """Verify ISO date parsing into timezone-aware UTC datetime."""
    dt = parse_curia_date("2026-09-03")
    assert dt is not None
    assert dt == datetime(2026, 9, 3, 0, 0, 0, tzinfo=timezone.utc)
    assert dt.year == 2026
    assert dt.month == 9
    assert dt.day == 3

    assert parse_curia_date(None) is None
    assert parse_curia_date("") is None
    assert parse_curia_date("invalid-date") is None


def test_extract_year_from_procedure():
    """Verify procedure year extraction logic."""
    assert extract_year_from_procedure("C/0380/25/00000000RP/01/P/01", "C-380/25") == "2025"
    assert extract_year_from_procedure("C/0498/24/00000000RP/01/P/01", "C-498/24 P") == "2024"
    assert extract_year_from_procedure("", "C-380/25") == "2025"
    assert extract_year_from_procedure("", "C-123/2023") == "2023"


def test_select_best_language():
    """Verify language preference order (EN -> FR -> first available)."""
    assert select_best_language([{"docLang": "FR"}, {"docLang": "EN"}, {"docLang": "DE"}]) == "EN"
    assert select_best_language([{"docLang": "DE"}, {"docLang": "FR"}]) == "FR"
    assert select_best_language([{"docLang": "IT"}, {"docLang": "ES"}]) == "IT"
    assert select_best_language([]) == "EN"


def test_clean_document_html_opinion():
    """Verify HTML cleanup to clean text, excerpt, and usual name parsing."""
    clean_text, excerpt, usual_name = clean_document_html(SAMPLE_OPINION_HTML)

    assert clean_text is not None
    assert "OPINION OF ADVOCATE GENERAL" in clean_text
    assert "Directive 1999/70/EC" in clean_text
    # Verify strict absence of HTML tags
    assert "<p" not in clean_text
    assert "<div" not in clean_text
    assert "<span" not in clean_text
    assert "<html" not in clean_text
    assert "<body" not in clean_text
    assert "</" not in clean_text

    assert usual_name is None
    assert excerpt is not None
    assert "Reference for a preliminary ruling" in excerpt


def test_clean_document_html_judgment_with_usual_name():
    """Verify extraction of bracketed case name like [Lertimene] and clean text."""
    clean_text, excerpt, usual_name = clean_document_html(SAMPLE_JUDGMENT_HTML)

    assert clean_text is not None
    assert "JUDGMENT OF THE COURT" in clean_text
    assert "<p" not in clean_text
    assert "<div" not in clean_text
    assert usual_name == "Lertimene"
    assert excerpt is not None
    assert "Directive 2004/38/EC" in excerpt


def test_doc_type_mapping():
    """Verify standard document type code mappings."""
    assert DOC_TYPE_MAP["CONCL"] == "Opinion of the Advocate General"
    assert DOC_TYPE_MAP["ARRET"] == "Judgment"
    assert DOC_TYPE_MAP["ORDONN"] == "Order"
    assert DOC_TYPE_MAP["AVIS"] == "Opinion"


# ==================================================
# 2. Extractor Execution with Mock HTTP
# ==================================================


class MockResponse:
    def __init__(self, status_code: int, text: str = "", json_data: dict = None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)


class MockAsyncClient:
    def __init__(self, search_resp: MockResponse, blob_resps: dict[str, MockResponse] = None):
        self.search_resp = search_resp
        self.blob_resps = blob_resps or {}

    async def post(self, url: str, **kwargs):
        return self.search_resp

    async def get(self, url: str, **kwargs):
        for key, resp in self.blob_resps.items():
            if key in url:
                return resp
        return MockResponse(404, "Not found")


@pytest.mark.asyncio
async def test_curia_extractor_success():
    """Verify end-to-end extraction from search hits and blob HTML with text normalization."""
    blob_map = {
        "325979-EN-1.html": MockResponse(200, SAMPLE_OPINION_HTML),
        "325980-FR-1.html": MockResponse(200, SAMPLE_JUDGMENT_HTML),
    }
    client = MockAsyncClient(MockResponse(200, json_data=MOCK_SEARCH_RESPONSE), blob_map)

    source = Source(
        id=uuid.uuid4(),
        name="Court of Justice of the European Union - Case Law",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://infocuria.curia.europa.eu/tabs/jurisprudence",
        config={"initial_fetch_limit": 20},
    )

    extractor = CuriaCaseLawExtractor()
    entries = await extractor.extract(client, source)

    assert len(entries) == 2

    # Entry 1: Opinion of the Advocate General
    e1 = entries[0]
    assert e1.external_id == "ECLI:EU:C:2026:702"
    assert e1.title == "Case C-380/25 | Opinion of the Advocate General"
    assert e1.content_type == "eu_case_law"
    assert e1.language == "en"
    assert e1.published_at == datetime(2026, 9, 3, 0, 0, 0, tzinfo=timezone.utc)
    assert "https://infocuria.curia.europa.eu/tabs/jurisprudence?lang=en&ecli=ECLI:EU:C:2026:702" in e1.url
    assert e1.raw_metadata["case_number"] == "C-380/25"
    assert e1.raw_metadata["celex"] == "62025CC0380"
    assert e1.raw_metadata["document_type"] == "Opinion of the Advocate General"
    assert e1.raw_metadata["content_source"] == "infocuria_html"
    assert e1.raw_metadata["content_format"] == "text/plain"
    assert e1.raw_metadata["full_text_available"] is True
    assert "OPINION OF ADVOCATE GENERAL" in e1.content
    assert "<p>" not in e1.content
    assert "<div>" not in e1.content

    # Entry 2: Judgment with [Lertimene]
    e2 = entries[1]
    assert e2.external_id == "ECLI:EU:C:2026:690"
    assert e2.title == "Case C-320/25 [Lertimene] | Judgment"
    assert e2.content_type == "eu_case_law"
    assert e2.language == "fr"
    assert e2.raw_metadata["case_number"] == "C-320/25"
    assert e2.raw_metadata["usual_name"] == "Lertimene"
    assert e2.raw_metadata["content_source"] == "infocuria_html"
    assert e2.raw_metadata["content_format"] == "text/plain"
    assert e2.raw_metadata["full_text_available"] is True
    assert "JUDGMENT OF THE COURT" in e2.content
    assert "<p>" not in e2.content


@pytest.mark.asyncio
async def test_curia_extractor_blob_fallback():
    """Verify that when official blob fails, content and excerpt are null without fabricated text."""
    client = MockAsyncClient(MockResponse(200, json_data=MOCK_SEARCH_RESPONSE), {})

    source = Source(
        id=uuid.uuid4(),
        name="Court of Justice of the European Union - Case Law",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://infocuria.curia.europa.eu/tabs/jurisprudence",
    )

    extractor = CuriaCaseLawExtractor()
    entries = await extractor.extract(client, source)

    assert len(entries) == 2
    for e in entries:
        # Strict provenance: no fabricated content or excerpt
        assert e.content is None
        assert e.excerpt is None
        # Metadata is fully preserved
        assert e.external_id is not None
        assert e.external_id.startswith("ECLI:EU:C:")
        assert e.raw_metadata["case_number"] is not None
        assert e.raw_metadata["full_text_available"] is False
        assert e.raw_metadata["content_source"] is None
        assert e.raw_metadata["content_format"] is None
        assert "infocuria_url" in e.raw_metadata


@pytest.mark.asyncio
async def test_curia_extractor_search_failure():
    """Verify ProviderError raised when search API returns 500."""
    client = MockAsyncClient(MockResponse(500, "Internal Server Error"))
    source = Source(
        id=uuid.uuid4(),
        name="Court of Justice of the European Union - Case Law",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://infocuria.curia.europa.eu/tabs/jurisprudence",
    )

    extractor = CuriaCaseLawExtractor()
    with pytest.raises(ProviderError, match="500"):
        await extractor.extract(client, source)


# ==================================================
# 3. NativeProvider Integration
# ==================================================


def test_native_provider_can_handle_curia():
    """Verify NativeProvider accepts curia source."""
    provider = NativeProvider()
    source = Source(
        id=uuid.uuid4(),
        name="Court of Justice of the European Union - Case Law",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://infocuria.curia.europa.eu/tabs/jurisprudence",
    )
    assert provider.can_handle(source) is True


# ==================================================
# 4. IngestionService Integration & Deduplication
# ==================================================


async def mock_curia_network_router(*args, **kwargs):
    url = args[0] if args else kwargs.get("url", "")
    if "search" in url:
        return MockResponse(200, json_data=MOCK_SEARCH_RESPONSE)
    if "325979" in url:
        return MockResponse(200, SAMPLE_OPINION_HTML)
    if "325980" in url:
        return MockResponse(200, SAMPLE_JUDGMENT_HTML)
    return MockResponse(404, "Not found")


@pytest.mark.asyncio
async def test_curia_ingestion_service_e2e(db_session: Session):
    """Verify full ingestion run lifecycle for CJEU / CURIA in SQLite test DB."""
    entity = TrackedEntity(
        id=uuid.uuid4(),
        display_name="El Tribunal de Justicia de la Unión Europea",
        entity_type="institution",
        active=True,
    )
    db_session.add(entity)

    source = Source(
        id=uuid.uuid4(),
        name="Court of Justice of the European Union - Case Law Test",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://infocuria.curia.europa.eu/tabs/jurisprudence",
        active=True,
        category="institutional",
        tracked_entity_id=entity.id,
        config={"initial_fetch_limit": 20, "freshness_warning_hours": 336},
    )
    db_session.add(source)
    db_session.commit()

    service = IngestionService()

    with patch("httpx.AsyncClient.post", side_effect=mock_curia_network_router), \
         patch("httpx.AsyncClient.get", side_effect=mock_curia_network_router):

        # First Run: creates 2 entries
        res1 = await service.ingest_source(source.id, db_session)
        assert res1.status == IngestionRunStatus.SUCCESS.value
        assert res1.fetched == 2
        assert res1.created == 2
        assert res1.duplicates == 0

        entries = db_session.query(Entry).filter(Entry.source_id == source.id).all()
        assert len(entries) == 2
        eclis = {e.external_id for e in entries}
        assert eclis == {"ECLI:EU:C:2026:702", "ECLI:EU:C:2026:690"}
        for e in entries:
            assert e.content_type == "eu_case_law"
            assert e.content is not None
            assert len(e.content) > 50
            # Strict verification of clean text without HTML tags
            assert "<html" not in e.content
            assert "<body" not in e.content
            assert "<p" not in e.content
            assert "<div" not in e.content
            assert "<span" not in e.content
            assert "</" not in e.content
            assert e.raw_metadata["content_source"] == "infocuria_html"
            assert e.raw_metadata["content_format"] == "text/plain"
            assert e.raw_metadata["full_text_available"] is True

        # Second Run: exact duplicates, creates 0
        res2 = await service.ingest_source(source.id, db_session)
        assert res2.status == IngestionRunStatus.SUCCESS.value
        assert res2.fetched == 2
        assert res2.created == 0
        assert res2.duplicates == 2

        entries_after = db_session.query(Entry).filter(Entry.source_id == source.id).all()
        assert len(entries_after) == 2

        # Verify freshness calculation
        status_resp = service.get_source_status(source, db_session)
        assert status_resp.freshness is not None
        assert status_resp.freshness.warning_hours == 336


@pytest.mark.asyncio
async def test_curia_ingestion_service_blob_failure_e2e(db_session: Session):
    """Verify that when the official blob fails, the Entry is still created with content=None and excerpt=None."""
    entity = TrackedEntity(
        id=uuid.uuid4(),
        display_name="El Tribunal de Justicia de la Unión Europea",
        entity_type="institution",
        active=True,
    )
    db_session.add(entity)

    source = Source(
        id=uuid.uuid4(),
        name="Court of Justice of the European Union - Blob Failure Test",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://infocuria.curia.europa.eu/tabs/jurisprudence",
        active=True,
        category="institutional",
        tracked_entity_id=entity.id,
    )
    db_session.add(source)
    db_session.commit()

    service = IngestionService()

    # Search succeeds, but GET blob returns 500
    async def mock_router_blob_down(*args, **kwargs):
        url = args[0] if args else kwargs.get("url", "")
        if "search" in url:
            return MockResponse(200, json_data=MOCK_SEARCH_RESPONSE)
        return MockResponse(500, "Blob Server Error")

    with patch("httpx.AsyncClient.post", side_effect=mock_router_blob_down), \
         patch("httpx.AsyncClient.get", side_effect=mock_router_blob_down):

        res = await service.ingest_source(source.id, db_session)
        assert res.status == IngestionRunStatus.SUCCESS.value
        assert res.fetched == 2
        assert res.created == 2

        entries = db_session.query(Entry).filter(Entry.source_id == source.id).all()
        assert len(entries) == 2
        for e in entries:
            # Strict provenance: no fabricated content or excerpt
            assert e.content is None
            assert e.excerpt is None
            assert e.external_id is not None
            assert e.external_id.startswith("ECLI:EU:C:")
            assert e.raw_metadata["full_text_available"] is False
            assert e.raw_metadata["content_source"] is None
            assert e.raw_metadata["content_format"] is None
            assert "infocuria_url" in e.raw_metadata
            assert "case_number" in e.raw_metadata
