"""Extractor for OECD Competition Law and Policy publications and roundtables.

Retrieves official publications from the OECD Competition Committee, focusing on the
'OECD Roundtables on Competition Policy Papers' series (ISSN 2075-8677). Uses Crossref
metadata API as the authoritative, unblocked registry for OECD DOIs (prefix 10.1787),
enriches abstracts via OpenAlex, opportunistically extracts full report text from
accessible portals, and applies deterministic DOI-based deduplication.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.models.source import Source
from app.providers.base import RawEntryData, ProviderError

logger = logging.getLogger(__name__)

DEFAULT_OECD_ISSN = "2075-8677"
DEFAULT_CONCURRENCY = 4
CROSSREF_API_BASE = "https://api.crossref.org/works"
OPENALEX_API_BASE = "https://api.openalex.org/works"
MAX_CONTENT_CHARS = 10000

SUFFICIENCY_THRESHOLD_FULL = 500
SUFFICIENCY_THRESHOLD_PARTIAL = 200

# Core competition thematic keywords for tagging
COMPETITION_THEMES = [
    ("cartel", "Cartels & Leniency"),
    ("merger", "Merger Control"),
    ("dominance", "Abuse of Dominance"),
    ("digital", "Digital Markets & Platforms"),
    ("artificial intelligence", "AI & Competition"),
    ("national security", "National Security & Competition"),
    ("healthcare", "Healthcare & Competition"),
    ("remedies", "Remedies & Enforcement"),
    ("information sharing", "Information Sharing"),
    ("prioritisation", "Prioritisation & Prosecutorial Discretion"),
    ("informal markets", "Informal Markets"),
    ("private enforcement", "Private Enforcement"),
    ("damages", "Damages Actions"),
    ("neutrality", "Competitive Neutrality"),
    ("market studies", "Market Studies"),
]


class OECDCompetitionExtractor:
    """Extractor for OECD Competition publications and roundtables."""

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
        """Extract OECD competition publications matching series and lookback configuration."""
        config = source.config or {}
        issn = config.get("issn", DEFAULT_OECD_ISSN)
        effective_limit = limit or config.get("initial_fetch_limit", 20)
        effective_lookback = lookback_days if lookback_days is not None else config.get("lookback_days")

        logger.info(
            "Extracting OECD Competition publications (issn=%s, limit=%d, lookback=%s)...",
            issn,
            effective_limit,
            effective_lookback,
        )

        # 1. Fetch raw publication records from Crossref API
        records = await self._fetch_crossref_records(
            client=client,
            issn=issn,
            limit=effective_limit,
            lookback_days=effective_lookback,
            now=now,
        )
        logger.info("Found %d candidate OECD publications from Crossref", len(records))

        if not records:
            return []

        # 2. Enrich publications concurrently bounded by semaphore
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _enrich(record: dict) -> RawEntryData:
            async with semaphore:
                return await self._enrich_publication(client=client, record=record)

        tasks = [_enrich(rec) for rec in records]
        raw_entries = await asyncio.gather(*tasks)
        return list(raw_entries)

    async def _fetch_crossref_records(
        self,
        client: httpx.AsyncClient,
        issn: str,
        limit: int,
        lookback_days: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> list[dict]:
        """Fetch works from Crossref API ordered by publication date descending."""
        # Query slightly more items to account for potential date filtering
        fetch_rows = min(100, max(limit * 2, 20))
        url = f"{CROSSREF_API_BASE}?filter=issn:{issn}&sort=published&order=desc&rows={fetch_rows}"

        headers = {
            "Accept": "application/json",
            "User-Agent": "HITCHINGS/0.1 (+https://github.com/hitchings; news-observatory; mailto:info@hitchings.eu)",
        }

        try:
            resp = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            logger.error("HTTP error fetching Crossref API for OECD ISSN %s: %s", issn, exc)
            raise ProviderError(f"Network error fetching OECD publications: {exc}") from exc

        if resp.status_code != 200:
            logger.error("Crossref API returned HTTP status %d for OECD ISSN %s", resp.status_code, issn)
            raise ProviderError(f"Crossref API returned HTTP status {resp.status_code}")

        try:
            data = resp.json()
            items = data.get("message", {}).get("items", [])
        except Exception as exc:
            logger.error("Failed to parse Crossref JSON response: %s", exc)
            raise ProviderError(f"Malformed JSON from Crossref API: {exc}") from exc

        reference_dt = now or datetime.now(timezone.utc)
        current_date = reference_dt.date()

        cutoff_date = (
            (reference_dt - timedelta(days=lookback_days)).date()
            if lookback_days is not None and lookback_days > 0
            else None
        )

        candidate_records: list[dict] = []
        for it in items:
            pub_date = self.parse_crossref_date(it)
            if not pub_date:
                continue

            pub_calendar_date = pub_date.date()

            # 1. Future date guard: publications dated after today must NOT be returned yet
            if pub_calendar_date > current_date:
                logger.debug(
                    "Skipping future-dated OECD publication '%s' (published_at=%s > today=%s)",
                    it.get("DOI"),
                    pub_calendar_date,
                    current_date,
                )
                continue

            # 2. Lookback filter: exclude publications older than cutoff_date
            if cutoff_date and pub_calendar_date < cutoff_date:
                continue

            candidate_records.append(it)
            if len(candidate_records) >= limit:
                break

        return candidate_records

    async def _enrich_publication(self, client: httpx.AsyncClient, record: dict) -> RawEntryData:
        """Enrich a single publication record with abstract, full report text, and metadata."""
        doi = record.get("DOI", "")
        title_list = record.get("title", [])
        title = (title_list[0] if title_list else "OECD Competition Publication").strip()
        pub_date = self.parse_crossref_date(record)
        canonical_url = record.get("URL") or f"https://doi.org/{doi}"
        resource_url = record.get("resource", {}).get("primary", {}).get("URL") or canonical_url

        # Author resolution (institutional default: "OECD")
        authors = self._parse_authors(record)

        # Container / Series resolution
        containers = record.get("container-title", [])
        container_title = containers[0] if containers else "OECD Roundtables on Competition Policy Papers"

        # Detect language from DOI suffix (e.g. -en, -fr, -es, -pt) or URL
        language = self._detect_language(doi=doi, url=resource_url)

        # Abstract resolution: Crossref first, then OpenAlex
        abstract: Optional[str] = self._extract_crossref_abstract(record)
        abstract_source = "crossref" if abstract else "none"

        if not abstract and doi:
            oa_abstract = await self._fetch_openalex_abstract(client=client, doi=doi)
            if oa_abstract:
                abstract = oa_abstract
                abstract_source = "openalex"

        # Opportunistic full report text extraction from resource_url (if not blocked)
        report_text: Optional[str] = None
        pdf_url: Optional[str] = None
        full_report_url = resource_url

        # Attempt portal fetch with gentle timeout; ignore 403 / anti-bot gracefully
        try:
            portal_resp = await client.get(resource_url, timeout=6.0)
            if portal_resp.status_code == 200:
                soup = BeautifulSoup(portal_resp.content, "html.parser")
                # Look for PDF links on landing page
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if href.lower().endswith(".pdf") or "/content/dam/" in href:
                        pdf_url = href
                        break

                # Look for full report text
                body_el = soup.select_one("article") or soup.select_one(".publication-content") or soup.select_one("main")
                if body_el:
                    clean_text = body_el.get_text(separator="\n\n", strip=True)
                    if len(clean_text) > 300:
                        report_text = clean_text[:MAX_CONTENT_CHARS]
        except Exception as exc:
            logger.debug("OECD portal direct fetch skipped for %s (%s)", resource_url, exc)

        # Final content, excerpt, and provenance assembly
        if report_text:
            content = report_text
            content_provider = "oecd_html"
            content_source = "portal_html"
        elif abstract:
            content = abstract
            content_provider = abstract_source  # "openalex" or "crossref"
            content_source = abstract_source
        else:
            # Informative fallback
            content = (
                f"{title}\n\n"
                f"Series: {container_title}\n"
                f"Publisher: {record.get('publisher', 'OECD')}\n"
                f"Official DOI: {doi}\n"
                f"Permanent Link: {canonical_url}\n"
            )
            content_provider = "metadata_fallback"
            content_source = "metadata_fallback"

        excerpt = (abstract or content)[:400]

        # Sufficiency calculation
        text_len = len(content or "")
        if text_len >= SUFFICIENCY_THRESHOLD_FULL:
            sufficiency = "FULL"
        elif text_len >= SUFFICIENCY_THRESHOLD_PARTIAL:
            sufficiency = "PARTIAL"
        else:
            sufficiency = "INSUFFICIENT"

        # Thematic tagging
        tags = self._extract_thematic_tags(title=title, content=content, series=container_title)

        # Publication identifier (e.g. 62c9a81e-en from 10.1787/62c9a81e-en)
        pub_id = doi.split("/")[-1] if "/" in doi else doi

        raw_metadata: dict[str, Any] = {
            "doi": doi,
            "issn": DEFAULT_OECD_ISSN,
            "series": container_title,
            "publisher": record.get("publisher", "OECD"),
            "publication_identifier": pub_id,
            "canonical_doi_url": canonical_url,
            "resource_url": resource_url,
            "full_report_url": full_report_url,
            "pdf_url": pdf_url,
            "discovery_provider": "crossref",
            "content_provider": content_provider,
            "content_source": content_source,
            "abstract_source": abstract_source,
            "content_sufficiency": sufficiency,
            "text_length": text_len,
            "tags": tags,
            "type": "report",
        }

        return RawEntryData(
            url=resource_url,
            title=title,
            content=content,
            excerpt=excerpt,
            author=authors,
            published_at=pub_date,
            external_id=doi or resource_url,
            language=language,
            content_type="policy_paper",
            raw_metadata=raw_metadata,
        )

    def parse_crossref_date(self, item: dict) -> Optional[datetime]:
        """Extract published date from Crossref date-parts."""
        for date_key in ("published", "published-online", "issued", "created"):
            val = item.get(date_key)
            if isinstance(val, dict):
                parts_list = val.get("date-parts")
                if parts_list and isinstance(parts_list, list) and parts_list[0]:
                    parts = parts_list[0]
                    try:
                        if len(parts) >= 3:
                            return datetime(parts[0], parts[1], parts[2], tzinfo=timezone.utc)
                        elif len(parts) == 2:
                            return datetime(parts[0], parts[1], 1, tzinfo=timezone.utc)
                        elif len(parts) == 1:
                            return datetime(parts[0], 1, 1, tzinfo=timezone.utc)
                    except Exception as exc:
                        logger.debug("Failed parsing date parts %s: %s", parts, exc)
        return None

    def _parse_authors(self, record: dict) -> str:
        """Parse author names or default to 'OECD'."""
        author_list = record.get("author", [])
        names: list[str] = []
        for a in author_list:
            if isinstance(a, dict):
                if a.get("name"):
                    names.append(a["name"].strip())
                elif a.get("family"):
                    given = a.get("given", "").strip()
                    family = a.get("family", "").strip()
                    full = f"{given} {family}".strip()
                    if full:
                        names.append(full)
        if names:
            return ", ".join(names)
        return "OECD"

    def _detect_language(self, doi: str, url: str) -> str:
        """Detect document language from DOI or URL suffix."""
        combined = f"{doi}_{url}".lower()
        if combined.endswith("-en") or "_en." in combined or "/en/" in combined:
            return "en"
        if combined.endswith("-es") or "_es." in combined or "/es/" in combined:
            return "es"
        if combined.endswith("-fr") or "_fr." in combined or "/fr/" in combined:
            return "fr"
        if combined.endswith("-pt") or "_pt." in combined or "/pt/" in combined:
            return "pt"
        return "en"

    def _extract_crossref_abstract(self, record: dict) -> Optional[str]:
        """Extract clean text from Crossref abstract field if present."""
        raw_abstract = record.get("abstract")
        if not raw_abstract:
            return None
        soup = BeautifulSoup(raw_abstract, "html.parser")
        clean = soup.get_text(strip=True)
        return clean if clean else None

    async def _fetch_openalex_abstract(self, client: httpx.AsyncClient, doi: str) -> Optional[str]:
        """Retrieve and reconstruct abstract from OpenAlex inverted index."""
        url = f"{OPENALEX_API_BASE}/doi:{doi}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "HITCHINGS/0.1 (+https://github.com/hitchings; mailto:info@hitchings.eu)",
        }
        try:
            resp = await client.get(url, headers=headers, timeout=8.0)
            if resp.status_code == 200:
                oa_data = resp.json()
                inv = oa_data.get("abstract_inverted_index")
                if inv and isinstance(inv, dict):
                    word_positions: list[tuple[int, str]] = []
                    for word, positions in inv.items():
                        for pos in positions:
                            word_positions.append((pos, word))
                    word_positions.sort(key=lambda x: x[0])
                    reconstructed = " ".join(word for _, word in word_positions).strip()
                    if reconstructed:
                        return reconstructed
        except Exception as exc:
            logger.debug("OpenAlex abstract lookup failed for DOI %s: %s", doi, exc)
        return None

    def _extract_thematic_tags(self, title: str, content: str, series: str) -> list[str]:
        """Detect thematic competition tags based on official keywords."""
        tags = ["OECD", "Competition Policy"]
        if "roundtable" in series.lower() or "roundtable" in title.lower():
            tags.append("OECD Roundtables")

        combined_text = f"{title}\n{content}".lower()
        for kw, tag_label in COMPETITION_THEMES:
            if kw in combined_text and tag_label not in tags:
                tags.append(tag_label)

        return tags
