"""Service orchestrating direct reference web source ingestion runs (Bloque 9B)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.url_utils import (
    domains_belong_to_same_site,
    extract_publisher_domain,
    normalize_title,
    normalize_url,
)
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity
from app.providers.direct_web.base import DirectWebExtractionError
from app.providers.direct_web.registry import DirectWebAdapterRegistry
from app.services.ingestion_service import compute_ingestion_dedupe_hash
from app.services.source_sufficiency_service import (
    SourceSufficiencyLevel,
    SourceSufficiencyService,
)

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class DirectSourceRunResult(BaseModel):
    """Execution summary for a single direct web source ingestion run."""

    source_id: str
    source_name: str
    adapter_code: str
    items_discovered: int = 0
    items_fetched: int = 0
    entries_created: int = 0
    duplicates_count: int = 0
    failed_count: int = 0
    google_news_matches_count: int = 0
    oldest_published_at: Optional[datetime] = None
    latest_published_at: Optional[datetime] = None
    errors: list[str] = Field(default_factory=list)
    sufficiency_counts: dict[str, int] = Field(
        default_factory=lambda: {
            SourceSufficiencyLevel.FULL.value: 0,
            SourceSufficiencyLevel.PARTIAL.value: 0,
            SourceSufficiencyLevel.INSUFFICIENT.value: 0,
        }
    )


class DirectWebIngestionReport(BaseModel):
    """Consolidated report across all ingested direct web sources."""

    is_dry_run: bool = True
    sources_processed: int = 0
    total_discovered: int = 0
    total_fetched: int = 0
    total_created: int = 0
    total_duplicates: int = 0
    total_failed: int = 0
    total_google_news_matches: int = 0
    results_by_source: list[DirectSourceRunResult] = Field(default_factory=list)
    total_sufficiency: dict[str, int] = Field(
        default_factory=lambda: {
            SourceSufficiencyLevel.FULL.value: 0,
            SourceSufficiencyLevel.PARTIAL.value: 0,
            SourceSufficiencyLevel.INSUFFICIENT.value: 0,
        }
    )


class DirectWebIngestionService:
    """Coordinates discovery, detail extraction, deduplication, GN traceability, and metrics for Direct Web Sources."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    def execute_ingestion(
        self,
        db: Session,
        sources: Optional[list[Source]] = None,
        max_items_per_source: Optional[int] = None,
        confirm_real_calls: bool = False,
        client: Optional[httpx.Client] = None,
    ) -> DirectWebIngestionReport:
        """Execute ingestion for direct web sources with fail-closed safety and failure isolation."""
        settings = self.settings
        is_dry_run = not (settings.DIRECT_WEB_INGESTION_ENABLED and confirm_real_calls)

        report = DirectWebIngestionReport(is_dry_run=is_dry_run)

        # 1. Resolve candidate direct web sources
        if sources is None:
            # Query active sources of type BLOG or WEBSITE that have an adapter configured
            candidate_sources = (
                db.query(Source)
                .filter(Source.active == True)
                .filter(Source.type.in_([SourceType.BLOG, SourceType.WEBSITE]))
                .all()
            )
            # Filter to those that can be resolved by DirectWebAdapterRegistry
            active_sources = []
            for s in candidate_sources:
                try:
                    DirectWebAdapterRegistry.get_adapter_for_source(s)
                    active_sources.append(s)
                except Exception:
                    continue
        else:
            active_sources = sources

        if not active_sources:
            logger.info("DirectWebIngestion: No candidate sources found to process.")
            return report

        effective_limit = max_items_per_source or settings.DIRECT_WEB_MAX_ITEMS_PER_SOURCE

        # Pre-load Google News entries for cross-source matching (Sections 23, 24)
        gn_source = db.query(Source).filter(Source.type == SourceType.GOOGLE_NEWS).first()
        existing_gn_entries: list[Entry] = []
        if gn_source:
            existing_gn_entries = db.query(Entry).filter(Entry.source_id == gn_source.id).all()

        source_ids = [s.id for s in active_sources]
        for s_id in source_ids:
            source = db.get(Source, s_id)
            if not source:
                continue

            source_result = self._process_single_source(
                db=db,
                source=source,
                limit=effective_limit,
                is_dry_run=is_dry_run,
                existing_gn_entries=existing_gn_entries,
                client=client,
            )
            report.results_by_source.append(source_result)
            report.sources_processed += 1
            report.total_discovered += source_result.items_discovered
            report.total_fetched += source_result.items_fetched
            report.total_created += source_result.entries_created
            report.total_duplicates += source_result.duplicates_count
            report.total_failed += source_result.failed_count
            report.total_google_news_matches += source_result.google_news_matches_count

            for level, count in source_result.sufficiency_counts.items():
                report.total_sufficiency[level] = report.total_sufficiency.get(level, 0) + count

        return report

    def _process_single_source(
        self,
        db: Session,
        source: Source,
        limit: int,
        is_dry_run: bool,
        existing_gn_entries: list[Entry],
        client: Optional[httpx.Client] = None,
    ) -> DirectSourceRunResult:
        """Process a single direct source with complete failure isolation and IngestionRun tracking."""
        source_id = source.id
        source_name = source.name

        try:
            adapter = DirectWebAdapterRegistry.get_adapter_for_source(source)
            adapter_code = adapter.adapter_code
        except Exception as exc:
            logger.error(f"Cannot resolve adapter for source {source_id}: {exc}")
            return DirectSourceRunResult(
                source_id=str(source_id),
                source_name=source_name,
                adapter_code="unknown",
                failed_count=1,
                errors=[f"Adapter resolution failed: {exc}"],
            )

        result = DirectSourceRunResult(
            source_id=str(source_id),
            source_name=source_name,
            adapter_code=adapter_code,
        )

        # In DRY RUN: 0 network, 0 DB writes (Section 29)
        if is_dry_run:
            logger.info(f"[DRY RUN] Would ingest Source '{source_name}' with adapter '{adapter_code}' (limit={limit})")
            return result

        # Real run: Create IngestionRun
        started_at = utc_now()
        run = IngestionRun(
            source_id=source_id,
            started_at=started_at,
            status=IngestionRunStatus.RUNNING.value,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        # Pre-load existing entries for this source to deduplicate fast
        existing_entries = db.query(Entry.url, Entry.canonical_url, Entry.content_hash).filter(
            Entry.source_id == source.id
        ).all()
        existing_urls = {normalize_url(e[0]) for e in existing_entries if e[0]}
        existing_canonicals = {normalize_url(e[1]) for e in existing_entries if e[1]}
        existing_hashes = {e[2] for e in existing_entries if e[2]}

        source_domain = extract_publisher_domain(source.url)

        try:
            with db.begin_nested():
                # 1. Discover items
                discovered_items = adapter.discover(source, limit=limit, client=client)
                result.items_discovered = len(discovered_items)

                seen_in_run_urls: set[str] = set()

                for item in discovered_items:
                    norm_item_url = normalize_url(item.url)
                    if not norm_item_url or norm_item_url in seen_in_run_urls:
                        result.duplicates_count += 1
                        continue
                    seen_in_run_urls.add(norm_item_url)

                    if norm_item_url in existing_urls:
                        result.duplicates_count += 1
                        continue

                    # Filter by lookback_days if configured on source
                    lookback_days = (source.config or {}).get("lookback_days")
                    if lookback_days and item.published_at:
                        cutoff_dt = utc_now() - timedelta(days=lookback_days)
                        if item.published_at < cutoff_dt:
                            continue

                    # 2. Fetch detail (Failure isolation Section 27)
                    try:
                        raw_html = adapter.fetch_detail(item, client=client)
                        result.items_fetched += 1
                    except Exception as exc:
                        logger.warning(f"Error fetching detail for {item.url}: {exc}")
                        result.failed_count += 1
                        result.errors.append(f"Fetch failed for {item.url[:60]}: {str(exc)[:100]}")
                        continue

                    # 3. Parse detail
                    try:
                        article = adapter.parse_detail(raw_html, item)
                    except Exception as exc:
                        logger.warning(f"Error parsing detail for {item.url}: {exc}")
                        result.failed_count += 1
                        result.errors.append(f"Parse failed for {item.url[:60]}: {str(exc)[:100]}")
                        continue

                    if lookback_days and article.published_at:
                        cutoff_dt = utc_now() - timedelta(days=lookback_days)
                        if article.published_at < cutoff_dt:
                            continue

                    # Deduplicate by canonical URL
                    norm_canonical = normalize_url(article.canonical_url)
                    if norm_canonical and (norm_canonical in existing_canonicals or norm_canonical in existing_urls):
                        result.duplicates_count += 1
                        continue

                    # Deduplicate by content hash
                    content_hash = compute_ingestion_dedupe_hash(
                        article.title, article.canonical_url or article.url, article.excerpt
                    )
                    if content_hash in existing_hashes:
                        result.duplicates_count += 1
                        continue

                    # 4. Cross-source Google News match (Section 24)
                    gn_match_id: Optional[str] = None
                    norm_article_title = normalize_title(article.title)
                    article_domain = extract_publisher_domain(article.url) or source_domain

                    for gn_entry in existing_gn_entries:
                        gn_pub_domain = (gn_entry.raw_metadata or {}).get("publisher_domain")
                        if not gn_pub_domain:
                            gn_pub_domain = extract_publisher_domain(gn_entry.url)

                        if domains_belong_to_same_site(article_domain, gn_pub_domain):
                            if normalize_title(gn_entry.title) == norm_article_title:
                                gn_match_id = str(gn_entry.id)
                                result.google_news_matches_count += 1
                                break

                    # 5. Create Entry
                    raw_meta = dict(article.raw_metadata)
                    if gn_match_id:
                        raw_meta["discovered_via_google_news"] = True
                        raw_meta["google_news_discovery_entry_id"] = gn_match_id

                    # Optional deterministic TrackedEntity author match (Section 6)
                    if article.author:
                        author_name = article.author.strip()
                        matched_author_entity = (
                            db.query(TrackedEntity)
                            .filter(
                                TrackedEntity.active == True,
                                TrackedEntity.display_name == author_name,
                            )
                            .first()
                        )
                        if matched_author_entity:
                            raw_meta["tracked_author_entity_id"] = str(matched_author_entity.id)
                            raw_meta["tracked_author_entity_name"] = matched_author_entity.display_name

                    entry = Entry(
                        source_id=source.id,
                        external_id=article.external_id,
                        url=article.url,
                        canonical_url=article.canonical_url,
                        title=article.title,
                        content=article.content,
                        excerpt=article.excerpt,
                        author=article.author,
                        published_at=article.published_at,
                        captured_at=utc_now(),
                        language=article.language,
                        content_type="article",
                        content_hash=content_hash,
                        raw_metadata=raw_meta,
                    )
                    db.add(entry)
                    db.flush()

                    # If GN entry matched, register reciprocal traceability
                    if gn_match_id:
                        for gn_entry in existing_gn_entries:
                            if str(gn_entry.id) == gn_match_id:
                                gn_meta = dict(gn_entry.raw_metadata or {})
                                gn_meta["direct_entry_id"] = str(entry.id)
                                gn_entry.raw_metadata = gn_meta
                                break

                    # Update existing sets for next iterations in same run
                    existing_urls.add(norm_item_url)
                    if norm_canonical:
                        existing_canonicals.add(norm_canonical)
                    existing_hashes.add(content_hash)

                    # Assess sufficiency
                    sufficiency = SourceSufficiencyService.assess(entry)
                    level_val = sufficiency.level.value
                    result.sufficiency_counts[level_val] = result.sufficiency_counts.get(level_val, 0) + 1

                    # Update date tracking
                    if article.published_at:
                        if result.oldest_published_at is None or article.published_at < result.oldest_published_at:
                            result.oldest_published_at = article.published_at
                        if result.latest_published_at is None or article.published_at > result.latest_published_at:
                            result.latest_published_at = article.published_at

                    result.entries_created += 1

            # Finalize IngestionRun on success
            run.finished_at = utc_now()
            run.fetched_count = result.items_fetched
            run.created_count = result.entries_created
            run.duplicate_count = result.duplicates_count
            run.failed_count = result.failed_count
            run.oldest_published_at = result.oldest_published_at
            run.latest_published_at = result.latest_published_at
            run.status = (
                IngestionRunStatus.SUCCESS.value
                if result.failed_count == 0
                else (
                    IngestionRunStatus.PARTIAL.value
                    if result.entries_created > 0
                    else IngestionRunStatus.FAILED.value
                )
            )
            run.run_metadata = {
                "adapter": adapter_code,
                "items_discovered": result.items_discovered,
                "google_news_matches": result.google_news_matches_count,
                "sufficiency": result.sufficiency_counts,
                "errors": result.errors[:10],
            }
            source.last_run_at = run.finished_at
            if result.entries_created > 0 or result.duplicates_count > 0:
                source.last_success_at = run.finished_at

            db.commit()

        except Exception as exc:
            logger.error(f"Fatal error running ingestion for Source {source_name}: {exc}", exc_info=True)
            result.failed_count += 1
            result.errors.append(f"Source-level execution failure: {str(exc)[:200]}")
            try:
                run.finished_at = utc_now()
                run.status = IngestionRunStatus.FAILED.value
                run.error_type = type(exc).__name__
                run.error_message = str(exc)[:500]
                db.commit()
            except Exception:
                db.rollback()

        return result
