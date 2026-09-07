"""Tests for European Commission / DG Competition extractor, editorial date hierarchy, and ingestion."""

import uuid
from datetime import datetime, timezone
import pytest
import httpx
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.models.tracking import TrackedEntity
from app.providers.native import NativeProvider
from app.providers.extractors.european_commission import (
    EuropeanCommissionExtractor,
    parse_rfc822_date,
    parse_iso_datetime,
    parse_human_date,
    parse_slug_date,
)
from app.services.ingestion_service import IngestionService

SAMPLE_EC_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Competition Policy | News</title>
    <link>https://competition-policy.ec.europa.eu</link>
    <description>Competition Policy news from the European Commission</description>
    <language>en</language>
    <item>
      <title>Commission adopts EU Guidelines on exclusionary abuses of dominance</title>
      <link>https://competition-policy.ec.europa.eu/about/news/commission-adopts-eu-guidelines-exclusionary-abuses-dominance-2026-09-03_en</link>
      <description>&lt;p&gt;Commission adopts EU Guidelines on exclusionary abuses of dominance&lt;/p&gt;</description>
      <pubDate>Thu, 03 Sep 2026 16:40:13 +0200</pubDate>
      <guid>https://competition-policy.ec.europa.eu/about/news/commission-adopts-eu-guidelines-exclusionary-abuses-dominance-2026-09-03_en</guid>
      <category>Directorate-General for Competition</category>
      <category>antitrust</category>
      <category>Press release</category>
      <category>competition policy</category>
    </item>
    <item>
      <title>Commission approves €4.5 million German State aid for fishing</title>
      <link>https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1740</link>
      <description>&lt;p&gt;The European Commission has approved a €4.5 million German State aid scheme.&lt;/p&gt;</description>
      <pubDate>Fri, 21 Aug 2026 12:18:23 +0200</pubDate>
      <guid>https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1740</guid>
      <category>Directorate-General for Competition</category>
      <category>state aid</category>
      <category>Press release</category>
    </item>
    <item>
      <title>Commission sends Statement of Objections over proposed joint venture between UPM and Sappi</title>
      <link>https://competition-policy.ec.europa.eu/about/news/commission-sends-statement-objections-over-proposed-joint-venture-between-upm-and-sappi-2026-08-26_en</link>
      <description>&lt;p&gt;Statement of objections in proposed joint venture.&lt;/p&gt;</description>
      <pubDate>Fri, 04 Sep 2026 15:22:49 +0200</pubDate>
      <guid>https://competition-policy.ec.europa.eu/about/news/commission-sends-statement-objections-over-proposed-joint-venture-between-upm-and-sappi-2026-08-26_en</guid>
      <category>Directorate-General for Competition</category>
      <category>mergers</category>
    </item>
  </channel>
</rss>
"""

SAMPLE_NODE_HTML_UPM = """<!DOCTYPE html>
<html>
<head>
  <meta property="og:updated_time" content="2026-08-26T14:00:00+0200" />
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "name": "Commission sends Statement of Objections over proposed joint venture between UPM and Sappi",
    "datePublished": "2026-08-26T14:00:00+02:00"
  }
  </script>
</head>
<body>
<main>
  <div class="ecl-container">
    <h1>Commission sends Statement of Objections over proposed joint venture between UPM and Sappi</h1>
    <div class="field-publication-date">Publication date 26 August 2026</div>
    <a href="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1747">Press release on Press Corner</a>
  </div>
</main>
</body>
</html>
"""

SAMPLE_PRESSCORNER_JSON_UPM = {
    "ky": 212553,
    "refCd": "IP/26/1747",
    "publishDate": "2026-08-26T10:00:10.000+02:00",
    "eventDate": "2026-08-26",
    "docuLanguageResource": {
        "title": "Commission sends Statement of Objections over proposed joint venture between UPM and Sappi",
        "htmlContent": "<p>The European Commission has informed UPM and Sappi of its preliminary view.</p>",
    },
}

SAMPLE_PRESSCORNER_JSON_GUIDELINES = {
    "ky": 212550,
    "refCd": "IP/26/1769",
    "publishDate": "2026-09-03T16:30:07.000+02:00",
    "eventDate": "2026-09-03",
    "docuLanguageResource": {
        "title": "Commission adopts EU Guidelines on exclusionary abuses of dominance",
        "htmlContent": "<p>The European Commission has adopted new Guidelines on exclusionary abuses of dominance.</p>",
    },
}

SAMPLE_PRESSCORNER_JSON_GERMAN = {
    "ky": 212540,
    "refCd": "IP/26/1740",
    "publishDate": "2026-08-21T11:45:53.000+02:00",
    "eventDate": "2026-08-21",
    "docuLanguageResource": {
        "title": "Commission approves German State aid for fishing",
        "htmlContent": "<p>The European Commission has approved a German State aid scheme.</p>",
    },
}


# ==============================================================================
# 1. PARSING & EXTRACTION TESTS
# ==============================================================================

def test_ec_parse_feed_structure() -> None:
    """Test parsing RSS XML structure, categories, and policy area detection."""
    extractor = EuropeanCommissionExtractor()
    items = extractor.parse_feed(
        SAMPLE_EC_RSS.encode("utf-8"),
        limit=10,
        feed_url="https://competition-policy.ec.europa.eu/node/38/rss_en",
    )

    assert len(items) == 3

    item1 = items[0]
    assert item1["title"] == "Commission adopts EU Guidelines on exclusionary abuses of dominance"
    assert "antitrust" in item1["categories"]
    assert item1["raw_metadata"]["policy_area"] == "antitrust"
    assert item1["rss_pub_date"] == datetime(2026, 9, 3, 14, 40, 13, tzinfo=timezone.utc)

    item3 = items[2]
    assert item3["title"] == "Commission sends Statement of Objections over proposed joint venture between UPM and Sappi"
    # Notice RSS pubDate is 04 Sep 2026
    assert item3["rss_pub_date"] == datetime(2026, 9, 4, 13, 22, 49, tzinfo=timezone.utc)
    assert item3["raw_metadata"]["policy_area"] == "mergers"


def test_ec_parse_feed_limit() -> None:
    """Test that the limit parameter restricts the parsed items count."""
    extractor = EuropeanCommissionExtractor()
    items = extractor.parse_feed(
        SAMPLE_EC_RSS.encode("utf-8"),
        limit=2,
        feed_url="https://competition-policy.ec.europa.eu/node/38/rss_en",
    )
    assert len(items) == 2


def test_extract_presscorner_ref() -> None:
    """Test regex extraction and normalization of Press Corner references."""
    url1 = "https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1740"
    assert EuropeanCommissionExtractor._extract_presscorner_ref(url1) == ("en", "IP/26/1740")

    url2 = "https://ec.europa.eu/commission/presscorner/detail/fr/mex_26_500"
    assert EuropeanCommissionExtractor._extract_presscorner_ref(url2) == ("fr", "MEX/26/500")

    url3 = "https://competition-policy.ec.europa.eu/about/news/some-news_en"
    assert EuropeanCommissionExtractor._extract_presscorner_ref(url3) is None


# ==============================================================================
# 2. DATE HIERARCHY TESTS
# ==============================================================================

def test_date_parsing_helpers() -> None:
    """Test ISO, RFC822, human readable, and URL slug date parsers."""
    iso_dt = parse_iso_datetime("2026-08-26T10:00:10.000+02:00")
    assert iso_dt == datetime(2026, 8, 26, 8, 0, 10, tzinfo=timezone.utc)

    human_dt = parse_human_date("26 August 2026")
    assert human_dt == datetime(2026, 8, 26, 0, 0, 0, tzinfo=timezone.utc)

    slug_dt = parse_slug_date("https://competition-policy.ec.europa.eu/news/objections-2026-08-26_en")
    assert slug_dt == datetime(2026, 8, 26, 0, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_date_hierarchy_presscorner_api_publish_date() -> None:
    """Test Priority 1: Press Corner API publishDate overrides other dates."""
    extractor = EuropeanCommissionExtractor()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        import json
        return httpx.Response(200, headers={"Content-Type": "application/json"}, text=json.dumps(SAMPLE_PRESSCORNER_JSON_UPM))

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        enrichment = await extractor.fetch_enrichment(
            client,
            "https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1747",
            fallback_description="Desc",
            rss_pub_date=datetime(2026, 9, 4, 15, 22, 49, tzinfo=timezone.utc),
        )

    assert enrichment.date_source == "presscorner_api"
    assert enrichment.editorial_date == datetime(2026, 8, 26, 8, 0, 10, tzinfo=timezone.utc)
    assert "UPM and Sappi" in (enrichment.content or "")


@pytest.mark.asyncio
async def test_date_hierarchy_node_jsonld_fallback() -> None:
    """Test Priority 2: Node page JSON-LD datePublished is used when Press Corner API is unavailable."""
    extractor = EuropeanCommissionExtractor()

    node_html_no_pc = """<!DOCTYPE html>
    <html>
    <head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "datePublished": "2026-08-26T14:00:00+02:00"
      }
      </script>
    </head>
    <body><main><p>Internal node content without presscorner.</p></main></body>
    </html>"""

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text=node_html_no_pc)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        enrichment = await extractor.fetch_enrichment(
            client,
            "https://competition-policy.ec.europa.eu/about/news/internal-node",
            fallback_description="Desc",
            rss_pub_date=datetime(2026, 9, 4, 15, 22, 49, tzinfo=timezone.utc),
        )

    assert enrichment.date_source == "node_jsonld"
    assert enrichment.editorial_date == datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_date_hierarchy_url_slug_fallback() -> None:
    """Test Priority 4: URL slug date is used when node page has no date."""
    extractor = EuropeanCommissionExtractor()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text="<main><p>Text</p></main>")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        enrichment = await extractor.fetch_enrichment(
            client,
            "https://competition-policy.ec.europa.eu/about/news/statement-objections-2026-08-26_en",
            fallback_description="Desc",
            rss_pub_date=datetime(2026, 9, 4, 15, 22, 49, tzinfo=timezone.utc),
        )

    assert enrichment.date_source == "url_slug"
    assert enrichment.editorial_date == datetime(2026, 8, 26, 0, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_date_hierarchy_rss_pubdate_last_resort() -> None:
    """Test Priority 5: Fallback to RSS pubDate when no other date can be extracted."""
    extractor = EuropeanCommissionExtractor()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    rss_date = datetime(2026, 9, 4, 15, 22, 49, tzinfo=timezone.utc)
    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        enrichment = await extractor.fetch_enrichment(
            client,
            "https://example.com/undated-page",
            fallback_description="Desc",
            rss_pub_date=rss_date,
        )

    assert enrichment.date_source == "rss_pubdate"
    assert enrichment.editorial_date == rss_date


# ==============================================================================
# 3. NATIVE PROVIDER DISPATCH & E2E DEDUPLICATION TESTS
# ==============================================================================

class MockResponse:
    def __init__(self, status_code: int = 200, content: bytes = b"", text: str = "", headers: dict = None):
        self.status_code = status_code
        self.content = content or text.encode("utf-8")
        self.text = text or (content.decode("utf-8") if content else "")
        self.headers = headers or {}

    def json(self):
        import json
        return json.loads(self.text)


async def mock_ec_network_router(url, *args, **kwargs):
    import json
    url_str = str(url)
    if "node/38/rss_en" in url_str:
        return MockResponse(200, content=SAMPLE_EC_RSS.encode("utf-8"))
    elif "presscorner/api/documents" in url_str:
        if "1747" in url_str:
            return MockResponse(200, text=json.dumps(SAMPLE_PRESSCORNER_JSON_UPM), headers={"content-type": "application/json"})
        elif "1740" in url_str:
            return MockResponse(200, text=json.dumps(SAMPLE_PRESSCORNER_JSON_GERMAN), headers={"content-type": "application/json"})
        return MockResponse(200, text=json.dumps(SAMPLE_PRESSCORNER_JSON_GUIDELINES), headers={"content-type": "application/json"})
    elif "commission-sends-statement" in url_str:
        return MockResponse(200, text=SAMPLE_NODE_HTML_UPM, headers={"content-type": "text/html"})
    elif "commission-adopts-eu-guidelines" in url_str:
        node_html_gl = """<main><div class="ecl-container"><a href="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1769">Press release</a></div></main>"""
        return MockResponse(200, text=node_html_gl, headers={"content-type": "text/html"})
    return MockResponse(200, text="<main><p>Default body text.</p></main>", headers={"content-type": "text/html"})


@pytest.mark.asyncio
async def test_native_provider_extracts_real_editorial_dates() -> None:
    """Test that NativeProvider dispatches to EC extractor and resolves real editorial dates."""
    provider = NativeProvider()
    source = Source(
        name="European Commission - Competition Policy",
        type=SourceType.RSS,
        provider="native",
        url="https://competition-policy.ec.europa.eu/node/38/rss_en",
        category="institutional",
        config={"initial_fetch_limit": 5},
    )

    from unittest.mock import patch
    with patch("httpx.AsyncClient.get", side_effect=mock_ec_network_router):
        entries = await provider.fetch_entries(source)

    assert len(entries) == 3

    # Item 3: UPM & Sappi had RSS pubDate 04 Sep 2026, but real date is 26 Aug 2026!
    upm_entry = next(e for e in entries if "UPM and Sappi" in (e.title or ""))
    assert upm_entry.published_at == datetime(2026, 8, 26, 8, 0, 10, tzinfo=timezone.utc)
    assert upm_entry.raw_metadata["publication_date_source"] == "presscorner_api"
    assert "2026-09-04" in upm_entry.raw_metadata["rss_pub_date"]


@pytest.mark.asyncio
async def test_ec_ingestion_service_e2e_ordering_and_freshness(db_session: Session) -> None:
    """Test full IngestionService flow with EC source: verify ordering and freshness calculation."""
    entity = TrackedEntity(
        id=uuid.uuid4(),
        display_name="European Commission Test Date",
        entity_type="institution",
        active=True,
    )
    db_session.add(entity)

    source = Source(
        id=uuid.uuid4(),
        name="European Commission - Competition Policy Test Date",
        type=SourceType.RSS,
        provider="native",
        url="https://competition-policy.ec.europa.eu/node/38/rss_en",
        active=True,
        category="institutional",
        tracked_entity_id=entity.id,
        config={"initial_fetch_limit": 20, "freshness_warning_hours": 168},
    )
    db_session.add(source)
    db_session.commit()

    service = IngestionService()

    from unittest.mock import patch
    with patch("httpx.AsyncClient.get", side_effect=mock_ec_network_router):
        result = await service.ingest_source(source.id, db_session)
        assert result.status == IngestionRunStatus.SUCCESS.value
        assert result.fetched == 3
        assert result.created == 3

        # Verify latest_published_at reflects real editorial date (03 Sep 2026, NOT fake 04 Sep)
        assert result.latest_published_at == datetime(2026, 9, 3, 14, 30, 7, tzinfo=timezone.utc)
        assert result.oldest_published_at == datetime(2026, 8, 21, 9, 45, 53, tzinfo=timezone.utc)

        # Verify entries ordered by published_at DESC in database
        entries = db_session.query(Entry).filter(
            Entry.source_id == source.id
        ).order_by(Entry.published_at.desc()).all()

        assert len(entries) == 3

        def as_utc(dt):
            return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt

        # First entry: Guidelines (03 Sep 2026)
        assert "Guidelines" in entries[0].title
        assert as_utc(entries[0].published_at) == datetime(2026, 9, 3, 14, 30, 7, tzinfo=timezone.utc)

        # Second entry: UPM & Sappi (26 Aug 2026) - placed AFTER Guidelines!
        assert "UPM and Sappi" in entries[1].title
        assert as_utc(entries[1].published_at) == datetime(2026, 8, 26, 8, 0, 10, tzinfo=timezone.utc)

        # Third entry: German fishing (21 Aug 2026)
        assert "German" in entries[2].title
        assert as_utc(entries[2].published_at) == datetime(2026, 8, 21, 9, 45, 53, tzinfo=timezone.utc)
