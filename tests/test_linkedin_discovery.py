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
from app.models.tracking import TrackingMatrix, TrackedEntity, TrackingTopic
from app.providers.linkedin.base import (
    LinkedInDiscoveredPost,
    LinkedInAuthError,
    LinkedInRecoverableError,
    LinkedInTimeoutError,
    LinkedInSnapshotTimeoutError,
    LinkedInQuotaExceededError,
)
from app.providers.linkedin.brightdata import (
    BrightDataLinkedInProvider,
    build_discovery_payload,
    resolve_discover_by,
)
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
    assert "/trigger" in captured_request["url"]
    assert "type=discover_new" in captured_request["url"]
    assert "discover_by=company_url" in captured_request["url"]
    assert captured_request["headers"]["authorization"] == "Bearer mock-token-xyz"
    assert captured_request["body"] == [{"url": "https://www.linkedin.com/company/hausfeld"}]
    assert "only_authored_posts" not in captured_request["body"][0]


def test_brightdata_build_discovery_payload_and_params():
    """Verify build_discovery_payload and resolve_discover_by contracts for org vs person."""
    # 1. Organization / Company: discover_by=company_url, NO only_authored_posts
    org_url = "https://www.linkedin.com/company/hausfeld"
    assert resolve_discover_by(org_url, entity_type="organization") == "company_url"
    assert resolve_discover_by(org_url) == "company_url"

    payload_org = build_discovery_payload(org_url, entity_type="organization")
    assert payload_org == [{"url": org_url}]
    assert "only_authored_posts" not in payload_org[0]

    # Without explicit entity_type (inferred by /company/ in URL)
    payload_org_inferred = build_discovery_payload(org_url)
    assert payload_org_inferred == [{"url": org_url}]
    assert "only_authored_posts" not in payload_org_inferred[0]

    # 2. Person: discover_by=profile_url, WITH only_authored_posts=True
    person_url = "https://www.linkedin.com/in/alex-hitchings"
    assert resolve_discover_by(person_url, entity_type="person") == "profile_url"
    assert resolve_discover_by(person_url) == "profile_url"

    payload_person = build_discovery_payload(person_url, entity_type="person")
    assert payload_person == [{"url": person_url, "only_authored_posts": True}]
    assert payload_person[0]["only_authored_posts"] is True

    # Without explicit entity_type (inferred by /in/ in URL)
    payload_person_inferred = build_discovery_payload(person_url)
    assert payload_person_inferred == [{"url": person_url, "only_authored_posts": True}]
    assert payload_person_inferred[0]["only_authored_posts"] is True


def test_brightdata_provider_person_url_success(monkeypatch):
    """Verify Bright Data provider builds correct request with profile_url and only_authored_posts for person."""
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
        target_url="https://www.linkedin.com/in/alex-hitchings",
        client=client,
        limit=5,
        entity_name="Alex Hitchings",
        entity_type="person",
    )

    assert "mock_dataset_123" in captured_request["url"]
    assert "/trigger" in captured_request["url"]
    assert "type=discover_new" in captured_request["url"]
    assert "discover_by=profile_url" in captured_request["url"]
    assert captured_request["body"] == [{"url": "https://www.linkedin.com/in/alex-hitchings", "only_authored_posts": True}]
    assert captured_request["body"][0]["only_authored_posts"] is True
    assert len(posts) == 2

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
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_MAX_POSTS_PER_ENTITY", 5)

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
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_MAX_POSTS_PER_ENTITY", 5)

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
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_MAX_POSTS_PER_ENTITY", 5)

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


def test_controlled_local_mock_full_flow_hausfeld(db_session: Session, client, monkeypatch):
    """Controlled local mock validation of the full LinkedIn discovery pipeline for Hausfeld.

    Validates:
    1. Planner selects Hausfeld entity.
    2. Provider mock returns post.
    3. Normalizer extracts activity ID -> external_id=urn:li:activity:{id}.
    4. Provenance: identity_status="activity_id", provenance_status="verified".
    5. Metadata does NOT contain legacy key "provider".
    6. Entry is created correctly in database.
    7. Dedupe functions when simulating second run from Apify with alternate URL.
    8. API & UI contract displays "LinkedIn · Hausfeld".
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-bd-token")
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "mock-apify-token")

    # 1. Seed TrackedEntity for Hausfeld
    matrix = TrackingMatrix(code="TEST-HAUSFELD-E2E", name="Hausfeld E2E Matrix", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={
            "linkedin_url": "https://www.linkedin.com/company/hausfeld",
            "linkedin_url_verified": True,
            "linkedin_entity_type": "organization",
        },
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    # Step 1: Planner selects entity
    planner = LinkedInDiscoveryPlanner()
    jobs = planner.plan_jobs(db_session)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.entity_name == "Hausfeld"
    assert job.entity_type == "organization"
    assert job.linkedin_url == "https://www.linkedin.com/company/hausfeld"
    assert job.tracked_entity_id == entity.id

    # Step 2: Provider mock (Bright Data) simulates 1 post
    activity_id = "7188223344556677889"
    bd_post_data = [{
        "url": f"https://www.linkedin.com/posts/hausfeld_antitrust-damages-activity-{activity_id}?utm_source=li_share",
        "id": activity_id,
        "author": "Hausfeld",
        "use_url": "https://www.linkedin.com/company/hausfeld",
        "post_text": "Groundbreaking CAT collective proceedings judgment on trucks cartel damages.",
        "date_posted": "2026-03-15T10:00:00Z",
        "account_type": "Organization",
    }]

    mock_bd_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=bd_post_data)))
    service = LinkedInIngestionService(planner=planner)
    report_bd = service.execute_discovery(db=db_session, confirm_real_calls=True, client=mock_bd_client)

    # Step 3, 4, 5, 6: Ingestion, Normalization, Provenance & DB Entry validation
    assert report_bd.entities_planned == 1
    assert report_bd.entities_executed == 1
    assert report_bd.posts_seen == 1
    assert report_bd.entries_created == 1
    assert report_bd.duplicates == 0
    assert report_bd.failed_jobs == 0

    expected_ext_id = f"urn:li:activity:{activity_id}"
    created_entry = db_session.execute(select(Entry).where(Entry.external_id == expected_ext_id)).scalar_one()

    # 3. Normalizer & external_id check
    assert created_entry.external_id == expected_ext_id
    assert created_entry.canonical_url == f"https://www.linkedin.com/posts/hausfeld_antitrust-damages-activity-{activity_id}"
    assert "?" not in created_entry.url

    # 4. Provenance & Identity status
    assert created_entry.raw_metadata["identity_status"] == "activity_id"
    assert created_entry.raw_metadata["provenance_status"] == "verified"
    assert created_entry.raw_metadata["author_name"] == "Hausfeld"
    assert created_entry.raw_metadata["author_type"] == "organization"
    assert created_entry.raw_metadata["author_profile_url"] == "https://www.linkedin.com/company/hausfeld"
    assert created_entry.raw_metadata["tracked_entity_id"] == str(entity.id)
    assert created_entry.raw_metadata["linkedin_activity_id"] == activity_id

    # 5. Metadata does NOT contain legacy key "provider", uses only "retrieval_provider"
    assert created_entry.raw_metadata["retrieval_provider"] == "brightdata"
    assert "provider" not in created_entry.raw_metadata

    # 6. Entry structure
    assert created_entry.author == "Hausfeld"
    assert created_entry.content_type == "social_post"
    assert created_entry.title == "LinkedIn — Hausfeld — 2026-03-15"
    assert "Groundbreaking CAT" in created_entry.content

    # Step 7: Dedupe validation with Apify returning same post with alternate URL format
    apify_post_data = [{
        "id": activity_id,
        "linkedinUrl": f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/?view=true",
        "content": "Groundbreaking CAT collective proceedings judgment on trucks cartel damages.",
        "author": {
            "name": "Hausfeld",
            "linkedinUrl": "https://www.linkedin.com/company/hausfeld",
        },
        "postedAt": {"date": "2026-03-15T10:00:00Z"},
        "type": "post",
    }]

    mock_apify_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=apify_post_data)))
    service_apify = LinkedInIngestionService(planner=planner, primary_provider=ApifyLinkedInProvider())
    report_apify = service_apify.execute_discovery(db=db_session, confirm_real_calls=True, client=mock_apify_client)

    assert report_apify.entries_created == 0
    assert report_apify.duplicates == 1

    total_in_db = db_session.execute(select(Entry).where(Entry.external_id == expected_ext_id)).scalars().all()
    assert len(total_in_db) == 1

    # Step 8: API & UI contract: "LinkedIn · Hausfeld"
    api_resp = client.get(f"/api/v1/entries/{created_entry.id}")
    assert api_resp.status_code == 200
    entry_payload = api_resp.json()

    assert entry_payload["author"] == "Hausfeld"
    assert entry_payload["content_type"] == "social_post"
    assert entry_payload["raw_metadata"]["author_type"] == "organization"
    assert entry_payload["raw_metadata"]["retrieval_provider"] == "brightdata"
    assert "provider" not in entry_payload["raw_metadata"]

    ui_header = f"LinkedIn · {entry_payload['author']}"
    assert ui_header == "LinkedIn · Hausfeld"


# ==============================================================================
# 9. CONTROLLED BRIGHT DATA PROBE TESTS (HAUSFELD)
# ==============================================================================

def test_brightdata_probe_guard_missing_token(db_session, monkeypatch):
    """Probe must fail-closed immediately if BRIGHTDATA_API_TOKEN is missing without logging secret."""
    from scripts.ingest_linkedin import run_brightdata_probe
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "")
    monkeypatch.setattr(settings, "BRIGHTDATA_API_KEY", "")

    exit_code, report, logs = run_brightdata_probe(db_session, confirm_real_calls=False, settings=settings)
    assert exit_code == 1
    assert report is None
    assert any("BRIGHTDATA_API_TOKEN is not configured" in log for log in logs)


def test_brightdata_probe_guard_entity_and_url_validation(db_session, monkeypatch):
    """Probe must validate unequivocal Hausfeld entity and exact configured LinkedIn URL."""
    from scripts.ingest_linkedin import run_brightdata_probe
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-secret")

    # 0. Reject if active TrackingMatrix is missing
    exit_code, report, logs = run_brightdata_probe(db_session, confirm_real_calls=False, settings=settings)
    assert exit_code == 1
    assert any("Active TrackingMatrix not found" in log for log in logs)

    # Add active TrackingMatrix
    matrix = TrackingMatrix(code="TEST-PROBE-VAL", name="Validation Matrix", status="active")
    db_session.add(matrix)
    db_session.commit()

    # 1. Reject targeting another entity
    exit_code, report, logs = run_brightdata_probe(
        db_session, confirm_real_calls=False, settings=settings, entity_override="CNMC"
    )
    assert exit_code == 1
    assert any("strictly locked to entity 'Hausfeld'" in log for log in logs)

    # 2. Reject if Hausfeld entity is not in database
    exit_code, report, logs = run_brightdata_probe(db_session, confirm_real_calls=False, settings=settings)
    assert exit_code == 1
    assert any("TrackedEntity for 'Hausfeld' not found" in log for log in logs)

    # 3. Create Hausfeld entity with wrong URL
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld-wrong"},
    )
    db_session.add(entity)
    db_session.commit()

    exit_code, report, logs = run_brightdata_probe(db_session, confirm_real_calls=False, settings=settings)
    assert exit_code == 1
    assert any("expected 'https://www.linkedin.com/company/hausfeld'" in log for log in logs)


def test_brightdata_probe_dry_run_success(db_session, monkeypatch):
    """Probe dry-run must validate all 7 guards, make 0 calls, 0 DB writes, and exit cleanly."""
    from scripts.ingest_linkedin import run_brightdata_probe
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-secret")

    matrix = TrackingMatrix(code="TEST-PROBE-DRY", name="Test Probe Matrix Dry", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={
            "linkedin_url": "https://www.linkedin.com/company/hausfeld",
            "linkedin_url_verified": True,
            "linkedin_entity_type": "organization",
        },
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    exit_code, report, logs = run_brightdata_probe(db_session, confirm_real_calls=False, settings=settings)
    assert exit_code == 0
    assert report is None
    # All 7 guards confirmed in dry run
    assert any("[GUARD 1]" in log for log in logs)
    assert any("[GUARD 2]" in log for log in logs)
    assert any("[GUARD 3]" in log for log in logs)
    assert any("[GUARD 4]" in log for log in logs)
    assert any("[GUARD 5]" in log for log in logs)
    assert any("[GUARD 6]" in log for log in logs)
    assert any("[GUARD 7]" in log for log in logs)

    # Confirm 0 entries written to DB
    entries = db_session.execute(select(Entry)).scalars().all()
    assert len(entries) == 0


def test_brightdata_probe_execution_success_and_itemized_reporting(db_session, monkeypatch):
    """Probe real execution must enforce max_posts=1, Bright Data only, and return complete itemized fields."""
    from scripts.ingest_linkedin import run_brightdata_probe
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-secret")
    monkeypatch.setattr(settings, "BRIGHTDATA_COST_PER_RECORD_USD", 0.0025)

    matrix = TrackingMatrix(code="TEST-PROBE-EXEC", name="Test Probe Matrix Exec", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={
            "linkedin_url": "https://www.linkedin.com/company/hausfeld",
            "linkedin_url_verified": True,
            "linkedin_entity_type": "organization",
        },
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    activity_id = "7199112233445566778"
    mock_response_data = [{
        "url": f"https://www.linkedin.com/posts/hausfeld_antitrust-probe-activity-{activity_id}?ref=test",
        "id": activity_id,
        "author": "Hausfeld",
        "use_url": "https://www.linkedin.com/company/hausfeld",
        "post_text": "First controlled probe of LinkedIn private enforcement publication.",
        "date_posted": "2026-03-20T12:00:00Z",
        "account_type": "Organization",
    }]

    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=mock_response_data))
    )

    exit_code, report, logs = run_brightdata_probe(
        db=db_session,
        confirm_real_calls=True,
        client=mock_client,
        settings=settings,
    )

    assert exit_code == 0
    assert report is not None
    assert report.entries_created == 1
    assert report.posts_seen == 1
    assert report.duplicates == 0
    assert report.failed_jobs == 0
    assert report.fallback_count == 0
    assert report.primary_provider == "brightdata"
    assert report.estimated_provider_cost == 0.0025

    # Validate itemized details for post-run reporting
    assert len(report.items_detail) == 1
    detail = report.items_detail[0]
    assert detail["http_status"] == 200
    assert detail["records_returned"] == 1
    assert detail["author_name"] == "Hausfeld"
    assert detail["author_profile_url"] == "https://www.linkedin.com/company/hausfeld"
    assert "https://www.linkedin.com/posts/hausfeld_antitrust-probe-activity-7199112233445566778" in detail["linkedin_post_url"]
    assert detail["activity_id"] == activity_id
    assert detail["published_at"] == "2026-03-20T12:00:00+00:00"
    assert detail["identity_status"] == "activity_id"
    assert detail["provenance_status"] == "verified"
    assert detail["retrieval_provider"] == "brightdata"
    assert detail["action"] == "CREATED"
    assert detail["entry_id"] is not None


def test_brightdata_probe_duplicate_detection(db_session, monkeypatch):
    """Subsequent probe execution on the same post must detect duplicate and record DUPLICATE action."""
    from scripts.ingest_linkedin import run_brightdata_probe
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-secret")

    matrix = TrackingMatrix(code="TEST-PROBE-DUP", name="Test Probe Matrix Dup", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    activity_id = "7200112233445566779"
    mock_response_data = [{
        "url": f"https://www.linkedin.com/posts/hausfeld_antitrust-probe2-activity-{activity_id}",
        "id": activity_id,
        "author": "Hausfeld",
        "use_url": "https://www.linkedin.com/company/hausfeld",
        "post_text": "Second controlled probe for duplicate verification.",
        "date_posted": "2026-03-21T09:00:00Z",
    }]

    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=mock_response_data))
    )

    # First run: creates entry
    code1, report1, _ = run_brightdata_probe(db_session, confirm_real_calls=True, client=mock_client, settings=settings)
    assert code1 == 0
    assert report1.entries_created == 1
    assert report1.items_detail[0]["action"] == "CREATED"

    # Second run: detects duplicate
    code2, report2, _ = run_brightdata_probe(db_session, confirm_real_calls=True, client=mock_client, settings=settings)
    assert code2 == 0
    assert report2.entries_created == 0
    assert report2.duplicates == 1
    assert report2.items_detail[0]["action"] == "DUPLICATE"


def test_brightdata_probe_no_fallback_on_error(db_session, monkeypatch):
    """Probe must strictly NOT trigger Apify fallback when Bright Data encounters a recoverable server error."""
    from scripts.ingest_linkedin import run_brightdata_probe
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-secret")
    monkeypatch.setattr(settings, "APIFY_API_TOKEN", "mock-apify-token-present")

    matrix = TrackingMatrix(code="TEST-PROBE-ERR", name="Test Probe Matrix Err", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    # Bright Data returns 500 error
    error_client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="Internal Gateway Error"))
    )

    exit_code, report, logs = run_brightdata_probe(
        db_session, confirm_real_calls=True, client=error_client, settings=settings
    )

    assert exit_code == 0
    assert report is not None
    assert report.failed_jobs == 1
    assert report.fallback_count == 0  # Fallback was strictly disabled
    assert report.entries_created == 0
    assert len(report.items_detail) == 1
    assert report.items_detail[0]["action"] == "FAILED"


# ==============================================================================
# 9. BRIGHT DATA ASYNC SNAPSHOT DISCOVERY FLOW TESTS
# ==============================================================================

def test_brightdata_async_snapshot_flow_success(monkeypatch, caplog):
    """Verify Bright Data async flow: POST creates snapshot -> poll running -> poll ready -> download snapshot."""
    import logging
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-xyz")
    monkeypatch.setattr(settings, "BRIGHTDATA_LINKEDIN_DATASET_ID", "gd_lyy3tktm25m4avu764")

    post_calls = []
    poll_calls = []
    download_calls = []

    def mock_transport_handler(request: httpx.Request):
        url_str = str(request.url)
        if request.method == "POST" and "trigger" in url_str:
            post_calls.append(url_str)
            return httpx.Response(200, json={"snapshot_id": "sd_mu34g4n42ptmtxyidh", "status": "running"})
        elif request.method == "GET" and "progress/sd_mu34g4n42ptmtxyidh" in url_str:
            poll_calls.append(url_str)
            if len(poll_calls) == 1:
                return httpx.Response(200, json={"status": "running"})
            return httpx.Response(200, json={"status": "ready"})
        elif request.method == "GET" and "snapshot/sd_mu34g4n42ptmtxyidh" in url_str:
            download_calls.append(url_str)
            assert "format=json" in url_str
            return httpx.Response(200, json=MOCK_BRIGHTDATA_POSTS)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = BrightDataLinkedInProvider(poll_interval=0.001, max_poll_attempts=5, poll_timeout=5.0)

    with caplog.at_level(logging.INFO):
        posts = provider.discover_posts(
            target_url="https://www.linkedin.com/company/hausfeld",
            client=client,
            limit=5,
            entity_name="Hausfeld",
        )

    assert len(post_calls) == 1
    assert "/trigger" in post_calls[0]
    assert len(poll_calls) == 2
    assert len(download_calls) == 1
    assert len(posts) == 2
    assert posts[0].provider_item_id == "7123456789"
    assert posts[0].author_name == "Hausfeld"

    # Verify clear structured logs
    log_text = caplog.text
    assert "Bright Data snapshot created: snapshot_id=sd_mu34g4n42ptmtxyidh" in log_text
    assert "Bright Data snapshot_id=sd_mu34g4n42ptmtxyidh current status: running" in log_text
    assert "Bright Data snapshot_id=sd_mu34g4n42ptmtxyidh current status: ready" in log_text
    assert "Bright Data snapshot download completed: snapshot_id=sd_mu34g4n42ptmtxyidh" in log_text


def test_brightdata_async_snapshot_person_url_flow(monkeypatch):
    """Verify Bright Data async flow works seamlessly for personal profile URLs."""
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-xyz")

    captured_post = {}

    def mock_transport_handler(request: httpx.Request):
        url_str = str(request.url)
        if request.method == "POST":
            captured_post["url"] = url_str
            captured_post["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json={"snapshot_id": "sd_person_999", "status": "running"})
        elif "progress/sd_person_999" in url_str:
            return httpx.Response(200, json={"status": "completed"})
        elif "snapshot/sd_person_999" in url_str:
            return httpx.Response(200, json=MOCK_BRIGHTDATA_POSTS[:1])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = BrightDataLinkedInProvider(poll_interval=0.001, max_poll_attempts=5)

    posts = provider.discover_posts(
        target_url="https://www.linkedin.com/in/alex-hitchings",
        client=client,
        limit=5,
        entity_name="Alex Hitchings",
        entity_type="person",
    )

    assert "/trigger" in captured_post["url"]
    assert "type=discover_new" in captured_post["url"]
    assert "discover_by=profile_url" in captured_post["url"]
    assert captured_post["body"][0]["only_authored_posts"] is True
    assert len(posts) == 1
    assert posts[0].provider_item_id == "7123456789"


def test_brightdata_async_snapshot_polling_timeout(monkeypatch):
    """Verify Bright Data raises LinkedInTimeoutError if polling exceeds max_attempts."""
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-xyz")

    def mock_transport_handler(request: httpx.Request):
        url_str = str(request.url)
        if request.method == "POST":
            return httpx.Response(200, json={"snapshot_id": "sd_stuck_123", "status": "running"})
        elif "progress/sd_stuck_123" in url_str:
            return httpx.Response(200, json={"status": "running"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = BrightDataLinkedInProvider(poll_interval=0.001, max_poll_attempts=3, poll_timeout=5.0)

    with pytest.raises(LinkedInTimeoutError) as exc_info:
        provider.discover_posts("https://www.linkedin.com/company/hausfeld", client=client)

    assert "sd_stuck_123" in str(exc_info.value)
    assert "did not complete within 3 attempts" in str(exc_info.value)


def test_brightdata_async_snapshot_failed_status(monkeypatch):
    """Verify Bright Data raises LinkedInRecoverableError if snapshot progress reports failed/error."""
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-xyz")

    def mock_transport_handler(request: httpx.Request):
        url_str = str(request.url)
        if request.method == "POST":
            return httpx.Response(200, json={"snapshot_id": "sd_fail_123"})
        elif "progress/sd_fail_123" in url_str:
            return httpx.Response(200, json={"status": "failed"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = BrightDataLinkedInProvider(poll_interval=0.001, max_poll_attempts=3)

    with pytest.raises(LinkedInRecoverableError) as exc_info:
        provider.discover_posts("https://www.linkedin.com/company/hausfeld", client=client)

    assert "sd_fail_123 failed with status: failed" in str(exc_info.value)


def test_brightdata_async_snapshot_dedupe_and_provenance(db_session, monkeypatch):
    """Verify full ingestion with async snapshot produces verified provenance and exact deduplication."""
    from scripts.ingest_linkedin import run_brightdata_probe
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-secret")

    matrix = TrackingMatrix(code="TEST-PROBE-ASYNC", name="Test Async Probe Matrix", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    def mock_transport_handler(request: httpx.Request):
        url_str = str(request.url)
        if request.method == "POST":
            return httpx.Response(200, json={"snapshot_id": "sd_async_dedupe_123"})
        elif "progress/sd_async_dedupe_123" in url_str:
            return httpx.Response(200, json={"status": "ready"})
        elif "snapshot/sd_async_dedupe_123" in url_str:
            return httpx.Response(200, json=MOCK_BRIGHTDATA_POSTS[:1])
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))

    # Fast polling for test
    monkeypatch.setattr(settings, "BRIGHTDATA_POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(settings, "BRIGHTDATA_POLL_MAX_ATTEMPTS", 5)

    # 1. Run 1: Creates entry
    code1, report1, _ = run_brightdata_probe(db_session, confirm_real_calls=True, client=mock_client, settings=settings)
    assert code1 == 0
    assert report1.entries_created == 1
    assert report1.items_detail[0]["action"] == "CREATED"
    assert report1.items_detail[0]["provenance_status"] == "verified"
    assert report1.items_detail[0]["identity_status"] == "activity_id"
    assert report1.items_detail[0]["retrieval_provider"] == "brightdata"

    # 2. Run 2: Exact duplicate detected
    code2, report2, _ = run_brightdata_probe(db_session, confirm_real_calls=True, client=mock_client, settings=settings)
    assert code2 == 0
    assert report2.entries_created == 0
    assert report2.duplicates == 1
    assert report2.items_detail[0]["action"] == "DUPLICATE"


# ==============================================================================
# PHASE 1-5: OPERATIONAL DISCOVERY PIPELINE TESTS
# ==============================================================================

def test_controlled_local_mock_full_flow_person(db_session: Session, client, monkeypatch):
    """Phase 1: Full controlled discovery flow for a person entity (e.g. Miguel Sousa Ferro).

    Validates:
    - payload with only_authored_posts: true
    - discover_by=profile_url
    - async Bright Data flow (/trigger -> /progress -> /snapshot)
    - parse of author_profile_url and author_name
    - creation of Entry with content_type="social_post", author_type="person"
    - provenance_status="verified", identity_status="activity_id"
    - deduplication on second run
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-bd-token")
    monkeypatch.setattr(settings, "BRIGHTDATA_POLL_INTERVAL_SECONDS", 0.001)

    person_name = "Miguel Sousa Ferro"
    person_url = "https://www.linkedin.com/in/miguel-sousa-ferro-b7551666"

    matrix = TrackingMatrix(code="TEST-PERSON-E2E", name="Person E2E Matrix", status="active")
    entity = TrackedEntity(
        display_name=person_name,
        entity_type="person",
        active=True,
        metadata_={
            "linkedin_url": person_url,
            "linkedin_url_verified": True,
            "linkedin_entity_type": "person",
        },
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    # Step 1: Planner selects entity
    planner = LinkedInDiscoveryPlanner()
    jobs = planner.plan_jobs(db_session, max_entities=10)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.entity_name == person_name
    assert job.entity_type == "person"
    assert job.linkedin_url == person_url
    assert job.priority == 70

    # Step 2: Mock async Bright Data
    activity_id = "7991122334455667788"
    snapshot_id = "sd_person_mock_789"
    captured_trigger_params = {}
    captured_trigger_payload = []

    def mock_transport_handler(request: httpx.Request):
        url_str = str(request.url)
        if "/datasets/v3/trigger" in url_str:
            captured_trigger_params.update(dict(request.url.params))
            captured_trigger_payload.extend(json.loads(request.read()))
            return httpx.Response(200, json={"snapshot_id": snapshot_id, "status": "running"})
        elif f"/progress/{snapshot_id}" in url_str:
            return httpx.Response(200, json={"status": "ready"})
        elif f"/snapshot/{snapshot_id}" in url_str:
            return httpx.Response(200, json=[{
                "url": f"https://www.linkedin.com/posts/miguel-sousa-ferro-b7551666_private-enforcement-activity-{activity_id}?ref=share",
                "id": activity_id,
                "author": person_name,
                "use_url": person_url,
                "post_text": "Groundbreaking analysis on private enforcement of EU competition law.",
                "date_posted": "2026-03-15T11:00:00Z",
                "account_type": "Person",
            }])
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    service = LinkedInIngestionService(planner=planner)
    report = service.execute_discovery(db=db_session, confirm_real_calls=True, client=mock_client)

    # Validate trigger params & payload for person
    assert captured_trigger_params.get("discover_by") == "profile_url"
    assert captured_trigger_params.get("type") == "discover_new"
    assert len(captured_trigger_payload) == 1
    assert captured_trigger_payload[0]["url"] == person_url
    assert captured_trigger_payload[0]["only_authored_posts"] is True

    # Validate report
    assert report.entities_executed == 1
    assert report.posts_seen == 1
    assert report.entries_created == 1
    assert report.duplicates == 0

    # Validate Entry
    expected_ext_id = f"urn:li:activity:{activity_id}"
    created_entry = db_session.execute(select(Entry).where(Entry.external_id == expected_ext_id)).scalar_one()
    assert created_entry.author == person_name
    assert created_entry.content_type == "social_post"
    assert created_entry.canonical_url == f"https://www.linkedin.com/posts/miguel-sousa-ferro-b7551666_private-enforcement-activity-{activity_id}"
    assert created_entry.raw_metadata["author_type"] == "person"
    assert created_entry.raw_metadata["author_name"] == person_name
    assert created_entry.raw_metadata["author_profile_url"] == person_url
    assert created_entry.raw_metadata["provenance_status"] == "verified"
    assert created_entry.raw_metadata["identity_status"] == "activity_id"
    assert created_entry.raw_metadata["retrieval_provider"] == "brightdata"

    # Step 3: Deduplication on second run
    report2 = service.execute_discovery(db=db_session, confirm_real_calls=True, client=mock_client)
    assert report2.entries_created == 0
    assert report2.duplicates == 1

    total_in_db = db_session.execute(select(Entry).where(Entry.external_id == expected_ext_id)).scalars().all()
    assert len(total_in_db) == 1


def test_linkedin_cost_calculation(db_session: Session, monkeypatch):
    """Phase 2: Cost tracking with BRIGHTDATA_LINKEDIN_POST_COST_PER_RECORD and fallback rates."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-bd-token")

    matrix = TrackingMatrix(code="TEST-COST", name="Cost Matrix", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    post_data = [{
        "url": "https://www.linkedin.com/posts/hausfeld_test-activity-71990001",
        "id": "71990001",
        "author": "Hausfeld",
        "use_url": "https://www.linkedin.com/company/hausfeld",
        "post_text": "Cost tracking post.",
        "date_posted": "2026-03-15T10:00:00Z",
    }]
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=post_data)))
    service = LinkedInIngestionService()

    # Case A: BRIGHTDATA_LINKEDIN_POST_COST_PER_RECORD configured
    monkeypatch.setattr(settings, "BRIGHTDATA_LINKEDIN_POST_COST_PER_RECORD", 0.0025)
    monkeypatch.setattr(settings, "BRIGHTDATA_COST_PER_RECORD_USD", None)
    report_a = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client)
    assert report_a.provider_records_fetched == 1
    assert report_a.estimated_provider_cost == 0.0025

    # Case B: BRIGHTDATA_LINKEDIN_POST_COST_PER_RECORD is None, falls back to BRIGHTDATA_COST_PER_RECORD_USD
    monkeypatch.setattr(settings, "BRIGHTDATA_LINKEDIN_POST_COST_PER_RECORD", None)
    monkeypatch.setattr(settings, "BRIGHTDATA_COST_PER_RECORD_USD", 0.0050)
    report_b = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client)
    assert report_b.provider_records_fetched == 1
    assert report_b.estimated_provider_cost == 0.0050

    # Case C: Both cost settings unset (None)
    monkeypatch.setattr(settings, "BRIGHTDATA_LINKEDIN_POST_COST_PER_RECORD", None)
    monkeypatch.setattr(settings, "BRIGHTDATA_COST_PER_RECORD_USD", None)
    report_c = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client)
    assert report_c.provider_records_fetched == 1
    assert report_c.estimated_provider_cost is None


def test_batch_planner_multiple_entities_and_safe_caps(db_session: Session):
    """Phase 3: Decoupled multi-entity discovery planner with safe caps and deterministic priority ordering."""
    matrix = TrackingMatrix(code="TEST-BATCH-PLAN", name="Batch Plan Matrix", status="active")
    inst = TrackedEntity(
        display_name="European Commission",
        entity_type="institution",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/european-commission"},
    )
    org = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    person = TrackedEntity(
        display_name="Miguel Sousa Ferro",
        entity_type="person",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/in/miguel-sousa-ferro-b7551666"},
    )
    db_session.add_all([matrix, inst, org, person])
    db_session.commit()

    planner = LinkedInDiscoveryPlanner()

    # Default cap: LINKEDIN_DISCOVERY_MAX_ENTITIES = 1
    jobs_default = planner.plan_jobs(db_session)
    assert len(jobs_default) == 1
    assert jobs_default[0].entity_name == "European Commission"
    assert jobs_default[0].priority == 90

    # Explicit cap: 2
    jobs_2 = planner.plan_jobs(db_session, max_entities=2)
    assert len(jobs_2) == 2
    assert jobs_2[0].entity_name == "European Commission"
    assert jobs_2[1].entity_name == "Hausfeld"

    # All jobs: cap=10 (deterministic sort: priority DESC, display_name ASC)
    jobs_all = planner.plan_jobs(db_session, max_entities=10)
    assert len(jobs_all) == 3
    assert [j.entity_name for j in jobs_all] == ["European Commission", "Hausfeld", "Miguel Sousa Ferro"]
    assert [j.priority for j in jobs_all] == [90, 80, 70]


def test_batch_planner_fail_closed_missing_or_invalid_url(db_session: Session):
    """Phase 3: Entities with missing, invalid or non-HTTP LinkedIn URLs fail-closed and are excluded."""
    matrix = TrackingMatrix(code="TEST-FAIL-CLOSED-URL", name="Fail Closed Matrix", status="active")
    e_no_meta = TrackedEntity(display_name="Entity No Meta", entity_type="organization", active=True, metadata_={})
    e_bad_url = TrackedEntity(display_name="Entity Bad URL", entity_type="organization", active=True, metadata_={"linkedin_url": "ftp://not-linkedin.com"})
    e_inactive = TrackedEntity(display_name="Entity Inactive", entity_type="organization", active=False, metadata_={"linkedin_url": "https://www.linkedin.com/company/valid"})
    e_valid = TrackedEntity(display_name="Entity Valid", entity_type="organization", active=True, metadata_={"linkedin_url": "https://www.linkedin.com/company/valid"})

    db_session.add_all([matrix, e_no_meta, e_bad_url, e_inactive, e_valid])
    db_session.commit()

    planner = LinkedInDiscoveryPlanner()
    jobs = planner.plan_jobs(db_session, max_entities=10)
    assert len(jobs) == 1
    assert jobs[0].entity_name == "Entity Valid"


def test_discovery_fail_closed_author_mismatch(db_session: Session, monkeypatch):
    """Phase 3: Discovered post with mismatched author profile fails closed (provenance_status='unverified')."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token")

    matrix = TrackingMatrix(code="TEST-MISMATCH", name="Mismatch Matrix", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    # Discovered post has author_profile_url pointing to a completely different company
    mismatched_post = [{
        "url": "https://www.linkedin.com/posts/unrelated_post-activity-719999999",
        "id": "719999999",
        "author": "Hausfeld",
        "use_url": "https://www.linkedin.com/company/some-unrelated-company",
        "post_text": "Post with mismatched authorship profile.",
        "date_posted": "2026-03-15T10:00:00Z",
    }]
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=mismatched_post)))
    service = LinkedInIngestionService()
    report = service.execute_discovery(db=db_session, confirm_real_calls=True, client=client)

    assert report.entries_created == 0
    assert len(report.items_detail) == 1
    assert report.items_detail[0]["action"] == "SKIPPED_PROVENANCE"
    assert report.items_detail[0]["provenance_status"] == "unverified"

    # Database contains 0 entries
    entries = db_session.execute(select(Entry)).scalars().all()
    assert len(entries) == 0


def test_cli_batch_run_output_formatting(db_session: Session, monkeypatch, capsys):
    """Phase 4: CLI batch run executes discovery and formats LINKEDIN DISCOVERY REPORT accurately."""
    from scripts.ingest_linkedin import main
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-bd-token")
    monkeypatch.setattr(settings, "BRIGHTDATA_LINKEDIN_POST_COST_PER_RECORD", 0.0025)

    matrix = TrackingMatrix(code="TEST-CLI-REPORT", name="CLI Matrix", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    post_data = [{
        "url": "https://www.linkedin.com/posts/hausfeld_cli-test-activity-7200112233",
        "id": "7200112233",
        "author": "Hausfeld",
        "use_url": "https://www.linkedin.com/company/hausfeld",
        "post_text": "CLI reporting post test.",
        "date_posted": "2026-03-15T10:00:00Z",
    }]

    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=post_data)))

    # Intercept SessionLocal and httpx.Client inside ingest_linkedin
    monkeypatch.setattr("scripts.ingest_linkedin.SessionLocal", lambda: db_session)
    monkeypatch.setattr("app.services.linkedin_ingestion_service.httpx.Client", lambda *a, **kw: mock_client)

    # 1. Dry run
    monkeypatch.setattr("sys.argv", ["scripts.ingest_linkedin"])
    main()
    captured_dry = capsys.readouterr().out
    assert "[DRY RUN]" in captured_dry
    assert "HITCHINGS - LINKEDIN DISCOVERY" in captured_dry
    assert "Hausfeld" in captured_dry

    # 2. Confirmed real calls
    monkeypatch.setattr("sys.argv", ["scripts.ingest_linkedin", "--confirm-real-calls"])
    main()
    captured_real = capsys.readouterr().out

    assert "LINKEDIN DISCOVERY REPORT" in captured_real
    assert "Entities planned: 1" in captured_real
    assert "Entities executed: 1" in captured_real
    assert "Posts discovered: 1" in captured_real
    assert "Entries created: 1" in captured_real
    assert "Duplicates: 0" in captured_real
    assert "Failed: 0" in captured_real
    assert "Provider Consumption:" in captured_real
    assert "1 records fetched" in captured_real
    assert "Estimated Cost:" in captured_real
    assert "$0.0025 USD" in captured_real
    assert "Por entidad:" in captured_real
    assert "Entity: Hausfeld" in captured_real
    assert "Provider: brightdata" in captured_real
    assert "Posts: 1" in captured_real
    assert "Created: 1" in captured_real
    assert "Duplicates: 0" in captured_real
    assert "Provenance rejected: 0" in captured_real
    assert "Errors: 0" in captured_real


def test_manual_override_with_discovery_disabled(db_session, monkeypatch):
    """Verify allow_manual=True permits batch execution even when LINKEDIN_DISCOVERY_ENABLED=False."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", False)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token")
    monkeypatch.setattr(settings, "BRIGHTDATA_LINKEDIN_DATASET_ID", "mock_ds")

    matrix = TrackingMatrix(code="TEST-OVERRIDE", name="Test Matrix", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    post_data = [{
        "url": "https://www.linkedin.com/posts/hausfeld_override-test-activity-7200998877",
        "id": "7200998877",
        "author": "Hausfeld",
        "use_url": "https://www.linkedin.com/company/hausfeld",
        "post_text": "Manual override test.",
        "date_posted": "2026-03-15T10:00:00Z",
    }]
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=post_data)))

    service = LinkedInIngestionService()

    # 1. Without allow_manual -> Aborted
    rep_blocked = service.execute_discovery(
        db=db_session,
        confirm_real_calls=True,
        client=mock_client,
        allow_manual=False,
    )
    assert len(rep_blocked.errors) == 1
    assert "LinkedIn discovery is disabled in settings." in rep_blocked.errors[0]
    assert rep_blocked.entries_created == 0

    # 2. With allow_manual=True -> Successfully executes
    rep_ok = service.execute_discovery(
        db=db_session,
        confirm_real_calls=True,
        client=mock_client,
        allow_manual=True,
    )
    assert len(rep_ok.errors) == 0
    assert rep_ok.entries_created == 1
    assert rep_ok.provenance_rejected == 0


def test_provenance_rejection_tracking(db_session, monkeypatch):
    """Verify incoherent author_profile_url increments provenance_rejected and reflects in per_entity."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token")
    monkeypatch.setattr(settings, "BRIGHTDATA_LINKEDIN_DATASET_ID", "mock_ds")

    matrix = TrackingMatrix(code="TEST-PROV-REJ", name="Test Matrix 2", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld Prov",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    # Author profile URL does NOT match entity URL
    post_data = [{
        "url": "https://www.linkedin.com/posts/imposter_fake-post-activity-7200991122",
        "id": "7200991122",
        "author": "Imposter Law",
        "use_url": "https://www.linkedin.com/company/imposter-law",
        "post_text": "Fake post not from Hausfeld.",
        "date_posted": "2026-03-15T10:00:00Z",
    }]
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=post_data)))

    service = LinkedInIngestionService()
    report = service.execute_discovery(
        db=db_session,
        confirm_real_calls=True,
        target_entity_id=entity.id,
        client=mock_client,
    )

    assert report.posts_seen == 1
    assert report.entries_created == 0
    assert report.provenance_rejected == 1
    assert report.per_entity[0]["provenance_rejected"] == 1


def test_entry_origin_properties_and_observatory_query(db_session):
    """Verify is_linkedin and source_origin_category on Entry and in Observatory query service."""
    from app.services.observatory_query_service import _build_list_item, _build_detail
    from app.services.topic_canonicalization_service import TopicHierarchy

    src_li = Source(
        name="LinkedIn",
        type=SourceType.LINKEDIN,
        category="social_media",
        active=True,
    )
    src_inst = Source(
        name="CNMC",
        type=SourceType.INSTITUTIONAL,
        category="official_authority",
        active=True,
    )
    db_session.add_all([src_li, src_inst])
    db_session.commit()

    e_li = Entry(
        source_id=src_li.id,
        url="https://www.linkedin.com/posts/hausfeld_test-activity-7123",
        canonical_url="https://www.linkedin.com/posts/hausfeld_test-activity-7123",
        content_type="social_post",
        author="Hausfeld",
        raw_metadata={
            "origin_source": "linkedin",
            "source_origin_category": "linkedin",
            "author_type": "organization",
        },
    )
    e_inst = Entry(
        source_id=src_inst.id,
        url="https://www.cnmc.es/resolucion-123",
        content_type="article",
        author="CNMC",
    )
    db_session.add_all([e_li, e_inst])
    db_session.commit()

    # Verify model properties
    assert e_li.is_linkedin is True
    assert e_li.source_origin_category == "linkedin"
    assert e_inst.is_linkedin is False
    assert e_inst.source_origin_category == "institutional"

    # Verify query service mapping
    analysis = EntryAnalysis(
        entry_id=e_li.id,
        status="completed",
        relevance_status="relevant",
        relevance_score=85,
        confidence=0.9,
        topics=[],
        key_points=["Key point 1"],
    )
    hierarchy = TopicHierarchy([])

    item = _build_list_item(e_li, analysis, hierarchy)
    assert item.is_linkedin is True
    assert item.source_origin_category == "linkedin"
    assert item.source.name == "LinkedIn"
    assert item.source.type == "linkedin"
    assert item.source.category == "social_media"

    detail = _build_detail(e_li, analysis, hierarchy)
    assert detail.is_linkedin is True
    assert detail.source_origin_category == "linkedin"


# ==============================================================================
# 11. RESILIENCE & REPORT CLASSIFICATION TESTS
# ==============================================================================

def test_brightdata_async_snapshot_ready_after_150_seconds(monkeypatch):
    """Verify snapshot becoming ready after 150s is valid and succeeds under 300s timeout."""
    settings = get_settings()
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-xyz")
    monkeypatch.setattr(settings, "BRIGHTDATA_POLL_TIMEOUT_SECONDS", 300.0)
    monkeypatch.setattr(settings, "BRIGHTDATA_POLL_MAX_ATTEMPTS", 40)

    # Simulated clock: starts at t=0.0
    simulated_time = [0.0]

    def mock_time():
        return simulated_time[0]

    monkeypatch.setattr("time.time", mock_time)

    def mock_transport_handler(request: httpx.Request):
        url_str = str(request.url)
        if request.method == "POST" and "trigger" in url_str:
            return httpx.Response(200, json={"snapshot_id": "sd_150s_ready", "status": "running"})
        elif "progress/sd_150s_ready" in url_str:
            if simulated_time[0] >= 150.0:
                return httpx.Response(200, json={"status": "ready"})
            return httpx.Response(200, json={"status": "running"})
        elif "snapshot/sd_150s_ready" in url_str:
            return httpx.Response(200, json=MOCK_BRIGHTDATA_POSTS[:1])
        return httpx.Response(404)

    def advance_time(seconds):
        simulated_time[0] += 50.0

    client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = BrightDataLinkedInProvider(sleep_fn=advance_time)

    assert provider.poll_timeout == 300.0
    assert provider.max_poll_attempts == 40

    posts = provider.discover_posts(
        target_url="https://www.linkedin.com/company/hausfeld",
        client=client,
        limit=5,
        entity_name="Hausfeld",
    )

    assert len(posts) == 1
    assert posts[0].provider_item_id == "7123456789"
    assert simulated_time[0] >= 150.0


def test_brightdata_async_snapshot_timeout_after_300_seconds_classified_as_timed_out_snapshot(
    db_session: Session, monkeypatch
):
    """Verify timeout after 300s raises LinkedInSnapshotTimeoutError and is classified as timed_out_snapshots, NOT provider_errors."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-xyz")
    monkeypatch.setattr(settings, "BRIGHTDATA_POLL_TIMEOUT_SECONDS", 300.0)
    monkeypatch.setattr(settings, "BRIGHTDATA_POLL_MAX_ATTEMPTS", 40)

    matrix = TrackingMatrix(code="TEST-TIMEOUT-300", name="Test Timeout 300s Matrix", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    simulated_time = [0.0]

    def mock_time():
        return simulated_time[0]

    monkeypatch.setattr("time.time", mock_time)

    def mock_transport_handler(request: httpx.Request):
        url_str = str(request.url)
        if request.method == "POST" and "trigger" in url_str:
            return httpx.Response(200, json={"snapshot_id": "sd_timeout_300", "status": "running"})
        elif "progress/sd_timeout_300" in url_str:
            return httpx.Response(200, json={"status": "running"})
        return httpx.Response(404)

    def advance_time_past_300(seconds):
        simulated_time[0] += 305.0

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    provider = BrightDataLinkedInProvider(sleep_fn=advance_time_past_300)

    service = LinkedInIngestionService(primary_provider=provider)
    report = service.execute_discovery(
        db=db_session,
        confirm_real_calls=True,
        target_entity_id=entity.id,
        client=mock_client,
        disable_fallback=True,
    )

    # Classification assertions:
    assert report.timed_out_snapshots == 1
    assert report.provider_errors == 0
    assert report.failed_jobs == 1
    assert report.entries_created == 0
    assert len(report.items_detail) == 1
    assert report.items_detail[0]["action"] == "TIMED_OUT_SNAPSHOT"
    assert "sd_timeout_300" in report.items_detail[0]["error"]
    assert report.per_entity[0]["timed_out_snapshots"] == 1
    assert report.per_entity[0]["provider_errors"] == 0


def test_brightdata_provider_error_classified_as_provider_error(db_session: Session, monkeypatch):
    """Verify HTTP 500 server error is classified as provider_errors, NOT timed_out_snapshots."""
    settings = get_settings()
    monkeypatch.setattr(settings, "LINKEDIN_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "BRIGHTDATA_API_TOKEN", "mock-token-xyz")

    matrix = TrackingMatrix(code="TEST-PROV-ERR", name="Test Provider Error Matrix", status="active")
    entity = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={"linkedin_url": "https://www.linkedin.com/company/hausfeld"},
    )
    db_session.add_all([matrix, entity])
    db_session.commit()

    def mock_transport_handler(request: httpx.Request):
        return httpx.Response(500, text="Internal Server Error")

    mock_client = httpx.Client(transport=httpx.MockTransport(mock_transport_handler))
    service = LinkedInIngestionService()
    report = service.execute_discovery(
        db=db_session,
        confirm_real_calls=True,
        target_entity_id=entity.id,
        client=mock_client,
        disable_fallback=True,
    )

    assert report.provider_errors == 1
    assert report.timed_out_snapshots == 0
    assert report.failed_jobs == 1
    assert report.entries_created == 0
    assert report.items_detail[0]["action"] == "FAILED"
    assert report.per_entity[0]["provider_errors"] == 1
    assert report.per_entity[0]["timed_out_snapshots"] == 0


# ==============================================================================
# 12. LINKEDIN ANALYSIS PIPELINE VALIDATION TESTS
# ==============================================================================

def setup_analysis_matrix_and_topics(db: Session) -> TrackingMatrix:
    """Helper to ensure an active TrackingMatrix with valid topics exists."""
    from scripts.seed_analysis_prompts import seed_analysis_prompts

    seed_analysis_prompts(db)

    matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    if not matrix:
        matrix = TrackingMatrix(
            code=f"TEST-LI-ANALYSIS-{uuid.uuid4().hex[:6]}",
            name="Matriz de Seguimiento LinkedIn",
            status="active",
            relevance_instructions="Focus on antitrust, litigation, cartel damages, competition enforcement.",
            exclusion_instructions="Exclude corporate promotions or unrelated law.",
        )
        db.add(matrix)
        db.flush()

    area = db.query(TrackingTopic).filter(TrackingTopic.matrix_id == matrix.id, TrackingTopic.parent_id.is_(None)).first()
    if not area:
        area = TrackingTopic(
            matrix_id=matrix.id,
            parent_id=None,
            code="competencia_general",
            name="Competencia General",
            priority=1,
            active=True,
        )
        db.add(area)
        db.flush()

    topic = db.query(TrackingTopic).filter(TrackingTopic.matrix_id == matrix.id, TrackingTopic.parent_id.is_not(None)).first()
    if not topic:
        topic = TrackingTopic(
            matrix_id=matrix.id,
            parent_id=area.id,
            code="carteles_antidanos",
            name="Cárteles y Daños",
            description="Reclamaciones de daños por infracciones de cárteles",
            keywords=["cartel", "danos", "antitrust"],
            priority=1,
            active=True,
        )
        db.add(topic)
        db.flush()

    return matrix


def test_linkedin_entry_selection_unanalyzed_and_already_analyzed(db_session: Session):
    """Verify that unanalyzed LinkedIn entries enter the pipeline while analyzed entries are ignored."""
    from scripts.analyze_linkedin_batch import get_unanalyzed_linkedin_entries

    matrix = setup_analysis_matrix_and_topics(db_session)
    source = Source(
        id=uuid.uuid4(),
        name="LinkedIn",
        type=SourceType.LINKEDIN,
        url="https://www.linkedin.com",
        active=True,
    )
    db_session.add(source)
    db_session.flush()

    # 1. Unanalyzed LinkedIn Entry
    entry_unanalyzed = Entry(
        source_id=source.id,
        url=f"https://www.linkedin.com/posts/hausfeld-{uuid.uuid4()}",
        canonical_url=f"https://www.linkedin.com/posts/hausfeld-{uuid.uuid4()}",
        title="LinkedIn — Hausfeld — 2026-03-15",
        content="Antitrust collective action filed against truck cartel infringers.",
        author="Hausfeld",
        content_type="social_post",
        raw_metadata={
            "origin_source": "linkedin",
            "source_origin_category": "linkedin",
            "tracked_entity_name": "Hausfeld",
        },
    )

    # 2. Already analyzed LinkedIn Entry
    entry_analyzed = Entry(
        source_id=source.id,
        url=f"https://www.linkedin.com/posts/eskariam-{uuid.uuid4()}",
        canonical_url=f"https://www.linkedin.com/posts/eskariam-{uuid.uuid4()}",
        title="LinkedIn — ESKARIAM — 2026-03-15",
        content="Resolución favorable en litigación colectiva de competencia.",
        author="ESKARIAM",
        content_type="social_post",
        raw_metadata={
            "origin_source": "linkedin",
            "source_origin_category": "linkedin",
            "tracked_entity_name": "ESKARIAM",
        },
    )
    db_session.add_all([entry_unanalyzed, entry_analyzed])
    db_session.flush()

    # Add completed analysis to entry_analyzed
    analysis = EntryAnalysis(
        entry_id=entry_analyzed.id,
        matrix_id=matrix.id,
        pipeline_version="v6",
        status="completed",
        matrix_snapshot={"topics": []},
        matrix_snapshot_hash="mock-hash",
        relevance_score=88,
        relevance_status="relevant",
        summary="Resumen completado",
    )
    db_session.add(analysis)
    db_session.commit()

    # Act
    candidates = get_unanalyzed_linkedin_entries(db_session)

    # Assert: Only unanalyzed enters pipeline; analyzed is ignored
    candidate_ids = [c.id for c in candidates]
    assert entry_unanalyzed.id in candidate_ids
    assert entry_analyzed.id not in candidate_ids


def test_linkedin_entry_selection_maintains_separation_institucional_and_web(db_session: Session):
    """Verify strict separation: institutional and web entries are excluded from LinkedIn pipeline."""
    from scripts.analyze_linkedin_batch import get_unanalyzed_linkedin_entries

    li_source = Source(
        id=uuid.uuid4(),
        name="LinkedIn",
        type=SourceType.LINKEDIN,
        url="https://www.linkedin.com",
        active=True,
    )
    inst_source = Source(
        id=uuid.uuid4(),
        name="CNMC - Noticias",
        type=SourceType.RSS,
        category="authority",
        url="https://www.cnmc.es/rss",
        active=True,
    )
    web_source = Source(
        id=uuid.uuid4(),
        name="Antitrust Blog",
        type=SourceType.BLOG,
        category="general",
        url="https://antitrustblog.example.com/feed",
        active=True,
    )
    db_session.add_all([li_source, inst_source, web_source])
    db_session.flush()

    # 1. LinkedIn Entry
    li_entry = Entry(
        source_id=li_source.id,
        url=f"https://www.linkedin.com/posts/ec-competition-{uuid.uuid4()}",
        canonical_url=f"https://www.linkedin.com/posts/ec-competition-{uuid.uuid4()}",
        title="LinkedIn — European Commission — 2026-03-15",
        content="Commission sends statement of objections in digital markets probe.",
        author="European Commission",
        content_type="social_post",
        raw_metadata={
            "origin_source": "linkedin",
            "source_origin_category": "linkedin",
            "tracked_entity_name": "European Commission",
        },
    )

    # 2. Institutional Entry
    inst_entry = Entry(
        source_id=inst_source.id,
        url=f"https://www.cnmc.es/noticias/resolucion-{uuid.uuid4()}",
        canonical_url=f"https://www.cnmc.es/noticias/resolucion-{uuid.uuid4()}",
        title="CNMC sanciona cártel de transporte",
        content="La CNMC ha impuesto sanciones millonarias por prácticas anticompetitivas.",
        author="CNMC",
        raw_metadata={
            "source_origin_category": "institutional",
        },
    )

    # 3. Web Editorial Entry
    web_entry = Entry(
        source_id=web_source.id,
        url=f"https://antitrustblog.example.com/posts/{uuid.uuid4()}",
        canonical_url=f"https://antitrustblog.example.com/posts/{uuid.uuid4()}",
        title="Análisis del nuevo reglamento DMA",
        content="El reglamento de mercados digitales plantea retos sustantivos.",
        author="Legal Scholar",
        raw_metadata={},
    )
    db_session.add_all([li_entry, inst_entry, web_entry])
    db_session.commit()

    # Verify origin properties
    assert li_entry.source_origin_category == "linkedin"
    assert inst_entry.source_origin_category == "institutional"
    assert web_entry.source_origin_category == "expert_analysis"
    assert inst_entry.source_origin_category != "linkedin"
    assert web_entry.source_origin_category != "linkedin"

    # Act: Retrieve pipeline candidates for LinkedIn batch
    candidates = get_unanalyzed_linkedin_entries(db_session)
    candidate_ids = [c.id for c in candidates]

    # Assert: Exclusively LinkedIn is selected; institutional and web are excluded
    assert li_entry.id in candidate_ids
    assert inst_entry.id not in candidate_ids
    assert web_entry.id not in candidate_ids


@pytest.mark.asyncio
async def test_analyze_linkedin_batch_dry_run(db_session: Session):
    """Verify dry-run mode identifies candidates without calling LLMs or persisting EntryAnalysis."""
    from scripts.analyze_linkedin_batch import run_linkedin_analysis_batch

    matrix = setup_analysis_matrix_and_topics(db_session)
    source = Source(
        id=uuid.uuid4(),
        name="LinkedIn",
        type=SourceType.LINKEDIN,
        url="https://www.linkedin.com",
        active=True,
    )
    entry = Entry(
        source_id=source.id,
        url=f"https://www.linkedin.com/posts/dryrun-{uuid.uuid4()}",
        canonical_url=f"https://www.linkedin.com/posts/dryrun-{uuid.uuid4()}",
        title="LinkedIn — Hausfeld — 2026-03-16",
        content="Dry run verification post for antitrust damages.",
        author="Hausfeld",
        content_type="social_post",
        raw_metadata={
            "origin_source": "linkedin",
            "source_origin_category": "linkedin",
            "tracked_entity_name": "Hausfeld",
        },
    )
    db_session.add_all([source, entry])
    db_session.commit()

    report = await run_linkedin_analysis_batch(
        db=db_session,
        dry_run=True,
    )

    assert report["dry_run"] is True
    assert report["candidates_found"] >= 1
    assert report["analyzed_count"] == 0

    # Ensure zero EntryAnalysis records were created in DB
    analyses = db_session.query(EntryAnalysis).filter(EntryAnalysis.entry_id == entry.id).all()
    assert len(analyses) == 0


@pytest.mark.asyncio
async def test_analyze_linkedin_batch_execution_mock_provider_and_idempotency(db_session: Session):
    """Verify real pipeline execution with mock provider produces triage, deep, and is idempotent."""
    from scripts.analyze_linkedin_batch import run_linkedin_analysis_batch
    from app.providers.ai.mock import MockAIProvider

    matrix = setup_analysis_matrix_and_topics(db_session)
    source = Source(
        id=uuid.uuid4(),
        name="LinkedIn",
        type=SourceType.LINKEDIN,
        url="https://www.linkedin.com",
        active=True,
    )
    entry = Entry(
        source_id=source.id,
        url=f"https://www.linkedin.com/posts/mockexec-{uuid.uuid4()}",
        canonical_url=f"https://www.linkedin.com/posts/mockexec-{uuid.uuid4()}",
        title="LinkedIn — Hausfeld — 2026-03-16",
        content=(
            "Hausfeld acts as co-lead counsel in landmark collective antitrust damages proceedings "
            "regarding international truck cartel price-fixing infringements. The tribunal has issued "
            "substantive directions on disclosure and expert economic reports."
        ),
        author="Hausfeld",
        content_type="social_post",
        raw_metadata={
            "origin_source": "linkedin",
            "source_origin_category": "linkedin",
            "tracked_entity_name": "Hausfeld",
        },
    )
    db_session.add_all([source, entry])
    db_session.commit()

    mock_provider = MockAIProvider()

    # 1. Run 1: Real execution with mock provider
    report1 = await run_linkedin_analysis_batch(
        db=db_session,
        provider=mock_provider,
        entity_name="Hausfeld",
        limit=1,
        dry_run=False,
    )

    assert report1["analyzed_count"] == 1
    assert len(report1["entries"]) == 1

    entry_rep = report1["entries"][0]
    assert entry_rep["entry_id"] == str(entry.id)
    assert entry_rep["entity"] == "Hausfeld"
    assert entry_rep["author"] == "Hausfeld"
    assert entry_rep["triage"]["relevance"] in ("relevant", "uncertain", "not_relevant")
    assert entry_rep["triage"]["category"] != "N/A"
    assert entry_rep["deep_analysis"]["executed"] in ("sí", "no")
    assert entry_rep["deep_analysis"]["result"] != ""

    # Verify database persistence
    db_analysis = db_session.query(EntryAnalysis).filter(EntryAnalysis.entry_id == entry.id).first()
    assert db_analysis is not None
    assert db_analysis.status == "completed"

    # 2. Run 2: Idempotency check — the same entry is now ignored
    report2 = await run_linkedin_analysis_batch(
        db=db_session,
        provider=mock_provider,
        entity_name="Hausfeld",
        limit=1,
        dry_run=False,
    )
    assert report2["candidates_found"] == 0
    assert report2["analyzed_count"] == 0


def test_analyze_linkedin_batch_entity_filter(db_session: Session):
    """Verify --entity filter selects only matching entries."""
    from scripts.analyze_linkedin_batch import get_unanalyzed_linkedin_entries

    source = Source(
        id=uuid.uuid4(),
        name="LinkedIn",
        type=SourceType.LINKEDIN,
        url="https://www.linkedin.com",
        active=True,
    )
    e1 = Entry(
        source_id=source.id,
        url=f"https://www.linkedin.com/posts/hausfeld-{uuid.uuid4()}",
        canonical_url=f"https://www.linkedin.com/posts/hausfeld-{uuid.uuid4()}",
        title="LinkedIn — Hausfeld — 2026-03-16",
        content="Antitrust updates from Hausfeld.",
        author="Hausfeld",
        content_type="social_post",
        raw_metadata={"source_origin_category": "linkedin", "tracked_entity_name": "Hausfeld"},
    )
    e2 = Entry(
        source_id=source.id,
        url=f"https://www.linkedin.com/posts/cnmc-{uuid.uuid4()}",
        canonical_url=f"https://www.linkedin.com/posts/cnmc-{uuid.uuid4()}",
        title="LinkedIn — CNMC — 2026-03-16",
        content="Notas de prensa y comunicados de la CNMC.",
        author="CNMC",
        content_type="social_post",
        raw_metadata={"source_origin_category": "linkedin", "tracked_entity_name": "CNMC"},
    )
    db_session.add_all([source, e1, e2])
    db_session.commit()

    hausfeld_candidates = get_unanalyzed_linkedin_entries(db_session, entity_name="Hausfeld")
    assert len(hausfeld_candidates) == 1
    assert hausfeld_candidates[0].author == "Hausfeld"

    cnmc_candidates = get_unanalyzed_linkedin_entries(db_session, entity_name="CNMC")
    assert len(cnmc_candidates) == 1
    assert cnmc_candidates[0].author == "CNMC"




# ============================================================
# Section 13 — analyze_linkedin_batch: real Settings attribute
# ============================================================


def test_analyze_linkedin_batch_uses_real_settings_gemini_api_key(db_session: Session) -> None:
    """Verify that run_linkedin_analysis_batch reads cfg.GEMINI_API_KEY (uppercase),
    which is the actual attribute defined in Settings, not the non-existent
    lowercase alias 'gemini_api_key' that caused AttributeError in production."""
    from app.core.config import Settings
    from scripts.analyze_linkedin_batch import run_linkedin_analysis_batch
    import asyncio

    # Confirm the attribute exists on instances with its canonical uppercase name
    assert hasattr(Settings(), "GEMINI_API_KEY"), (
        "Settings must expose GEMINI_API_KEY as an uppercase attribute"
    )
    # Confirm the incorrectly-cased alias does NOT exist (documents the regression)
    assert not hasattr(Settings(), "gemini_api_key"), (
        "Settings must NOT have 'gemini_api_key' — access must use GEMINI_API_KEY"
    )

    # Seed matrix + prompts using the same helpers as Section 12
    matrix = setup_analysis_matrix_and_topics(db_session)

    # Seed: one LinkedIn source + entry (no completed analysis)
    source = Source(
        id=uuid.uuid4(),
        name="LinkedIn",
        type=SourceType.LINKEDIN,
        url="https://www.linkedin.com",
        active=True,
    )
    db_session.add(source)
    db_session.flush()

    entry = Entry(
        source_id=source.id,
        url=f"https://www.linkedin.com/posts/s13-{uuid.uuid4()}",
        canonical_url=f"https://www.linkedin.com/posts/s13-{uuid.uuid4()}",
        title="Test entry S13",
        content="Contenido suficiente para análisis de prueba de regresión.",
        author="Test Author S13",
        content_type="social_post",
        raw_metadata={"origin_source": "linkedin", "tracked_entity_name": "Test S13"},
    )
    db_session.add(entry)
    db_session.commit()

    # Build settings with empty GEMINI_API_KEY
    settings_no_key = Settings(GEMINI_API_KEY="")

    # The function is async; run via asyncio.run
    async def _run() -> None:
        await run_linkedin_analysis_batch(
            db=db_session,
            provider=None,           # forces the provider-init path
            entity_name=None,
            limit=1,
            dry_run=False,
            settings=settings_no_key,
        )

    # Must raise RuntimeError with the GEMINI_API_KEY message,
    # NOT AttributeError from a bad attribute access (regression guard)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY no está configurada"):
        asyncio.run(_run())




# ============================================================
# Section 14 — Concurrent Job Execution
# ============================================================


def _make_mock_post(entity_name: str, entity_url: str, idx: int = 1) -> LinkedInDiscoveredPost:
    """Helper: create a minimal valid LinkedInDiscoveredPost for a given entity."""
    import re
    slug = re.sub(r"[^a-zA-Z0-9]", "", entity_name).lower()
    unique_num = f"{abs(hash(entity_name)) % 10000:04d}{idx:04d}"
    activity_id = f"urn:li:activity:{unique_num}"
    post_url = f"https://www.linkedin.com/posts/{slug}_update-{unique_num}-activity-{unique_num}"
    return LinkedInDiscoveredPost(
        linkedin_post_url=post_url,
        provider_item_id=activity_id,
        text=f"Post {idx} from {entity_name} about competition law and enforcement.",
        author_name=entity_name,
        author_profile_url=entity_url,
        published_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        provider="brightdata",
        engagement={"likes": 10, "comments": 2, "shares": 1},
        raw_metadata={"http_status": 200},
    )


def _seed_entity_with_linkedin(
    db: Session,
    name: str,
    linkedin_url: str,
    matrix_id: uuid.UUID = None,
) -> TrackedEntity:
    """Seed a TrackedEntity with LinkedIn URL for use in concurrency tests."""
    entity = TrackedEntity(
        id=uuid.uuid4(),
        display_name=name,
        entity_type="organization",
        metadata_={"linkedin_url": linkedin_url, "linkedin_entity_type": "organization"},
    )
    db.add(entity)
    db.flush()
    return entity


def test_concurrent_jobs_all_succeed(db_session: Session) -> None:
    """3 entities run concurrently, all succeed → report counters reflect all 3."""
    from unittest.mock import MagicMock, patch
    from app.core.config import Settings

    matrix = TrackingMatrix(code="HITCH", name="HITCHINGS", status="active")
    db_session.add(matrix)
    db_session.flush()

    entities = [
        _seed_entity_with_linkedin(db_session, "EntityA", "https://www.linkedin.com/company/entity-a", matrix.id),
        _seed_entity_with_linkedin(db_session, "EntityB", "https://www.linkedin.com/company/entity-b", matrix.id),
        _seed_entity_with_linkedin(db_session, "EntityC", "https://www.linkedin.com/company/entity-c", matrix.id),
    ]
    db_session.commit()

    # Build mock discover_posts that returns one post per entity
    def mock_discover(target_url, client, limit, entity_name, entity_id):
        # Map URL to entity name
        for ent in entities:
            if ent.metadata_["linkedin_url"] == target_url:
                return [_make_mock_post(ent.display_name, target_url, idx=1)]
        return []

    mock_primary = MagicMock()
    mock_primary.provider_name = "brightdata"
    mock_primary.discover_posts = mock_discover

    mock_fallback = MagicMock()
    mock_fallback.provider_name = "apify"

    mock_planner = MagicMock()
    mock_planner.plan_jobs.return_value = [
        LinkedInDiscoveryJob(
            job_id=f"job-{ent.id}",
            tracked_entity_id=ent.id,
            entity_name=ent.display_name,
            linkedin_url=ent.metadata_["linkedin_url"],
            entity_type="organization",
            provider="brightdata",
            priority=1,
        )
        for ent in entities
    ]

    settings_override = Settings(
        LINKEDIN_DISCOVERY_ENABLED=False,
        LINKEDIN_MAX_CONCURRENT_JOBS=3,
        LINKEDIN_MAX_POSTS_PER_ENTITY=2,
        LINKEDIN_MAX_NEW_ENTRIES_PER_RUN=50,
    )

    service = LinkedInIngestionService(
        planner=mock_planner,
        primary_provider=mock_primary,
        fallback_provider=mock_fallback,
    )
    service.settings = settings_override

    report = service.execute_discovery(
        db=db_session,
        confirm_real_calls=True,
        allow_manual=True,
        max_concurrent=3,
    )

    assert report.entities_executed == 3
    assert report.posts_seen == 3
    assert report.entries_created == 3
    assert report.failed_jobs == 0
    assert report.timed_out_snapshots == 0
    assert report.execution_mode == "concurrent_3"
    assert len(report.per_entity) == 3


def test_concurrent_jobs_one_timeout_others_continue(db_session: Session) -> None:
    """Hausfeld: OK, CNMC: snapshot timeout, ESKARIAM: OK → 2 created, 1 timed_out."""
    from unittest.mock import MagicMock
    from app.core.config import Settings

    matrix = TrackingMatrix(code="HITCH2", name="HITCHINGS-2", status="active")
    db_session.add(matrix)
    db_session.flush()

    hausfeld = _seed_entity_with_linkedin(db_session, "Hausfeld", "https://www.linkedin.com/company/hausfeld", matrix.id)
    cnmc = _seed_entity_with_linkedin(db_session, "CNMC", "https://www.linkedin.com/company/cnmc", matrix.id)
    eskariam = _seed_entity_with_linkedin(db_session, "ESKARIAM", "https://www.linkedin.com/company/eskariam", matrix.id)
    db_session.commit()

    timeout_url = cnmc.metadata_["linkedin_url"]

    def mock_discover(target_url, client, limit, entity_name, entity_id):
        if target_url == timeout_url:
            raise LinkedInSnapshotTimeoutError("Snapshot timed out after 300s")
        for ent in [hausfeld, eskariam]:
            if ent.metadata_["linkedin_url"] == target_url:
                return [_make_mock_post(ent.display_name, target_url, idx=1)]
        return []

    mock_primary = MagicMock()
    mock_primary.provider_name = "brightdata"
    mock_primary.discover_posts = mock_discover

    mock_fallback = MagicMock()
    mock_fallback.provider_name = "apify"

    mock_planner = MagicMock()
    mock_planner.plan_jobs.return_value = [
        LinkedInDiscoveryJob(
            job_id=f"job-{ent.id}",
            tracked_entity_id=ent.id,
            entity_name=ent.display_name,
            linkedin_url=ent.metadata_["linkedin_url"],
            entity_type="organization",
            provider="brightdata",
            priority=1,
        )
        for ent in [hausfeld, cnmc, eskariam]
    ]

    settings_override = Settings(
        LINKEDIN_DISCOVERY_ENABLED=False,
        LINKEDIN_MAX_CONCURRENT_JOBS=3,
        LINKEDIN_MAX_POSTS_PER_ENTITY=2,
        LINKEDIN_MAX_NEW_ENTRIES_PER_RUN=50,
        APIFY_API_TOKEN="",  # No fallback
    )

    service = LinkedInIngestionService(
        planner=mock_planner,
        primary_provider=mock_primary,
        fallback_provider=mock_fallback,
    )
    service.settings = settings_override

    report = service.execute_discovery(
        db=db_session,
        confirm_real_calls=True,
        allow_manual=True,
        max_concurrent=3,
        disable_fallback=True,
    )

    assert report.entities_executed == 3, f"Expected 3, got {report.entities_executed}"
    assert report.entries_created == 2, f"Expected 2 entries, got {report.entries_created}"
    assert report.timed_out_snapshots == 1, f"Expected 1 timeout, got {report.timed_out_snapshots}"
    assert report.failed_jobs == 1
    assert report.execution_mode == "concurrent_3"
    # Timeout for CNMC must not cancel Hausfeld or ESKARIAM
    created_entities = {
        item["entity_name"] for item in report.items_detail if item.get("action") == "CREATED"
    }
    assert "Hausfeld" in created_entities
    assert "ESKARIAM" in created_entities
    assert "CNMC" not in created_entities


def test_concurrent_jobs_semaphore_limits_parallelism(db_session: Session) -> None:
    """With max_concurrent=1 and 2 jobs, runs are sequential even if called with concurrent mode."""
    from unittest.mock import MagicMock
    from app.core.config import Settings

    matrix = TrackingMatrix(code="HITCH3", name="HITCHINGS-3", status="active")
    db_session.add(matrix)
    db_session.flush()

    ent1 = _seed_entity_with_linkedin(db_session, "SequA", "https://www.linkedin.com/company/sequ-a", matrix.id)
    ent2 = _seed_entity_with_linkedin(db_session, "SequB", "https://www.linkedin.com/company/sequ-b", matrix.id)
    db_session.commit()

    call_log: list[str] = []

    def mock_discover(target_url, client, limit, entity_name, entity_id):
        call_log.append(entity_name)
        for ent in [ent1, ent2]:
            if ent.metadata_["linkedin_url"] == target_url:
                return [_make_mock_post(ent.display_name, target_url, idx=1)]
        return []

    mock_primary = MagicMock()
    mock_primary.provider_name = "brightdata"
    mock_primary.discover_posts = mock_discover

    mock_fallback = MagicMock()
    mock_fallback.provider_name = "apify"

    mock_planner = MagicMock()
    mock_planner.plan_jobs.return_value = [
        LinkedInDiscoveryJob(
            job_id=f"job-{ent.id}",
            tracked_entity_id=ent.id,
            entity_name=ent.display_name,
            linkedin_url=ent.metadata_["linkedin_url"],
            entity_type="organization",
            provider="brightdata",
            priority=1,
        )
        for ent in [ent1, ent2]
    ]

    settings_override = Settings(
        LINKEDIN_DISCOVERY_ENABLED=False,
        LINKEDIN_MAX_CONCURRENT_JOBS=1,
        LINKEDIN_MAX_POSTS_PER_ENTITY=2,
        LINKEDIN_MAX_NEW_ENTRIES_PER_RUN=50,
    )

    service = LinkedInIngestionService(
        planner=mock_planner,
        primary_provider=mock_primary,
        fallback_provider=mock_fallback,
    )
    service.settings = settings_override

    # max_concurrent=1 → use_concurrent = False → sequential path
    report = service.execute_discovery(
        db=db_session,
        confirm_real_calls=True,
        allow_manual=True,
        max_concurrent=1,
    )

    assert report.entities_executed == 2
    assert report.entries_created == 2
    assert report.execution_mode == "sequential"
    # Both entities should have been called
    assert set(call_log) == {"SequA", "SequB"}


def test_concurrent_report_counters_are_correct(db_session: Session) -> None:
    """Report counters are correctly merged from 3 concurrent jobs without double-counting."""
    from unittest.mock import MagicMock
    from app.core.config import Settings

    matrix = TrackingMatrix(code="HITCH4", name="HITCHINGS-4", status="active")
    db_session.add(matrix)
    db_session.flush()

    entities = [
        _seed_entity_with_linkedin(db_session, f"MergeEnt{i}", f"https://www.linkedin.com/company/merge-ent-{i}", matrix.id)
        for i in range(1, 4)
    ]
    db_session.commit()

    def mock_discover(target_url, client, limit, entity_name, entity_id):
        # Each entity returns 2 posts
        for idx, ent in enumerate(entities):
            if ent.metadata_["linkedin_url"] == target_url:
                return [
                    _make_mock_post(ent.display_name, target_url, idx=idx * 10 + 1),
                    _make_mock_post(ent.display_name, target_url, idx=idx * 10 + 2),
                ]
        return []

    mock_primary = MagicMock()
    mock_primary.provider_name = "brightdata"
    mock_primary.discover_posts = mock_discover

    mock_fallback = MagicMock()
    mock_fallback.provider_name = "apify"

    mock_planner = MagicMock()
    mock_planner.plan_jobs.return_value = [
        LinkedInDiscoveryJob(
            job_id=f"job-{ent.id}",
            tracked_entity_id=ent.id,
            entity_name=ent.display_name,
            linkedin_url=ent.metadata_["linkedin_url"],
            entity_type="organization",
            provider="brightdata",
            priority=1,
        )
        for ent in entities
    ]

    settings_override = Settings(
        LINKEDIN_DISCOVERY_ENABLED=False,
        LINKEDIN_MAX_CONCURRENT_JOBS=3,
        LINKEDIN_MAX_POSTS_PER_ENTITY=5,
        LINKEDIN_MAX_NEW_ENTRIES_PER_RUN=50,
    )

    service = LinkedInIngestionService(
        planner=mock_planner,
        primary_provider=mock_primary,
        fallback_provider=mock_fallback,
    )
    service.settings = settings_override

    report = service.execute_discovery(
        db=db_session,
        confirm_real_calls=True,
        allow_manual=True,
        max_concurrent=3,
    )

    # 3 entities × 2 posts each = 6 posts_seen, 6 entries_created (no duplicates in this test)
    assert report.posts_seen == 6, f"Expected 6 posts_seen, got {report.posts_seen}"
    assert report.entries_created == 6, f"Expected 6 entries_created, got {report.entries_created}"
    assert report.duplicates == 0
    assert report.failed_jobs == 0
    assert report.entities_executed == 3
    assert len(report.per_entity) == 3
    # Sum of per_entity created must equal report total
    total_created_per_entity = sum(pe["created"] for pe in report.per_entity)
    assert total_created_per_entity == report.entries_created, (
        f"Per-entity sum {total_created_per_entity} != report.entries_created {report.entries_created}"
    )
