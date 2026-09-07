"""Native provider stub for direct scraping (public webs, RSS, blogs, institutions)."""

import logging
from app.models.source import Source, SourceType
from app.providers.base import BaseSourceProvider, RawEntryData

logger = logging.getLogger(__name__)

NATIVE_SUPPORTED_TYPES = {
    SourceType.WEBSITE,
    SourceType.RSS,
    SourceType.INSTITUTIONAL,
    SourceType.BLOG,
    SourceType.GOOGLE_NEWS,
}


class NativeProvider(BaseSourceProvider):
    """Direct scraper provider for zero-cost public sources."""

    @property
    def name(self) -> str:
        return "native"

    def is_enabled(self) -> bool:
        return True

    def can_handle(self, source: Source) -> bool:
        return source.provider == self.name and source.type in NATIVE_SUPPORTED_TYPES

    def check_limit_available(self, estimated_records: int = 1) -> bool:
        # Native direct requests do not have external SaaS quotas
        return True

    async def fetch_entries(self, source: Source) -> list[RawEntryData]:
        """Fetch entries directly.
        
        Deliberately unfulfilled in Block 0 (real scraping starts in subsequent blocks).
        """
        if not self.can_handle(source):
            raise ValueError(f"NativeProvider cannot handle source {source.name} (type={source.type})")
        
        logger.info("NativeProvider stub invoked for source '%s' (no real scraping in Block 0)", source.name)
        return []
