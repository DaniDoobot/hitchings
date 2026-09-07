"""HTML extractor for Competition Appeal Tribunal (CAT) judgments and official summaries."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.models.source import Source
from app.providers.base import RawEntryData, ProviderError

logger = logging.getLogger(__name__)

CAT_BASE_URL = "https://www.catribunal.org.uk/judgments"
CAT_DOMAIN = "https://www.catribunal.org.uk"
DEFAULT_CONCURRENCY = 4


def slugify_citation(citation: str) -> str:
    """Create a URL-safe anchor slug from a neutral citation like '[2026] CAT 71'."""
    clean = re.sub(r"[^\w\s-]", "", citation).strip().lower()
    return re.sub(r"[-\s]+", "-", clean)


def parse_case_link_text(text: str) -> tuple[str, str]:
    """Parse case link text into (case_number, case_name).

    Example:
        '1407/7/7/21 (T) Commercial and Inter-Dealer Telecommunications Limited v Ofcom'
        -> ('1407/7/7/21 (T)', 'Commercial and Inter-Dealer Telecommunications Limited v Ofcom')
    """
    clean_text = text.strip()
    match = re.match(r"^([0-9/]+(?:\s*\([A-Za-z0-9]+\))?)\s+(.*)$", clean_text)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", clean_text


class CompetitionAppealTribunalExtractor:
    """HTML extractor for Competition Appeal Tribunal (CAT) judgments and decisions."""

    def __init__(self, concurrency: int = DEFAULT_CONCURRENCY) -> None:
        self.concurrency = concurrency

    async def extract(self, client: httpx.AsyncClient, source: Source) -> list[RawEntryData]:
        """Fetch judgments listing pages and official summaries from CAT website."""
        base_url = source.url or CAT_BASE_URL
        limit = 20
        if source.config and isinstance(source.config, dict):
            limit = source.config.get("initial_fetch_limit", limit)

        logger.info("Extracting CAT judgments from '%s' (limit=%d)...", base_url, limit)

        # 1. Fetch listing items page by page until limit is reached
        items_to_fetch: list[dict] = []
        page = 0
        max_pages = 10

        while len(items_to_fetch) < limit and page < max_pages:
            page_url = f"{base_url}?page={page}" if page > 0 else base_url
            try:
                resp = await client.get(page_url)
            except httpx.RequestError as exc:
                logger.error("HTTP error requesting CAT listing page %s: %s", page_url, exc)
                if page == 0:
                    raise ProviderError(f"Network error fetching CAT listing: {exc}") from exc
                break

            if resp.status_code != 200:
                logger.warning("CAT listing page %s returned status %d", page_url, resp.status_code)
                if page == 0:
                    raise ProviderError(f"CAT website returned HTTP status {resp.status_code}")
                break

            page_items = self.parse_listing_page(resp.text, page_url)
            if not page_items:
                logger.info("No more items found on CAT page %d", page)
                break

            for it in page_items:
                items_to_fetch.append(it)
                if len(items_to_fetch) >= limit:
                    break

            page += 1

        logger.info("Found %d CAT listing items across %d pages", len(items_to_fetch), page)

        # 2. Fetch full official summary content for items with summary link
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _enrich_item(item_data: dict) -> RawEntryData:
            summary_url = item_data.get("summary_url")
            body_content: Optional[str] = None
            excerpt: Optional[str] = None

            if summary_url:
                async with semaphore:
                    try:
                        sum_resp = await client.get(summary_url)
                        if sum_resp.status_code == 200:
                            body_content, excerpt = self.parse_judgment_summary(sum_resp.text)
                    except Exception as exc:
                        logger.warning("Could not fetch CAT judgment summary from %s: %s", summary_url, exc)

            entry_url = item_data["entry_url"]
            external_id = item_data["external_id"]

            return RawEntryData(
                url=entry_url,
                title=item_data.get("title"),
                content=body_content,
                excerpt=excerpt,
                published_at=item_data.get("published_at"),
                external_id=external_id,
                language="en",
                content_type="judicial_decision",
                raw_metadata=item_data.get("raw_metadata", {}),
            )

        tasks = [_enrich_item(it) for it in items_to_fetch]
        raw_entries = await asyncio.gather(*tasks)
        return list(raw_entries)

    def parse_listing_page(self, html: str, page_url: str) -> list[dict]:
        """Parse Drupal views-row items from the CAT judgments listing page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all(class_=lambda c: c and "views-row" in c)
        parsed_items: list[dict] = []

        for row in rows:
            # 1. Publication date from <time datetime="...">
            published_at: Optional[datetime] = None
            time_el = row.find("time")
            if time_el and time_el.get("datetime"):
                try:
                    dt_str = time_el["datetime"].strip()
                    dt = datetime.fromisoformat(dt_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    published_at = dt.astimezone(timezone.utc)
                except Exception as exc:
                    logger.debug("Failed to parse CAT datetime '%s': %s", time_el.get("datetime"), exc)
            elif time_el and time_el.get_text(strip=True):
                try:
                    dt = datetime.strptime(time_el.get_text(strip=True), "%d/%m/%Y").replace(tzinfo=timezone.utc)
                    published_at = dt
                except Exception as exc:
                    logger.debug("Failed to parse DD/MM/YYYY date: %s", exc)

            # 2. Neutral citation: sibling spans after time element's parent span
            citations: list[str] = []
            time_parent = time_el.parent if time_el else None
            if time_parent:
                for sib in time_parent.find_next_siblings():
                    if sib.name == "span":
                        if "link-summary" in sib.get("class", []):
                            break
                        txt = sib.get_text(strip=True)
                        if txt:
                            citations.append(txt)
            neutral_citation = " ".join(citations).strip() or None

            # 3. Decision Type & PDF URL
            dec_heading = row.find(class_=lambda c: c and ("h5" in c or "margin-compact" in c))
            dec_a = dec_heading.find("a") if dec_heading else None
            if not dec_a:
                dec_a = row.select_one("h2 a, .file a")

            decision_type = dec_a.get_text(strip=True) if dec_a else "Judgment"
            raw_pdf_href = dec_a.get("href", "").strip() if dec_a else ""
            pdf_url = urljoin(page_url, raw_pdf_href) if raw_pdf_href else None

            # 4. Cases: multiple case links can exist in .readmore-group
            case_numbers: list[str] = []
            case_names: list[str] = []
            case_urls: list[str] = []

            case_group = row.find(class_=lambda c: c and "readmore-group" in c)
            if case_group:
                case_links = case_group.find_all("a")
                for ca in case_links:
                    txt = ca.get_text(strip=True)
                    if not txt or "see all" in txt.lower():
                        continue
                    c_num, c_name = parse_case_link_text(txt)
                    if c_num:
                        case_numbers.append(c_num)
                    case_names.append(c_name)
                    c_href = ca.get("href", "").strip()
                    if c_href:
                        case_urls.append(urljoin(page_url, c_href))

            # 5. Summary link (landing page)
            summary_url: Optional[str] = None
            sum_span = row.find(class_=lambda c: c and "link-summary" in c)
            if sum_span:
                sum_a = sum_span.find("a")
                if sum_a and sum_a.get("href"):
                    summary_url = urljoin(page_url, sum_a["href"].strip())

            has_summary = summary_url is not None

            # 6. Determine canonical Entry URL and external_id
            primary_case_name = case_names[0] if case_names else "Competition Appeal Tribunal"
            first_case_url = case_urls[0] if case_urls else None

            if summary_url:
                entry_url = summary_url
            elif first_case_url and neutral_citation:
                anchor = slugify_citation(neutral_citation)
                entry_url = f"{first_case_url}#{anchor}"
            elif first_case_url:
                entry_url = first_case_url
            elif pdf_url:
                entry_url = pdf_url
            else:
                entry_url = f"{page_url}#{len(parsed_items)}"

            # External ID: prioritize unique neutral citation, then bounded URL
            if neutral_citation:
                external_id = neutral_citation[:255]
            elif summary_url:
                external_id = summary_url[:255]
            else:
                external_id = entry_url[:255]

            # 7. Title formatting
            if neutral_citation:
                title = f"{neutral_citation} | {primary_case_name} - {decision_type}"
            else:
                title = f"{primary_case_name} - {decision_type}"

            raw_metadata = {
                "case_numbers": case_numbers,
                "case_names": case_names,
                "case_urls": case_urls,
                "decision_type": decision_type,
                "neutral_citation": neutral_citation,
                "judgment_pdf_url": pdf_url,
                "has_summary": has_summary,
                "summary_url": summary_url,
                "source_section": "Judgments",
                "publication_date_source": "listing_time_tag",
            }

            parsed_items.append({
                "title": title,
                "entry_url": entry_url,
                "external_id": external_id,
                "summary_url": summary_url,
                "published_at": published_at,
                "raw_metadata": raw_metadata,
            })

        return parsed_items

    def parse_judgment_summary(self, html: str) -> tuple[Optional[str], Optional[str]]:
        """Extract official summary text and excerpt from CAT judgment detail page."""
        soup = BeautifulSoup(html, "html.parser")

        heading = soup.find(lambda tag: tag.name in ["h2", "h3", "h4"] and "summary" in tag.get_text().lower())
        content_container = None

        if heading:
            content_container = heading.find_next_sibling()
            if not content_container:
                content_container = heading.parent.find(class_="above-related")

        if not content_container:
            content_container = soup.find(class_="above-related")

        if not content_container:
            content_container = soup.find("main") or soup.find("article")

        if not content_container:
            return None, None

        content = content_container.get_text(separator="\n\n", strip=True) or None

        first_p = content_container.find("p")
        if first_p:
            p_text = first_p.get_text(strip=True)
            excerpt = p_text[:400] if len(p_text) > 400 else p_text
        elif content:
            excerpt = content[:300]
        else:
            excerpt = None

        return content, excerpt
