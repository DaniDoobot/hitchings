"""DOJ Antitrust Division official extractor (Source 17/17).

Extracts official enforcement actions, consent decrees, plea agreements,
merger challenges, closing statements, and policy decisions from the
U.S. Department of Justice Antitrust Division (DOJ ATR) adhering to
execution-scoped lookbacks, strict read-only guarantees, and deterministic
canonical identity.
"""

from __future__ import annotations

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

logger = logging.getLogger("doj_antitrust_extractor")

DOJ_ATR_SOURCE_NAME = "Department of Justice - Antitrust Division"
DOJ_ATR_BASE_URL = "https://www.justice.gov/atr"
DOJ_ATR_RSS_FEED_URL = "https://www.justice.gov/news/rss?field_component=376&type=press_release"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)
DEFAULT_HTTP_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_DOJ_ATR_CONFIG: dict[str, Any] = {
    "lookback_days": 8,
    "freshness_warning_hours": 168,
    "base_url": DOJ_ATR_BASE_URL,
    "feed_url": DOJ_ATR_RSS_FEED_URL,
    "fetch_pdf": True,
    "max_pdf_bytes": 10 * 1024 * 1024,  # 10 MB
    "max_pdf_pages_extract": 15,
    "max_pdf_chars_extract": 50000,
}

# Legal PDF classification keywords
LEGAL_PDF_KEYWORDS = [
    "complaint",
    "consent",
    "decree",
    "plea",
    "agreement",
    "indictment",
    "order",
    "stipulation",
    "settlement",
    "brief",
    "amicus",
    "statement",
    "judgment",
    "motion",
    "cis",  # Competitive Impact Statement
    "competitive-impact",
]

EXCLUDED_PDF_PATTERNS = [
    "annual-report",
    "brochure",
    "flyer",
    "infographic",
    "budget",
    "fast-facts",
    "org-chart",
]

# Common antitrust legal bases in US federal enforcement
STATUTE_PATTERNS = [
    (r"Sherman Act(?:\s*(?:Section|§)\s*1)?", "Sherman Act § 1 (Conspiracy / Cartel / Restraint of Trade)"),
    (r"Sherman Act(?:\s*(?:Section|§)\s*2)?", "Sherman Act § 2 (Monopolization)"),
    (r"Clayton Act(?:\s*(?:Section|§)\s*7A)?", "Clayton Act § 7A (Hart-Scott-Rodino Premerger Review)"),
    (r"Clayton Act(?:\s*(?:Section|§)\s*7)?", "Clayton Act § 7 (Substantial Lessening of Competition)"),
    (r"Clayton Act(?:\s*(?:Section|§)\s*8)?", "Clayton Act § 8 (Interlocking Directorates)"),
    (r"Hart-Scott-Rodino|HSR Act", "Hart-Scott-Rodino (HSR) Act"),
    (r"Robinson-Patman Act", "Robinson-Patman Act"),
]


@dataclass
class DOJAntitrustDiscoveryMetrics:
    """Verifiable source-specific discovery metrics for the DOJ Antitrust extractor."""

    doj_feed_items_total: int = 0
    doj_inside_lookback: int = 0
    doj_antitrust_items: int = 0
    doj_non_antitrust_skipped: int = 0
    doj_detail_pages_fetched: int = 0
    doj_pdf_links_found: int = 0
    doj_pdfs_downloaded: int = 0
    doj_pdfs_skipped: int = 0
    doj_undated_skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class DOJAntitrustExtractor:
    """Official extractor for the U.S. Department of Justice - Antitrust Division."""

    def __init__(self, feed_url: str = DOJ_ATR_RSS_FEED_URL) -> None:
        self.feed_url = feed_url
        self.metrics = DOJAntitrustDiscoveryMetrics()

    @property
    def last_metrics(self) -> DOJAntitrustDiscoveryMetrics:
        """Return discovery metrics from the last extraction run."""
        return self.metrics

    async def extract(
        self,
        client: httpx.AsyncClient,
        source: Source,
        lookback_days: Optional[int] = None,
    ) -> list[RawEntryData]:
        """Extract Antitrust Division entries adhering to execution-scoped lookback."""
        self.metrics = DOJAntitrustDiscoveryMetrics()

        config = source.config or {}
        effective_lookback = (
            lookback_days if lookback_days is not None else config.get("lookback_days", 8)
        )

        cutoff_dt: Optional[datetime] = None
        if effective_lookback is not None and effective_lookback > 0:
            cutoff_dt = datetime.now(timezone.utc) - timedelta(days=effective_lookback)

        logger.info(
            "Starting DOJ Antitrust extraction (lookback=%s days, cutoff=%s)",
            effective_lookback,
            cutoff_dt.isoformat() if cutoff_dt else "None",
        )

        entries: list[RawEntryData] = []
        feed_url = config.get("feed_url", self.feed_url)

        try:
            resp = await client.get(feed_url, headers=DEFAULT_HTTP_HEADERS, timeout=25.0)
            if resp.status_code != 200:
                logger.error("DOJ ATR RSS feed returned status %s from %s", resp.status_code, feed_url)
                return []
            xml_content = resp.content
        except Exception as exc:
            logger.error("Failed to fetch DOJ ATR RSS feed from %s: %s", feed_url, exc)
            return []

        try:
            root = ET.fromstring(xml_content)
        except Exception as exc:
            logger.error("Failed to parse DOJ ATR RSS XML: %s", exc)
            return []

        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else root.findall(".//item")
        self.metrics.doj_feed_items_total = len(items)

        for item in items:
            title_elem = item.find("title")
            link_elem = item.find("link")
            guid_elem = item.find("guid")
            pub_date_elem = item.find("pubDate")
            desc_elem = item.find("description")

            title = " ".join((title_elem.text or "").split()) if title_elem is not None else ""
            link = (link_elem.text or "").strip() if link_elem is not None else ""
            guid = (guid_elem.text or "").strip() if guid_elem is not None else link
            rss_desc = (desc_elem.text or "").strip() if desc_elem is not None else ""

            if not link or not title:
                continue

            # Parse publication date
            published_at = self._parse_rss_date(pub_date_elem.text if pub_date_elem is not None else None)
            if not published_at:
                self.metrics.doj_undated_skipped += 1
                continue

            # Check execution-scoped lookback cutoff
            if cutoff_dt and published_at < cutoff_dt:
                continue

            self.metrics.doj_inside_lookback += 1

            # Fetch and parse detail page HTML
            try:
                entry = await self._fetch_and_parse_detail(
                    client=client,
                    source=source,
                    url=link,
                    title=title,
                    guid=guid,
                    rss_description=rss_desc,
                    published_at=published_at,
                )
                if entry:
                    entries.append(entry)
            except Exception as exc:
                logger.warning("Error parsing DOJ detail page %s: %s", link, exc)

        logger.info(
            "DOJ Antitrust extraction finished: %d valid entries extracted "
            "(feed_total=%d, inside_lookback=%d, antitrust=%d, non_antitrust_skipped=%d)",
            len(entries),
            self.metrics.doj_feed_items_total,
            self.metrics.doj_inside_lookback,
            self.metrics.doj_antitrust_items,
            self.metrics.doj_non_antitrust_skipped,
        )
        return entries

    def _parse_rss_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Safely parse RFC 822 / RFC 2822 RSS pubDate to UTC datetime."""
        if not date_str:
            return None
        try:
            dt = parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _is_doj_antitrust_scope(self, url: str, soup: BeautifulSoup) -> tuple[bool, str]:
        """Fail-closed guard to ensure publication belongs to Antitrust Division jurisdiction.

        Excludes non-antitrust publications originating from USAO or unrelated divisions.
        """
        url_lower = url.lower()
        is_usao_path = "/usao-" in url_lower

        # 1. Extract official Drupal taxonomy terms, offices, and topics
        topics = [
            t.get_text(strip=True).lower()
            for t in soup.select(".node-topics, .field--name-field-pr-topic, .topic, .usa-tag")
        ]
        components = [
            c.get_text(strip=True).lower()
            for c in soup.select(
                ".field--name-field-pr-component, .node-office, .field--name-field-components, .field--name-field-office"
            )
        ]

        has_antitrust_topic = any("antitrust" in t for t in topics)
        has_antitrust_component = any("antitrust" in c for c in components)

        # 2. Inspect lead paragraph / body mention
        body_elem = soup.select_one(".field--name-field-pr-body, article, main")
        has_lead_atr_mention = False
        if body_elem:
            lead_text = body_elem.get_text(separator=" ", strip=True)[:600].lower()
            if "antitrust division" in lead_text or "antitrust" in lead_text:
                has_lead_atr_mention = True

        if has_antitrust_topic or has_antitrust_component or (has_lead_atr_mention and not is_usao_path):
            return True, "antitrust_scope_confirmed"

        if is_usao_path:
            return False, "usao_local_office_excluded"

        return False, "non_antitrust_component_excluded"

    def _extract_slug(self, url: str) -> str:
        """Extract the canonical URL slug."""
        path = urlparse(url).path.rstrip("/")
        slug = path.split("/")[-1] if path else ""
        return slug or "unspecified"

    def _build_canonical_external_id(
        self,
        node_id: Optional[str],
        published_at: datetime,
        url: str,
    ) -> str:
        """Construct deterministic and permanent external_id.

        Format:
        1. If official node_id is available: doj_atr:node:{node_id}
        2. Otherwise fallback: doj_atr:{year:04d}:{month:02d}:{slug}
        """
        if node_id and node_id.isdigit():
            return f"doj_atr:node:{node_id}"

        slug = self._extract_slug(url)
        return f"doj_atr:{published_at.year:04d}:{published_at.month:02d}:{slug}"

    def _classify_action_type(self, title: str, body_text: str) -> str:
        """Classify DOJ Antitrust enforcement action type."""
        text_lower = f"{title} {body_text}".lower()

        if "consent decree" in text_lower or "proposed settlement" in text_lower or "settles" in text_lower:
            return "consent_decree"
        if "pleads guilty" in text_lower or "guilty plea" in text_lower or "plea agreement" in text_lower:
            return "plea_agreement"
        if "indictment" in text_lower or "indicted" in text_lower or "jury convicts" in text_lower or "convicted" in text_lower:
            return "criminal_conviction"
        if "merger" in text_lower and ("abandons" in text_lower or "blocks" in text_lower or "challenge" in text_lower):
            return "merger_challenge"
        if "closing of its investigation" in text_lower or "closing statement" in text_lower:
            return "merger_closing_statement"
        if "business review letter" in text_lower:
            return "business_review_letter"
        if "premerger" in text_lower or "hsr" in text_lower or "penalty" in text_lower:
            return "premerger_enforcement"
        if "guidelines" in text_lower or "annual report" in text_lower or "call to action" in text_lower:
            return "policy_statement"
        if "statement of interest" in text_lower or "amicus" in text_lower:
            return "amicus_statement_of_interest"

        return "press_release"

    def _extract_case_or_docket(self, body_text: str) -> Optional[str]:
        """Extract court case or civil action number if present."""
        m = re.search(
            r"(?:Civil Action No\.|Case No\.|Docket No\.|No\.)\s*[:#]?\s*([0-9]{1,2}:[0-9]{2}-[a-zA-Z]{1,4}-[0-9]{3,6}|[0-9]{1,2}-[a-zA-Z]{1,4}-[0-9]{3,6}|[0-9A-Za-z\-:]{4,20})",
            body_text,
        )
        return m.group(1).strip() if m else None

    def _extract_legal_bases(self, body_text: str) -> list[str]:
        """Identify relevant statutory grounds cited in the body."""
        found = []
        for pattern, label in STATUTE_PATTERNS:
            if re.search(pattern, body_text, re.IGNORECASE):
                found.append(label)
        return found

    def _is_candidate_legal_pdf(self, href: str, text: str) -> bool:
        """Determine whether an anchor link refers to an official legal document PDF."""
        h_lower = href.lower()
        t_lower = text.lower()

        if not h_lower.endswith(".pdf") and ".pdf?" not in h_lower and "/pdf" not in h_lower:
            return False

        if any(bad in h_lower or bad in t_lower for bad in EXCLUDED_PDF_PATTERNS):
            return False

        return any(k in h_lower or k in t_lower for k in LEGAL_PDF_KEYWORDS)

    async def _extract_pdf_enrichment(
        self,
        client: httpx.AsyncClient,
        pdf_url: str,
        max_bytes: int,
        max_pages: int,
        max_chars: int,
    ) -> dict[str, Any]:
        """Safely fetch and parse text from a legal attachment PDF without OCR."""
        try:
            resp = await client.get(
                pdf_url,
                headers=DEFAULT_HTTP_HEADERS,
                timeout=30.0,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                return {"text": "", "skip_reason": f"http_status_{resp.status_code}"}

            content = resp.content
            if len(content) > max_bytes:
                return {"text": "", "skip_reason": "exceeds_max_bytes"}

            reader = pypdf.PdfReader(io.BytesIO(content))
            pages_extracted = 0
            extracted_chars = 0
            text_parts = []

            for page_idx, page in enumerate(reader.pages):
                if page_idx >= max_pages:
                    break
                p_text = page.extract_text() or ""
                cleaned_page = " ".join(p_text.split())
                if cleaned_page:
                    text_parts.append(cleaned_page)
                    extracted_chars += len(cleaned_page)
                    pages_extracted += 1
                if extracted_chars >= max_chars:
                    break

            combined_pdf_text = "\n\n".join(text_parts).strip()
            if not combined_pdf_text:
                return {"text": "", "skip_reason": "scanned_or_empty_pdf"}

            return {
                "text": combined_pdf_text[:max_chars],
                "pages_processed": pages_extracted,
                "chars": len(combined_pdf_text[:max_chars]),
                "skip_reason": None,
            }
        except Exception as exc:
            logger.warning("Failed to extract PDF text from %s: %s", pdf_url, exc)
            return {"text": "", "skip_reason": f"extraction_error: {type(exc).__name__}"}

    async def _fetch_and_parse_detail(
        self,
        client: httpx.AsyncClient,
        source: Source,
        url: str,
        title: str,
        guid: str,
        rss_description: str,
        published_at: datetime,
    ) -> Optional[RawEntryData]:
        """Fetch and extract substantive content from DOJ HTML detail page."""
        self.metrics.doj_detail_pages_fetched += 1

        try:
            resp = await client.get(url, headers=DEFAULT_HTTP_HEADERS, timeout=25.0)
            if resp.status_code != 200:
                logger.warning("Detail page HTTP %s for %s", resp.status_code, url)
                return None
            html_text = resp.text
        except Exception as exc:
            logger.warning("Failed to fetch detail page %s: %s", url, exc)
            return None

        soup = BeautifulSoup(html_text, "html.parser")

        # 1. Fail-closed Scope Evaluation
        is_scope, reason = self._is_doj_antitrust_scope(url, soup)
        if not is_scope:
            self.metrics.doj_non_antitrust_skipped += 1
            logger.info("Skipping non-antitrust publication (%s): %s", reason, url)
            return None

        self.metrics.doj_antitrust_items += 1

        # 2. Canonical URL & Date Hardening
        canonical_elem = soup.find("link", rel="canonical")
        canonical_url = canonical_elem["href"].strip() if canonical_elem and canonical_elem.get("href") else url

        pub_meta = soup.find("meta", property="article:published_time")
        if pub_meta and pub_meta.get("content"):
            try:
                dt_meta = datetime.fromisoformat(pub_meta["content"])
                if dt_meta.tzinfo is None:
                    dt_meta = dt_meta.replace(tzinfo=timezone.utc)
                published_at = dt_meta.astimezone(timezone.utc)
            except Exception:
                pass

        # 3. Node ID discovery (if available in system paths or attributes)
        node_id: Optional[str] = None
        shortlink = soup.find("link", rel="shortlink")
        if shortlink and "node/" in shortlink.get("href", ""):
            node_id = shortlink["href"].split("node/")[-1].strip()

        # 4. Clean Body Text Extraction
        body_elem = soup.select_one(".field--name-field-pr-body, .node__content, article, .usa-layout-docs__main")
        body_text = ""
        candidate_pdf_links: list[tuple[str, str]] = []

        if body_elem:
            # Decompose boilerplate & unneeded blocks
            for tag in body_elem.find_all(["script", "style", "nav", "aside", "header"]):
                tag.decompose()

            paras: list[str] = []
            for p in body_elem.find_all(["p", "ul", "ol"]):
                p_text = " ".join(p.get_text().split())
                if not p_text:
                    continue
                # Skip media contact or footer disclaimers
                if p_text.startswith("Media Contact:") or p_text.startswith("Staff Contact:"):
                    continue
                paras.append(p_text)

            body_text = "\n\n".join(paras).strip()

            # Find PDF links inside body
            for a in body_elem.find_all("a", href=True):
                href = a["href"]
                anchor_text = a.get_text(strip=True)
                if self._is_candidate_legal_pdf(href, anchor_text):
                    full_pdf_url = urljoin(canonical_url, href)
                    candidate_pdf_links.append((anchor_text, full_pdf_url))

        # Check attachment blocks outside main body
        for att_container in soup.select(".field--name-field-pr-documents, .field--name-field-attachments, .field--name-field-media"):
            for a in att_container.find_all("a", href=True):
                href = a["href"]
                anchor_text = a.get_text(strip=True)
                if self._is_candidate_legal_pdf(href, anchor_text):
                    full_pdf_url = urljoin(canonical_url, href)
                    candidate_pdf_links.append((anchor_text, full_pdf_url))

        if not body_text:
            body_text = rss_description or title

        # 5. Classify Action & Extract Case Info
        action_type = self._classify_action_type(title, body_text)
        docket_number = self._extract_case_or_docket(body_text)
        legal_bases = self._extract_legal_bases(body_text)

        # 6. PDF Enrichment (if available and enabled)
        pdf_url: Optional[str] = None
        pdf_extracted = False
        pdf_pages_processed = 0
        pdf_chars = 0
        pdf_skip_reason: Optional[str] = None

        config = source.config or {}
        fetch_pdf_enabled = config.get("fetch_pdf", True)

        if candidate_pdf_links:
            self.metrics.doj_pdf_links_found += len(candidate_pdf_links)
            preferred_text, preferred_url = candidate_pdf_links[0]
            pdf_url = preferred_url

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
                    self.metrics.doj_pdfs_downloaded += 1
                    body_text += f"\n\n--- OFFICIAL LEGAL ATTACHMENT ({preferred_text.upper()}) ---\n\n" + pdf_res["text"]
                else:
                    self.metrics.doj_pdfs_skipped += 1
                    pdf_skip_reason = pdf_res.get("skip_reason", "extraction_failed")
            else:
                self.metrics.doj_pdfs_skipped += 1
                pdf_skip_reason = "pdf_fetching_disabled_by_config"
        else:
            pdf_skip_reason = "no_candidate_legal_pdf_found"

        # 7. Construct Identity & Metadata
        external_id = self._build_canonical_external_id(
            node_id=node_id,
            published_at=published_at,
            url=canonical_url,
        )

        raw_metadata: dict[str, Any] = {
            "agency": "DOJ",
            "division": "Antitrust Division",
            "component_id": 376,
            "guid": guid,
            "node_id": node_id,
            "official_date": published_at.isoformat(),
            "action_type": action_type,
            "docket_number": docket_number,
            "legal_bases": legal_bases,
            "pdf_url": pdf_url,
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
            excerpt=rss_description or (body_text[:280] + "..." if len(body_text) > 280 else body_text),
            published_at=published_at,
            language="en",
            content_type="press_release",
            raw_metadata=raw_metadata,
        )
