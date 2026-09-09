"""Incremental Analysis Orchestration Service for HITCHINGS (Bloque 9D).

Orchestrates the incremental execution of the v6 analysis pipeline over
eligible entries with full source sufficiency, strictly enforcing budget caps,
failure isolation, and idempotency.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.analysis import AnalysisCall, AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.tracking import TrackingMatrix
from app.providers.ai.base import BaseAIProvider
from app.providers.ai.gemini_api import GeminiAPIProvider
from app.providers.ai.mock import MockAIProvider
from app.services.analysis_pipeline_service import (
    AnalysisPipelineService,
    PipelineBudgetExceededError,
    PipelineStopRequestedError,
)
from app.services.incremental_analysis_planner import (
    AnalysisCandidate,
    IncrementalAnalysisPlan,
    IncrementalAnalysisPlanner,
)

logger = logging.getLogger(__name__)


class IncrementalAnalysisReport(BaseModel):
    """Detailed report of an incremental analysis execution."""

    run_id: str
    is_dry_run: bool = True
    planned: int = 0
    attempted: int = 0
    completed: int = 0
    failed: int = 0
    triage_calls: int = 0
    deep_calls: int = 0
    relevant: int = 0
    uncertain: int = 0
    not_relevant: int = 0
    skipped_budget: int = 0
    estimated_cost: float = 0.0
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    details: list[dict[str, Any]] = Field(default_factory=list)


def calculate_stage_reservation(
    stage: str,
    entry: Entry,
    prompt: AnalysisPromptVersion,
    input_rate: float,
    output_rate: float,
) -> float:
    """Calculate conservative upper-bound cost for a stage call without calling LLMs."""
    content_chars = len(entry.content or "")
    title_chars = len(entry.title or "")
    capped_content_chars = min(content_chars, 120_000)
    input_chars = capped_content_chars + title_chars + 3_000

    est_input_tokens = int((input_chars / 2.0) * 1.20)
    config = prompt.config or {}
    default_out = 1024 if stage == "triage" else 8192
    max_output_tokens = config.get("max_output_tokens", default_out)

    input_cost = (est_input_tokens * input_rate) / 1_000_000.0
    output_cost = (max_output_tokens * output_rate) / 1_000_000.0
    return round(input_cost + output_cost, 6)


class IncrementalAnalysisService:
    """Service that orchestrates the incremental AI analysis over eligible backlog entries."""

    def __init__(
        self,
        provider: Optional[BaseAIProvider] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        if provider is not None:
            self.provider = provider
        else:
            if self.settings.ANALYSIS_PROVIDER == "gemini_api":
                self.provider = GeminiAPIProvider(settings=self.settings)
            elif self.settings.ANALYSIS_PROVIDER == "mock":
                self.provider = MockAIProvider()
            else:
                self.provider = MockAIProvider()

    async def execute_incremental_analysis(
        self,
        db: Session,
        confirm_real_calls: bool = False,
        limit: Optional[int] = None,
        max_estimated_cost_usd: Optional[float] = None,
        entry_id: Optional[uuid.UUID] = None,
        source_id: Optional[uuid.UUID] = None,
    ) -> IncrementalAnalysisReport:
        """Execute or preview incremental analysis of eligible entries."""
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        # 1. Plan candidates
        effective_limit = (
            limit
            if limit is not None
            else self.settings.INCREMENTAL_ANALYSIS_DEFAULT_LIMIT
        )
        planner = IncrementalAnalysisPlanner(db=db)
        plan: IncrementalAnalysisPlan = planner.plan(
            entry_id=entry_id,
            source_id=source_id,
            limit=effective_limit,
        )

        candidates = plan.candidates
        planned_count = len(candidates)

        max_cost = (
            max_estimated_cost_usd
            if max_estimated_cost_usd is not None
            else self.settings.INCREMENTAL_ANALYSIS_MAX_ESTIMATED_COST_USD
        )

        # 2. DRY-RUN MODE: Return planned overview with zero calls/writes
        if not confirm_real_calls:
            details = [
                {
                    "entry_id": str(c.entry_id),
                    "source_name": c.source_name,
                    "title": c.title,
                    "published_at": c.published_at.isoformat() if c.published_at else None,
                    "sufficiency": c.sufficiency,
                    "content_chars": c.content_chars,
                    "reason": c.reason,
                    "estimated_stage_plan": c.estimated_stage_plan,
                    "status": "planned",
                }
                for c in candidates
            ]
            return IncrementalAnalysisReport(
                run_id=run_id,
                is_dry_run=True,
                planned=planned_count,
                attempted=0,
                completed=0,
                failed=0,
                triage_calls=0,
                deep_calls=0,
                relevant=0,
                uncertain=0,
                not_relevant=0,
                skipped_budget=0,
                estimated_cost=0.0,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                details=details,
            )

        # 3. REAL EXECUTION MODE (--confirm-real-calls)
        logger.info(
            "Starting real incremental analysis run_id=%s planned=%d max_cost=$%.4f",
            run_id,
            planned_count,
            max_cost,
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
        if not triage_prompt:
            raise RuntimeError("Active observatory_triage:v6 prompt not found in database")

        deep_prompt = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_deep_analysis",
                AnalysisPromptVersion.version == 6,
                AnalysisPromptVersion.active.is_(True),
            )
            .first()
        )
        if not deep_prompt:
            raise RuntimeError("Active observatory_deep_analysis:v6 prompt not found in database")

        matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
        if not matrix:
            raise RuntimeError("Active TrackingMatrix not found in database")

        pipeline = AnalysisPipelineService(provider=self.provider)

        in_rate = self.settings.GEMINI_INPUT_USD_PER_MILLION_TOKENS
        out_rate = self.settings.GEMINI_OUTPUT_USD_PER_MILLION_TOKENS

        attempted = 0
        completed = 0
        failed = 0
        triage_calls = 0
        deep_calls = 0
        relevant_cnt = 0
        uncertain_cnt = 0
        not_relevant_cnt = 0
        skipped_budget = 0
        cumulative_cost = 0.0

        details: list[dict[str, Any]] = []

        for candidate in candidates:
            entry = db.get(Entry, candidate.entry_id)
            if not entry:
                continue

            # Hook for budget reservation checking before each stage
            def before_stage_hook(
                stage: str,
                hook_entry: Entry,
                prompt: AnalysisPromptVersion,
                ea: EntryAnalysis,
            ) -> None:
                nonlocal cumulative_cost
                reservation = calculate_stage_reservation(
                    stage=stage,
                    entry=hook_entry,
                    prompt=prompt,
                    input_rate=in_rate,
                    output_rate=out_rate,
                )
                if cumulative_cost + reservation > max_cost:
                    msg = (
                        f"Budget cap exceeded: current=${cumulative_cost:.4f} + "
                        f"reservation=${reservation:.4f} > limit=${max_cost:.4f}"
                    )
                    logger.warning("[BUDGET_GUARD] %s", msg)
                    raise PipelineBudgetExceededError(msg)

            extra_meta = {
                "run_id": run_id,
                "run_type": "incremental_analysis",
                "orchestrator": "IncrementalAnalysisService",
                "pipeline_version": "v6",
            }

            attempted += 1
            try:
                analysis = await pipeline.run_pipeline(
                    entry_id=entry.id,
                    matrix_id=matrix.id,
                    triage_prompt_id=triage_prompt.id,
                    deep_prompt_id=deep_prompt.id,
                    db=db,
                    pipeline_version="v6",
                    extra_call_metadata=extra_meta,
                    before_stage_hook=before_stage_hook,
                )

                # Fetch calls performed for this analysis
                calls = (
                    db.query(AnalysisCall)
                    .filter(AnalysisCall.entry_analysis_id == analysis.id)
                    .order_by(AnalysisCall.created_at.asc())
                    .all()
                )

                entry_cost = sum(float(c.estimated_cost_usd or 0.0) for c in calls)
                cumulative_cost += entry_cost

                t_call = next((c for c in calls if c.stage == "triage"), None)
                d_call = next((c for c in calls if c.stage == "deep_analysis"), None)
                has_deep = d_call is not None and d_call.status == "completed"

                if t_call:
                    triage_calls += 1
                if d_call:
                    deep_calls += 1

                if analysis.relevance_status == "relevant":
                    relevant_cnt += 1
                elif analysis.relevance_status == "uncertain":
                    uncertain_cnt += 1
                elif analysis.relevance_status == "not_relevant":
                    not_relevant_cnt += 1

                if analysis.status == "completed":
                    completed += 1
                else:
                    failed += 1

                primary_topic_name = None
                for t in (analysis.topics or []):
                    if t.is_primary:
                        primary_topic_name = getattr(t.topic, "code", None) if getattr(t, "topic", None) else str(t.topic_id)
                        break

                details.append({
                    "entry_id": str(entry.id),
                    "source_name": candidate.source_name,
                    "title": entry.title,
                    "analysis_id": str(analysis.id),
                    "status": analysis.status,
                    "relevance_status": analysis.relevance_status,
                    "relevance_score": analysis.relevance_score,
                    "confidence": analysis.confidence,
                    "has_deep": has_deep,
                    "topics_count": len(analysis.topics or []),
                    "primary_topic": primary_topic_name,
                    "key_points_count": len(analysis.key_points or []),
                    "reason": analysis.reason,
                    "estimated_cost_usd": round(entry_cost, 6),
                })

            except PipelineBudgetExceededError:
                logger.warning("Stopping incremental run due to budget guard on entry %s", candidate.entry_id)
                skipped_budget += 1
                details.append({
                    "entry_id": str(candidate.entry_id),
                    "source_name": candidate.source_name,
                    "title": candidate.title,
                    "status": "skipped_budget",
                    "error": "PipelineBudgetExceededError",
                })
                # Clean halt: do not process further entries
                break

            except Exception as exc:
                logger.exception("Incremental analysis failed on entry %s: %s", candidate.entry_id, exc)
                failed += 1
                details.append({
                    "entry_id": str(candidate.entry_id),
                    "source_name": candidate.source_name,
                    "title": candidate.title,
                    "status": "failed",
                    "error": str(exc),
                })
                # If error is an authentication/critical config failure, fail fast
                err_lower = str(exc).lower()
                if "api_key" in err_lower or "unauthorized" in err_lower or "auth" in err_lower:
                    logger.critical("Fatal authentication error encountered; stopping backlog.")
                    break

        finished_at = datetime.now(timezone.utc)
        return IncrementalAnalysisReport(
            run_id=run_id,
            is_dry_run=False,
            planned=planned_count,
            attempted=attempted,
            completed=completed,
            failed=failed,
            triage_calls=triage_calls,
            deep_calls=deep_calls,
            relevant=relevant_cnt,
            uncertain=uncertain_cnt,
            not_relevant=not_relevant_cnt,
            skipped_budget=skipped_budget,
            estimated_cost=round(cumulative_cost, 6),
            started_at=started_at,
            finished_at=finished_at,
            details=details,
        )
