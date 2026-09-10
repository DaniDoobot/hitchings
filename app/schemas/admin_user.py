"""Pydantic schemas for administrative user management (Bloque 11A)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AdminUserPublic(BaseModel):
    """Administrative representation of a user including role, status, and activity timestamps."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None


class AdminUserCreateRequest(BaseModel):
    """Payload for creating a new user from administrative portal."""

    email: str = Field(..., description="User email address")
    display_name: str = Field(..., min_length=1, max_length=255, description="Display name")
    role: Literal["admin", "user"] = Field(default="user", description="Authorization role")
    password: str = Field(..., min_length=12, description="Initial plaintext password (min 12 chars)")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not cleaned or "@" not in cleaned or "." not in cleaned.split("@")[-1]:
            raise ValueError("Dirección de correo electrónico no válida.")
        return cleaned

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("El nombre no puede estar vacío.")
        return cleaned


class AdminUserUpdateRequest(BaseModel):
    """Payload for editing an existing user."""

    email: Optional[str] = Field(default=None, description="Updated email address")
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=255, description="Updated display name")
    role: Optional[Literal["admin", "user"]] = Field(default=None, description="Updated authorization role")
    is_active: Optional[bool] = Field(default=None, description="Account active status")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip().lower()
        if not cleaned or "@" not in cleaned or "." not in cleaned.split("@")[-1]:
            raise ValueError("Dirección de correo electrónico no válida.")
        return cleaned

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("El nombre no puede estar vacío.")
        return cleaned


class AdminResetPasswordRequest(BaseModel):
    """Payload for setting a new password administratively."""

    password: str = Field(..., min_length=12, description="New plaintext password (min 12 chars)")
