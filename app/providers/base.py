"""Base interface and data contracts for data source providers."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field

from app.models.source import Source


class RawEntryData(BaseModel):
    """Normalized data transfer object representing a raw captured item before persistence."""
    url: str
    title: Optional[str] = None
    content: Optional[str] = None
    excerpt: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    external_id: Optional[str] = None
    language: Optional[str] = None
    content_type: Optional[str] = None
    raw_metadata: Optional[dict[str, Any]] = Field(default_factory=dict)


class ProviderError(Exception):
    """Base exception for provider-related errors."""
    pass


class ProviderDisabledError(ProviderError):
    """Raised when an operation is attempted on a disabled provider."""
    pass


class ProviderLimitExceededError(ProviderError):
    """Raised when monthly quota or safety threshold is reached and paid usage is disallowed."""
    pass


class BaseSourceProvider(ABC):
    """Abstract contract for source data collection providers.
    
    Designed for asynchronous I/O (HTTP requests, streaming) while keeping
    persistence decoupled and synchronous for simplicity in early phases.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider identifier (e.g. 'native', 'brightdata', 'apify')."""
        pass

    @abstractmethod
    def can_handle(self, source: Source) -> bool:
        """Determine whether this provider can extract data from the given source."""
        pass

    @abstractmethod
    def is_enabled(self) -> bool:
        """Check if provider is globally enabled via settings."""
        pass

    @abstractmethod
    def check_limit_available(self, estimated_records: int = 1) -> bool:
        """Validate if usage limits permit fetching new records under Free-First policy."""
        pass

    @abstractmethod
    async def fetch_entries(self, source: Source) -> list[RawEntryData]:
        """Asynchronously extract and return raw entries from the given source."""
        pass
