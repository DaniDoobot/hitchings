"""Service orchestrating AI analysis, matrix snapshotting, prompt execution, and audit persistence."""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models.analysis import (
    AnalysisPromptVersion,
    EntryAnalysis,
    EntryAnalysisTopic,
    AnalysisCall,
)
from app.models.entry import Entry
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.providers.ai.base import BaseAIProvider, AIProviderResult
from app.providers.ai.mock import MockAIProvider
from app.schemas.analysis import AnalysisUsageResponse

logger = logging.getLogger(__name__)


class AnalysisValidationError(ValueError):
    """Raised when model response contains hallucinated topics or invalid topic designations."""
    pass


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


def compute_matrix_snapshot(matrix: TrackingMatrix) -> tuple[dict[str, Any], str]:
    """Generate a canonical snapshot dictionary for a TrackingMatrix and its SHA-256 hash."""
    active_topics = [t for t in matrix.topics if t.active]
    topics_data = []
    for t in sorted(active_topics, key=lambda x: x.code):
        topics_data.append({
            "topic_id": str(t.id),
            "code": t.code,
            "name": t.name,
            "parent_code": t.parent.code if t.parent else None,
            "description": t.description,
            "relevance_instructions": t.relevance_instructions,
            "keywords": sorted(t.keywords or []),
        })

    snapshot: dict[str, Any] = {
        "matrix_id": str(matrix.id),
        "code": matrix.code,
        "name": matrix.name,
        "status": matrix.status,
        "relevance_instructions": matrix.relevance_instructions,
        "exclusion_instructions": matrix.exclusion_instructions,
        "topics": topics_data,
    }

    serialized = json.dumps(snapshot, sort_keys=True, ensure_ascii=True)
    snapshot_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return snapshot, snapshot_hash


def compute_content_hash(entry: Entry) -> str:
    """Generate SHA-256 hash of an entry's textual content."""
    text = f"{entry.title or ''}|{entry.content or ''}".strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AnalysisService:
    """Manages AI analysis lifecycles, versioned prompt invocations, and granular audit."""

    def __init__(self, provider: Optional[BaseAIProvider] = None) -> None:
        self._provider = provider
        self.settings = get_settings()

    def get_provider(self) -> BaseAIProvider:
        """Resolve the active AI provider based on configuration or explicit injection."""
        if self._provider is not None:
            return self._provider

        provider_type = self.settings.ANALYSIS_PROVIDER.lower()
        if provider_type == "disabled":
            raise RuntimeError(
                "AI analysis provider is currently disabled (ANALYSIS_PROVIDER='disabled'). "
                "No external or automated LLM calls are permitted."
            )
        elif provider_type == "mock":
            return MockAIProvider()
        else:
            raise ValueError(f"Unsupported AI provider: '{provider_type}'")

    def derive_relevance_status(self, score: Optional[int]) -> Optional[str]:
        """Classify numerical relevance score (0-100) into business status categories."""
        if score is None:
            return None
        if score >= self.settings.ANALYSIS_RELEVANT_MIN_SCORE:
            return "relevant"
        elif score >= self.settings.ANALYSIS_UNCERTAIN_MIN_SCORE:
            return "uncertain"
        else:
            return "not_relevant"

    def check_monthly_limit(self, db: Session) -> None:
        """Verify that analysis call volume does not exceed monthly safeguard quota."""
        now = utc_now()
        start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

        count = db.scalar(
            select(func.count(EntryAnalysis.id)).where(
                EntryAnalysis.created_at >= start_of_month,
                EntryAnalysis.status == "completed",
            )
        ) or 0

        if count >= self.settings.ANALYSIS_MONTHLY_ENTRY_LIMIT:
            raise RuntimeError(
                f"Monthly analysis entry limit reached ({count}/{self.settings.ANALYSIS_MONTHLY_ENTRY_LIMIT}). "
                "Halting further executions until next billing period or limit elevation."
            )

    async def analyze_entry(
        self,
        entry_id: uuid.UUID,
        matrix_id: uuid.UUID,
        prompt_version_id: uuid.UUID,
        db: Session,
        pipeline_version: str = "v1",
    ) -> EntryAnalysis:
        """Execute full analysis flow: snapshot -> provider call -> validation -> audit -> persistence."""
        # 1. Quota check (enforcement deactivated in Bloque 7A per architectural guidelines)
        # Usage metrics and monthly setting remain active for tracking

        # 2. Resolve entities
        entry = db.get(Entry, entry_id)
        if not entry:
            raise ValueError(f"Entry with ID '{entry_id}' not found")

        matrix = db.query(TrackingMatrix).options(
            joinedload(TrackingMatrix.topics).joinedload(TrackingTopic.parent)
        ).filter(TrackingMatrix.id == matrix_id).first()
        if not matrix:
            raise ValueError(f"TrackingMatrix with ID '{matrix_id}' not found")

        prompt_version = db.get(AnalysisPromptVersion, prompt_version_id)
        if not prompt_version:
            raise ValueError(f"AnalysisPromptVersion with ID '{prompt_version_id}' not found")
        if not prompt_version.active:
            raise ValueError(f"AnalysisPromptVersion '{prompt_version.code}:v{prompt_version.version}' is inactive")

        # 3. Snapshot & Content Hash
        snapshot, snapshot_hash = compute_matrix_snapshot(matrix)
        content_hash = compute_content_hash(entry)

        # 4. Initialize EntryAnalysis in 'pending' status
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
        db.flush()

        # 5. Execute Provider Call
        provider = self.get_provider()
        req_hash = hashlib.sha256(f"{prompt_version.id}:{entry.id}:{content_hash}".encode("utf-8")).hexdigest()

        result: AIProviderResult
        try:
            result = await provider.analyze(
                prompt_version=prompt_version,
                entry=entry,
                matrix_snapshot=snapshot,
            )
        except Exception as exc:
            logger.exception("Unexpected exception in AI provider call: %s", exc)
            result = AIProviderResult(
                success=False,
                provider_name=provider.provider_name,
                model="unknown",
                error_type=type(exc).__name__,
                error_message=str(exc),
                latency_ms=0,
            )

        call_completed_at = utc_now()

        # 6. Record Audit Call (always created, using completed/failed status)
        audit_call = AnalysisCall(
            entry_analysis_id=analysis.id,
            prompt_version_id=prompt_version.id,
            stage=prompt_version.stage,
            provider=result.provider_name or provider.provider_name,
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
            call_metadata=result.call_metadata,
            started_at=started_at,
            completed_at=call_completed_at,
        )
        db.add(audit_call)

        # 7. Handle Provider Outcome
        if not result.success or result.payload is None:
            analysis.status = "failed"
            analysis.reason = f"Provider failure ({result.error_type}): {result.error_message}"
            analysis.completed_at = call_completed_at
            audit_call.status = "failed"
            db.commit()
            db.refresh(analysis)
            return analysis

        # 8. Success: Derive business fields
        payload = result.payload
        relevance_status = self.derive_relevance_status(payload.relevance_score)

        analysis.status = "completed"
        analysis.relevance_score = payload.relevance_score
        analysis.relevance_status = relevance_status
        analysis.confidence = payload.confidence
        analysis.summary = payload.summary
        analysis.reason = payload.reason
        analysis.key_points = payload.key_points
        analysis.completed_at = call_completed_at

        # 9. Map and strictly validate topics against matrix snapshot
        snapshot_topic_codes = {t["code"] for t in snapshot.get("topics", [])}
        topic_by_code: dict[str, TrackingTopic] = {
            t.code: t for t in matrix.topics if t.active and t.code in snapshot_topic_codes
        }

        try:
            # Reject any unknown, foreign, or inactive topic
            for topic_item in payload.topics:
                if (
                    topic_item.topic_code not in snapshot_topic_codes
                    or topic_item.topic_code not in topic_by_code
                ):
                    raise AnalysisValidationError(
                        f"Unrecognized topic code '{topic_item.topic_code}': topic does not exist, "
                        f"belongs to another matrix, or is inactive in the matrix snapshot."
                    )

            # Validate primary topic designation when topics are present
            if payload.topics:
                primaries = [t for t in payload.topics if t.is_primary]
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
        except AnalysisValidationError as val_exc:
            analysis.status = "failed"
            analysis.reason = str(val_exc)
            analysis.completed_at = call_completed_at
            audit_call.status = "failed"
            audit_call.error_type = "AnalysisValidationError"
            audit_call.error_message = str(val_exc)
            db.commit()
            db.refresh(analysis)
            return analysis

        # Persist valid topics
        for topic_item in payload.topics:
            topic = topic_by_code[topic_item.topic_code]
            analysis_topic = EntryAnalysisTopic(
                analysis_id=analysis.id,
                topic_id=topic.id,
                confidence=topic_item.confidence,
                is_primary=topic_item.is_primary,
                rationale=topic_item.rationale,
            )
            db.add(analysis_topic)

        db.commit()
        db.refresh(analysis)
        return analysis

    def get_usage_summary(self, db: Session) -> AnalysisUsageResponse:
        """Calculate aggregated token counts, execution metrics, and estimated costs."""
        calls = db.query(AnalysisCall).all()

        total_calls = len(calls)
        successful_calls = sum(1 for c in calls if c.status == "completed")
        failed_calls = sum(1 for c in calls if c.status == "failed")
        total_in_tokens = sum(c.input_tokens or 0 for c in calls)
        total_out_tokens = sum(c.output_tokens or 0 for c in calls)
        total_tokens = total_in_tokens + total_out_tokens
        total_cost = sum(float(c.estimated_cost_usd or 0.0) for c in calls)

        by_provider: dict[str, dict[str, Any]] = {}
        by_stage: dict[str, dict[str, Any]] = {}

        for c in calls:
            # By provider
            p = c.provider or "unknown"
            if p not in by_provider:
                by_provider[p] = {"calls": 0, "tokens": 0, "estimated_cost_usd": 0.0}
            by_provider[p]["calls"] += 1
            by_provider[p]["tokens"] += (c.input_tokens or 0) + (c.output_tokens or 0)
            by_provider[p]["estimated_cost_usd"] = round(
                by_provider[p]["estimated_cost_usd"] + float(c.estimated_cost_usd or 0.0), 6
            )

            # By stage
            s = c.stage or "unknown"
            if s not in by_stage:
                by_stage[s] = {"calls": 0, "tokens": 0, "estimated_cost_usd": 0.0}
            by_stage[s]["calls"] += 1
            by_stage[s]["tokens"] += (c.input_tokens or 0) + (c.output_tokens or 0)
            by_stage[s]["estimated_cost_usd"] = round(
                by_stage[s]["estimated_cost_usd"] + float(c.estimated_cost_usd or 0.0), 6
            )

        return AnalysisUsageResponse(
            total_calls=total_calls,
            successful_calls=successful_calls,
            failed_calls=failed_calls,
            total_input_tokens=total_in_tokens,
            total_output_tokens=total_out_tokens,
            total_tokens=total_tokens,
            total_estimated_cost_usd=round(total_cost, 6),
            by_provider=by_provider,
            by_stage=by_stage,
        )
