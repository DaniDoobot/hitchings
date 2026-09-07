"""Apify provider stub for alternative/fallback social scraping."""

import logging
from app.core.config import get_settings
from app.models.source import Source
from app.providers.base import (
    BaseSourceProvider,
    RawEntryData,
    ProviderDisabledError,
)

logger = logging.getLogger(__name__)


class ApifyProvider(BaseSourceProvider):
    """External provider fallback using Apify."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def name(self) -> str:
        return "apify"

    def is_enabled(self) -> bool:
        return self.settings.APIFY_ENABLED

    def can_handle(self, source: Source) -> bool:
        return source.provider == self.name

    def check_limit_available(self, estimated_records: int = 1) -> bool:
        return self.is_enabled()

    async def fetch_entries(self, source: Source) -> list[RawEntryData]:
        """Fetch entries via Apify actor.
        
        Deliberately disabled and mocked in Block 0.
        """
        if not self.is_enabled():
            raise ProviderDisabledError(
                f"Apify provider is disabled (APIFY_ENABLED=false). Cannot process source '{source.name}'."
            )
        
        logger.warning("Apify stub invoked: no real API calls executed in Block 0.")
        return []
