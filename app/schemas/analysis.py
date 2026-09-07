"""Pydantic schemas for AI analysis models, payloads, and API responses."""

import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Structured LLM Payload Schemas
# ---------------------------------------------------------------------------

class AIAnalysisTopicItem(BaseModel):
    """Topic classification item returned by analysis model."""
    topic_code: str = Field(..., description="Unique code of the tracking topic")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Confidence level 0.0 - 1.0")
    is_primary: bool = Field(False, description="Whether this is the primary topic (max 1 allowed)")
    rationale: Optional[str] = Field(None, description="Explanation for why this topic was assigned")


class AIAnalysisResponsePayload(BaseModel):
    """Structured response contract expected from an AI analysis provider."""
    relevance_score: int = Field(..., ge=0, le=100, description="Relevance score from 0 to 100")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Overall confidence 0.0 - 1.0")
    topics: list[AIAnalysisTopicItem] = Field(default_factory=list, description="Classified topics")
    summary: Optional[str] = Field(None, description="Executive summary of the publication")
    key_points: list[str] = Field(default_factory=list, description="Key legal/regulatory bullet points")
    reason: Optional[str] = Field(None, description="Reasoning behind the relevance score")


# ---------------------------------------------------------------------------
# Prompt Version API Schemas
# ---------------------------------------------------------------------------

class PromptVersionBase(BaseModel):
    code: str
    version: int
    stage: str
    name: str
    description: Optional[str] = None
    system_prompt: str
    user_prompt_template: str
    response_schema_version: str = "v1"
    config: Optional[dict[str, Any]] = None
    active: bool = True


class PromptVersionCreate(PromptVersionBase):
    pass


class PromptVersionResponse(PromptVersionBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Topic Association API Schemas
# ---------------------------------------------------------------------------

class EntryAnalysisTopicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    analysis_id: uuid.UUID
    topic_id: uuid.UUID
    confidence: Optional[float] = None
    is_primary: bool = False
    rationale: Optional[str] = None
    created_at: datetime
    topic_code: Optional[str] = None
    topic_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Analysis Call Audit API Schemas
# ---------------------------------------------------------------------------

class AnalysisCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entry_analysis_id: uuid.UUID
    prompt_version_id: uuid.UUID
    stage: str
    provider: str
    model: str
    status: str
    request_hash: Optional[str] = None
    input_chars: Optional[int] = None
    output_chars: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    latency_ms: Optional[int] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    call_metadata: Optional[dict[str, Any]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Entry Analysis API Schemas
# ---------------------------------------------------------------------------

class EntryAnalysisResponse(BaseModel):
    """Summary response for an EntryAnalysis."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entry_id: uuid.UUID
    matrix_id: uuid.UUID
    pipeline_version: str
    status: str
    entry_content_hash: Optional[str] = None
    matrix_snapshot_hash: str
    relevance_status: Optional[str] = None
    relevance_score: Optional[int] = None
    confidence: Optional[float] = None
    summary: Optional[str] = None
    reason: Optional[str] = None
    key_points: Optional[list[str]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class EntryAnalysisDetailResponse(EntryAnalysisResponse):
    """Detailed response including full snapshot, topic associations, and audit calls."""
    matrix_snapshot: dict[str, Any]
    topics: list[EntryAnalysisTopicResponse] = Field(default_factory=list)
    calls: list[AnalysisCallResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Analysis Usage & Cost Summary
# ---------------------------------------------------------------------------

class AnalysisUsageResponse(BaseModel):
    """Aggregated usage and cost metrics across analysis calls."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_estimated_cost_usd: float = 0.0
    by_provider: dict[str, Any] = Field(default_factory=dict)
    by_stage: dict[str, Any] = Field(default_factory=dict)
