"""Health check response schemas."""

from typing import Optional
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Schema for basic API health check."""
    status: str = "ok"
    service: str = "hitchings"


class DbHealthResponse(BaseModel):
    """Schema for database connection health check."""
    status: str
    database: str
    detail: Optional[str] = None
