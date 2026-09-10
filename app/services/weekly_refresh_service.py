"""Weekly refresh service orchestrating isolated source ingestion, deduplication, and AI analysis."""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.advisory_lock import RefreshAdvisoryLock
from app.core.config import Settings, get_settings
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix
from app.providers.direct_web.registry import DirectWebAdapterRegistry
from app.services.direct_web_ingestion_service import DirectWebIngestionService
from app.services.google_news_ingestion_service import GoogleNewsIngestionService
from app.services.incremental_analysis_planner import (
    IncrementalAnalysisPlan,
    IncrementalAnalysisPlanner,
)
from app.services.incremental_analysis_service import IncrementalAnalysisService
from app.services.ingestion_service import IngestionService
from app.services.linkedin_ingestion_service import LinkedInIngestionService

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class SourceRefreshDetail(BaseModel):
    """Detailed summary of the refresh run for an individual source."""

    source_id: str
    source_name: str
    source_type: str
    status: str = "success"  # "success", "partial", "skipped", "failed"
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_seconds: float = 0.0
    found: int = 0
    new_entries: int = 0
    duplicates: int = 0
    analyzed: int = 0
    skipped_analysis: int = 0
    errors: list[str] = Field(default_factory=list)
    new_entry_ids: list[str] = Field(default_factory=list)


class WeeklyRefreshReport(BaseModel):
    """Consolidated summary report of a weekly refresh execution."""

    run_id: str = Field(default_factory=lambda: f"wr-{uuid.uuid4().hex[:8]}")
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None
    duration_seconds: float = 0.0
    status: str = "completed"  # "completed", "already_running", "failed"
    is_dry_run: bool = True
    lookback_days: int = 8
    active_matrix_code: Optional[str] = None
    sources_attempted: int = 0
    sources_successful: int = 0
    sources_failed: int = 0
    sources_skipped: int = 0
    total_found: int = 0
    total_new_entries: int = 0
    total_duplicates: int = 0
    total_analyzed: int = 0
    total_skipped_analysis: int = 0
    total_errors: int = 0
    per_source: list[SourceRefreshDetail] = Field(default_factory=list)

    def summary_text(self) -> str:
        """Produce clean human-readable summary text."""
        return (
            f"Weekly refresh completed\n\n"
            f"Sources attempted: {self.sources_attempted}\n"
            f"Sources successful: {self.sources_successful}\n"
            f"Sources failed: {self.sources_failed}\n"
            f"Sources skipped: {self.sources_skipped}\n\n"
            f"Found: {self.total_found}\n"
            f"New entries: {self.total_new_entries}\n"
            f"Duplicates: {self.total_duplicates}\n"
            f"Analyzed: {self.total_analyzed}\n"
            f"Skipped analysis: {self.total_skipped_analysis}\n"
            f"Errors: {self.total_errors}"
        )


class WeeklyRefreshService:
    """Service that orchestrates the weekly refresh of all active sources."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        ingestion_service: Optional[IngestionService] = None,
        direct_web_service: Optional[DirectWebIngestionService] = None,
        google_news_service: Optional[GoogleNewsIngestionService] = None,
        linkedin_service: Optional[LinkedInIngestionService] = None,
        incremental_analysis_service: Optional[IncrementalAnalysisService] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.ingestion_service = ingestion_service or IngestionService()
        self.direct_web_service = direct_web_service or DirectWebIngestionService()
        self.google_news_service = google_news_service or GoogleNewsIngestionService()
        self.linkedin_service = linkedin_service or LinkedInIngestionService()
        self.analysis_service = incremental_analysis_service or IncrementalAnalysisService(
            settings=self.settings
        )

    def run_weekly_refresh(
        self,
        db: Session,
        lookback_days: Optional[int] = None,
        confirm_real_calls: bool = False,
        sources_filter: Optional[list[str]] = None,
    ) -> WeeklyRefreshReport:
        """Execute the weekly refresh over all active sources with strict concurrency and failure isolation."""
        effective_lookback = (
            lookback_days
            if lookback_days is not None
            else self.settings.WEEKLY_REFRESH_LOOKBACK_DAYS
        )

        lock = RefreshAdvisoryLock(db)
        if not lock.acquire():
            logger.warning("[WeeklyRefresh] Another weekly refresh instance is currently running. Exiting cleanly.")
            return WeeklyRefreshReport(
                status="already_running",
                is_dry_run=not confirm_real_calls,
                lookback_days=effective_lookback,
                finished_at=utc_now(),
            )

        report = WeeklyRefreshReport(
            is_dry_run=not confirm_real_calls,
            lookback_days=effective_lookback,
        )

        try:
            # 1. Resolve currently active TrackingMatrix
            active_matrix = (
                db.query(TrackingMatrix)
                .filter(TrackingMatrix.status == "active")
                .first()
            )
            report.active_matrix_code = active_matrix.code if active_matrix else None
            logger.info(
                "[WeeklyRefresh] Starting run %s (matrix=%s, lookback=%d days, real_calls=%s)",
                report.run_id,
                report.active_matrix_code,
                effective_lookback,
                confirm_real_calls,
            )

            # 2. Fetch active candidate sources
            query = db.query(Source).filter(Source.active.is_(True))
            all_active_sources = query.all()

            if sources_filter:
                filter_set = {s.lower().strip() for s in sources_filter}
                candidate_sources = [
                    s for s in all_active_sources
                    if str(s.id).lower() in filter_set or s.name.lower() in filter_set
                ]
            else:
                candidate_sources = all_active_sources

            # 3. Process each source in isolation
            all_newly_created_entry_ids: list[uuid.UUID] = []

            for source in candidate_sources:
                detail = self._process_source(
                    db=db,
                    source=source,
                    lookback_days=effective_lookback,
                    confirm_real_calls=confirm_real_calls,
                )
                report.per_source.append(detail)
                report.sources_attempted += 1

                if detail.status == "success":
                    report.sources_successful += 1
                elif detail.status == "skipped":
                    report.sources_skipped += 1
                elif detail.status == "failed":
                    report.sources_failed += 1
                elif detail.status == "partial":
                    report.sources_successful += 1

                report.total_found += detail.found
                report.total_new_entries += detail.new_entries
                report.total_duplicates += detail.duplicates
                report.total_errors += len(detail.errors)

                for e_id in detail.new_entry_ids:
                    all_newly_created_entry_ids.append(uuid.UUID(e_id))

            # 4. Analyze ONLY newly created entries with sufficient content
            if all_newly_created_entry_ids and confirm_real_calls:
                analyzed_cnt, skipped_cnt, err_cnt = self._analyze_new_entries(
                    db=db,
                    entry_ids=all_newly_created_entry_ids,
                    confirm_real_calls=confirm_real_calls,
                )
                report.total_analyzed = analyzed_cnt
                report.total_skipped_analysis = skipped_cnt
                report.total_errors += err_cnt
            elif all_newly_created_entry_ids:
                # Dry run evaluation of new entries
                planner = IncrementalAnalysisPlanner(db)
                entries = db.query(Entry).filter(Entry.id.in_(all_newly_created_entry_ids)).all()
                for e in entries:
                    cand = planner.evaluate_entry(e)
                    if cand.reason == "eligible":
                        report.total_analyzed += 1
                    else:
                        report.total_skipped_analysis += 1

            report.finished_at = utc_now()
            report.duration_seconds = round(
                (report.finished_at - report.started_at).total_seconds(), 2
            )
            report.status = "completed"

            logger.info(
                "[WeeklyRefresh] Finished run %s in %.2fs. Found=%d, New=%d, Dupes=%d, Analyzed=%d",
                report.run_id,
                report.duration_seconds,
                report.total_found,
                report.total_new_entries,
                report.total_duplicates,
                report.total_analyzed,
            )

        except Exception as exc:
            logger.error("[WeeklyRefresh] Critical error in weekly refresh: %s", exc, exc_info=True)
            report.status = "failed"
            report.finished_at = utc_now()
            report.duration_seconds = round(
                (report.finished_at - report.started_at).total_seconds(), 2
            )
            report.total_errors += 1
        finally:
            lock.release()

        return report

    def _process_source(
        self,
        db: Session,
        source: Source,
        lookback_days: int,
        confirm_real_calls: bool,
    ) -> SourceRefreshDetail:
        """Process a single source with error isolation."""
        start_time = utc_now()
        detail = SourceRefreshDetail(
            source_id=str(source.id),
            source_name=source.name,
            source_type=source.type.value if hasattr(source.type, "value") else str(source.type),
            started_at=start_time,
        )

        try:
            # 1. LINKEDIN
            if source.type == SourceType.LINKEDIN:
                has_provider = bool(
                    self.settings.LINKEDIN_DISCOVERY_ENABLED
                    and (self.settings.brightdata_token or self.settings.apify_token)
                )
                if not has_provider:
                    logger.info("[WeeklyRefresh] Source '%s' skipped (provider_not_configured)", source.name)
                    detail.status = "skipped"
                    detail.errors.append("provider_not_configured")
                    detail.finished_at = utc_now()
                    detail.duration_seconds = round((detail.finished_at - start_time).total_seconds(), 2)
                    return detail

                report_li = self.linkedin_service.execute_discovery(
                    db=db,
                    confirm_real_calls=confirm_real_calls,
                )
                detail.found = report_li.posts_seen
                detail.new_entries = report_li.entries_created
                detail.duplicates = report_li.duplicates
                detail.errors.extend(report_li.errors)
                detail.status = "failed" if report_li.failed_jobs > 0 and report_li.entries_created == 0 else "success"

            # 2. GOOGLE NEWS
            elif source.type == SourceType.GOOGLE_NEWS:
                report_gn = self.google_news_service.execute_ingestion(
                    db=db,
                    confirm_real_calls=confirm_real_calls,
                )
                detail.found = report_gn.items_seen
                detail.new_entries = report_gn.entries_created
                detail.duplicates = report_gn.duplicates_count
                detail.status = report_gn.status
                if report_gn.failed_queries > 0:
                    detail.errors.append(f"{report_gn.failed_queries} Google News queries failed")

            # 3. DIRECT WEB BLOGS (Kluwer, Chillin'Competition, Almacén de Derecho)
            elif DirectWebAdapterRegistry.has_adapter_for_source(source):
                report_dw = self.direct_web_service.execute_ingestion(
                    db=db,
                    sources=[source],
                    confirm_real_calls=confirm_real_calls,
                )
                detail.found = report_dw.total_discovered
                detail.new_entries = report_dw.total_created
                detail.duplicates = report_dw.total_duplicates
                detail.status = "failed" if report_dw.total_failed > 0 and report_dw.total_created == 0 else "success"
                for res in report_dw.results_by_source:
                    detail.errors.extend(res.errors)

            # 4. NATIVE EXTRACTORS (CNMC, EC, CAT, CURIA, generic RSS/Web)
            elif source.provider == "native":
                if not confirm_real_calls:
                    # Dry-run for native providers: report readiness without external calls
                    logger.info("[WeeklyRefresh] Source '%s' native dry-run preview (0 HTTP, 0 DB)", source.name)
                    detail.status = "success"
                else:
                    res_or_coro = self.ingestion_service.ingest_source(source.id, db)
                    if inspect.isawaitable(res_or_coro):
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = None

                        if loop and loop.is_running():
                            import concurrent.futures
                            with concurrent.futures.ThreadPoolExecutor() as executor:
                                future = executor.submit(lambda: asyncio.run(res_or_coro))
                                ingest_res = future.result()
                        else:
                            ingest_res = asyncio.run(res_or_coro)
                    else:
                        ingest_res = res_or_coro

                    detail.found = getattr(ingest_res, "fetched", getattr(ingest_res, "items_extracted", 0))
                    detail.new_entries = getattr(ingest_res, "created", getattr(ingest_res, "items_created", 0))
                    detail.duplicates = getattr(ingest_res, "duplicates", getattr(ingest_res, "duplicates_skipped", 0))
                    detail.status = getattr(ingest_res, "status", "success")
                    err = getattr(ingest_res, "error_message", None)
                    if err:
                        detail.errors.append(err)

            else:
                logger.warning("[WeeklyRefresh] Source '%s' has unsupported provider '%s'", source.name, source.provider)
                detail.status = "skipped"
                detail.errors.append(f"unsupported_provider: {source.provider}")

            # Capture IDs of entries newly created during this source's execution
            if detail.new_entries > 0:
                new_entries_query = (
                    db.query(Entry.id)
                    .filter(
                        Entry.source_id == source.id,
                        Entry.captured_at >= start_time,
                    )
                    .all()
                )
                detail.new_entry_ids = [str(r[0]) for r in new_entries_query]

        except Exception as exc:
            logger.error("[WeeklyRefresh] Error processing source '%s': %s", source.name, exc, exc_info=True)
            detail.status = "failed"
            detail.errors.append(str(exc))
        finally:
            detail.finished_at = utc_now()
            detail.duration_seconds = round((detail.finished_at - start_time).total_seconds(), 2)

        return detail

    def _analyze_new_entries(
        self,
        db: Session,
        entry_ids: list[uuid.UUID],
        confirm_real_calls: bool,
    ) -> tuple[int, int, int]:
        """Evaluate and incrementally analyze only genuinely new entries."""
        planner = IncrementalAnalysisPlanner(db)
        entries = db.query(Entry).filter(Entry.id.in_(entry_ids)).all()

        eligible_entries: list[Entry] = []
        skipped_count = 0

        for entry in entries:
            candidate = planner.evaluate_entry(entry)
            if candidate.reason == "eligible":
                eligible_entries.append(entry)
            else:
                skipped_count += 1

        if not eligible_entries:
            logger.info("[WeeklyRefresh] 0 new entries eligible for analysis (skipped=%d)", skipped_count)
            return 0, skipped_count, 0

        logger.info(
            "[WeeklyRefresh] Planning analysis for %d eligible new entries (skipped=%d)",
            len(eligible_entries),
            skipped_count,
        )

        candidates = [planner.evaluate_entry(e) for e in eligible_entries]
        plan = IncrementalAnalysisPlan(
            total_entries_inspected=len(entries),
            eligible_count=len(eligible_entries),
            candidates=candidates,
        )

        report = self.analysis_service.execute_incremental_run(
            db=db,
            plan=plan,
            confirm_real_calls=confirm_real_calls,
        )

        return report.completed, skipped_count + report.skipped_budget, report.failed
