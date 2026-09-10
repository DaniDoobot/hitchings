"""Adapter for Geradin Partners Direct Web Source (Bloque 12A).

Follows the Direct Web architecture (Bloque 9B) for monitoring:
- Monthly EU Litigation Briefing
- Papers & Reports
- Substantive competition litigation and antitrust analysis
- Substantive newsletters (e.g. Platform Newsletter)

Applies deterministic filtering to exclude corporate HR, recruitments,
nominations, awards, rankings, and commercial events.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.url_utils import normalize_url
from app.models.source import Source
from app.providers.direct_web.base import BaseWebSourceAdapter, DirectWebExtractionError
from app.providers.direct_web.html_cleaner import (
    clean_editorial_html,
    extract_canonical_url,
    extract_structured_date,
    _safe_parse_datetime,
)
from app.providers.direct_web.models import DirectWebArticle, DiscoveredItem

logger = logging.getLogger(__name__)

# Categories to unconditionally exclude from ingestion
EXCLUDED_CATEGORIES = {"events"}

# Categories that are always substantive legal content
ALWAYS_INCLUDED_CATEGORIES = {
    "monthly eu litigation briefing",
    "papers & reports",
    "papers and reports",
    "newsletters",
}

# Regex pattern for corporate / HR / awards / directory rankings exclusions
CORPORATE_EXCLUSION_PATTERN = re.compile(
    r"\b("
    r"appoints?|appointed|appointment|appointments|"
    r"promotes?|promoted|promotion|promotions|"
    r"joins?|joined|"
    r"seeks?|hiring|recruitment|associate|internship|career|"
    r"nominat(ed|ion|ions)|"
    r"ranks?|ranked|ranking|rankings?|gcr 100|best lawyers|"
    r"recogni[sz]es?|recogni[sz]ed|recognition|"
    r"awards?|shortlisted|wins? .* award|"
    r"legal 500|chambers|juve|who'?s who legal|\d+ under \d+"
    r")\b",
    re.IGNORECASE,
)


class GeradinPartnersAdapter(BaseWebSourceAdapter):
    """Adapter for Geradin Partners News & Insights."""

    adapter_code = "geradin_partners"
    default_language = "en"
    disallowed_publisher_names = [
        "Geradin Partners",
        "Geradin",
        "Geradin Partners Ltd",
    ]

    DEFAULT_LISTING_URL = "https://www.geradinpartners.com/news/"

    def is_substantive_article(self, category: str, title: str) -> tuple[bool, str]:
        """Determine deterministically whether an article is substantive legal content.

        Returns (is_substantive, reason).
        """
        cat_lower = category.strip().lower()
        title_clean = title.strip()

        # 1. Check excluded categories
        if cat_lower in EXCLUDED_CATEGORIES:
            return False, f"excluded_category:{cat_lower}"

        # 2. Always included legal categories
        if any(inc in cat_lower for inc in ALWAYS_INCLUDED_CATEGORIES):
            return True, "substantive_category"

        # 3. For 'Announcements', 'News', or other categories: apply corporate regex filter
        match = CORPORATE_EXCLUSION_PATTERN.search(title_clean)
        if match:
            return False, f"corporate_exclusion:{match.group(0)}"

        return True, "substantive_news_or_announcement"

    def discover(
        self,
        source: Source,
        limit: int = 20,
        client: Optional[httpx.Client] = None,
    ) -> list[DiscoveredItem]:
        """Discover recent publications from Geradin Partners News & Insights page."""
        listing_url = (
            (source.config or {}).get("listing_url")
            or source.url
            or self.DEFAULT_LISTING_URL
        )

        should_close = False
        if client is None:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            }
            client = httpx.Client(headers=headers, timeout=15.0, follow_redirects=True)
            should_close = True

        discovered_items: list[DiscoveredItem] = []
        current_page_url = listing_url
        page_num = 1
        max_pages = 5

        try:
            while len(discovered_items) < limit and page_num <= max_pages:
                logger.info(
                    "Discovering Geradin Partners items from %s (page %d, limit=%d)...",
                    current_page_url,
                    page_num,
                    limit,
                )
                resp = client.get(current_page_url)
                if resp.status_code != 200:
                    if page_num == 1:
                        raise DirectWebExtractionError(
                            f"HTTP {resp.status_code} fetching Geradin Partners listing from {current_page_url}"
                        )
                    logger.warning(
                        "Pagination stopped: HTTP %d at %s", resp.status_code, current_page_url
                    )
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("li", class_="wp-block-post")
                if not cards:
                    logger.info("No more cards found on page %d", page_num)
                    break

                for card in cards:
                    if len(discovered_items) >= limit:
                        break

                    # Card link & title
                    card_a = card.find("a", class_="gp-news-post-card") or card.find("a", href=True)
                    if not card_a or not card_a.get("href"):
                        continue

                    raw_url = urljoin(current_page_url, card_a["href"])
                    url = normalize_url(raw_url)
                    if not url:
                        continue

                    title_el = card.find(["h2", "h1", "h3"], class_="post-title") or card.find(["h2", "h1", "h3"])
                    title = title_el.get_text(strip=True) if title_el else ""
                    if not title:
                        continue

                    # Category
                    cat_span = card.find("span", class_="post-category")
                    category = cat_span.get_text(strip=True) if cat_span else "News"

                    # Deterministic editorial filtering
                    is_substantive, filter_reason = self.is_substantive_article(category, title)
                    if not is_substantive:
                        logger.debug("Excluded Geradin item '%s' (%s)", title[:60], filter_reason)
                        continue

                    # Date
                    time_el = card.find("time", class_="post-date") or card.find("time")
                    published_at = None
                    if time_el and time_el.get_text(strip=True):
                        published_at = _safe_parse_datetime(time_el.get_text(strip=True))

                    lookback_days = (source.config or {}).get("lookback_days")
                    if lookback_days and published_at:
                        cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
                        if published_at < cutoff_date:
                            logger.debug("Excluded Geradin item '%s' (older than %d days: %s)", title[:60], lookback_days, published_at)
                            continue

                    # External ID
                    external_id = url

                    raw_meta = {
                        "listing_url": current_page_url,
                        "category": category,
                        "filter_reason": filter_reason,
                        "publisher": "Geradin Partners",
                    }

                    discovered_items.append(
                        DiscoveredItem(
                            url=url,
                            title=title,
                            external_id=external_id,
                            published_at=published_at,
                            author=None,  # Detailed author resolved on article page
                            excerpt=None,
                            raw_metadata=raw_meta,
                        )
                    )

                # Check next page link
                next_a = soup.find("a", class_="wp-block-query-pagination-next")
                if not next_a or not next_a.get("href"):
                    break

                current_page_url = urljoin(current_page_url, next_a["href"])
                page_num += 1

            return discovered_items

        except Exception as exc:
            if isinstance(exc, DirectWebExtractionError):
                raise
            raise DirectWebExtractionError(
                f"Error discovering Geradin Partners listing: {exc}"
            ) from exc
        finally:
            if should_close:
                client.close()

    def locate_editorial_container(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Locate article column inside article.post-content."""
        art = soup.find("article", class_="post-content") or soup.find("article")
        if art:
            col = art.find("div", class_="column")
            if col:
                return col
            return art
        return soup.find("div", class_="column") or soup.find("main")

    def extract_author_from_detail(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract individual lawyer name from Geradin team member widget.

        Corporate 'Geradin Partners' links pointing to /news/ or matching publisher are rejected.
        Individual lawyers linking to /team/ are returned.
        """
        # Look for team-member or person-meta container
        for member in soup.find_all(class_=lambda c: c and any(w in c for w in ["team-member", "person-meta"])):
            # Look for link pointing to /team/
            team_link = member.find("a", href=lambda h: h and "/team/" in h)
            if team_link:
                # Text or title attribute
                name = team_link.get_text(strip=True) or team_link.get("title", "")
                name = re.sub(r"^Learn more about\s+", "", name, flags=re.IGNORECASE).strip()
                if name and name.lower() not in {p.lower() for p in self.disallowed_publisher_names}:
                    return name

            # Check if name is in person-name class
            p_name = member.find(class_=lambda c: c and "person-name" in c)
            if p_name:
                cand = p_name.get_text(strip=True)
                if cand and cand.lower() not in {p.lower() for p in self.disallowed_publisher_names}:
                    return cand

        return None

    def extract_pdf_url_from_detail(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Extract official attached PDF document link if present in article."""
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().endswith(".pdf") or ("/uploads/" in href.lower() and ".pdf" in href.lower()):
                return urljoin(base_url, href)
        return None

    def parse_detail(
        self,
        raw_html: str,
        item: DiscoveredItem,
    ) -> DirectWebArticle:
        """Parse detail page into normalized DirectWebArticle with author, date, PDF and clean body."""
        settings = get_settings()
        max_chars = settings.DIRECT_WEB_MAX_CONTENT_CHARS

        soup = BeautifulSoup(raw_html, "html.parser")

        # 1. Canonical URL
        canonical_url = extract_canonical_url(soup, item.url)

        # 2. Publication Date
        # Prefer detail <time class="post-date">, fallback to item.published_at
        pub_date, date_source = extract_structured_date(
            soup,
            fallback_listing_date=item.published_at,
        )
        if not pub_date:
            detail_time = soup.find("time", class_="post-date") or soup.find("time")
            if detail_time and detail_time.get_text(strip=True):
                pub_date = _safe_parse_datetime(detail_time.get_text(strip=True))
                if pub_date:
                    date_source = "detail_time_post_date"

        # 3. Author Extraction
        author = self.extract_author_from_detail(soup)

        # 4. PDF Link Detection
        pdf_url = self.extract_pdf_url_from_detail(soup, item.url)

        # 5. Editorial Content Extraction
        container = self.locate_editorial_container(soup)
        cleaned_text = ""
        if container:
            # Clone container to avoid mutating soup destructively
            col_copy = BeautifulSoup(str(container), "html.parser")

            # Decompose post-meta, author bio, social sharing, and filters from editorial text
            for unwanted in col_copy.find_all(
                class_=lambda c: c
                and any(
                    w in c
                    for w in [
                        "post-meta",
                        "team-member",
                        "person-meta",
                        "featured-image",
                        "social-nav-links",
                        "rudr-taxonomy-filter",
                    ]
                )
            ):
                unwanted.decompose()

            cleaned_text = clean_editorial_html(col_copy)

        if len(cleaned_text) > max_chars:
            cleaned_text = cleaned_text[:max_chars]

        # Combine metadata
        raw_meta = dict(item.raw_metadata)
        raw_meta["published_at_source"] = date_source
        raw_meta["adapter_code"] = self.adapter_code
        raw_meta["original_url"] = item.url
        if pdf_url:
            raw_meta["pdf_url"] = pdf_url
            raw_meta["has_pdf"] = True
        else:
            raw_meta["has_pdf"] = False

        return DirectWebArticle(
            external_id=item.external_id,
            title=item.title,
            url=item.url,
            canonical_url=canonical_url,
            content=cleaned_text,
            published_at=pub_date,
            author=author,
            excerpt=item.excerpt,
            language=self.default_language,
            raw_metadata=raw_meta,
        )
