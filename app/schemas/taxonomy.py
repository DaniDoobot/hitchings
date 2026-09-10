"""Pydantic schemas for taxonomy management (Areas and Temas) in the Observatory."""

from datetime import datetime
from typing import Optional, List
import uuid

from pydantic import BaseModel, ConfigDict, Field


class TaxonomyTopicItem(BaseModel):
    """Represents an individual topic or area node."""
    id: uuid.UUID
    code: str
    name: str
    description: Optional[str] = None
    parent_id: Optional[uuid.UUID] = None
    parent_code: Optional[str] = None
    active: bool = True
    priority: int = 0

    model_config = ConfigDict(from_attributes=True)


class TaxonomyAreaNode(BaseModel):
    """Represents an Area with its nested list of child Temas."""
    id: uuid.UUID
    code: str
    name: str
    description: Optional[str] = None
    active: bool = True
    priority: int = 0
    children: List[TaxonomyTopicItem] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class TaxonomyMatrixResponse(BaseModel):
    """Response containing the active TrackingMatrix metadata and full taxonomy tree."""
    matrix_id: uuid.UUID
    code: str
    name: str
    status: str
    updated_at: datetime
    areas: List[TaxonomyAreaNode] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class BaseMutationRequest(BaseModel):
    """Base request with optimistic concurrency check."""
    base_matrix_id: uuid.UUID = Field(
        ...,
        description="ID of the currently active matrix that this mutation is based on"
    )


class CreateAreaRequest(BaseMutationRequest):
    """Request to create a new Area."""
    name: str = Field(..., min_length=1, max_length=255, description="Name of the Area")
    description: Optional[str] = Field(None, description="Optional description of the Area")


class UpdateAreaRequest(BaseMutationRequest):
    """Request to update an existing Area."""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Updated name")
    description: Optional[str] = Field(None, description="Updated description")


class CreateTopicRequest(BaseMutationRequest):
    """Request to create a new Tema under an Area."""
    area_id: uuid.UUID = Field(..., description="ID of the parent Area")
    name: str = Field(..., min_length=1, max_length=255, description="Name of the Tema")
    description: Optional[str] = Field(None, description="Optional description of the Tema")


class UpdateTopicRequest(BaseMutationRequest):
    """Request to update an existing Tema."""
    name: Optional[str] = Field(None, min_length=1, max_length=255, description="Updated name")
    description: Optional[str] = Field(None, description="Updated description")
    area_id: Optional[uuid.UUID] = Field(None, description="New parent Area ID if relocating")


class ToggleStatusRequest(BaseMutationRequest):
    """Request to archive or reactivate an Area or Tema."""
    pass
