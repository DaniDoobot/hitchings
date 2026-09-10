"""HTML cleaning, date/author extraction, and structure normalization for Direct Web Sources (Bloque 9B)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Tuple
from bs4 import BeautifulSoup, Tag

from app.core.url_utils import domains_belong_to_same_site, normalize_url


# Tags that never contain editorial body content
STRIP_TAGS = {
    "script",
    "style",
    "noscript",
    "header",
    "footer",
    "nav",
    "aside",
    "form",
    "button",
    "iframe",
    "svg",
    "canvas",
}

# Substrings in class/id attributes that typically denote navigation, social or boilerplates
BOILERPLATE_PATTERNS = re.compile(
    r"(social|share|sharing|sharedaddy|related|sidebar|comment|comments|banner|cookie|newsletter|breadcrumbs?|widget-placeholder)",
    re.IGNORECASE,
)


def clean_editorial_html(container: Tag | None) -> str:
    """Extract clean, structured, plaintext editorial content from an HTML element.

    Normalizes whitespace, preserves paragraph and list structure, strips boilerplate.
    Does NOT translate, summarize, or truncate.
    """
    if not container:
        return ""

    # Clone or work with the element without mutating parent soup destructively
    soup = BeautifulSoup(str(container), "html.parser")
    target = soup.find() or soup

    # Decompose explicitly non-content tags
    for tag in target.find_all(list(STRIP_TAGS)):
        tag.decompose()

    # Decompose common boilerplate elements
    for el in target.find_all(True):
        attrs = getattr(el, "attrs", None)
        if not attrs:
            continue
        raw_classes = attrs.get("class", [])
        classes = " ".join(raw_classes) if isinstance(raw_classes, list) else str(raw_classes)
        el_id = str(attrs.get("id", ""))
        combined = f"{classes} {el_id}".strip()
        if BOILERPLATE_PATTERNS.search(combined):
            el.decompose()

    # Normalize paragraph and list spacing
    for heading in target.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        heading.insert_before("\n\n")
        heading.insert_after("\n\n")

    for p in target.find_all("p"):
        p.insert_before("\n\n")
        p.insert_after("\n\n")

    for li in target.find_all("li"):
        li.insert_before("\n- ")
        li.insert_after("\n")

    for br in target.find_all("br"):
        br.replace_with("\n")

    raw_text = target.get_text()

    # Normalize NBSP and exotic whitespaces
    text = raw_text.replace("\xa0", " ").replace("\u200b", "").replace("\r\n", "\n")

    # Collapse multiple spaces on the same line
    text = re.sub(r"[ \t]+", " ", text)

    # Collapse multiple blank lines to at most two
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def extract_structured_date(
    soup: BeautifulSoup,
    fallback_listing_date: Optional[datetime] = None,
) -> Tuple[Optional[datetime], str]:
    """Extract publication date following Section 18 strict hierarchy:
    1. JSON-LD datePublished
    2. meta article:published_time / date
    3. <time datetime>
    4. visible date regex
    5. fallback_listing_date (feed/sitemap)

    Returns tuple (parsed_datetime, source_label).
    """
    # 1. JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                graphs = item.get("@graph", [item]) if isinstance(item, dict) else []
                for node in graphs:
                    if isinstance(node, dict) and "datePublished" in node:
                        dt_str = str(node["datePublished"]).strip()
                        dt = _safe_parse_datetime(dt_str)
                        if dt:
                            return dt, "detail_json_ld"
        except Exception:
            continue

    # 2. Meta tags
    meta_props = [
        {"property": "article:published_time"},
        {"name": "article:published_time"},
        {"property": "og:published_time"},
        {"name": "date"},
        {"name": "dc.date"},
        {"name": "DC.date.issued"},
    ]
    for attrs in meta_props:
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            dt = _safe_parse_datetime(tag["content"])
            if dt:
                return dt, "detail_meta"

    # 3. <time datetime="...">
    for time_tag in soup.find_all("time"):
        dt_attr = time_tag.get("datetime")
        if dt_attr:
            dt = _safe_parse_datetime(dt_attr)
            if dt:
                return dt, "detail_time"

    # 4. Fallback from listing/feed
    if fallback_listing_date:
        if fallback_listing_date.tzinfo is None:
            fallback_listing_date = fallback_listing_date.replace(tzinfo=timezone.utc)
        return fallback_listing_date, "listing_feed"

    return None, "none"


def extract_structured_author(
    soup: BeautifulSoup,
    fallback_listing_author: Optional[str] = None,
    disallowed_publisher_names: Optional[list[str]] = None,
) -> Optional[str]:
    """Extract individual author following Section 17 (Publisher is NOT author).

    Rejects names that match publisher/institution/website names.
    """
    disallowed = {p.lower().strip() for p in (disallowed_publisher_names or [])}

    # 1. Check meta author tag
    meta_author = soup.find("meta", attrs={"name": "author"})
    if meta_author and meta_author.get("content"):
        candidate = meta_author["content"].strip()
        if candidate and candidate.lower() not in disallowed:
            return candidate

    # 2. Check JSON-LD author
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                graphs = item.get("@graph", [item]) if isinstance(item, dict) else []
                for node in graphs:
                    if isinstance(node, dict) and "author" in node:
                        auth_val = node["author"]
                        if isinstance(auth_val, dict) and auth_val.get("name"):
                            candidate = str(auth_val["name"]).strip()
                            if candidate and candidate.lower() not in disallowed:
                                return candidate
                        elif isinstance(auth_val, str):
                            candidate = auth_val.strip()
                            if candidate and candidate.lower() not in disallowed:
                                return candidate
        except Exception:
            continue

    # 3. Check fallback from listing (e.g. <dc:creator>)
    if fallback_listing_author:
        candidate = fallback_listing_author.strip()
        if candidate and candidate.lower() not in disallowed:
            return candidate

    return None


def extract_canonical_url(soup: BeautifulSoup, page_url: str) -> str:
    """Extract rel=canonical link tag and validate that it belongs to the same site (Section 22).

    If invalid or cross-domain, defaults to normalize_url(page_url).
    """
    norm_page = normalize_url(page_url)
    link = soup.find("link", rel="canonical")
    if not link or not link.get("href"):
        return norm_page

    href = link["href"].strip()
    norm_canonical = normalize_url(href)
    if not norm_canonical:
        return norm_page

    # Verify same-site domain
    if domains_belong_to_same_site(norm_canonical, norm_page):
        return norm_canonical

    return norm_page


def _safe_parse_datetime(dt_str: str | None) -> Optional[datetime]:
    """Parse date string into timezone-aware UTC datetime using standard library."""
    if not dt_str:
        return None
    raw = str(dt_str).strip()
    if not raw:
        return None

    # 1. Try RFC 2822 / 822 format (e.g. 'Wed, 15 Jul 2026 05:00:19 +0000')
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # 2. Try ISO 8601 (e.g. '2026-09-07T08:00:00.000Z' or '2026-07-31T15:26:19+00:00')
    try:
        iso_str = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # 3. Try standard date strings (both 4-digit and 2-digit year formats)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d/%m/%y", "%m/%d/%y"):
        try:
            dt = datetime.strptime(raw[:10], fmt).replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue

    return None
