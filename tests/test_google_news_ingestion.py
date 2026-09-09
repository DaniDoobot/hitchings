"""Unit tests for GoogleNewsIngestionService with mocked network (Bloque 9A)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest
import httpx
from sqlalchemy.orm import Session

from app.models.analysis import EntryAnalysis
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.providers.base import ProviderDisabledError
from app.services.google_news_ingestion_service import GoogleNewsIngestionService
from app.services.google_news_query_planner import GoogleNewsQueryPlanner

MOCK_RSS_XML_1 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Query 1 - Google News</title>
    <item>
      <title>CNMC abre expediente a petroleras - El Economista</title>
      <link>https://news.google.com/rss/articles/CBMi_TEST_1?oc=5</link>
      <guid isPermaLink="false">CBMi_TEST_1</guid>
      <pubDate>Wed, 09 Sep 2026 08:00:00 GMT</pubDate>
      <description>Expediente sancionador de la CNMC.</description>
      <source url="https://eleconomista.es">El Economista</source>
    </item>
    <item>
      <title>Sentencia del TJUE sobre cárteles - Expansión</title>
      <link>https://news.google.com/rss/articles/CBMi_TEST_2?oc=5</link>
      <guid isPermaLink="false">CBMi_TEST_2</guid>
      <pubDate>Wed, 09 Sep 2026 09:00:00 GMT</pubDate>
      <description>El TJUE aclara la prescripción de acciones de daños.</description>
      <source url="https://expansion.com">Expansión</source>
    </item>
  </channel>
</rss>
"""

MOCK_RSS_XML_2 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Query 2 - Google News</title>
    <item>
      <!-- Duplicate of TEST_1 in another query -->
      <title>CNMC abre expediente a petroleras - El Economista</title>
      <link>https://news.google.com/rss/articles/CBMi_TEST_1?oc=5</link>
      <guid isPermaLink="false">CBMi_TEST_1</guid>
      <pubDate>Wed, 09 Sep 2026 08:00:00 GMT</pubDate>
      <description>Expediente sancionador de la CNMC.</description>
      <source url="https://eleconomista.es">El Economista</source>
    </item>
    <item>
      <title>Novedad de competencia en Bruselas - Cinco Días</title>
      <link>https://news.google.com/rss/articles/CBMi_TEST_3?oc=5</link>
      <guid isPermaLink="false">CBMi_TEST_3</guid>
      <pubDate>Wed, 09 Sep 2026 10:00:00 GMT</pubDate>
      <description>La Comisión Europea revisa concentraciones.</description>
      <source url="https://cincodias.elpais.com">Cinco Días</source>
    </item>
  </channel>
</rss>
"""


def _setup_active_matrix(db: Session) -> None:
    matrix = TrackingMatrix(
        code="MATRIX-GN-INGEST",
        name="Matrix GN Ingest",
        status="active",
    )
    db.add(matrix)
    db.flush()
    topic = TrackingTopic(
        matrix_id=matrix.id,
        code="competition_law_general",
        name="Derecho de la competencia",
        priority=100,
        active=True,
        discovery_queries=["CNMC competencia", "antitrust updates"],
    )
    db.add(topic)
    db.commit()


def test_google_news_ingestion_creates_entries_and_run(db_session: Session, monkeypatch):
    """GoogleNewsIngestionService creates Source, IngestionRun, and Entry records with proper metadata."""
    _setup_active_matrix(db_session)

    # Enable provider in settings
    service = GoogleNewsIngestionService()
    monkeypatch.setattr(service.settings, "GOOGLE_NEWS_ENABLED", True)

    # Mock HTTP client
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=MOCK_RSS_XML_1))
    client = httpx.Client(transport=transport)

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        confirm_real_calls=True,
        client=client,
    )

    assert report.is_dry_run is False
    assert report.status == IngestionRunStatus.SUCCESS.value
    assert report.entries_created == 2
    assert report.duplicates_count == 0
    assert report.failed_queries == 0
    assert "El Economista" in report.publishers_found
    assert "Expansión" in report.publishers_found

    # Verify Source
    source = db_session.query(Source).filter(Source.type == SourceType.GOOGLE_NEWS).first()
    assert source is not None
    assert source.active is True
    assert source.last_success_at is not None

    # Verify IngestionRun in DB
    run = db_session.query(IngestionRun).filter(IngestionRun.id == report.run_id).first()
    assert run is not None
    assert run.status == IngestionRunStatus.SUCCESS.value
    assert run.created_count == 2

    # Verify Entries in DB
    entries = db_session.query(Entry).filter(Entry.source_id == source.id).all()
    assert len(entries) == 2
    for e in entries:
        assert e.content is None  # Discovery only: no full body scraping
        assert e.content_type == "news_article"
        assert e.raw_metadata["discovery_source"] == "Google News"
        assert e.raw_metadata["publisher"] is not None

    # Verify 0 EntryAnalysis created
    analyses_count = db_session.query(EntryAnalysis).count()
    assert analyses_count == 0


def test_google_news_ingestion_deduplicates_against_official_sources(db_session: Session, monkeypatch):
    """Google News skips inserting an entry if its URL/canonical_url already exists from an official source."""
    _setup_active_matrix(db_session)

    # Create an official CNMC source and an existing entry with the same URL as MOCK item 1
    cnmc_source = Source(
        name="CNMC - Prensa / Noticias",
        type=SourceType.WEBSITE,
        provider="native",
        category="institutional",
        active=True,
    )
    db_session.add(cnmc_source)
    db_session.flush()

    existing_official_entry = Entry(
        source_id=cnmc_source.id,
        url="https://news.google.com/rss/articles/CBMi_TEST_1",
        canonical_url="https://news.google.com/rss/articles/CBMi_TEST_1",
        title="Existing Official CNMC Article",
        captured_at=datetime.now(timezone.utc),
    )
    db_session.add(existing_official_entry)
    db_session.commit()

    service = GoogleNewsIngestionService()
    monkeypatch.setattr(service.settings, "GOOGLE_NEWS_ENABLED", True)

    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=MOCK_RSS_XML_1))
    client = httpx.Client(transport=transport)

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        confirm_real_calls=True,
        client=client,
    )

    # MOCK_RSS_XML_1 has 2 items: TEST_1 matches existing official entry, TEST_2 is new
    assert report.entries_created == 1
    assert report.duplicates_count == 1


def test_google_news_ingestion_deduplicates_intra_run(db_session: Session, monkeypatch):
    """Items appearing in multiple queries within the same run are deduplicated."""
    _setup_active_matrix(db_session)

    service = GoogleNewsIngestionService()
    monkeypatch.setattr(service.settings, "GOOGLE_NEWS_ENABLED", True)

    # Alternate response: Query 1 returns MOCK_1, Query 2 returns MOCK_2 (which shares TEST_1)
    call_count = 0

    def mock_handler(req):
        nonlocal call_count
        call_count += 1
        xml = MOCK_RSS_XML_1 if call_count == 1 else MOCK_RSS_XML_2
        return httpx.Response(200, text=xml)

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))

    report = service.execute_ingestion(
        db=db_session,
        max_queries=2,
        confirm_real_calls=True,
        client=client,
    )

    # Query 1 has TEST_1, TEST_2. Query 2 has TEST_1 (duplicate), TEST_3.
    # Total unique: 3. Total duplicates: 1.
    assert report.entries_created == 3
    assert report.duplicates_count == 1


def test_google_news_ingestion_error_isolation(db_session: Session, monkeypatch):
    """If one query fails with HTTP 500, remaining queries continue and run marks status PARTIAL."""
    _setup_active_matrix(db_session)

    service = GoogleNewsIngestionService()
    monkeypatch.setattr(service.settings, "GOOGLE_NEWS_ENABLED", True)

    call_count = 0

    def mock_handler(req):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(500, text="Internal Server Error")
        return httpx.Response(200, text=MOCK_RSS_XML_1)

    client = httpx.Client(transport=httpx.MockTransport(mock_handler))

    report = service.execute_ingestion(
        db=db_session,
        max_queries=2,
        confirm_real_calls=True,
        client=client,
    )

    assert report.status == IngestionRunStatus.PARTIAL.value
    assert report.failed_queries == 1
    assert report.entries_created == 2  # from query 2


def test_google_news_ingestion_respects_max_new_entries_cap(db_session: Session, monkeypatch):
    """Stops creating entries immediately upon reaching max_new_entries limit."""
    _setup_active_matrix(db_session)

    service = GoogleNewsIngestionService()
    monkeypatch.setattr(service.settings, "GOOGLE_NEWS_ENABLED", True)

    # Feed has 2 items, but cap is 1
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=MOCK_RSS_XML_1)))

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_new_entries=1,
        confirm_real_calls=True,
        client=client,
    )

    assert report.entries_created == 1


def test_google_news_ingestion_disabled_fails_closed(db_session: Session, monkeypatch):
    """Raises ProviderDisabledError when GOOGLE_NEWS_ENABLED is False and confirm_real_calls is True."""
    service = GoogleNewsIngestionService()
    monkeypatch.setattr(service.settings, "GOOGLE_NEWS_ENABLED", False)

    with pytest.raises(ProviderDisabledError) as exc_info:
        service.execute_ingestion(db=db_session, confirm_real_calls=True)
    assert "disabled" in str(exc_info.value).lower()


def test_google_news_ingestion_dry_run_no_writes(db_session: Session):
    """Dry run (confirm_real_calls=False) does 0 DB writes and no network requests."""
    _setup_active_matrix(db_session)

    service = GoogleNewsIngestionService()
    report = service.execute_ingestion(db=db_session, confirm_real_calls=False)

    assert report.is_dry_run is True
    assert report.entries_created == 0
    assert report.queries_executed == 0

    # Verify no entries created
    entries_count = db_session.query(Entry).count()
    assert entries_count == 0
    # Verify no IngestionRuns created
    runs_count = db_session.query(IngestionRun).count()
    assert runs_count == 0
