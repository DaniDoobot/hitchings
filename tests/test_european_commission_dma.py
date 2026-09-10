"""Tests for European Commission Digital Markets Act (DMA) extractor, cross-source deduplication, and weekly refresh."""

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
from app.providers.extractors.european_commission_dma import (
    EuropeanCommissionDMAExtractor,
    DMA_NEWS_DEFAULT_URL,
)
from app.services.ingestion_service import IngestionService
from app.services.weekly_refresh_service import WeeklyRefreshService

SAMPLE_DMA_LISTING_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>News | Digital Markets Act</title></head>
<body>
<main>
  <div class="ecl-container">
    <article class="ecl-content-item">
      <ul class="ecl-content-block__primary-meta">
        <li class="ecl-content-block__primary-meta-item">News article</li>
        <li class="ecl-content-block__primary-meta-item">
          <time datetime="2026-07-23T12:00:00Z">23 July 2026</time>
        </li>
      </ul>
      <h1 class="ecl-content-block__title">
        <a href="/commission-fines-google-eur890-million-breaches-digital-markets-act-2026-07-23_en" class="ecl-link">
          Commission fines Google €890 million for breaches of the Digital Markets Act
        </a>
      </h1>
      <div class="ecl-content-block__description">
        Today, the European Commission took two decisions finding non-compliance by Google with the DMA for self-preferencing.
      </div>
    </article>

    <article class="ecl-content-item">
      <ul class="ecl-content-block__primary-meta">
        <li class="ecl-content-block__primary-meta-item">News article</li>
        <li class="ecl-content-block__primary-meta-item">
          <time datetime="2026-07-20T10:00:00Z">20 July 2026</time>
        </li>
      </ul>
      <h1 class="ecl-content-block__title">
        <a href="/news/commission-hosted-stakeholder-roundtable-cloud-computing-2026-07-20_en" class="ecl-link">
          Commission hosted a stakeholder roundtable on cloud computing under DMA
        </a>
      </h1>
      <div class="ecl-content-block__description">
        Commission services hosted a workshop with enterprise users and cloud service providers to discuss interoperability.
      </div>
    </article>
  </div>
</main>
</body>
</html>
"""

SAMPLE_DMA_GOOGLE_DETAIL_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>Commission fines Google - Digital Markets Act</title></head>
<body>
<main>
  <dl class="ecl-description-list">
    <dt class="ecl-description-list__term">Publication date</dt>
    <dd class="ecl-description-list__definition">23 July 2026</dd>
    <dt class="ecl-description-list__term">Authors</dt>
    <dd class="ecl-description-list__definition">Directorate-General for Competition | Directorate-General for Communications Networks, Content and Technology</dd>
  </dl>
  <article>
    <p>The European Commission took two decisions finding non-compliance by Google with the Digital Markets Act (DMA) for self-preferencing its own services in search results and app distribution.</p>
    <p>Following an in-depth investigation under Article 8 of the DMA, the Commission concluded that Google failed to comply with obligations laid down in Article 6(5) and Article 6(12) regarding fair and non-discriminatory rankings and conditions.</p>
    <p>Under the decision, the Commission imposes a total fine of €890 million on Google and parent Alphabet Inc. This enforcement action highlights the strict framework applicable to designated gatekeepers in digital markets across the European Union.</p>
    <p>Full details and press documentation are available in the official European Commission Press Corner announcement at <a href="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1670">Press release IP/26/1670</a>.</p>
    <p>The non-confidential version of the formal decision will be made available under case file <a href="https://ec.europa.eu/competition/digital_markets_act/cases/dma_10012_decision.pdf">DMA.10012 Google Search Decision PDF</a>.</p>
  </article>
</main>
</body>
</html>
"""

SAMPLE_DMA_ROUNDTABLE_DETAIL_HTML = """<!DOCTYPE html>
<html lang="en">
<head><title>Stakeholder roundtable on cloud computing - Digital Markets Act</title></head>
<body>
<main>
  <dl class="ecl-description-list">
    <dt class="ecl-description-list__term">Publication date</dt>
    <dd class="ecl-description-list__definition">20 July 2026</dd>
    <dt class="ecl-description-list__term">Author</dt>
    <dd class="ecl-description-list__definition">Directorate-General for Communications Networks, Content and Technology</dd>
  </dl>
  <article>
    <p>Commission services hosted a stakeholder roundtable on cloud computing interoperability and data portability under the Digital Markets Act.</p>
    <p>Participants included designated gatekeepers, third-party cloud infrastructure providers, business users, and independent software vendors discussing technical interface specifications.</p>
    <p>Discussions centered on Article 6(9) of Regulation (EU) 2022/1925, focusing on real-time data portability mechanisms, open standard interfaces, and auditability for enterprise customers wishing to migrate between multicloud platforms without artificial switching friction.</p>
  </article>
</main>
</body>
</html>
"""


def test_parse_listing_page():
    """Verify ECL content item parsing from listing HTML without network."""
    extractor = EuropeanCommissionDMAExtractor()
    items = extractor.parse_listing_page(SAMPLE_DMA_LISTING_HTML.encode("utf-8"), DMA_NEWS_DEFAULT_URL)

    assert len(items) == 2
    assert items[0]["title"] == "Commission fines Google €890 million for breaches of the Digital Markets Act"
    assert items[0]["url"] == "https://digital-markets-act.ec.europa.eu/commission-fines-google-eur890-million-breaches-digital-markets-act-2026-07-23_en"
    assert items[0]["listing_date"] == datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)
    assert "Google" in items[0]["excerpt"]

    assert items[1]["title"] == "Commission hosted a stakeholder roundtable on cloud computing under DMA"
    assert items[1]["url"] == "https://digital-markets-act.ec.europa.eu/news/commission-hosted-stakeholder-roundtable-cloud-computing-2026-07-20_en"
    assert items[1]["listing_date"] == datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_dma_extractor_extract_mocked():
    """Verify full extraction and enrichment including author DGs, Press Corner reference and PDF links."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "news_en" in url or "digital-markets-act.ec.europa.eu" == request.url.host and request.url.path in ("/", "/news"):
            return httpx.Response(200, html=SAMPLE_DMA_LISTING_HTML)
        if "commission-fines-google" in url:
            return httpx.Response(200, html=SAMPLE_DMA_GOOGLE_DETAIL_HTML)
        if "commission-hosted-stakeholder-roundtable" in url:
            return httpx.Response(200, html=SAMPLE_DMA_ROUNDTABLE_DETAIL_HTML)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = EuropeanCommissionDMAExtractor()
        source = Source(
            id=uuid.uuid4(),
            name="European Commission - Digital Markets Act",
            url=DMA_NEWS_DEFAULT_URL,
            type=SourceType.WEBSITE,
            provider="native",
            category="institutional",
            config={"initial_fetch_limit": 10},
        )
        entries = await extractor.extract(client, source)

    assert len(entries) == 2

    # Verify Google DMA item
    e0 = entries[0]
    assert "Google" in e0.title
    assert e0.published_at == datetime(2026, 7, 23, 0, 0, 0, tzinfo=timezone.utc)
    assert "Directorate-General for Competition" in e0.author
    assert "Directorate-General for Communications Networks, Content and Technology" in e0.author
    assert e0.raw_metadata["presscorner_ref"] == "IP/26/1670"
    assert e0.raw_metadata["presscorner_url"] == "https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1670"
    assert any("dma_10012_decision.pdf" in pdf for pdf in e0.raw_metadata["pdf_links"])
    assert e0.raw_metadata["content_sufficiency"] == "FULL"
    assert len(e0.content) > 500

    # Verify Roundtable DMA item (exclusive DMA notice, no Press Corner link)
    e1 = entries[1]
    assert "roundtable" in e1.title.lower()
    assert e1.raw_metadata["presscorner_ref"] is None
    assert e1.raw_metadata["presscorner_url"] is None
    assert len(e1.raw_metadata["pdf_links"]) == 0
    assert e1.raw_metadata["content_sufficiency"] == "FULL"


@pytest.mark.asyncio
async def test_native_provider_can_handle_and_dispatch():
    """Verify NativeProvider routes digital-markets-act.ec.europa.eu to DMA extractor."""
    source = Source(
        id=uuid.uuid4(),
        name="European Commission - Digital Markets Act",
        url="https://digital-markets-act.ec.europa.eu/news_en",
        type=SourceType.WEBSITE,
        provider="native",
        category="institutional",
    )
    provider = NativeProvider()
    assert provider.can_handle(source) is True

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "news_en" in url:
            return httpx.Response(200, html=SAMPLE_DMA_LISTING_HTML)
        return httpx.Response(200, html=SAMPLE_DMA_ROUNDTABLE_DETAIL_HTML)

    transport = httpx.MockTransport(handler)
    # Monkeypatch AsyncClient inside NativeProvider.fetch_entries
    real_async_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    import app.providers.native as native_module
    import unittest.mock as mock

    with mock.patch.object(native_module.httpx, "AsyncClient", side_effect=mock_client_factory):
        entries = await provider.fetch_entries(source)
        assert len(entries) == 2


@pytest.mark.asyncio
async def test_cross_source_deduplication_dma_after_competition(db_session: Session):
    """Test cross-source deduplication when EC Competition already ingested Press Corner release."""
    # 1. Create European Commission TrackedEntity
    entity = TrackedEntity(
        id=uuid.uuid4(),
        display_name="European Commission / Digital Markets Act",
        entity_type="institution",
        active=True,
    )
    db_session.add(entity)

    # 2. Existing EC Competition Source and Entry
    ec_comp_source = Source(
        id=uuid.uuid4(),
        name="European Commission - Competition Policy",
        url="https://competition-policy.ec.europa.eu/node/38/rss_en",
        type=SourceType.RSS,
        provider="native",
        category="institutional",
        tracked_entity_id=entity.id,
    )
    db_session.add(ec_comp_source)

    existing_entry = Entry(
        id=uuid.uuid4(),
        source_id=ec_comp_source.id,
        external_id="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1670",
        url="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1670",
        canonical_url="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1670",
        title="Commission fines Google €890 million (Press Release)",
        content="Official press release text from Press Corner.",
        excerpt="Official press release text.",
        published_at=datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc),
        captured_at=datetime.now(timezone.utc),
        language="en",
        content_type="institutional_news",
        content_hash="hash_presscorner_1670",
        raw_metadata={"presscorner_ref": "IP/26/1670"},
    )
    db_session.add(existing_entry)
    db_session.commit()

    # 3. Create DMA Source
    dma_source = Source(
        id=uuid.uuid4(),
        name="European Commission - Digital Markets Act",
        url="https://digital-markets-act.ec.europa.eu/news_en",
        type=SourceType.WEBSITE,
        provider="native",
        category="institutional",
        tracked_entity_id=entity.id,
    )
    db_session.add(dma_source)
    db_session.commit()

    # 4. Ingest DMA entry pointing to the same presscorner_ref
    service = IngestionService()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "news_en" in url:
            return httpx.Response(200, html=SAMPLE_DMA_LISTING_HTML)
        if "commission-fines-google" in url:
            return httpx.Response(200, html=SAMPLE_DMA_GOOGLE_DETAIL_HTML)
        return httpx.Response(200, html=SAMPLE_DMA_ROUNDTABLE_DETAIL_HTML)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    import app.providers.native as native_module
    import unittest.mock as mock

    with mock.patch.object(native_module.httpx, "AsyncClient", side_effect=mock_client_factory):
        run = await service.ingest_source(dma_source.id, db_session)

    assert run.status == IngestionRunStatus.SUCCESS.value
    assert run.fetched == 2
    # 1 duplicate detected across sources (Google matched with IP/26/1670), 1 novel created (Roundtable)
    assert run.duplicates == 1
    assert run.created == 1

    # Verify existing EC Competition entry was cross-annotated
    db_session.refresh(existing_entry)
    assert existing_entry.raw_metadata.get("cross_source_matched") is True
    assert "dma_portal_url" in existing_entry.raw_metadata
    assert "commission-fines-google" in existing_entry.raw_metadata["dma_portal_url"]


@pytest.mark.asyncio
async def test_cross_source_deduplication_competition_after_dma(db_session: Session):
    """Test cross-source deduplication when DMA ingested first and EC Competition runs later."""
    entity = TrackedEntity(
        id=uuid.uuid4(),
        display_name="European Commission / Digital Markets Act",
        entity_type="institution",
        active=True,
    )
    db_session.add(entity)

    dma_source = Source(
        id=uuid.uuid4(),
        name="European Commission - Digital Markets Act",
        url="https://digital-markets-act.ec.europa.eu/news_en",
        type=SourceType.WEBSITE,
        provider="native",
        category="institutional",
        tracked_entity_id=entity.id,
    )
    db_session.add(dma_source)

    # 1. DMA Entry created first
    dma_entry = Entry(
        id=uuid.uuid4(),
        source_id=dma_source.id,
        external_id="https://digital-markets-act.ec.europa.eu/commission-fines-google-eur890-million-breaches-digital-markets-act-2026-07-23_en",
        url="https://digital-markets-act.ec.europa.eu/commission-fines-google-eur890-million-breaches-digital-markets-act-2026-07-23_en",
        canonical_url="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1670",
        title="Commission fines Google €890 million for breaches of the Digital Markets Act",
        content="Substantive article from DMA portal.",
        excerpt="Excerpt from DMA portal.",
        published_at=datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc),
        captured_at=datetime.now(timezone.utc),
        language="en",
        content_type="institutional_news",
        content_hash="hash_dma_google_article",
        raw_metadata={
            "presscorner_ref": "IP/26/1670",
            "presscorner_url": "https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1670",
        },
    )
    db_session.add(dma_entry)
    db_session.commit()

    # 2. Now simulate EC Competition source ingesting the Press Corner release
    ec_comp_source = Source(
        id=uuid.uuid4(),
        name="European Commission - Competition Policy",
        url="https://competition-policy.ec.europa.eu/node/38/rss_en",
        type=SourceType.RSS,
        provider="native",
        category="institutional",
        tracked_entity_id=entity.id,
    )
    db_session.add(ec_comp_source)
    db_session.commit()

    from app.providers.base import RawEntryData
    raw_comp_entry = RawEntryData(
        url="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1670",
        title="Commission fines Google €890 million",
        content="Content from Press Corner API.",
        excerpt="Excerpt.",
        published_at=datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc),
        external_id="https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1670",
        language="en",
        content_type="institutional_news",
        raw_metadata={"presscorner_ref": "IP/26/1670"},
    )

    import unittest.mock as mock
    service = IngestionService()
    with mock.patch.object(NativeProvider, "fetch_entries", return_value=[raw_comp_entry]):
        run = await service.ingest_source(ec_comp_source.id, db_session)

    assert run.status == IngestionRunStatus.SUCCESS.value
    assert run.duplicates == 1
    assert run.created == 0


def test_weekly_refresh_dma_dry_run(db_session: Session):
    """Test that WeeklyRefreshService safely processes European Commission DMA in dry-run mode."""
    source = Source(
        id=uuid.uuid4(),
        name="European Commission - Digital Markets Act",
        url="https://digital-markets-act.ec.europa.eu/news_en",
        type=SourceType.WEBSITE,
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
        sources_filter=["European Commission - Digital Markets Act"],
    )

    assert report.status == "completed"
    assert report.is_dry_run is True
    assert len(report.per_source) == 1
    detail = report.per_source[0]
    assert detail.source_name == "European Commission - Digital Markets Act"
    assert detail.status == "success"
    assert detail.new_entries == 0
