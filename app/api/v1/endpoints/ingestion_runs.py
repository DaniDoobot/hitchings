"""IngestionRuns read-only API endpoints for execution history and traceability."""

import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.ingestion_run import IngestionRun
from app.schemas.ingestion import IngestionRunResponse

router = APIRouter(prefix="/ingestion-runs", tags=["Ingestion Runs"])


@router.get("", response_model=List[IngestionRunResponse])
def list_ingestion_runs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    source_id: Optional[uuid.UUID] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
) -> List[IngestionRun]:
    """List historical ingestion runs with optional filtering by source and status."""
    stmt = select(IngestionRun).order_by(IngestionRun.started_at.desc())

    if source_id:
        stmt = stmt.where(IngestionRun.source_id == source_id)
    if status_filter:
        stmt = stmt.where(IngestionRun.status == status_filter)

    stmt = stmt.offset(offset).limit(limit)
    return list(db.execute(stmt).scalars().all())


@router.get("/{run_id}", response_model=IngestionRunResponse)
def get_ingestion_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> IngestionRun:
    """Retrieve details of a single historical ingestion run."""
    run = db.get(IngestionRun, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion run not found")
    return run
