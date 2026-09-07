"""Sources management API endpoints."""

import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.source import Source
from app.schemas.source import SourceCreate, SourceUpdate, SourceRead
from app.schemas.ingestion import IngestionResult, SourceStatusResponse
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
