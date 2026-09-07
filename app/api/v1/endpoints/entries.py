"""Entries read-only API endpoints."""

import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entry import Entry
from app.schemas.entry import EntryRead

router = APIRouter(prefix="/entries", tags=["Entries"])


@router.get("", response_model=List[EntryRead])
def list_entries(
    source_id: Optional[uuid.UUID] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> List[Entry]:
    """Retrieve captured entries with pagination, sorted by publication date descending."""
    stmt = select(Entry)
    if source_id is not None:
        stmt = stmt.where(Entry.source_id == source_id)

    stmt = stmt.order_by(Entry.published_at.desc().nullslast(), Entry.captured_at.desc())
    stmt = stmt.offset(offset).limit(limit)
    return list(db.execute(stmt).scalars().all())


@router.get("/{entry_id}", response_model=EntryRead)
def get_entry(entry_id: uuid.UUID, db: Session = Depends(get_db)) -> Entry:
    """Retrieve a single captured entry by ID."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    return entry
