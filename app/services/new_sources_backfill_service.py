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
from sqlalchemy import func, select, or_
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
from app.services.analysis_pipeline_service import (
    AnalysisPipelineService,
    PipelineBudgetExceededError,
)
from app.services.direct_web_ingestion_service import DirectWebIngestionService
from app.services.incremental_analysis_planner import IncrementalAnalysisPlanner
from app.services.ingestion_service import (
    IngestionService,
    IngestionVolumeLimitExceededError,
)
from app.services.source_sufficiency_service import (
    SourceSufficiencyLevel,
    SourceSufficiencyService,
)
from scripts.preview_source_discovery import (
    BUNDESKARTELLAMT_SOURCE_NAME,
    DMA_SOURCE_NAME,
    GERADIN_SOURCE_NAME,
    OECD_SOURCE_NAME,
    ReadOnlyDeduplicationInspector,
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
    max_analysis_entries: int = 15
    max_analysis_calls: int = 30
    actual_analysis_calls: int = 0
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

        self._ai_provider = ai_provider

    @property
    def ai_provider(self) -> BaseAIProvider:
        """Resolve active AI provider via injected provider or canonical AnalysisService factory."""
        if self._ai_provider is None:
            from app.services.analysis_service import AnalysisService
            self._ai_provider = AnalysisService(settings=self.settings).get_provider()
        return self._ai_provider

    @ai_provider.setter
    def ai_provider(self, provider: BaseAIProvider) -> None:
        self._ai_provider = provider
    async def _count_prospective_new_entries(
        self,
        target_sources: list[Source],
        db: Session,
        lookback_days: int,
        sync_client: Optional[httpx.Client] = None,
        async_client: Optional[httpx.AsyncClient] = None,
    ) -> tuple[int, dict[str, int]]:
        """Count prospective new entries across target sources before persisting, protecting against race conditions."""
        cutoff_dt = utc_now() - timedelta(days=lookback_days)
        total_new = 0
        per_source_counts: dict[str, int] = {}

        for source in target_sources:
            new_for_source = 0
            if DirectWebAdapterRegistry.has_adapter_for_source(source):
                adapter = DirectWebAdapterRegistry.get_adapter_for_source(source)
                try:
                    items = adapter.discover(source, client=sync_client)
                    for it in items:
                        if it.published_at and it.published_at < cutoff_dt:
                            continue
                        dedup = ReadOnlyDeduplicationInspector.check_geradin_item(
                            db=db,
                            source_id=source.id,
                            url=it.url,
                            canonical_url=it.url,
                            title=it.title,
                            excerpt=it.excerpt,
                        )
                        if not dedup.is_duplicate:
                            new_for_source += 1
                except Exception as exc:
                    logger.warning("[Backfill] Prospective check discovery error for %s: %s", source.name, exc)

            elif source.provider == "native":
                provider = self.ingestion_service.get_provider(source.provider)
                try:
                    import inspect
                    sig = inspect.signature(provider.fetch_entries)
                    if "client" in sig.parameters:
                        raw_entries = await provider.fetch_entries(source, client=async_client)
                    else:
                        raw_entries = await provider.fetch_entries(source)

                    for raw in raw_entries:
                        if raw.published_at and raw.published_at < cutoff_dt:
                            continue
                        if "digital markets act" in source.name.lower() or "dma" in source.name.lower():
                            dedup = ReadOnlyDeduplicationInspector.check_dma_item(
                                db=db,
                                source_id=source.id,
                                raw=raw,
                            )
                        elif "oecd" in source.name.lower():
                            dedup = ReadOnlyDeduplicationInspector.check_oecd_item(
                                db=db,
                                source_id=source.id,
                                raw=raw,
                            )
                        elif "bundeskartellamt" in source.name.lower() or "bkart" in source.name.lower():
                            dedup = ReadOnlyDeduplicationInspector.check_bundeskartellamt_item(
                                db=db,
                                source_id=source.id,
                                raw=raw,
                            )
                        else:
                            from app.services.analysis_service import compute_content_hash
                            c_hash = compute_content_hash(raw.title, raw.url, raw.excerpt)
                            existing = db.execute(
                                select(Entry.id).where(
                                    Entry.source_id == source.id,
                                    or_(
                                        Entry.url == raw.url,
                                        Entry.canonical_url == raw.url,
                                        Entry.content_hash == c_hash,
                                    ),
                                ).limit(1)
                            ).scalar_one_or_none()
                            dedup = type("DedupRes", (), {"is_duplicate": existing is not None})()

                        if not dedup.is_duplicate:
                            new_for_source += 1
                except Exception as exc:
                    logger.warning("[Backfill] Prospective check fetch error for %s: %s", source.name, exc)

            per_source_counts[source.name] = new_for_source
            total_new += new_for_source

        return total_new, per_source_counts

    async def execute_backfill(
        self,
        db: Session,
        lookback_days: int = 90,
        confirm_real_calls: bool = False,
        source_filter: Optional[str] = None,
        max_new_entries: int = 30,
        max_analysis_entries: int = 15,
        max_analysis_calls: int = 30,
        sync_client: Optional[httpx.Client] = None,
        async_client: Optional[httpx.AsyncClient] = None,
    ) -> NewSourcesBackfillReport:
        """Execute or dry-run the backfill of new sources with circuit-breaker guards."""
        start_time = utc_now()
        effective_max_analysis_entries = min(max_analysis_entries, max_analysis_calls)
        report = NewSourcesBackfillReport(
            is_dry_run=not confirm_real_calls,
            lookback_days=lookback_days,
            max_new_entries=max_new_entries,
            max_analysis_entries=max_analysis_entries,
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
                "[Backfill DRY RUN] Configured sources: %s, matrix=%s, lookback=%dd, max_new=%d, max_analysis_entries=%d, max_calls=%d. "
                "0 HTTP calls, 0 DB writes, 0 Gemini calls executed.",
                [s.name for s in target_sources],
                active_matrix.code,
                lookback_days,
                max_new_entries,
                max_analysis_entries,
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
            effective_max_analysis_entries,
        )

        # GUARD 9: Volume Guard — Abort BEFORE persistence if preview new entries exceed limit
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

        # GUARD 8: Cost Guard — Abort BEFORE calling Gemini if preview eligible analyses exceed limit
        if potential_analyses > effective_max_analysis_entries:
            msg = (
                f"Cost guard triggered: discovered {potential_analyses} eligible analyses, "
                f"which exceeds --max-analysis-calls limit of {effective_max_analysis_entries}. Aborting before calling Gemini!"
            )
            logger.warning("[Backfill GUARD] %s", msg)
            report.status = "aborted_max_analysis_calls"
            report.guard_triggered = msg
            report.finished_at = utc_now()
            report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
            report.db_counts_after = query_db_inventory_counts(db)
            return report

        # Re-check real discovery prospective counts before persisting to prevent race conditions
        real_new_candidates, _ = await self._count_prospective_new_entries(
            target_sources=target_sources,
            db=db,
            lookback_days=lookback_days,
            sync_client=sync_client,
            async_client=async_client,
        )
        if real_new_candidates > max_new_entries:
            msg = (
                f"Real discovery volume guard triggered: discovered {real_new_candidates} new candidates, "
                f"which exceeds --max-new-entries limit of {max_new_entries}. Aborting before persistence!"
            )
            logger.warning("[Backfill GUARD] %s", msg)
            report.status = "aborted_max_new_entries"
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
            remaining_new_budget = max(0, max_new_entries - report.entries_created)
            if remaining_new_budget <= 0:
                msg = (
                    f"Ingestion volume guard triggered: entries created reached limit of {max_new_entries}. "
                    f"Aborting before persisting source '{source.name}'!"
                )
                logger.warning("[Backfill GUARD] %s", msg)
                report.status = "aborted_max_new_entries"
                report.guard_triggered = msg
                report.finished_at = utc_now()
                report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
                report.db_counts_after = query_db_inventory_counts(db)
                return report

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
                        max_new_entries=remaining_new_budget,
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
                        max_new_entries=remaining_new_budget,
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

            except IngestionVolumeLimitExceededError as vol_exc:
                msg = str(vol_exc)
                logger.warning("[Backfill GUARD] %s", msg)
                report.status = "aborted_max_new_entries"
                report.guard_triggered = msg
                # Rollback / remove any entries created earlier in this backfill run
                if all_created_entries:
                    for ent in all_created_entries:
                        db.delete(ent)
                    db.commit()
                    all_created_entries.clear()
                report.entries_created = 0
                report.finished_at = utc_now()
                report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
                report.db_counts_after = query_db_inventory_counts(db)
                return report
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

        # Post-persistence verification: Invariant check that total created <= max_new_entries
        if report.entries_created > max_new_entries:
            msg = (
                f"Post-ingestion volume guard triggered: created {report.entries_created} entries, "
                f"which exceeds --max-new-entries limit of {max_new_entries}. Rolling back!"
            )
            logger.warning("[Backfill GUARD] %s", msg)
            report.status = "aborted_max_new_entries"
            report.guard_triggered = msg
            for ent in all_created_entries:
                db.delete(ent)
            db.commit()
            all_created_entries.clear()
            report.entries_created = 0
            report.finished_at = utc_now()
            report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
            report.db_counts_after = query_db_inventory_counts(db)
            return report

        # 7. Step 5C — Analysis Planning & AI Execution
        cutoff_dt = start_time - timedelta(days=lookback_days)
        target_source_ids = [s.id for s in target_sources]

        candidate_entries_dict: dict[uuid.UUID, Entry] = {}
        for e in (
            db.query(Entry)
            .filter(
                Entry.source_id.in_(target_source_ids),
                or_(
                    Entry.published_at >= cutoff_dt,
                    Entry.captured_at >= cutoff_dt,
                ),
            )
            .all()
        ):
            candidate_entries_dict[e.id] = e

        for e in all_created_entries:
            candidate_entries_dict[e.id] = e

        candidate_entries = list(candidate_entries_dict.values())

        planner = IncrementalAnalysisPlanner(db)
        eligible_entries: list[Entry] = []

        for entry in candidate_entries:
            cand = planner.evaluate_entry(entry)
            if cand.reason == "eligible":
                eligible_entries.append(entry)
                # Map back to source summary
                for s_res in report.per_source:
                    if s_res.source_id == str(entry.source_id):
                        s_res.eligible += 1
                        break

        report.potential_analysis = len(eligible_entries)

        # Real plan cost guard check immediately before calling Gemini
        if len(eligible_entries) > effective_max_analysis_entries:
            msg = (
                f"Real analysis plan cost guard triggered: {len(eligible_entries)} eligible entries "
                f"exceeds limit of {effective_max_analysis_entries}. Aborting before calling Gemini!"
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

            try:
                provider = self.ai_provider
            except Exception as exc:
                err_p = f"Failed to initialize AI provider: {exc}"
                logger.error("[Backfill] %s", err_p)
                report.guard_triggered = err_p
                report.status = "failed"
                report.finished_at = utc_now()
                report.duration_seconds = round((report.finished_at - start_time).total_seconds(), 2)
                report.db_counts_after = query_db_inventory_counts(db)
                return report

            pipeline = AnalysisPipelineService(provider=provider)
            actual_calls_count = 0

            def budget_stage_hook(stage_name: str, entry: Entry, prompt: Any, analysis: Any):
                nonlocal actual_calls_count
                if actual_calls_count >= max_analysis_calls:
                    raise PipelineBudgetExceededError(
                        f"Actual provider calls reached limit of {max_analysis_calls}"
                    )
                actual_calls_count += 1

            for entry in eligible_entries:
                matching_s_res = next(
                    (s for s in report.per_source if s.source_id == str(entry.source_id)),
                    None,
                )
                try:
                    analysis = await pipeline.run_pipeline(
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
                        before_stage_hook=budget_stage_hook,
                    )
                    if analysis.status == "completed":
                        report.analysis_completed += 1
                        if matching_s_res:
                            matching_s_res.analyzed += 1
                    else:
                        logger.warning(
                            "[Backfill] Analysis returned non-completed status '%s' (reason=%s) for entry %s",
                            analysis.status,
                            analysis.reason,
                            entry.id,
                        )
                        report.analysis_failed += 1
                        if matching_s_res:
                            matching_s_res.analysis_failed += 1

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

            report.actual_analysis_calls = actual_calls_count

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
        all_new_names = [GERADIN_SOURCE_NAME, DMA_SOURCE_NAME, OECD_SOURCE_NAME, BUNDESKARTELLAMT_SOURCE_NAME]
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
                elif norm_filter in {"bundeskartellamt", "bkart"}:
                    if name != BUNDESKARTELLAMT_SOURCE_NAME:
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
