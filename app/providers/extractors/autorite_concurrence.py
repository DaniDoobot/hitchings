"""Extractor for Autorité de la concurrence (France) decisions, merger reviews, opinions, and communiqués.

Architecture:
- Discrete legal acts model (in-place immutable point-in-time snapshot, no living docket mutations).
- Supported acts:
  * YY-D-XX => antitrust (cartels, abuse of dominance, sanctions, commitments)
  * YY-MC-XX => antitrust / interim_measures (mesures conservatoires)
  * YY-DCC-XXX => merger_control (décisions de contrôle des concentrations)
  * YY-A-XX => competition_policy / market_study (avis et études de marché)
- Unsupported act types fail closed editorially.
- Dual discovery: Sitemap XML with lastmod filtering + canonical listings safety net.
- Editorial filtering:
  * Excludes corporate/administrative noise (nomination, vie de l'institution, conferences).
  * Communiqués: skips derivative communiqués that link to an official numbered act;
    includes only autonomous substantive milestones (dawn raids / visites et saisies,
    referrals / article 22, Phase 2 openings).
- Conservative PDF enrichment at initial ingest for D, MC, A, and complex DCC (<= 20MB, max 60k chars, no OCR).
  Delayed PDFs do NOT mutate previously created entries.
- French is the canonical legal language (language="fr").
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Sequence
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
import pypdf

from app.models.source import Source
from app.providers.base import RawEntryData, ProviderError
from app.services.source_sufficiency_service import (
    SourceSufficiencyLevel,
    SourceSufficiencyService,
)

logger = logging.getLogger(__name__)

ADLC_SOURCE_NAME = "Autorité de la concurrence (France)"
BASE_URL = "https://www.autoritedelaconcurrence.fr"
ADLC_BASE_URL = "https://www.autoritedelaconcurrence.fr/fr"
SITEMAP_URL = "https://www.autoritedelaconcurrence.fr/sitemap.xml"
DECISIONS_LISTING_URL = "https://www.autoritedelaconcurrence.fr/fr/liste-des-decisions-et-avis"
MERGERS_LISTING_URL = "https://www.autoritedelaconcurrence.fr/fr/liste-de-controle-des-concentrations"
COMMUNIQUES_LISTING_URL = "https://www.autoritedelaconcurrence.fr/fr/communiques-de-presse"

DEFAULT_TIMEOUT = 15.0
DEFAULT_CONCURRENCY = 4

# Official Case ID patterns
ACT_ID_REGEX = re.compile(r"\b(\d{2}-(?:D|MC|DCC|A)-\d{2,4})\b", re.IGNORECASE)


def classify_official_act_id(official_id: str) -> Optional[tuple[str, str]]:
    """Classify official ID into (act_type, decision_family) or None if unsupported."""
    if not official_id:
        return None
    id_upper = official_id.strip().upper()
    if re.search(r"^\d{2}-D-\d+", id_upper):
        return ("decision", "antitrust")
    elif re.search(r"^\d{2}-MC-\d+", id_upper):
        return ("interim_measure", "interim_measures")
    elif re.search(r"^\d{2}-DCC-\d+", id_upper):
        return ("merger_decision", "merger_control")
    elif re.search(r"^\d{2}-A-\d+", id_upper):
        return ("opinion", "competition_policy")
    return None

# French month mapping for date parsing
FRENCH_MONTHS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12
}

# Conservative PDF Limits
MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_PDF_PAGES_FULL = 40           # If <= 40 pages, extract completely
MAX_PDF_PAGES_PARTIAL = 30        # If > 40 pages, extract first 30 pages
MAX_EXTRACTED_CHARS = 60_000      # Ceiling for extracted text

# Autonomous communiqué milestone keywords
AUTONOMOUS_COMMUNIQUE_KEYWORDS = [
    "visite et saisie",
    "visites et saisies",
    "opérations de visite",
    "opération de visite",
    "inspection inopinée",
    "inspections inopinées",
    "perquisition",
    "renvoi",
    "article 22",
    "article 9",
    "renvoie à l'autorité",
    "ouverture d'une phase 2",
    "examen approfondi",
]

# Corporate / institutional exclusion keywords for communiqués and opinions
INSTITUTIONAL_EXCLUDE_KEYWORDS = [
    "vie de l'institution",
    "rapport annuel",
    "save the date",
    "colloque",
    "atelier",
    "conférence",
    "conference",
    "rencontre semestrielle",
    "nomination",
    "meilleurs vœux",
    "vœux",
    "voeux",
    "cérémonie",
    "ceremonie",
    "organigramme",
    "hommage",
]


class ADLCDiscoveryMetrics(BaseModel):
    """Detailed discovery and editorial metrics for Autorité de la concurrence."""

    items_discovered: int = 0
    inside_lookback: int = 0
    outside_lookback: int = 0
    acts_supported: int = 0
    acts_filtered: int = 0
    decisions_d: int = 0
    interim_measures_mc: int = 0
    mergers_dcc: int = 0
    opinions_a: int = 0
    communiques_seen: int = 0
    derivative_communiques_skipped: int = 0
    autonomous_communiques_included: int = 0
    institutional_communiques_excluded: int = 0
    administrative_opinions_excluded: int = 0
    unsupported_act_type_skipped: int = 0
    pdf_available: int = 0
    pdf_extracted: int = 0
    pdf_skipped: int = 0
    full_count: int = 0
    partial_count: int = 0
    insufficient_count: int = 0

    @property
    def acts_discovered_total(self) -> int:
        return self.acts_supported + self.acts_filtered + self.unsupported_act_type_skipped

    @property
    def communiques_discovered(self) -> int:
        return self.communiques_seen

    @property
    def acts_by_type(self) -> dict[str, int]:
        return {
            "decision": self.decisions_d,
            "interim_measure": self.interim_measures_mc,
            "merger_decision": self.mergers_dcc,
            "opinion": self.opinions_a,
        }

    @property
    def pdfs_downloaded_and_extracted(self) -> int:
        return self.pdf_extracted

    @property
    def pdfs_skipped_simplified_mergers(self) -> int:
        return self.pdf_skipped

    @property
    def delayed_pdfs_skipped_existing_entries(self) -> int:
        return 0

    @property
    def pages_crawled(self) -> int:
        return self.items_discovered

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d.update({
            "acts_discovered_total": self.acts_discovered_total,
            "communiques_discovered": self.communiques_discovered,
            "acts_by_type": self.acts_by_type,
            "pdfs_downloaded_and_extracted": self.pdfs_downloaded_and_extracted,
            "pdfs_skipped_simplified_mergers": self.pdfs_skipped_simplified_mergers,
            "delayed_pdfs_skipped_existing_entries": self.delayed_pdfs_skipped_existing_entries,
            "pages_crawled": self.pages_crawled,
        })
        return d


def parse_french_date(text: str) -> Optional[datetime]:
    """Parse French textual date string into timezone-aware UTC datetime.

    Examples:
        '09 septembre 2026' -> datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
        '31 juillet 2026'   -> datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        '25 février 2026'   -> datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
    """
    if not text:
        return None

    cleaned = text.strip().lower()
    # Match pattern: DD month_name YYYY
    m = re.search(r"\b(\d{1,2})\s+([a-zA-Z\xe9\xfb\xe8\xe0]+)\s+(\d{4})\b", cleaned)
    if m:
        day = int(m.group(1))
        month_str = m.group(2)
        year = int(m.group(3))
        month = FRENCH_MONTHS.get(month_str)
        if month:
            try:
                return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)
            except ValueError:
                pass

    # Match ISO format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ
    iso_m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2}))?", cleaned)
    if iso_m:
        y, mo, d = int(iso_m.group(1)), int(iso_m.group(2)), int(iso_m.group(3))
        h = int(iso_m.group(4)) if iso_m.group(4) else 12
        mi = int(iso_m.group(5)) if iso_m.group(5) else 0
        s = int(iso_m.group(6)) if iso_m.group(6) else 0
        try:
            return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


def clean_html_content(raw_html: str) -> str:
    """Extract and normalize substantive text from an HTML fragment.

    Removes navigation, script, style, and UI elements while preserving
    legal structure, numbering, paragraphs, and French accents.
    """
    if not raw_html:
        return ""

    soup = BeautifulSoup(raw_html, "html.parser")

    for tag in soup(["script", "style", "nav", "header", "footer", "form", "button"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    cleaned_paragraphs = []
    current_p: list[str] = []

    for line in lines:
        if line:
            current_p.append(line)
        elif current_p:
            cleaned_paragraphs.append(" ".join(current_p))
            current_p = []

    if current_p:
        cleaned_paragraphs.append(" ".join(current_p))

    return "\n\n".join(cleaned_paragraphs)


class AutoriteConcurrenceExtractor:
    """Native extractor for Autorité de la concurrence official acts and publications."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        sitemap_url: str = SITEMAP_URL,
        concurrency: int = DEFAULT_CONCURRENCY,
        enrich_pdf: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.sitemap_url = sitemap_url
        self.concurrency = concurrency
        self.enrich_pdf = enrich_pdf
        self.metrics = ADLCDiscoveryMetrics()

    async def extract(
        self,
        client: httpx.AsyncClient,
        source: Source,
        lookback_days: int = 8,
        enrich_pdf: Optional[bool] = None,
        max_pages_per_listing: int = 15,
    ) -> list[RawEntryData]:
        """Discover, parse, filter, and optionally enrich ADLC entries within lookback.

        Zero database writes. Returns list of normalized RawEntryData.
        """
        do_enrich = self.enrich_pdf if enrich_pdf is None else enrich_pdf
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        logger.info(
            "Starting Autorité de la concurrence extraction (lookback_days=%d, cutoff=%s, enrich_pdf=%s)",
            lookback_days,
            cutoff.isoformat(),
            do_enrich,
        )

        candidate_urls = await self.discover_candidate_urls(
            client=client,
            cutoff=cutoff,
            max_pages=max_pages_per_listing,
        )
        self.metrics.items_discovered = len(candidate_urls)
        logger.info("Total candidate URLs discovered: %d", len(candidate_urls))

        semaphore = asyncio.Semaphore(self.concurrency)
        entries: list[RawEntryData] = []

        async def _fetch_and_parse(url: str) -> Optional[RawEntryData]:
            async with semaphore:
                try:
                    resp = await client.get(url, timeout=DEFAULT_TIMEOUT)
                    if resp.status_code != 200:
                        logger.warning("HTTP %d fetching ADLC detail: %s", resp.status_code, url)
                        return None
                    return await self.parse_detail_page(
                        client=client,
                        url=url,
                        html=resp.text,
                        cutoff=cutoff,
                        enrich_pdf=do_enrich,
                    )
                except Exception as exc:
                    logger.warning("Error fetching/parsing ADLC detail %s: %s", url, exc)
                    return None

        tasks = [_fetch_and_parse(url) for url in candidate_urls]
        results = await asyncio.gather(*tasks)

        for res in results:
            if res is not None:
                entries.append(res)

        entries.sort(
            key=lambda e: e.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        logger.info(
            "Extraction completed: %d eligible entries extracted (FULL=%d, PARTIAL=%d, INSUFFICIENT=%d)",
            len(entries),
            self.metrics.full_count,
            self.metrics.partial_count,
            self.metrics.insufficient_count,
        )
        return entries

    async def discover_candidate_urls(
        self,
        client: httpx.AsyncClient,
        cutoff: datetime,
        max_pages: int = 15,
    ) -> list[str]:
        """Discover candidate URLs using Sitemap XML + Listings safety net."""
        discovered: dict[str, Optional[datetime]] = {}

        # 1. Sitemap XML discovery
        try:
            sitemap_urls = await self._discover_from_sitemap(client, cutoff)
            for u, dt in sitemap_urls.items():
                discovered[u] = dt
            logger.info("Discovered %d candidate URLs from Sitemap XML", len(sitemap_urls))
        except Exception as exc:
            logger.warning("Sitemap discovery failed or partial: %s", exc)

        # 2. Canonical Listings discovery (safety net & fresh publications)
        listings = [
            (DECISIONS_LISTING_URL, "decision_or_avis"),
            (MERGERS_LISTING_URL, "merger"),
            (COMMUNIQUES_LISTING_URL, "communique"),
        ]

        for listing_url, list_type in listings:
            try:
                listing_items = await self._discover_from_listing(
                    client=client,
                    listing_url=listing_url,
                    list_type=list_type,
                    cutoff=cutoff,
                    max_pages=max_pages,
                )
                for u, dt in listing_items.items():
                    if u not in discovered:
                        discovered[u] = dt
                logger.info("Discovered %d URLs from listing '%s'", len(listing_items), listing_url)
            except Exception as exc:
                logger.warning("Listing discovery failed for '%s': %s", listing_url, exc)

        return list(discovered.keys())

    async def _discover_from_sitemap(
        self, client: httpx.AsyncClient, cutoff: datetime
    ) -> dict[str, Optional[datetime]]:
        """Query Drupal sitemap.xml and child pages for relevant nodes within lookback."""
        results: dict[str, Optional[datetime]] = {}

        try:
            resp = await client.get(self.sitemap_url, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                logger.warning("HTTP %d fetching sitemap index %s", resp.status_code, self.sitemap_url)
                return results

            root = ET.fromstring(resp.content)
            sitemap_locs: list[str] = []
            for sitemap in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap"):
                loc = sitemap.findtext("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
                if loc:
                    sitemap_locs.append(loc)

            if not sitemap_locs:
                sitemap_locs = [self.sitemap_url]

            target_prefixes = (
                "/fr/decision/",
                "/fr/decision-de-controle-des-concentrations/",
                "/fr/avis/",
                "/fr/communiques-de-presse/",
            )

            for s_loc in sitemap_locs:
                try:
                    s_resp = await client.get(s_loc, timeout=DEFAULT_TIMEOUT)
                    if s_resp.status_code != 200:
                        continue
                    s_root = ET.fromstring(s_resp.content)
                    for url_node in s_root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url"):
                        loc = url_node.findtext("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
                        lastmod_str = url_node.findtext("{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod")

                        if not loc:
                            continue

                        parsed_path = urlparse(loc).path
                        if not any(parsed_path.startswith(pfx) for pfx in target_prefixes):
                            continue

                        lastmod_dt = None
                        if lastmod_str:
                            lastmod_dt = parse_french_date(lastmod_str)

                        if lastmod_dt is not None:
                            if lastmod_dt >= cutoff:
                                results[loc] = lastmod_dt
                        else:
                            results[loc] = None

                except Exception as page_exc:
                    logger.warning("Error reading sub-sitemap %s: %s", s_loc, page_exc)

        except Exception as exc:
            logger.warning("Error in sitemap index resolution: %s", exc)

        return results

    async def _discover_from_listing(
        self,
        client: httpx.AsyncClient,
        listing_url: str,
        list_type: str,
        cutoff: datetime,
        max_pages: int = 15,
    ) -> dict[str, Optional[datetime]]:
        """Paginate a canonical listing page until items precede cutoff or max_pages reached."""
        results: dict[str, Optional[datetime]] = {}

        for page in range(max_pages):
            paged_url = f"{listing_url}?page={page}"
            resp = await client.get(paged_url, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            rows = soup.select(".views-row")
            if not rows:
                break

            found_on_page = 0
            all_rows_older_than_cutoff = True

            for row in rows:
                a_tag = row.find("a", href=lambda h: h and any(k in h for k in [
                    "/decision/", "/decision-de-controle-des-concentrations/", "/avis/", "/communiques-de-presse/"
                ]))
                if not a_tag:
                    continue

                item_url = urljoin(self.base_url, a_tag["href"])
                date_el = row.select_one("time, .date-display-single, .views-field-created")
                row_date = None
                if date_el:
                    row_date = parse_french_date(date_el.get("datetime") or date_el.get_text())

                if row_date is not None:
                    if row_date >= cutoff:
                        results[item_url] = row_date
                        found_on_page += 1
                        all_rows_older_than_cutoff = False
                    else:
                        pass
                else:
                    results[item_url] = None
                    found_on_page += 1
                    all_rows_older_than_cutoff = False

            if all_rows_older_than_cutoff and found_on_page == 0:
                logger.debug("Stopping listing '%s' at page %d (all rows before cutoff)", listing_url, page)
                break

        return results

    async def parse_detail_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        html: str,
        cutoff: datetime,
        enrich_pdf: bool = True,
    ) -> Optional[RawEntryData]:
        """Parse individual ADLC detail page into RawEntryData."""
        soup = BeautifulSoup(html, "html.parser")

        # 1. Canonical URL
        canonical_tag = soup.find("link", rel="canonical")
        canonical_url = canonical_tag.get("href") if canonical_tag else url
        if not canonical_url.startswith("http"):
            canonical_url = urljoin(self.base_url, canonical_url)

        # 2. Extract Title
        title = ""
        h1 = soup.find("h1")
        if h1:
            title = " ".join(h1.get_text().split())
        if not title and soup.title:
            title = re.sub(r"\s*\|\s*Autorité.*$", "", soup.title.get_text()).strip()

        # 3. Extract Published Date
        date_el = soup.select_one(".field--name-field-date-notice, .field--name-field-date-dcc, time")
        date_str = ""
        if date_el:
            date_str = date_el.get("datetime") or date_el.get_text()
        if not date_str:
            m = re.search(r"\b(\d{1,2}\s+(?:janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|octobre|novembre|decembre|décembre)\s+\d{4})\b", html[:2000], re.IGNORECASE)
            if m:
                date_str = m.group(1)

        published_at = parse_french_date(date_str)
        if published_at is None:
            m_slug = re.search(r"\bdu-(\d{1,2}-[a-z]+-\d{4})\b", url)
            if m_slug:
                published_at = parse_french_date(m_slug.group(1).replace("-", " "))

        # 4. Strict Lookback Cutoff Filter
        if published_at is not None:
            if published_at < cutoff:
                self.metrics.outside_lookback += 1
                return None
            self.metrics.inside_lookback += 1
        else:
            self.metrics.inside_lookback += 1

        # 5. Determine Act Type and Official ID
        fid_el = soup.select_one(".field--name-field-id, .field-id")
        official_id = fid_el.get_text(strip=True) if fid_el else ""
        if not official_id:
            m_id = ACT_ID_REGEX.search(title + " " + url)
            if m_id:
                official_id = m_id.group(1).upper()

        path_lower = urlparse(canonical_url).path.lower()
        is_communique = (
            "/communiques-de-presse/" in path_lower
            or "/article/" in path_lower
            or "/communique" in path_lower
            or not official_id
        )

        # Handle Communiqués
        if is_communique and not official_id:
            num_dec_el = soup.select_one(".field--name-field-numero-de-decision, .field-numero-de-decision")
            if num_dec_el and not ACT_ID_REGEX.search(num_dec_el.get_text()):
                self.metrics.unsupported_act_type_skipped += 1
                return None

            return await self._handle_communique(
                client=client,
                url=canonical_url,
                title=title,
                soup=soup,
                published_at=published_at,
            )

        # Handle Official Numbered Acts
        if not official_id:
            self.metrics.unsupported_act_type_skipped += 1
            return None

        classification = classify_official_act_id(official_id)
        if not classification:
            logger.info("Unsupported act type '%s' skipped (fail-closed)", official_id)
            self.metrics.unsupported_act_type_skipped += 1
            return None

        act_type, decision_family = classification
        content_type = act_type

        if act_type == "decision":
            self.metrics.decisions_d += 1
        elif act_type == "interim_measure":
            self.metrics.interim_measures_mc += 1
        elif act_type == "merger_decision":
            self.metrics.mergers_dcc += 1
        elif act_type == "opinion":
            self.metrics.opinions_a += 1

        # 6. Sector and Editorial Filtering for Opinions (YY-A)
        sectors = [a.get_text(strip=True) for a in soup.select(".field--name-field-sector a")]
        sector_str = " / ".join(sectors) if sectors else None

        if act_type == "opinion":
            lower_text = (title + " " + (sector_str or "")).lower()
            if any(kw in lower_text for kw in ["vie de l'institution", "proposition de nomination"]):
                logger.info("Excluding administrative opinion '%s' (%s)", official_id, title)
                self.metrics.administrative_opinions_excluded += 1
                self.metrics.acts_filtered += 1
                return None

        self.metrics.acts_supported += 1

        # 7. Extract Metadata Fields (Phase, Simplified, Dispositif, Parties)
        phase = None
        phase_el = soup.select_one(".field--name-field-phase-decision")
        if phase_el:
            phase = phase_el.get_text(strip=True)
        elif "phase 2" in html.lower():
            phase = "Phase 2"
        elif "phase 1" in html.lower():
            phase = "Phase 1"

        simplified = "décision simplifiée" in html.lower() or "decision simplifiee" in html.lower()

        outcome = None
        disp_el = soup.select_one(".field--name-field-provisions")
        if disp_el:
            outcome = disp_el.get_text(strip=True)

        parties = None
        parties_el = soup.select_one(".field--name-field-parties")
        if parties_el:
            parties = parties_el.get_text(strip=True)

        # 8. Extract HTML Body Content
        body_el = soup.select_one(".field--name-field-body, .node__content, .content, .field--name-body")
        raw_body_html = str(body_el) if body_el else ""
        content = clean_html_content(raw_body_html)

        if not title or len(title) < 10:
            title = f"Décision {official_id} du {date_str}" if date_str else f"Décision {official_id}"

        # 9. PDF Detection and Conservative Enrichment
        pdf_url = self._extract_pdf_url(soup)
        pdf_available = pdf_url is not None
        pdf_extracted = False

        if pdf_available:
            self.metrics.pdf_available += 1

        should_enrich = False
        if enrich_pdf and pdf_available:
            if act_type in ("decision", "interim_measure", "opinion"):
                should_enrich = True
            elif act_type == "merger_decision":
                if not simplified or phase == "Phase 2" or len(content) < 1500:
                    should_enrich = True

        if should_enrich and pdf_url:
            pdf_text = await self._extract_pdf_text(client, pdf_url)
            if pdf_text:
                pdf_extracted = True
                content = f"{content}\n\n[TEXTE INTÉGRAL OFFICIEL - EXTRAIT PDF]\n\n{pdf_text}"
            else:
                self.metrics.pdf_skipped += 1
        elif pdf_available:
            self.metrics.pdf_skipped += 1

        # 10. Excerpt Generation
        desc_el = soup.select_one(".field--name-field-chapo, .chapo")
        excerpt = desc_el.get_text(strip=True) if desc_el else (content[:250].strip() if content else None)

        # 11. Compute Sufficiency
        content_len = len(content)
        if content_len >= 1500:
            self.metrics.full_count += 1
        elif content_len >= 300:
            self.metrics.partial_count += 1
        else:
            self.metrics.insufficient_count += 1

        en_link = soup.select_one("a.language-link[hreflang='en'], a[href*='/en/']")
        english_url = urljoin(self.base_url, en_link["href"]) if en_link else None

        official_id_clean = official_id.lower().replace(" ", "")
        external_id = f"adlc:act:{official_id_clean}"

        raw_meta = {
            "official_id": official_id,
            "act_type": act_type,
            "canonical_url": canonical_url,
            "pdf_url": pdf_url,
            "publication_date": published_at.isoformat() if published_at else None,
            "sector": sector_str,
            "decision_family": decision_family,
            "language": "fr",
            "discovery_source": "adlc_native",
            "pdf_available_at_ingestion": pdf_available,
            "pdf_extracted": pdf_extracted,
            "phase": phase,
            "simplified_procedure": simplified,
            "outcome": outcome,
            "parties": parties,
            "english_url": english_url,
        }

        return RawEntryData(
            url=canonical_url,
            title=title,
            content=content,
            excerpt=excerpt,
            author="Autorité de la concurrence",
            published_at=published_at,
            external_id=external_id,
            language="fr",
            content_type=content_type,
            raw_metadata=raw_meta,
        )

    async def _handle_communique(
        self,
        client: httpx.AsyncClient,
        url: str,
        title: str,
        soup: BeautifulSoup,
        published_at: Optional[datetime],
    ) -> Optional[RawEntryData]:
        """Process a Communiqué de presse with strict derivative/autonomous filtering."""
        self.metrics.communiques_seen += 1

        body_el = soup.select_one(".field--name-field-body, .node__content, .content, .field--name-body")
        body_html = str(body_el) if body_el else ""

        is_derivative = False
        if body_el:
            for a in body_el.find_all("a", href=True):
                href_lower = a["href"].lower()
                if any(p in href_lower for p in ["/decision/", "/decision-de-controle-des-concentrations/", "/avis/"]):
                    is_derivative = True
                    break
                if ACT_ID_REGEX.search(a.get_text() + " " + href_lower):
                    is_derivative = True
                    break

        if is_derivative:
            logger.info("Skipping derivative communiqué '%s' (links to primary act)", title)
            self.metrics.derivative_communiques_skipped += 1
            return None

        text_full = (title + " " + (body_el.get_text() if body_el else "")).lower()

        if any(kw in text_full for kw in INSTITUTIONAL_EXCLUDE_KEYWORDS):
            logger.info("Excluding institutional communiqué '%s'", title)
            self.metrics.institutional_communiques_excluded += 1
            return None

        is_substantive = any(kw in text_full for kw in AUTONOMOUS_COMMUNIQUE_KEYWORDS)
        if not is_substantive:
            logger.info("Excluding non-substantive communiqué '%s'", title)
            self.metrics.institutional_communiques_excluded += 1
            return None

        self.metrics.autonomous_communiques_included += 1
        content = clean_html_content(body_html)

        slug = urlparse(url).path.strip("/").split("/")[-1]
        slug_clean = re.sub(r"[^a-zA-Z0-9_\-]+", "", slug).lower()
        external_id = f"adlc:communique:{slug_clean}"

        desc_el = soup.select_one(".field--name-field-chapo, .chapo")
        excerpt = desc_el.get_text(strip=True) if desc_el else (content[:250].strip() if content else None)

        content_len = len(content)
        if content_len >= 1500:
            self.metrics.full_count += 1
        elif content_len >= 300:
            self.metrics.partial_count += 1
        else:
            self.metrics.insufficient_count += 1

        raw_meta = {
            "official_id": None,
            "act_type": "communique",
            "canonical_url": url,
            "pdf_url": None,
            "publication_date": published_at.isoformat() if published_at else None,
            "sector": None,
            "decision_family": "antitrust",
            "language": "fr",
            "discovery_source": "adlc_native",
            "autonomous_communique": True,
            "is_autonomous_communique": True,
        }

        return RawEntryData(
            url=url,
            title=title,
            content=content,
            excerpt=excerpt,
            author="Autorité de la concurrence",
            published_at=published_at,
            external_id=external_id,
            language="fr",
            content_type="press_release",
            raw_metadata=raw_meta,
        )

    def _extract_pdf_url(self, soup: BeautifulSoup) -> Optional[str]:
        """Locate official decision PDF link in page."""
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if ".pdf" in href.lower() and not "entityprint" in href.lower():
                text = a.get_text().lower()
                if any(k in text for k in ["texte intégral", "texte integral", "décision", "avis", "télécharger", "lire"]):
                    return urljoin(self.base_url, href)
                if "/sites/default/files/integral_texts/" in href or "/sites/default/files/" in href:
                    return urljoin(self.base_url, href)
        return None

    async def _extract_pdf_text(self, client: httpx.AsyncClient, pdf_url: str) -> Optional[str]:
        """Download official PDF and extract digital text layer using pypdf."""
        try:
            resp = await client.get(pdf_url, timeout=25.0)
            if resp.status_code != 200:
                logger.warning("HTTP %d downloading PDF: %s", resp.status_code, pdf_url)
                return None

            content = resp.content
            if len(content) > MAX_PDF_BYTES:
                logger.warning("PDF exceeds %d bytes limit (%d bytes): %s", MAX_PDF_BYTES, len(content), pdf_url)
                return None

            reader = pypdf.PdfReader(io.BytesIO(content))
            num_pages = len(reader.pages)
            pages_to_extract = num_pages if num_pages <= MAX_PDF_PAGES_FULL else MAX_PDF_PAGES_PARTIAL

            text_chunks: list[str] = []
            total_chars = 0

            for p_idx in range(pages_to_extract):
                page_text = reader.pages[p_idx].extract_text() or ""
                cleaned = " ".join(page_text.split())
                if cleaned:
                    text_chunks.append(cleaned)
                    total_chars += len(cleaned)
                    if total_chars >= MAX_EXTRACTED_CHARS:
                        break

            full_text = "\n\n".join(text_chunks).strip()
            if len(full_text) > MAX_EXTRACTED_CHARS:
                full_text = full_text[:MAX_EXTRACTED_CHARS].rsplit(" ", 1)[0] + "..."

            if full_text:
                self.metrics.pdf_extracted += 1
                return full_text
            return None

        except Exception as exc:
            logger.warning("PDF extraction failed for %s: %s", pdf_url, exc)
            return None

    @property
    def last_metrics(self) -> ADLCDiscoveryMetrics:
        """Return discovery metrics from the last execution."""
        return self.metrics

    async def _extract_act_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        source: Optional[Source] = None,
        now: Optional[datetime] = None,
        fetch_pdf: bool = True,
    ) -> Optional[RawEntryData]:
        """Convenience method to fetch and parse an individual act detail page."""
        resp = await client.get(url, timeout=DEFAULT_TIMEOUT)
        if resp.status_code != 200:
            return None
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=365)
        return await self.parse_detail_page(
            client=client,
            url=url,
            html=resp.text,
            cutoff=cutoff,
            enrich_pdf=fetch_pdf,
        )

    async def _extract_communique_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        source: Optional[Source] = None,
        now: Optional[datetime] = None,
    ) -> Optional[RawEntryData]:
        """Convenience method to fetch and parse an individual communiqué page."""
        resp = await client.get(url, timeout=DEFAULT_TIMEOUT)
        if resp.status_code != 200:
            return None
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=365)
        return await self.parse_detail_page(
            client=client,
            url=url,
            html=resp.text,
            cutoff=cutoff,
            enrich_pdf=False,
        )

    async def _fetch_and_extract_pdf(self, client: httpx.AsyncClient, pdf_url: str) -> str:
        """Convenience method to fetch and extract PDF text returning empty string on failure."""
        res = await self._extract_pdf_text(client, pdf_url)
        return res or ""

    async def _crawl_section(
        self,
        client: httpx.AsyncClient,
        base_section_url: str,
        cutoff_date: datetime,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        """Convenience method to crawl a listing section returning item dicts."""
        res_dict = await self._discover_from_listing(
            client=client,
            listing_url=base_section_url,
            list_type="section",
            cutoff=cutoff_date,
            max_pages=max_pages,
        )
        return [{"url": url, "published_at": dt} for url, dt in res_dict.items()]
