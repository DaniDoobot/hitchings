"""Unit and integration tests for Direct Web Sources (Bloque 9B).

All network calls are strictly mocked with httpx.MockTransport.
Zero internet calls, zero Gemini calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.url_utils import normalize_url
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity
from app.providers.direct_web.adapters.almacen_derecho import AlmacenDerechoAdapter
from app.providers.direct_web.adapters.chillin_competition import ChillinCompetitionAdapter
from app.providers.direct_web.adapters.kluwer_competition import KluwerCompetitionAdapter
from app.providers.direct_web.base import DirectWebExtractionError
from app.providers.direct_web.html_cleaner import (
    clean_editorial_html,
    extract_canonical_url,
    extract_structured_author,
    extract_structured_date,
)
from app.providers.direct_web.models import DiscoveredItem
from app.providers.direct_web.registry import (
    DirectWebAdapterRegistry,
    DirectWebUnknownAdapterError,
)
from app.services.direct_web_ingestion_service import (
    DirectWebIngestionService,
)
from app.services.ingestion_service import compute_ingestion_dedupe_hash
from app.services.source_sufficiency_service import SourceSufficiencyLevel


# ==============================================================================
# FIXTURES & MOCK DATA
# ==============================================================================

MOCK_KLUWER_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Kluwer Competition Law Blog</title>
    <item>
      <title>The Danish Wolt Decision: Abuse of Dominance Explored</title>
      <link>https://legalblogs.wolterskluwer.com/competition-blog/the-danish-wolt-decision/?utm_source=rss</link>
      <guid>https://legalblogs.wolterskluwer.com/competition-blog/?p=12345</guid>
      <pubDate>2026-09-07T08:00:00.000Z</pubDate>
      <dc:creator>Christian Bergqvist</dc:creator>
      <description>Analysis of the Danish competition council decision regarding Wolt platform.</description>
    </item>
  </channel>
</rss>"""

MOCK_KLUWER_HTML = """<!DOCTYPE html>
<html>
<head>
  <link rel="canonical" href="https://legalblogs.wolterskluwer.com/competition-blog/the-danish-wolt-decision/" />
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Article",
    "headline": "The Danish Wolt Decision",
    "datePublished": "2026-09-07T08:00:00.000Z",
    "author": {"@type": "Person", "name": "Christian Bergqvist"}
  }
  </script>
</head>
<body>
  <div class="cg3-main-article-section-cstm">
    <p>The meal-ordering platform Wolt has been found to have infringed Article 102 TFEU in Denmark on three separate accounts of abuse between January 2022 and December 2024.</p>
    <p>The first concerned the use of narrow-price-parity clauses, while the latter two involved unfair contractual terms.</p>
    <h2>Market Definition</h2>
    <p>Although the dominance finding is based on substantial market share, the Danish authority carefully analyzed local competitive dynamics.</p>
  </div>
</body>
</html>"""

MOCK_CHILLIN_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Chillin'Competition</title>
    <item>
      <title>The hack-trick of football judgments and Article 101(1) TFEU</title>
      <link>https://chillingcompetition.com/2026/07/31/football-judgments/?utm_source=feed</link>
      <guid>https://chillingcompetition.com/?p=9988</guid>
      <pubDate>Fri, 31 Jul 2026 15:26:19 +0000</pubDate>
      <dc:creator>Pablo Ibanez Colomo</dc:creator>
      <description>The Court of Justice has recently delivered three consequential judgments revolving around football.</description>
    </item>
  </channel>
</rss>"""

MOCK_CHILLIN_HTML = """<!DOCTYPE html>
<html>
<head>
  <link rel="canonical" href="https://chillingcompetition.com/2026/07/31/football-judgments/" />
  <meta property="article:published_time" content="2026-07-31T15:26:19+00:00" />
  <meta name="author" content="Pablo Ibanez Colomo" />
</head>
<body>
  <div class="post">
    <h1>The hack-trick of football judgments and Article 101(1) TFEU</h1>
    <div class="sd-sharing">Share this: Twitter Facebook</div>
    <p>The Court of Justice has recently delivered three consequential judgments, from a competition law perspective, revolving around the regulation of football.</p>
    <p>Tondela came in April, followed by ROGON and Superleague appeals.</p>
    <div class="jp-relatedposts">Related posts here</div>
  </div>
</body>
</html>"""

MOCK_ALMACEN_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Almacén de Derecho</title>
    <item>
      <title>El primer gran caso de daños por cártel en España</title>
      <link>https://almacendederecho.org/el-primer-gran-caso-de-danos-por-cartel-en-espana?ref=feed</link>
      <guid>https://almacendederecho.org/?p=5544</guid>
      <pubDate>Wed, 15 Jul 2026 05:00:19 +0000</pubDate>
      <dc:creator>Francisco Marcos</dc:creator>
      <description>Las primeras indemnizaciones por los daños causados por un cártel en España.</description>
    </item>
  </channel>
</rss>"""

MOCK_ALMACEN_HTML = """<!DOCTYPE html>
<html>
<head>
  <link rel="canonical" href="https://almacendederecho.org/el-primer-gran-caso-de-danos-por-cartel-en-espana" />
  <meta property="article:published_time" content="2026-07-15T05:00:19+00:00" />
</head>
<body>
  <div class="entry-content">
    <p>Por Francisco Marcos</p>
    <p>Las primeras indemnizaciones por los daños causados por un cártel en España llegaron con el cártel del azúcar.</p>
    <p>Aquellos litigios fueron pioneros. La resolución sancionadora de la autoridad de competencia era anterior a la Ley de 2007.</p>
    <div class="sharedaddy">Compartir esto</div>
  </div>
</body>
</html>"""


# ==============================================================================
# 1. REGISTRY & DISCOVERY TESTS
# ==============================================================================

def test_registry_resolution():
    """Verify registry resolves adapters strictly by code and by explicit Source config."""
    adapter = DirectWebAdapterRegistry.get_adapter("kluwer_competition")
    assert isinstance(adapter, KluwerCompetitionAdapter)

    adapter2 = DirectWebAdapterRegistry.get_adapter("chillin_competition")
    assert isinstance(adapter2, ChillinCompetitionAdapter)

    adapter3 = DirectWebAdapterRegistry.get_adapter("almacen_derecho")
    assert isinstance(adapter3, AlmacenDerechoAdapter)

    # Resolution by Source config
    src = Source(
        name="Custom Kluwer Feed",
        type=SourceType.BLOG,
        config={"adapter": "kluwer_competition"},
    )
    assert isinstance(DirectWebAdapterRegistry.get_adapter_for_source(src), KluwerCompetitionAdapter)


def test_rename_source_resolves_adapter():
    """Verify renaming a source completely does not break dispatch if config.adapter is present (Section 11)."""
    renamed_source = Source(
        name="Totally Arbitrary Renamed EU Law Portal",
        type=SourceType.BLOG,
        url="https://arbitrary-domain.test",
        config={"adapter": "chillin_competition"},
    )
    # Must resolve ChillinCompetitionAdapter regardless of name or URL
    resolved = DirectWebAdapterRegistry.get_adapter_for_source(renamed_source)
    assert isinstance(resolved, ChillinCompetitionAdapter)
    assert resolved.adapter_code == "chillin_competition"


def test_missing_adapter_fails_closed():
    """Verify sources missing config.adapter fail closed without heuristic guessing (Section 12)."""
    # 1. Source with empty config
    src_empty = Source(
        name="Chillin'Competition Blog",
        type=SourceType.BLOG,
        url="https://chillingcompetition.com",
        config={},
    )
    with pytest.raises(DirectWebUnknownAdapterError) as exc_info:
        DirectWebAdapterRegistry.get_adapter_for_source(src_empty)
    assert "has no 'adapter' configured" in str(exc_info.value)

    # 2. Source with None config
    src_none = Source(
        name="Almacén de Derecho",
        type=SourceType.BLOG,
        url="https://almacendederecho.org",
        config=None,
    )
    with pytest.raises(DirectWebUnknownAdapterError):
        DirectWebAdapterRegistry.get_adapter_for_source(src_none)

    # 3. Source with unknown adapter code
    src_unknown = Source(
        name="Some Publication",
        type=SourceType.BLOG,
        config={"adapter": "non_existent_code_xyz"},
    )
    with pytest.raises(DirectWebUnknownAdapterError):
        DirectWebAdapterRegistry.get_adapter_for_source(src_unknown)


def test_source_ownership_vs_article_author(db_session: Session):
    """Verify a collective Source with tracked_entity_id=None can have multiple authors without assigning ownership (Section 13)."""
    source = Source(
        name="Almacén de Derecho - Competencia",
        type=SourceType.BLOG,
        provider="native",
        url="https://almacendederecho.org/category/competencia/",
        config={"adapter": "almacen_derecho"},
        tracked_entity_id=None,  # Collective publication: no single individual owner
        active=True,
    )
    db_session.add(source)
    db_session.flush()

    entry_a = Entry(
        source_id=source.id,
        external_id="almacen_art_1",
        url="https://almacendederecho.org/cartel-leche",
        title="¿Cuánto vale el cártel de la leche?",
        content="Contenido extenso sobre litigación antitrust en España...",
        author="Francisco Marcos",
        content_hash=compute_ingestion_dedupe_hash("¿Cuánto vale el cártel de la leche?", "https://almacendederecho.org/cartel-leche"),
        captured_at=datetime.now(timezone.utc),
    )
    entry_b = Entry(
        source_id=source.id,
        external_id="almacen_art_2",
        url="https://almacendederecho.org/sentencia-rogon",
        title="La sentencia Rogon del TJUE",
        content="Contenido extenso sobre agentes deportivos y derecho de la competencia...",
        author="Jesús Alfaro",
        content_hash=compute_ingestion_dedupe_hash("La sentencia Rogon del TJUE", "https://almacendederecho.org/sentencia-rogon"),
        captured_at=datetime.now(timezone.utc),
    )
    db_session.add_all([entry_a, entry_b])
    db_session.commit()

    # Verify both entries belong to the same Source
    assert entry_a.source_id == source.id
    assert entry_b.source_id == source.id

    # Verify authors are distinct personal authors
    assert entry_a.author == "Francisco Marcos"
    assert entry_b.author == "Jesús Alfaro"

    # Verify Source ownership remains unassigned to either individual author
    assert source.tracked_entity_id is None
    assert source.tracked_entity is None


def test_entry_author_tracked_entity_context(db_session: Session, monkeypatch):
    """Verify entry-level deterministic author matching records TrackedEntity context without mutating Source.tracked_entity_id (Section 6)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "DIRECT_WEB_INGESTION_ENABLED", True)

    # Create active TrackedEntity for Francisco Marcos
    te_francisco = TrackedEntity(
        display_name="Francisco Marcos",
        entity_type="person",
        active=True,
    )
    db_session.add(te_francisco)
    db_session.flush()

    source = Source(
        name="Almacén de Derecho Test",
        type=SourceType.BLOG,
        provider="native",
        url="https://almacendederecho.org",
        config={"adapter": "almacen_derecho"},
        tracked_entity_id=None,  # Source is NOT owned by Francisco Marcos
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    def mock_router(request: httpx.Request):
        url_str = str(request.url)
        if "feed" in url_str:
            return httpx.Response(200, text=MOCK_ALMACEN_RSS)
        return httpx.Response(200, text=MOCK_ALMACEN_HTML)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_router))

    service = DirectWebIngestionService()
    report = service.execute_ingestion(
        db=db_session,
        sources=[source],
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report.total_created == 1
    created_entry = db_session.query(Entry).filter(Entry.source_id == source.id).first()
    assert created_entry.author == "Francisco Marcos"

    # Entry metadata contains tracked author entity context
    assert created_entry.raw_metadata.get("tracked_author_entity_id") == str(te_francisco.id)
    assert created_entry.raw_metadata.get("tracked_author_entity_name") == "Francisco Marcos"

    # Source.tracked_entity_id MUST remain None!
    db_session.refresh(source)
    assert source.tracked_entity_id is None


# ==============================================================================
# 2. ADAPTER EXTRACTION & PARSING TESTS (MOCKED HTTP)
# ==============================================================================

def test_kluwer_adapter_discover_and_parse():
    """Verify Kluwer adapter discovery and parsing logic."""
    adapter = KluwerCompetitionAdapter()
    source = Source(name="Kluwer Competition", type=SourceType.BLOG, url="https://legalblogs.wolterskluwer.com")

    # Mock discover
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=MOCK_KLUWER_RSS))
    )
    items = adapter.discover(source, limit=5, client=mock_client)
    assert len(items) == 1
    item = items[0]
    assert item.title == "The Danish Wolt Decision: Abuse of Dominance Explored"
    assert "the-danish-wolt-decision" in item.url
    assert "utm_source" not in item.url
    assert item.author == "Christian Bergqvist"

    # Mock parse
    article = adapter.parse_detail(MOCK_KLUWER_HTML, item)
    assert article.title == item.title
    assert article.author == "Christian Bergqvist"
    assert article.canonical_url == "https://legalblogs.wolterskluwer.com/competition-blog/the-danish-wolt-decision"
    assert "Article 102 TFEU" in article.content
    assert article.raw_metadata.get("published_at_source") == "detail_json_ld"
    assert article.language == "en"


def test_chillin_adapter_discover_and_parse():
    """Verify Chillin'Competition adapter discovery and parsing logic."""
    adapter = ChillinCompetitionAdapter()
    source = Source(name="Chillin'Competition", type=SourceType.BLOG, url="https://chillingcompetition.com")

    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=MOCK_CHILLIN_RSS))
    )
    items = adapter.discover(source, limit=5, client=mock_client)
    assert len(items) == 1
    item = items[0]
    assert "football judgments" in item.title
    assert item.author == "Pablo Ibanez Colomo"

    article = adapter.parse_detail(MOCK_CHILLIN_HTML, item)
    assert "Article 101(1) TFEU" in article.title
    assert article.author == "Pablo Ibanez Colomo"
    assert "Tondela" in article.content
    # Boilerplates like 'Share this' should be stripped
    assert "Share this" not in article.content
    assert article.raw_metadata.get("published_at_source") == "detail_meta"


def test_almacen_adapter_discover_and_parse():
    """Verify Almacén de Derecho adapter discovery and parsing logic."""
    adapter = AlmacenDerechoAdapter()
    source = Source(name="Almacén de Derecho", type=SourceType.BLOG, url="https://almacendederecho.org")

    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=MOCK_ALMACEN_RSS))
    )
    items = adapter.discover(source, limit=5, client=mock_client)
    assert len(items) == 1
    item = items[0]
    assert "daños por cártel" in item.title
    assert item.author == "Francisco Marcos"

    article = adapter.parse_detail(MOCK_ALMACEN_HTML, item)
    assert article.author == "Francisco Marcos"
    assert article.language == "es"
    assert "cártel del azúcar" in article.content
    assert "Compartir esto" not in article.content


# ==============================================================================
# 3. HTML CLEANER, DATES & AUTHOR HARDENING
# ==============================================================================

def test_author_disallows_publisher_name():
    """Verify that publisher or website names are rejected as individual authors (Section 17)."""
    from bs4 import BeautifulSoup
    html = """<html><body><meta name="author" content="Wolters Kluwer" /></body></html>"""
    soup = BeautifulSoup(html, "html.parser")
    author = extract_structured_author(
        soup,
        fallback_listing_author="Wolters Kluwer",
        disallowed_publisher_names=["Wolters Kluwer", "Kluwer"],
    )
    assert author is None


def test_date_hierarchy_precedence():
    """Verify date extraction strictly obeys hierarchy: JSON-LD > meta > time > listing fallback."""
    from bs4 import BeautifulSoup
    # 1. JSON-LD present
    html_jsonld = """<html><head>
    <script type="application/ld+json">{"datePublished": "2026-09-01T10:00:00Z"}</script>
    <meta property="article:published_time" content="2026-08-01T10:00:00Z" />
    </head></html>"""
    soup = BeautifulSoup(html_jsonld, "html.parser")
    dt, source_lbl = extract_structured_date(soup, fallback_listing_date=datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert dt.month == 9
    assert source_lbl == "detail_json_ld"

    # 2. Meta present without JSON-LD
    html_meta = """<html><head>
    <meta property="article:published_time" content="2026-08-01T10:00:00Z" />
    </head></html>"""
    soup = BeautifulSoup(html_meta, "html.parser")
    dt, source_lbl = extract_structured_date(soup, fallback_listing_date=datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert dt.month == 8
    assert source_lbl == "detail_meta"

    # 3. Fallback to listing date
    soup_empty = BeautifulSoup("<html><body></body></html>", "html.parser")
    dt, source_lbl = extract_structured_date(soup_empty, fallback_listing_date=datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert dt.month == 7
    assert source_lbl == "listing_feed"


def test_canonical_url_rejects_cross_domain():
    """Verify canonical URL is discarded if pointing to an external domain (Section 22)."""
    from bs4 import BeautifulSoup
    page_url = "https://chillingcompetition.com/2026/07/31/article-1/"
    html_spoof = """<html><head><link rel="canonical" href="https://evil-spoof.com/article-1" /></head></html>"""
    soup = BeautifulSoup(html_spoof, "html.parser")
    canon = extract_canonical_url(soup, page_url)
    assert canon == normalize_url(page_url)


# ==============================================================================
# 4. SERVICE INTEGRATION & FAILURE ISOLATION TESTS
# ==============================================================================

def test_service_dry_run_no_writes_and_no_network(db_session: Session):
    """Verify DRY RUN mode writes nothing to DB and makes 0 calls."""
    settings = get_settings()
    source = Source(
        name="Chillin'Competition Dry Run",
        type=SourceType.BLOG,
        provider="native",
        url="https://chillingcompetition.com",
        config={"adapter": "chillin_competition"},
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    service = DirectWebIngestionService()
    # dry-run because confirm_real_calls is False
    report = service.execute_ingestion(
        db=db_session,
        sources=[source],
        confirm_real_calls=False,
    )

    assert report.is_dry_run is True
    assert report.total_created == 0
    assert report.total_fetched == 0
    assert db_session.query(Entry).filter(Entry.source_id == source.id).count() == 0
    assert db_session.query(IngestionRun).filter(IngestionRun.source_id == source.id).count() == 0


def test_service_real_run_creates_entry_and_run(db_session: Session, monkeypatch):
    """Verify real ingestion run creates Direct Entry, IngestionRun, and evaluates Sufficiency."""
    settings = get_settings()
    monkeypatch.setattr(settings, "DIRECT_WEB_INGESTION_ENABLED", True)

    source = Source(
        name="Kluwer Blog Real Test",
        type=SourceType.BLOG,
        provider="native",
        url="https://legalblogs.wolterskluwer.com/competition-blog/",
        config={"adapter": "kluwer_competition"},
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    def mock_router(request: httpx.Request):
        url_str = str(request.url)
        if "rss.xml" in url_str:
            return httpx.Response(200, text=MOCK_KLUWER_RSS)
        elif "wolt-decision" in url_str:
            return httpx.Response(200, text=MOCK_KLUWER_HTML)
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_router))

    service = DirectWebIngestionService()
    report = service.execute_ingestion(
        db=db_session,
        sources=[source],
        max_items_per_source=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report.is_dry_run is False
    assert report.total_created == 1
    assert report.total_duplicates == 0
    assert report.total_failed == 0

    # Verify Entry in DB
    created_entry = db_session.query(Entry).filter(Entry.source_id == source.id).first()
    assert created_entry is not None
    assert created_entry.author == "Christian Bergqvist"
    assert created_entry.title == "The Danish Wolt Decision: Abuse of Dominance Explored"
    assert "Article 102 TFEU" in created_entry.content

    # Verify IngestionRun in DB
    run = db_session.query(IngestionRun).filter(IngestionRun.source_id == source.id).first()
    assert run is not None
    assert run.status == IngestionRunStatus.SUCCESS.value
    assert run.created_count == 1
    assert run.run_metadata.get("adapter") == "kluwer_competition"

    # Running a second time must detect duplicate!
    report_dup = service.execute_ingestion(
        db=db_session,
        sources=[source],
        max_items_per_source=5,
        confirm_real_calls=True,
        client=mock_client,
    )
    assert report_dup.total_created == 0
    assert report_dup.total_duplicates == 1


def test_service_google_news_cross_matching(db_session: Session, monkeypatch):
    """Verify Direct Entry correctly matches and links existing Google News discovery entry (Section 24)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "DIRECT_WEB_INGESTION_ENABLED", True)

    # 1. Create Google News Source & Entry
    gn_source = Source(
        name="Google News",
        type=SourceType.GOOGLE_NEWS,
        provider="native",
        url="https://news.google.com",
        active=True,
    )
    db_session.add(gn_source)
    db_session.flush()

    gn_entry = Entry(
        source_id=gn_source.id,
        external_id="gn_art_123",
        url="https://news.google.com/rss/articles/CBMi_test",
        title="The hack-trick of football judgments and Article 101(1) TFEU",
        content="Short snippet from google news...",
        raw_metadata={"publisher": "Chillin'Competition", "publisher_domain": "chillingcompetition.com"},
        captured_at=datetime.now(timezone.utc),
    )
    db_session.add(gn_entry)
    db_session.commit()

    # 2. Ingest Direct Chillin Source
    chillin_source = Source(
        name="Chillin'Competition",
        type=SourceType.BLOG,
        provider="native",
        url="https://chillingcompetition.com/",
        config={"adapter": "chillin_competition"},
        active=True,
    )
    db_session.add(chillin_source)
    db_session.commit()

    def mock_router(request: httpx.Request):
        url_str = str(request.url)
        if "feed" in url_str:
            return httpx.Response(200, text=MOCK_CHILLIN_RSS)
        return httpx.Response(200, text=MOCK_CHILLIN_HTML)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_router))

    service = DirectWebIngestionService()
    report = service.execute_ingestion(
        db=db_session,
        sources=[chillin_source],
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report.total_created == 1
    assert report.total_google_news_matches == 1

    # Check direct entry has GN traceability
    direct_entry = db_session.query(Entry).filter(Entry.source_id == chillin_source.id).first()
    assert direct_entry.raw_metadata.get("discovered_via_google_news") is True
    assert direct_entry.raw_metadata.get("google_news_discovery_entry_id") == str(gn_entry.id)

    # Check Google News entry was reciprocally updated
    db_session.refresh(gn_entry)
    assert gn_entry.raw_metadata.get("direct_entry_id") == str(direct_entry.id)


def test_service_failure_isolation_item_and_source(db_session: Session, monkeypatch):
    """Verify that a failure on one item or source does not abort the remaining run (Section 27)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "DIRECT_WEB_INGESTION_ENABLED", True)

    source1 = Source(
        name="Source 1 Failing HTTP",
        type=SourceType.BLOG,
        provider="native",
        url="https://chillingcompetition.com",
        config={"adapter": "chillin_competition"},
        active=True,
    )
    source2 = Source(
        name="Source 2 Successful",
        type=SourceType.BLOG,
        provider="native",
        url="https://almacendederecho.org",
        config={"adapter": "almacen_derecho"},
        active=True,
    )
    db_session.add_all([source1, source2])
    db_session.commit()

    def mock_router(request: httpx.Request):
        url_str = str(request.url)
        if "chillingcompetition" in url_str:
            return httpx.Response(500, text="Internal Server Error")
        elif "feed" in url_str:
            return httpx.Response(200, text=MOCK_ALMACEN_RSS)
        else:
            return httpx.Response(200, text=MOCK_ALMACEN_HTML)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_router))

    service = DirectWebIngestionService()
    report = service.execute_ingestion(
        db=db_session,
        sources=[source1, source2],
        confirm_real_calls=True,
        client=mock_client,
    )

    # Source 1 failed, Source 2 succeeded!
    assert report.sources_processed == 2
    assert report.results_by_source[0].failed_count >= 1
    assert report.results_by_source[1].entries_created == 1
    assert report.total_created == 1
