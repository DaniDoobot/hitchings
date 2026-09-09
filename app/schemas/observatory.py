"""Client-facing Pydantic schemas for the Observatory API (BLOQUE 8A).

These schemas define the product-facing API contract.
They intentionally exclude ALL internal/technical fields:
  - pipeline_version, prompt_version
  - entry_content_hash, matrix_snapshot, matrix_snapshot_hash
  - raw_response, AnalysisCall details
  - estimated_cost_usd, provider, model
  - GroundingValidator internals
  - EntryAnalysis.status (if it is current, it is available)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Atomic building blocks
# ---------------------------------------------------------------------------


class ObservatorySourceRef(BaseModel):
    """Compact source reference embedded in entry responses."""

    id: uuid.UUID
    name: str


class ObservatoryTopicItem(BaseModel):
    """A canonical topic associated with an entry (minimal client contract)."""

    code: str
    name: str


class ObservatoryRelevance(BaseModel):
    """Relevance metadata for an entry's current analysis."""

    status: str = Field(..., description="relevant | uncertain | not_relevant")
    score: int = Field(..., ge=0, le=100)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Evidence (client-facing, no raw_response)
# ---------------------------------------------------------------------------


class ObservatoryEvidenceQuote(BaseModel):
    """A single verbatim quote from the source document."""

    source_field: Optional[str] = Field(None, description="title | content | excerpt")
    quote: str


class ObservatoryKeyPointEvidence(BaseModel):
    """A key point with its supporting verbatim quotes."""

    point: str
    quotes: list[ObservatoryEvidenceQuote] = Field(default_factory=list)


class ObservatoryEvidence(BaseModel):
    """Structured evidence extracted from the current analysis deep call.

    - summary_quotes: verbatim quotes supporting the overall summary
    - key_points: key points each with their supporting quotes
    - source: 'deep' when from deep_analysis call, 'triage' when only triage available, 'none' otherwise
    """

    summary_quotes: list[ObservatoryEvidenceQuote] = Field(default_factory=list)
    key_points: list[ObservatoryKeyPointEvidence] = Field(default_factory=list)
    source: str = Field("deep", description="deep | triage | none")


# ---------------------------------------------------------------------------
# Entry list item
# ---------------------------------------------------------------------------


class ObservatoryEntryListItem(BaseModel):
    """Summary entry item for the observatory list endpoint."""

    model_config = ConfigDict(from_attributes=False)

    entry_id: uuid.UUID
    title: Optional[str]
    source: ObservatorySourceRef
    published_at: Optional[datetime]
    url: str
    content_type: Optional[str]

    relevance: ObservatoryRelevance
    summary: Optional[str]
    canonical_topics: list[ObservatoryTopicItem] = Field(default_factory=list)
    canonical_primary_topic: Optional[ObservatoryTopicItem] = None
    key_points: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Entry detail
# ---------------------------------------------------------------------------


class ObservatoryEntryDetail(BaseModel):
    """Full entry detail including evidence, for the observatory detail endpoint."""

    model_config = ConfigDict(from_attributes=False)

    entry_id: uuid.UUID
    title: Optional[str]
    source: ObservatorySourceRef
    published_at: Optional[datetime]
    url: str
    content_type: Optional[str]

    relevance: ObservatoryRelevance
    summary: Optional[str]
    canonical_topics: list[ObservatoryTopicItem] = Field(default_factory=list)
    canonical_primary_topic: Optional[ObservatoryTopicItem] = None
    key_points: list[str] = Field(default_factory=list)

    evidence: ObservatoryEvidence = Field(
        default_factory=ObservatoryEvidence,
        description="Verbatim evidence from the current analysis. No internal audit data.",
    )


# ---------------------------------------------------------------------------
# Paginated list response
# ---------------------------------------------------------------------------


class ObservatoryListResponse(BaseModel):
    """Standard paginated response envelope for the entries list."""

    items: list[ObservatoryEntryListItem]
    total: int = Field(..., description="Total matching items after all filters")
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class ObservatorySourceDetail(BaseModel):
    """Source information for the sources discovery endpoint."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    name: str
    type: str
    url: Optional[str]
    entry_count: int = 0
    latest_published_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Topics (hierarchical)
# ---------------------------------------------------------------------------


class ObservatoryTopicNode(BaseModel):
    """Topic node in the hierarchical taxonomy."""

    model_config = ConfigDict(from_attributes=False)

    code: str
    name: str
    parent_code: Optional[str] = None
    description: Optional[str] = None
    children: list["ObservatoryTopicNode"] = Field(default_factory=list)


ObservatoryTopicNode.model_rebuild()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class ObservatoryTopicCount(BaseModel):
    """Aggregated count for a canonical topic in dashboard."""

    code: str
    name: str
    count: int


class ObservatorySourceCount(BaseModel):
    """Aggregated count for a source in dashboard."""

    source_id: uuid.UUID
    name: str
    publication_count: int
    relevant_count: int


class ObservatoryLatestRelevantEntry(BaseModel):
    """Compact entry for the latest relevant dashboard widget."""

    entry_id: uuid.UUID
    title: Optional[str]
    source: ObservatorySourceRef
    published_at: Optional[datetime]
    score: int
    summary: Optional[str]
    canonical_topics: list[ObservatoryTopicItem] = Field(default_factory=list)


class ObservatoryDashboard(BaseModel):
    """Dashboard KPIs for the observatory portal home screen."""

    total_publications: int
    relevant_count: int
    uncertain_count: int
    not_relevant_count: int

    publications_last_7_days: int
    publications_last_30_days: int
    relevant_last_30_days: int

    top_topics: list[ObservatoryTopicCount] = Field(default_factory=list)
    top_sources: list[ObservatorySourceCount] = Field(default_factory=list)
    latest_relevant_entries: list[ObservatoryLatestRelevantEntry] = Field(
        default_factory=list
    )
