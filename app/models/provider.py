"""Provider usage tracking model for free-first budget control."""

import uuid
from datetime import datetime, timezone
from typing import Optional, Any

from sqlalchemy import String, Integer, Boolean, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class ProviderUsage(Base):
    """Model tracking monthly resource usage and limits per provider."""
    __tablename__ = "provider_usage"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)  # Format: "YYYY-MM"
    records_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    soft_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    hard_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    allow_paid_usage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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
        UniqueConstraint("provider", "period", name="uq_provider_usage_provider_period"),
    )

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("records_used", 0)
        kwargs.setdefault("allow_paid_usage", False)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<ProviderUsage provider='{self.provider}' period='{self.period}' used={self.records_used}>"
