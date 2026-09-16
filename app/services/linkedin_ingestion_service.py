"""LinkedIn ingestion service coordinating provider dispatch, fallback, cross-dedupe, and traceability (Bloque 9C)."""

import asyncio
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
    LinkedInSnapshotTimeoutError,
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
    timed_out_snapshots: int = 0
    provider_errors: int = 0
    fallback_count: int = 0
    stopped_by_cap: bool = False
    estimated_provider_cost: Optional[float] = None
    provider_records_fetched: int = 0
    provenance_rejected: int = 0
    execution_time_seconds: float = 0.0
    execution_mode: str = "sequential"  # "sequential" or "concurrent_N"
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
        max_concurrent: Optional[int] = None,
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
        # NOTE: In concurrent mode, each job creates its own httpx.Client internally.
        # The caller-supplied `client` is used only in sequential mode (max_concurrent=1)
        # to preserve backward compatibility with tests that inject mock clients.
        effective_max_concurrent = max_concurrent if max_concurrent is not None else self.settings.LINKEDIN_MAX_CONCURRENT_JOBS
        use_concurrent = effective_max_concurrent > 1 and len(planned_jobs) > 1

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

        # Set execution mode on report
        if use_concurrent:
            report.execution_mode = f"concurrent_{effective_max_concurrent}"
        else:
            report.execution_mode = "sequential"

        intra_run_seen_urls: set[str] = set()
        intra_run_seen_ids: set[str] = set()
        provider_items_used: dict[str, int] = {
            self.primary.provider_name: 0,
            self.fallback.provider_name: 0,
        }

        run_start_time = datetime.now(timezone.utc)

        try:
            if use_concurrent:
                # ── CONCURRENT PATH ──────────────────────────────────────────
                # Each job runs independently in a ThreadPoolExecutor via
                # asyncio.run_in_executor. A Semaphore limits parallelism.
                # Shared state is protected by an asyncio.Lock.
                asyncio.run(
                    self._run_jobs_concurrent(
                        planned_jobs=planned_jobs,
                        db=db,
                        source=source,
                        report=report,
                        max_posts_eff=max_posts_eff,
                        max_new_entries_eff=max_new_entries_eff,
                        disable_fallback=disable_fallback,
                        max_concurrent=effective_max_concurrent,
                        provider_items_used=provider_items_used,
                        intra_run_seen_urls=intra_run_seen_urls,
                        intra_run_seen_ids=intra_run_seen_ids,
                    )
                )
            else:
                # ── SEQUENTIAL PATH (existing behaviour, preserves mock client support) ──
                should_close_client = False
                if client is None:
                    client = httpx.Client(timeout=self.settings.LINKEDIN_TIMEOUT_SECONDS)
                    should_close_client = True
                try:
                    self._run_jobs_sequential(
                        planned_jobs=planned_jobs,
                        db=db,
                        source=source,
                        report=report,
                        client=client,
                        max_posts_eff=max_posts_eff,
                        max_new_entries_eff=max_new_entries_eff,
                        disable_fallback=disable_fallback,
                        provider_items_used=provider_items_used,
                        intra_run_seen_urls=intra_run_seen_urls,
                        intra_run_seen_ids=intra_run_seen_ids,
                    )
                finally:
                    if should_close_client and client is not None:
                        client.close()

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

            # 7. Record execution time
            report.execution_time_seconds = round(
                (datetime.now(timezone.utc) - run_start_time).total_seconds(), 2
            )

            # 8. Update IngestionRun
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
                "timed_out_snapshots": report.timed_out_snapshots,
                "provider_errors": report.provider_errors,
                "fallback_count": report.fallback_count,
                "stopped_by_cap": report.stopped_by_cap,
                "provider_records_fetched": report.provider_records_fetched,
                "estimated_provider_cost": report.estimated_provider_cost,
                "execution_mode": report.execution_mode,
                "execution_time_seconds": report.execution_time_seconds,
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
                        crashed_run.finished_at = datetime.now(timezone.utc)
                        db.commit()
                except Exception:
                    pass
            raise
        finally:
            pass  # Client lifecycle managed inside sequential/concurrent path

        return report

    def _run_jobs_sequential(
        self,
        planned_jobs: list,
        db: Session,
        source: "Source",
        report: LinkedInIngestionReport,
        client: httpx.Client,
        max_posts_eff: int,
        max_new_entries_eff: int,
        disable_fallback: bool,
        provider_items_used: dict[str, int],
        intra_run_seen_urls: set[str],
        intra_run_seen_ids: set[str],
    ) -> None:
        """Execute jobs one at a time using the supplied shared client (preserves test mock compatibility)."""
        for job in planned_jobs:
            if report.entries_created >= max_new_entries_eff:
                logger.info("Reached maximum new entries cap (%d); stopping run.", max_new_entries_eff)
                report.stopped_by_cap = True
                break
            self._process_single_job(
                job=job,
                db=db,
                source=source,
                report=report,
                client=client,
                max_posts_eff=max_posts_eff,
                max_new_entries_eff=max_new_entries_eff,
                disable_fallback=disable_fallback,
                provider_items_used=provider_items_used,
                intra_run_seen_urls=intra_run_seen_urls,
                intra_run_seen_ids=intra_run_seen_ids,
            )

    async def _run_jobs_concurrent(
        self,
        planned_jobs: list,
        db: Session,
        source: "Source",
        report: LinkedInIngestionReport,
        max_posts_eff: int,
        max_new_entries_eff: int,
        disable_fallback: bool,
        max_concurrent: int,
        provider_items_used: dict[str, int],
        intra_run_seen_urls: set[str],
        intra_run_seen_ids: set[str],
    ) -> None:
        """Execute jobs concurrently up to max_concurrent at a time.

        Each job uses its own dedicated httpx.Client to avoid cross-job state corruption.
        Shared state (dedup sets, counters) is protected by an asyncio.Lock so that
        concurrent tasks cannot see each other's partial writes.
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        lock = asyncio.Lock()
        loop = asyncio.get_event_loop()

        async def run_one_job(job: "LinkedInDiscoveryJob") -> None:
            async with semaphore:
                # Check cap before starting (non-blocking read, worst case we run one extra)
                async with lock:
                    if report.entries_created >= max_new_entries_eff:
                        return

                # Each concurrent job gets its own client
                job_client = httpx.Client(timeout=self.settings.LINKEDIN_TIMEOUT_SECONDS)
                # Partial report for this job (not shared until merged under lock)
                job_items_detail: list[dict] = []
                job_per_entity: list[dict] = []
                job_errors: list[str] = []
                job_counters = {
                    "posts_seen": 0,
                    "entries_created": 0,
                    "duplicates": 0,
                    "failed_jobs": 0,
                    "timed_out_snapshots": 0,
                    "provider_errors": 0,
                    "fallback_count": 0,
                    "provenance_rejected": 0,
                    "stopped_by_cap": False,
                    "entities_executed": 0,
                }
                job_provider_items: dict[str, int] = {
                    self.primary.provider_name: 0,
                    self.fallback.provider_name: 0,
                }

                try:
                    # Run the blocking discover_posts call off the event loop thread
                    await loop.run_in_executor(
                        None,
                        lambda: self._process_single_job_into(
                            job=job,
                            db=db,
                            source=source,
                            client=job_client,
                            max_posts_eff=max_posts_eff,
                            max_new_entries_eff=max_new_entries_eff,
                            disable_fallback=disable_fallback,
                            counters=job_counters,
                            items_detail=job_items_detail,
                            per_entity=job_per_entity,
                            errors=job_errors,
                            provider_items_used=job_provider_items,
                            # We pass a thread-local dedup snapshot; final merge uses lock
                            intra_run_seen_urls=set(intra_run_seen_urls),
                            intra_run_seen_ids=set(intra_run_seen_ids),
                        )
                    )
                finally:
                    job_client.close()

                # Merge results back into shared report under lock
                async with lock:
                    report.posts_seen += job_counters["posts_seen"]
                    report.entries_created += job_counters["entries_created"]
                    report.duplicates += job_counters["duplicates"]
                    report.failed_jobs += job_counters["failed_jobs"]
                    report.timed_out_snapshots += job_counters["timed_out_snapshots"]
                    report.provider_errors += job_counters["provider_errors"]
                    report.fallback_count += job_counters["fallback_count"]
                    report.provenance_rejected += job_counters["provenance_rejected"]
                    report.entities_executed += job_counters["entities_executed"]
                    if job_counters["stopped_by_cap"]:
                        report.stopped_by_cap = True
                    report.errors.extend(job_errors)
                    report.items_detail.extend(job_items_detail)
                    report.per_entity.extend(job_per_entity)
                    for prov, cnt in job_provider_items.items():
                        provider_items_used[prov] = provider_items_used.get(prov, 0) + cnt
                    # Update shared dedup sets from entries actually created by this job
                    for item in job_items_detail:
                        if item.get("action") == "CREATED":
                            if item.get("external_id"):
                                intra_run_seen_ids.add(item["external_id"])
                            if item.get("linkedin_post_url"):
                                intra_run_seen_urls.add(item["linkedin_post_url"])

        tasks = [run_one_job(job) for job in planned_jobs]
        await asyncio.gather(*tasks, return_exceptions=True)

    def _process_single_job(
        self,
        job: "LinkedInDiscoveryJob",
        db: Session,
        source: "Source",
        report: LinkedInIngestionReport,
        client: httpx.Client,
        max_posts_eff: int,
        max_new_entries_eff: int,
        disable_fallback: bool,
        provider_items_used: dict[str, int],
        intra_run_seen_urls: set[str],
        intra_run_seen_ids: set[str],
    ) -> None:
        """Process one job directly into the shared report (used by sequential path)."""
        counters: dict[str, Any] = {
            "posts_seen": 0,
            "entries_created": 0,
            "duplicates": 0,
            "failed_jobs": 0,
            "timed_out_snapshots": 0,
            "provider_errors": 0,
            "fallback_count": 0,
            "provenance_rejected": 0,
            "stopped_by_cap": False,
            "entities_executed": 0,
        }
        items_detail: list[dict] = []
        per_entity: list[dict] = []
        errors: list[str] = []

        self._process_single_job_into(
            job=job,
            db=db,
            source=source,
            client=client,
            max_posts_eff=max_posts_eff,
            max_new_entries_eff=max_new_entries_eff,
            disable_fallback=disable_fallback,
            counters=counters,
            items_detail=items_detail,
            per_entity=per_entity,
            errors=errors,
            provider_items_used=provider_items_used,
            intra_run_seen_urls=intra_run_seen_urls,
            intra_run_seen_ids=intra_run_seen_ids,
        )

        # Merge directly (sequential: no lock needed)
        report.posts_seen += counters["posts_seen"]
        report.entries_created += counters["entries_created"]
        report.duplicates += counters["duplicates"]
        report.failed_jobs += counters["failed_jobs"]
        report.timed_out_snapshots += counters["timed_out_snapshots"]
        report.provider_errors += counters["provider_errors"]
        report.fallback_count += counters["fallback_count"]
        report.provenance_rejected += counters["provenance_rejected"]
        report.entities_executed += counters["entities_executed"]
        if counters["stopped_by_cap"]:
            report.stopped_by_cap = True
        report.errors.extend(errors)
        report.items_detail.extend(items_detail)
        report.per_entity.extend(per_entity)

    def _process_single_job_into(  # noqa: C901 (complexity)
        self,
        job: "LinkedInDiscoveryJob",
        db: Session,
        source: "Source",
        client: httpx.Client,
        max_posts_eff: int,
        max_new_entries_eff: int,
        disable_fallback: bool,
        counters: dict[str, Any],
        items_detail: list[dict],
        per_entity: list[dict],
        errors: list[str],
        provider_items_used: dict[str, int],
        intra_run_seen_urls: set[str],
        intra_run_seen_ids: set[str],
    ) -> None:
        """Core per-job processing logic: discover → provenance → dedup → create Entry.

        Results are written into the caller-supplied mutable containers so that
        both the sequential and concurrent execution paths can share this logic.
        """
        counters["entities_executed"] += 1
        posts: list[LinkedInDiscoveredPost] = []
        used_fallback_for_job = False
        fallback_reason: Optional[str] = None

        # ── Provider dispatch ─────────────────────────────────────────────────
        try:
            posts = self.primary.discover_posts(
                target_url=job.linkedin_url,
                client=client,
                limit=max_posts_eff,
                entity_name=job.entity_name,
                entity_id=job.tracked_entity_id,
            )
            provider_items_used[self.primary.provider_name] = (
                provider_items_used.get(self.primary.provider_name, 0) + len(posts)
            )
        except LinkedInAuthError as auth_err:
            logger.error("Primary auth failure on entity '%s': %s. Fail-closed.", job.entity_name, auth_err)
            counters["failed_jobs"] += 1
            counters["provider_errors"] += 1
            errors.append(f"Auth error on {job.entity_name}: {auth_err}")
            per_entity.append({
                "entity": job.entity_name, "provider": self.primary.provider_name,
                "posts": 0, "created": 0, "duplicates": 0, "provenance_rejected": 0,
                "errors": 1, "timed_out_snapshots": 0, "provider_errors": 1,
            })
            items_detail.append({
                "entity_name": job.entity_name, "http_status": 401,
                "records_returned": 0, "author_name": None, "author_profile_url": None,
                "linkedin_post_url": None, "activity_id": None, "published_at": None,
                "identity_status": None, "provenance_status": None,
                "retrieval_provider": self.primary.provider_name,
                "action": "AUTH_ERROR", "entry_id": None, "external_id": None,
                "error": str(auth_err),
            })
            return

        except LinkedInSnapshotTimeoutError as snap_err:
            logger.warning("Snapshot timed out for '%s'. Checking fallback...", job.entity_name)
            fallback_reason = str(snap_err)
            if not disable_fallback and self.settings.apify_token:
                try:
                    posts = self.fallback.discover_posts(
                        target_url=job.linkedin_url, client=client, limit=max_posts_eff,
                        entity_name=job.entity_name, entity_id=job.tracked_entity_id,
                    )
                    used_fallback_for_job = True
                    counters["fallback_count"] += 1
                    provider_items_used[self.fallback.provider_name] = (
                        provider_items_used.get(self.fallback.provider_name, 0) + len(posts)
                    )
                except Exception as fb_err:
                    counters["failed_jobs"] += 1
                    counters["timed_out_snapshots"] += 1
                    errors.append(f"Job failed on {job.entity_name}: snapshot timeout and fallback failed: {fb_err}")
                    per_entity.append({
                        "entity": job.entity_name, "provider": self.fallback.provider_name,
                        "posts": 0, "created": 0, "duplicates": 0, "provenance_rejected": 0,
                        "errors": 1, "timed_out_snapshots": 1, "provider_errors": 0,
                    })
                    items_detail.append({
                        "entity_name": job.entity_name, "http_status": "ERROR",
                        "records_returned": 0, "author_name": None, "author_profile_url": None,
                        "linkedin_post_url": None, "activity_id": None, "published_at": None,
                        "identity_status": None, "provenance_status": None,
                        "retrieval_provider": self.fallback.provider_name,
                        "action": "FAILED", "entry_id": None, "external_id": None,
                        "error": f"Snapshot timeout; Fallback failed: {fb_err}",
                    })
                    return
            else:
                reason_msg = "fallback disabled" if disable_fallback else "fallback token not configured"
                counters["failed_jobs"] += 1
                counters["timed_out_snapshots"] += 1
                errors.append(f"Job failed on {job.entity_name}: {snap_err} ({reason_msg})")
                per_entity.append({
                    "entity": job.entity_name, "provider": self.primary.provider_name,
                    "posts": 0, "created": 0, "duplicates": 0, "provenance_rejected": 0,
                    "errors": 1, "timed_out_snapshots": 1, "provider_errors": 0,
                })
                items_detail.append({
                    "entity_name": job.entity_name, "http_status": 200,
                    "records_returned": 0, "author_name": None, "author_profile_url": None,
                    "linkedin_post_url": None, "activity_id": None, "published_at": None,
                    "identity_status": None, "provenance_status": None,
                    "retrieval_provider": self.primary.provider_name,
                    "action": "TIMED_OUT_SNAPSHOT", "entry_id": None, "external_id": None,
                    "error": f"{snap_err} ({reason_msg})",
                })
                return

        except LinkedInRecoverableError as rec_err:
            logger.warning("Primary failed for '%s'. Checking fallback...", job.entity_name)
            fallback_reason = str(rec_err)
            if not disable_fallback and self.settings.apify_token:
                try:
                    posts = self.fallback.discover_posts(
                        target_url=job.linkedin_url, client=client, limit=max_posts_eff,
                        entity_name=job.entity_name, entity_id=job.tracked_entity_id,
                    )
                    used_fallback_for_job = True
                    counters["fallback_count"] += 1
                    provider_items_used[self.fallback.provider_name] = (
                        provider_items_used.get(self.fallback.provider_name, 0) + len(posts)
                    )
                except Exception as fb_err:
                    counters["failed_jobs"] += 1
                    counters["provider_errors"] += 1
                    errors.append(f"Job failed on {job.entity_name}: {fb_err}")
                    per_entity.append({
                        "entity": job.entity_name, "provider": self.fallback.provider_name,
                        "posts": 0, "created": 0, "duplicates": 0, "provenance_rejected": 0,
                        "errors": 1, "timed_out_snapshots": 0, "provider_errors": 1,
                    })
                    items_detail.append({
                        "entity_name": job.entity_name, "http_status": "ERROR",
                        "records_returned": 0, "author_name": None, "author_profile_url": None,
                        "linkedin_post_url": None, "activity_id": None, "published_at": None,
                        "identity_status": None, "provenance_status": None,
                        "retrieval_provider": self.fallback.provider_name,
                        "action": "FAILED", "entry_id": None, "external_id": None,
                        "error": f"Fallback failed: {fb_err}",
                    })
                    return
            else:
                reason_msg = "fallback disabled" if disable_fallback else "fallback token not configured"
                counters["failed_jobs"] += 1
                counters["provider_errors"] += 1
                errors.append(f"Job failed on {job.entity_name}: {rec_err} ({reason_msg})")
                per_entity.append({
                    "entity": job.entity_name, "provider": self.primary.provider_name,
                    "posts": 0, "created": 0, "duplicates": 0, "provenance_rejected": 0,
                    "errors": 1, "timed_out_snapshots": 0, "provider_errors": 1,
                })
                items_detail.append({
                    "entity_name": job.entity_name, "http_status": "ERROR",
                    "records_returned": 0, "author_name": None, "author_profile_url": None,
                    "linkedin_post_url": None, "activity_id": None, "published_at": None,
                    "identity_status": None, "provenance_status": None,
                    "retrieval_provider": self.primary.provider_name,
                    "action": "FAILED", "entry_id": None, "external_id": None,
                    "error": f"{rec_err} ({reason_msg})",
                })
                return

        except Exception as unk_err:
            logger.error("Unexpected error for '%s': %s", job.entity_name, unk_err)
            counters["failed_jobs"] += 1
            counters["provider_errors"] += 1
            errors.append(f"Unexpected error on {job.entity_name}: {unk_err}")
            per_entity.append({
                "entity": job.entity_name, "provider": self.primary.provider_name,
                "posts": 0, "created": 0, "duplicates": 0, "provenance_rejected": 0,
                "errors": 1, "timed_out_snapshots": 0, "provider_errors": 1,
            })
            items_detail.append({
                "entity_name": job.entity_name, "http_status": "ERROR",
                "records_returned": 0, "author_name": None, "author_profile_url": None,
                "linkedin_post_url": None, "activity_id": None, "published_at": None,
                "identity_status": None, "provenance_status": None,
                "retrieval_provider": self.primary.provider_name,
                "action": "ERROR", "entry_id": None, "external_id": None,
                "error": str(unk_err),
            })
            return

        # ── Post processing ───────────────────────────────────────────────────
        counters["posts_seen"] += len(posts)

        if len(posts) == 0:
            items_detail.append({
                "entity_name": job.entity_name, "http_status": 200,
                "records_returned": 0, "author_name": None, "author_profile_url": None,
                "linkedin_post_url": None, "activity_id": None, "published_at": None,
                "identity_status": None, "provenance_status": None,
                "retrieval_provider": self.primary.provider_name,
                "action": "NO_POSTS", "entry_id": None, "external_id": None,
            })

        job_created_start = counters["entries_created"]
        job_duplicates_start = counters["duplicates"]
        job_provenance_rejected_start = counters["provenance_rejected"]

        for post in posts:
            if counters["entries_created"] >= max_new_entries_eff:
                counters["stopped_by_cap"] = True
                break

            author_name = (post.author_name or "").strip()
            if (
                not author_name
                or author_name.lower() in ("linkedin author", "unknown", "author", "linkedin user")
                or not job.tracked_entity_id
            ):
                counters["provenance_rejected"] += 1
                items_detail.append({
                    "entity_name": job.entity_name,
                    "http_status": post.raw_metadata.get("http_status", 200),
                    "records_returned": len(posts), "author_name": author_name or None,
                    "author_profile_url": post.author_profile_url,
                    "linkedin_post_url": post.linkedin_post_url,
                    "activity_id": post.provider_item_id,
                    "published_at": post.published_at.isoformat() if post.published_at else None,
                    "identity_status": None, "provenance_status": "unverified",
                    "retrieval_provider": post.provider, "action": "SKIPPED_PROVENANCE",
                    "rejection_reason": "missing_or_unreliable_author",
                    "content_snippet": (post.text or "").strip()[:200],
                    "entry_id": None, "external_id": None,
                })
                continue

            if not is_author_profile_coherent(post.author_profile_url, job.linkedin_url):
                counters["provenance_rejected"] += 1
                items_detail.append({
                    "entity_name": job.entity_name,
                    "http_status": post.raw_metadata.get("http_status", 200),
                    "records_returned": len(posts), "author_name": author_name,
                    "author_profile_url": post.author_profile_url,
                    "linkedin_post_url": post.linkedin_post_url,
                    "activity_id": post.provider_item_id,
                    "published_at": post.published_at.isoformat() if post.published_at else None,
                    "identity_status": None, "provenance_status": "unverified",
                    "retrieval_provider": post.provider, "action": "SKIPPED_PROVENANCE",
                    "rejection_reason": f"author_profile_url_mismatch: {post.author_profile_url} != {job.linkedin_url}",
                    "content_snippet": (post.text or "").strip()[:200],
                    "entry_id": None, "external_id": None,
                })
                continue

            provenance_status = "verified"
            external_id, canonical_url, activity_id, identity_status = resolve_canonical_identity(
                post.provider_item_id, post.linkedin_post_url
            )

            if self._is_duplicate(db, external_id, canonical_url, intra_run_seen_urls, intra_run_seen_ids):
                counters["duplicates"] += 1
                items_detail.append({
                    "entity_name": job.entity_name,
                    "http_status": post.raw_metadata.get("http_status", 200),
                    "records_returned": len(posts), "author_name": author_name,
                    "author_profile_url": post.author_profile_url,
                    "linkedin_post_url": post.linkedin_post_url,
                    "activity_id": activity_id or post.provider_item_id,
                    "published_at": post.published_at.isoformat() if post.published_at else None,
                    "identity_status": identity_status, "provenance_status": provenance_status,
                    "retrieval_provider": post.provider, "action": "DUPLICATE",
                    "rejection_reason": None,
                    "content_snippet": (post.text or "").strip()[:200],
                    "entry_id": None, "external_id": external_id,
                })
                continue

            # Track locally (merged into shared set after lock in concurrent mode)
            intra_run_seen_urls.add(canonical_url)
            if external_id:
                intra_run_seen_ids.add(external_id)
            if activity_id:
                intra_run_seen_ids.add(activity_id)
            if post.provider_item_id:
                intra_run_seen_ids.add(post.provider_item_id)

            pub_date_str = post.published_at.strftime("%Y-%m-%d") if post.published_at else "undated"
            title = f"LinkedIn — {author_name} — {pub_date_str}"

            max_chars = self.settings.LINKEDIN_MAX_POST_CHARS
            clean_content = (post.text or "").strip()
            if len(clean_content) > max_chars:
                clean_content = clean_content[:max_chars]

            content_hash = compute_ingestion_dedupe_hash(title, canonical_url, None)

            etype = str(job.entity_type).lower().strip()
            if etype in ("organization", "institution", "publication", "company"):
                author_type = "organization"
            elif etype == "person":
                author_type = "person"
            else:
                author_type = "organization"

            enriched_meta = dict(post.raw_metadata or {})
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
            counters["entries_created"] += 1
            items_detail.append({
                "entity_name": job.entity_name,
                "http_status": post.raw_metadata.get("http_status", 200),
                "records_returned": len(posts), "author_name": author_name,
                "author_profile_url": post.author_profile_url,
                "linkedin_post_url": post.linkedin_post_url,
                "activity_id": activity_id or post.provider_item_id,
                "published_at": post.published_at.isoformat() if post.published_at else None,
                "identity_status": identity_status, "provenance_status": provenance_status,
                "retrieval_provider": post.provider, "action": "CREATED",
                "rejection_reason": None, "content_snippet": clean_content[:200],
                "entry_id": str(entry.id), "external_id": external_id,
            })

        # Per-entity summary
        used_prov = self.fallback.provider_name if used_fallback_for_job else self.primary.provider_name
        per_entity.append({
            "entity": job.entity_name,
            "provider": used_prov,
            "posts": len(posts),
            "created": counters["entries_created"] - job_created_start,
            "duplicates": counters["duplicates"] - job_duplicates_start,
            "provenance_rejected": counters["provenance_rejected"] - job_provenance_rejected_start,
            "errors": 0,
            "timed_out_snapshots": 0,
            "provider_errors": 0,
        })

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

