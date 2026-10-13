"""Source database model representing news, web, and social sources."""

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from sqlalchemy import String, Boolean, DateTime, Text, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, Session

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
    LINKEDIN = "linkedin"
    LINKEDIN_PROFILE = "linkedin_profile"
    LINKEDIN_COMPANY = "linkedin_company"
    LINKEDIN_SEARCH = "linkedin_search"


# Canonical source constants for manual document ingestion (Bloque 11C)
MANUAL_UPLOAD_SOURCE_ID = uuid.UUID("a0000000-0000-4000-a000-000000000001")
MANUAL_UPLOAD_SOURCE_NAME = "Aportación Manual / Fondo Documental"
MANUAL_UPLOAD_SOURCE_CODE = "manual_upload"
MANUAL_UPLOAD_SOURCE_URL = "https://hitchings.internal/manual-upload"


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
        SAEnum(SourceType, name="source_type", native_enum=True, values_callable=lambda x: [e.value for e in x]),
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
    ingestion_runs = relationship("IngestionRun", back_populates="source", cascade="all, delete-orphan", order_by="desc(IngestionRun.started_at)")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("active", True)
        kwargs.setdefault("provider", "native")
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<Source id={self.id} name='{self.name}' type='{self.type}' active={self.active}>"


def get_or_create_manual_source(db: Session) -> Source:
    """Resolve or idempotently create the canonical Source for manual document uploads."""
    source = (
        db.query(Source)
        .filter(
            (Source.id == MANUAL_UPLOAD_SOURCE_ID)
            | (Source.name == MANUAL_UPLOAD_SOURCE_NAME)
        )
        .first()
    )
    if not source:
        # Search by config logical code if present
        all_sources = db.query(Source).all()
        for s in all_sources:
            if s.config and isinstance(s.config, dict) and s.config.get("code") == MANUAL_UPLOAD_SOURCE_CODE:
                source = s
                break

    if not source:
        source = Source(
            id=MANUAL_UPLOAD_SOURCE_ID,
            name=MANUAL_UPLOAD_SOURCE_NAME,
            type=SourceType.WEBSITE,
            url=MANUAL_UPLOAD_SOURCE_URL,
            active=True,
            category="manual_document",
            provider="native",
            config={
                "code": MANUAL_UPLOAD_SOURCE_CODE,
                "ingestion_mode": "manual",
            },
        )
        db.add(source)
        db.flush()
    return source
