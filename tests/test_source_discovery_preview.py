"""Tests for read-only 90-day source discovery preview (Bloque 12F).

All external HTTP calls are strictly mocked with httpx.MockTransport.
Zero live network calls, zero DB mutations, zero Gemini calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models.analysis import AnalysisCall, EntryAnalysis
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity
from app.providers.base import RawEntryData
from app.services.ingestion_service import compute_content_hash, compute_ingestion_dedupe_hash
from app.services.source_sufficiency_service import SourceSufficiencyLevel
from scripts.preview_source_discovery import (
    GERADIN_SOURCE_NAME,
    DMA_SOURCE_NAME,
    OECD_SOURCE_NAME,
    ReadOnlyDeduplicationInspector,
    SourceDiscoveryPreviewService,
    print_preview_report,
)


# ==============================================================================
# MOCK HTML & JSON FIXTURES
# ==============================================================================

MOCK_GERADIN_LISTING_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>News & Insights – Geradin Partners</title></head>
<body>
<ul class="gp-post-template-block-news wp-block-post-template">
  <!-- 1. Substantive Briefing (inside 90-day lookback) -->
  <li class="wp-block-post category-monthly-eu-litigation-briefing">
    <a class="gp-news-post-card" href="/monthly-litigation-august-2026/">
      <div class="post-meta">
        <span class="post-category">Monthly EU Litigation Briefing</span>
        <time class="post-date">15/08/26</time>
      </div>
      <h2 class="post-title">Geradin Partners’ Monthly EU Litigation Briefing – August 2026</h2>
    </a>
  </li>

  <!-- 2. Substantive Newsletter (inside 90-day lookback) -->
  <li class="wp-block-post category-newsletters">
    <a class="gp-news-post-card" href="/platform-newsletter-august-2026/">
      <div class="post-meta">
        <span class="post-category">Newsletters</span>
        <time class="post-date">10/08/26</time>
      </div>
      <h2 class="post-title">Platform Newsletter Issue 42 - Gatekeeper Compliance</h2>
    </a>
  </li>

  <!-- 3. Excluded Corporate/HR Award Item -->
  <li class="wp-block-post category-news">
    <a class="gp-news-post-card" href="/geradin-partners-wins-gcr-award-2026/">
      <div class="post-meta">
        <span class="post-category">News</span>
        <time class="post-date">05/08/26</time>
      </div>
      <h2 class="post-title">Geradin Partners wins GCR 100 award for excellence</h2>
    </a>
  </li>

  <!-- 4. Older item outside 90-day lookback (e.g. 120 days ago) -->
  <li class="wp-block-post category-monthly-eu-litigation-briefing">
    <a class="gp-news-post-card" href="/monthly-litigation-april-2026/">
      <div class="post-meta">
        <span class="post-category">Monthly EU Litigation Briefing</span>
        <time class="post-date">10/04/26</time>
      </div>
      <h2 class="post-title">Geradin Partners’ Monthly EU Litigation Briefing – April 2026</h2>
    </a>
  </li>
</ul>
</body>
</html>
"""

MOCK_GERADIN_DETAIL_HTML_FULL = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Geradin Partners’ Monthly EU Litigation Briefing – August 2026</title>
  <link rel="canonical" href="https://www.geradinpartners.com/monthly-litigation-august-2026/" />
</head>
<body>
  <article class="post-content">
    <time class="post-date">15/08/2026</time>
    <div class="team-member"><a href="/team/damien-geradin/">Damien Geradin</a></div>
    <div class="column">
      <p>""" + ("Comprehensive antitrust litigation analysis of private enforcement and damage actions across EU Member States. " * 30) + """</p>
    </div>
  </article>
</body>
</html>
"""

MOCK_DMA_LISTING_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>News | Digital Markets Act</title></head>
<body>
<main>
  <div class="ecl-container">
    <article class="ecl-content-item">
      <ul class="ecl-content-block__primary-meta">
        <li class="ecl-content-block__primary-meta-item">News article</li>
        <li class="ecl-content-block__primary-meta-item">
          <time datetime="2026-08-10T12:00:00Z">10 August 2026</time>
        </li>
      </ul>
      <h1 class="ecl-content-block__title">
        <a href="/news/commission-opens-dma-compliance-investigation-2026-08-10_en" class="ecl-link">
          Commission opens non-compliance investigation under Digital Markets Act
        </a>
      </h1>
      <div class="ecl-content-block__description">
        Today the European Commission opened formal non-compliance proceedings under Article 20 DMA.
      </div>
    </article>

    <article class="ecl-content-item">
      <ul class="ecl-content-block__primary-meta">
        <li class="ecl-content-block__primary-meta-item">News article</li>
        <li class="ecl-content-block__primary-meta-item">
          <time datetime="2026-08-01T10:00:00Z">01 August 2026</time>
        </li>
      </ul>
      <h1 class="ecl-content-block__title">
        <a href="/news/commission-designates-new-core-platform-service-2026-08-01_en" class="ecl-link">
          Commission designates new core platform service under Digital Markets Act
        </a>
      </h1>
      <div class="ecl-content-block__description">
        Commission services adopted a designation decision following market investigation.
      </div>
    </article>
  </div>
</main>
</body>
</html>
"""

MOCK_DMA_DETAIL_WITH_PRESSCORNER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Commission opens non-compliance investigation under Digital Markets Act</title>
  <link rel="canonical" href="https://digital-markets-act.ec.europa.eu/news/commission-opens-dma-compliance-investigation-2026-08-10_en" />
</head>
<body>
  <div class="ecl-page-header__meta">
    <span class="ecl-meta__item">10 August 2026</span>
    <span class="ecl-meta__item">DG COMP</span>
  </div>
  <article>
    <div class="field--name-body">
      <p>""" + ("The European Commission has initiated formal proceedings against a designated gatekeeper under Regulation (EU) 2022/1925. " * 20) + """</p>
      <p>For full details see <a href="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1844">Press Release IP/26/1844</a>.</p>
    </div>
  </article>
</body>
</html>
"""

MOCK_DMA_DETAIL_UNIQUE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Commission designates new core platform service under Digital Markets Act</title>
  <link rel="canonical" href="https://digital-markets-act.ec.europa.eu/news/commission-designates-new-core-platform-service-2026-08-01_en" />
</head>
<body>
  <div class="ecl-page-header__meta">
    <span class="ecl-meta__item">01 August 2026</span>
    <span class="ecl-meta__item">DG CNECT</span>
  </div>
  <article>
    <div class="field--name-body">
      <p>""" + ("The Commission has adopted a designation decision under the Digital Markets Act following a market investigation. " * 25) + """</p>
    </div>
  </article>
</body>
</html>
"""

MOCK_CROSSREF_OECD_RESPONSE = {
    "status": "ok",
    "message": {
        "items": [
            # 1. Past item inside lookback (June 2026)
            {
                "DOI": "10.1787/1330d48b-en",
                "title": ["National security considerations in competition enforcement"],
                "publisher": "OECD",
                "container-title": ["OECD Roundtables on Competition Policy Papers"],
                "ISSN": ["2075-8677"],
                "type": "report",
                "published": {"date-parts": [[2026, 6, 20]]},
                "URL": "https://doi.org/10.1787/1330d48b-en",
                "resource": {
                    "primary": {
                        "URL": "https://www.oecd.org/en/publications/national-security-considerations_1330d48b-en.html"
                    }
                },
            },
            # 2. Past item inside lookback (July 2026)
            {
                "DOI": "10.1787/unique-dma-oecd-en",
                "title": ["Remedies and commitments in digital markets"],
                "publisher": "OECD",
                "container-title": ["OECD Roundtables on Competition Policy Papers"],
                "ISSN": ["2075-8677"],
                "type": "report",
                "published": {"date-parts": [[2026, 7, 15]]},
                "URL": "https://doi.org/10.1787/unique-dma-oecd-en",
                "resource": {
                    "primary": {
                        "URL": "https://www.oecd.org/en/publications/remedies-digital-markets_unique-dma-oecd-en.html"
                    }
                },
            },
            # 3. Future-dated item (September 14, 2026 when now is September 10)
            {
                "DOI": "10.1787/62c9a81e-en",
                "title": ["Early resolution of cartel cases in Latin America and the Caribbean"],
                "publisher": "OECD",
                "container-title": ["OECD Roundtables on Competition Policy Papers"],
                "ISSN": ["2075-8677"],
                "type": "report",
                "published": {"date-parts": [[2026, 9, 14]]},
                "URL": "https://doi.org/10.1787/62c9a81e-en",
            },
        ]
    }
}

MOCK_OPENALEX_ABSTRACT_RESPONSE = {
    "abstract_inverted_index": {
        "This": [0],
        "report": [1],
        "examines": [2],
        "substantive": [3],
        "competition": [4, 10],
        "policy": [5],
        "and": [6],
        "enforcement": [7],
        "remedies": [8],
        "in": [9],
        "markets.": [11],
    }
}


# ==============================================================================
# TESTS
# ==============================================================================

def test_preview_geradin_new_candidate(db_session: Session):
    """Verify Geradin Partners discovery identifies new candidate, computes sufficiency, and eligibility."""
    simulated_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

    def router(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "news" in url_str:
            return httpx.Response(200, text=MOCK_GERADIN_LISTING_HTML)
        elif "monthly-litigation" in url_str or "platform-newsletter" in url_str:
            return httpx.Response(200, text=MOCK_GERADIN_DETAIL_HTML_FULL)
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(router))

    service = SourceDiscoveryPreviewService(db=db_session, now=simulated_now)
    report = service.run_preview(
        lookback_days=90,
        source_filter="geradin",
        sync_client=mock_client,
    )

    assert len(report.sources_summaries) == 1
    g_summary = report.sources_summaries[0]
    assert g_summary.source_name == GERADIN_SOURCE_NAME
    assert g_summary.discovered_total == 4
    assert g_summary.excluded_editorially == 1  # Award post excluded
    assert g_summary.inside_lookback == 2  # 2 substantive within 90 days (April post is outside)
    assert g_summary.new_candidates == 2
    assert g_summary.duplicates == 0
    assert g_summary.full_count >= 1
    assert g_summary.eligible_for_analysis >= 1
    assert len(g_summary.new_items) == 2


def test_preview_geradin_duplicate_detection(db_session: Session):
    """Verify Geradin duplicate is correctly detected by canonical URL and content hash without DB mutation."""
    simulated_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

    # Pre-insert existing Entry into database
    source = Source(
        id=uuid.uuid4(),
        name=GERADIN_SOURCE_NAME,
        type=SourceType.BLOG,
        url="https://www.geradinpartners.com/news/",
    )
    db_session.add(source)
    existing_entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        url="https://www.geradinpartners.com/monthly-litigation-august-2026/",
        canonical_url="https://www.geradinpartners.com/monthly-litigation-august-2026/",
        title="Geradin Partners’ Monthly EU Litigation Briefing – August 2026",
        content="Existing substantive content...",
        content_hash="h1",
    )
    db_session.add(existing_entry)
    db_session.commit()

    def router(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "news" in url_str:
            return httpx.Response(200, text=MOCK_GERADIN_LISTING_HTML)
        return httpx.Response(200, text=MOCK_GERADIN_DETAIL_HTML_FULL)

    mock_client = httpx.Client(transport=httpx.MockTransport(router))

    service = SourceDiscoveryPreviewService(db=db_session, now=simulated_now)
    report = service.run_preview(
        lookback_days=90,
        source_filter="geradin",
        sync_client=mock_client,
    )

    g_summary = report.sources_summaries[0]
    assert g_summary.duplicates >= 1
    assert g_summary.new_candidates == 1  # Only the newsletter is new now


def test_preview_dma_presscorner_duplicate(db_session: Session):
    """Verify DMA candidate matching an existing Press Corner entry is classified as DUPLICATE with reason."""
    simulated_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

    # Pre-insert European Commission Press Corner Entry
    ec_source = Source(
        id=uuid.uuid4(),
        name="European Commission - Competition Policy",
        type=SourceType.INSTITUTIONAL,
    )
    db_session.add(ec_source)
    ec_entry = Entry(
        id=uuid.uuid4(),
        source_id=ec_source.id,
        url="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1844",
        canonical_url="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1844",
        external_id="ip_26_1844",
        title="Antitrust: Commission opens DMA investigation",
        content="Press release content...",
    )
    db_session.add(ec_entry)
    db_session.commit()

    def router(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "news_en" in url_str or "digital-markets-act" in url_str:
            if "compliance-investigation" in url_str:
                return httpx.Response(200, text=MOCK_DMA_DETAIL_WITH_PRESSCORNER_HTML)
            elif "designates-new-core" in url_str:
                return httpx.Response(200, text=MOCK_DMA_DETAIL_UNIQUE_HTML)
            return httpx.Response(200, text=MOCK_DMA_LISTING_HTML)
        return httpx.Response(404)

    mock_async_client = httpx.AsyncClient(transport=httpx.MockTransport(router))

    service = SourceDiscoveryPreviewService(db=db_session, now=simulated_now)
    report = service.run_preview(
        lookback_days=90,
        source_filter="dma",
        async_client=mock_async_client,
    )

    dma_summary = report.sources_summaries[0]
    assert dma_summary.discovered_total == 2
    assert dma_summary.duplicates == 1  # Matched ip_26_1844
    assert dma_summary.new_candidates == 1  # Designation article is new
    assert dma_summary.new_items[0].title == "Commission designates new core platform service under Digital Markets Act"


def test_preview_oecd_doi_duplicate_and_future_guard(db_session: Session):
    """Verify OECD DOI deduplication matches existing entry and future date guard filters out future items."""
    simulated_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

    # Pre-insert existing Entry with DOI 10.1787/1330d48b-en
    oecd_source = Source(
        id=uuid.uuid4(),
        name=OECD_SOURCE_NAME,
        type=SourceType.INSTITUTIONAL,
    )
    db_session.add(oecd_source)
    existing_oecd = Entry(
        id=uuid.uuid4(),
        source_id=oecd_source.id,
        external_id="10.1787/1330d48b-en",
        url="https://doi.org/10.1787/1330d48b-en",
        title="National security considerations in competition enforcement",
        content="Existing report...",
    )
    db_session.add(existing_oecd)
    db_session.commit()

    def router(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "crossref.org" in url_str:
            return httpx.Response(200, json=MOCK_CROSSREF_OECD_RESPONSE)
        elif "openalex.org" in url_str:
            return httpx.Response(200, json=MOCK_OPENALEX_ABSTRACT_RESPONSE)
        return httpx.Response(200, text="<html><body>Report HTML content</body></html>")

    mock_async_client = httpx.AsyncClient(transport=httpx.MockTransport(router))

    service = SourceDiscoveryPreviewService(db=db_session, now=simulated_now)
    report = service.run_preview(
        lookback_days=90,
        source_filter="oecd",
        async_client=mock_async_client,
    )

    oecd_summary = report.sources_summaries[0]
    assert oecd_summary.discovered_total == 3
    assert oecd_summary.excluded_future == 1  # 2026-09-14 item excluded by Future Date Guard
    assert oecd_summary.inside_lookback == 2
    assert oecd_summary.duplicates == 1  # 10.1787/1330d48b-en matched
    assert oecd_summary.new_candidates == 1  # 10.1787/unique-dma-oecd-en is new
    assert oecd_summary.new_items[0].title == "Remedies and commitments in digital markets"


def test_preview_strict_db_invariants_and_zero_mutations(db_session: Session):
    """Verify preview performs 0 DB writes, 0 commits, 0 EntryAnalyses, 0 AnalysisCalls."""
    simulated_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

    # Initial baseline
    count_entries_before = db_session.query(Entry).count()
    count_analyses_before = db_session.query(EntryAnalysis).count()
    count_calls_before = db_session.query(AnalysisCall).count()
    count_runs_before = db_session.query(IngestionRun).count()
    count_sources_before = db_session.query(Source).count()

    def sync_router(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=MOCK_GERADIN_LISTING_HTML)

    def async_router(req: httpx.Request) -> httpx.Response:
        url_str = str(req.url)
        if "crossref.org" in url_str:
            return httpx.Response(200, json=MOCK_CROSSREF_OECD_RESPONSE)
        elif "openalex.org" in url_str:
            return httpx.Response(200, json=MOCK_OPENALEX_ABSTRACT_RESPONSE)
        return httpx.Response(200, text=MOCK_DMA_LISTING_HTML)

    sync_client = httpx.Client(transport=httpx.MockTransport(sync_router))
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(async_router))

    service = SourceDiscoveryPreviewService(db=db_session, now=simulated_now)
    report = service.run_preview(
        lookback_days=90,
        sync_client=sync_client,
        async_client=async_client,
    )

    # Invariants verification
    assert db_session.query(Entry).count() == count_entries_before
    assert db_session.query(EntryAnalysis).count() == count_analyses_before
    assert db_session.query(AnalysisCall).count() == count_calls_before
    assert db_session.query(IngestionRun).count() == count_runs_before
    assert db_session.query(Source).count() == count_sources_before
    assert report.total_discovered >= 1


def test_preview_source_filter_options(db_session: Session):
    """Verify source filter correctly restricts discovery to the requested target."""
    simulated_now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

    def router(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=MOCK_GERADIN_LISTING_HTML)

    sync_client = httpx.Client(transport=httpx.MockTransport(router))

    service = SourceDiscoveryPreviewService(db=db_session, now=simulated_now)

    # Filter: geradin
    report_g = service.run_preview(source_filter="geradin", sync_client=sync_client)
    assert len(report_g.sources_summaries) == 1
    assert report_g.sources_summaries[0].source_name == GERADIN_SOURCE_NAME

    # Filter: dma
    report_dma = service.run_preview(source_filter="dma", async_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=MOCK_DMA_LISTING_HTML))))
    assert len(report_dma.sources_summaries) == 1
    assert report_dma.sources_summaries[0].source_name == DMA_SOURCE_NAME


def test_preview_lookback_cutoff_boundary(db_session: Session):
    """Verify lookback cutoff strictly excludes items published prior to (now - lookback_days)."""
    now = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)

    # Lookback 10 days => cutoff = 2026-08-31
    service = SourceDiscoveryPreviewService(db=db_session, now=now)

    def router(req: httpx.Request) -> httpx.Response:
        url_str = str(req.url)
        if "crossref.org" in url_str:
            return httpx.Response(200, json=MOCK_CROSSREF_OECD_RESPONSE)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    # All items in MOCK_CROSSREF_OECD_RESPONSE are July, June, or September 14 (future).
    # With 10 days lookback (cutoff 2026-08-31), none of the past items fall within 10 days.
    report = service.run_preview(lookback_days=10, source_filter="oecd", async_client=client)
    o_summary = report.sources_summaries[0]
    assert o_summary.inside_lookback == 0
    assert o_summary.new_candidates == 0


def test_print_preview_report_renders_cleanly(capsys):
    """Verify report formatting utility renders without raising exceptions."""
    from scripts.preview_source_discovery import (
        GlobalPreviewReport,
        SourcePreviewSummary,
        PreviewCandidateItem,
        print_preview_report,
    )

    report = GlobalPreviewReport(
        lookback_days=90,
        cutoff_date="2026-06-12",
        reference_date="2026-09-10",
        sources_summaries=[
            SourcePreviewSummary(
                source_name=GERADIN_SOURCE_NAME,
                discovered_total=5,
                inside_lookback=3,
                excluded_editorially=1,
                duplicates=1,
                new_candidates=2,
                full_count=2,
                eligible_for_analysis=2,
                estimated_input_chars=12000,
                new_items=[
                    PreviewCandidateItem(
                        date="2026-08-15",
                        source_name=GERADIN_SOURCE_NAME,
                        title="Litigation Briefing August 2026",
                        url="https://example.com/1",
                        is_duplicate=False,
                        sufficiency="full",
                        content_chars=6000,
                        eligible_for_analysis=True,
                    )
                ]
            )
        ],
        total_discovered=5,
        total_duplicates=1,
        total_new=2,
        total_full=2,
        potential_gemini_analyses=2,
        total_estimated_input_chars=12000,
        new_candidates_table=[
            PreviewCandidateItem(
                date="2026-08-15",
                source_name=GERADIN_SOURCE_NAME,
                title="Litigation Briefing August 2026",
                url="https://example.com/1",
                is_duplicate=False,
                sufficiency="full",
                content_chars=6000,
                eligible_for_analysis=True,
            )
        ]
    )

    print_preview_report(report)
    captured = capsys.readouterr().out
    assert "READ-ONLY SOURCE DISCOVERY PREVIEW" in captured
    assert "Geradin Partners" in captured
    assert "Litigation Briefing August 2026" in captured


def test_preview_geradin_google_news_cross_match(db_session: Session):
    """Verify Geradin candidate matching a Google News entry by domain + normalized title is marked duplicate."""
    gn_source = Source(
        id=uuid.uuid4(),
        name="Google News - Competition",
        type=SourceType.GOOGLE_NEWS,
    )
    db_session.add(gn_source)
    gn_entry = Entry(
        id=uuid.uuid4(),
        source_id=gn_source.id,
        url="https://news.google.com/rss/articles/CBMi...",
        title="Geradin Partners’ Monthly EU Litigation Briefing – August 2026",
        content="Brief snippet",
        raw_metadata={"publisher_domain": "geradinpartners.com"},
    )
    db_session.add(gn_entry)
    db_session.commit()

    res = ReadOnlyDeduplicationInspector.check_geradin_item(
        db=db_session,
        source_id=uuid.uuid4(),
        url="https://www.geradinpartners.com/monthly-litigation-august-2026/",
        canonical_url="https://www.geradinpartners.com/monthly-litigation-august-2026/",
        title="Geradin Partners’ Monthly EU Litigation Briefing – August 2026",
        excerpt="Snippet",
        existing_gn_entries=[gn_entry],
    )
    assert res.is_duplicate is True
    assert res.duplicate_reason == "google_news_match"
    assert res.matched_entry_id == str(gn_entry.id)


def test_preview_sufficiency_and_analysis_eligibility_classification(db_session: Session):
    """Verify source-aware sufficiency assessment and incremental analysis eligibility rules.

    Classification is delegated strictly to SourceSufficiencyService.assess(entry), which applies
    domain-specific rules (CAT judgments/summaries, CURIA documents, EC press releases, etc.)
    and generic fallbacks. The preview defines zero custom thresholds.
    """
    from app.services.incremental_analysis_planner import IncrementalAnalysisPlanner
    from app.services.source_sufficiency_service import SourceSufficiencyService

    source = Source(id=uuid.uuid4(), name="Generic Source", type=SourceType.WEBSITE)
    db_session.add(source)
    db_session.commit()

    # 1. SourceSufficiencyLevel.FULL -> eligible for analysis
    entry_full = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        source=source,
        title="Full Article",
        content="Substantive competition analysis. " * 60,
    )
    suff_full = SourceSufficiencyService.assess(entry_full)
    assert suff_full.level.value == "full"
    planner = IncrementalAnalysisPlanner(db_session)
    cand_full = planner.evaluate_entry(entry_full)
    assert cand_full.reason == "eligible"

    # 2. SourceSufficiencyLevel.PARTIAL -> NOT eligible
    entry_partial = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        source=source,
        title="Partial Article",
        content="Short update on court proceedings. " * 15,
    )
    suff_partial = SourceSufficiencyService.assess(entry_partial)
    assert suff_partial.level.value == "partial"
    cand_partial = planner.evaluate_entry(entry_partial)
    assert cand_partial.reason == "partial"

    # 3. SourceSufficiencyLevel.INSUFFICIENT -> NOT eligible
    entry_insufficient = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        source=source,
        title="Snippet",
        content="Brief summary.",
    )
    suff_insufficient = SourceSufficiencyService.assess(entry_insufficient)
    assert suff_insufficient.level.value == "insufficient"
    cand_insufficient = planner.evaluate_entry(entry_insufficient)
    assert cand_insufficient.reason == "insufficient"


def test_preview_fail_closed_on_mutation_attempt(db_session: Session):
    """Verify fail-closed ORM flush listener blocks any database flush containing new/dirty/deleted entities."""
    from scripts.preview_source_discovery import configure_read_only_session

    cleanups = configure_read_only_session(db_session)
    try:
        new_entry = Entry(id=uuid.uuid4(), title="Unauthorized ORM write")
        db_session.add(new_entry)

        with pytest.raises(RuntimeError, match="READ-ONLY VIOLATION: Preview attempted to modify database"):
            db_session.flush()
    finally:
        for cb in cleanups:
            cb()
        db_session.rollback()


def test_preview_fail_closed_on_raw_sql_mutation(db_session: Session):
    """Verify raw SQL mutation blocker intercepts and rejects direct INSERT/UPDATE/DELETE/DROP statements."""
    from sqlalchemy import text
    from scripts.preview_source_discovery import configure_read_only_session

    cleanups = configure_read_only_session(db_session)
    try:
        # 1. SELECT query is allowed
        res = db_session.execute(text("SELECT 1")).scalar()
        assert res == 1

        # 2. Direct UPDATE is blocked
        with pytest.raises(RuntimeError, match="READ-ONLY VIOLATION: Mutating SQL execution blocked"):
            db_session.execute(text("UPDATE entries SET title='injected' WHERE 1=1"))

        # 3. Direct INSERT is blocked
        with pytest.raises(RuntimeError, match="READ-ONLY VIOLATION: Mutating SQL execution blocked"):
            db_session.execute(text("INSERT INTO entries (id, title) VALUES ('foo', 'bar')"))

        # 4. Direct DELETE is blocked
        with pytest.raises(RuntimeError, match="READ-ONLY VIOLATION: Mutating SQL execution blocked"):
            db_session.execute(text("DELETE FROM entries WHERE 1=1"))

        # 5. Direct DROP is blocked
        with pytest.raises(RuntimeError, match="READ-ONLY VIOLATION: Mutating SQL execution blocked"):
            db_session.execute(text("DROP TABLE entries"))
    finally:
        for cb in cleanups:
            cb()
        db_session.rollback()


def test_preview_postgresql_isolation_and_read_only_configured():
    """Verify that for PostgreSQL dialect, SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY is issued first."""
    from unittest.mock import patch
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from scripts.preview_source_discovery import configure_read_only_session

    engine = create_engine("sqlite:///:memory:")
    engine.dialect.name = "postgresql"

    TestSession = sessionmaker(bind=engine)
    db = TestSession()

    executed_statements = []
    orig_execute = db.execute

    def spy_execute(stmt, *args, **kwargs):
        stmt_str = str(stmt)
        executed_statements.append(stmt_str)
        if "SET TRANSACTION" in stmt_str:
            return None
        return orig_execute(stmt, *args, **kwargs)

    with patch.object(db, "execute", side_effect=spy_execute):
        cleanups = configure_read_only_session(db)
        try:
            assert len(executed_statements) == 1
            assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in executed_statements[0]
        finally:
            for cb in cleanups:
                cb()
            db.close()

