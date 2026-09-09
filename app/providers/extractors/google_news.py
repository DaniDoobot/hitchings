"""Google News RSS feed parser and item extractor (Bloque 9A)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import logging
import re
from typing import Optional
import xml.etree.ElementTree as ET

from app.core.url_utils import normalize_url

logger = logging.getLogger(__name__)

# Regex to strip HTML tags from snippet/description
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class GoogleNewsItem:
    """Structured representation of a news item extracted from Google News RSS.
    
    URL Semantics:
    - google_news_url: The Google News redirect link to the article.
    - canonical_url: Normalized google_news_url used for identity tracking.
    - publisher_url: The publisher root/homepage domain link from RSS `<source url="...">`.
      NOTE: This is NOT the individual article URL, but the publisher's website root/domain.
    """
    title: str
    google_news_url: str
    canonical_url: str
    guid: Optional[str]
    published_at: Optional[datetime]
    publisher: Optional[str]
    publisher_url: Optional[str]
    excerpt: Optional[str]
    language: str

    @property
    def publisher_domain(self) -> str:
        """Return clean, normalized publisher domain."""
        from app.core.url_utils import extract_publisher_domain
        return extract_publisher_domain(self.publisher_url or self.publisher)


def clean_html_text(raw_html: str | None) -> str:
    """Strip HTML tags and unescape entities, normalizing whitespace."""
    if not raw_html:
        return ""
    text = _HTML_TAG_RE.sub(" ", raw_html)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def clean_article_title(title: str | None, publisher: str | None) -> str:
    """Clean title by stripping Google News publisher suffix (e.g. ' - Publisher') if present."""
    if not title:
        return ""
    cleaned = html.unescape(title).strip()
    if publisher:
        publisher_clean = publisher.strip()
        suffix = f" - {publisher_clean}"
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)].strip()
    # Also handle generic trailing ' - ...' if it matches standard Google News pattern
    return cleaned


def parse_rfc822_date(date_str: str | None) -> Optional[datetime]:
    """Parse RFC 822 / 2822 date string to timezone-aware UTC datetime."""
    if not date_str:
        return None
    try:
        dt = parsedate_to_datetime(date_str.strip())
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as exc:
        logger.debug("Failed to parse pubDate '%s': %s", date_str, exc)
        return None


def parse_google_news_rss(xml_content: str, language: str = "es") -> list[GoogleNewsItem]:
    """Parse Google News RSS XML feed into structured GoogleNewsItem records.
    
    Robust against malformed feeds, missing fields, or empty channels.
    """
    if not xml_content or not xml_content.strip():
        return []

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as err:
        logger.warning("XML parse error on Google News feed: %s", err)
        return []

    channel = root.find("channel")
    if channel is None:
        # Fallback if root itself is channel
        channel = root

    items: list[GoogleNewsItem] = []
    for item_el in channel.findall("item"):
        raw_title = item_el.findtext("title")
        raw_link = item_el.findtext("link")
        guid = item_el.findtext("guid")
        pub_date_str = item_el.findtext("pubDate")
        raw_desc = item_el.findtext("description")

        source_el = item_el.find("source")
        publisher = source_el.text.strip() if source_el is not None and source_el.text else None
        publisher_url = source_el.get("url") if source_el is not None else None

        if not raw_title and not raw_link:
            continue

        title = clean_article_title(raw_title, publisher)
        link = (raw_link or "").strip()
        canonical_link = normalize_url(link)
        published_at = parse_rfc822_date(pub_date_str)
        excerpt = clean_html_text(raw_desc) or None

        items.append(
            GoogleNewsItem(
                title=title,
                google_news_url=link,
                canonical_url=canonical_link,
                guid=guid.strip() if guid else None,
                published_at=published_at,
                publisher=publisher,
                publisher_url=normalize_url(publisher_url) if publisher_url else None,
                excerpt=excerpt,
                language=language,
            )
        )

    return items
