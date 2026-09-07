"""Source database model representing news, web, and social sources."""

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from sqlalchemy import String, Boolean, DateTime, Text, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class SourceType(str, enum.Enum):
    """Supported source types in HITCHINGS."""
    WEBSITE = "website"
    RSS = "rss"
    GOOGLE_NEWS = "google_news"
    INSTITUTIONAL = "institutional"
    BLOG = "blog"
    LINKEDIN_PROFILE = "linkedin_profile"
    LINKEDIN_COMPANY = "linkedin_company"
    LINKEDIN_SEARCH = "linkedin_search"


class Source(Base):
    """Model representing an information source to be monitored."""
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[SourceType] = mapped_column(
        SAEnum(SourceType, name="source_type", native_enum=True),
        nullable=False
    )
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), default="native", nullable=False)
    
    # Specific configuration per source type (e.g. keywords, custom query, headers)
    config: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    
    # Future scheduler configuration (e.g. interval, cron expression, jitter)
    schedule_config: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

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

    # Optional association with TrackedEntity (person, institution, publication, etc.)
    tracked_entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tracked_entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # Relationships
    entries = relationship("Entry", back_populates="source", cascade="all, delete-orphan")
    tracked_entity = relationship("TrackedEntity", back_populates="sources")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("active", True)
        kwargs.setdefault("provider", "native")
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<Source id={self.id} name='{self.name}' type='{self.type}' active={self.active}>"
