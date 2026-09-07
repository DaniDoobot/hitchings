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

router = APIRouter(tags=["AI Analysis"])


@router.get("/entry-analyses", response_model=List[EntryAnalysisResponse])
def list_entry_analyses(
    status: Optional[str] = Query(None, description="Filter by status (pending, completed, failed)"),
    relevance_status: Optional[str] = Query(None, description="Filter by relevance_status (relevant, uncertain, not_relevant)"),
    entry_id: Optional[uuid.UUID] = Query(None, description="Filter by entry_id"),
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

    stmt = stmt.order_by(EntryAnalysis.created_at.desc()).offset(offset).limit(limit)
    return list(db.execute(stmt).scalars().all())


@router.get("/entry-analyses/{analysis_id}", response_model=EntryAnalysisDetailResponse)
def get_entry_analysis(analysis_id: uuid.UUID, db: Session = Depends(get_db)) -> EntryAnalysisDetailResponse:
    """Retrieve a full entry analysis by ID, including snapshot, topics, and audit calls."""
    analysis = db.query(EntryAnalysis).options(
        joinedload(EntryAnalysis.topics).joinedload(EntryAnalysisTopic.topic),
        joinedload(EntryAnalysis.calls),
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

    calls_formatted = [
        AnalysisCallResponse.model_validate(c) for c in analysis.calls
    ]

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
        calls=calls_formatted,
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
