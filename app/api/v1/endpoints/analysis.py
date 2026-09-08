"""AI Analysis read-only API endpoints."""

import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.models.analysis import (
    AnalysisPromptVersion,
    EntryAnalysis,
    EntryAnalysisTopic,
    AnalysisCall,
)
from app.schemas.analysis import (
    EntryAnalysisResponse,
    EntryAnalysisDetailResponse,
    EntryAnalysisTopicResponse,
    AnalysisCallResponse,
    AnalysisUsageResponse,
    PromptVersionResponse,
)
from app.services.analysis_service import AnalysisService
from app.services.topic_canonicalization_service import canonicalize_analysis_topics

router = APIRouter(tags=["AI Analysis"])


@router.get("/entry-analyses", response_model=List[EntryAnalysisResponse])
def list_entry_analyses(
    status: Optional[str] = Query(None, description="Filter by status (pending, completed, failed)"),
    relevance_status: Optional[str] = Query(None, description="Filter by relevance_status (relevant, uncertain, not_relevant)"),
    entry_id: Optional[uuid.UUID] = Query(None, description="Filter by entry_id"),
    matrix_id: Optional[uuid.UUID] = Query(None, description="Filter by matrix_id"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> List[EntryAnalysis]:
    """Retrieve entry analyses with filtering and pagination, sorted by creation date descending."""
    stmt = select(EntryAnalysis)
    if status is not None:
        stmt = stmt.where(EntryAnalysis.status == status)
    if relevance_status is not None:
        stmt = stmt.where(EntryAnalysis.relevance_status == relevance_status)
    if entry_id is not None:
        stmt = stmt.where(EntryAnalysis.entry_id == entry_id)
    if matrix_id is not None:
        stmt = stmt.where(EntryAnalysis.matrix_id == matrix_id)

    stmt = stmt.order_by(EntryAnalysis.created_at.desc()).offset(offset).limit(limit)
    return list(db.execute(stmt).scalars().all())


@router.get("/entry-analyses/{analysis_id}", response_model=EntryAnalysisDetailResponse)
def get_entry_analysis(analysis_id: uuid.UUID, db: Session = Depends(get_db)) -> EntryAnalysisDetailResponse:
    """Retrieve a full entry analysis by ID, including snapshot, topics, and audit calls."""
    analysis = db.query(EntryAnalysis).options(
        joinedload(EntryAnalysis.topics).joinedload(EntryAnalysisTopic.topic),
        joinedload(EntryAnalysis.calls).joinedload(AnalysisCall.prompt_version),
    ).filter(EntryAnalysis.id == analysis_id).first()

    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EntryAnalysis with ID '{analysis_id}' not found",
        )

    # Format topic items with human-readable code and name
    topics_formatted = [
        EntryAnalysisTopicResponse(
            id=t.id,
            analysis_id=t.analysis_id,
            topic_id=t.topic_id,
            confidence=t.confidence,
            is_primary=t.is_primary,
            rationale=t.rationale,
            created_at=t.created_at,
            topic_code=t.topic.code if t.topic else None,
            topic_name=t.topic.name if t.topic else None,
        )
        for t in analysis.topics
    ]

    # Compute canonical topic view (removing redundant ancestors when specific descendants are selected)
    canonical_topics_formatted: list[EntryAnalysisTopicResponse] = []
    canonical_primary_formatted: Optional[EntryAnalysisTopicResponse] = None

    if analysis.topics:
        canonical_res = canonicalize_analysis_topics(analysis.topics, db=db)
        raw_topic_map = {t.topic_id: t for t in analysis.topics}

        for ct in canonical_res.canonical_topics:
            orig = raw_topic_map.get(ct.topic_id)
            resp = EntryAnalysisTopicResponse(
                id=orig.id if orig else ct.topic_id,
                analysis_id=analysis.id,
                topic_id=ct.topic_id,
                confidence=ct.confidence,
                is_primary=ct.is_primary,
                rationale=orig.rationale if orig else None,
                created_at=orig.created_at if orig else analysis.created_at,
                topic_code=ct.topic_code,
                topic_name=ct.topic_name,
            )
            canonical_topics_formatted.append(resp)
            if ct.is_primary:
                canonical_primary_formatted = resp

    calls_formatted = [
        AnalysisCallResponse(
            id=c.id,
            entry_analysis_id=c.entry_analysis_id,
            prompt_version_id=c.prompt_version_id,
            prompt_code=c.prompt_version.code if c.prompt_version else None,
            prompt_version=c.prompt_version.version if c.prompt_version else None,
            prompt_stage=c.prompt_version.stage if c.prompt_version else c.stage,
            stage=c.stage,
            provider=c.provider,
            model=c.model,
            status=c.status,
            request_hash=c.request_hash,
            input_chars=c.input_chars,
            output_chars=c.output_chars,
            input_tokens=c.input_tokens,
            output_tokens=c.output_tokens,
            estimated_cost_usd=float(c.estimated_cost_usd) if c.estimated_cost_usd is not None else None,
            latency_ms=c.latency_ms,
            error_type=c.error_type,
            error_message=c.error_message,
            call_metadata=c.call_metadata,
            started_at=c.started_at,
            completed_at=c.completed_at,
            created_at=c.created_at,
        )
        for c in analysis.calls
    ]

    # Extract grounding evidence from audit calls if available (v3 pipeline)
    grounding_evidence: Optional[dict[str, Any]] = None
    triage_ev = None
    summary_ev = None
    kp_ev = None

    for c in analysis.calls:
        if not c.raw_response or not isinstance(c.raw_response, dict):
            continue
        res = c.raw_response.get("result")
        if not isinstance(res, dict):
            continue
        if c.stage == "triage" and "evidence" in res:
            triage_ev = res.get("evidence")
        elif c.stage == "deep_analysis":
            if "summary_evidence" in res:
                summary_ev = res.get("summary_evidence")
            if "key_points" in res:
                kp_ev = res.get("key_points")

    if triage_ev is not None or summary_ev is not None or kp_ev is not None:
        grounding_evidence = {
            "triage_evidence": triage_ev or [],
            "summary_evidence": summary_ev or [],
            "key_points_evidence": kp_ev or [],
        }

    return EntryAnalysisDetailResponse(
        id=analysis.id,
        entry_id=analysis.entry_id,
        matrix_id=analysis.matrix_id,
        pipeline_version=analysis.pipeline_version,
        status=analysis.status,
        entry_content_hash=analysis.entry_content_hash,
        matrix_snapshot=analysis.matrix_snapshot,
        matrix_snapshot_hash=analysis.matrix_snapshot_hash,
        relevance_status=analysis.relevance_status,
        relevance_score=analysis.relevance_score,
        confidence=analysis.confidence,
        summary=analysis.summary,
        reason=analysis.reason,
        key_points=analysis.key_points,
        started_at=analysis.started_at,
        completed_at=analysis.completed_at,
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
        topics=topics_formatted,
        canonical_topics=canonical_topics_formatted,
        canonical_primary_topic=canonical_primary_formatted,
        calls=calls_formatted,
        grounding_evidence=grounding_evidence,
    )


@router.get("/analysis-usage", response_model=AnalysisUsageResponse)
def get_analysis_usage(db: Session = Depends(get_db)) -> AnalysisUsageResponse:
    """Retrieve aggregated usage, token counts, and estimated cost across all analysis calls."""
    service = AnalysisService()
    return service.get_usage_summary(db)


@router.get("/analysis-prompts", response_model=List[PromptVersionResponse])
def list_analysis_prompts(
    stage: Optional[str] = None,
    active_only: bool = True,
    db: Session = Depends(get_db),
) -> List[AnalysisPromptVersion]:
    """List available AI analysis prompt versions."""
    stmt = select(AnalysisPromptVersion)
    if stage:
        stmt = stmt.where(AnalysisPromptVersion.stage == stage)
    if active_only:
        stmt = stmt.where(AnalysisPromptVersion.active == True)  # noqa: E712
    stmt = stmt.order_by(AnalysisPromptVersion.code, AnalysisPromptVersion.version.desc())
    return list(db.execute(stmt).scalars().all())
