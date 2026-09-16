"""Ingestion schemas for results, runs history, and source technical status."""

import enum
import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict


class IngestionResult(BaseModel):
    """Execution summary returned after an ingestion run."""
    ingestion_run_id: uuid.UUID
    source_id: uuid.UUID
    status: str
    fetched: int
    created: int
    duplicates: int
    failed: int = 0
    started_at: datetime
    finished_at: datetime
    duration_ms: Optional[int] = None
    latest_published_at: Optional[datetime] = None
    oldest_published_at: Optional[datetime] = None


class IngestionRunResponse(BaseModel):
    """Detailed information for an IngestionRun record."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str
    fetched_count: int
    created_count: int
    duplicate_count: int
    failed_count: int
    duration_ms: Optional[int] = None
    latest_published_at: Optional[datetime] = None
    oldest_published_at: Optional[datetime] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime


class FreshnessStatus(str, enum.Enum):
    """Dynamically calculated freshness status for a data source."""
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class FreshnessDetail(BaseModel):
    """Freshness evaluation details."""
    status: FreshnessStatus
    warning_hours: int
    hours_since_latest: Optional[float] = None


class IngestionRunSummary(BaseModel):
    """Summary of the most recent IngestionRun."""
    id: uuid.UUID
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str
    fetched: int
    created: int
    duplicates: int
    failed: int
    duration_ms: Optional[int] = None


class SourceStatusResponse(BaseModel):
    """Technical observability status for a Source."""
    source_id: uuid.UUID
    name: str
    active: bool
    last_run_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_run_status: Optional[str] = None
    latest_published_at: Optional[datetime] = None
    freshness: FreshnessDetail
    last_run: Optional[IngestionRunSummary] = None


class SourceStatusSummaryItem(BaseModel):
    """Summary item for the list of all sources' operational status."""
    source: str
    source_id: uuid.UUID
    source_type: str
    enabled: bool
    last_execution: Optional[datetime] = None
    last_success: Optional[datetime] = None
    entries_created: int = 0
    errors: int = 0
    status: str  # "healthy", "degraded", "disabled"


class SourceQualityMetricsItem(BaseModel):
    """Aggregated quality and efficiency metrics for a source."""
    source_id: uuid.UUID
    source_name: str
    source_type: str
    is_linkedin: bool = False
    posts_captured: int = 0
    entries_created: int = 0
    total_analyzed: int = 0
    relevant_count: int = 0
    relevant_pct: float = 0.0
    deep_analysis_count: int = 0
    deep_analysis_pct: float = 0.0
    avg_analysis_time_ms: Optional[float] = None
    estimated_gemini_cost_usd: float = 0.0
    estimated_provider_cost_usd: float = 0.0


class SourceQualityMetricsResponse(BaseModel):
    """Response containing quality metrics across sources."""
    sources: list[SourceQualityMetricsItem]
    generated_at: datetime

