"""SQLAlchemy models for AI Analysis: prompts, analysis, topics, and audit calls."""

import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from sqlalchemy import (
    String,
    DateTime,
    Text,
    ForeignKey,
    Integer,
    Float,
    Boolean,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class AnalysisPromptVersion(Base):
    """Stores versioned prompts for AI analysis stages (e.g. triage, deep_analysis)."""

    __tablename__ = "analysis_prompt_versions"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_prompt_code_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    response_schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True, default=dict
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<AnalysisPromptVersion {self.code}:v{self.version} stage={self.stage}>"


class EntryAnalysis(Base):
    """Point-in-time analysis result of an Entry evaluated against a TrackingMatrix."""

    __tablename__ = "entry_analyses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    matrix_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tracking_matrices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    pipeline_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True, default="pending"
    )

    entry_content_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    matrix_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    matrix_snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )

    relevance_status: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True
    )
    relevance_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_points: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    entry = relationship("Entry", back_populates="analyses")
    matrix = relationship("TrackingMatrix")
    topics = relationship(
        "EntryAnalysisTopic",
        back_populates="analysis",
        cascade="all, delete-orphan",
        order_by="desc(EntryAnalysisTopic.is_primary)",
    )
    calls = relationship(
        "AnalysisCall",
        back_populates="entry_analysis",
        cascade="all, delete-orphan",
        order_by="AnalysisCall.created_at",
    )

    def __repr__(self) -> str:
        return f"<EntryAnalysis id={self.id} entry_id={self.entry_id} status={self.status} relevance={self.relevance_status}>"


class EntryAnalysisTopic(Base):
    """Normalized association between an EntryAnalysis and relevant TrackingTopics."""

    __tablename__ = "entry_analysis_topics"
    __table_args__ = (
        UniqueConstraint("analysis_id", "topic_id", name="uq_analysis_topic"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entry_analyses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tracking_topics.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # Relationships
    analysis = relationship("EntryAnalysis", back_populates="topics")
    topic = relationship("TrackingTopic")

    def __repr__(self) -> str:
        return f"<EntryAnalysisTopic analysis_id={self.analysis_id} topic_id={self.topic_id} primary={self.is_primary}>"


class AnalysisCall(Base):
    """Audit record for individual LLM / model API calls within an analysis."""

    __tablename__ = "analysis_calls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entry_analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entry_analyses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_prompt_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True, default="pending"
    )

    request_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    input_chars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_chars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Optional[float]] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    raw_response: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    error_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    call_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True, default=dict
    )

    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # Relationships
    entry_analysis = relationship("EntryAnalysis", back_populates="calls")
    prompt_version = relationship("AnalysisPromptVersion")

    def __repr__(self) -> str:
        return f"<AnalysisCall id={self.id} provider={self.provider} model={self.model} status={self.status}>"
