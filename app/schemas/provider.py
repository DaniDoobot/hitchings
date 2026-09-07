"""Provider usage schemas."""

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class ProviderUsageBase(BaseModel):
    """Base schema for ProviderUsage."""
    provider: str
    period: str
    records_used: int = 0
    soft_limit: Optional[int] = None
    hard_limit: Optional[int] = None
    allow_paid_usage: bool = False


class ProviderUsageRead(ProviderUsageBase):
    """Schema for reading ProviderUsage with persisted attributes."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
