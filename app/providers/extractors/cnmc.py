"""HTML extractor for CNMC press releases and news listing."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.models.source import Source
from app.providers.base import RawEntryData, ProviderError

logger = logging.getLogger(__name__)

CNMC_BASE_URL = "https://www.cnmc.es/prensa/noticias"
DEFAULT_CONCURRENCY = 4


class CNMCNewsExtractor:
    """HTML extractor for CNMC press releases and news listing."""

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.concurrency = concurrency

    async def extract(self, client: httpx.AsyncClient, source: Source) -> list[RawEntryData]:
        """Fetch listing pages and article bodies from CNMC website."""
        base_url = source.url or CNMC_BASE_URL
        limit = 20
        if source.config and isinstance(source.config, dict):
            limit = source.config.get("initial_fetch_limit", limit)

        logger.info("Extracting CNMC news from '%s' (limit=%d)...", base_url, limit)

        # 1. Fetch listing items page by page until limit is reached
        items_to_fetch: list[dict] = []
        page = 0
        max_pages = 10  # safety circuit breaker

        while len(items_to_fetch) < limit and page < max_pages:
            page_url = f"{base_url}?page={page}" if page > 0 else base_url
            try:
                resp = await client.get(page_url)
            except httpx.RequestError as exc:
                logger.error("HTTP error requesting CNMC listing page %s: %s", page_url, exc)
                if page == 0:
                    raise ProviderError(f"Network error fetching CNMC listing: {exc}") from exc
                break

            if resp.status_code != 200:
                logger.warning("CNMC listing page %s returned status %d", page_url, resp.status_code)
                if page == 0:
                    raise ProviderError(f"CNMC website returned HTTP status {resp.status_code}")
                break

            page_items = self.parse_listing_page(resp.text, page_url)
            if not page_items:
                logger.info("No more items found on page %d", page)
                break

            for it in page_items:
                items_to_fetch.append(it)
                if len(items_to_fetch) >= limit:
                    break

            page += 1

        logger.info("Found %d listing items across %d pages for CNMC", len(items_to_fetch), page)

        # 2. Fetch full body content for each item with bounded concurrency
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _enrich_item(item_data: dict) -> RawEntryData:
            url = item_data["url"]
            body_content: Optional[str] = None
            excerpt: Optional[str] = None

            async with semaphore:
                try:
                    art_resp = await client.get(url)
                    if art_resp.status_code == 200:
                        body_content, excerpt = self.parse_article_body(art_resp.text)
                except Exception as exc:
                    logger.warning("Could not fetch article body from %s: %s", url, exc)

            return RawEntryData(
                url=url,
                title=item_data.get("title"),
                content=body_content,
                excerpt=excerpt,
                published_at=item_data.get("published_at"),
                external_id=url,
                language="es",
                content_type="institutional_news",
                raw_metadata=item_data.get("raw_metadata", {}),
            )

        tasks = [_enrich_item(it) for it in items_to_fetch]
        raw_entries = await asyncio.gather(*tasks)
        return list(raw_entries)

    def parse_listing_page(self, html: str, page_url: str) -> list[dict]:
        """Parse Drupal views-row items from the listing page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all(class_=lambda c: c and "views-row" in c)
        parsed_items: list[dict] = []

        for row in rows:
            # 1. Date
            published_at = None
            time_el = row.find("time")
            if time_el and time_el.get("datetime"):
                try:
                    published_at = datetime.fromisoformat(time_el["datetime"]).astimezone(timezone.utc)
                except Exception as exc:
                    logger.debug("Failed to parse datetime '%s': %s", time_el.get("datetime"), exc)

            # 2. Title & Link
            title_span = row.find(class_=lambda c: c and "views-field-title" in c)
            a_tag = title_span.find("a") if title_span else None
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True) or None
            raw_href = a_tag.get("href", "").strip()
            if not raw_href:
                continue

            clean_href = raw_href.split("?")[0]
            item_url = urljoin(page_url, clean_href)

            # 3. Sector / Category tag
            tag_el = row.find(class_=lambda c: c and "views-field-field-tags" in c)
            sector = tag_el.get_text(strip=True) if tag_el else None
            if not sector:
                sec_el = row.find(class_=lambda c: c and "text-secondary" in c)
                sector = sec_el.get_text(strip=True) if sec_el else None

            raw_metadata = {
                "sector": sector,
                "source_page": page_url,
            }

            parsed_items.append({
                "title": title,
                "url": item_url,
                "published_at": published_at,
                "sector": sector,
                "raw_metadata": raw_metadata,
            })

        return parsed_items

    def parse_article_body(self, html: str) -> tuple[Optional[str], Optional[str]]:
        """Extract clean body text and excerpt from article detail page."""
        soup = BeautifulSoup(html, "html.parser")
        body_el = soup.find(class_="page-nw-article-body")
        if not body_el:
            body_el = soup.find(class_=lambda c: c and "field--name-body" in c)

        if not body_el:
            return None, None

        # Clean main content text
        content = body_el.get_text(separator="\n\n", strip=True) or None

        # Excerpt: prefer first paragraph, fallback to first 300 chars
        first_p = body_el.find("p")
        if first_p:
            p_text = first_p.get_text(strip=True)
            excerpt = p_text[:400] if len(p_text) > 400 else p_text
        elif content:
            excerpt = content[:300]
        else:
            excerpt = None

        return content, excerpt
