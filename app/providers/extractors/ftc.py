"""FTC (Federal Trade Commission - Bureau of Competition) extractor.

Audited and implemented under Bloque 17B of the HITCHINGS project.
Captures official competition enforcement actions, merger challenges, consent orders,
court litigations, and competition policy statements directly from official FTC systems.

Primary Channel:
    Official dedicated RSS feed: https://www.ftc.gov/feeds/press-release-competition.xml
Secondary / Historical Channel:
    FTC Legal Library Cases & Proceedings: https://www.ftc.gov/legal-library/browse/cases-proceedings?field_mission[30]=30

Canonical external_id:
    Derived from the stable official Drupal Node ID (nid) exposed in the RSS <guid>
    and HTML <link rel="shortlink">:
        ftc:competition:node-{nid} (e.g. ftc:competition:node-335907)
    Fallback when nid is absent:
        ftc:competition:{year}:{month}:{slug}

PDF Enrichment Policy:
    Candidate types: complaint, administrative complaint, consent order, stipulated order,
    court filing, injunction, decision.
    Max PDF size: 10 MB (10 * 1024 * 1024 bytes)
    Max pages processed: 15
    Max extracted chars: 50,000
    OCR: disabled
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import io
import logging
import re
from typing import Any, Optional, Sequence
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import httpx
import pypdf

from app.models.source import Source
from app.providers.base import RawEntryData

logger = logging.getLogger("ftc_extractor")

FTC_SOURCE_NAME = "Federal Trade Commission - Bureau of Competition"
FTC_BASE_URL = "https://www.ftc.gov/enforcement/competition"
FTC_RSS_FEED_URL = "https://www.ftc.gov/feeds/press-release-competition.xml"
FTC_LEGAL_LIBRARY_URL = (
    "https://www.ftc.gov/legal-library/browse/cases-proceedings?field_mission[30]=30"
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
DEFAULT_HTTP_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_FTC_CONFIG: dict[str, Any] = {
    "lookback_days": 8,
    "freshness_warning_hours": 168,
    "base_url": FTC_BASE_URL,
    "feed_url": FTC_RSS_FEED_URL,
    "fetch_pdf": True,
    "max_pdf_bytes": 10 * 1024 * 1024,  # 10 MB
    "max_pdf_pages_extract": 15,
    "max_pdf_chars_extract": 50000,
    "enable_legal_library": False,
}

# PDF Candidate legal terms
LEGAL_PDF_KEYWORDS = [
    "complaint",
    "order",
    "consent",
    "stipulated",
    "agreement",
    "injunction",
    "decision",
    "ruling",
    "brief",
    "amicus",
    "docket",
    "petition",
    "decree",
    "pleading",
    "filing",
]

# Non-legal PDF patterns to exclude
EXCLUDED_PDF_PATTERNS = [
    "competition-counts",
    "brochure",
    "flyer",
    "infographic",
    "annual-report",
    "budget",
    "fast-facts",
]

# Pure consumer protection signal terms for fail-closed guards
CONSUMER_PROTECTION_EXCLUSIVE_TERMS = [
    "robocalls",
    "do not call registry",
    "telemarketing scam",
    "debt collection fraud",
    "crypto scam",
    "imposter scam",
    "credit repair scam",
    "pyramid scheme",
    "refund checks to consumers",
]

COMPETITION_VALIDATION_TERMS = [
    "competition",
    "antitrust",
    "merger",
    "monopoly",
    "monopolization",
    "market power",
    "clayton act",
    "sherman act",
    "ftc act section 5",
    "hart-scott-rodino",
    "hsr",
    "collusion",
    "cartel",
    "price-fixing",
    "exclusive dealing",
    "non-compete",
    "unfair methods of competition",
]


@dataclass
class FTCDiscoveryMetrics:
    """Verifiable source-specific discovery metrics for the FTC extractor."""

    ftc_feed_items_total: int = 0
    ftc_inside_lookback: int = 0
    ftc_competition_items: int = 0
    ftc_consumer_items_skipped: int = 0
    ftc_detail_pages_fetched: int = 0
    ftc_pdf_links_found: int = 0
    ftc_pdfs_downloaded: int = 0
    ftc_pdfs_skipped: int = 0
    ftc_legal_library_items: int = 0
    ftc_duplicate_channels: int = 0
    ftc_undated_skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class FTCCompetitionExtractor:
    """Official extractor for Federal Trade Commission (FTC) Bureau of Competition."""

    def __init__(self) -> None:
        self.name = "ftc_competition"
        self.base_url = FTC_BASE_URL
        self.feed_url = FTC_RSS_FEED_URL
        self.metrics = FTCDiscoveryMetrics()

    @property
    def last_metrics(self) -> FTCDiscoveryMetrics:
        """Return discovery metrics from the last execution."""
        return self.metrics

    async def extract(
        self,
        client: httpx.AsyncClient,
        source: Source,
        lookback_days: Optional[int] = None,
    ) -> list[RawEntryData]:
        """Extract competition entries from FTC systems adhering to execution-scoped lookback."""
        self.metrics = FTCDiscoveryMetrics()

        config = source.config or {}
        effective_lookback = (
            lookback_days if lookback_days is not None else config.get("lookback_days", 8)
        )

        cutoff_dt: Optional[datetime] = None
        if effective_lookback is not None and effective_lookback > 0:
            cutoff_dt = datetime.now(timezone.utc) - timedelta(days=effective_lookback)

        logger.info(
            "Starting FTC extraction (effective_lookback=%s days, cutoff=%s)",
            effective_lookback,
            cutoff_dt.isoformat() if cutoff_dt else "None",
        )

        entries: list[RawEntryData] = []
        seen_ext_ids: set[str] = set()

        # 1. Primary Discovery: Bureau of Competition RSS Feed
        rss_entries = await self._extract_rss_feed(
            client=client,
            source=source,
            cutoff_dt=cutoff_dt,
            seen_ext_ids=seen_ext_ids,
        )
        entries.extend(rss_entries)

        # 2. Secondary / Historical Discovery: Legal Library (if enabled in source config)
        if config.get("enable_legal_library", False):
            logger.info("FTC Legal Library discovery is enabled by source config")
            ll_entries = await self._extract_legal_library(
                client=client,
                source=source,
                cutoff_dt=cutoff_dt,
                seen_ext_ids=seen_ext_ids,
            )
            entries.extend(ll_entries)

        logger.info(
            "FTC extraction finished: %d entries extracted (discovered=%d, inside_lookback=%d, competition=%d, consumer_skipped=%d)",
            len(entries),
            self.metrics.ftc_feed_items_total,
            self.metrics.ftc_inside_lookback,
            self.metrics.ftc_competition_items,
            self.metrics.ftc_consumer_items_skipped,
        )
        return entries

    async def _extract_rss_feed(
        self,
        client: httpx.AsyncClient,
        source: Source,
        cutoff_dt: Optional[datetime],
        seen_ext_ids: set[str],
    ) -> list[RawEntryData]:
        """Discover and fetch entries via the official Bureau of Competition RSS feed."""
        entries: list[RawEntryData] = []
        config = source.config or {}
        feed_url = config.get("feed_url", self.feed_url)

        headers = DEFAULT_HTTP_HEADERS

        try:
            resp = await client.get(feed_url, headers=headers, timeout=25.0)
            if resp.status_code != 200:
                logger.error("Failed to fetch FTC RSS feed: HTTP %d (%s)", resp.status_code, feed_url)
                return entries
            xml_text = resp.text
        except Exception as exc:
            logger.error("Exception fetching FTC RSS feed (%s): %s", feed_url, exc)
            return entries

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Failed to parse FTC RSS XML: %s", exc)
            return entries

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> found in FTC RSS feed")
            return entries

        items = channel.findall("item")
        self.metrics.ftc_feed_items_total = len(items)

        for item in items:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            guid_elem = item.find("guid")
            guid_str = (guid_elem.text if guid_elem is not None else "").strip()
            pub_date_str = (item.findtext("pubDate") or "").strip()
            desc = (item.findtext("description") or "").strip()
            categories = [c.text.strip() for c in item.findall("category") if c.text]

            # 1. Parse publication date
            published_at = self._parse_rfc822_date(pub_date_str)
            if not published_at:
                # Fallback to date in URL path
                published_at = self._extract_date_from_url(link)

            if not published_at:
                self.metrics.ftc_undated_skipped += 1
                logger.warning("Skipping undated FTC RSS item: '%s' (%s)", title[:60], link)
                continue

            # 2. Check cutoff
            if cutoff_dt and published_at < cutoff_dt:
                continue

            self.metrics.ftc_inside_lookback += 1

            # 3. Material filtering guard: exclude pure consumer protection items
            if self._is_pure_consumer_protection(title, desc, categories):
                self.metrics.ftc_consumer_items_skipped += 1
                logger.info("Skipping pure Consumer Protection item: '%s'", title[:60])
                continue

            self.metrics.ftc_competition_items += 1

            # 4. Construct canonical external_id
            external_id = self._build_canonical_external_id(guid=guid_str, url=link)
            if external_id in seen_ext_ids:
                continue
            seen_ext_ids.add(external_id)

            # 5. Fetch and clean HTML detail page
            detail_data = await self._fetch_and_parse_detail(
                client=client,
                source=source,
                url=link,
                title=title,
                published_at=published_at,
                guid=guid_str,
                categories=categories,
                rss_description=desc,
            )

            if detail_data:
                entries.append(detail_data)

        return entries

    async def _fetch_and_parse_detail(
        self,
        client: httpx.AsyncClient,
        source: Source,
        url: str,
        title: str,
        published_at: datetime,
        guid: str,
        categories: list[str],
        rss_description: str,
    ) -> Optional[RawEntryData]:
        """Fetch article HTML, clean noise, extract commission votes, and enrich with PDF."""
        self.metrics.ftc_detail_pages_fetched += 1
        headers = DEFAULT_HTTP_HEADERS

        html_content = ""
        try:
            resp = await client.get(url, headers=headers, timeout=25.0)
            if resp.status_code == 200:
                html_content = resp.text
            else:
                logger.warning("HTTP %d fetching FTC detail: %s", resp.status_code, url)
        except Exception as exc:
            logger.warning("Failed to fetch FTC detail (%s): %s", url, exc)

        canonical_url = url
        subtitle = ""
        body_text = ""
        extracted_tags: list[str] = list(categories)
        commission_vote: Optional[str] = None
        docket_number: Optional[str] = None
        matter_number: Optional[str] = None
        legal_bases: list[str] = []
        node_id: Optional[str] = guid if guid.isdigit() else None

        candidate_pdf_links: list[tuple[str, str]] = []

        if html_content:
            soup = BeautifulSoup(html_content, "html.parser")

            # Canonical link & node ID
            canon_tag = soup.find("link", rel="canonical")
            if canon_tag and canon_tag.get("href"):
                canonical_url = canon_tag["href"]

            shortlink_tag = soup.find("link", rel="shortlink")
            if shortlink_tag and shortlink_tag.get("href"):
                sl_href = shortlink_tag["href"]
                m_nid = re.search(r"/node/(\d+)", sl_href)
                if m_nid and not node_id:
                    node_id = m_nid.group(1)

            # Subtitle
            sub_elem = soup.find("div", class_=lambda c: c and "field--name-field-subtitle" in c)
            if sub_elem:
                subtitle = sub_elem.get_text(strip=True)

            # Tags from article DOM
            tags_elem = soup.find("div", class_=lambda c: c and "field--name-field-tags-view" in c)
            if tags_elem:
                for a in tags_elem.find_all("a"):
                    t_text = a.get_text(strip=True)
                    if t_text and t_text not in extracted_tags:
                        extracted_tags.append(t_text)

            # Find the full article container
            full_art = soup.select_one("article.node--view-mode-full")
            if not full_art:
                full_art = soup.find("article", class_=lambda c: c and "node--type-press-release" in c)
            if not full_art:
                full_art = soup.find("article")

            # If node_id still missing, check article classes
            if not node_id and full_art:
                for cls in full_art.get("class", []):
                    m_cls_nid = re.match(r"^node--(\d+)$", cls)
                    if m_cls_nid:
                        node_id = m_cls_nid.group(1)
                        break

            # Target substantive body container
            body_container = None
            if full_art:
                body_container = full_art.find("div", class_=lambda c: c and "field--name-body" in c)
            if not body_container:
                body_container = soup.find("div", class_=lambda c: c and "field--name-body" in c)

            if body_container:
                # Remove boilerplate blocks, media contacts, phones, and social buttons
                for unneeded in body_container.find_all(
                    class_=lambda c: c
                    and (
                        "field--name-field-boilerplate-block" in c
                        or "field--name-field-media-contact-single" in c
                        or "field--name-field-phone" in c
                        or "share" in c
                    )
                ):
                    unneeded.decompose()

                paras: list[str] = []
                for p in body_container.find_all(["p", "ul", "ol"]):
                    p_text = " ".join(p.get_text().split())
                    if not p_text:
                        continue
                    # Skip contact or generic footer boilerplate
                    if p_text.startswith("Media Contact:") or p_text.startswith("Staff Contact:"):
                        continue
                    paras.append(p_text)

                body_text = "\n\n".join(paras).strip()

                # Extract Commission vote if present
                for p in paras:
                    if "commission vote" in p.lower():
                        commission_vote = p
                        break

                # Extract legal candidate PDF links
                for a in body_container.find_all("a", href=True):
                    href = a["href"]
                    anchor_text = a.get_text(strip=True)
                    if self._is_candidate_legal_pdf(href, anchor_text):
                        full_pdf_url = urljoin(url, href)
                        candidate_pdf_links.append((anchor_text, full_pdf_url))

        # Fallback to RSS description if HTML body was empty
        if not body_text:
            body_text = rss_description or title

        # Extract matter / docket numbers and legal bases from text
        docket_number = self._extract_docket_number(body_text)
        matter_number = self._extract_matter_number(body_text)
        legal_bases = self._extract_legal_bases(body_text)

        # Content classification
        content_type = self._classify_content_type(title, body_text, extracted_tags)

        # PDF Enrichment
        pdf_url: Optional[str] = None
        pdf_doc_type: Optional[str] = None
        pdf_extracted = False
        pdf_pages_processed = 0
        pdf_chars = 0
        pdf_skip_reason: Optional[str] = None

        config = source.config or {}
        fetch_pdf_enabled = config.get("fetch_pdf", True)

        if candidate_pdf_links:
            self.metrics.ftc_pdf_links_found += len(candidate_pdf_links)
            # Pick first legal candidate PDF
            preferred_text, preferred_url = candidate_pdf_links[0]
            pdf_url = preferred_url
            pdf_doc_type = self._classify_pdf_document_type(preferred_text, preferred_url)

            if fetch_pdf_enabled:
                pdf_res = await self._extract_pdf_enrichment(
                    client=client,
                    pdf_url=preferred_url,
                    max_bytes=config.get("max_pdf_bytes", 10 * 1024 * 1024),
                    max_pages=config.get("max_pdf_pages_extract", 15),
                    max_chars=config.get("max_pdf_chars_extract", 50000),
                )
                if pdf_res.get("text"):
                    pdf_extracted = True
                    pdf_pages_processed = pdf_res.get("pages_processed", 0)
                    pdf_chars = pdf_res.get("chars", 0)
                    self.metrics.ftc_pdfs_downloaded += 1
                    body_text += f"\n\n--- OFFICIAL LEGAL ATTACHMENT ({pdf_doc_type.upper()}) ---\n\n" + pdf_res["text"]
                else:
                    self.metrics.ftc_pdfs_skipped += 1
                    pdf_skip_reason = pdf_res.get("skip_reason", "extraction_failed")
            else:
                self.metrics.ftc_pdfs_skipped += 1
                pdf_skip_reason = "pdf_fetching_disabled_by_config"
        else:
            pdf_skip_reason = "no_candidate_legal_pdf_found"

        # Re-derive canonical external_id if node_id was extracted from HTML
        external_id = self._build_canonical_external_id(guid=node_id or guid, url=canonical_url)

        raw_metadata: dict[str, Any] = {
            "agency": "FTC",
            "bureau": "Bureau of Competition",
            "guid": guid,
            "node_id": node_id,
            "official_date": published_at.isoformat(),
            "matter_number": matter_number,
            "docket_number": docket_number,
            "action_type": content_type,
            "legal_bases": legal_bases,
            "industries": extracted_tags,
            "commission_vote": commission_vote,
            "pdf_url": pdf_url,
            "pdf_document_type": pdf_doc_type,
            "pdf_extracted": pdf_extracted,
            "pdf_pages_processed": pdf_pages_processed,
            "pdf_chars": pdf_chars,
            "pdf_skip_reason": pdf_skip_reason,
            "source_channel": "rss",
        }

        return RawEntryData(
            source_id=source.id,
            external_id=external_id,
            title=title,
            url=canonical_url,
            content=body_text,
            excerpt=subtitle or (body_text[:280] + "..." if len(body_text) > 280 else body_text),
            published_at=published_at,
            language="en",
            content_type=content_type,
            raw_metadata=raw_metadata,
        )

    async def _extract_pdf_enrichment(
        self,
        client: httpx.AsyncClient,
        pdf_url: str,
        max_bytes: int,
        max_pages: int,
        max_chars: int,
    ) -> dict[str, Any]:
        """Download legal PDF and extract digital text layer using pypdf (OCR disabled)."""
        try:
            resp = await client.get(
                pdf_url,
                headers=DEFAULT_HTTP_HEADERS,
                timeout=25.0,
            )
            if resp.status_code != 200:
                logger.warning("HTTP %d downloading PDF %s", resp.status_code, pdf_url)
                return {"text": None, "skip_reason": f"http_status_{resp.status_code}"}

            content = resp.content
            if len(content) > max_bytes:
                logger.info("Skipping PDF %s: size %d bytes exceeds max %d bytes", pdf_url, len(content), max_bytes)
                return {"text": None, "skip_reason": "pdf_exceeds_max_bytes"}

            reader = pypdf.PdfReader(io.BytesIO(content))
            num_pages = len(reader.pages)
            pages_to_process = min(num_pages, max_pages)

            chunks: list[str] = []
            total_chars = 0

            for page_idx in range(pages_to_process):
                page = reader.pages[page_idx]
                page_text = page.extract_text() or ""
                if total_chars + len(page_text) > max_chars:
                    remaining = max_chars - total_chars
                    chunks.append(page_text[:remaining])
                    total_chars = max_chars
                    break
                chunks.append(page_text)
                total_chars += len(page_text)

            full_extracted = "\n\n".join(chunks).strip()
            return {
                "text": full_extracted,
                "pages_processed": pages_to_process,
                "chars": len(full_extracted),
                "skip_reason": None if full_extracted else "empty_text_layer",
            }
        except Exception as exc:
            logger.warning("Failed to extract PDF %s: %s", pdf_url, exc)
            return {"text": None, "skip_reason": f"extraction_error_{type(exc).__name__}"}

    async def _discover_from_legal_library(
        self,
        client: httpx.AsyncClient,
        source: Source,
        cutoff_dt: Optional[datetime],
        seen_ext_ids: set[str],
    ) -> list[RawEntryData]:
        """Secondary/Historical discovery via Cases and Proceedings (field_mission[30]=30)."""
        entries: list[RawEntryData] = []
        headers = DEFAULT_HTTP_HEADERS

        try:
            resp = await client.get(FTC_LEGAL_LIBRARY_URL, headers=headers, timeout=25.0)
            if resp.status_code != 200:
                logger.warning("HTTP %d fetching Legal Library (%s)", resp.status_code, FTC_LEGAL_LIBRARY_URL)
                return entries
            html_text = resp.text
        except Exception as exc:
            logger.warning("Exception fetching Legal Library: %s", exc)
            return entries

        soup = BeautifulSoup(html_text, "html.parser")
        rows = soup.find_all("div", class_=lambda c: c and "views-row" in c)
        self.metrics.ftc_legal_library_items = len(rows)

        for row in rows:
            h_tag = row.find(["h2", "h3", "h4"])
            a_tag = h_tag.find("a") if h_tag else row.find("a")
            if not a_tag or not a_tag.get("href"):
                continue

            link = urljoin(FTC_BASE_URL, a_tag["href"])
            title = a_tag.get_text(strip=True)

            # Skip non-enforcement items (blogs, conferences)
            if "/microeconomics" in link or "/policy/advocacy-research" in link:
                continue

            ext_id = self._build_canonical_external_id(guid="", url=link)
            if ext_id in seen_ext_ids:
                self.metrics.ftc_duplicate_channels += 1
                continue
            seen_ext_ids.add(ext_id)

            # Extract date if available
            time_tag = row.find("time")
            pub_date: Optional[datetime] = None
            if time_tag and time_tag.get("datetime"):
                try:
                    pub_date = datetime.fromisoformat(time_tag["datetime"]).astimezone(timezone.utc)
                except Exception:
                    pass

            if not pub_date:
                pub_date = self._extract_date_from_url(link) or datetime.now(timezone.utc)

            if cutoff_dt and pub_date < cutoff_dt:
                continue

            desc_elem = row.find(class_=lambda c: c and ("summary" in c or "body" in c or "description" in c))
            desc_text = desc_elem.get_text(strip=True) if desc_elem else title

            entries.append(
                RawEntryData(
                    source_id=source.id,
                    external_id=ext_id,
                    title=title,
                    url=link,
                    content=desc_text,
                    excerpt=desc_text[:280],
                    published_at=pub_date,
                    language="en",
                    content_type="court_litigation",
                    raw_metadata={
                        "agency": "FTC",
                        "bureau": "Bureau of Competition",
                        "source_channel": "legal_library",
                        "official_date": pub_date.isoformat(),
                    },
                )
            )

        return entries

    # --------------------------------------------------------------------------
    # HELPERS & POLICY METHODS
    # --------------------------------------------------------------------------

    def _build_canonical_external_id(self, guid: str, url: str) -> str:
        """Construct deterministic, immutable external_id for an FTC competition item."""
        if guid and guid.isdigit():
            return f"ftc:competition:node-{guid}"

        # Fallback to normalized path: /YYYY/MM/{slug}
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        parts = path.split("/")

        # Check for /YYYY/MM/{slug} pattern
        for i in range(len(parts) - 2):
            if parts[i].isdigit() and len(parts[i]) == 4 and parts[i + 1].isdigit() and len(parts[i + 1]) == 2:
                year = parts[i]
                month = parts[i + 1]
                slug = parts[i + 2]
                return f"ftc:competition:{year}:{month}:{slug}"

        # General path slug fallback
        slug = parts[-1] if parts else "item"
        return f"ftc:competition:{slug}"

    def _parse_rfc822_date(self, date_str: str) -> Optional[datetime]:
        """Parse RFC 822 / 2822 date string to timezone-aware UTC datetime."""
        if not date_str:
            return None
        try:
            dt = parsedate_to_datetime(date_str)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _extract_date_from_url(self, url: str) -> Optional[datetime]:
        """Extract YYYY/MM date from standard FTC press release path."""
        m = re.search(r"/(\d{4})/(\d{2})/", url)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            return datetime(year, month, 1, tzinfo=timezone.utc)
        return None

    def _is_pure_consumer_protection(
        self, title: str, description: str, categories: list[str]
    ) -> bool:
        """Fail-closed guard to exclude pure Consumer Protection announcements."""
        combined = f"{title} {description} {' '.join(categories)}".lower()

        # If it explicitly matches competition terms, it's valid competition content
        if any(term in combined for term in COMPETITION_VALIDATION_TERMS):
            return False

        # If it has exclusive consumer protection terms without competition terms
        if any(cp_term in combined for cp_term in CONSUMER_PROTECTION_EXCLUSIVE_TERMS):
            return True

        # Non-competition event/budget proposals (e.g. horseracing authority budget)
        if "horseracing" in combined or "budget for horseracing" in combined:
            return True

        return False

    def _is_candidate_legal_pdf(self, href: str, anchor_text: str) -> bool:
        """Check if PDF link corresponds to a substantive legal document."""
        href_lower = href.lower()
        if not (href_lower.endswith(".pdf") or ".pdf?" in href_lower or "/pdf/" in href_lower):
            return False

        # Exclude known brochures, infographics, annual budgets
        if any(excl in href_lower for excl in EXCLUDED_PDF_PATTERNS):
            return False

        combined_text = f"{anchor_text} {href}".lower()
        return any(term in combined_text for term in LEGAL_PDF_KEYWORDS)

    def _classify_pdf_document_type(self, anchor_text: str, href: str) -> str:
        """Classify PDF legal attachment type."""
        t = f"{anchor_text} {href}".lower()
        if "complaint" in t:
            return "complaint"
        if "consent" in t or "agreement" in t:
            return "consent_order"
        if "stipulated" in t or "final order" in t:
            return "final_order"
        if "injunction" in t:
            return "injunction"
        if "decision" in t or "ruling" in t:
            return "decision"
        if "brief" in t or "amicus" in t:
            return "amicus_brief"
        return "court_filing"

    def _classify_content_type(
        self, title: str, body: str, tags: list[str]
    ) -> str:
        """Map FTC competition action to standard Observatory content_type."""
        t_lower = f"{title} {' '.join(tags)} {body[:1000]}".lower()

        if any(w in t_lower for w in ["merger", "acquisition", "deal", "sale to"]):
            return "merger_action"
        if any(w in t_lower for w in ["consent order", "settlement", "stipulated", "order resolving", "resolves litigation", "resolving antitrust concerns"]):
            return "consent_order"
        if any(w in t_lower for w in ["final order", "approves final"]):
            return "final_order"
        if "administrative complaint" in t_lower or "files administrative" in t_lower:
            return "administrative_complaint"
        if any(w in t_lower for w in ["amicus", "court", "litigation", "judge", "ruling", "win"]):
            return "court_litigation"
        if any(w in t_lower for w in ["guidelines", "policy statement", "competition policy"]):
            return "competition_policy"
        if "guidance" in t_lower:
            return "competition_guidance"

        return "antitrust_enforcement"


    def _extract_docket_number(self, text: str) -> Optional[str]:
        """Extract federal court or FTC administrative Docket Number."""
        m = re.search(r"(?:Docket\s+(?:No\.|Number)\s*[:\s]*|docket\s+no\.\s*)([A-Za-z0-9\-:]+)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    def _extract_matter_number(self, text: str) -> Optional[str]:
        """Extract FTC internal Matter Number (e.g. Matter No. 241 0012)."""
        m = re.search(r"(?:Matter\s+(?:No\.|Number)\s*[:\s]*|matter\s+no\.\s*)([0-9\sA-Za-z]+)", text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if len(val) <= 15:
                return val
        return None

    def _extract_legal_bases(self, text: str) -> list[str]:
        """Extract statutory antitrust provisions referenced in the text."""
        bases: list[str] = []
        t_lower = text.lower()
        if "clayton act" in t_lower or "section 7" in t_lower:
            bases.append("Clayton Act Section 7")
        if "ftc act" in t_lower or "section 5" in t_lower:
            bases.append("FTC Act Section 5")
        if "sherman act" in t_lower:
            if "section 1" in t_lower:
                bases.append("Sherman Act Section 1")
            if "section 2" in t_lower:
                bases.append("Sherman Act Section 2")
            if "sherman act" not in " ".join(bases).lower():
                bases.append("Sherman Act")
        if "hart-scott-rodino" in t_lower or "hsr act" in t_lower:
            bases.append("Hart-Scott-Rodino Act")
        return bases
