"""Source data schemas for API requests and responses."""

import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict, Field

from app.models.source import SourceType


class SourceBase(BaseModel):
    """Base schema with common attributes of a Source."""
    name: str = Field(..., max_length=255)
    type: SourceType
    url: Optional[str] = None
    active: bool = True
    category: Optional[str] = Field(None, max_length=100)
    provider: str = Field("native", max_length=50)
    config: Optional[dict[str, Any]] = None
    schedule_config: Optional[dict[str, Any]] = None
    tracked_entity_id: Optional[uuid.UUID] = None


class SourceCreate(SourceBase):
    """Schema for creating a new Source."""
    pass


class SourceUpdate(BaseModel):
    """Schema for updating an existing Source (all fields optional)."""
    name: Optional[str] = Field(None, max_length=255)
    type: Optional[SourceType] = None
    url: Optional[str] = None
    active: Optional[bool] = None
    category: Optional[str] = Field(None, max_length=100)
    provider: Optional[str] = Field(None, max_length=50)
    config: Optional[dict[str, Any]] = None
    schedule_config: Optional[dict[str, Any]] = None
    tracked_entity_id: Optional[uuid.UUID] = None


class SourceRead(SourceBase):
    """Schema for reading a Source with system-generated timestamps and IDs."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    last_run_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
