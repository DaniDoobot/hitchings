"""Native provider for direct scraping and feed ingestion (RSS, public webs, blogs)."""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx

from app.models.source import Source, SourceType
from app.providers.base import BaseSourceProvider, RawEntryData, ProviderError

logger = logging.getLogger(__name__)

NATIVE_SUPPORTED_TYPES = {
    SourceType.WEBSITE,
    SourceType.RSS,
    SourceType.INSTITUTIONAL,
    SourceType.BLOG,
    SourceType.GOOGLE_NEWS,
}

USER_AGENT = "HITCHINGS/0.1 (+https://github.com/hitchings; news-observatory)"
DEFAULT_TIMEOUT = 15.0


def parse_rfc822_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse RFC 822 / RFC 2822 date string into a timezone-aware UTC datetime."""
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as exc:
        logger.warning("Could not parse date '%s': %s", date_str, exc)
        return None


class NativeProvider(BaseSourceProvider):
    """Direct provider for zero-cost public sources, starting with RSS feeds."""

    @property
    def name(self) -> str:
        return "native"

    def is_enabled(self) -> bool:
        return True

    def can_handle(self, source: Source) -> bool:
        return source.provider == self.name and source.type in NATIVE_SUPPORTED_TYPES

    def check_limit_available(self, estimated_records: int = 1) -> bool:
        return True

    async def fetch_entries(self, source: Source) -> list[RawEntryData]:
        """Fetch raw entries from the given source."""
        if not self.can_handle(source):
            raise ValueError(f"NativeProvider cannot handle source {source.name} (type={source.type})")

        url_lower = (source.url or "").lower()

        # 1. European Commission / Digital Markets Act (DMA) adapter
        if "digital-markets-act.ec.europa.eu" in url_lower:
            from app.providers.extractors.european_commission_dma import EuropeanCommissionDMAExtractor
            extractor = EuropeanCommissionDMAExtractor()
            headers = {"User-Agent": USER_AGENT}
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client:
                return await extractor.extract(client, source)

        # 2. European Commission / DG Competition adapter
        if "competition-policy.ec.europa.eu" in url_lower or "ec.europa.eu" in url_lower:
            from app.providers.extractors.european_commission import EuropeanCommissionExtractor
            extractor = EuropeanCommissionExtractor()
            headers = {"User-Agent": USER_AGENT}
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client:
                return await extractor.extract(client, source)

        # 3. CJEU / CURIA adapter
        if "curia.europa.eu" in url_lower:
            from app.providers.extractors.curia import CuriaCaseLawExtractor
            extractor = CuriaCaseLawExtractor()
            headers = {"User-Agent": USER_AGENT}
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client:
                return await extractor.extract(client, source)

        # 4. OECD / Competition Law and Policy adapter
        if "oecd.org" in url_lower or "oecd" in (source.name or "").lower():
            from app.providers.extractors.oecd_competition import OECDCompetitionExtractor
            extractor = OECDCompetitionExtractor()
            headers = {"User-Agent": USER_AGENT}
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client:
                return await extractor.extract(client, source)

        if source.type == SourceType.RSS:
            return await self._fetch_rss(source)
        elif source.type == SourceType.WEBSITE:
            return await self._fetch_website(source)

        logger.info(
            "NativeProvider stub invoked for source '%s' (type=%s, real scraping reserved for future blocks)",
            source.name,
            source.type
        )
        return []

    async def _fetch_website(self, source: Source) -> list[RawEntryData]:
        """Asynchronously fetch entries from a website source."""
        if not source.url:
            raise ProviderError(f"Source '{source.name}' has no URL configured for website ingestion")

        # Specific website adapter dispatch: CNMC
        if "cnmc.es" in source.url.lower():
            from app.providers.extractors.cnmc import CNMCNewsExtractor
            extractor = CNMCNewsExtractor()
            headers = {"User-Agent": USER_AGENT}
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client:
                return await extractor.extract(client, source)

        # Specific website adapter dispatch: CAT (Competition Appeal Tribunal)
        if "catribunal.org.uk" in source.url.lower():
            from app.providers.extractors.competition_appeal_tribunal import CompetitionAppealTribunalExtractor
            extractor = CompetitionAppealTribunalExtractor()
            headers = {"User-Agent": USER_AGENT}
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client:
                return await extractor.extract(client, source)

        # Specific website adapter dispatch: CJEU / CURIA
        if "curia.europa.eu" in source.url.lower():
            from app.providers.extractors.curia import CuriaCaseLawExtractor
            extractor = CuriaCaseLawExtractor()
            headers = {"User-Agent": USER_AGENT}
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client:
                return await extractor.extract(client, source)

        # Specific website adapter dispatch: European Commission / Digital Markets Act (DMA)
        if "digital-markets-act.ec.europa.eu" in source.url.lower():
            from app.providers.extractors.european_commission_dma import EuropeanCommissionDMAExtractor
            extractor = EuropeanCommissionDMAExtractor()
            headers = {"User-Agent": USER_AGENT}
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client:
                return await extractor.extract(client, source)

        # Specific website adapter dispatch: OECD
        if "oecd.org" in source.url.lower() or "oecd" in (source.name or "").lower():
            from app.providers.extractors.oecd_competition import OECDCompetitionExtractor
            extractor = OECDCompetitionExtractor()
            headers = {"User-Agent": USER_AGENT}
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=headers, follow_redirects=True) as client:
                return await extractor.extract(client, source)

        raise ProviderError(f"No website extractor implemented yet for URL: {source.url}")

    async def _fetch_rss(self, source: Source) -> list[RawEntryData]:
        """Asynchronously fetch and parse an RSS feed."""
        if not source.url:
            raise ProviderError(f"Source '{source.name}' has no URL configured for RSS ingestion")

        limit = 20
        if source.config and isinstance(source.config, dict):
            limit = source.config.get("initial_fetch_limit", limit)

        logger.info("Fetching RSS from '%s' (limit=%s)...", source.url, limit)

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
                response = await client.get(source.url, headers=headers)
        except httpx.RequestError as exc:
            logger.error("HTTP error requesting RSS for '%s': %s", source.name, exc)
            raise ProviderError(f"Network error fetching RSS feed: {exc}") from exc

        if response.status_code != 200:
            logger.error("RSS response HTTP %d for '%s'", response.status_code, source.url)
            raise ProviderError(f"RSS endpoint returned HTTP status {response.status_code}")

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            logger.error("Failed to parse XML from '%s': %s", source.url, exc)
            raise ProviderError(f"Malformed XML in RSS feed: {exc}") from exc

        channel = root.find("channel")
        if channel is None:
            # Fallback if items are at root level (some RSS dialects)
            items = root.findall("item")
            feed_language = None
        else:
            items = channel.findall("item")
            feed_language = channel.findtext("language")

        logger.info("Found %d items in RSS feed '%s'", len(items), source.name)

        raw_entries: list[RawEntryData] = []
        for item in items[:limit]:
            # Extract standard tags
            title = (item.findtext("title") or "").strip() or None
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip() or None
            pub_date_str = item.findtext("pubDate")
            published_at = parse_rfc822_date(pub_date_str)
            guid = (item.findtext("guid") or "").strip() or None

            # Extract creator / author
            creator = None
            for child in item:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if tag in ("creator", "author"):
                    creator = (child.text or "").strip() or None
                    if creator:
                        break

            # Collect raw metadata
            raw_metadata: dict[str, str] = {}
            for child in item:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if child.text:
                    raw_metadata[tag] = child.text.strip()

            if not link:
                logger.warning("Skipping RSS item without link in source '%s': %s", source.name, title)
                continue

            content_type = "institutional_news" if source.category == "institutional" else "news_article"

            raw_entries.append(
                RawEntryData(
                    url=link,
                    title=title,
                    content=None,  # Full content parsing can be enriched later if required
                    excerpt=description,
                    author=creator,
                    published_at=published_at,
                    external_id=guid,
                    language=feed_language,
                    content_type=content_type,
                    raw_metadata=raw_metadata,
                )
            )

        logger.info("Extracted %d valid raw entries from RSS feed '%s'", len(raw_entries), source.name)
        return raw_entries
