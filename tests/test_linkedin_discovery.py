"""Tests for LinkedIn post discovery via external providers (Bright Data & Apify) (Bloque 9C)."""

import json
import uuid
from datetime import datetime, timezone
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.models.provider import ProviderUsage
from app.models.analysis import EntryAnalysis
from app.models.tracking import TrackingMatrix, TrackedEntity
from app.providers.linkedin.base import (
    LinkedInDiscoveredPost,
    LinkedInAuthError,
    LinkedInRecoverableError,
    LinkedInTimeoutError,
    LinkedInQuotaExceededError,
)
from app.providers.linkedin.brightdata import BrightDataLinkedInProvider
from app.providers.linkedin.apify import ApifyLinkedInProvider
from app.services.linkedin_discovery_planner import (
    LinkedInDiscoveryPlanner,
    LinkedInDiscoveryJob,
)
from app.services.linkedin_ingestion_service import (
    LinkedInIngestionService,
    LinkedInIngestionReport,
)


# ==============================================================================
# 1. BRIGHT DATA PROVIDER TESTS
# ==============================================================================

MOCK_BRIGHTDATA_POSTS = [
    {
        "url": "https://www.linkedin.com/posts/hausfeld_antitrust-litigation-activity-7123456789",
        "id": "7123456789",
        "author": "Hausfeld",
        "use_url": "https://www.linkedin.com/company/hausfeld",
        "post_text": "Groundbreaking developments in European private enforcement and cartel damages litigation.",
        "date_posted": "2026-03-15T14:30:00Z",
        "num_likes": 42,
        "num_comments": 5,
        "num_reposts": 8,
        "account_type": "Organization",
        "post_type": "post",
        "user_followers": 25000,
    },
    {
        "url": "https://www.linkedin.com/posts/hausfeld_competition-appeal-tribunal-activity-7123456790",
        "id": "7123456790",
        "author": "Hausfeld",
        "use_url": "https://www.linkedin.com/company/hausfeld",
        "post_text": "Analysis of the latest CAT judgment on trucks cartel collective proceedings.",
        "date_posted": "2026-03-16T10:00:00Z",
        "num_likes": 88,
        "num_comments": 12,
        "num_reposts": 15,
        "account_type": "Organization",
        "post_type": "post",
    },
]


def test_brightdata_provider_success(monkeypatch):
    """Verify Bright Data provider builds correct request headers, payload, and parses posts."""
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-xyz")
    monkeypatch.setattr(settings, "BRIGHTDATA_LINKEDIN_DATASET_ID", "mock_dataset_123")

    captured_request = {}

    def mock_transport_handler(request: httpx.Request):
        captured_request["url"] = str(request.url)
        captured_request["headers"] = dict(request.headers)
        captured_request["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json=MOCK_BRIGHTDATA_POSTS)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))

    provider = BrightDataLinkedInProvider()
    posts = provider.discover_posts(
        target_url="https://www.linkedin.com/company/hausfeld",
        client=client,
        limit=5,
        entity_name="Hausfeld",
    )

    # 1. Verify request format
    assert "mock_dataset_123" in captured_request["url"]
    assert captured_request["headers"]["authorization"] == "Bearer mock-token-xyz"
    assert captured_request["body"]["input"][0]["url"] == "https://www.linkedin.com/company/hausfeld"
    assert captured_request["body"]["input"][0]["only_authored_posts"] is True

    # 2. Verify parsed posts
    assert len(posts) == 2
    p1 = posts[0]
    assert p1.provider == "brightdata"
    assert p1.provider_item_id == "7123456789"
    assert p1.author_name == "Hausfeld"
    assert "Groundbreaking developments" in p1.text
    assert p1.published_at.year == 2026
    assert p1.published_at.month == 3
    assert p1.engagement["likes"] == 42
    assert p1.raw_metadata["dataset_id"] == "mock_dataset_123"


def test_brightdata_provider_auth_error_fails_closed(monkeypatch):
    """Verify Bright Data fails closed with LinkedInAuthError on 401/403 without fallback."""
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "bad-token")

    def mock_transport_handler(request: httpx.Request):
        return httpx.Response(401, json={"error": "Unauthorized"})

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = BrightDataLinkedInProvider()

    with pytest.raises(LinkedInAuthError) as exc_info:
        provider.discover_posts("https://www.linkedin.com/company/hausfeld", client)
    assert "authentication error" in str(exc_info.value).lower()


def test_brightdata_provider_missing_token_raises_auth_error(monkeypatch):
    """Verify Bright Data raises LinkedInAuthError immediately when token is empty."""
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "")
    monkeypatch.setattr(settings, "BRIGHTDATA_API_KEY", "")

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    provider = BrightDataLinkedInProvider()

    with pytest.raises(LinkedInAuthError):
        provider.discover_posts("https://www.linkedin.com/company/hausfeld", client)


def test_brightdata_provider_rate_limit_and_timeout(monkeypatch):
    """Verify 429 raises LinkedInQuotaExceededError and timeout raises LinkedInTimeoutError."""
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "token")

    # 429 test
    client_429 = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, text="Too Many Requests")))
    provider = BrightDataLinkedInProvider()
    with pytest.raises(LinkedInQuotaExceededError):
        provider.discover_posts("https://www.linkedin.com/company/hausfeld", client_429)

    # Timeout test
    def timeout_handler(r):
        raise httpx.ReadTimeout("Connection timed out")

    client_timeout = httpx.Client(transport=httpx.MockTransport(timeout_handler))
    with pytest.raises(LinkedInTimeoutError):
        provider.discover_posts("https://www.linkedin.com/company/hausfeld", client_timeout)


# ==============================================================================
# 2. APIFY PROVIDER TESTS
# ==============================================================================

MOCK_APIFY_ITEMS = [
    {
        "url": "https://www.linkedin.com/posts/hausfeld_antitrust-litigation-activity-7123456789",
        "urn": "7123456789",
        "authorName": "Hausfeld",
        "authorProfileUrl": "https://www.linkedin.com/company/hausfeld",
        "text": "Groundbreaking developments in European private enforcement and cartel damages litigation.",
        "postedAt": "2026-03-15T14:30:00Z",
        "likesCount": 42,
        "commentsCount": 5,
        "repostsCount": 8,
    }
]


def test_apify_provider_success(monkeypatch):
    """Verify Apify fallback provider executes sync run endpoint and parses items."""
    settings = get_settings()
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "mock-apify-token")
    monkeypatch.setattr(settings, "APIFY_LINKEDIN_ACTOR_ID", "mock-actor-xyz")

    captured_request = {}

    def mock_transport_handler(request: httpx.Request):
        captured_request["url"] = str(request.url)
        captured_request["headers"] = dict(request.headers)
        captured_request["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json=MOCK_APIFY_ITEMS)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = ApifyLinkedInProvider()

    posts = provider.discover_posts(
        target_url="https://www.linkedin.com/company/hausfeld",
        client=client,
        limit=5,
        entity_name="Hausfeld",
    )

    assert "mock-actor-xyz/run-sync-get-dataset-items" in captured_request["url"]
    assert captured_request["headers"]["authorization"] == "Bearer mock-apify-token"
    assert len(posts) == 1
    p = posts[0]
    assert p.provider == "apify"
    assert p.provider_item_id == "7123456789"
    assert p.author_name == "Hausfeld"
    assert p.engagement["likes"] == 42


def test_apify_provider_auth_error_and_timeout(monkeypatch):
    """Verify Apify raises LinkedInAuthError on 401 and LinkedInTimeoutError on timeout."""
    settings = get_settings()
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "bad-token")

    client_401 = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    provider = ApifyLinkedInProvider()
    with pytest.raises(LinkedInAuthError):
        provider.discover_posts("https://www.linkedin.com/company/hausfeld", client_401)

    client_timeout = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("Timeout"))))
    with pytest.raises(LinkedInTimeoutError):
        provider.discover_posts("https://www.linkedin.com/company/hausfeld", client_timeout)


# ==============================================================================
# 3. PLANNER TESTS
# ==============================================================================

def test_planner_deterministic_selection_and_caps(db_session: Session):
    """Verify planner selects active verified entities with deterministic ordering and caps."""
    # 1. Create active TrackingMatrix
    matrix = TrackingMatrix(
        code="HITCHINGS-TEST",
        name="Test Matrix",
        status="active",
    )
    db_session.add(matrix)

    # 2. Add test entities with varying statuses and metadata
    e1 = TrackedEntity(
        display_name="Entity A Org",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/entity-a"},
    )
    e2 = TrackedEntity(
        display_name="Entity B Inst",
        entity_type="institution",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/entity-b"},
    )
    e3 = TrackedEntity(
        display_name="Entity C Inactive",
        entity_type="organization",
        active=False,  # Inactive must be ignored
        metadata_={"linkedin_url": "https://www.linkedin.com/company/entity-c"},
    )
    e4 = TrackedEntity(
        display_name="Entity D No LinkedIn",
        entity_type="person",
        active=True,
        metadata_={},  # No LinkedIn URL must be skipped
    )
    e5 = TrackedEntity(
        display_name="Entity E Duplicate URL",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/entity-a/"},  # Duplicate URL
    )
    db_session.add_all([e1, e2, e3, e4, e5])
    db_session.commit()

    planner = LinkedInDiscoveryPlanner()
    jobs = planner.plan_jobs(db_session, max_entities=10)

    # Should select only e2 (Institution, priority 90) and e1 (Organization, priority 80)
    assert len(jobs) == 2
    assert jobs[0].entity_name == "Entity B Inst"
    assert jobs[0].priority == 90
    assert jobs[1].entity_name == "Entity A Org"
    assert jobs[1].priority == 80

    # Test cap enforcement
    capped_jobs = planner.plan_jobs(db_session, max_entities=1)
    assert len(capped_jobs) == 1
    assert capped_jobs[0].entity_name == "Entity B Inst"


# ==============================================================================
# 4. INGESTION SERVICE & CROSS-DEDUPE TESTS
# ==============================================================================

def test_ingestion_creates_canonical_source_and_entries(db_session: Session, monkeypatch):
    """Verify canonical Source creation, Entry attributes, and metadata recording."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "token")

    matrix = TrackingMatrix(code="TEST-MAT", name="Test", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    def mock_transport_handler(request: httpx.Request):
        return httpx.Response(200, json=MOCK_BRIGHTDATA_POSTS)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))

    service = LinkedInIngestionService()
    report = service.execute_discovery(
        db=db_session,
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report.entries_created == 2
    assert report.duplicates == 0
    assert report.failed_jobs == 0
    assert report.fallback_count == 0

    # Verify canonical Source
    source = db_session.execute(select(Source).where(Source.type == SourceType.LINKEDIN)).scalar_one()
    assert source.name == "LinkedIn"
    assert source.provider == "external"
    assert source.tracked_entity_id is None

    # Verify created Entries
    entries = db_session.execute(select(Entry).where(Entry.source_id == source.id)).scalars().all()
    assert len(entries) == 2
    first_entry = entries[0]
    assert first_entry.author == "Hausfeld"
    assert first_entry.title.startswith("LinkedIn — Hausfeld — 2026-03-")
    assert first_entry.raw_metadata["tracked_entity_id"] == str(entity.id)
    assert first_entry.raw_metadata["tracked_entity_name"] == "Hausfeld"
    assert first_entry.raw_metadata["provider"] == "brightdata"
    assert first_entry.raw_metadata["fallback_used"] is False

    # Verify NO EntryAnalysis created (0 Gemini!)
    analyses = db_session.execute(select(EntryAnalysis)).scalars().all()
    assert len(analyses) == 0


def test_provider_cross_dedupe(db_session: Session, monkeypatch):
    """Verify that a post seen via Bright Data and later via Apify is deduplicated to 1 Entry."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "bd-token")
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "apify-token")

    matrix = TrackingMatrix(code="TEST-MAT", name="Test", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    service = LinkedInIngestionService()

    # 1. Run with Bright Data
    client_bd = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=MOCK_BRIGHTDATA_POSTS[:1])))
    report1 = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client_bd)
    assert report1.entries_created == 1

    # 2. Run again with Apify returning the same post URL
    client_apify = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=MOCK_APIFY_ITEMS)))
    # Inject Apify as primary for this second run to simulate cross-provider deduplication
    service_apify_primary = LinkedInIngestionService(primary_provider=ApifyLinkedInProvider())
    report2 = service_apify_primary.execute_discovery(db=db_session, confirm_real_calls=True, client=client_apify)

    # Must be recognized as duplicate, 0 new entries
    assert report2.entries_created == 0
    assert report2.duplicates == 1

    # In database, exactly 1 entry exists
    total_entries = db_session.execute(select(Entry)).scalars().all()
    assert len(total_entries) == 1


def test_primary_timeout_triggers_fallback(db_session: Session, monkeypatch):
    """Verify that a primary timeout error cleanly triggers the Apify fallback."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "bd-token")
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "apify-token")

    matrix = TrackingMatrix(code="TEST-MAT", name="Test", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    def router(request: httpx.Request):
        url_str = str(request.url)
        if "brightdata" in url_str:
            raise httpx.ReadTimeout("Primary Bright Data timed out")
        # Fallback Apify succeeds
        return httpx.Response(200, json=MOCK_APIFY_ITEMS)

    mock_client = httpx.Client(transport=httpx.MockTransport(router))
    service = LinkedInIngestionService()
    report = service.execute_discovery(db=db_session, confirm_real_calls=True, client=mock_client)

    assert report.entries_created == 1
    assert report.fallback_count == 1
    assert report.failed_jobs == 0

    entry = db_session.execute(select(Entry)).scalars().first()
    assert entry.raw_metadata["provider"] == "apify"
    assert entry.raw_metadata["fallback_used"] is True
    assert "timed out" in entry.raw_metadata["fallback_reason"].lower()


def test_primary_auth_error_does_not_trigger_fallback(db_session: Session, monkeypatch):
    """Verify that a 401/403 credentials error on primary FAILS CLOSED and does NOT call fallback."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "bad-token")
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "apify-token")

    matrix = TrackingMatrix(code="TEST-MAT", name="Test", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    fallback_called = False

    def router(request: httpx.Request):
        nonlocal fallback_called
        url_str = str(request.url)
        if "brightdata" in url_str:
            return httpx.Response(401, json={"error": "Unauthorized"})
        fallback_called = True
        return httpx.Response(200, json=MOCK_APIFY_ITEMS)

    mock_client = httpx.Client(transport=httpx.MockTransport(router))
    service = LinkedInIngestionService()
    report = service.execute_discovery(db=db_session, confirm_real_calls=True, client=mock_client)

    assert fallback_called is False
    assert report.failed_jobs == 1
    assert report.fallback_count == 0
    assert report.entries_created == 0


def test_provider_usage_accounting(db_session: Session, monkeypatch):
    """Verify that provider usage records are properly tracked in provider_usage table."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "token")
    monkeypatch.setattr(settings, "BRIGHTDATA_COST_PER_RECORD_USD", 0.0025)

    matrix = TrackingMatrix(code="TEST-MAT", name="Test", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=MOCK_BRIGHTDATA_POSTS)))
    service = LinkedInIngestionService()
    report = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client)

    # 2 items processed * $0.0025 = $0.005 estimated cost
    assert report.estimated_provider_cost == 0.005

    # Check ProviderUsage table
    current_period = datetime.now(timezone.utc).strftime("%Y-%m")
    usage = db_session.execute(
        select(ProviderUsage).where(
            ProviderUsage.provider == "brightdata",
            ProviderUsage.period == current_period,
        )
    ).scalar_one()

    assert usage.records_used == 2
