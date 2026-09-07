"""IngestionRun database model representing execution history, metrics, and traceability."""

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from sqlalchemy import String, Integer, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class IngestionRunStatus(str, enum.Enum):
    """Possible execution states for an ingestion run."""
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class IngestionRun(Base):
    """Execution log for data source ingestion runs."""
    __tablename__ = "ingestion_runs"

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
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=IngestionRunStatus.RUNNING.value,
        nullable=False,
        index=True
    )
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    latest_published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    oldest_published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    error_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Note: Named 'metadata' in DB, mapped to 'run_metadata' in Python to prevent conflict with Base.metadata
    run_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False
    )

    # Relationships
    source = relationship("Source", back_populates="ingestion_runs")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("status", IngestionRunStatus.RUNNING.value)
        kwargs.setdefault("fetched_count", 0)
        kwargs.setdefault("created_count", 0)
        kwargs.setdefault("duplicate_count", 0)
        kwargs.setdefault("failed_count", 0)
        super().__init__(**kwargs)

    @property
    def duration_ms(self) -> Optional[int]:
        """Compute execution duration in milliseconds if finished."""
        if self.started_at and self.finished_at:
            return int((self.finished_at - self.started_at).total_seconds() * 1000)
        return None

    def __repr__(self) -> str:
        return f"<IngestionRun id={self.id} source_id={self.source_id} status={self.status} fetched={self.fetched_count}>"
