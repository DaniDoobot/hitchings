"""Tests for RSS parsing, IngestionService, deduplication, and entries endpoints."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.providers.native import NativeProvider, parse_rfc822_date
from app.providers.base import RawEntryData, ProviderError
from app.services.ingestion_service import (
    IngestionService,
    compute_content_hash,
    compute_ingestion_dedupe_hash,
)

SAMPLE_RSS_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>RSS CNMC Noticias</title>
    <link>https://www.cnmc.es/</link>
    <description>Canal oficial</description>
    <language>es</language>
    <item>
      <title>La CNMC investiga posibles prácticas anticompetitivas</title>
      <link>https://www.cnmc.es/prensa/investigacion-2026</link>
      <description>Nota informativa sobre el expediente sancionador.</description>
      <pubDate>Tue, 01 Sep 2026 06:08:02 +0000</pubDate>
      <dc:creator>comunicacion_cnmc</dc:creator>
      <guid>422166 at https://www.cnmc.es</guid>
    </item>
    <item>
      <title>Resolución sobre concentraciones empresariales</title>
      <link>https://www.cnmc.es/prensa/concentracion-2026</link>
      <description>Autorización condicionada de la operación.</description>
      <pubDate>Mon, 31 Aug 2026 11:39:10 +0000</pubDate>
      <dc:creator>alobo</dc:creator>
      <guid>421983 at https://www.cnmc.es</guid>
    </item>
  </channel>
</rss>
"""


# ==============================================================================
# 1. RSS PARSING & NORMALIZATION TESTS
# ==============================================================================

def test_parse_rfc822_date() -> None:
    """Test RFC 822 date parsing into timezone-aware UTC datetime."""
    dt = parse_rfc822_date("Tue, 01 Sep 2026 06:08:02 +0000")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 9
    assert dt.day == 1
    assert dt.tzinfo == timezone.utc

    invalid_dt = parse_rfc822_date("invalid date string")
    assert invalid_dt is None


@pytest.mark.asyncio
async def test_native_rss_fetch_and_parse() -> None:
    """Test that NativeProvider correctly parses RSS XML into RawEntryData list."""
    provider = NativeProvider()
    source = Source(
        name="CNMC Test Feed",
        type=SourceType.RSS,
        url="https://www.cnmc.es/feed/prensa/noticias",
        category="institutional",
        config={"initial_fetch_limit": 10},
    )

    class MockResponse:
        status_code = 200
        content = SAMPLE_RSS_XML.encode("utf-8")

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = MockResponse()
        entries = await provider.fetch_entries(source)

    assert len(entries) == 2
    item1 = entries[0]
    assert item1.title == "La CNMC investiga posibles prácticas anticompetitivas"
    assert item1.url == "https://www.cnmc.es/prensa/investigacion-2026"
    assert item1.external_id == "422166 at https://www.cnmc.es"
    assert item1.author == "comunicacion_cnmc"
    assert item1.language == "es"
    assert item1.content_type == "institutional_news"
    assert item1.published_at == datetime(2026, 9, 1, 6, 8, 2, tzinfo=timezone.utc)


# ==============================================================================
# 2. INGESTION SERVICE & DEDUPLICATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_ingestion_service_deduplication(db_session: Session) -> None:
    """Test full ingestion workflow and verify that a second run creates 0 duplicates."""
    source = Source(
        name="CNMC Mock Source",
        type=SourceType.RSS,
        url="https://www.cnmc.es/feed/prensa/noticias",
        category="institutional",
    )
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)

    mock_raw_entries = [
        RawEntryData(
            url="https://www.cnmc.es/prensa/noticia-1",
            title="Noticia 1",
            excerpt="Resumen 1",
            external_id="guid-1",
            author="author1",
            published_at=datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
            content_type="institutional_news",
        ),
        RawEntryData(
            url="https://www.cnmc.es/prensa/noticia-2",
            title="Noticia 2",
            excerpt="Resumen 2",
            external_id="guid-2",
            author="author2",
            published_at=datetime(2026, 9, 1, 11, 0, 0, tzinfo=timezone.utc),
            content_type="institutional_news",
        ),
    ]

    service = IngestionService()

    with patch.object(NativeProvider, "fetch_entries", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_raw_entries

        # First run: should create 2 entries
        res1 = await service.ingest_source(source.id, db_session)
        assert res1.fetched == 2
        assert res1.created == 2
        assert res1.duplicates == 0

        # Verify persisted entries
        db_entries = db_session.execute(
            select(Entry).where(Entry.source_id == source.id)
        ).scalars().all()
        assert len(db_entries) == 2

        # Verify timestamps updated on source
        db_session.refresh(source)
        assert source.last_run_at is not None
        assert source.last_success_at is not None

        # Second run with exact same entries: should create 0 entries and report 2 duplicates
        res2 = await service.ingest_source(source.id, db_session)
        assert res2.fetched == 2
        assert res2.created == 0
        assert res2.duplicates == 2

        # Verify table row count did not increase
        total_entries = db_session.execute(
            select(Entry).where(Entry.source_id == source.id)
        ).scalars().all()
        assert len(total_entries) == 2


# ==============================================================================
# 3. MANUAL INGEST ENDPOINT TESTS
# ==============================================================================

def test_manual_ingest_endpoint(client: TestClient) -> None:
    """Test POST /api/v1/sources/{id}/ingest manual trigger endpoint."""
    # Create source
    src_resp = client.post(
        "/api/v1/sources",
        json={
            "name": "CNMC API Ingest Source",
            "type": "rss",
            "url": "https://www.cnmc.es/feed/prensa/noticias",
            "provider": "native",
        }
    )
    assert src_resp.status_code == status.HTTP_201_CREATED
    source_id = src_resp.json()["id"]

    mock_entries = [
        RawEntryData(
            url="https://www.cnmc.es/prensa/test-endpoint",
            title="Título Endpoint",
            external_id="guid-api-1",
            published_at=datetime(2026, 9, 2, 8, 0, 0, tzinfo=timezone.utc),
        )
    ]

    with patch.object(NativeProvider, "fetch_entries", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_entries

        # First trigger
        ingest_resp = client.post(f"/api/v1/sources/{source_id}/ingest")
        assert ingest_resp.status_code == status.HTTP_200_OK
        data = ingest_resp.json()
        assert data["fetched"] == 1
        assert data["created"] == 1
        assert data["duplicates"] == 0

        # Second trigger
        ingest_resp2 = client.post(f"/api/v1/sources/{source_id}/ingest")
        assert ingest_resp2.status_code == status.HTTP_200_OK
        data2 = ingest_resp2.json()
        assert data2["fetched"] == 1
        assert data2["created"] == 0
        assert data2["duplicates"] == 1


# ==============================================================================
# 4. GET ENTRIES ENDPOINTS TESTS
# ==============================================================================

def test_get_entries_endpoint(client: TestClient, db_session: Session) -> None:
    """Test GET /api/v1/entries and GET /api/v1/entries/{id} read-only endpoints."""
    # Create source
    source = Source(name="Entries Read Source", type=SourceType.RSS, provider="native")
    db_session.add(source)
    db_session.flush()

    # Create 2 entries
    e1 = Entry(
        source_id=source.id,
        url="https://example.com/e1",
        title="Entrada Más Reciente",
        published_at=datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    e2 = Entry(
        source_id=source.id,
        url="https://example.com/e2",
        title="Entrada Antigua",
        published_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add_all([e1, e2])
    db_session.commit()

    # List entries
    list_resp = client.get(f"/api/v1/entries?source_id={source.id}")
    assert list_resp.status_code == status.HTTP_200_OK
    entries = list_resp.json()
    assert len(entries) == 2
    # Verify descending ordering by published_at
    assert entries[0]["title"] == "Entrada Más Reciente"
    assert entries[1]["title"] == "Entrada Antigua"

    # Single entry
    detail_resp = client.get(f"/api/v1/entries/{e1.id}")
    assert detail_resp.status_code == status.HTTP_200_OK
    assert detail_resp.json()["id"] == str(e1.id)
    assert detail_resp.json()["url"] == "https://example.com/e1"


# ==============================================================================
# 5. ERROR HANDLING TESTS
# ==============================================================================

def test_ingestion_failure_handling(client: TestClient) -> None:
    """Test that provider failure returns 502 Bad Gateway without crashing API."""
    src_resp = client.post(
        "/api/v1/sources",
        json={
            "name": "Failing Source",
            "type": "rss",
            "url": "https://invalid-host-that-fails.com/rss",
            "provider": "native",
        }
    )
    source_id = src_resp.json()["id"]

    with patch.object(NativeProvider, "fetch_entries", side_effect=ProviderError("Connection timeout")):
        resp = client.post(f"/api/v1/sources/{source_id}/ingest")
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY
        assert "Connection timeout" in resp.json()["detail"]


# ==============================================================================
# 6. CNMC HTML EXTRACTOR DETERMINISTIC TESTS
# ==============================================================================

SAMPLE_CNMC_LISTING_HTML = """<!DOCTYPE html>
<html>
<body>
  <div class="views-row">
    <div class="views-field views-field-created-1">
      <span class="field-content">
        <time class="datetime" datetime="2026-09-01T08:08:02+02:00">01 Sep 2026</time>
      </span>
    </div>
    <span class="views-field views-field-title">
      <span class="field-content">
        <a href="/prensa/registro-alias-llamamiento-20260901?back=news" hreflang="es">
          La CNMC insta a las empresas a registrar sus alias
        </a>
      </span>
    </span>
    <div class="views-field views-field-field-tags">
      <div class="field-content text-secondary">Telecomunicaciones</div>
    </div>
  </div>
  <div class="views-row">
    <div class="views-field views-field-created-1">
      <span class="field-content">
        <time class="datetime" datetime="2026-08-31T13:39:10+02:00">31 Ago 2026</time>
      </span>
    </div>
    <span class="views-field views-field-title">
      <span class="field-content">
        <a href="/prensa/dcoor-96-rondas-20260831?back=news" hreflang="es">
          La CNMC aprueba la modificación de las Reglas del Mercado
        </a>
      </span>
    </span>
    <div class="views-field views-field-field-tags">
      <div class="field-content text-secondary">Energía</div>
    </div>
  </div>
</body>
</html>
"""

SAMPLE_CNMC_ARTICLE_HTML = """<!DOCTYPE html>
<html>
<body>
  <header><nav>Menú de navegación que debe ignorarse</nav></header>
  <main class="main-content">
    <h1>La CNMC insta a las empresas a registrar sus alias</h1>
    <div class="page-nw-article-body">
      <div class="field--name-body">
        <ul>
          <li>Primer punto clave de la noticia sobre alias.</li>
        </ul>
        <p>La Comisión Nacional de los Mercados y la Competencia (CNMC) recuerda a las empresas...</p>
        <p>Texto adicional del cuerpo de la noticia con detalles técnicos.</p>
      </div>
    </div>
  </main>
  <footer>Pie de página que debe ignorarse</footer>
</body>
</html>
"""


def test_cnmc_extractor_listing_parsing() -> None:
    """Test deterministic HTML parsing of CNMC listing page."""
    from app.providers.extractors.cnmc import CNMCNewsExtractor

    extractor = CNMCNewsExtractor()
    items = extractor.parse_listing_page(SAMPLE_CNMC_LISTING_HTML, "https://www.cnmc.es/prensa/noticias")

    assert len(items) == 2

    item1 = items[0]
    assert item1["title"] == "La CNMC insta a las empresas a registrar sus alias"
    assert item1["url"] == "https://www.cnmc.es/prensa/registro-alias-llamamiento-20260901"
    assert item1["sector"] == "Telecomunicaciones"
    assert item1["published_at"] == datetime(2026, 9, 1, 6, 8, 2, tzinfo=timezone.utc)
    assert item1["raw_metadata"] == {
        "sector": "Telecomunicaciones",
        "source_page": "https://www.cnmc.es/prensa/noticias",
    }

    item2 = items[1]
    assert item2["title"] == "La CNMC aprueba la modificación de las Reglas del Mercado"
    assert item2["url"] == "https://www.cnmc.es/prensa/dcoor-96-rondas-20260831"
    assert item2["sector"] == "Energía"
    assert item2["published_at"] == datetime(2026, 8, 31, 11, 39, 10, tzinfo=timezone.utc)


def test_cnmc_extractor_article_body_parsing() -> None:
    """Test clean extraction of article body text and excerpt without boilerplate."""
    from app.providers.extractors.cnmc import CNMCNewsExtractor

    extractor = CNMCNewsExtractor()
    content, excerpt = extractor.parse_article_body(SAMPLE_CNMC_ARTICLE_HTML)

    assert content is not None
    assert "Primer punto clave de la noticia sobre alias" in content
    assert "La Comisión Nacional de los Mercados y la Competencia" in content
    assert "Texto adicional del cuerpo de la noticia" in content

    # Verify no navigation or footer text is captured
    assert "Menú de navegación" not in content
    assert "Pie de página" not in content

    # Verify excerpt
    assert excerpt is not None
    assert "La Comisión Nacional de los Mercados y la Competencia" in excerpt


def test_cnmc_extractor_malformed_html() -> None:
    """Test that unexpected or malformed HTML does not raise exceptions."""
    from app.providers.extractors.cnmc import CNMCNewsExtractor

    extractor = CNMCNewsExtractor()

    # Empty HTML
    assert extractor.parse_listing_page("", "https://www.cnmc.es/prensa/noticias") == []

    # HTML with rows but no links
    broken_html = '<div class="views-row"><p>Sin enlaces</p></div>'
    assert extractor.parse_listing_page(broken_html, "https://www.cnmc.es/prensa/noticias") == []

    # Body parsing with missing container
    c, e = extractor.parse_article_body("<html><body><p>Sin artículo</p></body></html>")
    assert c is None
    assert e is None


@pytest.mark.asyncio
async def test_cnmc_website_native_provider_integration() -> None:
    """Test NativeProvider end-to-end with website source and CNMC extractor dispatch."""
    import httpx

    source = Source(
        name="CNMC - Noticias",
        type=SourceType.WEBSITE,
        provider="native",
        url="https://www.cnmc.es/prensa/noticias",
        config={"initial_fetch_limit": 2},
    )

    provider = NativeProvider()
    assert provider.can_handle(source) is True

    class MockListingResponse:
        status_code = 200
        text = SAMPLE_CNMC_LISTING_HTML

    class MockArticleResponse:
        status_code = 200
        text = SAMPLE_CNMC_ARTICLE_HTML

    async def mock_get(self, url, *args, **kwargs):
        if "page=" in url or url == "https://www.cnmc.es/prensa/noticias":
            return MockListingResponse()
        return MockArticleResponse()

    with patch.object(httpx.AsyncClient, "get", new=mock_get):
        entries = await provider.fetch_entries(source)

    assert len(entries) == 2
    assert entries[0].title == "La CNMC insta a las empresas a registrar sus alias"
    assert entries[0].url == "https://www.cnmc.es/prensa/registro-alias-llamamiento-20260901"
    assert entries[0].content is not None
    assert "Primer punto clave" in entries[0].content
    assert entries[0].raw_metadata["sector"] == "Telecomunicaciones"
    assert entries[0].external_id == "https://www.cnmc.es/prensa/registro-alias-llamamiento-20260901"


def test_compute_ingestion_dedupe_hash_consistency():
    """Verify compute_ingestion_dedupe_hash and compute_content_hash backwards compat."""
    h1 = compute_ingestion_dedupe_hash("Title", "https://example.com/test", "Excerpt")
    h2 = compute_content_hash("Title", "https://example.com/test", "Excerpt")
    assert h1 == h2
    assert len(h1) == 64


