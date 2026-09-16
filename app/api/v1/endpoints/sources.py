"""Sources management API endpoints."""

import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.analysis import EntryAnalysis, AnalysisCall
from app.models.ingestion_run import IngestionRun
from app.schemas.source import SourceCreate, SourceUpdate, SourceRead
from app.schemas.ingestion import (
    IngestionResult,
    SourceStatusResponse,
    SourceStatusSummaryItem,
    SourceQualityMetricsItem,
    SourceQualityMetricsResponse,
)
from app.services.ingestion_service import IngestionService

router = APIRouter(prefix="/sources", tags=["Sources"])


@router.get("", response_model=List[SourceRead])
def list_sources(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    active: Optional[bool] = None,
    tracked_entity_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
) -> List[Source]:
    """List sources with optional filtering and pagination."""
    stmt = select(Source)
    if active is not None:
        stmt = stmt.where(Source.active == active)
    if tracked_entity_id is not None:
        stmt = stmt.where(Source.tracked_entity_id == tracked_entity_id)
    
    stmt = stmt.order_by(Source.created_at.desc()).offset(offset).limit(limit)
    return list(db.execute(stmt).scalars().all())


@router.get("/status", response_model=List[SourceStatusSummaryItem])
def get_all_sources_status(
    db: Session = Depends(get_db),
) -> List[SourceStatusSummaryItem]:
    """Retrieve operational status for all sources."""
    sources = db.execute(select(Source).order_by(Source.name.asc())).scalars().all()
    results = []

    for s in sources:
        latest_run = (
            db.execute(
                select(IngestionRun)
                .where(IngestionRun.source_id == s.id)
                .order_by(IngestionRun.started_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )

        enabled = bool(s.active)
        if s.config and "enabled" in s.config:
            enabled = enabled and bool(s.config["enabled"])

        if not enabled:
            source_status = "disabled"
        elif latest_run and latest_run.status == "failed":
            source_status = "degraded"
        else:
            source_status = "healthy"

        created_cnt = latest_run.created_count if latest_run else 0
        error_cnt = latest_run.failed_count if latest_run else 0
        last_exec = s.last_run_at or (latest_run.started_at if latest_run else None)
        last_succ = s.last_success_at or (
            latest_run.finished_at if latest_run and latest_run.status == "success" else None
        )

        stype_val = s.type.value if hasattr(s.type, "value") else str(s.type)
        results.append(
            SourceStatusSummaryItem(
                source=s.name.lower().replace(" ", "_"),
                source_id=s.id,
                source_type=stype_val,
                enabled=enabled,
                last_execution=last_exec,
                last_success=last_succ,
                entries_created=created_cnt,
                errors=error_cnt,
                status=source_status,
            )
        )
    return results


@router.get("/metrics", response_model=SourceQualityMetricsResponse)
def get_sources_quality_metrics(
    db: Session = Depends(get_db),
) -> SourceQualityMetricsResponse:
    """Retrieve aggregated quality and cost metrics per source."""
    sources = db.execute(select(Source).order_by(Source.name.asc())).scalars().all()
    items = []

    for s in sources:
        is_li = s.type == SourceType.LINKEDIN or "linkedin" in str(s.type).lower()

        # 1. Posts captured from IngestionRun fetched_count
        captured = (
            db.execute(
                select(func.coalesce(func.sum(IngestionRun.fetched_count), 0)).where(
                    IngestionRun.source_id == s.id
                )
            ).scalar()
            or 0
        )

        # 2. Entries created count
        entries_cnt = (
            db.execute(select(func.count(Entry.id)).where(Entry.source_id == s.id)).scalar()
            or 0
        )
        if captured == 0 and entries_cnt > 0:
            captured = entries_cnt

        # 3. Completed analyses for entries of this source
        analyses = (
            db.execute(
                select(EntryAnalysis)
                .join(Entry, EntryAnalysis.entry_id == Entry.id)
                .where(Entry.source_id == s.id, EntryAnalysis.status == "completed")
            )
            .scalars()
            .all()
        )
        total_analyzed = len(analyses)
        relevant_cnt = sum(1 for a in analyses if a.relevance_status == "relevant")
        relevant_pct = round((relevant_cnt / total_analyzed) * 100, 1) if total_analyzed > 0 else 0.0

        # 4. Deep analyses & calls
        analysis_ids = [a.id for a in analyses]
        deep_cnt = 0
        total_latency_ms = 0
        call_count = 0
        gemini_cost = 0.0

        if analysis_ids:
            calls = (
                db.execute(
                    select(AnalysisCall).where(AnalysisCall.entry_analysis_id.in_(analysis_ids))
                )
                .scalars()
                .all()
            )
            deep_seen_analyses = set()
            for c in calls:
                call_count += 1
                if c.latency_ms:
                    total_latency_ms += c.latency_ms
                if c.estimated_cost_usd:
                    gemini_cost += float(c.estimated_cost_usd)
                if c.stage == "deep_analysis" and c.status == "completed":
                    deep_seen_analyses.add(c.entry_analysis_id)

            deep_cnt = len(deep_seen_analyses)

        deep_pct = round((deep_cnt / total_analyzed) * 100, 1) if total_analyzed > 0 else 0.0
        avg_time = round(total_latency_ms / call_count, 1) if call_count > 0 else None

        # 5. Provider cost (e.g. Bright Data for LinkedIn) from IngestionRun.run_metadata
        provider_cost = 0.0
        if is_li:
            runs = db.execute(select(IngestionRun).where(IngestionRun.source_id == s.id)).scalars().all()
            for r in runs:
                meta = r.run_metadata or {}
                if "estimated_provider_cost" in meta and meta["estimated_provider_cost"]:
                    provider_cost += float(meta["estimated_provider_cost"])

        stype_val = s.type.value if hasattr(s.type, "value") else str(s.type)
        items.append(
            SourceQualityMetricsItem(
                source_id=s.id,
                source_name=s.name,
                source_type=stype_val,
                is_linkedin=is_li,
                posts_captured=int(captured),
                entries_created=int(entries_cnt),
                total_analyzed=total_analyzed,
                relevant_count=relevant_cnt,
                relevant_pct=relevant_pct,
                deep_analysis_count=deep_cnt,
                deep_analysis_pct=deep_pct,
                avg_analysis_time_ms=avg_time,
                estimated_gemini_cost_usd=round(gemini_cost, 4),
                estimated_provider_cost_usd=round(provider_cost, 4),
            )
        )

    return SourceQualityMetricsResponse(
        sources=items,
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/{source_id}", response_model=SourceRead)
def get_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> Source:
    """Retrieve a single source by ID."""
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return source


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, db: Session = Depends(get_db)) -> Source:
    """Create a new technical data source."""
    source = Source(
        name=payload.name,
        type=payload.type,
        url=payload.url,
        active=payload.active,
        category=payload.category,
        provider=payload.provider,
        config=payload.config,
        schedule_config=payload.schedule_config,
        tracked_entity_id=payload.tracked_entity_id,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.patch("/{source_id}", response_model=SourceRead)
def update_source(
    source_id: uuid.UUID,
    payload: SourceUpdate,
    db: Session = Depends(get_db),
) -> Source:
    """Partially update an existing source."""
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(source, field, value)

    db.commit()
    db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    """Delete a source."""
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    db.delete(source)
    db.commit()


@router.post("/{source_id}/ingest", response_model=IngestionResult)
async def ingest_source_endpoint(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> IngestionResult:
    """Manually trigger ingestion for a specific data source."""
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    service = IngestionService()
    try:
        return await service.ingest_source(source_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ingestion failed: {exc}"
        )


@router.get("/{source_id}/status", response_model=SourceStatusResponse)
def get_source_status_endpoint(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> SourceStatusResponse:
    """Retrieve real-time technical observability and freshness status for a source."""
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    return IngestionService.get_source_status(source, db)
