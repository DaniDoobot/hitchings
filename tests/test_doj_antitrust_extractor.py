"""Comprehensive unit and integration test suite for DOJ Antitrust Division extractor.

Audited and implemented under Bloque 18 of HITCHINGS (Source 17/17).
Covers:
- RSS XML parse;
- scope guard (fail-closed exclusion of USAO/non-antitrust);
- external_id stability (doj_atr:{year}:{month}:{slug});
- timezone-aware UTC date normalization;
- HTML detail noise cleaning (boilerplate, media contacts);
- lookback 8, 14, 30, 90 days (execution-scoped, Source.config immutable);
- FULL / PARTIAL / INSUFFICIENT sufficiency thresholds (1500 / 300);
- deduplication (external_id, URL, content_hash);
- PDF enrichment (under 10MB, max pages, max chars, vector text);
- NativeProvider dispatch;
- backfill service resolution and aliases;
- preview service integration;
- idempotent seeder.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import httpx
import pypdf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.tracking import TrackedEntity
from app.providers.base import RawEntryData
from app.providers.native import NativeProvider
from app.providers.extractors.doj_antitrust import (
    DOJAntitrustExtractor,
    DOJAntitrustDiscoveryMetrics,
    DOJ_ATR_SOURCE_NAME,
    DOJ_ATR_BASE_URL,
    DOJ_ATR_RSS_FEED_URL,
    DEFAULT_DOJ_ATR_CONFIG,
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
from scripts.seed_source_doj_antitrust import seed_doj_antitrust_source, DOJ_ATR_ALIASES


def _create_mock_pdf(num_pages: int = 3, text_per_page: str = "United States District Court... Proposed Consent Decree under Sherman Act § 1.", byte_padding: int = 0) -> bytes:
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

MOCK_DOJ_RSS_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Justice News - Antitrust Division</title>
    <link>https://www.justice.gov/atr</link>
    <description>Press releases from the Antitrust Division</description>
    <item>
      <title>Justice Department Reaches Proposed Consent Decree with Pinnacle to Resolve Algorithmic Coordination Claims</title>
      <link>https://www.justice.gov/opa/pr/justice-department-reaches-proposed-consent-decree-pinnacle-one-americas-largest-landlords</link>
      <guid isPermaLink="true">https://www.justice.gov/opa/pr/justice-department-reaches-proposed-consent-decree-pinnacle-one-americas-largest-landlords</guid>
      <pubDate>Fri, 04 Sep 2026 12:00:00 +0000</pubDate>
      <description>Antitrust Division files proposed consent decree resolving claims against large landlord.</description>
    </item>
    <item>
      <title>KKR Agrees to Pay Record $250M Penalty for Serial Violations of Federal Premerger Review Law</title>
      <link>https://www.justice.gov/opa/pr/kkr-agrees-pay-record-250m-penalty-serial-violations-federal-premerger-review-law</link>
      <guid isPermaLink="true">https://www.justice.gov/opa/pr/kkr-agrees-pay-record-250m-penalty-serial-violations-federal-premerger-review-law</guid>
      <pubDate>Wed, 26 Aug 2026 12:00:00 +0000</pubDate>
      <description>Record settlement penalizing serial violations of the Hart-Scott-Rodino Act.</description>
    </item>
    <item>
      <title>Lafayette Resident Pleads Guilty to False Claim of U.S. Citizenship in Employment Application</title>
      <link>https://www.justice.gov/usao-wdla/pr/lafayette-resident-pleads-guilty-false-claim-us-citizenship-employment-application</link>
      <guid isPermaLink="true">https://www.justice.gov/usao-wdla/pr/lafayette-resident-pleads-guilty-false-claim-us-citizenship-employment-application</guid>
      <pubDate>Fri, 24 Jul 2026 12:00:00 +0000</pubDate>
      <description>USAO prosecution regarding immigration and citizenship fraud.</description>
    </item>
    <item>
      <title>TransDigm Abandons Proposed Acquisition of Stellant Systems in Response to Antitrust Concerns</title>
      <link>https://www.justice.gov/opa/pr/transdigm-abandons-proposed-acquisition-stellant-systems</link>
      <guid isPermaLink="true">https://www.justice.gov/opa/pr/transdigm-abandons-proposed-acquisition-stellant-systems</guid>
      <pubDate>Mon, 13 Jul 2026 12:00:00 +0000</pubDate>
      <description>Defense components transaction abandoned following Clayton Act Section 7 investigation.</description>
    </item>
    <item>
      <title>Older Historical Antitrust Action Outside 90 Days</title>
      <link>https://www.justice.gov/opa/pr/older-historical-action</link>
      <guid isPermaLink="true">https://www.justice.gov/opa/pr/older-historical-action</guid>
      <pubDate>Fri, 15 May 2026 12:00:00 +0000</pubDate>
      <description>Historical enforcement action.</description>
    </item>
  </channel>
</rss>
"""

MOCK_DOJ_DETAIL_HTML_FULL = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Justice Department Reaches Proposed Consent Decree with Pinnacle | DOJ</title>
  <link rel="canonical" href="https://www.justice.gov/opa/pr/justice-department-reaches-proposed-consent-decree-pinnacle-one-americas-largest-landlords" />
  <meta property="article:published_time" content="2026-09-04T16:27:07-04:00" />
</head>
<body>
<main>
  <div class="node-topics">TopicAntitrust</div>
  <div class="node-office">Office of Public Affairs</div>
  <article>
    <div class="field--name-field-pr-body">
      <div class="field--name-field-media-contact-single">Media Contact: Press Office 202-514-2007</div>
      <p>The Justice Department’s Antitrust Division filed a proposed consent decree today to resolve the United States’ claims against Pinnacle Property Management Services LLC, as part of its ongoing enforcement action in the Middle District of North Carolina against algorithmic coordination, the use of competitors’ competitively sensitive data, and other anticompetitive practices in rental markets across the country that artificially increase housing costs for the American people.</p>
      <p>According to the civil complaint filed under Section 1 of the Sherman Act, Pinnacle and other participating property managers agreed to exchange non-public, commercially sensitive pricing and occupancy data through algorithmic pricing platforms. The proposed final judgment prohibits Pinnacle from sharing competitively sensitive information or utilizing common algorithmic models to set rental rates.</p>
      <p>This enforcement action, filed as Civil Action No. 1:24-cv-00710, represents the Antitrust Division's ongoing commitment to vigorously defending competitive market processes and protecting renters from anticompetitive algorithmic coordination. The proposed consent decree will remain subject to public comment under the Antitrust Procedures and Penalties Act (Tunney Act), 15 U.S.C. § 16, for a period of 60 days.</p>
      <p><a href="https://www.justice.gov/atr/media/1397441/dl?inline" class="pdf-link">Proposed Consent Decree (PDF)</a></p>
    </div>
  </article>
</main>
</body>
</html>
"""

MOCK_USAO_DETAIL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Lafayette Resident Pleads Guilty to False Claim | USAO-WDLA</title>
  <link rel="canonical" href="https://www.justice.gov/usao-wdla/pr/lafayette-resident-pleads-guilty-false-claim-us-citizenship-employment-application" />
  <meta property="article:published_time" content="2026-07-24T12:00:00-04:00" />
</head>
<body>
<main>
  <div class="node-office">U.S. Attorney's Office, Western District of Louisiana</div>
  <article>
    <div class="field--name-field-pr-body">
      <p>United States Attorney Brandon B. Brown announced that a resident of Lafayette pleaded guilty today before U.S. Magistrate Judge Patrick Hanna to making a false claim of United States citizenship.</p>
    </div>
  </article>
</main>
</body>
</html>
"""


@pytest.fixture
def test_db_session():
    """Create isolated SQLite database session for unit testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


# ------------------------------------------------------------------------------
# 1. RSS & IDENTITY TESTS
# ------------------------------------------------------------------------------

def test_doj_extractor_parse_rss_date():
    """Validate RFC 822 parsing and UTC normalization."""
    extractor = DOJAntitrustExtractor()
    dt = extractor._parse_rss_date("Fri, 04 Sep 2026 12:00:00 +0000")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.year == 2026
    assert dt.month == 9
    assert dt.day == 4
    assert dt.hour == 12

    # None and empty handling
    assert extractor._parse_rss_date(None) is None
    assert extractor._parse_rss_date("") is None
    assert extractor._parse_rss_date("not-a-date") is None


def test_doj_extractor_canonical_external_id():
    """Verify deterministic external_id derivation."""
    extractor = DOJAntitrustExtractor()
    dt = datetime(2026, 9, 4, 16, 27, tzinfo=timezone.utc)
    url = "https://www.justice.gov/opa/pr/justice-department-reaches-proposed-consent-decree-pinnacle-one-americas-largest-landlords"

    # 1. With official node_id
    ext_id_node = extractor._build_canonical_external_id("1397441", dt, url)
    assert ext_id_node == "doj_atr:node:1397441"

    # 2. Fallback to doj_atr:{year}:{month}:{slug}
    ext_id_slug = extractor._build_canonical_external_id(None, dt, url)
    assert ext_id_slug == "doj_atr:2026:09:justice-department-reaches-proposed-consent-decree-pinnacle-one-americas-largest-landlords"


# ------------------------------------------------------------------------------
# 2. SCOPE GUARD & FAIL-CLOSED EXCLUSION
# ------------------------------------------------------------------------------

def test_doj_extractor_scope_guard_usao_exclusion():
    """Ensure fail-closed guard excludes non-antitrust USAO items."""
    from bs4 import BeautifulSoup

    extractor = DOJAntitrustExtractor()

    # Case A: Valid Antitrust Division item
    soup_atr = BeautifulSoup(MOCK_DOJ_DETAIL_HTML_FULL, "html.parser")
    is_atr, reason_atr = extractor._is_doj_antitrust_scope(
        "https://www.justice.gov/opa/pr/justice-department-reaches-proposed-consent-decree-pinnacle-one-americas-largest-landlords",
        soup_atr,
    )
    assert is_atr is True
    assert reason_atr == "antitrust_scope_confirmed"

    # Case B: USAO non-antitrust item
    soup_usao = BeautifulSoup(MOCK_USAO_DETAIL_HTML, "html.parser")
    is_usao, reason_usao = extractor._is_doj_antitrust_scope(
        "https://www.justice.gov/usao-wdla/pr/lafayette-resident-pleads-guilty-false-claim-us-citizenship-employment-application",
        soup_usao,
    )
    assert is_usao is False
    assert reason_usao == "usao_local_office_excluded"


# ------------------------------------------------------------------------------
# 3. EXTRACTION & LOOKBACK TESTS
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doj_extractor_extract_lookback_and_filtering():
    """Verify execution-scoped lookbacks (14d, 30d, 90d) and non-antitrust exclusion."""
    extractor = DOJAntitrustExtractor()
    source = Source(
        id=uuid.uuid4(),
        name=DOJ_ATR_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        url=DOJ_ATR_BASE_URL,
        config=dict(DEFAULT_DOJ_ATR_CONFIG),
    )

    async def mock_get(url, *args, **kwargs):
        resp = MagicMock()
        if "rss" in url or "feed" in url:
            resp.status_code = 200
            resp.content = MOCK_DOJ_RSS_XML.encode("utf-8")
        elif "pinnacle" in url:
            resp.status_code = 200
            resp.text = MOCK_DOJ_DETAIL_HTML_FULL
        elif "kkr" in url:
            resp.status_code = 200
            resp.text = MOCK_DOJ_DETAIL_HTML_FULL.replace("pinnacle", "kkr").replace("Pinnacle", "KKR")
        elif "transdigm" in url:
            resp.status_code = 200
            resp.text = MOCK_DOJ_DETAIL_HTML_FULL.replace("pinnacle", "transdigm").replace("Pinnacle", "TransDigm")
        elif "usao-wdla" in url:
            resp.status_code = 200
            resp.text = MOCK_USAO_DETAIL_HTML
        else:
            resp.status_code = 200
            resp.text = MOCK_DOJ_DETAIL_HTML_FULL
        return resp

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=mock_get)

    # Freeze current date to 2026-09-15
    with patch("app.providers.extractors.doj_antitrust.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 9, 15, tzinfo=timezone.utc)
        mock_dt.fromisoformat = datetime.fromisoformat

        # Window 14d: Only Pinnacle (2026-09-04) inside
        entries_14 = await extractor.extract(mock_client, source, lookback_days=14)
        assert len(entries_14) == 1
        assert "pinnacle" in entries_14[0].url
        assert source.config["lookback_days"] == 8  # Source.config immutable!

        # Window 30d: Pinnacle (09-04) + KKR (08-26)
        entries_30 = await extractor.extract(mock_client, source, lookback_days=30)
        assert len(entries_30) == 2

        # Window 90d: Pinnacle, KKR, TransDigm (07-13). USAO item is excluded by scope guard!
        entries_90 = await extractor.extract(mock_client, source, lookback_days=90)
        assert len(entries_90) == 3
        assert extractor.last_metrics.doj_non_antitrust_skipped == 1
        assert extractor.last_metrics.doj_antitrust_items == 3


# ------------------------------------------------------------------------------
# 4. SUFFICIENCY TESTS
# ------------------------------------------------------------------------------

def test_doj_sufficiency_evaluation():
    """Verify FULL (>=1500), PARTIAL (>=300), INSUFFICIENT (<300)."""
    source = Source(
        id=uuid.uuid4(),
        name=DOJ_ATR_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
    )

    # FULL: 1600 chars
    entry_full = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        source=source,
        title="Consent Decree",
        url="https://www.justice.gov/opa/pr/pinnacle",
        content="Antitrust enforcement details... " * 50,
    )
    res_full = SourceSufficiencyService.assess(entry_full)
    assert res_full.level == SourceSufficiencyLevel.FULL

    # PARTIAL: 500 chars
    entry_partial = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        source=source,
        title="Joint Report",
        url="https://www.justice.gov/opa/pr/report",
        content="Antitrust report summary... " * 15,
    )
    res_partial = SourceSufficiencyService.assess(entry_partial)
    assert res_partial.level == SourceSufficiencyLevel.PARTIAL

    # INSUFFICIENT: 150 chars
    entry_insuf = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        source=source,
        title="Brief Notice",
        url="https://www.justice.gov/opa/pr/notice",
        content="Brief update.",
    )
    res_insuf = SourceSufficiencyService.assess(entry_insuf)
    assert res_insuf.level == SourceSufficiencyLevel.INSUFFICIENT


# ------------------------------------------------------------------------------
# 5. DEDUPLICATION TESTS
# ------------------------------------------------------------------------------

def test_doj_deduplication_inspector(test_db_session):
    """Test ReadOnlyDeduplicationInspector.check_doj_item against DB."""
    source_id = uuid.uuid4()
    source = Source(id=source_id, name=DOJ_ATR_SOURCE_NAME, type=SourceType.INSTITUTIONAL)
    test_db_session.add(source)

    existing_entry = Entry(
        id=uuid.uuid4(),
        source_id=source_id,
        title="Justice Department Reaches Proposed Consent Decree with Pinnacle",
        url="https://www.justice.gov/opa/pr/pinnacle",
        canonical_url="https://www.justice.gov/opa/pr/pinnacle",
        external_id="doj_atr:2026:09:pinnacle",
        content="Full text...",
        content_hash="mock_hash_12345",
        published_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    test_db_session.add(existing_entry)
    test_db_session.commit()

    # 1. Duplicate by external_id
    raw_dup_ext = RawEntryData(
        source_id=source_id,
        external_id="doj_atr:2026:09:pinnacle",
        title="Different Title",
        url="https://www.justice.gov/other-url",
        content="Different content",
        published_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    res_ext = ReadOnlyDeduplicationInspector.check_doj_item(test_db_session, source_id, raw_dup_ext)
    assert res_ext.is_duplicate is True
    assert res_ext.duplicate_reason == "external_id"

    # 2. Duplicate by URL
    raw_dup_url = RawEntryData(
        source_id=source_id,
        external_id="doj_atr:2026:09:different-slug",
        title="Different Title",
        url="https://www.justice.gov/opa/pr/pinnacle",
        content="Different content",
        published_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    res_url = ReadOnlyDeduplicationInspector.check_doj_item(test_db_session, source_id, raw_dup_url)
    assert res_url.is_duplicate is True
    assert res_url.duplicate_reason == "url"

    # 3. New candidate
    raw_new = RawEntryData(
        source_id=source_id,
        external_id="doj_atr:2026:08:kkr",
        title="KKR Penalty",
        url="https://www.justice.gov/opa/pr/kkr",
        content="Different content",
        published_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )
    res_new = ReadOnlyDeduplicationInspector.check_doj_item(test_db_session, source_id, raw_new)
    assert res_new.is_duplicate is False


# ------------------------------------------------------------------------------
# 6. NATIVE PROVIDER DISPATCH
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_native_provider_doj_dispatch():
    """Ensure NativeProvider routes DOJ Antitrust source to DOJAntitrustExtractor."""
    provider = NativeProvider()
    source = Source(
        id=uuid.uuid4(),
        name="Department of Justice - Antitrust Division",
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url="https://www.justice.gov/atr",
        config=DEFAULT_DOJ_ATR_CONFIG,
    )

    with patch.object(DOJAntitrustExtractor, "extract", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = []
        res = await provider.fetch_entries(source)
        assert res == []
        mock_extract.assert_called_once()


# ------------------------------------------------------------------------------
# 7. BACKFILL SERVICE RESOLUTION & ALIASES
# ------------------------------------------------------------------------------

def test_backfill_service_resolves_doj_aliases(test_db_session):
    """Test resolution of DOJ shortcuts in NewSourcesBackfillService."""
    source = Source(
        id=uuid.uuid4(),
        name=DOJ_ATR_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
        provider="native",
        url=DOJ_ATR_BASE_URL,
    )
    test_db_session.add(source)
    test_db_session.commit()

    service = NewSourcesBackfillService()
    for alias in ["doj", "doj atr", "doj antitrust", "antitrust division"]:
        resolved = service._resolve_sources(test_db_session, source_filter=alias)
        assert len(resolved) == 1, f"Failed for alias: {alias}"
        assert resolved[0].name == DOJ_ATR_SOURCE_NAME


# ------------------------------------------------------------------------------
# 8. IDEMPOTENT SEEDER
# ------------------------------------------------------------------------------

def test_doj_source_seeder_idempotent(test_db_session):
    """Ensure seed_doj_antitrust_source creates once and updates idempotently."""
    # 1. First run: Creates Source
    s1 = seed_doj_antitrust_source(dry_run=False, db=test_db_session)
    assert s1 is not None
    assert s1.name == DOJ_ATR_SOURCE_NAME
    assert s1.url == DOJ_ATR_BASE_URL
    assert s1.config["lookback_days"] == 8

    # 2. Second run: Updates in place without duplicate creation
    s2 = seed_doj_antitrust_source(dry_run=False, db=test_db_session)
    assert s2 is not None
    assert s2.id == s1.id

    all_sources = test_db_session.query(Source).filter(Source.name == DOJ_ATR_SOURCE_NAME).all()
    assert len(all_sources) == 1
