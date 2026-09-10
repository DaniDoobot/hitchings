"""Service orchestrating the controlled backfill of newly integrated sources (Bloque 12G).

Coordinates discovery, idempotent persistence, and selective v6 AI analysis
for the three new sources:
1. Geradin Partners - EU Competition & Litigation
2. European Commission - Digital Markets Act
3. OECD - Competition Law and Policy

Guarantees:
- Strict idempotency: re-running produces 0 new entries and 0 duplicate analyses.
- Source sufficiency gate: saves all discovered entries (FULL, PARTIAL, INSUFFICIENT),
  but analyzes ONLY those deemed 'eligible' by IncrementalAnalysisPlanner.
- Matrix binding: analyzes exclusively with the active TrackingMatrix.
- Volume Guard (--max-new-entries): aborts before persistence if exceeded.
- Cost Guard (--max-analysis-calls): aborts before calling Gemini if exceeded.
- Failure isolation: errors in one source do not block processing of other sources.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.analysis import AnalysisCall, AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix
from app.providers.ai.base import BaseAIProvider
from app.providers.ai.gemini_api import GeminiAPIProvider
from app.providers.ai.mock import MockAIProvider
from app.providers.direct_web.registry import DirectWebAdapterRegistry
from app.services.analysis_pipeline_service import AnalysisPipelineService
from app.services.direct_web_ingestion_service import DirectWebIngestionService
from app.services.incremental_analysis_planner import IncrementalAnalysisPlanner
from app.services.ingestion_service import IngestionService
from app.services.source_sufficiency_service import (
    SourceSufficiencyLevel,
    SourceSufficiencyService,
)
from scripts.preview_source_discovery import (
    DMA_SOURCE_NAME,
    GERADIN_SOURCE_NAME,
    OECD_SOURCE_NAME,
    SourceDiscoveryPreviewService,
)

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class DBInventoryCounts(BaseModel):
    """Snapshot of database entity counts before and after backfill execution."""

    sources: int = 0
    entries: int = 0
    entry_analyses: int = 0
    analysis_calls: int = 0
    ingestion_runs: int = 0


class SourceBackfillResult(BaseModel):
    """Detailed summary of backfill execution for an individual source."""

    source_id: str
    source_name: str
    status: str = "success"  # "success", "failed", "skipped"
    discovered: int = 0
    duplicates: int = 0
    created: int = 0
    full_count: int = 0
    partial_count: int = 0
    insufficient_count: int = 0
    eligible: int = 0
    analyzed: int = 0
    analysis_failed: int = 0
    ingestion_failed: int = 0
    errors: list[str] = Field(default_factory=list)
    new_entry_ids: list[str] = Field(default_factory=list)


class NewSourcesBackfillReport(BaseModel):
    """Consolidated summary report of a controlled new sources backfill run."""

    run_id: str = Field(default_factory=lambda: f"bf-{uuid.uuid4().hex[:8]}")
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None
    duration_seconds: float = 0.0
    status: str = "completed"  # "completed", "dry_run", "aborted_max_new_entries", "aborted_max_analysis_calls", "failed"
    guard_triggered: Optional[str] = None
    is_dry_run: bool = True
    lookback_days: int = 90
    max_new_entries: int = 30
    max_analysis_calls: int = 15
    active_matrix_code: Optional[str] = None
    sources_processed: int = 0
    entries_created: int = 0
    duplicates: int = 0
    potential_analysis: int = 0
    analysis_completed: int = 0
    analysis_failed: int = 0
    per_source: list[SourceBackfillResult] = Field(default_factory=list)
    db_counts_before: Optional[DBInventoryCounts] = None
    db_counts_after: Optional[DBInventoryCounts] = None


def query_db_inventory_counts(db: Session) -> DBInventoryCounts:
    """Retrieve exact entity inventory counts from database."""
    return DBInventoryCounts(
        sources=db.scalar(select(func.count(Source.id))) or 0,
        entries=db.scalar(select(func.count(Entry.id))) or 0,
        entry_analyses=db.scalar(select(func.count(EntryAnalysis.id))) or 0,
        analysis_calls=db.scalar(select(func.count(AnalysisCall.id))) or 0,
        ingestion_runs=db.scalar(select(func.count(IngestionRun.id))) or 0,
    )


class NewSourcesBackfillService:
    """Service that coordinates idempotent ingestion, guards enforcement, and v6 analysis."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        ingestion_service: Optional[IngestionService] = None,
        direct_web_service: Optional[DirectWebIngestionService] = None,
        ai_provider: Optional[BaseAIProvider] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.ingestion_service = ingestion_service or IngestionService()
        dw_settings = self.settings.model_copy(update={"DIRECT_WEB_INGESTION_ENABLED": True})
        self.direct_web_service = direct_web_service or DirectWebIngestionService(settings=dw_settings)

        if ai_provider is not None:
            self.ai_provider = ai_provider
        else:
            provider_type = self.settings.ANALYSIS_PROVIDER.lower()
            if provider_type == "gemini_api":
                self.ai_provider = GeminiAPIProvider(settings=self.settings)
            elif provider_type == "mock":
                self.ai_provider = MockAIProvider()
            else:
                self.ai_provider = MockAIProvider()

    async def execute_backfill(
        self,
        db: Session,
        lookback_days: int = 90,
        confirm_real_calls: bool = False,
        source_filter: Optional[str] = None,
        max_new_entries: int = 30,
        max_analysis_calls: int = 15,
        sync_client: Optional[httpx.Client] = None,
        async_client: Optional[httpx.AsyncClient] = None,
    ) -> NewSourcesBackfillReport:
        """Execute or dry-run the backfill of new sources with circuit-breaker guards."""
        start_time = utc_now()
        report = NewSourcesBackfillReport(
            is_dry_run=not confirm_real_calls,
            lookback_days=lookback_days,
            max_new_entries=max_new_entries,
            max_analysis_calls=max_analysis_calls,
            started_at=start_time,
        )

        # 1. Capture inventory counts before execution
        report.db_counts_before = query_db_inventory_counts(db)

        # 2. Resolve active TrackingMatrix
        active_matrix = (
            db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
        )
        if not active_matrix:
            report.status = "failed"
            report.guard_triggered = "Active TrackingMatrix not found in database"
            logger.error("[Backfill] Active TrackingMatrix not found. Aborting.")
            report.finished_at = utc_now()
            report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
            report.db_counts_after = query_db_inventory_counts(db)
            return report

        report.active_matrix_code = active_matrix.code

        # 3. Resolve target sources
        target_sources = self._resolve_sources(db=db, source_filter=source_filter)
        if not target_sources:
            logger.warning("[Backfill] No target sources resolved for filter '%s'", source_filter)
            report.status = "completed"
            report.finished_at = utc_now()
            report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
            report.db_counts_after = query_db_inventory_counts(db)
            return report

        # 4. DRY-RUN MODE: Zero HTTP calls, zero DB writes, zero Gemini calls
        if not confirm_real_calls:
            logger.info(
                "[Backfill DRY RUN] Configured sources: %s, matrix=%s, lookback=%dd, max_new=%d, max_calls=%d. "
                "0 HTTP calls, 0 DB writes, 0 Gemini calls executed.",
                [s.name for s in target_sources],
                active_matrix.code,
                lookback_days,
                max_new_entries,
                max_analysis_calls,
            )
            for s in target_sources:
                report.per_source.append(
                    SourceBackfillResult(
                        source_id=str(s.id),
                        source_name=s.name,
                        status="skipped",
                    )
                )
            report.status = "dry_run"
            report.finished_at = utc_now()
            report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
            report.db_counts_after = report.db_counts_before
            return report

        # 5. REAL EXECUTION: Step 5A — Pre-discovery Inspection & Circuit-Breaker Guards
        logger.info(
            "[Backfill] Running pre-discovery inspection for %d sources (lookback=%dd)...",
            len(target_sources),
            lookback_days,
        )
        preview_service = SourceDiscoveryPreviewService(db=db, now=start_time)
        preview_report = await preview_service.run_preview_async(
            lookback_days=lookback_days,
            source_filter=source_filter,
            sync_client=sync_client,
            async_client=async_client,
        )

        total_new_candidates = preview_report.total_new
        potential_analyses = preview_report.potential_gemini_analyses

        logger.info(
            "[Backfill] Discovery pre-check complete: new_candidates=%d (limit=%d), potential_analyses=%d (limit=%d)",
            total_new_candidates,
            max_new_entries,
            potential_analyses,
            max_analysis_calls,
        )

        # GUARD 9: Volume Guard — Abort BEFORE persistence if new entries exceed limit
        if total_new_candidates > max_new_entries:
            msg = (
                f"Volume guard triggered: discovered {total_new_candidates} new candidates, "
                f"which exceeds --max-new-entries limit of {max_new_entries}. Aborting before persistence!"
            )
            logger.warning("[Backfill GUARD] %s", msg)
            report.status = "aborted_max_new_entries"
            report.guard_triggered = msg
            report.finished_at = utc_now()
            report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
            report.db_counts_after = query_db_inventory_counts(db)
            return report

        # GUARD 8: Cost Guard — Abort BEFORE calling Gemini if eligible analyses exceed limit
        if potential_analyses > max_analysis_calls:
            msg = (
                f"Cost guard triggered: discovered {potential_analyses} eligible analyses, "
                f"which exceeds --max-analysis-calls limit of {max_analysis_calls}. Aborting before calling Gemini!"
            )
            logger.warning("[Backfill GUARD] %s", msg)
            report.status = "aborted_max_analysis_calls"
            report.guard_triggered = msg
            report.finished_at = utc_now()
            report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
            report.db_counts_after = query_db_inventory_counts(db)
            return report

        # 6. Step 5B — Idempotent Persistence per Source with Failure Isolation
        all_created_entries: list[Entry] = []
        preview_summaries_by_name = {
            s.source_name: s for s in preview_report.sources_summaries
        }

        for source in target_sources:
            source_res = SourceBackfillResult(
                source_id=str(source.id),
                source_name=source.name,
            )
            source_start = utc_now()
            try:
                # Update config with current lookback_days for this run
                source.config = {**(source.config or {}), "lookback_days": lookback_days}

                # 6.1 Process Geradin Partners via DirectWebIngestionService
                if DirectWebAdapterRegistry.has_adapter_for_source(source):
                    dw_report = self.direct_web_service.execute_ingestion(
                        db=db,
                        sources=[source],
                        confirm_real_calls=True,
                        client=sync_client,
                    )
                    source_res.discovered = dw_report.total_discovered
                    source_res.duplicates = dw_report.total_duplicates
                    source_res.created = dw_report.total_created
                    if dw_report.total_failed > 0:
                        source_res.ingestion_failed = dw_report.total_failed
                        for r in dw_report.results_by_source:
                            source_res.errors.extend(r.errors)

                # 6.2 Process native extractors (DMA and OECD) via IngestionService
                elif source.provider == "native":
                    ingest_res = await self.ingestion_service.ingest_source(
                        source_id=source.id,
                        db=db,
                        client=async_client,
                    )
                    source_res.discovered = getattr(
                        ingest_res, "fetched", getattr(ingest_res, "items_extracted", 0)
                    )
                    source_res.duplicates = getattr(
                        ingest_res, "duplicates", getattr(ingest_res, "duplicates_skipped", 0)
                    )
                    source_res.created = getattr(
                        ingest_res, "created", getattr(ingest_res, "items_created", 0)
                    )
                    err_msg = getattr(ingest_res, "error_message", None)
                    if err_msg:
                        source_res.errors.append(err_msg)
                        source_res.ingestion_failed += 1

                else:
                    source_res.status = "skipped"
                    source_res.errors.append(f"unsupported_provider: {source.provider}")

                # Query entries newly created for this source during this run
                if source_res.created > 0:
                    created_entries = (
                        db.query(Entry)
                        .filter(
                            Entry.source_id == source.id,
                            Entry.captured_at >= source_start,
                        )
                        .all()
                    )
                    source_res.new_entry_ids = [str(e.id) for e in created_entries]
                    all_created_entries.extend(created_entries)

                    # Assess sufficiency of newly persisted entries
                    for entry in created_entries:
                        suff = SourceSufficiencyService.assess(entry)
                        if suff.level == SourceSufficiencyLevel.FULL:
                            source_res.full_count += 1
                        elif suff.level == SourceSufficiencyLevel.PARTIAL:
                            source_res.partial_count += 1
                        else:
                            source_res.insufficient_count += 1
                else:
                    # If 0 created (e.g. second idempotent run), copy discovery breakdown from preview
                    p_summary = preview_summaries_by_name.get(source.name)
                    if p_summary:
                        source_res.discovered = p_summary.discovered_total
                        source_res.duplicates = p_summary.duplicates
                        source_res.full_count = p_summary.full_count
                        source_res.partial_count = p_summary.partial_count
                        source_res.insufficient_count = p_summary.insufficient_count

                if source_res.ingestion_failed > 0:
                    source_res.status = "partial" if source_res.created > 0 else "failed"
                else:
                    source_res.status = "success"

            except Exception as exc:
                logger.error(
                    "[Backfill] Error ingesting source '%s': %s", source.name, exc, exc_info=True
                )
                source_res.status = "failed"
                source_res.errors.append(str(exc))
                source_res.ingestion_failed += 1

            report.per_source.append(source_res)
            report.sources_processed += 1
            report.entries_created += source_res.created
            report.duplicates += source_res.duplicates

        # 7. Step 5C — Analysis Planning & AI Execution
        planner = IncrementalAnalysisPlanner(db)
        eligible_entries: list[Entry] = []

        for entry in all_created_entries:
            cand = planner.evaluate_entry(entry)
            if cand.reason == "eligible":
                eligible_entries.append(entry)
                # Map back to source summary
                for s_res in report.per_source:
                    if s_res.source_id == str(entry.source_id):
                        s_res.eligible += 1
                        break

        report.potential_analysis = len(eligible_entries)

        # Secondary runtime cost guard check against actually persisted eligible entries
        if len(eligible_entries) > max_analysis_calls:
            msg = (
                f"Cost guard triggered at analysis dispatch: {len(eligible_entries)} eligible entries "
                f"exceeds limit {max_analysis_calls}. Aborting before calling Gemini!"
            )
            logger.warning("[Backfill GUARD] %s", msg)
            report.status = "aborted_max_analysis_calls"
            report.guard_triggered = msg
            report.finished_at = utc_now()
            report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
            report.db_counts_after = query_db_inventory_counts(db)
            return report

        # Execute v6 AI analysis on eligible entries
        if eligible_entries:
            logger.info(
                "[Backfill] Dispatching v6 analysis for %d eligible entries using active matrix '%s'...",
                len(eligible_entries),
                active_matrix.code,
            )

            # Resolve active v6 prompts
            triage_prompt = (
                db.query(AnalysisPromptVersion)
                .filter(
                    AnalysisPromptVersion.code == "observatory_triage",
                    AnalysisPromptVersion.version == 6,
                    AnalysisPromptVersion.active.is_(True),
                )
                .first()
            )
            deep_prompt = (
                db.query(AnalysisPromptVersion)
                .filter(
                    AnalysisPromptVersion.code == "observatory_deep_analysis",
                    AnalysisPromptVersion.version == 6,
                    AnalysisPromptVersion.active.is_(True),
                )
                .first()
            )

            if not triage_prompt or not deep_prompt:
                err_p = "Active v6 triage or deep prompts not found in database"
                logger.error("[Backfill] %s", err_p)
                report.guard_triggered = err_p
                report.status = "failed"
                report.finished_at = utc_now()
                report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
                report.db_counts_after = query_db_inventory_counts(db)
                return report

            pipeline = AnalysisPipelineService(provider=self.ai_provider)

            for entry in eligible_entries:
                matching_s_res = next(
                    (s for s in report.per_source if s.source_id == str(entry.source_id)),
                    None,
                )
                try:
                    await pipeline.run_pipeline(
                        entry_id=entry.id,
                        matrix_id=active_matrix.id,
                        triage_prompt_id=triage_prompt.id,
                        deep_prompt_id=deep_prompt.id,
                        db=db,
                        pipeline_version="v6",
                        extra_call_metadata={
                            "run_id": report.run_id,
                            "run_type": "new_sources_backfill",
                            "orchestrator": "NewSourcesBackfillService",
                        },
                    )
                    report.analysis_completed += 1
                    if matching_s_res:
                        matching_s_res.analyzed += 1

                except Exception as a_exc:
                    logger.exception(
                        "[Backfill] Analysis failed for entry id=%s ('%s'): %s",
                        entry.id,
                        entry.title,
                        a_exc,
                    )
                    report.analysis_failed += 1
                    if matching_s_res:
                        matching_s_res.analysis_failed += 1
                        matching_s_res.errors.append(f"Analysis error: {a_exc}")

        # 8. Step 5D — Finalize Report
        report.finished_at = utc_now()
        report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
        report.status = "completed"
        report.db_counts_after = query_db_inventory_counts(db)

        logger.info(
            "[Backfill] Finished run %s in %.2fs. Created=%d, Dupes=%d, Eligible=%d, Analyzed=%d, Failed=%d",
            report.run_id,
            report.duration_seconds,
            report.entries_created,
            report.duplicates,
            report.potential_analysis,
            report.analysis_completed,
            report.analysis_failed,
        )

        return report

    def _resolve_sources(
        self, db: Session, source_filter: Optional[str] = None
    ) -> list[Source]:
        """Resolve candidate new Sources from database, supporting exact name and shortcut filters."""
        all_new_names = [GERADIN_SOURCE_NAME, DMA_SOURCE_NAME, OECD_SOURCE_NAME]
        resolved: list[Source] = []

        for name in all_new_names:
            if source_filter:
                norm_filter = source_filter.strip().lower()
                if norm_filter in {"geradin", "geradin partners"}:
                    if name != GERADIN_SOURCE_NAME:
                        continue
                elif norm_filter in {"dma", "digital markets act"}:
                    if name != DMA_SOURCE_NAME:
                        continue
                elif norm_filter in {"oecd", "competition"}:
                    if name != OECD_SOURCE_NAME:
                        continue
                else:
                    if norm_filter not in name.lower():
                        continue

            source = (
                db.execute(select(Source).where(Source.name == name).limit(1))
                .scalar_one_or_none()
            )
            if source:
                resolved.append(source)

        return resolved
