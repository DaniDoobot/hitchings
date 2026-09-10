"""Extractor for European Commission / Digital Markets Act (DMA) news and press releases.

Scrapes the official DMA portal (https://digital-markets-act.ec.europa.eu/news_en),
extracts Drupal ECL structured items, fetches substantive detail articles,
resolves author Directorates-General (DG COMP | DG CNECT), detects Press Corner links
(for cross-source deduplication), and collects PDF document attachments.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

import httpx
from bs4 import BeautifulSoup

from app.models.source import Source
from app.providers.base import RawEntryData, ProviderError
from app.providers.extractors.european_commission import (
    parse_iso_datetime,
    parse_human_date,
    parse_slug_date,
)

logger = logging.getLogger(__name__)

DMA_NEWS_DEFAULT_URL = "https://digital-markets-act.ec.europa.eu/news_en"
DEFAULT_CONCURRENCY = 4
SUFFICIENCY_THRESHOLD_FULL = 500
SUFFICIENCY_THRESHOLD_PARTIAL = 200

PRESSCORNER_REGEX = re.compile(r"presscorner/detail/([a-zA-Z]{2})/([a-zA-Z0-9_]+)", re.IGNORECASE)


class EuropeanCommissionDMAExtractor:
    """Direct website extractor for European Commission Digital Markets Act news."""

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.concurrency = concurrency

    async def extract(self, client: httpx.AsyncClient, source: Source) -> list[RawEntryData]:
        """Fetch listing pages from DMA portal and enrich with detail page contents."""
        base_url = source.url or DMA_NEWS_DEFAULT_URL
        config = source.config or {}
        limit = config.get("initial_fetch_limit", 20)
        lookback_days = config.get("lookback_days")

        logger.info("Extracting European Commission DMA news from '%s' (limit=%d)...", base_url, limit)

        # 1. Fetch listing cards across pages until limit is satisfied
        discovered_cards = await self._fetch_listing_cards(
            client=client,
            base_url=base_url,
            limit=limit,
            lookback_days=lookback_days,
        )
        logger.info("Discovered %d candidate DMA items from listing", len(discovered_cards))

        if not discovered_cards:
            return []

        # 2. Enrich candidate items concurrently bounded by semaphore
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _enrich_card(card: dict) -> RawEntryData:
            async with semaphore:
                return await self._enrich_item(client=client, card=card)

        tasks = [_enrich_card(card) for card in discovered_cards]
        raw_entries = await asyncio.gather(*tasks)
        return list(raw_entries)

    async def _fetch_listing_cards(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        limit: int,
        lookback_days: Optional[int] = None,
    ) -> list[dict]:
        """Paginate Drupal ECL listing until limit is reached or items exceed lookback."""
        cards: list[dict] = []
        page_idx = 0
        max_pages = max(1, (limit + 9) // 10 + 1)
        cutoff_dt: Optional[datetime] = None

        if lookback_days is not None and lookback_days > 0:
            from datetime import timedelta
            cutoff_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        while len(cards) < limit and page_idx < max_pages:
            # Build paginated URL
            parsed = urlparse(base_url)
            query = parse_qs(parsed.query)
            if page_idx > 0:
                query["page"] = [str(page_idx)]
            elif "page" in query:
                del query["page"]
            new_query = urlencode(query, doseq=True)
            page_url = parsed._replace(query=new_query).geturl()

            logger.debug("Fetching DMA listing page %d: %s", page_idx, page_url)
            try:
                resp = await client.get(page_url)
            except httpx.RequestError as exc:
                logger.error("HTTP error fetching DMA listing %s: %s", page_url, exc)
                if page_idx == 0:
                    raise ProviderError(f"Network error fetching DMA listing: {exc}") from exc
                break

            if resp.status_code != 200:
                logger.error("DMA listing %s returned HTTP status %d", page_url, resp.status_code)
                if page_idx == 0:
                    raise ProviderError(f"DMA listing returned HTTP status {resp.status_code}")
                break

            items_on_page = self.parse_listing_page(resp.content, base_url)
            if not items_on_page:
                logger.debug("No articles found on DMA listing page %d, ending pagination", page_idx)
                break

            reached_cutoff = False
            for item in items_on_page:
                if cutoff_dt and item.get("listing_date") and item["listing_date"] < cutoff_dt:
                    reached_cutoff = True
                    break
                cards.append(item)
                if len(cards) >= limit:
                    break

            if reached_cutoff or len(items_on_page) < 10:
                break

            page_idx += 1

        return cards

    def parse_listing_page(self, html_bytes: bytes, base_url: str) -> list[dict]:
        """Parse ECL content items from DMA listing HTML."""
        soup = BeautifulSoup(html_bytes, "html.parser")
        articles = soup.select("article.ecl-content-item")
        parsed: list[dict] = []

        for art in articles:
            # Title & URL
            title_el = art.select_one(
                ".ecl-content-block__title a, .ecl-content-item__title a, h1 a, h2 a, h3 a"
            )
            if not title_el or not title_el.get("href"):
                continue

            raw_href = title_el["href"].strip()
            item_url = urljoin(base_url, raw_href)
            title = title_el.get_text(strip=True)

            # Metadata (Item type & Date)
            meta_items = [
                li.get_text(strip=True)
                for li in art.select(".ecl-content-block__primary-meta-item")
            ]
            item_type_raw = meta_items[0] if meta_items else "News article"

            # Parse date from <time datetime="..."> or text
            time_el = art.select_one("time")
            listing_date: Optional[datetime] = None
            if time_el:
                if time_el.get("datetime"):
                    listing_date = parse_iso_datetime(time_el["datetime"])
                if not listing_date and time_el.get_text(strip=True):
                    listing_date = parse_human_date(time_el.get_text(strip=True))

            # Excerpt from description
            desc_el = art.select_one(".ecl-content-block__description")
            excerpt = desc_el.get_text(strip=True) if desc_el else None

            parsed.append({
                "title": title,
                "url": item_url,
                "raw_item_type": item_type_raw,
                "listing_date": listing_date,
                "excerpt": excerpt,
            })

        return parsed

    async def _enrich_item(self, client: httpx.AsyncClient, card: dict) -> RawEntryData:
        """Fetch article detail page and enrich with full body, authors, Press Corner and PDF links."""
        url = card["url"]
        title = card["title"]
        listing_date = card.get("listing_date")
        card_excerpt = card.get("excerpt")
        raw_type = card.get("raw_item_type", "News article")

        # Map item type to observatory content_type
        content_type = "institutional_news"
        if "press release" in raw_type.lower():
            content_type = "press_release"

        content: Optional[str] = None
        excerpt = card_excerpt
        author: Optional[str] = None
        editorial_date: Optional[datetime] = listing_date
        date_source: str = "listing_time" if listing_date else "none"
        presscorner_ref: Optional[str] = None
        presscorner_url: Optional[str] = None
        pdf_links: list[str] = []

        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                html = resp.text
                soup = BeautifulSoup(html, "html.parser")

                # 1. Parse Authors and Publication Date from <dl class="ecl-description-list">
                dl = soup.select_one("dl.ecl-description-list")
                if dl:
                    terms = [dt.get_text(strip=True) for dt in dl.select("dt")]
                    defs = [dd.get_text(strip=True) for dd in dl.select("dd")]
                    dl_dict = dict(zip(terms, defs))

                    if "Authors" in dl_dict:
                        author = dl_dict["Authors"].replace("|", " | ").strip()
                    elif "Author" in dl_dict:
                        author = dl_dict["Author"].replace("|", " | ").strip()

                    if "Publication date" in dl_dict:
                        dt_val = parse_human_date(dl_dict["Publication date"])
                        if dt_val:
                            editorial_date = dt_val
                            date_source = "detail_dl_date"

                # 2. Extract substantive content from <article>
                article_el = soup.select_one("article") or soup.select_one("main")
                if article_el:
                    # Clean text
                    clean_text = article_el.get_text(separator="\n\n", strip=True)
                    if clean_text:
                        content = clean_text

                    # Refined excerpt from first paragraph if available
                    first_p = article_el.find("p")
                    if first_p:
                        p_text = first_p.get_text(strip=True)
                        if p_text:
                            excerpt = p_text[:400] if len(p_text) > 400 else p_text

                    # 3. Detect Press Corner and PDF links
                    for a in article_el.select("a[href]"):
                        href = a["href"].strip()
                        resolved_href = urljoin(url, href)

                        # Check Press Corner reference
                        pc_match = PRESSCORNER_REGEX.search(resolved_href)
                        if pc_match and not presscorner_ref:
                            lang = pc_match.group(1).lower()
                            ref_raw = pc_match.group(2)
                            presscorner_ref = ref_raw.upper().replace("_", "/")
                            presscorner_url = resolved_href

                        # Check PDF document
                        if resolved_href.lower().endswith(".pdf") or "/cases/" in resolved_href.lower():
                            if resolved_href not in pdf_links:
                                pdf_links.append(resolved_href)

        except Exception as exc:
            logger.warning("Failed to enrich DMA detail page %s: %s", url, exc)

        # Fallback to slug date if no date resolved
        if not editorial_date:
            slug_dt = parse_slug_date(url)
            if slug_dt:
                editorial_date = slug_dt
                date_source = "url_slug"

        # Content fallback to card excerpt
        if not content and excerpt:
            content = excerpt

        # Calculate text sufficiency
        text_len = len(content or "")
        if text_len >= SUFFICIENCY_THRESHOLD_FULL:
            sufficiency = "FULL"
        elif text_len >= SUFFICIENCY_THRESHOLD_PARTIAL:
            sufficiency = "PARTIAL"
        else:
            sufficiency = "INSUFFICIENT"

        raw_metadata = {
            "source_portal": "Digital Markets Act",
            "portal_section": "News",
            "raw_item_type": raw_type,
            "author_departments": author,
            "publication_date_source": date_source,
            "presscorner_ref": presscorner_ref,
            "presscorner_url": presscorner_url,
            "pdf_links": pdf_links,
            "content_sufficiency": sufficiency,
            "text_length": text_len,
        }
        if listing_date:
            raw_metadata["listing_date"] = listing_date.isoformat()

        return RawEntryData(
            url=url,
            title=title,
            content=content,
            excerpt=excerpt,
            author=author or "European Commission",
            published_at=editorial_date,
            external_id=url,
            language="en",
            content_type=content_type,
            raw_metadata=raw_metadata,
        )
