"""Pydantic schemas for authentication and user sessions (Bloque 8C.1)."""

from __future__ import annotations

import uuid
from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """User credentials for authentication."""

    email: str = Field(..., description="User email address")
    password: str = Field(..., min_length=1, description="Plaintext password")


class UserPublic(BaseModel):
    """Safe public representation of an authenticated user (no secrets, no internal metadata)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str


class LoginResponse(BaseModel):
    """Successful authentication response."""

    user: UserPublic
