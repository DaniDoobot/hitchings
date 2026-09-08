"""Pydantic schemas for AI analysis models, payloads, and API responses."""

import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    # v3 Grounding evidence fields
    evidence: Optional[list["GroundingEvidence"]] = Field(None, description="Triage grounding evidence quotes")
    summary_evidence: Optional[list["GroundingEvidence"]] = Field(None, description="Deep summary grounding evidence quotes")
    key_point_items: Optional[list["KeyPointV3"]] = Field(None, description="Deep key points with individual evidence")


# ---------------------------------------------------------------------------
# Gemini Developer API Structured Output Schemas (Bloque 7B — v2)
# Preserved intact for historical v2 auditability and playback.
# ---------------------------------------------------------------------------

class TriageAnalysisResult(BaseModel):
    """Structured output schema for the TRIAGE stage (v2).

    Used as Structured Output schema in Gemini API calls.
    The model must return valid JSON matching this schema.
    """
    relevance_score: int = Field(
        ..., ge=0, le=100,
        description="Puntuación de relevancia HITCHINGS de 0 (no relevante) a 100 (muy relevante)"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Confianza en la clasificación de 0.0 a 1.0"
    )
    topic_codes: list[str] = Field(
        default_factory=list,
        description="Códigos de temas HITCHINGS aplicables. Usar únicamente los códigos proporcionados."
    )
    primary_topic_code: Optional[str] = Field(
        None,
        description="Código del tema principal. Debe ser uno de topic_codes. Null si topic_codes está vacío."
    )
    reason: str = Field(
        ...,
        description="Justificación breve y específica en castellano de la puntuación de relevancia asignada."
    )


class DeepAnalysisResult(BaseModel):
    """Structured output schema for the DEEP ANALYSIS stage (v2).

    Only executed when relevance_status == 'relevant'.
    Does NOT modify relevance_score, topics, or primary topic from triage.
    """
    summary: str = Field(
        ...,
        description=(
            "Resumen jurídico preciso del documento en castellano. "
            "Longitud orientativa: 150-300 palabras. No añadir relleno."
        )
    )
    key_points: list[str] = Field(
        ...,
        description=(
            "Lista de 3 a 6 puntos clave concretos y no redundantes en castellano. "
            "Cada punto debe ser específico y útil para el observatorio HITCHINGS."
        )
    )


# ---------------------------------------------------------------------------
# Gemini Developer API Structured Output Schemas (Bloque 7E — v3 Grounded)
# Evidence-grounded pipeline with deterministic quote verification.
# ---------------------------------------------------------------------------

class GroundingEvidence(BaseModel):
    """Verbatim evidence quote extracted from source text for strict grounding."""
    source_field: str = Field(
        ...,
        description="Campo de origen de la cita: 'title', 'content' o 'excerpt'."
    )
    quote: str = Field(
        ...,
        description="Cita textual exacta copiada VERBATIM de la fuente original. No traducir, no parafrasear, sin markdown."
    )


class TriageAnalysisResultV3(BaseModel):
    """Structured output schema for TRIAGE stage v3 with mandatory grounding evidence."""
    relevance_score: int = Field(
        ..., ge=0, le=100,
        description="Puntuación de relevancia HITCHINGS de 0 (no relevante) a 100 (muy relevante)"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Confianza en la clasificación de 0.0 a 1.0"
    )
    topic_codes: list[str] = Field(
        default_factory=list,
        description="Códigos de temas HITCHINGS aplicables. Usar únicamente los códigos proporcionados."
    )
    primary_topic_code: Optional[str] = Field(
        None,
        description="Código del tema principal. Debe ser uno de topic_codes. Null si topic_codes está vacío."
    )
    reason: str = Field(
        ...,
        description="Justificación específica en castellano de la puntuación de relevancia asignada."
    )
    evidence: list[GroundingEvidence] = Field(
        default_factory=list,
        description=(
            "1 a 3 evidencias textuales breves copiadas VERBATIM de la fuente original "
            "(title, content o excerpt) que sustentan la decisión, incluso para not_relevant o uncertain."
        )
    )


class KeyPointV3(BaseModel):
    """Individual key point item with mandatory grounding evidence quotes."""
    point: str = Field(
        ...,
        description="Punto clave concreto, sustantivo y no redundante en castellano."
    )
    evidence: list[GroundingEvidence] = Field(
        ...,
        min_length=1,
        description="Al menos 1 evidencia textual breve copiada VERBATIM de la fuente que respalda este punto."
    )


class DeepAnalysisResultV3(BaseModel):
    """Structured output schema for DEEP ANALYSIS stage v3 with mandatory grounding evidence."""
    summary: str = Field(
        ...,
        description=(
            "Resumen jurídico preciso en castellano (orientativamente 150-300 palabras cuando el material lo justifique). "
            "Exclusivamente basado en el material suministrado."
        )
    )
    summary_evidence: list[GroundingEvidence] = Field(
        default_factory=list,
        description="2 a 4 evidencias textuales representativas copiadas VERBATIM de la fuente que respaldan las ideas materiales centrales del resumen."
    )
    key_points: list[KeyPointV3] = Field(
        ...,
        min_length=1,
        description="Lista de 3 a 6 puntos clave en castellano, cada uno obligatoriamente respaldado por al menos 1 evidencia textual."
    )



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
    prompt_code: Optional[str] = None
    prompt_version: Optional[int] = None
    prompt_stage: Optional[str] = None
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

    @field_validator("key_points", mode="before")
    @classmethod
    def normalize_key_points(cls, v: Any) -> Optional[list[str]]:
        """Ensure key_points is strictly list[str] on API serialization, converting historical dicts if present."""
        if v is None:
            return None
        if not isinstance(v, list):
            return None
        result: list[str] = []
        for item in v:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict) and "point" in item:
                result.append(str(item["point"]))
            elif hasattr(item, "point"):
                result.append(str(getattr(item, "point")))
            else:
                result.append(str(item))
        return result


class EntryAnalysisDetailResponse(EntryAnalysisResponse):
    """Detailed response including full snapshot, topic associations, audit calls, and grounding evidence."""
    matrix_snapshot: dict[str, Any]
    topics: list[EntryAnalysisTopicResponse] = Field(default_factory=list, description="Raw model-assigned topic classifications")
    canonical_topics: list[EntryAnalysisTopicResponse] = Field(
        default_factory=list,
        description="Canonical topics view removing redundant ancestor categories when specific descendants are selected",
    )
    canonical_primary_topic: Optional[EntryAnalysisTopicResponse] = Field(
        None,
        description="Canonical primary topic resolved deterministically",
    )
    calls: list[AnalysisCallResponse] = Field(default_factory=list)
    grounding_evidence: Optional[dict[str, Any]] = Field(None, description="Structured grounding evidence extracted from v3 audit calls")


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
