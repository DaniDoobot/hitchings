"""LinkedIn ingestion service coordinating provider dispatch, fallback, cross-dedupe, and traceability (Bloque 9C)."""

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.models.provider import ProviderUsage
from app.services.ingestion_service import compute_ingestion_dedupe_hash
from app.services.linkedin_discovery_planner import (
    LinkedInDiscoveryPlanner,
    LinkedInDiscoveryJob,
)
from app.providers.linkedin.base import (
    BaseLinkedInProvider,
    LinkedInDiscoveredPost,
    LinkedInAuthError,
    LinkedInRecoverableError,
)
from app.providers.linkedin.brightdata import BrightDataLinkedInProvider
from app.providers.linkedin.apify import ApifyLinkedInProvider

logger = logging.getLogger(__name__)


# ==============================================================================
# 1. REPORT DATACLASS
# ==============================================================================

@dataclass
class LinkedInIngestionReport:
    """Summary report for a LinkedIn discovery execution."""

    run_id: Optional[uuid.UUID]
    primary_provider: str
    fallback_provider: str
    entities_planned: int = 0
    entities_executed: int = 0
    posts_seen: int = 0
    entries_created: int = 0
    duplicates: int = 0
    failed_jobs: int = 0
    fallback_count: int = 0
    stopped_by_cap: bool = False
    estimated_provider_cost: Optional[float] = None
    errors: list[str] = field(default_factory=list)


# ==============================================================================
# 2. INGESTION SERVICE
# ==============================================================================

class LinkedInIngestionService:
    """Orchestrates LinkedIn post discovery using Bright Data (primary) and Apify (fallback)."""

    def __init__(
        self,
        planner: Optional[LinkedInDiscoveryPlanner] = None,
        primary_provider: Optional[BaseLinkedInProvider] = None,
        fallback_provider: Optional[BaseLinkedInProvider] = None,
    ) -> None:
        self.settings = get_settings()
        self.planner = planner or LinkedInDiscoveryPlanner()
        self.primary = primary_provider or BrightDataLinkedInProvider()
        self.fallback = fallback_provider or ApifyLinkedInProvider()

    def get_or_create_linkedin_source(self, db: Session) -> Source:
        """Resolve or idempotently create the canonical LinkedIn Source record."""
        source = db.execute(
            select(Source).where(Source.type == SourceType.LINKEDIN)
        ).scalar_one_or_none()

        if not source:
            source = Source(
                name="LinkedIn",
                type=SourceType.LINKEDIN,
                url="https://www.linkedin.com",
                active=True,
                category="social_network",
                provider="external",
                tracked_entity_id=None,  # Canonical source has NO single tracked entity owner
                config={
                    "discovery": True,
                    "primary_provider": self.primary.provider_name,
                    "fallback_provider": self.fallback.provider_name,
                },
            )
            db.add(source)
            db.flush()
            logger.info("Created canonical LinkedIn source (id=%s)", source.id)

        return source

    def execute_discovery(
        self,
        db: Session,
        confirm_real_calls: bool = False,
        target_entity_id: Optional[uuid.UUID] = None,
        client: Optional[httpx.Client] = None,
    ) -> LinkedInIngestionReport:
        """Execute discovery workflow for verified LinkedIn tracked entities."""
        report = LinkedInIngestionReport(
            run_id=None,
            primary_provider=self.primary.provider_name,
            fallback_provider=self.fallback.provider_name,
        )

        # 1. Plan jobs
        planned_jobs = self.planner.plan_jobs(db)
        if target_entity_id:
            planned_jobs = [j for j in planned_jobs if j.tracked_entity_id == target_entity_id]

        report.entities_planned = len(planned_jobs)
        if not planned_jobs:
            logger.info("No LinkedIn discovery jobs planned.")
            return report

        # 2. DRY-RUN check: if real execution is not confirmed or disabled, stop immediately
        if not confirm_real_calls:
            logger.info("LinkedIn discovery dry-run complete. %d jobs planned.", len(planned_jobs))
            return report

        if not self.settings.LINKEDIN_DISCOVERY_ENABLED:
            logger.warning(
                "LINKEDIN_DISCOVERY_ENABLED=false. Cannot execute real calls. Aborting."
            )
            report.errors.append("LinkedIn discovery is disabled in settings.")
            return report

        # 3. Resolve canonical source & create IngestionRun
        source = self.get_or_create_linkedin_source(db)
        now_utc = datetime.now(timezone.utc)
        run = IngestionRun(
            source_id=source.id,
            started_at=now_utc,
            status=IngestionRunStatus.RUNNING.value,
            run_metadata={
                "operation": "linkedin_discovery",
                "primary_provider": self.primary.provider_name,
                "fallback_provider": self.fallback.provider_name,
                "entities_planned": len(planned_jobs),
            },
        )
        db.add(run)
        db.flush()
        report.run_id = run.id

        # 4. Prepare execution state
        should_close_client = False
        if client is None:
            client = httpx.Client(timeout=self.settings.LINKEDIN_TIMEOUT_SECONDS)
            should_close_client = True

        max_posts_per_entity = self.settings.LINKEDIN_MAX_POSTS_PER_ENTITY
        max_new_entries = self.settings.LINKEDIN_MAX_NEW_ENTRIES_PER_RUN

        intra_run_seen_urls: set[str] = set()
        intra_run_seen_ids: set[str] = set()
        provider_items_used: dict[str, int] = {
            self.primary.provider_name: 0,
            self.fallback.provider_name: 0,
        }

        try:
            for job in planned_jobs:
                if report.entries_created >= max_new_entries:
                    logger.info("Reached maximum new entries cap (%d); stopping run.", max_new_entries)
                    report.stopped_by_cap = True
                    break

                report.entities_executed += 1
                posts: list[LinkedInDiscoveredPost] = []
                used_fallback_for_job = False
                fallback_reason: Optional[str] = None

                # 4.1 Try Primary Provider
                try:
                    posts = self.primary.discover_posts(
                        target_url=job.linkedin_url,
                        client=client,
                        limit=max_posts_per_entity,
                        entity_name=job.entity_name,
                        entity_id=job.tracked_entity_id,
                    )
                    provider_items_used[self.primary.provider_name] += len(posts)
                except LinkedInAuthError as auth_err:
                    # CRITICAL: Credentials error fails closed, NO FALLBACK
                    logger.error(
                        "Primary provider auth failure on entity '%s': %s. Fail-closed.",
                        job.entity_name,
                        auth_err,
                    )
                    report.failed_jobs += 1
                    report.errors.append(f"Auth error on {job.entity_name}: {auth_err}")
                    continue
                except LinkedInRecoverableError as rec_err:
                    logger.warning(
                        "Primary provider failed for '%s' (%s). Checking fallback eligibility...",
                        job.entity_name,
                        rec_err,
                    )
                    fallback_reason = str(rec_err)

                    # 4.2 Attempt Fallback Provider if configured
                    if self.settings.apify_token:
                        try:
                            posts = self.fallback.discover_posts(
                                target_url=job.linkedin_url,
                                client=client,
                                limit=max_posts_per_entity,
                                entity_name=job.entity_name,
                                entity_id=job.tracked_entity_id,
                            )
                            used_fallback_for_job = True
                            report.fallback_count += 1
                            provider_items_used[self.fallback.provider_name] += len(posts)
                            logger.info(
                                "Fallback '%s' succeeded for '%s' (%d posts).",
                                self.fallback.provider_name,
                                job.entity_name,
                                len(posts),
                            )
                        except Exception as fb_err:
                            logger.error(
                                "Fallback '%s' also failed for '%s': %s",
                                self.fallback.provider_name,
                                job.entity_name,
                                fb_err,
                            )
                            report.failed_jobs += 1
                            report.errors.append(f"Job failed on {job.entity_name}: {fb_err}")
                            continue
                    else:
                        logger.error(
                            "Primary failed and fallback token not configured. Skipping '%s'.",
                            job.entity_name,
                        )
                        report.failed_jobs += 1
                        report.errors.append(f"Job failed on {job.entity_name}: {rec_err}")
                        continue
                except Exception as unk_err:
                    logger.error("Unexpected error for '%s': %s", job.entity_name, unk_err)
                    report.failed_jobs += 1
                    report.errors.append(f"Unexpected error on {job.entity_name}: {unk_err}")
                    continue

                report.posts_seen += len(posts)

                # 4.3 Process Discovered Posts
                for post in posts:
                    if report.entries_created >= max_new_entries:
                        report.stopped_by_cap = True
                        break

                    # Deduplication checks
                    if self._is_duplicate(db, post, intra_run_seen_urls, intra_run_seen_ids):
                        report.duplicates += 1
                        continue

                    # Track in intra-run cache
                    norm_url = self._normalize_linkedin_url(post.linkedin_post_url)
                    intra_run_seen_urls.add(norm_url)
                    if post.provider_item_id:
                        intra_run_seen_ids.add(post.provider_item_id)

                    # Technical title generation (Section 16)
                    pub_date_str = (
                        post.published_at.strftime("%Y-%m-%d")
                        if post.published_at
                        else "undated"
                    )
                    title = f"LinkedIn — {post.author_name} — {pub_date_str}"

                    # Content length limit (Section 21)
                    max_chars = self.settings.LINKEDIN_MAX_POST_CHARS
                    clean_content = (post.text or "").strip()
                    if len(clean_content) > max_chars:
                        clean_content = clean_content[:max_chars]

                    # Content hash for standard deduplication
                    content_hash = compute_ingestion_dedupe_hash(title, post.linkedin_post_url, None)

                    # Metadata enrichment & Data minimization (Section 17, 21, 22)
                    enriched_meta = dict(post.raw_metadata)
                    enriched_meta.update({
                        "provider": post.provider,
                        "provider_item_id": post.provider_item_id,
                        "tracked_entity_id": str(job.tracked_entity_id),
                        "tracked_entity_name": job.entity_name,
                        "author_profile_url": post.author_profile_url,
                        "engagement": post.engagement,
                        "fallback_used": used_fallback_for_job,
                    })
                    if fallback_reason:
                        enriched_meta["fallback_reason"] = fallback_reason

                    entry = Entry(
                        source_id=source.id,
                        external_id=post.provider_item_id,
                        url=post.linkedin_post_url,
                        canonical_url=post.linkedin_post_url,
                        title=title,
                        content=clean_content if clean_content else None,
                        excerpt=clean_content[:300] if clean_content else None,
                        author=post.author_name,
                        published_at=post.published_at,
                        captured_at=datetime.now(timezone.utc),
                        content_type="social_post",
                        content_hash=content_hash,
                        raw_metadata=enriched_meta,
                    )
                    db.add(entry)
                    report.entries_created += 1

            # 5. Record ProviderUsage (Section 13)
            current_period = datetime.now(timezone.utc).strftime("%Y-%m")
            for prov_name, items_count in provider_items_used.items():
                if items_count > 0:
                    self._record_provider_usage(db, prov_name, current_period, items_count)

            # 6. Calculate estimated provider cost if rates configured (Section 13 & 14)
            est_cost: Optional[float] = None
            bd_cost = self.settings.BRIGHTDATA_COST_PER_RECORD_USD
            ap_cost = self.settings.APIFY_COST_PER_RECORD_USD
            if bd_cost is not None or ap_cost is not None:
                cost_sum = 0.0
                if bd_cost is not None:
                    cost_sum += provider_items_used.get("brightdata", 0) * bd_cost
                if ap_cost is not None:
                    cost_sum += provider_items_used.get("apify", 0) * ap_cost
                est_cost = round(cost_sum, 6)
            report.estimated_provider_cost = est_cost

            # 7. Update IngestionRun
            run.status = (
                IngestionRunStatus.SUCCESS.value
                if report.failed_jobs == 0
                else IngestionRunStatus.PARTIAL.value
            )
            run.finished_at = datetime.now(timezone.utc)
            run.fetched_count = report.posts_seen
            run.created_count = report.entries_created
            run.duplicate_count = report.duplicates
            run.failed_count = report.failed_jobs
            run.run_metadata = {
                "operation": "linkedin_discovery",
                "primary_provider": self.primary.provider_name,
                "fallback_provider": self.fallback.provider_name,
                "entities_planned": report.entities_planned,
                "entities_executed": report.entities_executed,
                "posts_seen": report.posts_seen,
                "entries_created": report.entries_created,
                "duplicates": report.duplicates,
                "failed_jobs": report.failed_jobs,
                "fallback_count": report.fallback_count,
                "stopped_by_cap": report.stopped_by_cap,
                "estimated_provider_cost": report.estimated_provider_cost,
                "errors": report.errors[:10],
            }

            source.last_run_at = datetime.now(timezone.utc)
            if report.entries_created > 0:
                source.last_success_at = datetime.now(timezone.utc)

            db.commit()

        except Exception as exc:
            db.rollback()
            logger.error("LinkedIn discovery run crashed: %s", exc, exc_info=True)
            report.errors.append(str(exc))
            if report.run_id:
                try:
                    crashed_run = db.get(IngestionRun, report.run_id)
                    if crashed_run:
                        crashed_run.status = IngestionRunStatus.FAILED
                        crashed_run.error_message = str(exc)
                        crashed_run.completed_at = datetime.now(timezone.utc)
                        db.commit()
                except Exception:
                    pass
            raise
        finally:
            if should_close_client and client is not None:
                client.close()

        return report

    def _is_duplicate(
        self,
        db: Session,
        post: LinkedInDiscoveredPost,
        seen_urls: set[str],
        seen_ids: set[str],
    ) -> bool:
        """Cross-provider deduplication check (Section 18 & 19)."""
        norm_url = self._normalize_linkedin_url(post.linkedin_post_url)
        if norm_url in seen_urls:
            return True

        if post.provider_item_id and post.provider_item_id in seen_ids:
            return True

        # Check in database
        existing_url = db.execute(
            select(Entry.id).where(
                (Entry.url == post.linkedin_post_url) | (Entry.canonical_url == post.linkedin_post_url)
            )
        ).scalar_one_or_none()
        if existing_url:
            return True

        if post.provider_item_id:
            existing_ext = db.execute(
                select(Entry.id).where(Entry.external_id == post.provider_item_id)
            ).scalar_one_or_none()
            if existing_ext:
                return True

        return False

    @staticmethod
    def _normalize_linkedin_url(url: str) -> str:
        """Strip trailing slash, query tracking parameters, and protocol differences."""
        if not url:
            return ""
        clean = url.split("?")[0].split("#")[0].strip().rstrip("/")
        # Normalize http -> https
        clean = re.sub(r"^http://", "https://", clean)
        return clean.lower()

    @staticmethod
    def _record_provider_usage(
        db: Session,
        provider_name: str,
        period: str,
        items_count: int,
    ) -> None:
        """Update provider_usage accounting table."""
        usage = db.execute(
            select(ProviderUsage).where(
                ProviderUsage.provider == provider_name,
                ProviderUsage.period == period,
            )
        ).scalar_one_or_none()

        if usage:
            usage.records_used += items_count
            usage.updated_at = datetime.now(timezone.utc)
        else:
            usage = ProviderUsage(
                provider=provider_name,
                period=period,
                records_used=items_count,
                allow_paid_usage=False,
            )
            db.add(usage)
        db.flush()
