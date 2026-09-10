"""Tests for OECD Competition Law and Policy extractor, future-date guard, provenance, and deduplication."""

import uuid
from datetime import datetime, timezone
import pytest
import httpx
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRunStatus
from app.models.tracking import TrackedEntity
from app.providers.native import NativeProvider
from app.providers.extractors.oecd_competition import (
    OECDCompetitionExtractor,
    DEFAULT_OECD_ISSN,
)
from app.services.ingestion_service import IngestionService
from app.services.weekly_refresh_service import WeeklyRefreshService

SAMPLE_CROSSREF_RESPONSE = {
    "status": "ok",
    "message-type": "work-list",
    "message": {
        "total-results": 2,
        "items": [
            {
                "DOI": "10.1787/1330d48b-en",
                "title": ["National security considerations in competition enforcement"],
                "publisher": "Organisation for Economic Co-Operation and Development (OECD)",
                "container-title": ["OECD Roundtables on Competition Policy Papers"],
                "ISSN": ["2075-8677"],
                "type": "report",
                "published": {
                    "date-parts": [[2026, 6, 2]]
                },
                "URL": "https://doi.org/10.1787/1330d48b-en",
                "resource": {
                    "primary": {
                        "URL": "https://www.oecd.org/en/publications/national-security-considerations-in-competition-enforcement_1330d48b-en.html"
                    }
                },
                "author": [{"name": "OECD"}],
            },
            {
                "DOI": "10.1787/62c9a81e-en",
                "title": ["Early resolution of cartel cases in Latin America and the Caribbean"],
                "publisher": "Organisation for Economic Co-Operation and Development (OECD)",
                "container-title": ["OECD Roundtables on Competition Policy Papers"],
                "ISSN": ["2075-8677"],
                "type": "report",
                "published": {
                    "date-parts": [[2026, 9, 14]]
                },
                "URL": "https://doi.org/10.1787/62c9a81e-en",
                "resource": {
                    "primary": {
                        "URL": "https://www.oecd.org/en/publications/early-resolution-of-cartel-cases-in-latin-america-and-the-caribbean_62c9a81e-en.html"
                    }
                },
                "author": [{"name": "OECD"}],
            },
        ],
    },
}

SAMPLE_OPENALEX_RESPONSE_NATIONAL_SECURITY = {
    "id": "https://openalex.org/W4400000001",
    "doi": "https://doi.org/10.1787/1330d48b-en",
    "title": "National security considerations in competition enforcement",
    "publication_year": 2026,
    "publication_date": "2026-06-02",
    "abstract_inverted_index": {
        "National": [0],
        "security": [1, 16, 57],
        "considerations": [2, 17],
        "are": [3, 18],
        "becoming": [4],
        "increasingly": [5],
        "prominent": [6],
        "in": [7, 24],
        "economic": [8],
        "policymaking,": [9],
        "reflecting": [10],
        "geopolitical": [11],
        "developments": [12],
        "and": [13, 21],
        "growing": [14],
        "attention": [15],
        "to": [19],
        "resilience": [20],
        "technological": [22],
        "capability.": [23],
        "This": [25],
        "paper": [26],
        "examines": [27],
        "the": [28],
        "implications": [29],
        "for": [30],
        "competition": [31],
        "authorities": [32],
        "across": [33],
        "merger": [34],
        "control,": [35],
        "cartels,": [36],
        "and": [37],
        "unilateral": [38],
        "conduct.": [39],
        "It": [40],
        "identifies": [41],
        "key": [42],
        "principles": [43],
        "for": [44],
        "preserving": [45],
        "legal": [46],
        "predictability,": [47],
        "procedural": [48],
        "fairness,": [49],
        "and": [50],
        "effective": [51],
        "remedies": [52],
        "design": [53],
        "under": [54],
        "evolving": [55],
        "national": [56],
        "policy": [58],
        "frameworks": [59],
        "worldwide.": [60],
    },
}


def test_parse_crossref_date():
    """Verify robust UTC parsing from Crossref date-parts variants."""
    extractor = OECDCompetitionExtractor()

    d1 = extractor.parse_crossref_date({"published": {"date-parts": [[2026, 6, 2]]}})
    assert d1 == datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc)

    d2 = extractor.parse_crossref_date({"published": {"date-parts": [[2026, 9]]}})
    assert d2 == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)

    d3 = extractor.parse_crossref_date({"published": {"date-parts": [[2025]]}})
    assert d3 == datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    d4 = extractor.parse_crossref_date({"published": None})
    assert d4 is None


@pytest.mark.asyncio
async def test_oecd_extractor_mocked():
    """Verify full extraction, OpenAlex abstract enrichment, and provenance metadata."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.crossref.org" in url:
            return httpx.Response(200, json=SAMPLE_CROSSREF_RESPONSE)
        if "openalex.org" in url and "1330d48b-en" in url:
            return httpx.Response(200, json=SAMPLE_OPENALEX_RESPONSE_NATIONAL_SECURITY)
        if "openalex.org" in url and "62c9a81e-en" in url:
            return httpx.Response(200, json={"title": "Early resolution", "abstract_inverted_index": None})
        if "oecd.org" in url:
            # Simulate Cloudflare 403 on direct portal web
            return httpx.Response(403, text="Just a moment... Cloudflare")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = OECDCompetitionExtractor()
        source = Source(
            id=uuid.uuid4(),
            name="OECD - Competition Law and Policy",
            url="https://www.oecd.org/en/topics/policy-issues/competition.html",
            type=SourceType.INSTITUTIONAL,
            provider="native",
            category="institutional",
            config={"initial_fetch_limit": 10},
        )
        # Pass simulated date on or after 2026-09-14 so both items are eligible
        entries = await extractor.extract(
            client, source, now=datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
        )

    assert len(entries) == 2

    # Entry 0: Enriched via OpenAlex with accurate provenance
    e0 = entries[0]
    assert e0.title == "National security considerations in competition enforcement"
    assert e0.external_id == "10.1787/1330d48b-en"
    assert e0.published_at == datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc)
    assert e0.author == "OECD"
    assert e0.content_type == "policy_paper"
    assert "National security considerations" in e0.content
    assert e0.raw_metadata["doi"] == "10.1787/1330d48b-en"
    assert e0.raw_metadata["issn"] == DEFAULT_OECD_ISSN
    assert e0.raw_metadata["discovery_provider"] == "crossref"
    assert e0.raw_metadata["content_provider"] == "openalex"
    assert e0.raw_metadata["abstract_source"] == "openalex"
    assert e0.raw_metadata["series"] == "OECD Roundtables on Competition Policy Papers"
    assert any("National Security & Competition" in tag for tag in e0.raw_metadata["tags"])
    assert any("OECD Roundtables" in tag for tag in e0.raw_metadata["tags"])

    # Entry 1: Metadata fallback with accurate provenance
    e1 = entries[1]
    assert e1.title == "Early resolution of cartel cases in Latin America and the Caribbean"
    assert e1.external_id == "10.1787/62c9a81e-en"
    assert e1.published_at == datetime(2026, 9, 14, 0, 0, 0, tzinfo=timezone.utc)
    assert e1.raw_metadata["discovery_provider"] == "crossref"
    assert e1.raw_metadata["content_provider"] == "metadata_fallback"
    assert e1.raw_metadata["abstract_source"] == "none"
    assert any("Cartels & Leniency" in tag for tag in e1.raw_metadata["tags"])


@pytest.mark.asyncio
async def test_oecd_future_date_guard_and_lookback():
    """Verify test cases A, B, C, D, E for future date guard and lookback window."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE_CROSSREF_RESPONSE)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        extractor = OECDCompetitionExtractor()
        source = Source(
            id=uuid.uuid4(),
            name="OECD - Competition Law and Policy",
            url="https://www.oecd.org/en/topics/policy-issues/competition.html",
            type=SourceType.INSTITUTIONAL,
            provider="native",
            category="institutional",
            config={"initial_fetch_limit": 10},
        )

        # ----------------------------------------------------------------------
        # A) now = 2026-09-10, publication = 2026-09-14
        # => Future publication (62c9a81e-en) is NOT returned
        # ----------------------------------------------------------------------
        now_sept_10 = datetime(2026, 9, 10, 14, 0, 0, tzinfo=timezone.utc)
        entries_sept_10 = await extractor.extract(client, source, now=now_sept_10)
        returned_dois_a = [e.external_id for e in entries_sept_10]
        assert "10.1787/62c9a81e-en" not in returned_dois_a
        assert "10.1787/1330d48b-en" in returned_dois_a

        # ----------------------------------------------------------------------
        # B) now = 2026-09-14, publication = 2026-09-14
        # => On official release day, publication IS returned
        # ----------------------------------------------------------------------
        now_sept_14 = datetime(2026, 9, 14, 9, 0, 0, tzinfo=timezone.utc)
        entries_sept_14 = await extractor.extract(client, source, now=now_sept_14)
        returned_dois_b = [e.external_id for e in entries_sept_14]
        assert "10.1787/62c9a81e-en" in returned_dois_b

        # ----------------------------------------------------------------------
        # C) Publication within lookback and not future => returned
        # now = 2026-09-15, lookback = 5 days (covers 2026-09-10 to 2026-09-15)
        # ----------------------------------------------------------------------
        now_sept_15 = datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc)
        entries_within_lb = await extractor.extract(
            client, source, lookback_days=5, now=now_sept_15
        )
        assert len(entries_within_lb) == 1
        assert entries_within_lb[0].external_id == "10.1787/62c9a81e-en"

        # ----------------------------------------------------------------------
        # D) Old publication outside lookback => excluded
        # 2026-06-02 is > 5 days old, so excluded in entries_within_lb
        # ----------------------------------------------------------------------
        assert "10.1787/1330d48b-en" not in [e.external_id for e in entries_within_lb]

        # ----------------------------------------------------------------------
        # E) Backfill (lookback = 120 days) with now = 2026-09-10
        # Includes historical item (2026-06-02), but NEVER includes future item (2026-09-14)
        # ----------------------------------------------------------------------
        entries_backfill = await extractor.extract(
            client, source, lookback_days=120, now=now_sept_10
        )
        backfill_dois = [e.external_id for e in entries_backfill]
        assert "10.1787/1330d48b-en" in backfill_dois
        assert "10.1787/62c9a81e-en" not in backfill_dois


@pytest.mark.asyncio
async def test_oecd_native_provider_dispatch():
    """Verify NativeProvider routes oecd.org to OECDCompetitionExtractor."""
    source = Source(
        id=uuid.uuid4(),
        name="OECD - Competition Law and Policy",
        url="https://www.oecd.org/en/topics/policy-issues/competition.html",
        type=SourceType.INSTITUTIONAL,
        provider="native",
        category="institutional",
    )
    provider = NativeProvider()
    assert provider.can_handle(source) is True

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.crossref.org" in url:
            return httpx.Response(200, json=SAMPLE_CROSSREF_RESPONSE)
        if "openalex.org" in url:
            return httpx.Response(200, json={"title": "Test", "abstract_inverted_index": None})
        return httpx.Response(403)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    import app.providers.native as native_module
    import unittest.mock as mock

    with mock.patch.object(native_module.httpx, "AsyncClient", side_effect=mock_client_factory):
        entries = await provider.fetch_entries(source)
        assert len(entries) >= 1


@pytest.mark.asyncio
async def test_oecd_deduplication_by_doi(db_session: Session):
    """Verify deterministic deduplication by official DOI in IngestionService."""
    entity = TrackedEntity(
        id=uuid.uuid4(),
        display_name="OECD Competition Law and Policy",
        entity_type="publication",
        active=True,
    )
    db_session.add(entity)

    source = Source(
        id=uuid.uuid4(),
        name="OECD - Competition Law and Policy",
        url="https://www.oecd.org/en/topics/policy-issues/competition.html",
        type=SourceType.INSTITUTIONAL,
        provider="native",
        category="institutional",
        tracked_entity_id=entity.id,
    )
    db_session.add(source)

    # Existing Entry already persisted
    existing_entry = Entry(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="10.1787/1330d48b-en",
        url="https://www.oecd.org/en/publications/national-security-considerations-in-competition-enforcement_1330d48b-en.html",
        canonical_url="https://doi.org/10.1787/1330d48b-en",
        title="National security considerations in competition enforcement",
        content="Full paper content.",
        excerpt="Abstract.",
        published_at=datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc),
        captured_at=datetime.now(timezone.utc),
        language="en",
        content_type="policy_paper",
        content_hash="hash_oecd_national_security",
        raw_metadata={"doi": "10.1787/1330d48b-en"},
    )
    db_session.add(existing_entry)
    db_session.commit()

    service = IngestionService()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.crossref.org" in url:
            return httpx.Response(200, json=SAMPLE_CROSSREF_RESPONSE)
        if "openalex.org" in url:
            return httpx.Response(200, json={"title": "Test", "abstract_inverted_index": None})
        return httpx.Response(403)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def mock_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    import app.providers.native as native_module
    import unittest.mock as mock

    # Run ingestion simulating date on or after 2026-09-14
    with mock.patch.object(native_module.httpx, "AsyncClient", side_effect=mock_client_factory):
        with mock.patch(
            "app.providers.extractors.oecd_competition.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            run = await service.ingest_source(source.id, db_session)

    assert run.status == IngestionRunStatus.SUCCESS.value
    assert run.fetched == 2
    # 1 duplicate detected (10.1787/1330d48b-en), 1 new created (10.1787/62c9a81e-en)
    assert run.duplicates == 1
    assert run.created == 1


def test_weekly_refresh_oecd_dry_run(db_session: Session):
    """Test that WeeklyRefreshService safely processes OECD source in dry-run mode."""
    source = Source(
        id=uuid.uuid4(),
        name="OECD - Competition Law and Policy",
        url="https://www.oecd.org/en/topics/policy-issues/competition.html",
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
        sources_filter=["OECD - Competition Law and Policy"],
    )

    assert report.status == "completed"
    assert report.is_dry_run is True
    assert len(report.per_source) == 1
    detail = report.per_source[0]
    assert detail.source_name == "OECD - Competition Law and Policy"
    assert detail.status == "success"
    assert detail.new_entries == 0
