"""Tests for European Commission / DG Competition extractor, provider dispatch, and ingestion."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
import httpx
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.models.tracking import TrackedEntity
from app.providers.base import RawEntryData, ProviderError
from app.providers.native import NativeProvider
from app.providers.extractors.european_commission import (
    EuropeanCommissionExtractor,
    parse_rfc822_date,
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
      <title>Commission sends Statement of Objections over proposed joint venture</title>
      <link>https://competition-policy.ec.europa.eu/about/news/statement-objections-2026-08-26_en</link>
      <description>&lt;p&gt;Statement of objections in proposed joint venture.&lt;/p&gt;</description>
      <pubDate>Fri, 04 Sep 2026 15:22:49 +0200</pubDate>
      <guid>https://competition-policy.ec.europa.eu/about/news/statement-objections-2026-08-26_en</guid>
      <category>Directorate-General for Competition</category>
      <category>mergers</category>
    </item>
  </channel>
</rss>
"""

SAMPLE_NODE_HTML = """<!DOCTYPE html>
<html>
<body>
<main>
  <div class="ecl-container">
    <h1>Commission adopts EU Guidelines on exclusionary abuses of dominance</h1>
    <a href="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1769">Press release on Press Corner</a>
  </div>
</main>
</body>
</html>
"""

SAMPLE_PRESSCORNER_JSON = {
    "ky": 212553,
    "refCd": "IP/26/1769",
    "docuLanguageResource": {
        "title": "Commission adopts EU Guidelines on exclusionary abuses of dominance",
        "htmlContent": "<p>The European Commission has adopted new Guidelines on exclusionary abuses of dominance.</p><p>These Guidelines provide legal certainty for businesses operating in the Single Market.</p>",
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
    assert item1["url"] == "https://competition-policy.ec.europa.eu/about/news/commission-adopts-eu-guidelines-exclusionary-abuses-dominance-2026-09-03_en"
    assert item1["guid"] == item1["url"]
    assert "antitrust" in item1["categories"]
    assert item1["raw_metadata"]["policy_area"] == "antitrust"
    assert item1["published_at"] == datetime(2026, 9, 3, 14, 40, 13, tzinfo=timezone.utc)

    item2 = items[1]
    assert item2["title"] == "Commission approves €4.5 million German State aid for fishing"
    assert item2["url"] == "https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1740"
    assert "state aid" in item2["categories"]
    assert item2["raw_metadata"]["policy_area"] == "state aid"

    item3 = items[2]
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
    ref1 = EuropeanCommissionExtractor._extract_presscorner_ref(url1)
    assert ref1 == ("en", "IP/26/1740")

    url2 = "https://ec.europa.eu/commission/presscorner/detail/fr/mex_26_500"
    ref2 = EuropeanCommissionExtractor._extract_presscorner_ref(url2)
    assert ref2 == ("fr", "MEX/26/500")

    url3 = "https://competition-policy.ec.europa.eu/about/news/some-news_en"
    ref3 = EuropeanCommissionExtractor._extract_presscorner_ref(url3)
    assert ref3 is None


# ==============================================================================
# 2. DETAIL ENRICHMENT & FALLBACK TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_enrichment_via_presscorner_api() -> None:
    """Test full body enrichment directly from Press Corner API."""
    extractor = EuropeanCommissionExtractor()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "presscorner/api/documents" in url:
            import json
            return httpx.Response(200, headers={"Content-Type": "application/json"}, text=json.dumps(SAMPLE_PRESSCORNER_JSON))
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        content, excerpt = await extractor.fetch_content_and_excerpt(
            client,
            "https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1769",
            fallback_description="Fallback desc",
        )

    assert content is not None
    assert "The European Commission has adopted new Guidelines" in content
    assert excerpt == "The European Commission has adopted new Guidelines on exclusionary abuses of dominance."


@pytest.mark.asyncio
async def test_enrichment_via_node_page_linking_to_presscorner() -> None:
    """Test node page HTML parsed for presscorner link then fetched from API."""
    extractor = EuropeanCommissionExtractor()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "competition-policy.ec.europa.eu/about/news" in url:
            return httpx.Response(200, headers={"Content-Type": "text/html"}, text=SAMPLE_NODE_HTML)
        elif "presscorner/api/documents" in url:
            import json
            return httpx.Response(200, headers={"Content-Type": "application/json"}, text=json.dumps(SAMPLE_PRESSCORNER_JSON))
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        content, excerpt = await extractor.fetch_content_and_excerpt(
            client,
            "https://competition-policy.ec.europa.eu/about/news/commission-adopts-eu-guidelines-exclusionary-abuses-dominance-2026-09-03_en",
            fallback_description="Fallback desc",
        )

    assert content is not None
    assert "The European Commission has adopted new Guidelines" in content


@pytest.mark.asyncio
async def test_enrichment_fallback_to_description() -> None:
    """Test graceful fallback to description when network requests fail."""
    extractor = EuropeanCommissionExtractor()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        content, excerpt = await extractor.fetch_content_and_excerpt(
            client,
            "https://competition-policy.ec.europa.eu/about/news/error-news_en",
            fallback_description="<p>Clean fallback description text</p>",
        )

    assert content == "Clean fallback description text"
    assert excerpt == "Clean fallback description text"


class MockResponse:
    def __init__(self, status_code: int = 200, content: bytes = b"", text: str = "", headers: dict = None):
        self.status_code = status_code
        self.content = content or text.encode("utf-8")
        self.text = text or (content.decode("utf-8") if content else "")
        self.headers = headers or {}

    def json(self):
        import json
        return json.loads(self.text)


async def mock_ec_network_get(url, *args, **kwargs):
    url_str = str(url)
    if "node/38/rss_en" in url_str:
        return MockResponse(200, content=SAMPLE_EC_RSS.encode("utf-8"))
    elif "presscorner/api/documents" in url_str:
        import json
        return MockResponse(
            200,
            text=json.dumps(SAMPLE_PRESSCORNER_JSON),
            headers={"content-type": "application/json"},
        )
    return MockResponse(
        200,
        text=SAMPLE_NODE_HTML,
        headers={"content-type": "text/html"},
    )


# ==============================================================================
# 3. NATIVE PROVIDER DISPATCH TEST
# ==============================================================================

@pytest.mark.asyncio
async def test_native_provider_dispatches_to_ec_extractor() -> None:
    """Test that NativeProvider routes European Commission URLs to EuropeanCommissionExtractor."""
    provider = NativeProvider()
    source = Source(
        name="European Commission - Competition Policy",
        type=SourceType.RSS,
        provider="native",
        url="https://competition-policy.ec.europa.eu/node/38/rss_en",
        category="institutional",
        config={"initial_fetch_limit": 5},
    )

    with patch("httpx.AsyncClient.get", side_effect=mock_ec_network_get):
        entries = await provider.fetch_entries(source)

    assert len(entries) == 3
    assert entries[0].title == "Commission adopts EU Guidelines on exclusionary abuses of dominance"
    assert entries[0].language == "en"
    assert entries[0].content_type == "institutional_news"
    assert entries[0].raw_metadata["source_section"] == "Competition Policy"


# ==============================================================================
# 4. INGESTION SERVICE & INGESTION RUN WORKFLOW
# ==============================================================================

@pytest.mark.asyncio
async def test_ec_ingestion_service_e2e_and_deduplication(db_session: Session) -> None:
    """Test full IngestionService flow with EC source: run 1 inserts, run 2 deduplicates."""
    # 1. Create TrackedEntity & Source
    entity = TrackedEntity(
        id=uuid.uuid4(),
        display_name="European Commission Test",
        entity_type="institution",
        active=True,
    )
    db_session.add(entity)

    source = Source(
        id=uuid.uuid4(),
        name="European Commission - Competition Policy Test",
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

    with patch("httpx.AsyncClient.get", side_effect=mock_ec_network_get):
        # RUN 1: First ingestion
        result1 = await service.ingest_source(source.id, db_session)
        assert result1.status == IngestionRunStatus.SUCCESS.value
        assert result1.fetched == 3
        assert result1.created == 3
        assert result1.duplicates == 0

        # Check DB entries
        db_entries = db_session.query(Entry).filter(Entry.source_id == source.id).all()
        assert len(db_entries) == 3

        # Verify IngestionRun recorded
        run1 = db_session.query(IngestionRun).filter(
            IngestionRun.source_id == source.id
        ).order_by(IngestionRun.started_at.desc()).first()
        assert run1 is not None
        assert run1.status == IngestionRunStatus.SUCCESS.value
        assert run1.fetched_count == 3
        assert run1.created_count == 3
        assert run1.duplicate_count == 0

        # RUN 2: Second identical ingestion -> Deduplication verification
        result2 = await service.ingest_source(source.id, db_session)
        assert result2.status == IngestionRunStatus.SUCCESS.value
        assert result2.fetched == 3
        assert result2.created == 0
        assert result2.duplicates == 3

        # Confirm still 3 entries in DB
        db_entries_after = db_session.query(Entry).filter(Entry.source_id == source.id).all()
        assert len(db_entries_after) == 3
