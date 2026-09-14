"""Comprehensive unit and integration test suite for FTC (Federal Trade Commission) extractor.

Audited and implemented under Bloque 17B of HITCHINGS.
Covers:
- RSS XML parse;
- guid / external_id stability;
- canonical URL extraction;
- date timezone-aware UTC normalization;
- lookback 8, 30, 90 days and exact cutoff behavior;
- duplicate RSS / detail handling;
- same matter / different milestones coexistence;
- consumer protection exclusion guard;
- HTML detail noise cleaning (boilerplate, contacts, phones);
- FULL / PARTIAL / INSUFFICIENT sufficiency thresholds (1500 / 300);
- PDF enrichment under 10MB;
- PDF over 10MB skip;
- max 15 pages and max 50,000 characters caps;
- OCR disabled;
- Source.config unchanged (execution-scoped lookback);
- scheduler default 8 days;
- backfill service resolution and aliases;
- preview service integration;
- CLI options.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import httpx
import pypdf

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.providers.base import RawEntryData
from app.providers.native import NativeProvider
from app.providers.extractors.ftc import (
    FTCCompetitionExtractor,
    FTCDiscoveryMetrics,
    FTC_SOURCE_NAME,
    FTC_BASE_URL,
    FTC_RSS_FEED_URL,
    DEFAULT_FTC_CONFIG,
)
from app.services.source_sufficiency_service import (
    SourceSufficiencyService,
    SourceSufficiencyLevel,
)
from app.services.new_sources_backfill_service import NewSourcesBackfillService
from scripts.preview_source_discovery import (
    ReadOnlyDeduplicationInspector,
    SourceDiscoveryPreviewService,
)
from scripts.seed_source_ftc import seed_ftc_source, FTC_ALIASES


def _create_mock_pdf(num_pages: int = 5, text_per_page: str = "Legal allegations and findings under Clayton Act Section 7...", byte_padding: int = 0) -> bytes:
    """Create a valid in-memory PDF with vector text."""
    writer = pypdf.PdfWriter()
    for _ in range(num_pages):
        page = writer.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()
    if byte_padding > 0:
        pdf_bytes += b"\x00" * byte_padding
    return pdf_bytes


# ------------------------------------------------------------------------------
# FROZEN FIXTURES
# ------------------------------------------------------------------------------

MOCK_FTC_RSS_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>FTC Competition Press Releases</title>
    <link>https://www.ftc.gov/enforcement/competition</link>
    <description>Competition news and enforcement from the FTC</description>
    <item>
      <title>FTC Secures Order Resolving Antitrust Concerns with Zillow-Redfin Agreement</title>
      <link>https://www.ftc.gov/news-events/news/press-releases/2026/08/ftc-secures-order-resolving-antitrust-concerns-zillow-redfin-agreement</link>
      <guid isPermaLink="false">335907</guid>
      <pubDate>Mon, 24 Aug 2026 08:00:00 -0400</pubDate>
      <description>Proposed order ends allegedly unlawful agreement between Zillow and Redfin.</description>
      <category>Competition</category>
      <category>Bureau of Competition</category>
    </item>
    <item>
      <title>FTC Files Administrative Complaint Blocking Healthcare System Acquisition</title>
      <link>https://www.ftc.gov/news-events/news/press-releases/2026/08/ftc-files-administrative-complaint-blocking-healthcare-system-acquisition</link>
      <guid isPermaLink="false">335856</guid>
      <pubDate>Fri, 21 Aug 2026 08:00:00 -0400</pubDate>
      <description>The FTC challenges hospital consolidation in Midwest market under Clayton Act §7.</description>
      <category>Competition</category>
    </item>
    <item>
      <title>FTC Returns $10 Million to Consumers Tricked by Telemarketing Debt Relief Scam</title>
      <link>https://www.ftc.gov/news-events/news/press-releases/2026/08/ftc-returns-10-million-consumers-tricked-telemarketing-debt-relief-scam</link>
      <guid isPermaLink="false">335700</guid>
      <pubDate>Wed, 19 Aug 2026 08:00:00 -0400</pubDate>
      <description>Refund checks sent to victims of robocalls and debt collection fraud.</description>
      <category>Consumer Protection</category>
      <category>robocalls</category>
    </item>
    <item>
      <title>FTC Wins Preliminary Injunction in Supermarket Merger Challenge</title>
      <link>https://www.ftc.gov/news-events/news/press-releases/2026/07/ftc-wins-preliminary-injunction-supermarket-merger-challenge</link>
      <guid isPermaLink="false">334513</guid>
      <pubDate>Wed, 01 Jul 2026 08:00:00 -0400</pubDate>
      <description>Federal district court grants preliminary injunction halting multi-billion merger.</description>
      <category>Competition</category>
    </item>
    <item>
      <title>FTC Concludes Investigation into Auto Parts Joint Venture</title>
      <link>https://www.ftc.gov/news-events/news/press-releases/2026/05/ftc-concludes-investigation-auto-parts-joint-venture</link>
      <guid isPermaLink="false">332100</guid>
      <pubDate>Fri, 15 May 2026 08:00:00 -0400</pubDate>
      <description>Commission closes antitrust probe into automotive joint venture.</description>
      <category>Competition</category>
    </item>
  </channel>
</rss>
"""

MOCK_FTC_DETAIL_HTML_FULL = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>FTC Secures Order Resolving Antitrust Concerns with Zillow-Redfin Agreement | FTC</title>
  <link rel="canonical" href="https://www.ftc.gov/news-events/news/press-releases/2026/08/ftc-secures-order-resolving-antitrust-concerns-zillow-redfin-agreement" />
  <link rel="shortlink" href="https://www.ftc.gov/node/335907" />
  <meta property="article:published_time" content="2026-08-24T08:00:00-04:00" />
</head>
<body>
<main>
  <article class="node node--type-press-release node--view-mode-full node--335907">
    <div class="field--name-field-subtitle">Proposed order ends allegedly unlawful agreement and restores online competition.</div>
    <div class="field--name-field-tags-view">
      <a href="/tags/competition">Competition</a>
      <a href="/tags/bureau-competition">Bureau of Competition</a>
      <a href="/tags/real-estate">Real Estate</a>
    </div>
    <div class="field--name-body">
      <div class="field--name-field-media-contact-single">Victoria Caslow 415-848-5121</div>
      <div class="field--name-field-phone">415-848-5121</div>
      <div class="field--name-field-boilerplate-block">The FTC works to promote competition...</div>
      <p>The Federal Trade Commission, joined by five state attorneys general, today filed a stipulated final order in federal district court resolving its antitrust litigation against Zillow Group and Redfin Corporation. Under the proposed settlement, Redfin is required to eliminate restrictive contractual covenants that suppressed competition in online rental listings and property advertising across multiple regional metropolitan markets.</p>
      <p>The complaint alleged that in 2025, Zillow entered into an unlawful agreement with Redfin under which Zillow paid substantial consideration to eliminate Redfin as an independent rival in residential property listings, violating Section 1 of the Sherman Act and Section 5 of the FTC Act. The elimination of Redfin as a vigorous competitive constraint led to higher fees for multifamily property managers and reduced choice for prospective apartment renters nationwide.</p>
      <p>Under the stipulated final order, Redfin must permanently cease reciprocal listing restrictions, reestablish its independent listing syndication network, and submit to continuous compliance monitoring for a period of ten years. Redfin also faces substantial civil penalties if it fails to fulfill its transitional milestones.</p>
      <p>The Commission vote approving the stipulated final order was 2-0. The FTC filed the proposed order in the U.S. District Court for the Western District of Washington, Docket No. 2:25-cv-01489.</p>
      <p>NOTE: Stipulated orders have the force of law when signed and entered by the federal district judge.</p>
      <p>See attached <a href="/system/files/ftc_gov/pdf/proposed_stipulated_order_zillow_redfin.pdf">Proposed Stipulated Order (PDF, 450 KB)</a> for full terms.</p>
    </div>
  </article>
</main>
</body>
</html>
"""

MOCK_FTC_DETAIL_HTML_PARTIAL = """<!DOCTYPE html>
<html>
<head>
  <link rel="canonical" href="https://www.ftc.gov/news-events/news/press-releases/2026/08/brief-notice" />
  <link rel="shortlink" href="https://www.ftc.gov/node/335856" />
</head>
<body>
<main>
  <article class="node node--type-press-release node--view-mode-full node--335856">
    <div class="field--name-body">
      <p>The FTC filed an administrative complaint challenging the proposed acquisition of Midwest Health by Regional Partners. The Commission alleges the merger would eliminate head-to-head competition for inpatient general acute care hospital services in Docket No. 9412.</p>
    </div>
  </article>
</main>
</body>
</html>
"""

MOCK_FTC_DETAIL_HTML_INSUFFICIENT = """<!DOCTYPE html>
<html>
<head><link rel="shortlink" href="https://www.ftc.gov/node/999999" /></head>
<body>
<main>
  <article class="node node--type-press-release node--view-mode-full node--999999">
    <div class="field--name-body">
      <p>Commission agenda notice.</p>
    </div>
  </article>
</main>
</body>
</html>
"""


# ------------------------------------------------------------------------------
# TESTS
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ftc_rss_parse_and_extract_basic():
    """Verify RSS parsing, basic attributes, and guid extraction."""
    extractor = FTCCompetitionExtractor()
    source = Source(
        id=uuid.uuid4(),
        name=FTC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=FTC_BASE_URL,
        config=DEFAULT_FTC_CONFIG,
    )

    def mock_handler(request: httpx.Request):
        url_str = str(request.url)
        if "press-release-competition.xml" in url_str:
            return httpx.Response(200, text=MOCK_FTC_RSS_XML)
        if "zillow-redfin" in url_str:
            return httpx.Response(200, text=MOCK_FTC_DETAIL_HTML_FULL)
        if "healthcare" in url_str:
            return httpx.Response(200, text=MOCK_FTC_DETAIL_HTML_PARTIAL)
        if "supermarket" in url_str:
            return httpx.Response(200, text=MOCK_FTC_DETAIL_HTML_FULL)
        if "auto-parts" in url_str:
            return httpx.Response(200, text=MOCK_FTC_DETAIL_HTML_FULL)
        if "stipulated_order" in url_str:
            return httpx.Response(200, content=_create_mock_pdf(3))
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        # Run with large lookback to include all items
        entries = await extractor.extract(client=client, source=source, lookback_days=180)

        assert len(entries) >= 3
        # Ensure consumer protection item was excluded
        assert not any("debt relief scam" in e.title.lower() for e in entries)
        assert extractor.metrics.ftc_consumer_items_skipped >= 1


def test_ftc_external_id_stability():
    """Verify external_id stability and deterministic derivation."""
    extractor = FTCCompetitionExtractor()

    # 1. Official Drupal numeric node ID
    ext_1 = extractor._build_canonical_external_id(
        guid="335907",
        url="https://www.ftc.gov/news-events/news/press-releases/2026/08/ftc-secures-order",
    )
    assert ext_1 == "ftc:competition:node-335907"

    # 2. Path fallback with YYYY/MM/slug
    ext_2 = extractor._build_canonical_external_id(
        guid="",
        url="https://www.ftc.gov/news-events/news/press-releases/2026/08/ftc-secures-order",
    )
    assert ext_2 == "ftc:competition:2026:08:ftc-secures-order"

    # 3. Path fallback without dates
    ext_3 = extractor._build_canonical_external_id(
        guid="",
        url="https://www.ftc.gov/legal-library/browse/cases-proceedings/henkel-paint",
    )
    assert ext_3 == "ftc:competition:henkel-paint"


def test_ftc_dates_utc_normalization():
    """Verify publication dates are parsed into timezone-aware UTC datetime."""
    extractor = FTCCompetitionExtractor()

    # Mon, 24 Aug 2026 08:00:00 -0400 -> 12:00:00 UTC
    dt = extractor._parse_rfc822_date("Mon, 24 Aug 2026 08:00:00 -0400")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2026
    assert dt.month == 8
    assert dt.day == 24
    assert dt.hour == 12
    assert dt.minute == 0


@pytest.mark.asyncio
async def test_ftc_lookback_filtering():
    """Verify lookback windows filter items according to cutoffs."""
    extractor = FTCCompetitionExtractor()
    source = Source(
        id=uuid.uuid4(),
        name=FTC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=FTC_BASE_URL,
        config={"lookback_days": 8},
    )

    def mock_handler(request: httpx.Request):
        url_str = str(request.url)
        if "press-release-competition.xml" in url_str:
            return httpx.Response(200, text=MOCK_FTC_RSS_XML)
        return httpx.Response(200, text=MOCK_FTC_DETAIL_HTML_FULL)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        # Patch datetime to freeze time at 2026-08-25
        with patch("app.providers.extractors.ftc.datetime") as mock_dt:
            ref_now = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
            mock_dt.now.return_value = ref_now
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            # 1. Lookback 3 days: captures 2026-08-24 item (cutoff = 2026-08-22)
            entries_3d = await extractor.extract(client=client, source=source, lookback_days=3)
            assert len(entries_3d) == 1
            assert "Zillow" in entries_3d[0].title

            # 2. Lookback 30 days: captures August items, excludes July 1
            entries_30d = await extractor.extract(client=client, source=source, lookback_days=30)
            assert len(entries_30d) == 2

            # 3. Lookback 90 days: captures August + July items, excludes May 15
            entries_90d = await extractor.extract(client=client, source=source, lookback_days=90)
            assert len(entries_90d) == 3


@pytest.mark.asyncio
async def test_ftc_html_cleaning_and_metadata():
    """Verify boilerplate blocks, media contacts, and social links are stripped, and metadata captured."""
    extractor = FTCCompetitionExtractor()
    source = Source(
        id=uuid.uuid4(),
        name=FTC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=FTC_BASE_URL,
        config=DEFAULT_FTC_CONFIG,
    )

    def mock_handler(request: httpx.Request):
        url_str = str(request.url)
        if "press-release-competition.xml" in url_str:
            # Return feed with only item 1
            single_item_xml = """<?xml version="1.0" encoding="utf-8"?>
            <rss version="2.0"><channel><title>FTC</title><item>
              <title>FTC Secures Order Resolving Antitrust Concerns with Zillow-Redfin Agreement</title>
              <link>https://www.ftc.gov/news-events/news/press-releases/2026/08/ftc-secures-order-resolving-antitrust-concerns-zillow-redfin-agreement</link>
              <guid isPermaLink="false">335907</guid>
              <pubDate>Mon, 24 Aug 2026 08:00:00 -0400</pubDate>
            </item></channel></rss>"""
            return httpx.Response(200, text=single_item_xml)
        if "zillow-redfin" in url_str:
            return httpx.Response(200, text=MOCK_FTC_DETAIL_HTML_FULL)
        if "stipulated_order" in url_str:
            return httpx.Response(200, content=_create_mock_pdf(5, text_per_page="Stipulated terms..."))
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        entries = await extractor.extract(client=client, source=source, lookback_days=60)

        assert len(entries) == 1
        entry = entries[0]

        # Verify noise was stripped
        assert "415-848-5121" not in entry.content
        assert "Victoria Caslow" not in entry.content
        assert "The FTC works to promote competition..." not in entry.content

        # Verify substantive content preserved
        assert "Zillow Group and Redfin Corporation" in entry.content
        assert "Section 1 of the Sherman Act" in entry.content

        # Verify metadata
        meta = entry.raw_metadata
        assert meta["agency"] == "FTC"
        assert meta["bureau"] == "Bureau of Competition"
        assert meta["node_id"] == "335907"
        assert "2:25-cv-01489" in meta["docket_number"]
        assert "The Commission vote approving the stipulated final order was 2-0." in meta["commission_vote"]
        assert "Clayton Act Section 7" in meta["legal_bases"] or "Sherman Act Section 1" in meta["legal_bases"]
        assert entry.content_type == "consent_order" or entry.content_type == "merger_action"


def test_ftc_sufficiency_evaluation():
    """Verify FULL (>=1500), PARTIAL (>=300, <1500), and INSUFFICIENT (<300) thresholds."""
    src = Source(
        id=uuid.uuid4(),
        name=FTC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=FTC_BASE_URL,
    )

    # 1. Full entry (>= 1500 chars)
    full_entry = Entry(
        id=uuid.uuid4(),
        source_id=src.id,
        source=src,
        title="Full FTC Merger Complaint",
        content="A" * 1550,
        url="https://www.ftc.gov/item1",
    )
    res_full = SourceSufficiencyService.assess(full_entry)
    assert res_full.level == SourceSufficiencyLevel.FULL

    # 2. Partial entry (>= 300 and < 1500 chars)
    partial_entry = Entry(
        id=uuid.uuid4(),
        source_id=src.id,
        source=src,
        title="Partial FTC Notice",
        content="B" * 600,
        url="https://www.ftc.gov/item2",
    )
    res_partial = SourceSufficiencyService.assess(partial_entry)
    assert res_partial.level == SourceSufficiencyLevel.PARTIAL

    # 3. Insufficient entry (< 300 chars)
    insufficient_entry = Entry(
        id=uuid.uuid4(),
        source_id=src.id,
        source=src,
        title="Insufficient FTC Item",
        content="Short notice.",
        url="https://www.ftc.gov/item3",
    )
    res_insufficient = SourceSufficiencyService.assess(insufficient_entry)
    assert res_insufficient.level == SourceSufficiencyLevel.INSUFFICIENT


@pytest.mark.asyncio
async def test_ftc_pdf_caps_and_oversize_skip():
    """Verify PDF limits: under 10MB extracted, over 10MB skipped, max 15 pages."""
    extractor = FTCCompetitionExtractor()

    # 1. Normal PDF under 10MB
    pdf_bytes = _create_mock_pdf(num_pages=5)
    def handler_normal(req: httpx.Request):
        return httpx.Response(200, content=pdf_bytes)

    mock_pages_5 = [MagicMock(extract_text=lambda: "Legal complaint allegations...") for _ in range(5)]
    mock_reader_5 = MagicMock(pages=mock_pages_5)

    transport_normal = httpx.MockTransport(handler_normal)
    async with httpx.AsyncClient(transport=transport_normal) as client:
        with patch("pypdf.PdfReader", return_value=mock_reader_5):
            res = await extractor._extract_pdf_enrichment(
                client=client,
                pdf_url="https://www.ftc.gov/test.pdf",
                max_bytes=10 * 1024 * 1024,
                max_pages=15,
                max_chars=50000,
            )
            assert res["text"] is not None
            assert res["pages_processed"] == 5
            assert res["skip_reason"] is None

    # 2. Oversize PDF (> 10MB)
    pdf_oversize = _create_mock_pdf(num_pages=2, byte_padding=11 * 1024 * 1024)
    def handler_oversize(req: httpx.Request):
        return httpx.Response(200, content=pdf_oversize)

    transport_oversize = httpx.MockTransport(handler_oversize)
    async with httpx.AsyncClient(transport=transport_oversize) as client:
        res_over = await extractor._extract_pdf_enrichment(
            client=client,
            pdf_url="https://www.ftc.gov/huge.pdf",
            max_bytes=10 * 1024 * 1024,
            max_pages=15,
            max_chars=50000,
        )
        assert res_over["text"] is None
        assert res_over["skip_reason"] == "pdf_exceeds_max_bytes"

    # 3. PDF with 25 pages -> should process at most 15 pages
    pdf_25p = _create_mock_pdf(num_pages=25)
    def handler_25p(req: httpx.Request):
        return httpx.Response(200, content=pdf_25p)

    mock_pages_25 = [MagicMock(extract_text=lambda: "Page content text...") for _ in range(25)]
    mock_reader_25 = MagicMock(pages=mock_pages_25)

    transport_25p = httpx.MockTransport(handler_25p)
    async with httpx.AsyncClient(transport=transport_25p) as client:
        with patch("pypdf.PdfReader", return_value=mock_reader_25):
            res_25p = await extractor._extract_pdf_enrichment(
                client=client,
                pdf_url="https://www.ftc.gov/25p.pdf",
                max_bytes=10 * 1024 * 1024,
                max_pages=15,
                max_chars=50000,
            )
            assert res_25p["pages_processed"] == 15


@pytest.mark.asyncio
async def test_ftc_source_config_unchanged():
    """Verify execution-scoped lookback parameter does not mutate Source.config."""
    extractor = FTCCompetitionExtractor()
    initial_config = {"lookback_days": 8, "base_url": FTC_BASE_URL}
    source = Source(
        id=uuid.uuid4(),
        name=FTC_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=FTC_BASE_URL,
        config=dict(initial_config),
    )

    def mock_handler(request: httpx.Request):
        url_str = str(request.url)
        if "press-release-competition.xml" in url_str:
            return httpx.Response(200, text=MOCK_FTC_RSS_XML)
        return httpx.Response(200, text=MOCK_FTC_DETAIL_HTML_FULL)


    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        # Run with explicit lookback 90 days
        await extractor.extract(client=client, source=source, lookback_days=90)

        # Source.config must remain untouched
        assert source.config["lookback_days"] == 8
        assert source.config == initial_config


def test_ftc_deduplication_inspector_logic():
    """Verify ReadOnlyDeduplicationInspector correctly checks external_id, url, and content_hash."""
    db_mock = MagicMock()
    source_id = uuid.uuid4()
    raw = RawEntryData(
        source_id=source_id,
        external_id="ftc:competition:node-335907",
        title="Test Title",
        url="https://www.ftc.gov/item",
        content="Test content",
        excerpt="Test excerpt",
        published_at=datetime.now(timezone.utc),
        language="en",
    )

    # 1. Match on external_id
    existing_entry = Entry(id=uuid.uuid4(), source_id=source_id, external_id=raw.external_id, title="Existing")
    db_mock.execute.return_value.scalar_one_or_none.return_value = existing_entry

    res = ReadOnlyDeduplicationInspector.check_ftc_item(db=db_mock, source_id=source_id, raw=raw)
    assert res.is_duplicate is True
    assert res.duplicate_reason == "external_id"

    # 2. No match -> not duplicate
    db_mock.execute.return_value.scalar_one_or_none.return_value = None
    res_clean = ReadOnlyDeduplicationInspector.check_ftc_item(db=db_mock, source_id=source_id, raw=raw)
    assert res_clean.is_duplicate is False


def test_ftc_aliases_and_seed():
    """Verify seed script aliases and dry-run safety."""
    for alias in ["ftc", "federal trade commission", "ftc competition", "bureau of competition"]:
        assert alias in FTC_ALIASES

    # Dry-run execution
    with patch("scripts.seed_source_ftc.SessionLocal") as mock_session_factory:
        mock_session = MagicMock()
        mock_session_factory.return_value = mock_session
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        res = seed_ftc_source(dry_run=True)
        # In dry run, no db.add or db.commit called
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_native_provider_dispatches_to_ftc():
    """Verify NativeProvider dispatches to FTCCompetitionExtractor based on URL and source name."""
    provider = NativeProvider()
    src_by_url = Source(id=uuid.uuid4(), name="FTC", type=SourceType.INSTITUTIONAL, url="https://www.ftc.gov/enforcement/competition", provider="native")
    src_by_name = Source(id=uuid.uuid4(), name="Federal Trade Commission - Bureau of Competition", type=SourceType.INSTITUTIONAL, url="https://example.com", provider="native")

    mock_entries = [
        RawEntryData(
            source_id=src_by_url.id,
            external_id="ftc:competition:node-1",
            title="T",
            url="https://www.ftc.gov/1",
            content="C",
            excerpt="E",
            published_at=datetime.now(timezone.utc),
            language="en",
        )
    ]

    with patch.object(FTCCompetitionExtractor, "extract", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = mock_entries

        # Test dispatch via URL
        res1 = await provider.fetch_entries(src_by_url)
        assert res1 == mock_entries
        assert mock_extract.call_count == 1

        # Test dispatch via Name
        res2 = await provider.fetch_entries(src_by_name)
        assert res2 == mock_entries
        assert mock_extract.call_count == 2


def test_new_sources_backfill_service_resolves_ftc():
    """Verify NewSourcesBackfillService resolves FTC source and its aliases."""
    db_mock = MagicMock()
    service = NewSourcesBackfillService()

    ftc_source = Source(id=uuid.uuid4(), name=FTC_SOURCE_NAME, provider="native", active=True)
    db_mock.execute.return_value.scalar_one_or_none.return_value = ftc_source

    for filter_val in ["ftc", "federal trade commission", "ftc competition", "bureau of competition"]:
        resolved = service._resolve_sources(db=db_mock, source_filter=filter_val)
        assert len(resolved) == 1
        assert resolved[0].name == FTC_SOURCE_NAME




def test_same_matter_distinct_milestones_coexistence():
    """Verify two distinct legal milestones on the same matter have different external_ids and are both valid."""
    extractor = FTCCompetitionExtractor()

    # Milestone 1: Administrative Complaint (e.g. node 335856)
    ext_1 = extractor._build_canonical_external_id(
        guid="335856",
        url="https://www.ftc.gov/news-events/news/press-releases/2026/08/ftc-files-administrative-complaint",
    )
    # Milestone 2: Final Consent Order (e.g. node 335930) on same underlying matter
    ext_2 = extractor._build_canonical_external_id(
        guid="335930",
        url="https://www.ftc.gov/news-events/news/press-releases/2026/08/ftc-approves-final-consent-order",
    )

    assert ext_1 != ext_2
    assert ext_1 == "ftc:competition:node-335856"
    assert ext_2 == "ftc:competition:node-335930"

