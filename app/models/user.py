"""User and AuthSession database models for private portal authentication (Bloque 8C.1)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class UserRole:
    """Supported portal user authorization roles."""
    ADMIN = "admin"
    USER = "user"
    ALL = (ADMIN, USER)


class User(Base):
    """Registered portal user account."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(50),
        default=UserRole.USER,
        server_default="user",
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    sessions: Mapped[list[AuthSession]] = relationship(
        "AuthSession",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="desc(AuthSession.created_at)",
    )

    def __repr__(self) -> str:
        return f"<User {self.email} ({'active' if self.is_active else 'inactive'})>"


class AuthSession(Base):
    """Server-side authenticated session holding cryptographically hashed token."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="sessions")

    def is_valid(self) -> bool:
        """Check if the session is currently active, unrevoked, and unexpired."""
        now = utc_now()
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return self.revoked_at is None and exp > now

    def __repr__(self) -> str:
        status = "revoked" if self.revoked_at else ("expired" if not self.is_valid() else "active")
        return f"<AuthSession {self.id} user={self.user_id} status={status}>"
