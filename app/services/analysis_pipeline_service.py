"""Two-stage AI analysis pipeline orchestrator for HITCHINGS.

Orchestrates:
  1. TRIAGE call  → relevance score, topics, reason
  2. DEEP ANALYSIS call (only when relevance_status == 'relevant')
     → summary, key_points

Both stages use the same provider instance injected at construction time.
All persistence is handled here; providers only return AIProviderResult.

Failure semantics:
- TRIAGE failure    → EntryAnalysis.status = 'failed', no deep call.
- DEEP failure      → triage data preserved, EntryAnalysis.status = 'failed'.
- Validation error  → same as provider failure (AnalysisValidationError caught here).
"""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session, joinedload

from app.models.analysis import (
    AnalysisCall,
    AnalysisPromptVersion,
    EntryAnalysis,
    EntryAnalysisTopic,
)
from app.models.entry import Entry
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.providers.ai.base import BaseAIProvider, AIProviderResult
from app.schemas.analysis import AIAnalysisTopicItem
from app.services.analysis_service import (
    AnalysisService,
    AnalysisValidationError,
    compute_analysis_input_hash,
    compute_content_hash,
    compute_matrix_snapshot,
)
from app.services.grounding_validator import (
    AnalysisGroundingError,
    validate_triage_evidence,
    validate_deep_evidence,
)
from app.services.source_sufficiency_service import assess_source_sufficiency

logger = logging.getLogger(__name__)


class PipelineBudgetExceededError(Exception):
    """Raised when a pipeline stage cannot proceed due to budget reservation limits."""
    pass


class PipelineStopRequestedError(Exception):
    """Raised when pipeline must halt before a stage due to SIGINT or user stop request."""
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisPipelineService:
    """Orchestrates the two-stage triage → deep analysis pipeline.

    Usage:
        pipeline = AnalysisPipelineService(provider=GeminiAPIProvider())
        analysis = await pipeline.run_pipeline(
            entry_id=...,
            matrix_id=...,
            triage_prompt_id=...,
            deep_prompt_id=...,
            db=db,
        )
    """

    def __init__(self, provider: BaseAIProvider) -> None:
        self._provider = provider
        self._svc = AnalysisService(provider=provider)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_pipeline(
        self,
        entry_id: uuid.UUID,
        matrix_id: uuid.UUID,
        triage_prompt_id: uuid.UUID,
        deep_prompt_id: uuid.UUID,
        db: Session,
        pipeline_version: str = "v2",
        extra_call_metadata: Optional[dict[str, Any]] = None,
        before_stage_hook: Optional[Any] = None,
    ) -> EntryAnalysis:
        """Execute the full triage → deep pipeline for a single Entry.

        Returns:
            EntryAnalysis — always committed, reflects final status.
        """
        extra_meta = extra_call_metadata or {}

        # 1. Resolve entities
        entry = db.get(Entry, entry_id)
        if entry is None:
            raise ValueError(f"Entry '{entry_id}' not found")

        # Eagerly load source so provider can access entry.source.name
        _ = entry.source

        matrix = (
            db.query(TrackingMatrix)
            .options(joinedload(TrackingMatrix.topics).joinedload(TrackingTopic.parent))
            .filter(TrackingMatrix.id == matrix_id)
            .first()
        )
        if matrix is None:
            raise ValueError(f"TrackingMatrix '{matrix_id}' not found")

        triage_prompt = db.get(AnalysisPromptVersion, triage_prompt_id)
        if triage_prompt is None:
            raise ValueError(f"AnalysisPromptVersion '{triage_prompt_id}' not found (triage)")
        if not triage_prompt.active:
            raise ValueError(f"Triage prompt '{triage_prompt.code}:v{triage_prompt.version}' is inactive")

        deep_prompt = db.get(AnalysisPromptVersion, deep_prompt_id)
        if deep_prompt is None:
            raise ValueError(f"AnalysisPromptVersion '{deep_prompt_id}' not found (deep)")
        if not deep_prompt.active:
            raise ValueError(f"Deep prompt '{deep_prompt.code}:v{deep_prompt.version}' is inactive")

        # 2. Assess source sufficiency & compute snapshot and hashes
        sufficiency_assessment = assess_source_sufficiency(entry)
        sufficiency_level = sufficiency_assessment.level.value
        signals = sufficiency_assessment.signals

        extra_meta.update({
            "source_sufficiency": sufficiency_level,
            "source_sufficiency_reason": sufficiency_assessment.reason,
            "content_source": signals.content_source,
            "full_text_available": signals.full_text_available,
            "official_summary_available": signals.official_summary_available,
            "pdf_available": signals.pdf_available,
            "content_chars": signals.content_chars,
        })

        snapshot, snapshot_hash = compute_matrix_snapshot(matrix)
        content_hash = compute_analysis_input_hash(entry)

        # 3. Create EntryAnalysis record
        started_at = utc_now()
        analysis = EntryAnalysis(
            entry_id=entry.id,
            matrix_id=matrix.id,
            pipeline_version=pipeline_version,
            status="pending",
            entry_content_hash=content_hash,
            matrix_snapshot=snapshot,
            matrix_snapshot_hash=snapshot_hash,
            started_at=started_at,
        )
        db.add(analysis)
        db.flush()  # get analysis.id for FK in AnalysisCall

        # Pre-triage hook (e.g. budget reservation, stop request)
        if before_stage_hook is not None:
            try:
                hook_res = before_stage_hook("triage", entry, triage_prompt, analysis)
                if hasattr(hook_res, "__await__"):
                    await hook_res
            except (PipelineBudgetExceededError, PipelineStopRequestedError):
                db.rollback()
                raise

        # 4. TRIAGE STAGE
        analysis, triage_call, triage_parsed = await self._run_triage(
            analysis=analysis,
            entry=entry,
            matrix=matrix,
            snapshot=snapshot,
            triage_prompt=triage_prompt,
            db=db,
            started_at=started_at,
            extra_meta=extra_meta,
        )

        if analysis.status == "failed":
            db.commit()
            db.refresh(analysis)
            return analysis

        # Gate check: If source sufficiency is INSUFFICIENT, triage is allowed but DEEP is skipped
        if sufficiency_level == "insufficient":
            triage_call.call_metadata = {
                **(triage_call.call_metadata or {}),
                "deep_skipped": True,
                "deep_skipped_reason": "insufficient_source",
            }
            analysis.status = "completed"
            analysis.summary = None
            analysis.key_points = []
            analysis.completed_at = utc_now()
            db.commit()
            db.refresh(analysis)
            return analysis

        # 5. DEEP ANALYSIS (only for 'relevant' entries with sufficient sources)
        if analysis.relevance_status == "relevant":
            if before_stage_hook is not None:
                try:
                    hook_res = before_stage_hook("deep_analysis", entry, deep_prompt, analysis)
                    if hasattr(hook_res, "__await__"):
                        await hook_res
                except (PipelineBudgetExceededError, PipelineStopRequestedError) as stop_exc:
                    triage_call.call_metadata = {
                        **(triage_call.call_metadata or {}),
                        "deep_skipped": True,
                        "deep_skipped_reason": "budget_limit" if isinstance(stop_exc, PipelineBudgetExceededError) else "stop_requested",
                    }
                    analysis.status = "incomplete"
                    analysis.reason = (analysis.reason or "") + f" [DEEP SKIPPED: {stop_exc}]"
                    analysis.completed_at = utc_now()
                    db.commit()
                    db.refresh(analysis)
                    return analysis

            analysis = await self._run_deep(
                analysis=analysis,
                entry=entry,
                snapshot=snapshot,
                deep_prompt=deep_prompt,
                triage_parsed=triage_parsed,
                db=db,
                extra_meta=extra_meta,
            )
        else:
            # not_relevant or uncertain: complete without deep
            analysis.status = "completed"
            analysis.summary = None
            analysis.key_points = []
            analysis.completed_at = utc_now()

        db.commit()
        db.refresh(analysis)
        return analysis

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _record_call(
        self,
        analysis: EntryAnalysis,
        prompt_version: AnalysisPromptVersion,
        result: AIProviderResult,
        started_at: datetime,
        extra_meta: dict[str, Any],
    ) -> AnalysisCall:
        """Persist an AnalysisCall audit record."""
        req_hash = hashlib.sha256(
            f"{prompt_version.id}:{analysis.entry_id}:{analysis.entry_content_hash}".encode()
        ).hexdigest()

        call = AnalysisCall(
            entry_analysis_id=analysis.id,
            prompt_version_id=prompt_version.id,
            stage=prompt_version.stage,
            provider=result.provider_name or self._provider.provider_name,
            model=result.model or "unknown",
            status="completed" if result.success else "failed",
            request_hash=req_hash,
            input_chars=result.input_chars,
            output_chars=result.output_chars,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_usd=result.estimated_cost_usd,
            latency_ms=result.latency_ms,
            raw_response=result.raw_response,
            error_type=result.error_type,
            error_message=result.error_message,
            call_metadata={**(result.call_metadata or {}), **extra_meta},
            started_at=started_at,
            completed_at=utc_now(),
        )
        return call

    async def _run_triage(
        self,
        analysis: EntryAnalysis,
        entry: Entry,
        matrix: TrackingMatrix,
        snapshot: dict[str, Any],
        triage_prompt: AnalysisPromptVersion,
        db: Session,
        started_at: datetime,
        extra_meta: dict[str, Any],
    ) -> tuple[EntryAnalysis, AnalysisCall, Optional[dict[str, Any]]]:
        """Execute TRIAGE call, validate response, persist results.

        Returns:
            (analysis, triage_call, triage_parsed_dict)
            triage_parsed_dict is None if the call or validation failed.
        """
        # Provide triage_result=None to the provider (triage has no prior context)
        extra_meta_with_stage = {**extra_meta, "pipeline_stage": "triage"}

        try:
            result: AIProviderResult = await self._provider.analyze(
                prompt_version=triage_prompt,
                entry=entry,
                matrix_snapshot=snapshot,
                extra_call_metadata=extra_meta_with_stage,
                triage_result=None,
            )
        except Exception as exc:
            logger.exception("Unexpected exception during triage call (entry=%s): %s", entry.id, exc)
            result = AIProviderResult(
                success=False,
                provider_name=self._provider.provider_name,
                model="unknown",
                error_type=type(exc).__name__,
                error_message=str(exc),
                latency_ms=0,
                call_metadata=extra_meta_with_stage,
            )

        triage_call = self._record_call(analysis, triage_prompt, result, started_at, extra_meta)
        db.add(triage_call)

        if not result.success or result.payload is None:
            analysis.status = "failed"
            analysis.reason = f"Triage failure ({result.error_type}): {result.error_message}"
            analysis.completed_at = utc_now()
            return analysis, triage_call, None

        payload = result.payload
        relevance_status = self._svc.derive_relevance_status(payload.relevance_score)

        # Snapshot codes for strict validation
        snapshot_topic_codes = {t["code"] for t in snapshot.get("topics", [])}
        topic_by_code: dict[str, TrackingTopic] = {
            t.code: t for t in matrix.topics if t.active and t.code in snapshot_topic_codes
        }

        try:
            self._validate_topics(payload.topics, snapshot_topic_codes, topic_by_code)
        except AnalysisValidationError as val_exc:
            analysis.status = "failed"
            analysis.reason = str(val_exc)
            analysis.completed_at = utc_now()
            triage_call.status = "failed"
            triage_call.error_type = "AnalysisValidationError"
            triage_call.error_message = str(val_exc)
            return analysis, triage_call, None

        # Validate grounding evidence if v3 triage
        if getattr(payload, "evidence", None) is not None or getattr(triage_prompt, "response_schema_version", None) == "v3" or triage_prompt.version >= 3:
            try:
                validate_triage_evidence(payload.evidence, entry)
            except AnalysisGroundingError as gr_exc:
                analysis.status = "failed"
                analysis.reason = str(gr_exc)
                analysis.completed_at = utc_now()
                triage_call.status = "failed"
                triage_call.error_type = "AnalysisGroundingError"
                triage_call.error_message = str(gr_exc)
                return analysis, triage_call, None

        # Persist triage results into EntryAnalysis
        analysis.relevance_score = payload.relevance_score
        analysis.relevance_status = relevance_status
        analysis.confidence = payload.confidence
        analysis.reason = payload.reason

        # Persist topic associations
        for topic_item in payload.topics:
            topic = topic_by_code[topic_item.topic_code]
            db.add(EntryAnalysisTopic(
                analysis_id=analysis.id,
                topic_id=topic.id,
                confidence=topic_item.confidence,
                is_primary=topic_item.is_primary,
                rationale=topic_item.rationale,
            ))

        # Build dict for deep prompt context
        triage_parsed = {
            "relevance_score": payload.relevance_score,
            "topic_codes": [t.topic_code for t in payload.topics],
            "primary_topic_code": next(
                (t.topic_code for t in payload.topics if t.is_primary), None
            ),
            "reason": payload.reason,
        }

        return analysis, triage_call, triage_parsed

    async def _run_deep(
        self,
        analysis: EntryAnalysis,
        entry: Entry,
        snapshot: dict[str, Any],
        deep_prompt: AnalysisPromptVersion,
        triage_parsed: Optional[dict[str, Any]],
        db: Session,
        extra_meta: dict[str, Any],
    ) -> EntryAnalysis:
        """Execute DEEP ANALYSIS call and persist summary/key_points.

        On failure: preserves triage data, marks EntryAnalysis as failed.
        """
        deep_started_at = utc_now()
        extra_meta_with_stage = {**extra_meta, "pipeline_stage": "deep_analysis"}

        try:
            result: AIProviderResult = await self._provider.analyze(
                prompt_version=deep_prompt,
                entry=entry,
                matrix_snapshot=snapshot,
                extra_call_metadata=extra_meta_with_stage,
                triage_result=triage_parsed,
            )
        except Exception as exc:
            logger.exception("Unexpected exception during deep analysis (entry=%s): %s", entry.id, exc)
            result = AIProviderResult(
                success=False,
                provider_name=self._provider.provider_name,
                model="unknown",
                error_type=type(exc).__name__,
                error_message=str(exc),
                latency_ms=0,
                call_metadata=extra_meta_with_stage,
            )

        deep_call = self._record_call(analysis, deep_prompt, result, deep_started_at, extra_meta)
        db.add(deep_call)

        if not result.success or result.payload is None:
            # Triage data preserved; only mark status as failed
            analysis.status = "failed"
            analysis.completed_at = utc_now()
            return analysis

        payload = result.payload

        # Validate grounding evidence if v3 deep
        if (
            getattr(payload, "key_point_items", None) is not None
            or getattr(payload, "summary_evidence", None) is not None
            or getattr(deep_prompt, "response_schema_version", None) == "v3"
            or deep_prompt.version >= 3
        ):
            try:
                validate_deep_evidence(
                    summary_evidence=payload.summary_evidence,
                    key_points=payload.key_point_items,
                    entry=entry,
                )
            except AnalysisGroundingError as gr_exc:
                analysis.status = "failed"
                analysis.reason = str(gr_exc)
                analysis.completed_at = utc_now()
                deep_call.status = "failed"
                deep_call.error_type = "AnalysisGroundingError"
                deep_call.error_message = str(gr_exc)
                return analysis

        # Persist deep results (summary + key_points only; triage fields unchanged)
        analysis.summary = payload.summary
        # Enforce key_points contract: strictly list[str] in EntryAnalysis
        clean_key_points: list[str] = []
        if payload.key_points:
            for item in payload.key_points:
                if isinstance(item, str):
                    clean_key_points.append(item)
                elif isinstance(item, dict) and "point" in item:
                    clean_key_points.append(str(item["point"]))
                elif hasattr(item, "point"):
                    clean_key_points.append(str(getattr(item, "point")))
                else:
                    clean_key_points.append(str(item))
        analysis.key_points = clean_key_points
        analysis.status = "completed"
        analysis.completed_at = utc_now()
        return analysis

    def _validate_topics(
        self,
        topics: list[AIAnalysisTopicItem],
        snapshot_topic_codes: set[str],
        topic_by_code: dict[str, TrackingTopic],
    ) -> None:
        """Strict topic validation — same rules as AnalysisService.

        Raises:
            AnalysisValidationError: on any invalid topic or primary designation.
        """
        for topic_item in topics:
            if (
                topic_item.topic_code not in snapshot_topic_codes
                or topic_item.topic_code not in topic_by_code
            ):
                raise AnalysisValidationError(
                    f"Unrecognized topic code '{topic_item.topic_code}': topic does not exist, "
                    f"belongs to another matrix, or is inactive in the matrix snapshot."
                )

        if topics:
            primaries = [t for t in topics if t.is_primary]
            if len(primaries) == 0:
                raise AnalysisValidationError(
                    "No primary topic designated among classified topics. Exactly one primary topic is required."
                )
            if len(primaries) > 1:
                raise AnalysisValidationError(
                    f"Multiple primary topics designated ({len(primaries)}). Exactly one primary topic is permitted."
                )
            if primaries[0].topic_code not in snapshot_topic_codes:
                raise AnalysisValidationError(
                    f"Primary topic '{primaries[0].topic_code}' does not exist in matrix snapshot."
                )
