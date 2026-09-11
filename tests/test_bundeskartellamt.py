"""Tests for Bundeskartellamt extractor, multilingual resolution, Aktenzeichen extraction, deduplication, and seed script."""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRunStatus
from app.providers.base import RawEntryData
from app.providers.native import NativeProvider
from app.providers.extractors.bundeskartellamt import (
    BundeskartellamtExtractor,
    BUNDESKARTELLAMT_SOURCE_NAME,
    BUNDESKARTELLAMT_RSS_URL,
    BUNDESKARTELLAMT_SITEMAP_URL,
    AKTENZEICHEN_REGEX,
)
from app.services.ingestion_service import IngestionService
from app.services.weekly_refresh_service import WeeklyRefreshService
from app.services.source_sufficiency_service import (
    SourceSufficiencyService,
    SourceSufficiencyLevel,
)
from scripts.preview_source_discovery import ReadOnlyDeduplicationInspector
from scripts.seed_source_bundeskartellamt import seed_bundeskartellamt_source
from tests.conftest import TestingSessionLocal


SAMPLE_RSS_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Bundeskartellamt - Aktuelle Pressemitteilungen</title>
    <link>https://www.bundeskartellamt.de</link>
    <description>Meldungen des Bundeskartellamtes</description>
    <language>de-de</language>
    <item>
      <title>Bundeskartellamt leitet Verfahren gegen Technologieunternehmen ein (B12-21/23)</title>
      <link>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_01_2026_Tech.html</link>
      <description>Das Bundeskartellamt hat heute ein Missbrauchsverfahren eingeleitet.</description>
      <category>Kartellrecht</category>
      <pubDate>Mon, 12 Jan 2026 10:00:00 +0100</pubDate>
    </item>
    <item>
      <title>Freigabe der Fusion im Bereich Messtechnik (B7-54/25)</title>
      <link>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/05_01_2026_Messtechnik.html</link>
      <description>Die Fusionskontrolle wurde in Phase 1 ohne Auflagen freigegeben.</description>
      <category>Fusionskontrolle</category>
      <pubDate>Tue, 06 Jan 2026 09:30:00 +0100</pubDate>
    </item>
    <item>
      <title>Das Bundeskartellamt jetzt auf YouTube</title>
      <link>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/02_01_2026_YouTube.html</link>
      <description>Sehen Sie unsere neuen Erklärvideos auf dem offiziellen YouTube-Kanal.</description>
      <category>Social Media</category>
      <pubDate>Fri, 02 Jan 2026 12:00:00 +0100</pubDate>
    </item>
    <item>
      <title>Stellenausschreibung: Volljurist / Volljuristin (m/w/d)</title>
      <link>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Karriere/2026/01_01_2026_Jurist.html</link>
      <description>Wir suchen engagierte Juristen für die Beschlussabteilungen.</description>
      <category>Karriere</category>
      <pubDate>Thu, 01 Jan 2026 08:00:00 +0100</pubDate>
    </item>
    <item>
      <title>Zukünftige Meldung zu Digitalkonzernen</title>
      <link>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/Future_Tech.html</link>
      <description>Geplante Bekanntmachung.</description>
      <pubDate>Thu, 01 Jan 2030 10:00:00 +0100</pubDate>
    </item>
  </channel>
</rss>
"""

SAMPLE_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_01_2026_Tech.html</loc>
    <lastmod>2026-01-12</lastmod>
  </url>
  <url>
    <loc>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/05_01_2026_Messtechnik.html</loc>
    <lastmod>2026-01-06</lastmod>
  </url>
  <url>
    <loc>https://www.bundeskartellamt.de/SharedDocs/Entscheidung/DE/Fallberichte/Kartellrecht/2026/B12-21-23.html</loc>
    <lastmod>2026-01-15</lastmod>
  </url>
  <url>
    <loc>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2024/Alt_2024.html</loc>
    <lastmod>2024-01-01</lastmod>
  </url>
</urlset>
"""

SAMPLE_GERMAN_DETAIL_WITH_EN_LINK = """<!DOCTYPE html>
<html lang="de">
<head>
  <title>Bundeskartellamt leitet Verfahren gegen Technologieunternehmen ein</title>
  <meta name="description" content="Das Bundeskartellamt hat heute ein Verfahren eingeleitet." />
</head>
<body>
  <main id="main">
    <div class="c-topline">
      <span class="c-topline__item">Pressemitteilung</span>
      <span class="c-topline__item">12.01.2026</span>
    </div>
    <h1>Bundeskartellamt leitet Verfahren gegen Technologieunternehmen ein</h1>
    <div class="c-teaser-newsbox">
      <p class="c-teaser-newsbox__link-wrapper">
        <a class="c-link" href="/SharedDocs/Meldung/EN/Pressemitteilungen/2026/08_01_2026_Tech.html">English Version</a>
      </p>
    </div>
    <div class="c-rich-text">
      <p>Das Bundeskartellamt untersucht Praktiken im Bereich digitaler Plattformen (Aktenzeichen: B12-21/23). Die Behörde prüft Wettbewerbsverzerrungen.</p>
      <p>Weitere Details finden sich im Bericht.</p>
      <p><a href="/SharedDocs/Publikation/DE/Downloads/Bericht_B12_21_23.pdf">Fallbericht als PDF herunterladen (PDF, 450KB)</a></p>
    </div>
  </main>
</body>
</html>
"""

SAMPLE_ENGLISH_DETAIL = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Bundeskartellamt initiates proceeding against tech company</title>
  <meta name="description" content="The Bundeskartellamt today initiated a proceeding." />
</head>
<body>
  <main id="main">
    <div class="c-topline">
      <span class="c-topline__item">Press Release</span>
      <span class="c-topline__item">12.01.2026</span>
    </div>
    <h1>Bundeskartellamt initiates proceeding against tech company</h1>
    <div class="c-rich-text">
      <p>The Bundeskartellamt is investigating practices in digital platform markets (case reference: B12-21/23). The authority examines competition distortion.</p>
      <p>Further details are set out in the report.</p>
      <p><a href="/SharedDocs/Publikation/EN/Downloads/Report_B12_21_23.pdf">Download report as PDF (PDF, 420KB)</a></p>
    </div>
  </main>
</body>
</html>
"""


def test_aktenzeichen_regex():
    """Verify Aktenzeichen extraction pattern across various Bundeskartellamt formats."""
    extractor = BundeskartellamtExtractor()

    assert extractor.extract_aktenzeichen("Entscheidung im Fall B12-21/23 ergangen") == "B12-21/23"
    assert extractor.extract_aktenzeichen("Freigabe Messtechnik (B7-54/25)") == "B7-54/25"
    assert extractor.extract_aktenzeichen("Vergabekammer VK 1-12/24") == "VK 1-12/24"
    assert extractor.extract_aktenzeichen("Kein Aktenzeichen vorhanden") is None


def test_editorial_exclusion():
    """Verify editorial filtering excludes social media, vacancies, and generic events."""
    extractor = BundeskartellamtExtractor()

    assert extractor.is_editorial_noise("Bundeskartellamt auf YouTube", "/DE/Pressemitteilungen/yt.html", "Social Media") is True
    assert extractor.is_editorial_noise("Stellenausschreibung: Volljurist", "/DE/Karriere/job.html", "Karriere") is True
    assert extractor.is_editorial_noise("Jahresrückblick 2025 Grußwort", "/DE/Veranstaltungen/event.html", "Veranstaltungen") is True
    assert extractor.is_editorial_noise("Verfahren gegen Tech-Konzern (B12-21/23)", "/DE/Pressemitteilungen/tech.html", "Kartellrecht") is False


@pytest.mark.asyncio
async def test_bundeskartellamt_rss_discovery_and_multilingual():
    """Verify RSS discovery, editorial exclusion, future-date guard, and English pairing."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "rssnewsfeed.xml" in url:
            return httpx.Response(200, text=SAMPLE_RSS_FEED)
        if "08_01_2026_Tech.html" in url:
            if "/EN/" in url:
                return httpx.Response(200, text=SAMPLE_ENGLISH_DETAIL)
            return httpx.Response(200, text=SAMPLE_GERMAN_DETAIL_WITH_EN_LINK)
        if "05_01_2026_Messtechnik.html" in url:
            html = """<!DOCTYPE html><html><body><h1>Freigabe der Fusion im Bereich Messtechnik</h1>
            <div class="c-rich-text"><p>Freigabe erteilt unter Aktenzeichen B7-54/25 ohne Auflagen.</p></div>
            </body></html>"""
            return httpx.Response(200, text=html)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = BundeskartellamtExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=BUNDESKARTELLAMT_SOURCE_NAME,
            url="https://www.bundeskartellamt.de/",
            type=SourceType.INSTITUTIONAL,
            provider="native",
        )
        simulated_now = datetime(2026, 1, 20, 12, 0, 0, tzinfo=timezone.utc)
        entries = await extractor.extract(
            client=client,
            source=source,
            lookback_days=30,
            now=simulated_now,
        )

    assert len(entries) == 2

    # Entry 1: Multilingual paired
    tech_entry = next((e for e in entries if "Tech" in e.url), None)
    assert tech_entry is not None
    assert tech_entry.title == "Bundeskartellamt initiates proceeding against tech company"
    assert tech_entry.language == "en"
    assert tech_entry.raw_metadata["has_english_version"] is True
    assert tech_entry.raw_metadata["case_reference"] == "B12-21/23"
    assert tech_entry.raw_metadata["de_url"] == "https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_01_2026_Tech.html"
    assert len(tech_entry.raw_metadata["pdf_attachments"]) == 1
    assert "Report_B12_21_23.pdf" in tech_entry.raw_metadata["pdf_attachments"][0]["url"]

    # Entry 2: German-only
    m_entry = next((e for e in entries if "Messtechnik" in e.url), None)
    assert m_entry is not None
    assert "Messtechnik" in m_entry.title
    assert m_entry.language == "de"
    assert m_entry.raw_metadata["has_english_version"] is False
    assert m_entry.raw_metadata["case_reference"] == "B7-54/25"


@pytest.mark.asyncio
async def test_english_fallback_on_404():
    """Verify that if English translation link returns 404, extractor falls back to German gracefully."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "rssnewsfeed.xml" in url:
            return httpx.Response(200, text=SAMPLE_RSS_FEED)
        if "/DE/" in url and "08_01_2026_Tech.html" in url:
            return httpx.Response(200, text=SAMPLE_GERMAN_DETAIL_WITH_EN_LINK)
        if "/EN/" in url:
            return httpx.Response(404, text="Not Found")
        if "05_01_2026_Messtechnik.html" in url:
            return httpx.Response(200, text="<html><body><h1>Messtechnik</h1><p>B7-54/25</p></body></html>")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = BundeskartellamtExtractor()
        source = Source(
            id=uuid.uuid4(),
            name=BUNDESKARTELLAMT_SOURCE_NAME,
            url="https://www.bundeskartellamt.de/",
            type=SourceType.INSTITUTIONAL,
            provider="native",
        )
        simulated_now = datetime(2026, 1, 20, 12, 0, 0, tzinfo=timezone.utc)
        entries = await extractor.extract(
            client=client,
            source=source,
            lookback_days=30,
            now=simulated_now,
        )

    tech_entry = next((e for e in entries if "08_01_2026_Tech" in e.url), None)
    assert tech_entry is not None
    assert tech_entry.title == "Bundeskartellamt leitet Verfahren gegen Technologieunternehmen ein"
    assert tech_entry.language == "de"
    assert tech_entry.raw_metadata["has_english_version"] is False
    assert tech_entry.raw_metadata["english_fetch_failed"] is True


@pytest.mark.asyncio
async def test_sitemap_historical_discovery():
    """Verify sitemap discovery when lookback > 8 days."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "rssnewsfeed.xml" in url:
            return httpx.Response(200, text=SAMPLE_RSS_FEED)
        if "Sitemap_XML.xml" in url:
            return httpx.Response(200, text=SAMPLE_SITEMAP_XML)
        return httpx.Response(200, text="<html><body><h1>Test Detail</h1><p>Inhalt</p></body></html>")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = BundeskartellamtExtractor()
        simulated_now = datetime(2026, 1, 20, 12, 0, 0, tzinfo=timezone.utc)
        candidates = await extractor.discover_candidates(
            client=client,
            lookback_days=45,
            now=simulated_now,
        )

    urls = [c["url"] for c in candidates]
    assert "https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_01_2026_Tech.html" in urls
    assert "https://www.bundeskartellamt.de/SharedDocs/Entscheidung/DE/Fallberichte/Kartellrecht/2026/B12-21-23.html" in urls
    assert "https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2024/Alt_2024.html" not in urls


def test_dedup_non_destructive_same_aktenzeichen(db_session: Session):
    """Verify that press release and decision sharing same Aktenzeichen do NOT collide."""
    source = Source(
        id=uuid.uuid4(),
        name=BUNDESKARTELLAMT_SOURCE_NAME,
        url="https://www.bundeskartellamt.de/",
        type=SourceType.INSTITUTIONAL,
        provider="native",
        category="institutional",
    )
    db_session.add(source)
    db_session.commit()

    pr_entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        title="Pressemitteilung zum Verfahren (B12-21/23)",
        url="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/Tech.html",
        canonical_url="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/Tech.html",
        published_at=datetime(2026, 1, 12, tzinfo=timezone.utc),
        captured_at=datetime.now(timezone.utc),
        content_type="press_release",
        content_hash="hash_pr_b12",
        raw_metadata={"case_reference": "B12-21/23"},
    )
    db_session.add(pr_entry)
    db_session.commit()

    # Inspector check for identical URL (must detect duplicate)
    raw_identical = RawEntryData(
        title="Pressemitteilung zum Verfahren (B12-21/23)",
        url="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/Tech.html",
        external_id="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/Tech.html",
        raw_metadata={"case_reference": "B12-21/23", "german_url": "https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/Tech.html"},
    )
    identical_check = ReadOnlyDeduplicationInspector.check_bundeskartellamt_item(
        db=db_session,
        source_id=source.id,
        raw=raw_identical,
    )
    assert identical_check.is_duplicate is True
    assert identical_check.duplicate_reason == "canonical_url"

    # Inspector check for decision document with same Aktenzeichen but distinct URL and title
    raw_decision = RawEntryData(
        title="Fallbericht Plattformmärkte (B12-21/23)",
        url="https://www.bundeskartellamt.de/SharedDocs/Entscheidung/DE/Fallberichte/2026/B12-21-23.html",
        external_id="https://www.bundeskartellamt.de/SharedDocs/Entscheidung/DE/Fallberichte/2026/B12-21-23.html",
        raw_metadata={"case_reference": "B12-21/23", "german_url": "https://www.bundeskartellamt.de/SharedDocs/Entscheidung/DE/Fallberichte/2026/B12-21-23.html"},
    )
    decision_check = ReadOnlyDeduplicationInspector.check_bundeskartellamt_item(
        db=db_session,
        source_id=source.id,
        raw=raw_decision,
    )
    assert decision_check.is_duplicate is False


def test_bundeskartellamt_sufficiency():
    """Verify that Bundeskartellamt entries satisfy source sufficiency rules."""
    source = Source(
        id=uuid.uuid4(),
        name=BUNDESKARTELLAMT_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
    )

    valid_entry = Entry(
        title="Bundeskartellamt leitet Missbrauchsverfahren gegen Technologieunternehmen ein",
        url="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_01_2026_Tech.html",
        content="Das Bundeskartellamt hat heute ein Missbrauchsverfahren eingeleitet. Untersucht werden Praktiken auf digitalen Plattformen nach Paragraf 19a GWB. Die Behörde prüft Wettbewerbsverzerrungen und Marktmacht eingehend. Umfassende Prüfschritte wurden unternommen.",
        excerpt="Das Bundeskartellamt hat heute ein Missbrauchsverfahren eingeleitet.",
        language="de",
        source=source,
    )
    result = SourceSufficiencyService.assess(valid_entry)
    assert result.level == SourceSufficiencyLevel.FULL

    sparse_entry = Entry(
        title="Kurze Meldung",
        url="https://www.bundeskartellamt.de/test.html",
        content="Zu kurz.",
        excerpt="Kurz.",
        source=source,
    )
    sparse_result = SourceSufficiencyService.assess(sparse_entry)
    assert sparse_result.level == SourceSufficiencyLevel.INSUFFICIENT


@pytest.mark.asyncio
async def test_native_provider_dispatch():
    """Verify NativeProvider routes Bundeskartellamt source to BundeskartellamtExtractor."""
    provider = NativeProvider()
    source = Source(
        id=uuid.uuid4(),
        name=BUNDESKARTELLAMT_SOURCE_NAME,
        url="https://www.bundeskartellamt.de/",
        type=SourceType.INSTITUTIONAL,
        provider="native",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "rssnewsfeed.xml" in str(request.url):
            return httpx.Response(200, text=SAMPLE_RSS_FEED)
        return httpx.Response(200, text=SAMPLE_GERMAN_DETAIL_WITH_EN_LINK)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        entries = await provider.fetch_entries(source, client=client)

    assert len(entries) > 0
    assert any("Tech" in e.url for e in entries)


def test_weekly_refresh_bundeskartellamt_dry_run(db_session: Session):
    """Test that WeeklyRefreshService safely processes Bundeskartellamt source in dry-run mode."""
    source = Source(
        id=uuid.uuid4(),
        name=BUNDESKARTELLAMT_SOURCE_NAME,
        url="https://www.bundeskartellamt.de/",
        type=SourceType.INSTITUTIONAL,
        provider="native",
        category="institutional",
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    service = WeeklyRefreshService()
    report = service.run_weekly_refresh(
        db=db_session,
        confirm_real_calls=False,
        sources_filter=[BUNDESKARTELLAMT_SOURCE_NAME],
    )

    assert report.status == "completed"
    assert report.is_dry_run is True
    assert len(report.per_source) == 1
    detail = report.per_source[0]
    assert detail.source_name == BUNDESKARTELLAMT_SOURCE_NAME
    assert detail.status == "success"
    assert detail.new_entries == 0


def test_seed_bundeskartellamt_idempotency():
    """Verify seed_source_bundeskartellamt runs idempotently without creating entries or making network calls."""
    with patch("scripts.seed_source_bundeskartellamt.SessionLocal", TestingSessionLocal):
        # 1. First execution
        src1 = seed_bundeskartellamt_source()
        assert src1 is not None
        assert src1.id is not None

        # 2. Second execution
        src2 = seed_bundeskartellamt_source()
        assert src2.id == src1.id

        # 3. Verify database state
        db = TestingSessionLocal()
        try:
            source = db.execute(
                select(Source).where(Source.name == BUNDESKARTELLAMT_SOURCE_NAME)
            ).scalar_one()
            assert source.name == BUNDESKARTELLAMT_SOURCE_NAME
            assert source.url == "https://www.bundeskartellamt.de/"
            assert source.provider == "native"
            assert source.type == SourceType.INSTITUTIONAL
            assert source.active is True
            entry_count = db.execute(
                select(Entry).where(Entry.source_id == source.id)
            ).scalars().all()
            assert len(entry_count) == 0
        finally:
            db.close()
