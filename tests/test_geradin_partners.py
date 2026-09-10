"""Unit tests for Geradin Partners Direct Web Source (Bloque 12A).

All external network calls are strictly mocked with httpx.MockTransport.
Zero internet calls, zero Gemini calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from bs4 import BeautifulSoup
import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.url_utils import normalize_url
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity
from app.providers.direct_web.adapters.geradin_partners import (
    GeradinPartnersAdapter,
    EXCLUDED_CATEGORIES,
    CORPORATE_EXCLUSION_PATTERN,
)
from app.providers.direct_web.models import DiscoveredItem
from app.providers.direct_web.registry import (
    DirectWebAdapterRegistry,
    DirectWebUnknownAdapterError,
)
from app.services.direct_web_ingestion_service import DirectWebIngestionService
from app.services.source_sufficiency_service import (
    SourceSufficiencyLevel,
    SourceSufficiencyService,
)
from app.services.weekly_refresh_service import WeeklyRefreshService


# ==============================================================================
# FIXTURES & MOCK HTML DATA
# ==============================================================================

MOCK_GERADIN_LISTING_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>News & Insights – Geradin Partners</title></head>
<body>
<ul class="gp-post-template-block-news wp-block-post-template">
  <!-- 1. Substantive Briefing (Should be INCLUDED) -->
  <li class="wp-block-post category-monthly-eu-litigation-briefing">
    <a class="gp-news-post-card" href="/geradin-partners-monthly-eu-litigation-briefing-july-august-2026/">
      <div class="post-meta">
        <span class="post-category">Monthly EU Litigation Briefing</span>
        <time class="post-date">02/09/26</time>
      </div>
      <h2 class="post-title">Geradin Partners’ Monthly EU Litigation Briefing – July/August 2026</h2>
    </a>
  </li>

  <!-- 2. Substantive Settlement in Announcements (Should be INCLUDED) -->
  <li class="wp-block-post category-announcements">
    <a class="gp-news-post-card" href="/prof-barry-rodger-secures-260-million-settlement-in-app-developer-claim-against-google/">
      <div class="post-meta">
        <span class="post-category">Announcements</span>
        <time class="post-date">27/08/26</time>
      </div>
      <h2 class="post-title">Prof Barry Rodger secures £260 million settlement in app developer claim against Google</h2>
    </a>
  </li>

  <!-- 3. Corporate Nomination in Announcements (Should be EXCLUDED) -->
  <li class="wp-block-post category-announcements">
    <a class="gp-news-post-card" href="/geradin-partners-german-team-nominated-for-juves-antitrust-law-firm-of-the-year/">
      <div class="post-meta">
        <span class="post-category">Announcements</span>
        <time class="post-date">28/08/26</time>
      </div>
      <h2 class="post-title">Geradin Partners’ German team nominated for JUVE’s ‘Antitrust Law Firm of the Year’</h2>
    </a>
  </li>

  <!-- 4. Corporate Appointment (Should be EXCLUDED) -->
  <li class="wp-block-post category-announcements">
    <a class="gp-news-post-card" href="/geradin-partners-appoints-tarik-hennen-as-head-of-ai/">
      <div class="post-meta">
        <span class="post-category">Announcements</span>
        <time class="post-date">20/07/26</time>
      </div>
      <h2 class="post-title">Geradin Partners appoints Tarik Hennen as Head of AI</h2>
    </a>
  </li>

  <!-- 5. Commercial Event (Should be EXCLUDED by category) -->
  <li class="wp-block-post category-events">
    <a class="gp-news-post-card" href="/webinar-dutch-competition-law/">
      <div class="post-meta">
        <span class="post-category">Events</span>
        <time class="post-date">15/07/26</time>
      </div>
      <h2 class="post-title">Webinar – Dutch Competition Law: A Year in Review</h2>
    </a>
  </li>

  <!-- 6. Substantive Newsletter (Should be INCLUDED) -->
  <li class="wp-block-post category-newsletters">
    <a class="gp-news-post-card" href="/platform-newsletter-august-2026/">
      <div class="post-meta">
        <span class="post-category">Newsletters</span>
        <time class="post-date">03/08/26</time>
      </div>
      <h2 class="post-title">Platform Newsletter – August 2026</h2>
    </a>
  </li>
</ul>
</body>
</html>"""

MOCK_GERADIN_ARTICLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <title>Geradin Partners’ Monthly EU Litigation Briefing – July/August 2026</title>
  <link rel="canonical" href="https://www.geradinpartners.com/geradin-partners-monthly-eu-litigation-briefing-july-august-2026/" />
</head>
<body>
<main class="main">
  <article class="post-content">
    <div class="featured-image"><img src="/image.webp" /></div>
    <div class="column">
      <div class="post-meta has-small-font-size">
        <span class="post-category">Monthly EU Litigation Briefing</span>
        <time class="post-date">02/09/26</time>
      </div>
      <h1 class="post-title">Geradin Partners’ Monthly EU Litigation Briefing – July/August 2026</h2>
      <p class="wp-block-paragraph">Our latest Monthly EU Litigation Briefing reviews the key developments before the Court of Justice of the European Union and the General Court in July and August 2026, and looks ahead to the judgments and Opinions expected in September.</p>
      <h2 class="wp-block-heading">Key developments in July and August</h2>
      <p class="wp-block-paragraph">In Google and Alphabet v Commission (C-738/22 P), the CJEU dismissed Google’s appeal in its entirety, bringing the Google Android proceedings to a close and leaving the EUR 4.125 billion fine in place. Among the key takeaways, the Court confirmed that the as-efficient-competitor test is not a mandatory requirement for establishing an abuse under Article 102 TFEU and that surrounding market conditions and lawful practices may be relevant when assessing abusive bundling and exclusivity terms.</p>
      <p class="wp-block-paragraph">Furthermore, in private enforcement proceedings, national courts continue to apply the Damages Directive principles regarding disclosure of evidence and limitation periods. The General Court also issued significant rulings on merger control and foreign subsidies regulation that directly affect high-technology markets across the European Union.</p>
      <p class="wp-block-paragraph">For private litigants and competition practitioners, these judgments reaffirm that anticompetitive conduct in platform ecosystems can trigger substantial follow-on damages claims before national courts.</p>
      <p class="wp-block-paragraph">Download the complete July/August 2026 briefing in PDF format below for detailed case-by-case analysis and judicial citations.</p>
      <div class="wp-block-file">
        <a href="https://www.geradinpartners.com/wp-content/uploads/2026/09/Geradin-Partners-Monthly-EU-Litigation-Briefing-July-August-2026.pdf">Download briefing PDF</a>
      </div>
      <div class="team-member">
        <div class="person-meta">
          <a href="https://www.geradinpartners.com/team/thomas-hoppner/" title="Learn more about Thomas Höppner">Thomas Höppner</a>
          <br />
          <a class="email" href="mailto:thoppner@geradinpartners.com">thoppner@geradinpartners.com</a>
        </div>
      </div>
    </div>
  </article>
</main>
</body>
</html>"""


# ==============================================================================
# TESTS
# ==============================================================================

def test_geradin_adapter_registered():
    """Verify GeradinPartnersAdapter is properly registered in DirectWebAdapterRegistry."""
    adapter = DirectWebAdapterRegistry.get_adapter("geradin_partners")
    assert isinstance(adapter, GeradinPartnersAdapter)
    assert adapter.adapter_code == "geradin_partners"
    assert "geradin_partners" in DirectWebAdapterRegistry.list_adapters()

    source = Source(
        name="Geradin Partners Test",
        type=SourceType.BLOG,
        config={"adapter": "geradin_partners"},
    )
    assert DirectWebAdapterRegistry.has_adapter_for_source(source) is True
    assert DirectWebAdapterRegistry.get_adapter_for_source(source) is adapter


def test_deterministic_editorial_filtering():
    """Verify deterministic filtering separates legal content from corporate announcements/events."""
    adapter = GeradinPartnersAdapter()

    # 1. Briefing: always included
    substantive, reason = adapter.is_substantive_article(
        "Monthly EU Litigation Briefing",
        "Geradin Partners’ Monthly EU Litigation Briefing – July/August 2026"
    )
    assert substantive is True
    assert reason == "substantive_category"

    # 2. Papers & Reports: always included
    substantive, reason = adapter.is_substantive_article(
        "Papers & Reports",
        "ICLG – Vertical Agreements and Dominant Firms 2026 guide (Finland)"
    )
    assert substantive is True
    assert reason == "substantive_category"

    # 3. Newsletters: always included
    substantive, reason = adapter.is_substantive_article(
        "Newsletters",
        "Platform Newsletter – August 2026"
    )
    assert substantive is True
    assert reason == "substantive_category"

    # 4. Events: unconditionally excluded
    substantive, reason = adapter.is_substantive_article(
        "Events",
        "Webinar – Dutch Competition Law: A Year in Review"
    )
    assert substantive is False
    assert "excluded_category" in reason

    # 5. Announcements with case law / settlement: included
    substantive, reason = adapter.is_substantive_article(
        "Announcements",
        "Prof Barry Rodger secures £260 million settlement in app developer claim against Google"
    )
    assert substantive is True

    # 6. Announcements with corporate award / nomination: excluded
    substantive, reason = adapter.is_substantive_article(
        "Announcements",
        "Geradin Partners’ German team nominated for JUVE’s ‘Antitrust Law Firm of the Year’"
    )
    assert substantive is False
    assert "corporate_exclusion" in reason

    # 7. Announcements with recruitment / appointments: excluded
    substantive, reason = adapter.is_substantive_article(
        "Announcements",
        "Geradin Partners appoints Tarik Hennen as Head of AI"
    )
    assert substantive is False
    assert "corporate_exclusion" in reason


def test_discover_mock_html():
    """Verify discovery extracts items, applies filtering, and normalizes URLs."""
    adapter = GeradinPartnersAdapter()

    def mock_router(request: httpx.Request):
        if "news" in str(request.url):
            return httpx.Response(200, text=MOCK_GERADIN_LISTING_HTML)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(mock_router))
    source = Source(
        name="Geradin Partners",
        type=SourceType.BLOG,
        url="https://www.geradinpartners.com/news/",
        config={"adapter": "geradin_partners"},
    )

    items = adapter.discover(source, limit=10, client=client)

    # 6 items in HTML: 3 included (Briefing, Settlement, Newsletter), 3 excluded (Nomination, Appointment, Event)
    assert len(items) == 3

    # Check first item: Monthly Briefing
    b = items[0]
    assert "Monthly EU Litigation Briefing" in b.title
    assert b.url == normalize_url("https://www.geradinpartners.com/geradin-partners-monthly-eu-litigation-briefing-july-august-2026/")
    assert b.published_at == datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)
    assert b.raw_metadata["category"] == "Monthly EU Litigation Briefing"

    # Check second item: Settlement
    s = items[1]
    assert "Barry Rodger secures" in s.title
    assert s.published_at == datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc)

    # Check third item: Newsletter
    n = items[2]
    assert "Platform Newsletter" in n.title
    assert n.published_at == datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc)


def test_parse_detail_substantive_article_with_pdf():
    """Verify parse_detail extracts author, clean content, and PDF link correctly."""
    adapter = GeradinPartnersAdapter()
    canonical = normalize_url("https://www.geradinpartners.com/geradin-partners-monthly-eu-litigation-briefing-july-august-2026/")
    item = DiscoveredItem(
        url=canonical,
        title="Geradin Partners’ Monthly EU Litigation Briefing – July/August 2026",
        external_id=canonical,
        published_at=datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc),
        raw_metadata={"category": "Monthly EU Litigation Briefing"},
    )

    article = adapter.parse_detail(MOCK_GERADIN_ARTICLE_HTML, item)

    assert article.title == item.title
    assert article.canonical_url == canonical
    assert article.published_at == datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)

    # Author extraction: Thomas Höppner
    assert article.author == "Thomas Höppner"

    # PDF detection
    assert article.raw_metadata.get("has_pdf") is True
    assert article.raw_metadata.get("pdf_url") == "https://www.geradinpartners.com/wp-content/uploads/2026/09/Geradin-Partners-Monthly-EU-Litigation-Briefing-July-August-2026.pdf"

    # Content cleanliness: post-meta and team-member stripped, text preserved
    assert "In Google and Alphabet v Commission" in article.content
    assert "thoppner@geradinpartners.com" not in article.content
    assert "Article 102 TFEU" in article.content

    # Sufficiency evaluation via SourceSufficiencyService.assess
    entry_mock = Entry(
        title=article.title,
        content=article.content,
        url=article.url,
        canonical_url=article.canonical_url,
        raw_metadata=article.raw_metadata,
        source=Source(name="Geradin Partners", type=SourceType.BLOG),
    )
    sufficiency = SourceSufficiencyService.assess(entry_mock)
    assert sufficiency.level == SourceSufficiencyLevel.FULL


def test_author_rejection_for_corporate_publisher():
    """Verify corporate publisher 'Geradin Partners' is rejected as author."""
    adapter = GeradinPartnersAdapter()
    html_corp = """<!DOCTYPE html><html><body>
    <article class="post-content">
      <div class="team-member">
        <div class="person-meta">
          <a href="https://www.geradinpartners.com/news/" title="Learn more about Geradin Partners">Geradin Partners</a>
        </div>
      </div>
    </article></body></html>"""
    soup = BeautifulSoup(html_corp, "html.parser")
    author = adapter.extract_author_from_detail(soup)
    assert author is None


def test_service_ingestion_and_idempotency(db_session: Session, monkeypatch):
    """Verify DirectWebIngestionService ingests Geradin Partners and deduplicates cleanly."""
    settings = get_settings()
    monkeypatch.setattr(settings, "DIRECT_WEB_INGESTION_ENABLED", True)

    source = Source(
        name="Geradin Partners - EU Competition & Litigation",
        type=SourceType.BLOG,
        provider="native",
        url="https://www.geradinpartners.com/news/",
        config={"adapter": "geradin_partners"},
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    def mock_router(request: httpx.Request):
        url_str = str(request.url)
        if "news" in url_str:
            return httpx.Response(200, text=MOCK_GERADIN_LISTING_HTML)
        elif "litigation-briefing" in url_str:
            return httpx.Response(200, text=MOCK_GERADIN_ARTICLE_HTML)
        else:
            return httpx.Response(200, text="<!DOCTYPE html><html><body><article class='post-content'><div class='column'><p>Substantive text.</p></div></article></body></html>")

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_router))

    service = DirectWebIngestionService()

    # 1. First real run: creates entries
    report1 = service.execute_ingestion(
        db=db_session,
        sources=[source],
        max_items_per_source=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report1.is_dry_run is False
    assert report1.total_created == 3
    assert report1.total_duplicates == 0
    assert report1.total_failed == 0

    # Verify Entry in DB
    created_entries = db_session.query(Entry).filter(Entry.source_id == source.id).all()
    assert len(created_entries) == 3

    briefing_entry = next(e for e in created_entries if "Monthly EU Litigation Briefing" in e.title)
    assert briefing_entry.author == "Thomas Höppner"
    assert briefing_entry.raw_metadata.get("has_pdf") is True
    assert briefing_entry.raw_metadata.get("pdf_url") is not None

    # Verify IngestionRun in DB
    run = db_session.query(IngestionRun).filter(IngestionRun.source_id == source.id).first()
    assert run is not None
    assert run.status == IngestionRunStatus.SUCCESS.value
    assert run.created_count == 3

    # 2. Second real run: must detect all 3 as duplicates, 0 created
    report2 = service.execute_ingestion(
        db=db_session,
        sources=[source],
        max_items_per_source=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report2.total_created == 0
    assert report2.total_duplicates == 3


def test_weekly_refresh_detects_geradin_partners(db_session: Session, monkeypatch):
    """Verify WeeklyRefreshService automatically recognizes and processes Geradin Partners."""
    source = Source(
        name="Geradin Partners - EU Competition & Litigation",
        type=SourceType.BLOG,
        provider="native",
        url="https://www.geradinpartners.com/news/",
        config={"adapter": "geradin_partners"},
        active=True,
    )
    db_session.add(source)
    db_session.commit()

    service = WeeklyRefreshService()

    # Dry-run execution over Geradin Partners source
    detail = service._process_source(
        db=db_session,
        source=source,
        lookback_days=8,
        confirm_real_calls=False,
    )

    assert detail.source_name == source.name
    assert detail.status == "success"
    assert detail.new_entries == 0
    assert len(detail.errors) == 0
