"""Entry data schemas."""

import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict


class EntryBase(BaseModel):
    """Base schema for Entry."""
    source_id: uuid.UUID
    url: str
    canonical_url: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    excerpt: Optional[str] = None
    author: Optional[str] = None
    external_id: Optional[str] = None
    published_at: Optional[datetime] = None
    language: Optional[str] = None
    content_type: Optional[str] = None
    content_hash: Optional[str] = None
    raw_metadata: Optional[dict[str, Any]] = None


class EntryRead(EntryBase):
    """Schema for reading an Entry with persisted attributes."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    captured_at: datetime
    created_at: datetime
    updated_at: datetime
