"""Ingestion service for Google News discovery feed queries (Bloque 9A)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
from typing import Optional, Sequence
from urllib.parse import quote_plus

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.url_utils import (
    compute_discovery_fingerprint,
    extract_publisher_domain,
    normalize_title,
    normalize_url,
)
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.models.source import Source, SourceType
from app.providers.base import ProviderDisabledError
from app.providers.extractors.google_news import (
    GoogleNewsItem,
    parse_google_news_rss,
)
from app.services.google_news_query_planner import GoogleNewsQueryPlanner, PlannedQuery
from app.services.ingestion_service import compute_ingestion_dedupe_hash

logger = logging.getLogger(__name__)


def _make_bounded_external_id(guid: Optional[str], content_hash: str) -> str:
    """Ensure external_id is <= 255 characters (VARCHAR(255) column constraint)."""
    if not guid:
        return content_hash
    clean = guid.strip()
    if len(clean) <= 255:
        return clean
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


@dataclass
class GoogleNewsIngestionReport:
    """Detailed summary report of a Google News ingestion execution."""
    run_id: Optional[uuid.UUID]
    source_id: uuid.UUID
    status: str
    is_dry_run: bool
    queries_planned: int
    queries_executed: int
    items_seen: int
    entries_created: int
    duplicates_count: int
    failed_queries: int
    stopped_by_cap: bool
    publishers_found: list[str]
    sample_created_entries: list[dict]
    latest_published_at: Optional[datetime]
    oldest_published_at: Optional[datetime]


def _build_google_news_rss_url(query: PlannedQuery) -> str:
    """Construct public Google News RSS URL for a planned query."""
    encoded_q = quote_plus(query.query_text)
    if query.language == "es":
        hl = "es"
        gl = "ES"
        ceid = "ES:es"
    else:
        hl = "en-GB"
        gl = "GB"
        ceid = "GB:en"
    return f"https://news.google.com/rss/search?q={encoded_q}&hl={hl}&gl={gl}&ceid={ceid}"


class GoogleNewsIngestionService:
    """Coordinates deterministic Google News query execution, RSS parsing, deduplication, and Entry creation."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def get_or_create_google_news_source(self, db: Session) -> Source:
        """Retrieve existing Google News source or create it idempotently."""
        source = (
            db.query(Source)
            .filter(Source.type == SourceType.GOOGLE_NEWS)
            .first()
        )
        if not source:
            source = Source(
                name="Google News",
                type=SourceType.GOOGLE_NEWS,
                provider="native",
                category="news_aggregator",
                url="https://news.google.com",
                active=True,
                config={
                    "discovery": True,
                    "languages": ["es", "en"],
                    "region": "ES",
                    "freshness_warning_hours": 72,
                },
            )
            db.add(source)
            db.commit()
            db.refresh(source)
        return source

    def execute_ingestion(
        self,
        db: Session,
        planner: Optional[GoogleNewsQueryPlanner] = None,
        max_queries: Optional[int] = None,
        max_items_per_query: Optional[int] = None,
        max_new_entries: Optional[int] = None,
        confirm_real_calls: bool = False,
        client: Optional[httpx.Client] = None,
    ) -> GoogleNewsIngestionReport:
        """Run discovery ingestion.
        
        If confirm_real_calls is False, performs a dry-run preview (0 external calls, 0 DB writes).
        """
        source = self.get_or_create_google_news_source(db)

        # Resolve effective limits
        eff_max_queries = max_queries or self.settings.GOOGLE_NEWS_MAX_QUERIES_PER_RUN
        eff_max_items_per_query = max_items_per_query or self.settings.GOOGLE_NEWS_MAX_ITEMS_PER_QUERY
        eff_max_new_entries = max_new_entries or self.settings.GOOGLE_NEWS_MAX_NEW_ENTRIES_PER_RUN

        if planner is None:
            planner = GoogleNewsQueryPlanner(
                languages=self.settings.GOOGLE_NEWS_LANGUAGES,
                region=self.settings.GOOGLE_NEWS_REGION,
                max_queries=eff_max_queries,
            )

        planned_queries = planner.plan_queries(db)

        # DRY RUN branch: no HTTP requests, no DB writes
        if not confirm_real_calls:
            logger.info("Executing Google News discovery in DRY-RUN mode (confirm_real_calls=False)")
            return GoogleNewsIngestionReport(
                run_id=None,
                source_id=source.id,
                status="dry_run",
                is_dry_run=True,
                queries_planned=len(planned_queries),
                queries_executed=0,
                items_seen=0,
                entries_created=0,
                duplicates_count=0,
                failed_queries=0,
                stopped_by_cap=False,
                publishers_found=[],
                sample_created_entries=[],
                latest_published_at=None,
                oldest_published_at=None,
            )

        # Fail-closed check: require GOOGLE_NEWS_ENABLED=True in settings
        if not self.settings.GOOGLE_NEWS_ENABLED:
            raise ProviderDisabledError(
                "Google News discovery provider is disabled in settings. "
                "Set GOOGLE_NEWS_ENABLED=true in .env or environment to proceed."
            )

        # REAL RUN execution
        started_at = datetime.now(timezone.utc)
        run = IngestionRun(
            source_id=source.id,
            started_at=started_at,
            status=IngestionRunStatus.RUNNING.value,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        # Track metrics
        total_items_seen = 0
        total_created = 0
        total_duplicates = 0
        failed_queries = 0
        queries_executed = 0
        publishers_set: set[str] = set()
        pub_dates: list[datetime] = []
        sample_entries: list[dict] = []

        # Deduplication within the run
        seen_in_run_urls: set[str] = set()
        seen_in_run_guids: set[str] = set()

        # Pre-load official sources and their existing titles for generic cross-source deduplication
        official_sources = (
            db.query(Source)
            .filter(Source.type != SourceType.GOOGLE_NEWS, Source.active == True)
            .all()
        )
        official_source_titles: dict[str, set[str]] = {}
        for os_source in official_sources:
            src_domain = extract_publisher_domain(os_source.url)
            if not src_domain:
                continue
            e_titles = db.query(Entry.title).filter(Entry.source_id == os_source.id).all()
            titles_set = {normalize_title(t[0]) for t in e_titles if t[0]}
            official_source_titles[src_domain] = titles_set
            # Associate parent domains for known multi-level official endpoints
            if src_domain.endswith(".ec.europa.eu"):
                official_source_titles.setdefault("ec.europa.eu", set()).update(titles_set)
            elif src_domain.endswith(".curia.europa.eu"):
                official_source_titles.setdefault("curia.europa.eu", set()).update(titles_set)

        # Pre-load existing discovery entries from DB to populate historical deduplication sets
        existing_gn_entries = (
            db.query(Entry.title, Entry.raw_metadata)
            .filter(Entry.source_id == source.id)
            .all()
        )
        seen_fingerprints: set[str] = set()
        seen_domains_titles: set[tuple[str, str]] = set()
        for e_title, e_meta in existing_gn_entries:
            if not e_meta:
                continue
            fp = e_meta.get("discovery_fingerprint")
            if fp:
                seen_fingerprints.add(fp)
            p_dom = e_meta.get("publisher_domain")
            if p_dom and e_title:
                seen_domains_titles.add((p_dom, normalize_title(e_title)))

        close_client = False
        if client is None:
            client = httpx.Client(
                timeout=self.settings.GOOGLE_NEWS_TIMEOUT_SECONDS,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"},
            )
            close_client = True

        try:
            for q in planned_queries:
                if total_created >= eff_max_new_entries:
                    logger.info("Reached max_new_entries cap (%d). Stopping query iteration.", eff_max_new_entries)
                    break

                queries_executed += 1
                rss_url = _build_google_news_rss_url(q)

                # Error isolation per query: HTTP failure logs warning and continues
                try:
                    resp = client.get(rss_url)
                    if resp.status_code != 200:
                        logger.warning(
                            "Google News RSS returned HTTP %s for query '%s'",
                            resp.status_code,
                            q.query_text,
                        )
                        failed_queries += 1
                        continue
                    xml_content = resp.text
                except Exception as net_err:
                    logger.warning(
                        "Network error fetching Google News RSS for query '%s': %s",
                        q.query_text,
                        net_err,
                    )
                    failed_queries += 1
                    continue

                items = parse_google_news_rss(xml_content, language=q.language)
                items_to_process = items[:eff_max_items_per_query]

                for item in items_to_process:
                    if total_created >= eff_max_new_entries:
                        break

                    total_items_seen += 1
                    if item.publisher:
                        publishers_set.add(item.publisher)
                    if item.published_at:
                        pub_dates.append(item.published_at)

                    # 1. Normalization & Fingerprint computation
                    canon_url = item.canonical_url or normalize_url(item.google_news_url)
                    item_domain = item.publisher_domain
                    norm_title = normalize_title(item.title)
                    item_fp = compute_discovery_fingerprint(item_domain, item.title)

                    # 2. Intra-run & GUID deduplication
                    if canon_url in seen_in_run_urls or (item.guid and item.guid in seen_in_run_guids):
                        total_duplicates += 1
                        continue

                    # 3. Discovery fingerprint & (publisher_domain + normalized_title) deduplication
                    # (Prevents re-ingesting same article under different Google News URLs)
                    if item_fp in seen_fingerprints or (item_domain and (item_domain, norm_title) in seen_domains_titles):
                        total_duplicates += 1
                        continue

                    # 4. Generic Cross-Source Official Source Deduplication
                    # If publisher_domain matches an official Source (e.g. cnmc.es, catribunal.org.uk, curia.europa.eu, ec.europa.eu)
                    # and an Entry with identical normalized title already exists for that official source, mark as duplicate.
                    is_official_dup = False
                    if item_domain:
                        for off_dom, off_titles in official_source_titles.items():
                            if item_domain == off_dom or item_domain.endswith("." + off_dom) or off_dom.endswith("." + item_domain):
                                if norm_title in off_titles:
                                    is_official_dup = True
                                    break
                    if is_official_dup:
                        total_duplicates += 1
                        continue

                    # 5. Database URL & Identity hash deduplication
                    c_hash = compute_ingestion_dedupe_hash(item.title, item.google_news_url, item.excerpt)
                    ext_id = _make_bounded_external_id(item.guid, c_hash)

                    dup_conditions = [
                        Entry.canonical_url == canon_url,
                        Entry.url == canon_url,
                        Entry.url == item.google_news_url,
                        Entry.content_hash == c_hash,
                        Entry.external_id == ext_id,
                    ]

                    existing = db.execute(
                        select(Entry.id).where(or_(*dup_conditions)).limit(1)
                    ).scalar_one_or_none()

                    if existing:
                        total_duplicates += 1
                        continue

                    # 6. Passed deduplication: register in tracking sets
                    seen_in_run_urls.add(canon_url)
                    seen_in_run_urls.add(item.google_news_url)
                    if item.guid:
                        seen_in_run_guids.add(item.guid)
                    seen_fingerprints.add(item_fp)
                    if item_domain and norm_title:
                        seen_domains_titles.add((item_domain, norm_title))

                    # 7. Create and persist new discovery Entry (author is strictly None, publisher is NOT author)
                    raw_meta = {
                        "discovery_source": "Google News",
                        "publisher": item.publisher,
                        "publisher_url": item.publisher_url,
                        "publisher_domain": item_domain,
                        "discovery_fingerprint": item_fp,
                        "google_news_url": item.google_news_url,
                        "guid": item.guid,
                        "discovery_query": q.query_text,
                        "query_id": q.query_id,
                        "entity_id": str(q.entity_id) if q.entity_id else None,
                        "entity_name": q.entity_name,
                        "topic_codes": list(q.topic_codes),
                        "language": item.language,
                    }

                    new_entry = Entry(
                        source_id=source.id,
                        external_id=ext_id,
                        url=item.google_news_url,
                        canonical_url=canon_url,
                        title=item.title,
                        content=None,  # Discovery only: no full scraping in 9A
                        excerpt=item.excerpt,
                        author=None,  # Strictly None: author is NOT publisher
                        published_at=item.published_at,
                        captured_at=datetime.now(timezone.utc),
                        language=item.language,
                        content_type="news_article",
                        content_hash=c_hash,
                        raw_metadata=raw_meta,
                    )
                    db.add(new_entry)
                    db.flush()
                    total_created += 1

                    if len(sample_entries) < 10:
                        sample_entries.append({
                            "title": new_entry.title,
                            "publisher": item.publisher,
                            "published_at": item.published_at.isoformat() if item.published_at else None,
                            "url": canon_url,
                            "discovery_query": q.query_text,
                        })

            # Finalize IngestionRun
            finished_at = datetime.now(timezone.utc)
            stopped_by_cap = total_created >= eff_max_new_entries
            run.finished_at = finished_at
            run.fetched_count = total_items_seen
            run.created_count = total_created
            run.duplicate_count = total_duplicates
            run.failed_count = failed_queries
            run.latest_published_at = max(pub_dates, default=None)
            run.oldest_published_at = min(pub_dates, default=None)
            run.run_metadata = {
                "queries_planned": len(planned_queries),
                "queries_executed": queries_executed,
                "stopped_by_cap": stopped_by_cap,
                "max_new_entries_cap": eff_max_new_entries,
            }

            if failed_queries == 0:
                run.status = IngestionRunStatus.SUCCESS.value
                source.last_success_at = finished_at
            elif total_created > 0 or total_duplicates > 0:
                run.status = IngestionRunStatus.PARTIAL.value
                run.error_type = "PartialQueryExecutionError"
                run.error_message = f"{failed_queries} of {queries_executed} queries failed"
            else:
                run.status = IngestionRunStatus.FAILED.value
                run.error_type = "AllQueriesFailedError"
                run.error_message = f"All {failed_queries} queries failed"

            source.last_run_at = started_at
            db.commit()

            return GoogleNewsIngestionReport(
                run_id=run.id,
                source_id=source.id,
                status=run.status,
                is_dry_run=False,
                queries_planned=len(planned_queries),
                queries_executed=queries_executed,
                items_seen=total_items_seen,
                entries_created=total_created,
                duplicates_count=total_duplicates,
                failed_queries=failed_queries,
                stopped_by_cap=stopped_by_cap,
                publishers_found=sorted(list(publishers_set)),
                sample_created_entries=sample_entries,
                latest_published_at=run.latest_published_at,
                oldest_published_at=run.oldest_published_at,
            )

        except Exception as exc:
            db.rollback()
            run.finished_at = datetime.now(timezone.utc)
            run.status = IngestionRunStatus.FAILED.value
            run.error_type = type(exc).__name__
            run.error_message = str(exc)[:1000]
            db.commit()
            raise
        finally:
            if close_client and client:
                client.close()
