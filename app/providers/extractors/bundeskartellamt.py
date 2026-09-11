"""Extractor for Bundeskartellamt (German Federal Cartel Office) decisions, case reports, and news.

Ingests official publications from https://www.bundeskartellamt.de/ with:
- Fast RSS discovery for recent publications (lookback <= 8 days).
- Comprehensive Sitemap XML discovery for 90-day historical lookback without 30-item limit.
- Deterministic English translation resolution from German detail pages with safe German fallback.
- Case number (Aktenzeichen) extraction (e.g. B12-21/23, B7-54/25).
- Official decision and case report PDF link detection.
- Editorial filtering excluding institutional noise (YouTube videos, vacancies, generic conferences).
"""

from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional, Sequence
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.models.source import Source
from app.providers.base import RawEntryData, ProviderError

logger = logging.getLogger(__name__)

BUNDESKARTELLAMT_SOURCE_NAME = "Bundeskartellamt"
BASE_URL = "https://www.bundeskartellamt.de"
RSS_NEWSFEED_URL = "https://www.bundeskartellamt.de/DE/Service/RSS/_documents/rssnewsfeed.xml"
BUNDESKARTELLAMT_RSS_URL = RSS_NEWSFEED_URL
SITEMAP_XML_URL = "https://www.bundeskartellamt.de/Sitemap_XML.xml"
BUNDESKARTELLAMT_SITEMAP_URL = SITEMAP_XML_URL
DEFAULT_CONCURRENCY = 4

AKTENZEICHEN_REGEX = re.compile(r"\b(B\d{1,2}[-\s/]\d+/\d+|\bVK\s*\d+-\d+/\d+)\b", re.IGNORECASE)

EDITORIAL_EXCLUDE_KEYWORDS = [
    "youtube",
    "jahrespressekonferenz",
    "stellenangebot",
    "karriere",
    "ausbildung",
    "festakt",
    "verabschiedung",
    "personalie",
    "praktikum",
    "social media",
    "veranstaltung",
    "grußwort",
    "jahresrückblick",
]


def parse_rfc822_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse RFC 822 / RFC 2822 date string into timezone-aware UTC datetime."""
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


def parse_slug_date(url: str, text: str = "") -> Optional[datetime]:
    """Extract publication date from GSB URL slug or text."""
    # Pattern 1: YYYY_MM_DD (e.g. /2025_03_04_... or /2026_01_15_...)
    m1 = re.search(r"/(\d{4})_(\d{2})_(\d{2})_", url)
    if m1:
        y, m, d = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        try:
            return datetime(y, m, d, 8, 0, tzinfo=timezone.utc)
        except ValueError:
            pass

    # Pattern 2: DD_MM_YYYY or MM_DD_YYYY or DD_MM_YY (e.g. /09_10_2026_... or /08_17_26_...)
    m2 = re.search(r"/(\d{2})_(\d{2})_(\d{2,4})_", url)
    if m2:
        p1, p2, p3 = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        year = p3 if p3 > 100 else (2000 + p3)
        if p1 > 12:
            day, month = p1, p2
        elif p2 > 12:
            day, month = p2, p1
        else:
            # Check text date for disambiguation: e.g. "17.08.2026"
            text_m = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", text)
            if text_m:
                t_day, t_month, t_year = int(text_m.group(1)), int(text_m.group(2)), int(text_m.group(3))
                return datetime(t_year, t_month, t_day, 8, 0, tzinfo=timezone.utc)
            # Default to month, day
            month, day = p1, p2
        try:
            return datetime(year, month, day, 8, 0, tzinfo=timezone.utc)
        except ValueError:
            pass

    # Fallback to text date in document: e.g. "17.08.2026"
    text_m = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", text)
    if text_m:
        t_day, t_month, t_year = int(text_m.group(1)), int(text_m.group(2)), int(text_m.group(3))
        try:
            return datetime(t_year, t_month, t_day, 8, 0, tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


class BundeskartellamtExtractor:
    """Direct website & RSS extractor for Bundeskartellamt decisions, case reports, and news."""

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.concurrency = concurrency

    async def extract(
        self,
        client: httpx.AsyncClient,
        source: Source,
        lookback_days: Optional[int] = None,
        limit: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> list[RawEntryData]:
        """Discover and enrich candidate items within the specified lookback window."""
        ref_now = now or datetime.now(timezone.utc)
        config = source.config or {}
        effective_limit = limit or config.get("initial_fetch_limit", 30)
        effective_lookback = lookback_days if lookback_days is not None else config.get("lookback_days")

        logger.info(
            "Extracting Bundeskartellamt publications (limit=%d, lookback_days=%s)...",
            effective_limit,
            effective_lookback,
        )

        # 1. Discover raw candidate items (RSS for short lookback, Sitemap for historical)
        candidates = await self.discover_candidates(
            client=client,
            source=source,
            lookback_days=effective_lookback,
            limit=effective_limit,
            now=ref_now,
        )

        logger.info("Discovered %d candidate Bundeskartellamt items after discovery & filtering", len(candidates))

        if not candidates:
            return []

        # 2. Enrich candidates concurrently bounded by semaphore
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _enrich(cand: dict) -> Optional[RawEntryData]:
            async with semaphore:
                try:
                    return await self._enrich_item(client=client, candidate=cand, now=ref_now)
                except Exception as exc:
                    logger.error("Failed to enrich Bundeskartellamt item %s: %s", cand.get("url"), exc)
                    return None

        tasks = [_enrich(cand) for cand in candidates]
        results = await asyncio.gather(*tasks)
        valid_entries = [r for r in results if r is not None]
        logger.info("Successfully enriched %d Bundeskartellamt entries", len(valid_entries))
        return valid_entries

    async def discover_candidates(
        self,
        client: httpx.AsyncClient,
        source: Optional[Source] = None,
        lookback_days: Optional[int] = None,
        limit: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> list[dict]:
        """Discover candidate items using RSS for normal runs or Sitemap for extended lookback."""
        ref_now = now or datetime.now(timezone.utc)
        cutoff_dt = (ref_now - timedelta(days=lookback_days)) if (lookback_days and lookback_days > 0) else None

        # For lookback > 8 days, we must use the Sitemap to avoid the 30-item limit of the RSS feed
        use_sitemap = bool(lookback_days and lookback_days > 8)

        candidates: list[dict] = []
        seen_urls: set[str] = set()

        if use_sitemap:
            logger.info("Using Sitemap XML historical discovery for lookback=%d days", lookback_days)
            sitemap_candidates = await self._discover_from_sitemap(
                client=client,
                source=source,
                cutoff_dt=cutoff_dt,
                now=ref_now,
            )
            for c in sitemap_candidates:
                if c["url"] not in seen_urls:
                    seen_urls.add(c["url"])
                    candidates.append(c)

        # Always check the RSS feed as well (ensures immediate availability of very recent items)
        rss_candidates = await self._discover_from_rss(
            client=client,
            source=source,
            cutoff_dt=cutoff_dt,
            now=ref_now,
        )
        for c in rss_candidates:
            if c["url"] not in seen_urls:
                seen_urls.add(c["url"])
                candidates.append(c)

        # Sort candidates chronologically descending
        candidates.sort(key=lambda x: x.get("published_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        # Apply limit if configured
        if limit and limit > 0:
            candidates = candidates[:limit]

        return candidates

    async def _discover_from_rss(
        self,
        client: httpx.AsyncClient,
        source: Optional[Source] = None,
        cutoff_dt: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> list[dict]:
        """Fetch and parse official German RSS newsfeed."""
        ref_now = now or datetime.now(timezone.utc)
        rss_url = (source.config or {}).get("rss_url") if source else RSS_NEWSFEED_URL
        rss_url = rss_url or RSS_NEWSFEED_URL

        headers = {
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "User-Agent": "HITCHINGS/0.1 (+https://github.com/hitchings; news-observatory)",
        }

        try:
            resp = await client.get(rss_url, headers=headers)
        except httpx.RequestError as exc:
            logger.warning("Network error fetching Bundeskartellamt RSS: %s", exc)
            return []

        if resp.status_code != 200:
            logger.warning("Bundeskartellamt RSS returned HTTP %d", resp.status_code)
            return []

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            logger.warning("Malformed XML in Bundeskartellamt RSS: %s", exc)
            return []

        items = root.findall("./channel/item")
        candidates: list[dict] = []

        for it in items:
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub_date_str = it.findtext("pubDate")
            description = (it.findtext("description") or "").strip()
            published_at = parse_rfc822_date(pub_date_str)

            if not link:
                continue

            # Fallback published_at from slug if pubDate parsing failed
            if not published_at:
                published_at = parse_slug_date(link, title)

            # Editorial filtering
            if self._is_editorially_excluded(title=title, url=link):
                logger.debug("Editorially excluded Bundeskartellamt item: %s", title)
                continue

            # Date boundaries
            if published_at:
                if cutoff_dt and published_at < cutoff_dt:
                    continue
                if published_at > ref_now + timedelta(days=1):
                    # Exclude future dated items
                    continue

            candidates.append({
                "url": link,
                "title": title,
                "description": description,
                "published_at": published_at,
                "source_channel": "rss",
            })

        return candidates

    async def _discover_from_sitemap(
        self,
        client: httpx.AsyncClient,
        source: Optional[Source] = None,
        cutoff_dt: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> list[dict]:
        """Discover historical publications from Sitemap XML for extended lookback windows."""
        ref_now = now or datetime.now(timezone.utc)
        sitemap_url = (source.config or {}).get("sitemap_url") if source else SITEMAP_XML_URL
        sitemap_url = sitemap_url or SITEMAP_XML_URL

        headers = {
            "Accept": "application/xml, text/xml, */*",
            "User-Agent": "HITCHINGS/0.1 (+https://github.com/hitchings; news-observatory)",
        }

        try:
            resp = await client.get(sitemap_url, headers=headers)
        except httpx.RequestError as exc:
            logger.warning("Network error fetching Bundeskartellamt sitemap: %s", exc)
            return []

        if resp.status_code != 200:
            logger.warning("Bundeskartellamt sitemap returned HTTP %d", resp.status_code)
            return []

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            logger.warning("Malformed XML in Bundeskartellamt sitemap: %s", exc)
            return []

        # Find all url elements across namespaces
        ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        url_elements = root.findall(".//ns:url", ns) or root.findall(".//url")

        candidates: list[dict] = []
        for u_el in url_elements:
            loc = (u_el.findtext("ns:loc", namespaces=ns) or u_el.findtext("loc") or "").strip()
            lastmod = (u_el.findtext("ns:lastmod", namespaces=ns) or u_el.findtext("lastmod") or "").strip()
            if not loc:
                continue

            # We focus on substantive German publications (/DE/):
            # Pressemitteilungen, AktuelleMeldungen, Fallberichte, Entscheidungen
            if not ("/DE/Pressemitteilungen/" in loc or "/DE/AktuelleMeldungen/" in loc or "/DE/Fallberichte/" in loc or "/DE/Entscheidungen/" in loc):
                continue

            # Editorial filtering based on URL slug
            if self._is_editorially_excluded(title="", url=loc):
                continue

            # Extract date from URL slug or fallback to lastmod in sitemap
            published_at = parse_slug_date(loc)
            if not published_at and lastmod:
                try:
                    published_at = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
                    if published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            # Date boundaries
            if published_at:
                if cutoff_dt and published_at < cutoff_dt:
                    continue
                if published_at > ref_now + timedelta(days=1):
                    continue
            elif cutoff_dt:
                # If date could not be determined at all, only accept if current year in URL
                current_year = ref_now.year
                if f"/{current_year}/" not in loc:
                    continue

            candidates.append({
                "url": loc,
                "title": "",  # Will be populated during detail enrichment
                "description": "",
                "published_at": published_at,
                "source_channel": "sitemap",
            })

        return candidates

    def is_editorial_noise(self, title: str, url: str, category: str = "") -> bool:
        """Determine if an item is institutional noise (YouTube video, vacancies, generic ceremony)."""
        combined = f"{title} {url} {category}".lower()
        return any(kw in combined for kw in EDITORIAL_EXCLUDE_KEYWORDS)

    def extract_aktenzeichen(self, text: str) -> Optional[str]:
        """Extract official German case number (Aktenzeichen)."""
        return self._extract_aktenzeichen(text)

    def _is_editorially_excluded(self, title: str, url: str) -> bool:
        """Internal shorthand for editorial exclusion check."""
        return self.is_editorial_noise(title=title, url=url)

    async def _enrich_item(
        self,
        client: httpx.AsyncClient,
        candidate: dict,
        now: Optional[datetime] = None,
    ) -> Optional[RawEntryData]:
        """Fetch detail page HTML, resolve English version if present, extract Aktenzeichen and PDF."""
        de_url = candidate["url"]
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "HITCHINGS/0.1 (+https://github.com/hitchings; news-observatory)",
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        try:
            resp_de = await client.get(de_url, headers=headers)
        except httpx.RequestError as exc:
            logger.error("HTTP error fetching Bundeskartellamt detail page %s: %s", de_url, exc)
            return None

        if resp_de.status_code != 200:
            logger.warning("Bundeskartellamt detail page %s returned status %d", de_url, resp_de.status_code)
            return None

        html_de = resp_de.text
        soup_de = BeautifulSoup(html_de, "html.parser")

        # 1. Title
        h1_el = soup_de.find("h1")
        de_title = h1_el.get_text(strip=True) if h1_el else (candidate.get("title") or "")
        title = de_title

        # Double check editorial exclusion after parsing H1 title
        if self._is_editorially_excluded(title=de_title, url=de_url):
            return None

        # 2. Publication date
        published_at = candidate.get("published_at")
        if not published_at:
            published_at = parse_slug_date(de_url, soup_de.get_text()) or (now or datetime.now(timezone.utc))

        # 3. Extract Case Number (Aktenzeichen) from full raw HTML text BEFORE clean_content mutates DOM
        raw_soup_text = soup_de.get_text(separator=" ", strip=True)
        aktenzeichen = self._extract_aktenzeichen(f"{de_title} {raw_soup_text} {de_url}")

        # 4. Check for deterministic English translation link (BEFORE clean_content mutates DOM)
        en_url = self._extract_english_url(soup_de, de_url)
        has_english_version = bool(en_url)
        english_fetch_failed = False
        language_selected = "de"
        en_title = None
        en_content = None

        # 5. Extract attached PDF URL (BEFORE clean_content mutates DOM)
        pdf_url = self._extract_pdf_url(soup_de, de_url)

        # 6. Clean German body text
        de_content = self._clean_content(soup_de)
        content = de_content

        if en_url:
            en_headers = dict(headers)
            en_headers["Accept-Language"] = "en-US,en;q=0.9"
            try:
                resp_en = await client.get(en_url, headers=en_headers)
                if resp_en.status_code == 200:
                    soup_en = BeautifulSoup(resp_en.text, "html.parser")
                    h1_en = soup_en.find("h1")
                    if h1_en:
                        en_title = h1_en.get_text(strip=True)
                    en_pdf = self._extract_pdf_url(soup_en, en_url)
                    if en_pdf:
                        pdf_url = en_pdf
                    clean_en = self._clean_content(soup_en)
                    if clean_en and len(clean_en) > 50:
                        en_content = clean_en
                        content = en_content
                        title = en_title or title
                        language_selected = "en"
                        logger.debug("Successfully enriched English version for %s", de_url)
                    elif en_title:
                        title = en_title
                        language_selected = "en"
                    if not aktenzeichen:
                        raw_en_text = soup_en.get_text(separator=" ", strip=True)
                        aktenzeichen = self._extract_aktenzeichen(f"{title} {raw_en_text} {en_url}")
                else:
                    logger.warning("English version for %s returned HTTP %d; falling back to DE", de_url, resp_en.status_code)
                    english_fetch_failed = True
                    has_english_version = False
            except Exception as exc:
                logger.warning("Failed fetching English version for %s: %s; falling back to DE", de_url, exc)
                english_fetch_failed = True
                has_english_version = False

        # 7. Classify document type
        content_type = self._classify_content_type(url=de_url, title=title)

        # 8. Compute excerpt
        excerpt = candidate.get("description") or (content[:350] + "..." if len(content) > 350 else content)

        raw_metadata = {
            "source_portal": "Bundeskartellamt",
            "institution": "Bundeskartellamt",
            "jurisdiction": "Germany",
            "german_url": de_url,
            "de_url": de_url,
            "has_english_version": has_english_version,
            "language_selected": language_selected,
            "has_pdf": bool(pdf_url),
        }
        if english_fetch_failed:
            raw_metadata["english_fetch_failed"] = True
        if en_url:
            raw_metadata["english_url"] = en_url
        if aktenzeichen:
            raw_metadata["aktenzeichen"] = aktenzeichen
            raw_metadata["case_reference"] = aktenzeichen
            raw_metadata["case_number"] = aktenzeichen
        if pdf_url:
            raw_metadata["pdf_url"] = pdf_url
            raw_metadata["pdf_attachments"] = [{"url": pdf_url, "title": "Official Publication PDF"}]
        else:
            raw_metadata["pdf_attachments"] = []

        return RawEntryData(
            url=de_url,
            title=title,
            content=content,
            excerpt=excerpt,
            author="Bundeskartellamt",
            published_at=published_at,
            external_id=de_url,
            language=language_selected,
            content_type=content_type,
            raw_metadata=raw_metadata,
        )

    def _extract_english_url(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Extract explicit English version link from German detail page."""
        # 1. Pattern: <span class="c-topline__item">English Version</span> followed by link
        en_box = soup.find(string=re.compile(r"English Version", re.I))
        if en_box:
            # Look for link in parent containers
            parent_container = en_box.find_parent(class_=re.compile(r"c-teaser-newsbox|article|box", re.I))
            if parent_container:
                a_tag = parent_container.find("a", href=True)
                if a_tag and "/EN/" in a_tag["href"] and "home_node.html" not in a_tag["href"].lower():
                    return urljoin(base_url, a_tag["href"])

        # 2. General search for links with /EN/ in href and English title or text
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href or "home_node.html" in href.lower():
                continue
            text = a.get_text(strip=True).lower()
            title_attr = (a.get("title") or "").lower()
            if "/EN/Pressemitteilungen/" in href and ("learn more" in text or "english" in text or "english" in title_attr):
                return urljoin(base_url, href)

        return None

    def _clean_content(self, soup: BeautifulSoup) -> str:
        """Extract clean substantive text while removing nav, cookie, footer, and boilerplate elements."""
        container = soup.find("div", class_=re.compile(r"article|c-article|content", re.I)) or soup.find("main") or soup

        # Remove noise elements
        for el in container.find_all(["nav", "footer", "script", "style", "header", "aside"]):
            el.decompose()
        for el in container.find_all(class_=re.compile(r"c-meta|c-topline|c-teaser-newsbox|breadcrumb|cookie|social|sharing|navlang", re.I)):
            el.decompose()

        paragraphs: list[str] = []
        for tag in container.find_all(["p", "h2", "h3", "li"]):
            txt = tag.get_text(separator=" ", strip=True)
            # Filter boilerplate phrases
            if len(txt) > 25 and not any(k in txt.lower() for k in ["cookie", "javascript", "barriere melden", "datenschutz", "impressum", "kaiser-friedrich-str"]):
                paragraphs.append(txt)

        return "\n\n".join(paragraphs)

    def _extract_aktenzeichen(self, text: str) -> Optional[str]:
        """Extract official German case number (Aktenzeichen) e.g. B12-21/23, B7-54/25, VK 1-45/26."""
        m = AKTENZEICHEN_REGEX.search(text)
        if m:
            val = m.group(1).strip()
            if val.upper().startswith("VK"):
                return re.sub(r"^VK\s*", "VK ", val, flags=re.I)
            elif val.upper().startswith("B"):
                return re.sub(r"^B\s*", "B", val, flags=re.I)
            return val
        return None

    def _extract_pdf_url(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Detect attached decision or case report PDF."""
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if ".pdf" in href.lower():
                # Avoid newsletters/press conference presentations if decisions or fallberichte exist
                full_pdf = urljoin(base_url, href)
                return full_pdf
        return None

    def _classify_content_type(self, url: str, title: str) -> str:
        """Classify Bundeskartellamt document type."""
        u_lower = url.lower()
        t_lower = title.lower()

        if "fallbericht" in u_lower or "fallbericht" in t_lower:
            return "case_report"
        if "beschluss" in u_lower or "beschluss" in t_lower or "/entscheidungen/" in u_lower:
            return "decision"
        if "fusionskontrolle" in u_lower or any(w in t_lower for w in ["übernahme", "erwerb", "freigabe", "zusammenschluss", "merger"]):
            return "merger_control"
        if "merkblatt" in u_lower or "leitfaden" in u_lower:
            return "guideline"
        if "sektoruntersuchung" in u_lower or "kraftstoff" in u_lower or "marktuntersuchung" in t_lower:
            return "sector_inquiry"
        if "pressemitteilungen" in u_lower:
            return "press_release"
        return "other_substantive"
