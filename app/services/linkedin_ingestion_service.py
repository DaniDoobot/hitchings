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
from app.providers.linkedin.normalizer import (
    extract_linkedin_activity_id,
    normalize_linkedin_canonical_url,
    resolve_canonical_identity,
    is_author_profile_coherent,
)

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
    provider_records_fetched: int = 0
    provenance_rejected: int = 0
    errors: list[str] = field(default_factory=list)
    items_detail: list[dict[str, Any]] = field(default_factory=list)
    per_entity: list[dict[str, Any]] = field(default_factory=list)


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
        max_posts_per_entity: Optional[int] = None,
        max_new_entries: Optional[int] = None,
        allow_probe: bool = False,
        disable_fallback: bool = False,
        max_entities: Optional[int] = None,
        allow_manual: bool = False,
    ) -> LinkedInIngestionReport:
        """Execute discovery workflow for verified LinkedIn tracked entities."""
        report = LinkedInIngestionReport(
            run_id=None,
            primary_provider=self.primary.provider_name,
            fallback_provider=self.fallback.provider_name,
        )

        # 1. Plan jobs
        planned_jobs = self.planner.plan_jobs(db, max_entities=max_entities)
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

        if not self.settings.LINKEDIN_DISCOVERY_ENABLED and not allow_probe and not allow_manual:
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

        default_max_posts = getattr(self.settings, "LINKEDIN_DISCOVERY_MAX_POSTS_PER_ENTITY", None)
        if default_max_posts is None:
            default_max_posts = getattr(self.settings, "LINKEDIN_MAX_POSTS_PER_ENTITY", 1)
        max_posts_eff = (
            max_posts_per_entity
            if max_posts_per_entity is not None
            else default_max_posts
        )
        max_new_entries_eff = (
            max_new_entries
            if max_new_entries is not None
            else self.settings.LINKEDIN_MAX_NEW_ENTRIES_PER_RUN
        )

        intra_run_seen_urls: set[str] = set()
        intra_run_seen_ids: set[str] = set()
        provider_items_used: dict[str, int] = {
            self.primary.provider_name: 0,
            self.fallback.provider_name: 0,
        }

        try:
            for job in planned_jobs:
                if report.entries_created >= max_new_entries_eff:
                    logger.info("Reached maximum new entries cap (%d); stopping run.", max_new_entries_eff)
                    report.stopped_by_cap = True
                    break

                report.entities_executed += 1
                job_created_start = report.entries_created
                job_duplicates_start = report.duplicates
                job_provenance_rejected_start = report.provenance_rejected
                posts: list[LinkedInDiscoveredPost] = []
                used_fallback_for_job = False
                fallback_reason: Optional[str] = None

                # 4.1 Try Primary Provider
                try:
                    posts = self.primary.discover_posts(
                        target_url=job.linkedin_url,
                        client=client,
                        limit=max_posts_eff,
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
                    report.per_entity.append({
                        "entity": job.entity_name,
                        "provider": self.primary.provider_name,
                        "posts": 0,
                        "created": 0,
                        "duplicates": 0,
                        "provenance_rejected": 0,
                        "errors": 1,
                    })
                    report.items_detail.append({
                        "entity_name": job.entity_name,
                        "http_status": getattr(self.primary, "last_http_status", None) or 401,
                        "records_returned": 0,
                        "author_name": None,
                        "author_profile_url": None,
                        "linkedin_post_url": None,
                        "activity_id": None,
                        "published_at": None,
                        "identity_status": None,
                        "provenance_status": None,
                        "retrieval_provider": self.primary.provider_name,
                        "action": "AUTH_ERROR",
                        "entry_id": None,
                        "external_id": None,
                        "error": str(auth_err),
                    })
                    continue
                except LinkedInRecoverableError as rec_err:
                    logger.warning(
                        "Primary provider failed for '%s' (%s). Checking fallback eligibility...",
                        job.entity_name,
                        rec_err,
                    )
                    fallback_reason = str(rec_err)

                    # 4.2 Attempt Fallback Provider if configured and not disabled
                    if not disable_fallback and self.settings.apify_token:
                        try:
                            posts = self.fallback.discover_posts(
                                target_url=job.linkedin_url,
                                client=client,
                                limit=max_posts_eff,
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
                            report.per_entity.append({
                                "entity": job.entity_name,
                                "provider": self.fallback.provider_name,
                                "posts": 0,
                                "created": 0,
                                "duplicates": 0,
                                "errors": 1,
                            })
                            report.items_detail.append({
                                "entity_name": job.entity_name,
                                "http_status": getattr(self.fallback, "last_http_status", None) or "ERROR",
                                "records_returned": 0,
                                "author_name": None,
                                "author_profile_url": None,
                                "linkedin_post_url": None,
                                "activity_id": None,
                                "published_at": None,
                                "identity_status": None,
                                "provenance_status": None,
                                "retrieval_provider": self.fallback.provider_name,
                                "action": "FAILED",
                                "entry_id": None,
                                "external_id": None,
                                "error": f"Fallback failed: {fb_err}",
                            })
                            continue
                    else:
                        reason_msg = "fallback disabled" if disable_fallback else "fallback token not configured"
                        logger.error(
                            "Primary failed and %s. Skipping '%s'.",
                            reason_msg,
                            job.entity_name,
                        )
                        report.failed_jobs += 1
                        report.errors.append(f"Job failed on {job.entity_name}: {rec_err} ({reason_msg})")
                        report.per_entity.append({
                            "entity": job.entity_name,
                            "provider": self.primary.provider_name,
                            "posts": 0,
                            "created": 0,
                            "duplicates": 0,
                            "errors": 1,
                        })
                        report.items_detail.append({
                            "entity_name": job.entity_name,
                            "http_status": getattr(self.primary, "last_http_status", None) or "ERROR",
                            "records_returned": 0,
                            "author_name": None,
                            "author_profile_url": None,
                            "linkedin_post_url": None,
                            "activity_id": None,
                            "published_at": None,
                            "identity_status": None,
                            "provenance_status": None,
                            "retrieval_provider": self.primary.provider_name,
                            "action": "FAILED",
                            "entry_id": None,
                            "external_id": None,
                            "error": f"{rec_err} ({reason_msg})",
                        })
                        continue
                except Exception as unk_err:
                    logger.error("Unexpected error for '%s': %s", job.entity_name, unk_err)
                    report.failed_jobs += 1
                    report.errors.append(f"Unexpected error on {job.entity_name}: {unk_err}")
                    report.per_entity.append({
                        "entity": job.entity_name,
                        "provider": self.primary.provider_name,
                        "posts": 0,
                        "created": 0,
                        "duplicates": 0,
                        "errors": 1,
                    })
                    report.items_detail.append({
                        "entity_name": job.entity_name,
                        "http_status": getattr(self.primary, "last_http_status", None) or "ERROR",
                        "records_returned": 0,
                        "author_name": None,
                        "author_profile_url": None,
                        "linkedin_post_url": None,
                        "activity_id": None,
                        "published_at": None,
                        "identity_status": None,
                        "provenance_status": None,
                        "retrieval_provider": self.primary.provider_name,
                        "action": "ERROR",
                        "entry_id": None,
                        "external_id": None,
                        "error": str(unk_err),
                    })
                    continue

                report.posts_seen += len(posts)

                if len(posts) == 0:
                    report.items_detail.append({
                        "entity_name": job.entity_name,
                        "http_status": getattr(self.primary, "last_http_status", 200),
                        "records_returned": 0,
                        "author_name": None,
                        "author_profile_url": None,
                        "linkedin_post_url": None,
                        "activity_id": None,
                        "published_at": None,
                        "identity_status": None,
                        "provenance_status": None,
                        "retrieval_provider": self.primary.provider_name,
                        "action": "NO_POSTS",
                        "entry_id": None,
                        "external_id": None,
                    })

                # 4.3 Process Discovered Posts
                for post in posts:
                    if report.entries_created >= max_new_entries_eff:
                        report.stopped_by_cap = True
                        break

                    # Strict Provenance Verification (Section 1: TrackedEntity, author_name, and author_profile_url coherence)
                    author_name = (post.author_name or "").strip()
                    if (
                        not author_name
                        or author_name.lower() in ("linkedin author", "unknown", "author", "linkedin user")
                        or not job.tracked_entity_id
                    ):
                        logger.warning(
                            "Skipping LinkedIn post without reliable author_name or tracked_entity: url=%s, author=%r",
                            post.linkedin_post_url,
                            author_name,
                        )
                        report.provenance_rejected += 1
                        report.items_detail.append({
                            "entity_name": job.entity_name,
                            "http_status": post.raw_metadata.get("http_status", getattr(self.primary, "last_http_status", 200)),
                            "records_returned": len(posts),
                            "author_name": author_name or None,
                            "author_profile_url": post.author_profile_url,
                            "linkedin_post_url": post.linkedin_post_url,
                            "activity_id": post.provider_item_id,
                            "published_at": post.published_at.isoformat() if post.published_at else None,
                            "identity_status": None,
                            "provenance_status": "unverified",
                            "retrieval_provider": post.provider,
                            "action": "SKIPPED_PROVENANCE",
                            "rejection_reason": "missing_or_unreliable_author",
                            "content_snippet": (post.text or "").strip()[:200],
                            "entry_id": None,
                            "external_id": None,
                        })
                        continue

                    if not is_author_profile_coherent(post.author_profile_url, job.linkedin_url):
                        logger.warning(
                            "Skipping LinkedIn post with unverified authorship provenance: author_profile_url=%r does not match configured entity url=%r for entity %r",
                            post.author_profile_url,
                            job.linkedin_url,
                            job.entity_name,
                        )
                        report.provenance_rejected += 1
                        report.items_detail.append({
                            "entity_name": job.entity_name,
                            "http_status": post.raw_metadata.get("http_status", getattr(self.primary, "last_http_status", 200)),
                            "records_returned": len(posts),
                            "author_name": author_name,
                            "author_profile_url": post.author_profile_url,
                            "linkedin_post_url": post.linkedin_post_url,
                            "activity_id": post.provider_item_id,
                            "published_at": post.published_at.isoformat() if post.published_at else None,
                            "identity_status": None,
                            "provenance_status": "unverified",
                            "retrieval_provider": post.provider,
                            "action": "SKIPPED_PROVENANCE",
                            "rejection_reason": f"author_profile_url_mismatch: {post.author_profile_url} != {job.linkedin_url}",
                            "content_snippet": (post.text or "").strip()[:200],
                            "entry_id": None,
                            "external_id": None,
                        })
                        continue

                    # Authorship provenance is strictly verified
                    provenance_status = "verified"

                    # Resolve canonical identity & normalized URL (returns identity_status: 'activity_id' | 'canonical_url_fallback')
                    external_id, canonical_url, activity_id, identity_status = resolve_canonical_identity(
                        post.provider_item_id, post.linkedin_post_url
                    )

                    # Deduplication checks
                    if self._is_duplicate(db, external_id, canonical_url, intra_run_seen_urls, intra_run_seen_ids):
                        report.duplicates += 1
                        report.items_detail.append({
                            "entity_name": job.entity_name,
                            "http_status": post.raw_metadata.get("http_status", getattr(self.primary, "last_http_status", 200)),
                            "records_returned": len(posts),
                            "author_name": author_name,
                            "author_profile_url": post.author_profile_url,
                            "linkedin_post_url": post.linkedin_post_url,
                            "activity_id": activity_id or post.provider_item_id,
                            "published_at": post.published_at.isoformat() if post.published_at else None,
                            "identity_status": identity_status,
                            "provenance_status": provenance_status,
                            "retrieval_provider": post.provider,
                            "action": "DUPLICATE",
                            "rejection_reason": None,
                            "content_snippet": (post.text or "").strip()[:200],
                            "entry_id": None,
                            "external_id": external_id,
                        })
                        continue

                    # Track in intra-run cache
                    intra_run_seen_urls.add(canonical_url)
                    if external_id:
                        intra_run_seen_ids.add(external_id)
                    if activity_id:
                        intra_run_seen_ids.add(activity_id)
                    if post.provider_item_id:
                        intra_run_seen_ids.add(post.provider_item_id)

                    # Technical title generation (Section 16)
                    pub_date_str = (
                        post.published_at.strftime("%Y-%m-%d")
                        if post.published_at
                        else "undated"
                    )
                    title = f"LinkedIn — {author_name} — {pub_date_str}"

                    # Content length limit (Section 21)
                    max_chars = self.settings.LINKEDIN_MAX_POST_CHARS
                    clean_content = (post.text or "").strip()
                    if len(clean_content) > max_chars:
                        clean_content = clean_content[:max_chars]

                    # Content hash for standard deduplication
                    content_hash = compute_ingestion_dedupe_hash(title, canonical_url, None)

                    # Determine author_type from tracked entity
                    etype = str(job.entity_type).lower().strip()
                    if etype in ("organization", "institution", "publication", "company"):
                        author_type = "organization"
                    elif etype == "person":
                        author_type = "person"
                    else:
                        author_type = "organization"

                    # Metadata enrichment & Strict Provenance (Sections 17, 21, 22)
                    enriched_meta = dict(post.raw_metadata or {})
                    # Ensure legacy 'provider' key is NOT written to new entries (Requirement 2)
                    enriched_meta.pop("provider", None)
                    enriched_meta.update({
                        "origin_source": "linkedin",
                        "source_origin_category": "linkedin",
                        "retrieval_provider": post.provider,
                        "identity_status": identity_status,
                        "provenance_status": provenance_status,
                        "author_name": author_name,
                        "author_type": author_type,
                        "author_profile_url": post.author_profile_url,
                        "tracked_entity_id": str(job.tracked_entity_id),
                        "tracked_entity_name": job.entity_name,
                        "linkedin_activity_id": activity_id,
                        "engagement": post.engagement,
                        "fallback_used": used_fallback_for_job,
                    })
                    if fallback_reason:
                        enriched_meta["fallback_reason"] = fallback_reason

                    entry = Entry(
                        source_id=source.id,
                        external_id=external_id,
                        url=canonical_url,
                        canonical_url=canonical_url,
                        title=title,
                        content=clean_content if clean_content else None,
                        excerpt=clean_content[:300] if clean_content else None,
                        author=author_name,
                        published_at=post.published_at,
                        captured_at=datetime.now(timezone.utc),
                        content_type="social_post",
                        content_hash=content_hash,
                        raw_metadata=enriched_meta,
                    )
                    db.add(entry)
                    db.flush()
                    report.entries_created += 1
                    report.items_detail.append({
                        "entity_name": job.entity_name,
                        "http_status": post.raw_metadata.get("http_status", getattr(self.primary, "last_http_status", 200)),
                        "records_returned": len(posts),
                        "author_name": author_name,
                        "author_profile_url": post.author_profile_url,
                        "linkedin_post_url": post.linkedin_post_url,
                        "activity_id": activity_id or post.provider_item_id,
                        "published_at": post.published_at.isoformat() if post.published_at else None,
                        "identity_status": identity_status,
                        "provenance_status": provenance_status,
                        "retrieval_provider": post.provider,
                        "action": "CREATED",
                        "rejection_reason": None,
                        "content_snippet": clean_content[:200],
                        "entry_id": str(entry.id),
                        "external_id": external_id,
                    })

                job_created = report.entries_created - job_created_start
                job_duplicates = report.duplicates - job_duplicates_start
                job_provenance_rejected = report.provenance_rejected - job_provenance_rejected_start
                job_posts = len(posts)
                used_prov = self.fallback.provider_name if used_fallback_for_job else self.primary.provider_name
                report.per_entity.append({
                    "entity": job.entity_name,
                    "provider": used_prov,
                    "posts": job_posts,
                    "created": job_created,
                    "duplicates": job_duplicates,
                    "provenance_rejected": job_provenance_rejected,
                    "errors": 0,
                })

            # 5. Record ProviderUsage (Section 13)
            current_period = datetime.now(timezone.utc).strftime("%Y-%m")
            for prov_name, items_count in provider_items_used.items():
                if items_count > 0:
                    self._record_provider_usage(db, prov_name, current_period, items_count)

            # 6. Calculate estimated provider cost if rates configured (Section 13 & 14)
            est_cost: Optional[float] = None
            bd_cost = getattr(self.settings, "BRIGHTDATA_LINKEDIN_POST_COST_PER_RECORD", None)
            if bd_cost is None:
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
            report.provider_records_fetched = sum(provider_items_used.values())

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
                "provider_records_fetched": report.provider_records_fetched,
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
        external_id: str,
        canonical_url: str,
        seen_urls: set[str],
        seen_ids: set[str],
    ) -> bool:
        """Cross-provider deduplication check in strict priority:
        1. In-memory external_id or canonical_url
        2. Database check by external_id (e.g. urn:li:activity:{id})
        3. Database check by canonical_url or raw url
        """
        if external_id and external_id in seen_ids:
            return True

        if canonical_url and canonical_url in seen_urls:
            return True

        # 1. Check in database by external_id
        if external_id:
            existing_ext = db.execute(
                select(Entry.id).where(Entry.external_id == external_id)
            ).scalar_one_or_none()
            if existing_ext:
                return True

        # 2. Check in database by canonical_url or raw url
        if canonical_url:
            existing_url = db.execute(
                select(Entry.id).where(
                    (Entry.canonical_url == canonical_url) | (Entry.url == canonical_url)
                )
            ).scalar_one_or_none()
            if existing_url:
                return True

        return False

    @staticmethod
    def _normalize_linkedin_url(url: str) -> str:
        """Strip trailing slash, query tracking parameters, and protocol differences."""
        return normalize_linkedin_canonical_url(url)

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

    @staticmethod
    def get_retrieval_provider(entry: Entry) -> Optional[str]:
        """Read retrieval provider from entry metadata with backward-compatible fallback to legacy 'provider'."""
        meta = entry.raw_metadata or {}
        return meta.get("retrieval_provider") or meta.get("provider")

