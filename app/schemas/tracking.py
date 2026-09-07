"""Tracking schemas for matrices, topics, entities, and relationships."""

import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict, Field


# ------------------------------------------------------------------------------
# TRACKING MATRIX SCHEMAS
# ------------------------------------------------------------------------------

class TrackingMatrixBase(BaseModel):
    code: str = Field(..., max_length=100)
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    status: str = Field("draft", max_length=20)
    relevance_instructions: Optional[str] = None
    exclusion_instructions: Optional[str] = None
    config: Optional[dict[str, Any]] = None


class TrackingMatrixCreate(TrackingMatrixBase):
    pass


class TrackingMatrixUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    status: Optional[str] = Field(None, max_length=20)
    relevance_instructions: Optional[str] = None
    exclusion_instructions: Optional[str] = None
    config: Optional[dict[str, Any]] = None


class TrackingMatrixRead(TrackingMatrixBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    activated_at: Optional[datetime] = None


# ------------------------------------------------------------------------------
# TRACKING TOPIC SCHEMAS
# ------------------------------------------------------------------------------

class TrackingTopicBase(BaseModel):
    matrix_id: uuid.UUID
    parent_id: Optional[uuid.UUID] = None
    code: str = Field(..., max_length=100)
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    relevance_instructions: Optional[str] = None
    keywords: Optional[list[str]] = None
    discovery_queries: Optional[list[str]] = None
    priority: int = 0
    active: bool = True
    provisional: bool = True


class TrackingTopicCreate(TrackingTopicBase):
    pass


class TrackingTopicUpdate(BaseModel):
    parent_id: Optional[uuid.UUID] = None
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    relevance_instructions: Optional[str] = None
    keywords: Optional[list[str]] = None
    discovery_queries: Optional[list[str]] = None
    priority: Optional[int] = None
    active: Optional[bool] = None
    provisional: Optional[bool] = None


class TrackingTopicRead(TrackingTopicBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------------------
# TRACKED ENTITY SCHEMAS
# ------------------------------------------------------------------------------

class TrackedEntityBase(BaseModel):
    display_name: str = Field(..., max_length=255)
    entity_type: Optional[str] = Field("unknown", max_length=50)
    active: bool = True
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = Field(default=None, alias="metadata_")

    model_config = ConfigDict(populate_by_name=True)


class TrackedEntityCreate(BaseModel):
    display_name: str = Field(..., max_length=255)
    entity_type: Optional[str] = Field("unknown", max_length=50)
    active: bool = True
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class TrackedEntityUpdate(BaseModel):
    display_name: Optional[str] = Field(None, max_length=255)
    entity_type: Optional[str] = Field(None, max_length=50)
    active: Optional[bool] = None
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class TrackedEntityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    display_name: str
    entity_type: Optional[str] = None
    active: bool
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = Field(default=None, alias="metadata_")
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------------------
# ENTITY-TOPIC ASSOCIATION SCHEMAS
# ------------------------------------------------------------------------------

class TrackedEntityTopicLink(BaseModel):
    is_primary: bool = False
    priority: Optional[int] = None
    notes: Optional[str] = None


class TrackedEntityTopicRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tracked_entity_id: uuid.UUID
    tracking_topic_id: uuid.UUID
    is_primary: bool
    priority: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class EntityTopicDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    topic_id: uuid.UUID
    code: str
    name: str
    is_primary: bool
    priority: Optional[int] = None
    notes: Optional[str] = None
