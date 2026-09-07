"""Extractor for European Commission / DG Competition news and press releases."""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.models.source import Source
from app.providers.base import RawEntryData, ProviderError

logger = logging.getLogger(__name__)

EC_COMPETITION_RSS_DEFAULT = "https://competition-policy.ec.europa.eu/node/38/rss_en"
PRESSCORNER_API_BASE = "https://ec.europa.eu/commission/presscorner/api/documents"
DEFAULT_CONCURRENCY = 4

# Policy areas in DG Competition for semantic classification
KNOWN_POLICY_AREAS = [
    "antitrust",
    "mergers",
    "state aid",
    "cartels",
    "foreign subsidies",
    "competition law",
    "competition policy",
    "digital markets",
]


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


class EuropeanCommissionExtractor:
    """Extractor for European Commission DG Competition feed with Press Corner enrichment."""

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.concurrency = concurrency

    async def extract(self, client: httpx.AsyncClient, source: Source) -> list[RawEntryData]:
        """Fetch RSS feed items and enrich with full article text from Press Corner or EC portal."""
        feed_url = source.url or EC_COMPETITION_RSS_DEFAULT
        limit = 20
        if source.config and isinstance(source.config, dict):
            limit = source.config.get("initial_fetch_limit", limit)

        logger.info("Extracting European Commission news from '%s' (limit=%d)...", feed_url, limit)

        headers = {
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }

        try:
            resp = await client.get(feed_url, headers=headers)
        except httpx.RequestError as exc:
            logger.error("HTTP error fetching EC feed %s: %s", feed_url, exc)
            raise ProviderError(f"Network error fetching European Commission feed: {exc}") from exc

        if resp.status_code != 200:
            logger.error("EC feed %s returned HTTP status %d", feed_url, resp.status_code)
            raise ProviderError(f"European Commission feed returned HTTP status {resp.status_code}")

        parsed_items = self.parse_feed(resp.content, limit, feed_url)
        logger.info("Parsed %d items from European Commission feed", len(parsed_items))

        # Enrich body content with bounded concurrency
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _enrich_item(item: dict) -> RawEntryData:
            url = item["url"]
            body_content: Optional[str] = None
            excerpt: Optional[str] = None

            async with semaphore:
                try:
                    body_content, excerpt = await self.fetch_content_and_excerpt(client, url, item.get("description"))
                except Exception as exc:
                    logger.warning("Failed to enrich EC item %s: %s", url, exc)
                    # Fallback to description
                    desc = item.get("description")
                    if desc:
                        clean_desc = BeautifulSoup(desc, "html.parser").get_text(strip=True)
                        body_content = clean_desc
                        excerpt = clean_desc

            return RawEntryData(
                url=url,
                title=item.get("title"),
                content=body_content,
                excerpt=excerpt,
                published_at=item.get("published_at"),
                external_id=item.get("guid") or url,
                language=item.get("language") or "en",
                content_type="institutional_news",
                raw_metadata=item.get("raw_metadata", {}),
            )

        tasks = [_enrich_item(it) for it in parsed_items]
        raw_entries = await asyncio.gather(*tasks)
        return list(raw_entries)

    def parse_feed(self, xml_content: bytes, limit: int, feed_url: str) -> list[dict]:
        """Parse the RSS XML feed into structured item dictionaries."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as exc:
            logger.error("Failed to parse European Commission RSS XML: %s", exc)
            raise ProviderError(f"Malformed XML in European Commission feed: {exc}") from exc

        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else root.findall("item")
        feed_lang = channel.findtext("language") if channel is not None else "en"

        parsed_items: list[dict] = []
        for item in items[:limit]:
            title = (item.findtext("title") or "").strip() or None
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip() or None
            pub_date_str = item.findtext("pubDate")
            published_at = parse_rfc822_date(pub_date_str)
            guid = (item.findtext("guid") or "").strip() or None

            # Collect all categories
            categories = [
                c.text.strip()
                for c in item.findall("category")
                if c.text and c.text.strip()
            ]

            # Detect main policy area
            detected_policy_area = None
            for cat in categories:
                cat_lower = cat.lower()
                for area in KNOWN_POLICY_AREAS:
                    if area in cat_lower:
                        detected_policy_area = area
                        break
                if detected_policy_area:
                    break

            raw_metadata = {
                "categories": categories,
                "policy_area": detected_policy_area,
                "guid": guid,
                "feed_url": feed_url,
                "source_section": "Competition Policy",
            }

            if not link:
                logger.warning("Skipping EC RSS item without link: %s", title)
                continue

            parsed_items.append({
                "title": title,
                "url": link,
                "description": description,
                "published_at": published_at,
                "guid": guid,
                "language": feed_lang or "en",
                "categories": categories,
                "raw_metadata": raw_metadata,
            })

        return parsed_items

    async def fetch_content_and_excerpt(
        self,
        client: httpx.AsyncClient,
        url: str,
        fallback_description: Optional[str]
    ) -> tuple[Optional[str], Optional[str]]:
        """Fetch article content via Press Corner API or HTML page, with graceful fallback."""
        presscorner_ref = self._extract_presscorner_ref(url)

        # If not a direct presscorner URL, check if it's a competition-policy node page
        if not presscorner_ref and "competition-policy.ec.europa.eu" in url:
            try:
                node_resp = await client.get(url)
                if node_resp.status_code == 200:
                    presscorner_ref = self._find_presscorner_link_in_html(node_resp.text)
                    if not presscorner_ref:
                        # Extract text directly from node page HTML
                        return self._parse_node_page_html(node_resp.text)
            except Exception as exc:
                logger.debug("Failed fetching node page %s: %s", url, exc)

        # If we have a Press Corner reference (e.g. ('en', 'IP/26/1769')), call the official API
        if presscorner_ref:
            lang, ref = presscorner_ref
            try:
                api_url = f"{PRESSCORNER_API_BASE}?reference={ref}&language={lang}"
                api_resp = await client.get(api_url)
                if api_resp.status_code == 200:
                    content_type = api_resp.headers.get("content-type", "")
                    if "application/json" in content_type:
                        doc = api_resp.json()
                        dl = doc.get("docuLanguageResource", {})
                        html_content = dl.get("htmlContent")
                        if html_content:
                            return self._parse_html_body(html_content)
            except Exception as exc:
                logger.debug("Failed fetching Press Corner API for ref %s: %s", ref, exc)

        # Fallback to RSS description
        if fallback_description:
            clean_desc = BeautifulSoup(fallback_description, "html.parser").get_text(strip=True)
            if clean_desc:
                return clean_desc, clean_desc[:300]

        return None, None

    @staticmethod
    def _extract_presscorner_ref(url: str) -> Optional[tuple[str, str]]:
        """Extract (lang, normalized_ref) from Press Corner detail URL."""
        match = re.search(r"presscorner/detail/([a-zA-Z]{2})/([a-zA-Z0-9_]+)", url)
        if match:
            lang = match.group(1).lower()
            raw_ref = match.group(2)
            normalized_ref = raw_ref.upper().replace("_", "/")
            return lang, normalized_ref
        return None

    @staticmethod
    def _find_presscorner_link_in_html(html: str) -> Optional[tuple[str, str]]:
        """Find Press Corner link inside a node page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            ref = EuropeanCommissionExtractor._extract_presscorner_ref(href)
            if ref:
                return ref
        return None

    @staticmethod
    def _parse_html_body(html_content: str) -> tuple[Optional[str], Optional[str]]:
        """Extract clean text and excerpt from HTML content."""
        soup = BeautifulSoup(html_content, "html.parser")
        text = soup.get_text(separator="\n\n", strip=True) or None
        first_p = soup.find("p")
        if first_p:
            p_text = first_p.get_text(strip=True)
            excerpt = p_text[:400] if len(p_text) > 400 else p_text
        elif text:
            excerpt = text[:300]
        else:
            excerpt = None
        return text, excerpt

    @staticmethod
    def _parse_node_page_html(html: str) -> tuple[Optional[str], Optional[str]]:
        """Extract main text from a competition-policy node page."""
        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main") or soup.find("article") or soup.find("div", class_="ecl-container")
        if not main:
            return None, None
        text = main.get_text(separator="\n\n", strip=True) or None
        first_p = main.find("p")
        if first_p:
            p_text = first_p.get_text(strip=True)
            excerpt = p_text[:400] if len(p_text) > 400 else p_text
        elif text:
            excerpt = text[:300]
        else:
            excerpt = None
        return text, excerpt
