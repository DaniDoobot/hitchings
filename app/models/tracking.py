"""Tracking configuration domain models: TrackingMatrix, TrackingTopic, TrackedEntity, and associations."""

import uuid
from datetime import datetime, timezone
from typing import Optional, Any, List

from sqlalchemy import String, Boolean, Integer, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class TrackingMatrix(Base):
    """Versioned tracking matrix defining the scope and rules of intelligence gathering."""
    __tablename__ = "tracking_matrices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)  # draft, active, archived
    
    relevance_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exclusion_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False
    )
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    topics = relationship("TrackingTopic", back_populates="matrix", cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("status", "draft")
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<TrackingMatrix id={self.id} code='{self.code}' status='{self.status}'>"


class TrackingTopic(Base):
    """Thematic topic or subtopic forming the hierarchy of interest for a tracking matrix."""
    __tablename__ = "tracking_topics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    matrix_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tracking_matrices.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tracking_topics.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    relevance_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    keywords: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)
    discovery_queries: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)
    
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    provisional: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint("matrix_id", "code", name="uq_tracking_topics_matrix_code"),
    )

    # Relationships
    matrix = relationship("TrackingMatrix", back_populates="topics")
    parent = relationship("TrackingTopic", remote_side=[id], back_populates="children")
    children = relationship("TrackingTopic", back_populates="parent", cascade="all, delete-orphan")
    entity_associations = relationship("TrackedEntityTopic", back_populates="topic", cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("priority", 0)
        kwargs.setdefault("active", True)
        kwargs.setdefault("provisional", True)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<TrackingTopic id={self.id} code='{self.code}' name='{self.name}' provisional={self.provisional}>"


class TrackedEntity(Base):
    """Person, organization, institution, or publication monitored by HITCHINGS."""
    __tablename__ = "tracked_entities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(50), default="unknown", nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Mapped to 'metadata' column in Postgres without conflicting with Base.metadata in Python
    metadata_: Mapped[Optional[dict[str, Any]]] = mapped_column("metadata", JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False
    )

    # Relationships
    topic_associations = relationship("TrackedEntityTopic", back_populates="entity", cascade="all, delete-orphan")
    sources = relationship("Source", back_populates="tracked_entity")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("active", True)
        kwargs.setdefault("entity_type", "unknown")
        # Support kwargs passing 'metadata' or 'metadata_'
        if "metadata" in kwargs and "metadata_" not in kwargs:
            kwargs["metadata_"] = kwargs.pop("metadata")
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<TrackedEntity id={self.id} name='{self.display_name}' type='{self.entity_type}'>"


class TrackedEntityTopic(Base):
    """Association between a TrackedEntity and a TrackingTopic."""
    __tablename__ = "tracked_entity_topics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    tracked_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tracked_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    tracking_topic_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tracking_topics.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tracked_entity_id", "tracking_topic_id", name="uq_entity_topic"),
    )

    # Relationships
    entity = relationship("TrackedEntity", back_populates="topic_associations")
    topic = relationship("TrackingTopic", back_populates="entity_associations")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("is_primary", False)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<TrackedEntityTopic entity={self.tracked_entity_id} topic={self.tracking_topic_id} primary={self.is_primary}>"
