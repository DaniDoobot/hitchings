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
        "id": "7123456789",
        "linkedinUrl": "https://www.linkedin.com/posts/hausfeld_antitrust-litigation-activity-7123456789",
        "content": "Groundbreaking developments in European private enforcement and cartel damages litigation.",
        "author": {
            "name": "Hausfeld",
            "linkedinUrl": "https://www.linkedin.com/company/hausfeld",
        },
        "postedAt": {
            "date": "2026-03-15T14:30:00Z",
        },
        "engagement": {
            "likes": 42,
            "comments": 5,
            "shares": 8,
        },
        "type": "post",
    }
]


def test_apify_endpoint_shape_and_host_path(monkeypatch):
    """Verify Apify endpoint generates host 'api.apify.com' and path with '~' actor delimiter (Bloque 9C.2)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "mock-apify-token")
    monkeypatch.setattr(settings, "APIFY_LINKEDIN_ENDPOINT", "https://api.apify.com/v2/acts")
    monkeypatch.setattr(settings, "APIFY_LINKEDIN_ACTOR_ID", "harvestapi/linkedin-profile-posts")

    captured = {}

    def mock_transport_handler(request: httpx.Request):
        captured["host"] = request.url.host
        captured["path"] = request.url.path
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = ApifyLinkedInProvider()
    provider.discover_posts(
        target_url="https://www.linkedin.com/company/hausfeld",
        client=client,
        limit=5,
    )

    assert captured["host"] == "api.apify.com"
    assert captured["path"] == "/v2/acts/harvestapi~linkedin-profile-posts/run-sync-get-dataset-items"


def test_apify_provider_company_url_success(monkeypatch):
    """Verify Apify fallback provider handles Company URLs with harvestapi/linkedin-profile-posts schema."""
    settings = get_settings()
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "mock-apify-token")
    monkeypatch.setattr(settings, "APIFY_LINKEDIN_ACTOR_ID", "harvestapi/linkedin-profile-posts")

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

    # 1. Endpoint path verification
    assert "/v2/acts/harvestapi~linkedin-profile-posts/run-sync-get-dataset-items" in captured_request["url"]
    assert captured_request["headers"]["authorization"] == "Bearer mock-apify-token"

    # 2. Input contract verification: targetUrls, maxPosts, scrapeReactions, scrapeComments
    body = captured_request["body"]
    assert body["targetUrls"] == ["https://www.linkedin.com/company/hausfeld"]
    assert body["maxPosts"] == 5
    assert body["scrapeReactions"] is False
    assert body["scrapeComments"] is False

    # 3. Absence of forbidden / deprecated fields
    forbidden_keys = {
        "searchqueries", "authorurls", "cookie", "cookies", "li_at",
        "session", "password", "username", "login", "jsessionid"
    }
    for k in body.keys():
        assert k.lower() not in forbidden_keys, f"Forbidden field '{k}' found in payload"

    # 4. Output parsed verification
    assert len(posts) == 1
    p = posts[0]
    assert p.provider == "apify"
    assert p.provider_item_id == "7123456789"
    assert p.author_name == "Hausfeld"
    assert p.engagement["likes"] == 42
    assert p.engagement["reposts"] == 8
    assert p.text == "Groundbreaking developments in European private enforcement and cartel damages litigation."


def test_apify_provider_person_url_success(monkeypatch):
    """Verify Apify fallback provider handles Person profile URLs (Bloque 9C.2)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "mock-apify-token")
    monkeypatch.setattr(settings, "APIFY_LINKEDIN_ACTOR_ID", "harvestapi/linkedin-profile-posts")

    mock_person_post = [
        {
            "id": "7999888777",
            "linkedinUrl": "https://www.linkedin.com/posts/expert-jurist_antitrust-digital-markets-7999888777",
            "content": "Key takeaways from the CJEU judgment on abuse of dominance and digital platforms.",
            "author": {
                "name": "Dr. Expert Jurist",
                "publicIdentifier": "expert-jurist",
                "type": "profile",
                "linkedinUrl": "https://www.linkedin.com/in/expert-jurist",
            },
            "postedAt": {
                "date": "2026-03-18T09:15:00Z",
            },
            "engagement": {
                "likes": 95,
                "comments": 14,
                "shares": 12,
            },
            "type": "post",
        }
    ]

    captured_request = {}

    def mock_transport_handler(request: httpx.Request):
        captured_request["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json=mock_person_post)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = ApifyLinkedInProvider()

    posts = provider.discover_posts(
        target_url="https://www.linkedin.com/in/expert-jurist",
        client=client,
        limit=3,
        entity_name="Dr. Expert Jurist",
    )

    body = captured_request["body"]
    assert body["targetUrls"] == ["https://www.linkedin.com/in/expert-jurist"]
    assert body["maxPosts"] == 3
    assert body["scrapeReactions"] is False
    assert body["scrapeComments"] is False

    assert len(posts) == 1
    p = posts[0]
    assert p.provider == "apify"
    assert p.provider_item_id == "7999888777"
    assert p.author_name == "Dr. Expert Jurist"
    assert p.author_profile_url == "https://www.linkedin.com/in/expert-jurist"
    assert p.engagement["likes"] == 95


def test_apify_payload_contains_no_cookies(monkeypatch):
    """Verify that Apify LinkedIn provider payload never contains cookies, passwords, or personal tokens."""
    settings = get_settings()
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "mock-apify-token")

    captured_request = {}

    def mock_transport_handler(request: httpx.Request):
        captured_request["headers"] = dict(request.headers)
        captured_request["body"] = json.loads(request.read().decode())
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = ApifyLinkedInProvider()

    provider.discover_posts(
        target_url="https://www.linkedin.com/company/hausfeld",
        client=client,
        limit=5,
    )

    body = captured_request["body"]
    headers = captured_request["headers"]

    # Assert no cookie/credential/deprecated fields in payload
    forbidden_keys = {
        "searchqueries", "authorurls", "cookie", "cookies", "li_at",
        "session", "password", "username", "login", "jsessionid"
    }
    for k in body.keys():
        assert k.lower() not in forbidden_keys, f"Forbidden key '{k}' found in Apify payload"

    # Assert no cookie header
    assert "cookie" not in [h.lower() for h in headers.keys()]
    assert "harvestapi" in settings.APIFY_LINKEDIN_ACTOR_ID


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
    assert first_entry.raw_metadata["retrieval_provider"] == "brightdata"
    assert "provider" not in first_entry.raw_metadata
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
    assert entry.raw_metadata["retrieval_provider"] == "apify"
    assert "provider" not in entry.raw_metadata
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


def test_cnmc_incorrect_url_rejected_and_canonical_validated(db_session: Session):
    """Regression test (Bloque 9C.1): Ensure 'company/cnmc' is rejected and canonical URL is validated.

    'https://www.linkedin.com/company/cnmc' belongs to Capital North Management Company.
    The Spanish regulator must strictly use the canonical URL:
    'https://www.linkedin.com/company/cnmc-comision-nacional-de-los-mercados-y-la-competencia'
    with declared official domain 'cnmc.es'.
    """
    from scripts.ingest_linkedin import VERIFIED_LINKEDIN_PILOT_ENTITIES

    cnmc_pilot = next(
        p for p in VERIFIED_LINKEDIN_PILOT_ENTITIES
        if "CNMC" in p["name"] or "Mercados" in p["name"]
    )

    # Assert wrong handle is explicitly rejected
    assert cnmc_pilot["linkedin_url"] != "https://www.linkedin.com/company/cnmc"
    assert "capital" not in cnmc_pilot["linkedin_url"].lower()

    # Assert canonical URL and domain
    assert cnmc_pilot["linkedin_url"] == "https://www.linkedin.com/company/cnmc-comision-nacional-de-los-mercados-y-la-competencia"
    assert cnmc_pilot["linkedin_declared_website"] == "cnmc.es"
    assert cnmc_pilot["linkedin_url_verified"] is True
    assert cnmc_pilot["linkedin_url_verification_method"] == "public_linkedin_page_identity_and_official_domain"
    assert cnmc_pilot["linkedin_url_verified_at"] is not None


def test_normalizer_extract_activity_id_and_canonical_url():
    """Verify normalizer extracts activity IDs of various lengths and normalizes URLs."""
    from app.providers.linkedin.normalizer import (
        extract_linkedin_activity_id,
        normalize_linkedin_canonical_url,
        resolve_canonical_identity,
    )

    # 1. Non-19 digit ID / varying lengths
    assert extract_linkedin_activity_id("urn:li:activity:7123456789") == "7123456789"
    assert extract_linkedin_activity_id("urn:li:activity:12345") == "12345"
    assert extract_linkedin_activity_id("https://www.linkedin.com/feed/update/urn:li:activity:98765432101234567890/") == "98765432101234567890"
    assert extract_linkedin_activity_id("https://www.linkedin.com/posts/acme_update-activity-7123456789-abcd") == "7123456789"
    assert extract_linkedin_activity_id("urn:li:share:555444333") == "555444333"

    # 2. URL normalization (strip tracking query params, fragments, trailing slashes)
    dirty_url = "https://www.linkedin.com/posts/lawfirm_antitrust-activity-7123456789?utm_source=share&utm_medium=member_desktop#comments"
    clean_url = normalize_linkedin_canonical_url(dirty_url)
    assert clean_url == "https://www.linkedin.com/posts/lawfirm_antitrust-activity-7123456789"

    # 3. Canonical identity resolution with activity ID
    ext_id, canon_url, act_id, identity_status = resolve_canonical_identity(dirty_url)
    assert ext_id == "urn:li:activity:7123456789"
    assert canon_url == "https://www.linkedin.com/posts/lawfirm_antitrust-activity-7123456789"
    assert act_id == "7123456789"
    assert identity_status == "activity_id"

    # 4. Fallback identity when activity ID cannot be extracted
    generic_url = "https://www.linkedin.com/pulse/some-article-slug?trk=pulse-article"
    f_ext_id, f_canon_url, f_act_id, f_identity_status = resolve_canonical_identity(generic_url)
    assert f_ext_id.startswith("linkedin:post:")
    assert f_canon_url == "https://www.linkedin.com/pulse/some-article-slug"
    assert f_act_id is None
    assert f_identity_status == "canonical_url_fallback"


def test_cross_provider_dedupe_brightdata_and_apify_different_urls(db_session: Session, monkeypatch):
    """Verify that the same post coming in different URL formats from Bright Data and Apify deduplicates to 1 Entry."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "bd-token")
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "apify-token")

    matrix = TrackingMatrix(code="TEST-CROSS-DEDUPE", name="Test Dedupe", status="active")
    entity = TrackedEntity(
        display_name="Cuatrecasas",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/cuatrecasas"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    service = LinkedInIngestionService()

    # Bright Data returns post with /posts/ slug URL
    bd_post = [{
        "url": "https://www.linkedin.com/posts/cuatrecasas_antitrust-bulletin-activity-7299881122334455667?utm_source=li",
        "id": "7299881122334455667",
        "author": "Cuatrecasas",
        "use_url": "https://www.linkedin.com/company/cuatrecasas",
        "post_text": "Publicación sobre nuevo reglamento de concentraciones en la UE.",
        "date_posted": "2026-03-15T12:00:00Z",
        "account_type": "Organization",
    }]
    client_bd = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=bd_post)))
    report1 = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client_bd)
    assert report1.entries_created == 1

    entry1 = db_session.execute(select(Entry).where(Entry.external_id == "urn:li:activity:7299881122334455667")).scalar_one()
    assert entry1.author == "Cuatrecasas"
    assert entry1.raw_metadata["author_type"] == "organization"
    assert entry1.raw_metadata["retrieval_provider"] == "brightdata"
    assert "provider" not in entry1.raw_metadata  # Strict Requirement 2: legacy key not written to new entries
    assert entry1.raw_metadata["identity_status"] == "activity_id"
    assert entry1.raw_metadata["provenance_status"] == "verified"
    assert entry1.raw_metadata["linkedin_activity_id"] == "7299881122334455667"
    assert "?" not in entry1.url

    # Apify returns SAME post but with /feed/update/... URL format and different query params
    apify_post = [{
        "id": "7299881122334455667",
        "linkedinUrl": "https://www.linkedin.com/feed/update/urn:li:activity:7299881122334455667/?view=true",
        "content": "Publicación sobre nuevo reglamento de concentraciones en la UE.",
        "author": {
            "name": "Cuatrecasas",
            "linkedinUrl": "https://www.linkedin.com/company/cuatrecasas",
        },
        "postedAt": {"date": "2026-03-15T12:00:00Z"},
        "type": "post",
    }]
    client_apify = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=apify_post)))
    service_apify = LinkedInIngestionService(primary_provider=ApifyLinkedInProvider())
    report2 = service_apify.execute_discovery(db=db_session, confirm_real_calls=True, client=client_apify)

    # Cross-provider deduplication succeeds via external_id = urn:li:activity:7299881122334455667
    assert report2.entries_created == 0
    assert report2.duplicates == 1

    # Exactly 1 entry in DB
    total = db_session.execute(select(Entry).where(Entry.external_id == "urn:li:activity:7299881122334455667")).scalars().all()
    assert len(total) == 1


def test_missing_or_generic_author_fails_closed(db_session: Session, monkeypatch):
    """Verify that posts with missing or generic author names are skipped (fail-closed)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "bd-token")

    matrix = TrackingMatrix(code="TEST-AUTHOR-FAIL", name="Test Author", status="active")
    entity = TrackedEntity(
        display_name="Uría Menéndez",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/uria-menendez"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    posts_with_bad_authors = [
        {
            "url": "https://www.linkedin.com/posts/activity-111111111",
            "id": "111111111",
            "author": "",  # Empty
            "post_text": "Post with empty author name.",
            "date_posted": "2026-03-15T10:00:00Z",
        },
        {
            "url": "https://www.linkedin.com/posts/activity-222222222",
            "id": "222222222",
            "author": "LinkedIn Author",  # Generic placeholder
            "post_text": "Post with generic author name.",
            "date_posted": "2026-03-15T11:00:00Z",
        },
        {
            "url": "https://www.linkedin.com/posts/activity-333333333",
            "id": "333333333",
            "author": "Unknown",  # Generic placeholder
            "post_text": "Post with unknown author name.",
            "date_posted": "2026-03-15T12:00:00Z",
        },
        {
            "url": "https://www.linkedin.com/posts/activity-444444444",
            "id": "444444444",
            "author": "Uría Menéndez",  # Valid
            "post_text": "Valid post with legitimate author.",
            "date_posted": "2026-03-15T13:00:00Z",
        },
    ]

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=posts_with_bad_authors)))
    service = LinkedInIngestionService()
    report = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client)

    # 3 bad posts skipped, 1 valid created
    assert report.entries_created == 1
    assert report.duplicates == 0

    valid_entry = db_session.execute(select(Entry).where(Entry.external_id == "urn:li:activity:444444444")).scalar_one()
    assert valid_entry.author == "Uría Menéndez"
    assert valid_entry.raw_metadata["retrieval_provider"] == "brightdata"
    assert "provider" not in valid_entry.raw_metadata
    assert valid_entry.raw_metadata["identity_status"] == "activity_id"
    assert valid_entry.raw_metadata["provenance_status"] == "verified"
    assert "brightdata" not in valid_entry.author.lower()


def test_incoherent_author_profile_url_skips_fail_closed(db_session: Session, monkeypatch):
    """Verify that a post with author_profile_url mismatched from the tracked entity URL is skipped (fail-closed)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "bd-token")

    matrix = TrackingMatrix(code="TEST-INCOHERENT", name="Test Incoherent", status="active")
    entity = TrackedEntity(
        display_name="Garrigues",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/garrigues"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    # Post has legitimate author name but author profile URL points to a different entity
    impostor_post = [{
        "url": "https://www.linkedin.com/posts/activity-999999999",
        "id": "999999999",
        "author": "Garrigues",
        "use_url": "https://www.linkedin.com/company/someone-else-entirely",  # Incoherent with Garrigues
        "post_text": "Post claiming to be from Garrigues but from different company page.",
        "date_posted": "2026-03-15T10:00:00Z",
    }]

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=impostor_post)))
    service = LinkedInIngestionService()
    report = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client)

    # Must be skipped fail-closed: 0 entries created
    assert report.entries_created == 0
    assert report.duplicates == 0


def test_identity_and_provenance_separation_with_fallback(db_session: Session, monkeypatch):
    """Verify that a post with verified author but without numeric activity ID gets fallback identity and verified provenance."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "bd-token")

    matrix = TrackingMatrix(code="TEST-IDENTITY-SEP", name="Test Sep", status="active")
    entity = TrackedEntity(
        display_name="Miguel Sousa Ferro",
        entity_type="person",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/in/miguel-sousa-ferro-b7551666"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    # Post with coherent author profile URL but non-activity URL structure (no numeric ID)
    fallback_post = [{
        "url": "https://www.linkedin.com/pulse/eu-competition-law-briefing-2026?trk=pulse-article",
        "id": "",  # No numeric activity ID
        "author": "Miguel Sousa Ferro",
        "use_url": "https://pt.linkedin.com/in/miguel-sousa-ferro-b7551666/",  # Regional subdomain + trailing slash -> coherent
        "post_text": "New developments in private enforcement of competition law.",
        "date_posted": "2026-03-15T11:00:00Z",
        "account_type": "Person",
    }]

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=fallback_post)))
    service = LinkedInIngestionService()
    report = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client)

    assert report.entries_created == 1
    entry = db_session.execute(select(Entry).where(Entry.author == "Miguel Sousa Ferro")).scalar_one()

    # Identity status is canonical_url_fallback, but provenance status is verified
    assert entry.raw_metadata["identity_status"] == "canonical_url_fallback"
    assert entry.raw_metadata["provenance_status"] == "verified"
    assert entry.external_id.startswith("linkedin:post:")
    assert entry.raw_metadata["linkedin_activity_id"] is None
    assert entry.raw_metadata["retrieval_provider"] == "brightdata"
    assert "provider" not in entry.raw_metadata  # Strict Requirement 2


def test_legacy_provider_read_compatibility(db_session: Session):
    """Verify that get_retrieval_provider reads legacy 'provider' when 'retrieval_provider' is absent."""
    service = LinkedInIngestionService()
    source = service.get_or_create_linkedin_source(db_session)
    db_session.commit()

    # 1. Historical entry with only legacy 'provider'
    legacy_entry = Entry(
        source_id=source.id,
        external_id="urn:li:activity:1000000001",
        url="https://www.linkedin.com/posts/legacy-1",
        title="Legacy Entry",
        author="Historical Author",
        raw_metadata={"provider": "brightdata"},
    )
    db_session.add(legacy_entry)

    # 2. Modern entry with only 'retrieval_provider'
    modern_entry = Entry(
        source_id=source.id,
        external_id="urn:li:activity:1000000002",
        url="https://www.linkedin.com/posts/modern-2",
        title="Modern Entry",
        author="Modern Author",
        raw_metadata={"retrieval_provider": "apify"},
    )
    db_session.add(modern_entry)
    db_session.commit()

    assert LinkedInIngestionService.get_retrieval_provider(legacy_entry) == "brightdata"
    assert LinkedInIngestionService.get_retrieval_provider(modern_entry) == "apify"


def test_new_writes_never_include_legacy_provider_key(db_session: Session, monkeypatch):
    """Verify that raw_metadata on newly ingested entries never contains the legacy 'provider' key."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "bd-token")

    matrix = TrackingMatrix(code="TEST-NO-LEGACY-WRITE", name="Test No Legacy Write", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    post_data = [{
        "url": "https://www.linkedin.com/posts/hausfeld-activity-888888888",
        "id": "888888888",
        "author": "Hausfeld",
        "use_url": "https://www.linkedin.com/company/hausfeld",
        "post_text": "Antitrust litigation update.",
        "date_posted": "2026-03-15T12:00:00Z",
        # Even if raw data somehow had a 'provider' key:
        "provider": "brightdata",
    }]

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=post_data)))
    service = LinkedInIngestionService()
    report = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client)

    assert report.entries_created == 1
    entry = db_session.execute(select(Entry).where(Entry.external_id == "urn:li:activity:888888888")).scalar_one()
    assert "retrieval_provider" in entry.raw_metadata
    assert entry.raw_metadata["retrieval_provider"] == "brightdata"
    assert "provider" not in entry.raw_metadata


def test_is_author_profile_coherent_edge_cases():
    """Verify is_author_profile_coherent handles subdomains, casing, slashes, query params, and mismatches."""
    from app.providers.linkedin.normalizer import is_author_profile_coherent

    # Matching subdomains and casing
    assert is_author_profile_coherent(
        "https://es.linkedin.com/company/hausfeld/",
        "https://www.linkedin.com/company/hausfeld"
    ) is True

    assert is_author_profile_coherent(
        "https://pt.linkedin.com/in/miguel-sousa-ferro-b7551666?trk=public_profile",
        "https://www.linkedin.com/in/miguel-sousa-ferro-b7551666"
    ) is True

    # Trailing slashes
    assert is_author_profile_coherent(
        "https://linkedin.com/company/eskariam/",
        "https://www.linkedin.com/company/eskariam"
    ) is True

    # Incoherent / Mismatch
    assert is_author_profile_coherent(
        "https://www.linkedin.com/company/competitor",
        "https://www.linkedin.com/company/hausfeld"
    ) is False

    # Empty / None
    assert is_author_profile_coherent("", "https://www.linkedin.com/company/hausfeld") is False
    assert is_author_profile_coherent(None, "https://www.linkedin.com/company/hausfeld") is False
    assert is_author_profile_coherent("https://www.linkedin.com/company/hausfeld", None) is False



