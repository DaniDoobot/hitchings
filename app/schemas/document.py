"""Client-facing Pydantic schemas for the Documentary Archive API (BLOQUE 11B).

These schemas define the documentary consultation contract.
Permits querying both analysed and unanalysed entries, distinguishing
strictly between original captured document attributes and AI analysis results.
"""

from __future__ import annotations

import urllib.parse
import uuid
from datetime import datetime
from typing import Optional, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

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


# ==============================================================================
# Manual Document Ingestion Schemas (BLOQUE 11C)
# ==============================================================================


class IngestUrlRequest(BaseModel):
    """Request schema for ingesting a document via external URL."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., description="Target HTTP or HTTPS URL to ingest")
    analyze_with_ai: bool = Field(False, description="Trigger AI analysis pipeline")
    manual_title: Optional[str] = Field(None, max_length=500, description="Optional custom title override")
    manual_author: Optional[str] = Field(None, max_length=255, description="Optional custom author override")
    manual_published_at: Optional[datetime] = Field(None, description="Optional custom publication datetime")

    @field_validator("url")
    @classmethod
    def validate_url_syntax(cls, v: str) -> str:
        trimmed = (v or "").strip()
        if not trimmed:
            raise ValueError("La URL no puede estar vacía.")
        if not (trimmed.startswith("http://") or trimmed.startswith("https://")):
            raise ValueError("La URL debe comenzar con http:// o https://.")
        parsed = urllib.parse.urlsplit(trimmed)
        if not parsed.netloc:
            raise ValueError("La URL debe contener un host o dominio válido.")
        return trimmed


class DocumentIngestionResponse(BaseModel):
    """Response returned upon successful manual document ingestion (HTTP 201)."""

    model_config = ConfigDict(from_attributes=False)

    status: Literal["created"] = "created"
    entry_id: uuid.UUID
    title: Optional[str] = None
    url: str
    canonical_url: Optional[str] = None
    content_type: Optional[str] = None
    analysis_requested: bool = False
    analysis_scheduled: bool = False
    created_at: datetime


class DocumentDuplicateResponse(BaseModel):
    """Response returned when the document already exists in the archive (HTTP 409)."""

    model_config = ConfigDict(from_attributes=False)

    status: Literal["duplicate"] = "duplicate"
    existing_entry_id: uuid.UUID
    duplicate_reason: str
    message: str


class DossierExportRequest(BaseModel):
    """Request payload for multi-document thematic dossier export (Bloque 12 — Fase 4B)."""

    model_config = ConfigDict(from_attributes=False)

    document_ids: list[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=30,
        description="Ordered list of document UUIDs to compile into the dossier (1 to 30 items)",
    )
    title: Optional[str] = Field(
        None,
        max_length=200,
        description="Optional custom title for the dossier cover page and header",
    )

    @field_validator("document_ids")
    @classmethod
    def validate_document_ids(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if not v:
            raise ValueError("El dossier debe contener al menos 1 documento.")
        if len(v) > 30:
            raise ValueError("El dossier no puede exceder el límite máximo de 30 documentos.")
        if len(v) != len(set(v)):
            raise ValueError("La lista de documentos contiene identificadores duplicados.")
        return v

    @field_validator("title")
    @classmethod
    def sanitize_title_input(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            trimmed = v.strip()
            return trimmed if trimmed else None
        return None
