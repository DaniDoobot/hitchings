"""Ingestion result schema."""

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class IngestionResult(BaseModel):
    """Execution summary returned after an ingestion run."""
    source_id: uuid.UUID
    fetched: int
    created: int
    duplicates: int
    failed: int = 0
    started_at: datetime
    finished_at: datetime
    latest_published_at: Optional[datetime] = None
    oldest_published_at: Optional[datetime] = None
