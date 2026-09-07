"""Extractor for European Commission / DG Competition news and press releases."""

import asyncio
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
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


def parse_iso_datetime(date_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string into a timezone-aware UTC datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as exc:
        logger.debug("Could not parse ISO datetime '%s': %s", date_str, exc)
        return None


def parse_human_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse human readable date like '26 August 2026' into UTC datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%d %B %Y").replace(tzinfo=timezone.utc)
        return dt
    except Exception as exc:
        logger.debug("Could not parse human date '%s': %s", date_str, exc)
        return None


def parse_slug_date(url: str) -> Optional[datetime]:
    """Extract YYYY-MM-DD date from URL slug as last resort fallback."""
    match = re.search(r"[_\-](20\d\d)-(0[1-9]|1[0-2])-([0-2]\d|3[01])(?:[_\-]|\.|$)", url)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
        except Exception:
            return None
    return None


@dataclass
class EnrichmentResult:
    content: Optional[str] = None
    excerpt: Optional[str] = None
    editorial_date: Optional[datetime] = None
    date_source: str = "rss_pubdate"
    presscorner_ref: Optional[str] = None
    extra_metadata: Optional[dict] = None


class EuropeanCommissionExtractor:
    """Extractor for European Commission DG Competition feed with Press Corner enrichment."""

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.concurrency = concurrency

    async def extract(self, client: httpx.AsyncClient, source: Source) -> list[RawEntryData]:
        """Fetch RSS feed items and enrich with full article text and editorial dates."""
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

        # Enrich body content and editorial dates with bounded concurrency
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _enrich_item(item: dict) -> RawEntryData:
            url = item["url"]
            rss_pub_date = item.get("rss_pub_date")

            enrichment = EnrichmentResult(
                editorial_date=rss_pub_date,
                date_source="rss_pubdate",
            )

            async with semaphore:
                try:
                    enrichment = await self.fetch_enrichment(
                        client,
                        url,
                        fallback_description=item.get("description"),
                        rss_pub_date=rss_pub_date,
                    )
                except Exception as exc:
                    logger.warning("Failed to enrich EC item %s: %s", url, exc)
                    # Fallback to description
                    desc = item.get("description")
                    if desc:
                        clean_desc = BeautifulSoup(desc, "html.parser").get_text(strip=True)
                        enrichment.content = clean_desc
                        enrichment.excerpt = clean_desc

            # Construct raw_metadata preserving RSS date and tracking the source used for published_at
            raw_metadata = dict(item.get("raw_metadata", {}))
            raw_metadata["rss_pub_date"] = rss_pub_date.isoformat() if rss_pub_date else None
            raw_metadata["publication_date_source"] = enrichment.date_source
            if enrichment.presscorner_ref:
                raw_metadata["presscorner_ref"] = enrichment.presscorner_ref
            if enrichment.extra_metadata:
                raw_metadata.update(enrichment.extra_metadata)

            final_published_at = enrichment.editorial_date or rss_pub_date

            return RawEntryData(
                url=url,
                title=item.get("title"),
                content=enrichment.content,
                excerpt=enrichment.excerpt,
                published_at=final_published_at,
                external_id=item.get("guid") or url,
                language=item.get("language") or "en",
                content_type="institutional_news",
                raw_metadata=raw_metadata,
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
            rss_pub_date = parse_rfc822_date(pub_date_str)
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
                "rss_pub_date": rss_pub_date,
                "guid": guid,
                "language": feed_lang or "en",
                "categories": categories,
                "raw_metadata": raw_metadata,
            })

        return parsed_items

    async def fetch_enrichment(
        self,
        client: httpx.AsyncClient,
        url: str,
        fallback_description: Optional[str],
        rss_pub_date: Optional[datetime],
    ) -> EnrichmentResult:
        """Fetch article content and resolve real editorial publication date via hierarchy."""
        res = EnrichmentResult(
            editorial_date=rss_pub_date,
            date_source="rss_pubdate",
        )

        presscorner_ref = self._extract_presscorner_ref(url)
        node_html: Optional[str] = None

        # 1. If not a direct presscorner URL, fetch competition-policy node page
        if not presscorner_ref and "competition-policy.ec.europa.eu" in url:
            try:
                node_resp = await client.get(url)
                if node_resp.status_code == 200:
                    node_html = node_resp.text
                    presscorner_ref = self._find_presscorner_link_in_html(node_html)
                    # If no presscorner link, parse content from node HTML
                    if not presscorner_ref:
                        res.content, res.excerpt = self._parse_node_page_html(node_html)
            except Exception as exc:
                logger.debug("Failed fetching node page %s: %s", url, exc)

        # 2. Priority 1: Press Corner API (content + publishDate/eventDate)
        if presscorner_ref:
            lang, ref = presscorner_ref
            res.presscorner_ref = ref
            try:
                api_url = f"{PRESSCORNER_API_BASE}?reference={ref}&language={lang}"
                api_resp = await client.get(api_url)
                if api_resp.status_code == 200 and "application/json" in api_resp.headers.get("content-type", ""):
                    doc = api_resp.json()
                    dl = doc.get("docuLanguageResource", {})
                    html_content = dl.get("htmlContent")
                    if html_content:
                        res.content, res.excerpt = self._parse_html_body(html_content)

                    # Extract official document date
                    api_publish_date = doc.get("publishDate")
                    if api_publish_date:
                        parsed_dt = parse_iso_datetime(api_publish_date)
                        if parsed_dt:
                            res.editorial_date = parsed_dt
                            res.date_source = "presscorner_api"
                            res.extra_metadata = {"presscorner_publish_date": api_publish_date}

                    if not res.editorial_date and doc.get("eventDate"):
                        parsed_event = parse_iso_datetime(doc["eventDate"]) or parse_human_date(doc["eventDate"])
                        if parsed_event:
                            res.editorial_date = parsed_event
                            res.date_source = "presscorner_api_event"
            except Exception as exc:
                logger.debug("Failed fetching Press Corner API for ref %s: %s", ref, exc)

        # 3. Priority 2 & 3: Node page structured metadata (JSON-LD datePublished, meta og:updated_time, text)
        if res.date_source == "rss_pubdate" and node_html:
            # 2a. JSON-LD datePublished
            jsonld_dt = self._extract_jsonld_date(node_html)
            if jsonld_dt:
                res.editorial_date = jsonld_dt
                res.date_source = "node_jsonld"
            else:
                # 2b. Meta tag og:updated_time / article:published_time
                meta_dt = self._extract_meta_date(node_html)
                if meta_dt:
                    res.editorial_date = meta_dt
                    res.date_source = "node_meta"
                else:
                    # 2c. Visible text 'Publication date ...'
                    text_dt = self._extract_visible_date(node_html)
                    if text_dt:
                        res.editorial_date = text_dt
                        res.date_source = "node_text"

        # 4. Priority 4: URL slug date (e.g. ...-2026-08-26_en)
        if res.date_source == "rss_pubdate":
            slug_dt = parse_slug_date(url)
            if slug_dt:
                res.editorial_date = slug_dt
                res.date_source = "url_slug"

        # 5. Content fallback if still None
        if not res.content and fallback_description:
            clean_desc = BeautifulSoup(fallback_description, "html.parser").get_text(strip=True)
            if clean_desc:
                res.content = clean_desc
                res.excerpt = clean_desc[:300]

        return res

    # Backwards compatibility alias
    async def fetch_content_and_excerpt(
        self,
        client: httpx.AsyncClient,
        url: str,
        fallback_description: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        """Fetch article content via Press Corner API or HTML page, with graceful fallback."""
        enrichment = await self.fetch_enrichment(client, url, fallback_description, None)
        return enrichment.content, enrichment.excerpt

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
    def _extract_jsonld_date(html: str) -> Optional[datetime]:
        """Extract datePublished from JSON-LD NewsArticle snippet."""
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and data.get("@type") == "NewsArticle":
                    date_pub = data.get("datePublished")
                    if date_pub:
                        return parse_iso_datetime(date_pub)
            except Exception:
                continue
        return None

    @staticmethod
    def _extract_meta_date(html: str) -> Optional[datetime]:
        """Extract date from OpenGraph or standard meta tags."""
        soup = BeautifulSoup(html, "html.parser")
        for prop in ["article:published_time", "og:updated_time", "date", "publication_date"]:
            meta = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
            if meta and meta.get("content"):
                dt = parse_iso_datetime(meta["content"])
                if dt:
                    return dt
        return None

    @staticmethod
    def _extract_visible_date(html: str) -> Optional[datetime]:
        """Extract date from visible text 'Publication date: DD Month YYYY'."""
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text()
        match = re.search(r"Publication date\s*([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})", text)
        if match:
            return parse_human_date(match.group(1))
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
