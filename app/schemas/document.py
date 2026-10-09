"""Client-facing Pydantic schemas for the Documentary Archive API (BLOQUE 11B).

These schemas define the documentary consultation contract.
Permits querying both analysed and unanalysed entries, distinguishing
strictly between original captured document attributes and AI analysis results.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.observatory import (
    ObservatoryEvidence,
    ObservatoryTopicItem,
)


class DocumentSourceRef(BaseModel):
    """Source reference for documentary view with tracked entity context."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    name: str
    type: Optional[str] = None
    category: Optional[str] = None
    tracked_entity_name: Optional[str] = None


class DocumentAnalysisSummary(BaseModel):
    """Analysis summary embedded in document views when available."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    status: str
    relevance_status: Optional[str] = None
    relevance_score: Optional[int] = None
    confidence: Optional[float] = None
    summary: Optional[str] = None
    key_points: list[str] = Field(default_factory=list)
    canonical_topics: list[ObservatoryTopicItem] = Field(default_factory=list)
    canonical_primary_topic: Optional[ObservatoryTopicItem] = None
    analyzed_at: Optional[datetime] = None


class DocumentListItem(BaseModel):
    """List representation of a document in the archive."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    title: Optional[str] = None
    url: str
    canonical_url: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    captured_at: datetime
    content_type: Optional[str] = None
    language: Optional[str] = None
    source: DocumentSourceRef
    is_linkedin: bool = False
    source_origin_category: str = "other"
    excerpt: Optional[str] = None

    # Analysis status
    has_analysis: bool = False
    analysis: Optional[DocumentAnalysisSummary] = None


class DocumentDetail(BaseModel):
    """Full detail of a document including full content and evidence if analyzed."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    title: Optional[str] = None
    url: str
    canonical_url: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    captured_at: datetime
    content_type: Optional[str] = None
    language: Optional[str] = None
    source: DocumentSourceRef
    is_linkedin: bool = False
    source_origin_category: str = "other"
    content: Optional[str] = None
    excerpt: Optional[str] = None
    raw_metadata: Optional[dict[str, Any]] = None

    # Analysis status & detailed intelligence
    has_analysis: bool = False
    analysis: Optional[DocumentAnalysisSummary] = None
    evidence: Optional[ObservatoryEvidence] = None


class DocumentListResponse(BaseModel):
    """Paginated response envelope for the document archive."""

    items: list[DocumentListItem]
    total: int
    limit: int
    offset: int
