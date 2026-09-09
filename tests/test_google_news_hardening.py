"""Tests for Google News hardening: semantics, dedupe, and traceability (Bloque 9A.1)."""

import ast
from datetime import datetime, timezone
import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.url_utils import (
    compute_discovery_fingerprint,
    domains_belong_to_same_site,
    extract_publisher_domain,
    normalize_title,
    normalize_url,
)
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity, TrackingMatrix, TrackingTopic
from app.services.google_news_ingestion_service import GoogleNewsIngestionService
from app.services.google_news_query_planner import GoogleNewsQueryPlanner
from app.services.ingestion_service import compute_ingestion_dedupe_hash


def _setup_active_matrix(db: Session) -> None:
    """Seed active matrix and topics for tests."""
    matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    if not matrix:
        matrix = TrackingMatrix(
            code="TEST-MATRIX-GN",
            name="Test Matrix GN",
            status="active",
        )
        db.add(matrix)
        db.flush()
    topic = db.query(TrackingTopic).filter(TrackingTopic.code == "competition_law_general").first()
    if not topic:
        topic = TrackingTopic(
            matrix_id=matrix.id,
            code="competition_law_general",
            name="Derecho de la competencia",
            priority=100,
            active=True,
            discovery_queries=["competencia antitrust", "EU competition law developments"],
        )
        db.add(topic)
        db.commit()


# ---------------------------------------------------------------------------
# 1. Author vs Publisher
# ---------------------------------------------------------------------------
def test_author_is_strictly_none_for_google_news(db_session: Session, monkeypatch):
    """Verify that Entry.author is strictly None on Google News ingestion, and publisher is in raw_metadata."""
    _setup_active_matrix(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>Competition Law Enforcement Update - LawFirm XYZ</title>
          <link>https://news.google.com/rss/articles/CBMi_test1</link>
          <guid>CBMi_test1</guid>
          <pubDate>Wed, 09 Sep 2026 10:00:00 GMT</pubDate>
          <description>Summary excerpt of the article</description>
          <source url="https://www.lawfirmxyz.com">LawFirm XYZ</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    source = service.get_or_create_google_news_source(db_session)

    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml))
    )

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_items_per_query=5,
        max_new_entries=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report.entries_created == 1
    entry = db_session.query(Entry).filter(Entry.source_id == source.id).first()
    assert entry is not None
    assert entry.author is None  # Author is strictly None!
    assert entry.raw_metadata["publisher"] == "LawFirm XYZ"
    assert entry.raw_metadata["publisher_url"] == "https://www.lawfirmxyz.com"
    assert entry.raw_metadata["publisher_domain"] == "lawfirmxyz.com"
    assert entry.raw_metadata["discovery_source"] == "Google News"
    assert "discovery_fingerprint" in entry.raw_metadata


# ---------------------------------------------------------------------------
# 2. Publisher Domain Normalization
# ---------------------------------------------------------------------------
def test_publisher_domain_normalization():
    """Verify publisher domain extraction normalizes hosts and strips prefixes/ports/paths."""
    assert extract_publisher_domain("https://www.cnmc.es/prensa/noticias") == "cnmc.es"
    assert extract_publisher_domain("http://WWW.FT.COM:8080/world") == "ft.com"
    assert extract_publisher_domain("https://legalblogs.wolterskluwer.com/antitrust") == "legalblogs.wolterskluwer.com"
    assert extract_publisher_domain("ga-p.com") == "ga-p.com"
    assert extract_publisher_domain("Macfarlanes") == ""  # Bare name is not a domain
    assert extract_publisher_domain("LawFirm XYZ") == ""
    assert extract_publisher_domain("") == ""
    assert extract_publisher_domain(None) == ""


# ---------------------------------------------------------------------------
# 3. Discovery Fingerprint Determinism
# ---------------------------------------------------------------------------
def test_discovery_fingerprint_deterministic():
    """Verify that compute_discovery_fingerprint produces deterministic SHA256 hex digests."""
    t1 = "El Tribunal Supremo aclara: ¿qué plazo de prescripción aplica?"
    t2 = "El Tribunal Supremo aclara qué plazo de prescripción aplica"

    fp1 = compute_discovery_fingerprint("https://www.cnmc.es/prensa", t1)
    fp2 = compute_discovery_fingerprint("cnmc.es", t2)
    assert fp1 == fp2  # Identical despite punctuation / scheme difference

    # Different publisher with same title -> different fingerprint
    fp_reuters = compute_discovery_fingerprint("reuters.com", "Tech Giant Antitrust Probe")
    fp_ft = compute_discovery_fingerprint("ft.com", "Tech Giant Antitrust Probe")
    assert fp_reuters != fp_ft

    # Same publisher with materially different titles -> different fingerprint
    fp_a = compute_discovery_fingerprint("ft.com", "Headline Alpha")
    fp_b = compute_discovery_fingerprint("ft.com", "Headline Beta")
    assert fp_a != fp_b


# ---------------------------------------------------------------------------
# 4. Dedupe Weakness Demonstration
# ---------------------------------------------------------------------------
def test_dedupe_weakness_demonstration_and_resolution(db_session: Session, monkeypatch):
    """Demonstrate that content_hash failed cross-source and discovery fingerprint resolves it."""
    _setup_active_matrix(db_session)
    title = "La CNMC sanciona el cártel de conservación de carreteras"
    official_url = "https://www.cnmc.es/prensa/resolucion-carreteras-2026"
    gn_url = "https://news.google.com/rss/articles/CBMi_carreteras_gn_redirect"

    # 1. Official entry identity hash vs Google News identity hash
    hash_official = compute_ingestion_dedupe_hash(title, official_url, "Excerpt text")
    hash_gn = compute_ingestion_dedupe_hash(title, gn_url, "Excerpt text")
    assert hash_official != hash_gn  # Identity hash alone FAILS cross-source!

    # 2. Discovery fingerprint succeeds
    fp_official = compute_discovery_fingerprint("cnmc.es", title)
    fp_gn = compute_discovery_fingerprint("https://www.cnmc.es", title)
    assert fp_official == fp_gn  # Discovery fingerprint SUCCEEDS!

    # 3. Test in ingestion service
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    # Create official CNMC source and Entry
    cnmc_source = Source(
        name="CNMC - Noticias",
        type=SourceType.WEBSITE,
        provider="cnmc",
        category="official_bulletin",
        url="https://www.cnmc.es/prensa/noticias",
        active=True,
    )
    db_session.add(cnmc_source)
    db_session.flush()

    official_entry = Entry(
        source_id=cnmc_source.id,
        external_id="cnmc_123",
        url=official_url,
        canonical_url=official_url,
        title=title,
        content="Texto completo de la resolucion...",
        excerpt="Excerpt text",
        content_hash=hash_official,
        captured_at=datetime.now(timezone.utc),
    )
    db_session.add(official_entry)
    db_session.commit()

    # Ingest Google News discovery feed containing this same article
    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>{title}</title>
          <link>{gn_url}</link>
          <guid>CBMi_guid_cnmc</guid>
          <pubDate>Wed, 09 Sep 2026 12:00:00 GMT</pubDate>
          <description>Excerpt text</description>
          <source url="https://www.cnmc.es">CNMC</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml))
    )

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_items_per_query=5,
        max_new_entries=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    # Correctly detected as duplicate!
    assert report.entries_created == 0
    assert report.duplicates_count == 1


# ---------------------------------------------------------------------------
# 5. Over-Deduplication Protections
# ---------------------------------------------------------------------------
def test_different_publishers_same_title_not_duplicate(db_session: Session, monkeypatch):
    """Verify that different publishers with identical titles do NOT collide (no over-deduplication)."""
    _setup_active_matrix(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>Antitrust regulators launch investigation into cloud computing</title>
          <link>https://news.google.com/rss/articles/reuters_art1</link>
          <guid>reuters_guid_1</guid>
          <source url="https://www.reuters.com">Reuters</source>
        </item>
        <item>
          <title>Antitrust regulators launch investigation into cloud computing</title>
          <link>https://news.google.com/rss/articles/ft_art1</link>
          <guid>ft_guid_1</guid>
          <source url="https://www.ft.com">Financial Times</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml))
    )

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_items_per_query=5,
        max_new_entries=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    # Both must be created because publishers are different (reuters.com vs ft.com)
    assert report.entries_created == 2
    assert report.duplicates_count == 0


def test_same_publisher_same_normalized_title_duplicate(db_session: Session, monkeypatch):
    """Verify same publisher with punctuation/whitespace variation is detected as duplicate."""
    _setup_active_matrix(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>Supreme Court clarifies limitation period for antitrust damages</title>
          <link>https://news.google.com/rss/articles/garrigues_1</link>
          <guid>garrigues_guid_1</guid>
          <source url="https://www.garrigues.com">Garrigues</source>
        </item>
        <item>
          <title>Supreme Court clarifies limitation period: for antitrust damages!</title>
          <link>https://news.google.com/rss/articles/garrigues_2</link>
          <guid>garrigues_guid_2</guid>
          <source url="https://www.garrigues.com">Garrigues</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml))
    )

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_items_per_query=5,
        max_new_entries=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report.entries_created == 1
    assert report.duplicates_count == 1


def test_same_publisher_materially_different_title_not_duplicate(db_session: Session, monkeypatch):
    """Verify same publisher with different titles creates both entries."""
    _setup_active_matrix(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>Article One on Cartels</title>
          <link>https://news.google.com/rss/articles/mac_1</link>
          <guid>mac_guid_1</guid>
          <source url="https://www.macfarlanes.com">Macfarlanes</source>
        </item>
        <item>
          <title>Article Two on Merger Control</title>
          <link>https://news.google.com/rss/articles/mac_2</link>
          <guid>mac_guid_2</guid>
          <source url="https://www.macfarlanes.com">Macfarlanes</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml))
    )

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_items_per_query=5,
        max_new_entries=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report.entries_created == 2
    assert report.duplicates_count == 0


def test_same_guid_repeated_duplicate(db_session: Session, monkeypatch):
    """Verify same GUID repeated within feed is detected as duplicate."""
    _setup_active_matrix(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>Unique Title Alpha</title>
          <link>https://news.google.com/rss/articles/guid_test_1</link>
          <guid>reused_guid_123</guid>
          <source url="https://www.example.com">Example</source>
        </item>
        <item>
          <title>Unique Title Beta</title>
          <link>https://news.google.com/rss/articles/guid_test_2</link>
          <guid>reused_guid_123</guid>
          <source url="https://www.example.com">Example</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml))
    )

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_items_per_query=5,
        max_new_entries=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report.entries_created == 1
    assert report.duplicates_count == 1


# ---------------------------------------------------------------------------
# 6. Planner Hardcode Audit (AST Inspection & Dynamic Entity Test)
# ---------------------------------------------------------------------------
def test_planner_source_code_has_no_hardcoded_entity_names():
    """Verify via AST inspection that GoogleNewsQueryPlanner contains zero hardcoded entity names."""
    import inspect
    import app.services.google_news_query_planner as planner_module

    source_code = inspect.getsource(planner_module.GoogleNewsQueryPlanner)
    tree = ast.parse(source_code)

    banned_entity_names = {
        "CNMC",
        "Comisión Nacional de los Mercados y la Competencia",
        "European Commission",
        "Comisión Europea",
        "Competition Appeal Tribunal",
        "CAT",
        "Tribunal de Justicia de la Unión Europea",
        "TJUE",
        "CURIA",
        "Court of Justice of the European Union",
        "Hausfeld",
        "ESKARIAM",
        "ALI",
        "Redi Abogados",
    }

    found_constants: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for banned in banned_entity_names:
                if banned in node.value:
                    found_constants.add(banned)

    assert not found_constants, f"Hardcoded entity names found in GoogleNewsQueryPlanner: {found_constants}"


def test_planner_generates_queries_for_arbitrary_new_entity(db_session: Session):
    """Verify planner dynamically generates queries for any new TrackedEntity without code changes."""
    _setup_active_matrix(db_session)

    # Add completely novel entities
    ent_inst = TrackedEntity(
        display_name="Autoridad Vasca de la Competencia",
        entity_type="institution",
        active=True,
    )
    ent_org = TrackedEntity(
        display_name="Litigation Capital Partners",
        entity_type="organization",
        active=True,
    )
    db_session.add_all([ent_inst, ent_org])
    db_session.commit()

    planner = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=50)
    queries = planner.plan_queries(db_session)

    query_texts = [q.query_text for q in queries]
    assert '"Autoridad Vasca de la Competencia" competencia' in query_texts
    assert '"Autoridad Vasca de la Competencia" competition' in query_texts
    assert '"Litigation Capital Partners" competencia' in query_texts
    assert '"Litigation Capital Partners" competition' in query_texts


def test_topic_queries_derived_from_matrix_topics(db_session: Session):
    """Verify topic queries are derived from active TrackingTopic.discovery_queries."""
    matrix = TrackingMatrix(code="MAT-TOPIC", name="Matrix Topic", status="active")
    db_session.add(matrix)
    db_session.flush()

    topic = TrackingTopic(
        matrix_id=matrix.id,
        code="merger_control",
        name="Control de concentraciones",
        priority=80,
        active=True,
        discovery_queries=["EU merger clearance decision", "notificación concentraciones"],
    )
    db_session.add(topic)
    db_session.commit()

    planner = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=20)
    queries = planner.plan_queries(db_session)
    texts = [q.query_text for q in queries]

    assert "EU merger clearance decision" in texts
    assert "notificación concentraciones" in texts


# ---------------------------------------------------------------------------
# 7. IngestionRun Metrics & Stopped by Cap
# ---------------------------------------------------------------------------
def test_ingestion_run_tracks_query_metrics_and_stopped_by_cap(db_session: Session, monkeypatch):
    """Verify IngestionRun.run_metadata accurately records queries_planned, queries_executed, and stopped_by_cap."""
    _setup_active_matrix(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>Article One</title>
          <link>https://news.google.com/rss/articles/cap_1</link>
          <guid>cap_1</guid>
          <source url="https://www.example.com">Example</source>
        </item>
        <item>
          <title>Article Two</title>
          <link>https://news.google.com/rss/articles/cap_2</link>
          <guid>cap_2</guid>
          <source url="https://www.example.com">Example</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml))
    )

    # Set max_new_entries to 1 to force stopped_by_cap=True
    report = service.execute_ingestion(
        db=db_session,
        max_queries=5,
        max_items_per_query=5,
        max_new_entries=1,
        confirm_real_calls=True,
        client=mock_client,
    )

    assert report.entries_created == 1
    assert report.stopped_by_cap is True
    assert report.queries_executed >= 1

    from app.models.ingestion_run import IngestionRun
    run = db_session.get(IngestionRun, report.run_id)
    assert run is not None
    assert run.run_metadata["stopped_by_cap"] is True
    assert run.run_metadata["max_new_entries_cap"] == 1
    assert run.run_metadata["queries_planned"] == report.queries_planned
    assert run.run_metadata["queries_executed"] == report.queries_executed


# ---------------------------------------------------------------------------
# 8. Bloque 9A.2 — Domain Matching & Subdomain/Parent Hierarchy Tests
# ---------------------------------------------------------------------------
def test_domains_belong_to_same_site_rules():
    """Verify domains_belong_to_same_site correctly matches safe hierarchies and rejects spoofing."""
    # Valid hierarchy matches
    assert domains_belong_to_same_site("competition-policy.ec.europa.eu", "ec.europa.eu") is True
    assert domains_belong_to_same_site("ec.europa.eu", "competition-policy.ec.europa.eu") is True
    assert domains_belong_to_same_site("infocuria.curia.europa.eu", "curia.europa.eu") is True
    assert domains_belong_to_same_site("curia.europa.eu", "infocuria.curia.europa.eu") is True
    assert domains_belong_to_same_site("news.example.com", "example.com") is True
    assert domains_belong_to_same_site("example.com", "news.example.com") is True
    assert domains_belong_to_same_site("www.cnmc.es", "cnmc.es") is True
    assert domains_belong_to_same_site("www.catribunal.org.uk", "catribunal.org.uk") is True

    # Malicious / false matches (must NOT match)
    assert domains_belong_to_same_site("example.com", "example.com.evil.test") is False
    assert domains_belong_to_same_site("ec.europa.eu", "fake-ec.europa.eu") is False
    assert domains_belong_to_same_site("fake-ec.europa.eu", "ec.europa.eu") is False
    assert domains_belong_to_same_site("curia.europa.eu", "curia.europa.eu.example.org") is False
    assert domains_belong_to_same_site("cnmc.es", "evil-cnmc.es") is False
    assert domains_belong_to_same_site("Macfarlanes", "macfarlanes.com") is False
    assert domains_belong_to_same_site("", "cnmc.es") is False
    assert domains_belong_to_same_site(None, "cnmc.es") is False


def test_cross_source_dedupe_ec_parent_subdomain(db_session: Session, monkeypatch):
    """Verify Google News item with publisher ec.europa.eu matches Source competition-policy.ec.europa.eu."""
    _setup_active_matrix(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    title = "Commission adopts EU guidelines on exclusionary abuses of dominance"
    official_url = "https://competition-policy.ec.europa.eu/about/news/commission-adopts-eu-guidelines-exclusionary-abuses-dominance-2026-09-03_en"
    gn_url = "https://news.google.com/rss/articles/CBMi_ec_dup"

    ec_source = Source(
        name="European Commission - Competition Policy",
        type=SourceType.RSS,
        provider="european_commission",
        category="official_bulletin",
        url="https://competition-policy.ec.europa.eu/node/38/rss_en",
        active=True,
    )
    db_session.add(ec_source)
    db_session.flush()

    official_entry = Entry(
        source_id=ec_source.id,
        external_id="ec_official_1",
        url=official_url,
        canonical_url=official_url,
        title=title,
        content="Commission adopts new guidelines...",
        excerpt="Exclusionary abuses guidelines excerpt",
        content_hash=compute_ingestion_dedupe_hash(title, official_url, "excerpt"),
        captured_at=datetime.now(timezone.utc),
    )
    db_session.add(official_entry)
    db_session.commit()

    # Google News item has publisher_url pointing to ec.europa.eu
    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>{title}</title>
          <link>{gn_url}</link>
          <guid>CBMi_guid_ec_test</guid>
          <pubDate>Wed, 09 Sep 2026 14:00:00 GMT</pubDate>
          <source url="https://ec.europa.eu">European Commission</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml))
    )

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_items_per_query=5,
        max_new_entries=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    # Must be detected as duplicate!
    assert report.entries_created == 0
    assert report.duplicates_count == 1


def test_cross_source_dedupe_curia_parent_subdomain(db_session: Session, monkeypatch):
    """Verify Google News item with publisher curia.europa.eu matches Source infocuria.curia.europa.eu."""
    _setup_active_matrix(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    title = "Judgment of the Court of Justice in Case C-123/24 Competition Appeal"
    official_url = "https://infocuria.curia.europa.eu/tabs/jurisprudence?lang=en&ecli=ECLI:EU:C:2026:999"
    gn_url = "https://news.google.com/rss/articles/CBMi_curia_dup"

    curia_source = Source(
        name="Court of Justice of the European Union - Case Law",
        type=SourceType.WEBSITE,
        provider="curia",
        category="case_law",
        url="https://infocuria.curia.europa.eu/tabs/jurisprudence",
        active=True,
    )
    db_session.add(curia_source)
    db_session.flush()

    official_entry = Entry(
        source_id=curia_source.id,
        external_id="curia_official_1",
        url=official_url,
        canonical_url=official_url,
        title=title,
        content="Judgment text...",
        excerpt="Judgment excerpt",
        content_hash=compute_ingestion_dedupe_hash(title, official_url, "excerpt"),
        captured_at=datetime.now(timezone.utc),
    )
    db_session.add(official_entry)
    db_session.commit()

    # Google News item has publisher_url pointing to curia.europa.eu
    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>{title}</title>
          <link>{gn_url}</link>
          <guid>CBMi_guid_curia_test</guid>
          <pubDate>Wed, 09 Sep 2026 15:00:00 GMT</pubDate>
          <source url="https://curia.europa.eu">Court of Justice</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml))
    )

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_items_per_query=5,
        max_new_entries=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    # Must be detected as duplicate!
    assert report.entries_created == 0
    assert report.duplicates_count == 1


def test_cross_source_dedupe_subdomain_inverso(db_session: Session, monkeypatch):
    """Verify Google News item with subdomain publisher matches apex Source (news.example.com -> example.com)."""
    _setup_active_matrix(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    title = "Official Antitrust Regulatory Guidance Published"
    official_url = "https://example.com/antitrust-guidance-2026"
    gn_url = "https://news.google.com/rss/articles/CBMi_apex_sub_dup"

    example_source = Source(
        name="Example Regulator",
        type=SourceType.WEBSITE,
        provider="custom",
        category="official_bulletin",
        url="https://example.com",
        active=True,
    )
    db_session.add(example_source)
    db_session.flush()

    official_entry = Entry(
        source_id=example_source.id,
        external_id="example_1",
        url=official_url,
        canonical_url=official_url,
        title=title,
        content="Guidance text...",
        excerpt="Excerpt text",
        content_hash=compute_ingestion_dedupe_hash(title, official_url, "excerpt"),
        captured_at=datetime.now(timezone.utc),
    )
    db_session.add(official_entry)
    db_session.commit()

    # Google News item publisher is news.example.com (subdomain of example.com)
    rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>{title}</title>
          <link>{gn_url}</link>
          <guid>CBMi_guid_subdomain_inverso</guid>
          <pubDate>Wed, 09 Sep 2026 15:30:00 GMT</pubDate>
          <source url="https://news.example.com">Example News</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml))
    )

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_items_per_query=5,
        max_new_entries=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    # Must be detected as duplicate!
    assert report.entries_created == 0
    assert report.duplicates_count == 1


def test_cross_source_dedupe_protects_against_spoofed_or_malicious_domains(db_session: Session, monkeypatch):
    """Verify that fake or spoofed domains (e.g. fake-ec.europa.eu or example.com.evil.test) do NOT falsely collide."""
    _setup_active_matrix(db_session)
    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_NEWS_ENABLED", True)

    title = "Crucial Ruling on Cartel Damages"
    official_url = "https://ec.europa.eu/commission/presscorner/detail/en/ip_26_1000"

    ec_source = Source(
        name="European Commission Press",
        type=SourceType.WEBSITE,
        provider="custom",
        category="official_bulletin",
        url="https://ec.europa.eu",
        active=True,
    )
    db_session.add(ec_source)
    db_session.flush()

    official_entry = Entry(
        source_id=ec_source.id,
        external_id="ec_official_genuine",
        url=official_url,
        canonical_url=official_url,
        title=title,
        content="Press release text...",
        excerpt="Excerpt text",
        content_hash=compute_ingestion_dedupe_hash(title, official_url, "excerpt"),
        captured_at=datetime.now(timezone.utc),
    )
    db_session.add(official_entry)
    db_session.commit()

    # 1. Fake EC domain: fake-ec.europa.eu (must NOT match ec.europa.eu)
    rss_xml_fake = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Google News</title>
        <item>
          <title>{title}</title>
          <link>https://news.google.com/rss/articles/fake_ec_article</link>
          <guid>guid_fake_ec_1</guid>
          <source url="https://fake-ec.europa.eu">Fake EC</source>
        </item>
      </channel>
    </rss>"""

    service = GoogleNewsIngestionService()
    mock_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=rss_xml_fake))
    )

    report = service.execute_ingestion(
        db=db_session,
        max_queries=1,
        max_items_per_query=5,
        max_new_entries=5,
        confirm_real_calls=True,
        client=mock_client,
    )

    # Must NOT collide with official source! Created = 1
    assert report.entries_created == 1
    assert report.duplicates_count == 0


def test_discovery_fingerprint_fallback_on_bare_publisher_name():
    """Verify that bare publisher names without a domain trigger pub: fallback without fake hostname."""
    # When publisher_url is missing / bare string, publisher_domain is empty
    fp_bare1 = compute_discovery_fingerprint("", "Antitrust Litigation Overview", publisher_name="Macfarlanes")
    fp_bare2 = compute_discovery_fingerprint(None, "Antitrust Litigation Overview", publisher_name="Macfarlanes")
    assert fp_bare1 == fp_bare2

    # Different bare publisher name with same title produces different fingerprint
    fp_other = compute_discovery_fingerprint(None, "Antitrust Litigation Overview", publisher_name="Clifford Chance")
    assert fp_bare1 != fp_other

    # Domain takes precedence over publisher_name
    fp_domain = compute_discovery_fingerprint("macfarlanes.com", "Antitrust Litigation Overview", publisher_name="Macfarlanes")
    assert fp_domain != fp_bare1
