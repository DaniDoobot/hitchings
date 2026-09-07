"""Entry database model representing captured content from any source."""

import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from sqlalchemy import String, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class Entry(Base):
    """Model representing an article, post, or publication captured from a source."""
    __tablename__ = "entries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    
    url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    canonical_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    
    # Title is nullable: some social posts (e.g. LinkedIn updates) don't have separate titles
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )
    
    language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    
    # Unstructured original payload / provider metadata
    raw_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

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
    source = relationship("Source", back_populates="entries")
    analyses = relationship(
        "EntryAnalysis",
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="desc(EntryAnalysis.created_at)",
    )

    def __repr__(self) -> str:
        return f"<Entry id={self.id} source_id={self.source_id} title='{self.title[:30] if self.title else 'No Title'}'>"
