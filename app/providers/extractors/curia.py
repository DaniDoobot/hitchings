"""Official case law extractor for the Court of Justice of the European Union (CJEU / CURIA).

Fetches individual judgments, orders, and Advocate General opinions from the official
InfoCuria search API and downloads full clean HTML document content via the official
InfoCuria blob storage endpoint.

Rule: 1 judicial document = 1 Entry.
Canonical ID: ECLI (e.g. ECLI:EU:C:2026:702).
Content Type: eu_case_law.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.models.source import Source
from app.providers.base import RawEntryData, ProviderError

logger = logging.getLogger(__name__)

INFOCURIA_SEARCH_URL = "https://infocuriaws.curia.europa.eu/elastic-connector/search"
INFOCURIA_BLOB_BASE = "https://infocuriaws.curia.europa.eu/blob/download-file-html"
INFOCURIA_WEB_SEARCH_URL = "https://infocuria.curia.europa.eu/tabs/jurisprudence"
CURIA_CLASSIC_BASE = "https://curia.europa.eu/juris/liste.jsf"
DEFAULT_CONCURRENCY = 4

DOC_TYPE_MAP: dict[str, str] = {
    "CONCL": "Opinion of the Advocate General",
    "ARRET": "Judgment",
    "ORDONN": "Order",
    "AVIS": "Opinion",
    "PRISE_POS": "View",
}

JURISDICTION_MAP: dict[str, str] = {
    "C": "Court of Justice",
    "T": "General Court",
    "F": "Civil Service Tribunal",
}


def parse_curia_date(date_str: Optional[str]) -> Optional[datetime]:
    """Parse 'YYYY-MM-DD' into a timezone-aware UTC datetime."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except Exception as exc:
        logger.warning("Could not parse CURIA doc date '%s': %s", date_str, exc)
        return None


def extract_year_from_procedure(procedure_id: str, published_id: str) -> str:
    """Extract 4-digit year from procedure ID (e.g. 'C/0380/25/00000000RP/01/P/01' -> '2025')
    or published ID (e.g. 'C-380/25' -> '2025').
    """
    parts = procedure_id.split("/")
    if len(parts) >= 3 and parts[2].isdigit():
        yy = int(parts[2])
        return str(2000 + yy if yy < 50 else 1900 + yy)

    m = re.search(r"/(\d{2,4})", published_id)
    if m:
        yy_str = m.group(1)
        if len(yy_str) == 4:
            return yy_str
        yy = int(yy_str)
        return str(2000 + yy if yy < 50 else 1900 + yy)

    return str(datetime.now(timezone.utc).year)


def select_best_language(lang_variants: list[dict]) -> str:
    """Select preferred language code for document download.
    Preference: EN -> FR -> first available.
    """
    if not lang_variants:
        return "EN"

    codes = [str(v.get("docLang", "")).upper() for v in lang_variants if v.get("docLang")]
    if "EN" in codes:
        return "EN"
    if "FR" in codes:
        return "FR"
    return codes[0] if codes else "EN"


def clean_document_html(raw_html: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Clean official document HTML, convert to clean plain text, and extract excerpt and usual case name.

    Returns:
        (clean_text, excerpt, usual_name)
    """
    if not raw_html or not raw_html.strip():
        return None, None, None

    soup = BeautifulSoup(raw_html, "html.parser")

    for el in soup.find_all(["script", "style", "link", "meta", "nav", "header", "footer"]):
        el.decompose()

    text_head = soup.get_text()[:1500]
    usual_name = None
    bracket_match = re.search(r"\[([A-Za-z\s'-]{2,35})\]", text_head)
    if bracket_match:
        candidate = bracket_match.group(1).strip()
        lower_cand = candidate.lower()
        if lower_cand not in {"1", "i", "ii", "provisional text", "texte provisoire", "curia"} and not lower_cand.startswith("demande"):
            usual_name = candidate

    body = soup.find("body") or soup

    for br in body.find_all("br"):
        br.replace_with("\n")

    block_tags = ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "li", "blockquote"]
    lines: list[str] = []
    paragraphs_for_excerpt: list[str] = []

    for tag in body.find_all(block_tags):
        # Only take leaf block elements to avoid duplicating nested text
        if not any(child.name in block_tags for child in tag.find_all(block_tags)):
            clean = re.sub(r"[ \t\xa0]+", " ", tag.get_text()).strip()
            if clean:
                lines.append(clean)
                if tag.name == "p" and len(clean) > 35:
                    paragraphs_for_excerpt.append(clean)

    if not lines:
        raw = body.get_text()
        clean_text = re.sub(r"\n\s*\n+", "\n\n", raw).strip()
    else:
        clean_text = "\n\n".join(lines).strip()

    if not clean_text:
        return None, None, usual_name

    # Extract substantive excerpt from official text without inventing artificial phrases
    clean_paragraphs = [
        p for p in paragraphs_for_excerpt
        if not p.lower().startswith("provisional text")
        and not p.lower().startswith("édition provisoire")
        and not p.lower().startswith("delivered on")
        and not p.lower().startswith("présentées le")
    ]

    excerpt = None
    if clean_paragraphs:
        for p in clean_paragraphs[:5]:
            if p.startswith("(") and len(p) > 50:
                excerpt = p[:400].strip()
                if len(p) > 400:
                    excerpt += "..."
                break
        if not excerpt:
            first_p = clean_paragraphs[0]
            excerpt = first_p[:400].strip()
            if len(first_p) > 400:
                excerpt += "..."

    return clean_text, excerpt, usual_name


class CuriaCaseLawExtractor:
    """Extractor for official judgments, orders, and AG opinions from InfoCuria."""

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.concurrency = concurrency

    async def extract(self, client: httpx.AsyncClient, source: Source) -> list[RawEntryData]:
        """Fetch recent judicial documents from InfoCuria search API and enrich with full HTML content."""
        limit = 20
        if source.config and isinstance(source.config, dict):
            limit = source.config.get("initial_fetch_limit", limit)

        logger.info("Extracting CURIA case law (limit=%d)...", limit)

        payload = {
            "multiSearchTerms": [],
            "searchTerm": "",
            "ecli": "",
            "publishedId": "",
            "usualName": "",
            "logicDocId": "",
            "repJurExpand": False,
            "pagination": {"pageNumber": 0, "pageSize": limit},
            "sortTermList": [{"sortTerm": "DOC_DATE", "sortDirection": "DESC"}],
            "filtersValue": [],
            "language": "EN",
            "isSearchExact": False,
            "searchSources": ["document", "metadata"],
            "tabName": "jurisprudence",
            "isAllTabsRequest": False,
        }

        search_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            resp = await client.post(INFOCURIA_SEARCH_URL, json=payload, headers=search_headers)
        except httpx.RequestError as exc:
            logger.error("HTTP error connecting to InfoCuria search API: %s", exc)
            raise ProviderError(f"Network error contacting InfoCuria search API: {exc}") from exc

        if resp.status_code != 200:
            logger.error("InfoCuria search returned HTTP status %d: %s", resp.status_code, resp.text[:200])
            raise ProviderError(f"InfoCuria search API returned HTTP {resp.status_code}")

        try:
            data = resp.json()
        except Exception as exc:
            logger.error("Failed to parse JSON from InfoCuria: %s", exc)
            raise ProviderError(f"Invalid JSON response from InfoCuria: {exc}") from exc

        hits = data.get("searchHits", [])
        if not hits:
            logger.warning("No search hits returned by InfoCuria search API")
            return []

        logger.info("Retrieved %d judicial document hits from InfoCuria", len(hits))

        semaphore = asyncio.Semaphore(self.concurrency)

        async def _enrich_hit(hit_item: dict) -> Optional[RawEntryData]:
            content_dict = hit_item.get("content", {})
            ecli = content_dict.get("ecli")
            if not ecli:
                logger.warning("Skipping hit without ECLI: %s", content_dict.get("id"))
                return None

            published_id = content_dict.get("idPublished") or ""
            doc_type_code = content_dict.get("docTypeCode") or ""
            raw_doc_type = content_dict.get("docType") or "Judicial Decision"
            doc_type_en = DOC_TYPE_MAP.get(doc_type_code, raw_doc_type)

            doc_date_str = content_dict.get("docDate")
            published_at = parse_curia_date(doc_date_str)

            jur_code = content_dict.get("affairJurisdictionCode") or "C"
            court_name = content_dict.get("affairJurisdiction") or JURISDICTION_MAP.get(jur_code, "Court of Justice")
            procedure_id = content_dict.get("idProcedure") or ""
            logic_doc_id = (content_dict.get("logicDocId") or "").replace("id_", "")
            doc_no_part = str(content_dict.get("docNoPart", 1))
            celex = content_dict.get("celex")

            year_str = extract_year_from_procedure(procedure_id, published_id)
            proc_underscores = procedure_id.replace("/", "_")

            lang_variants = content_dict.get("groupByLogicalId", [])
            selected_lang = select_best_language(lang_variants)

            file_name = f"{logic_doc_id}-{selected_lang}-{doc_no_part}.html"
            blob_url = (
                f"{INFOCURIA_BLOB_BASE}/{jur_code}/{year_str}/{proc_underscores}/{file_name}"
                if logic_doc_id and procedure_id
                else None
            )

            canonical_web_url = f"{INFOCURIA_WEB_SEARCH_URL}?lang=en&ecli={ecli}"
            classic_url = f"{CURIA_CLASSIC_BASE}?num={published_id}" if published_id else None
            eurlex_url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}" if celex else None

            body_content: Optional[str] = None
            excerpt: Optional[str] = None
            usual_name: Optional[str] = None

            if blob_url:
                async with semaphore:
                    try:
                        blob_resp = await client.get(blob_url, headers={"Accept": "text/html, */*"})
                        if blob_resp.status_code == 200 and len(blob_resp.text.strip()) > 100:
                            body_content, excerpt, usual_name = clean_document_html(blob_resp.text)
                        else:
                            logger.warning(
                                "Failed downloading HTML for %s (%s): HTTP %d",
                                ecli, blob_url, blob_resp.status_code
                            )
                    except Exception as exc:
                        logger.warning("Error fetching document blob for %s: %s", ecli, exc)

            # If blob was unavailable, DO NOT fabricate content or excerpt (preserve provenance)
            if not body_content:
                body_content = None
                excerpt = None

            if usual_name:
                title = f"Case {published_id} [{usual_name}] | {doc_type_en}"
            elif published_id:
                title = f"Case {published_id} | {doc_type_en}"
            else:
                title = f"{doc_type_en} ({ecli})"

            has_text = bool(body_content and len(body_content.strip()) > 0)

            raw_metadata = {
                "ecli": ecli,
                "case_number": published_id,
                "document_type": doc_type_en,
                "document_type_code": doc_type_code,
                "court": court_name,
                "court_code": jur_code,
                "celex": celex,
                "document_date": doc_date_str,
                "procedure_id": procedure_id,
                "logic_doc_id": logic_doc_id,
                "infocuria_url": canonical_web_url,
                "curia_classic_url": classic_url,
                "eurlex_url": eurlex_url,
                "blob_url": blob_url,
                "language": selected_lang.lower(),
                "usual_name": usual_name,
                "content_source": "infocuria_html" if has_text else None,
                "content_format": "text/plain" if has_text else None,
                "full_text_available": has_text,
                "has_html_body": has_text,
            }

            return RawEntryData(
                url=canonical_web_url,
                title=title,
                content=body_content,
                excerpt=excerpt,
                author=court_name,
                published_at=published_at,
                external_id=ecli,
                language=selected_lang.lower(),
                content_type="eu_case_law",
                raw_metadata=raw_metadata,
            )

        tasks = [_enrich_hit(h) for h in hits[:limit]]
        results = await asyncio.gather(*tasks)

        entries = [r for r in results if r is not None]
        logger.info("Successfully processed %d CURIA raw entries", len(entries))
        return entries
