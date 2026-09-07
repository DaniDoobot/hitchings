"""Bright Data provider stub for social/anti-bot sources (specifically LinkedIn)."""

import logging
from app.core.config import get_settings
from app.models.source import Source, SourceType
from app.providers.base import (
    BaseSourceProvider,
    RawEntryData,
    ProviderDisabledError,
)

logger = logging.getLogger(__name__)

BRIGHTDATA_SUPPORTED_TYPES = {
    SourceType.LINKEDIN_PROFILE,
    SourceType.LINKEDIN_COMPANY,
    SourceType.LINKEDIN_SEARCH,
}


class BrightDataProvider(BaseSourceProvider):
    """External provider for LinkedIn data using Bright Data.
    
    Adheres to the Free-First policy: disabled by default, strictly capped
    to prevent accidental paid charges unless explicitly allowed.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def name(self) -> str:
        return "brightdata"

    def is_enabled(self) -> bool:
        return self.settings.BRIGHTDATA_ENABLED

    def can_handle(self, source: Source) -> bool:
        return source.provider == self.name and source.type in BRIGHTDATA_SUPPORTED_TYPES

    def check_limit_available(self, estimated_records: int = 1) -> bool:
        """Check if requested usage fits within safety limits without incurring paid cost."""
        if not self.is_enabled():
            return False
        # In Block 0, real quota accounting is reserved for later
        return True

    async def fetch_entries(self, source: Source) -> list[RawEntryData]:
        """Fetch entries via Bright Data API.
        
        Deliberately disabled and mocked in Block 0.
        """
        if not self.is_enabled():
            raise ProviderDisabledError(
                f"Bright Data provider is disabled (BRIGHTDATA_ENABLED=false). "
                f"Cannot process source '{source.name}'."
            )
        
        if not self.can_handle(source):
            raise ValueError(f"BrightDataProvider cannot handle source {source.name}")

        logger.warning("Bright Data stub invoked: no real API calls executed in Block 0.")
        return []
