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
    """Verify that press release B7-54/25 and decision B7-54/25 sharing the same Aktenzeichen do NOT collide."""
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
        title="Apple ändert Regeln für personalisierte Werbung in Apps (B7-54/25)",
        url="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_17_2026_Apple_ATTF.html",
        canonical_url="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_17_2026_Apple_ATTF.html",
        published_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
        captured_at=datetime.now(timezone.utc),
        content_type="press_release",
        content_hash="hash_pr_b7_54_25",
        raw_metadata={"case_reference": "B7-54/25"},
    )
    db_session.add(pr_entry)
    db_session.commit()

    # 1. Inspector check for identical URL (must detect duplicate via canonical_url)
    raw_identical = RawEntryData(
        title="Apple ändert Regeln für personalisierte Werbung in Apps (B7-54/25)",
        url="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_17_2026_Apple_ATTF.html",
        external_id="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_17_2026_Apple_ATTF.html",
        raw_metadata={"case_reference": "B7-54/25", "german_url": "https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_17_2026_Apple_ATTF.html"},
    )
    identical_check = ReadOnlyDeduplicationInspector.check_bundeskartellamt_item(
        db=db_session,
        source_id=source.id,
        raw=raw_identical,
    )
    assert identical_check.is_duplicate is True
    assert identical_check.duplicate_reason == "canonical_url"

    # 2. Inspector check for decision document with same Aktenzeichen but distinct document URL & title
    raw_decision = RawEntryData(
        title="Beschluss im Apple-ATTF-Verfahren veröffentlicht (B7-54/25)",
        url="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/AktuelleMeldungen/2026/09_01_2026_Entscheidung_ATTF.html",
        external_id="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/AktuelleMeldungen/2026/09_01_2026_Entscheidung_ATTF.html",
        raw_metadata={"case_reference": "B7-54/25", "german_url": "https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/AktuelleMeldungen/2026/09_01_2026_Entscheidung_ATTF.html"},
    )
    decision_check = ReadOnlyDeduplicationInspector.check_bundeskartellamt_item(
        db=db_session,
        source_id=source.id,
        raw=raw_decision,
    )
    assert decision_check.is_duplicate is False


def test_bundeskartellamt_sufficiency():
    """Verify that Bundeskartellamt entries satisfy standard generic source sufficiency rules.
    - Substantive realistic text (~7k chars) => FULL
    - Short/broken teaser text (250 chars) => NO FULL (INSUFFICIENT)
    """
    source = Source(
        id=uuid.uuid4(),
        name=BUNDESKARTELLAMT_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
    )

    # Substantive realistic publication (7,560 chars) => must be FULL
    substantive_content = "Das Bundeskartellamt hat ein Missbrauchsverfahren eingeleitet. " * 120
    assert len(substantive_content) >= 7000

    substantive_entry = Entry(
        title="Bundeskartellamt leitet Missbrauchsverfahren ein",
        url="https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_01_2026_Tech.html",
        content=substantive_content,
        excerpt="Das Bundeskartellamt hat ein Verfahren eingeleitet.",
        language="de",
        source=source,
    )
    result_substantive = SourceSufficiencyService.assess(substantive_entry)
    assert result_substantive.level == SourceSufficiencyLevel.FULL
    assert result_substantive.signals.full_text_available is True

    # Broken extraction / teaser (250 chars) => must NOT be FULL (must be INSUFFICIENT)
    teaser_content = "A" * 250
    assert len(teaser_content) == 250
    teaser_entry = Entry(
        title="Kurze Meldung oder Teaser",
        url="https://www.bundeskartellamt.de/teaser.html",
        content=teaser_content,
        excerpt="Kurz.",
        source=source,
    )
    result_teaser = SourceSufficiencyService.assess(teaser_entry)
    assert result_teaser.level != SourceSufficiencyLevel.FULL
    assert result_teaser.level == SourceSufficiencyLevel.INSUFFICIENT


@pytest.mark.asyncio
async def test_preview_bundeskartellamt_read_only(db_session: Session):
    """Verify that SourceDiscoveryPreviewService previews Bundeskartellamt in strictly read-only mode."""
    from scripts.preview_source_discovery import SourceDiscoveryPreviewService

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

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "rssnewsfeed.xml" in url:
            return httpx.Response(200, text=SAMPLE_RSS_FEED)
        return httpx.Response(200, text=SAMPLE_GERMAN_DETAIL_WITH_EN_LINK)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        service = SourceDiscoveryPreviewService(db=db_session, now=datetime(2026, 1, 20, tzinfo=timezone.utc))
        report = await service.run_preview_async(
            lookback_days=30,
            source_filter="bundeskartellamt",
            async_client=client,
            target_sources=[source],
        )

    assert report is not None
    assert len(report.sources_summaries) == 1
    src_report = report.sources_summaries[0]
    assert src_report.source_name == BUNDESKARTELLAMT_SOURCE_NAME
    assert src_report.discovered_total >= 2
    assert src_report.new_candidates >= 2
    # Verify zero mutations occurred
    entries_in_db = db_session.execute(select(Entry).where(Entry.source_id == source.id)).scalars().all()
    assert len(entries_in_db) == 0


@pytest.mark.asyncio
async def test_backfill_prospective_count_bundeskartellamt(db_session: Session):
    """Verify that NewSourcesBackfillService counts prospective entries for Bundeskartellamt."""
    from app.services.new_sources_backfill_service import NewSourcesBackfillService

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

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "rssnewsfeed.xml" in url:
            return httpx.Response(200, text=SAMPLE_RSS_FEED)
        return httpx.Response(200, text=SAMPLE_GERMAN_DETAIL_WITH_EN_LINK)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        service = NewSourcesBackfillService()
        total_new, per_source = await service._count_prospective_new_entries(
            target_sources=[source],
            db=db_session,
            lookback_days=300,
            async_client=client,
        )

    assert total_new >= 2


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
            from sqlalchemy import delete
            from app.models.tracking import TrackedEntity
            db.execute(delete(Source).where(Source.name == BUNDESKARTELLAMT_SOURCE_NAME))
            db.execute(delete(TrackedEntity).where(TrackedEntity.display_name == "Bundeskartellamt"))
            db.commit()
            db.close()


@pytest.mark.asyncio
async def test_publication_notice_resolution_to_substantive_doc():
    """Verify that a Fallbericht publication notice resolves to the substantive decision document and PDF."""
    notice_url = "https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/AktuelleMeldungen/2026/09_10_2026_Fallbericht_Messtechnik.html"
    substantive_doc_url = "https://www.bundeskartellamt.de/SharedDocs/Entscheidung/DE/Fallberichte/Kartellverbot/2026/B12-21-23.html"
    pdf_url = "https://www.bundeskartellamt.de/SharedDocs/Entscheidung/DE/Fallberichte/Kartellverbot/2026/B12-21-23.pdf?__blob=publicationFile&v=3"

    notice_html = f"""<!DOCTYPE html><html><body><main>
      <h1>Fallbericht: Bußgelder wegen wettbewerbsbeschränkender Absprachen (B12-21/23)</h1>
      <p>Das Bundeskartellamt hat am 23. Juni 2026 Bußgelder gegen drei Unternehmen verhängt.</p>
      <div class="c-teaser-download">
        <h3 class="c-teaser-download__headline">B12-21/23</h3>
        <p class="c-topline"><span class="c-topline__item">Fallbericht</span></p>
        <p class="c-teaser-download__link-wrapper">
          <a class="c-link" href="{substantive_doc_url}?nn=55030">Mehr erfahren</a>
        </p>
      </div>
    </main></body></html>"""

    substantive_html = f"""<!DOCTYPE html><html><body><main>
      <div class="c-article">
        <h1>B12-21/23</h1>
        <div class="c-article__intro">
          <p>Fallbericht vom 10.09.2026: Ordnungswidrigkeitenverfahren wegen des Verdachts wettbewerbsbeschränkender Absprachen.</p>
          <ul class="c-doc-data">
            <li>Produktmärkte: Prüf- und Messgeräte</li>
            <li>Entscheidungsart: Sonstiges</li>
            <li>Entscheidungsdatum: 23.06.2026</li>
          </ul>
        </div>
        <p><a class="c-link is-download-link" href="{pdf_url}">Download (PDF, 123KB)</a></p>
      </div>
    </main></body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "Fallbericht_Messtechnik.html" in url:
            return httpx.Response(200, text=notice_html)
        if "B12-21-23.html" in url:
            return httpx.Response(200, text=substantive_html)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = BundeskartellamtExtractor()
        cand = {"url": notice_url, "title": "Fallbericht Messtechnik", "published_at": None}
        raw = await extractor._enrich_item(client=client, candidate=cand)

    assert raw is not None
    # Canonical / external_id must point to the official substantive document
    assert raw.url == substantive_doc_url
    assert raw.external_id == substantive_doc_url

    # Traceability metadata
    meta = raw.raw_metadata
    assert meta["discovery_url"] == notice_url
    assert meta["announcement_url"] == notice_url
    assert meta["document_url"] == substantive_doc_url
    assert meta["case_reference"] == "B12-21/23"

    # PDF metadata preserved without downloading
    assert meta["has_pdf"] is True
    assert meta["pdf_url"] == pdf_url
    assert len(meta["pdf_attachments"]) == 1

    # Content contains substantive document details + notice summary
    assert "Produktmärkte: Prüf- und Messgeräte" in raw.content
    assert "Zusammenfassung des Bundeskartellamtes:" in raw.content
    assert "Das Bundeskartellamt hat am 23. Juni 2026 Bußgelder" in raw.content


@pytest.mark.asyncio
async def test_publication_notice_fallback_on_missing_or_error_link():
    """Verify that if substantive document link fails, notice content and URL are safely retained."""
    notice_url = "https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/AktuelleMeldungen/2026/09_10_2026_Fallbericht_Broken.html"
    broken_doc_url = "https://www.bundeskartellamt.de/SharedDocs/Entscheidung/DE/Fallberichte/Broken.html"

    notice_html = f"""<!DOCTYPE html><html><body><main>
      <h1>Fallbericht: Bußgelder verhängt</h1>
      <p>Aviso de publicación con enlace roto.</p>
      <div class="c-teaser-download">
        <a class="c-link" href="{broken_doc_url}">Mehr erfahren</a>
      </div>
    </main></body></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "Fallbericht_Broken.html" in url:
            return httpx.Response(200, text=notice_html)
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = BundeskartellamtExtractor()
        cand = {"url": notice_url, "title": "Fallbericht Broken", "published_at": None}
        raw = await extractor._enrich_item(client=client, candidate=cand)

    assert raw is not None
    # Falls back safely to announcement URL
    assert raw.url == notice_url
    assert raw.external_id == notice_url
    assert "document_url" not in raw.raw_metadata
    assert "Aviso de publicación con enlace roto." in raw.content


def test_notice_and_substantive_doc_canonical_dedup(db_session: Session):
    """Verify that an announcement notice canonicalized to a decision document does NOT duplicate when the decision is encountered directly."""
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

    substantive_doc_url = "https://www.bundeskartellamt.de/SharedDocs/Entscheidung/DE/Fallberichte/Kartellverbot/2026/B12-21-23.html"
    notice_url = "https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/AktuelleMeldungen/2026/09_10_2026_Fallbericht_Messtechnik.html"

    # 1. First: Ingest the resolved announcement entry (whose canonical is substantive_doc_url)
    entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        title="Fallbericht: Bußgelder wegen wettbewerbsbeschränkender Absprachen (B12-21/23)",
        url=substantive_doc_url,
        canonical_url=substantive_doc_url,
        published_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        captured_at=datetime.now(timezone.utc),
        content_type="case_report",
        content_hash="hash_b12_21_23",
        raw_metadata={"discovery_url": notice_url, "document_url": substantive_doc_url, "case_reference": "B12-21/23"},
    )
    db_session.add(entry)
    db_session.commit()

    # 2. Later: Direct encounter of substantive_doc_url (e.g. from sitemap or re-run)
    raw_direct = RawEntryData(
        title="B12-21/23",
        url=substantive_doc_url,
        external_id=substantive_doc_url,
        raw_metadata={"case_reference": "B12-21/23"},
    )
    check = ReadOnlyDeduplicationInspector.check_bundeskartellamt_item(
        db=db_session,
        source_id=source.id,
        raw=raw_direct,
    )
    assert check.is_duplicate is True
    assert check.duplicate_reason == "canonical_url"


@pytest.mark.asyncio
async def test_historical_discovery_sitemap_only_older_than_rss():
    """Verify that extended lookback (180 days) discovers sitemap items older than the oldest item in RSS."""
    rss_feed = """<?xml version="1.0" encoding="utf-8"?>
    <rss version="2.0">
      <channel>
        <title>Bundeskartellamt</title>
        <item>
          <title>Meldung August 2026</title>
          <link>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_15_2026_Recent.html</link>
          <pubDate>Sat, 15 Aug 2026 10:00:00 +0200</pubDate>
        </item>
        <item>
          <title>Meldung Juli 2026</title>
          <link>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/07_01_2026_OldestInRSS.html</link>
          <pubDate>Wed, 01 Jul 2026 10:00:00 +0200</pubDate>
        </item>
      </channel>
    </rss>"""

    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/08_15_2026_Recent.html</loc>
        <lastmod>2026-08-15</lastmod>
      </url>
      <url>
        <loc>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/Pressemitteilungen/2026/07_01_2026_OldestInRSS.html</loc>
        <lastmod>2026-07-01</lastmod>
      </url>
      <url>
        <loc>https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/AktuelleMeldungen/2026/13_05_2026_Fallbericht_Strabag.html</loc>
        <lastmod>2026-05-13</lastmod>
      </url>
    </urlset>"""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "rssnewsfeed.xml" in url:
            return httpx.Response(200, text=rss_feed)
        if "Sitemap_XML.xml" in url:
            return httpx.Response(200, text=sitemap_xml)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = BundeskartellamtExtractor()
        ref_now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        candidates = await extractor.discover_candidates(
            client=client,
            lookback_days=180,
            now=ref_now,
        )

    urls = [c["url"] for c in candidates]
    # Older item from May 2026 (outside the RSS feed) must be discovered
    assert "https://www.bundeskartellamt.de/SharedDocs/Meldung/DE/AktuelleMeldungen/2026/13_05_2026_Fallbericht_Strabag.html" in urls
    assert len(candidates) == 3
    # Check deduplicated and chronological sort (August > July > May)
    assert candidates[0]["published_at"] > candidates[1]["published_at"] > candidates[2]["published_at"]
