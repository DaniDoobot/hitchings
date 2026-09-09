"""Base adapter interface for Direct Web Sources (Bloque 9B)."""

from __future__ import annotations

import abc
from typing import Optional
import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.url_utils import normalize_url
from app.models.source import Source
from app.providers.direct_web.html_cleaner import (
    clean_editorial_html,
    extract_canonical_url,
    extract_structured_author,
    extract_structured_date,
)
from app.providers.direct_web.models import DirectWebArticle, DiscoveredItem


class DirectWebExtractionError(Exception):
    """Raised when an item extraction fails in a controlled manner."""
    pass


class BaseWebSourceAdapter(abc.ABC):
    """Abstract base class for all direct reference web source adapters."""

    adapter_code: str = "base"
    default_language: str = "en"
    disallowed_publisher_names: list[str] = []

    @abc.abstractmethod
    def discover(
        self,
        source: Source,
        limit: int = 20,
        client: Optional[httpx.Client] = None,
    ) -> list[DiscoveredItem]:
        """Discover recent items for this source (via RSS, Atom, sitemap, or listing)."""
        pass

    def fetch_detail(
        self,
        item: DiscoveredItem,
        client: Optional[httpx.Client] = None,
    ) -> str:
        """Fetch raw HTML for an item with defensive byte limits and timeouts."""
        settings = get_settings()
        timeout = settings.DIRECT_WEB_TIMEOUT_SECONDS
        max_bytes = settings.DIRECT_WEB_MAX_RESPONSE_BYTES

        should_close = False
        if client is None:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            }
            client = httpx.Client(headers=headers, timeout=timeout, follow_redirects=True)
            should_close = True

        try:
            # Stream response to protect against oversized payloads (Section 20)
            with client.stream("GET", item.url) as response:
                if response.status_code != 200:
                    raise DirectWebExtractionError(
                        f"HTTP {response.status_code} fetching detail for {item.url}"
                    )

                content_len = response.headers.get("content-length")
                if content_len and int(content_len) > max_bytes:
                    raise DirectWebExtractionError(
                        f"Payload size {content_len} bytes exceeds limit of {max_bytes} bytes"
                    )

                body_chunks = []
                total_bytes = 0
                for chunk in response.iter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise DirectWebExtractionError(
                            f"Downloaded bytes exceeded safety limit of {max_bytes} bytes"
                        )
                    body_chunks.append(chunk)

                raw_bytes = b"".join(body_chunks)
                encoding = response.encoding or "utf-8"
                return raw_bytes.decode(encoding, errors="replace")
        except httpx.RequestError as exc:
            raise DirectWebExtractionError(f"Network error fetching {item.url}: {exc}") from exc
        finally:
            if should_close:
                client.close()

    @abc.abstractmethod
    def locate_editorial_container(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Locate the specific DOM container holding the article editorial text."""
        pass

    def parse_detail(
        self,
        raw_html: str,
        item: DiscoveredItem,
    ) -> DirectWebArticle:
        """Parse detail page into normalized DirectWebArticle."""
        settings = get_settings()
        max_chars = settings.DIRECT_WEB_MAX_CONTENT_CHARS

        soup = BeautifulSoup(raw_html, "html.parser")

        # 1. Canonical URL
        canonical_url = extract_canonical_url(soup, item.url)

        # 2. Structured Date (Hierarchy Section 18)
        pub_date, date_source = extract_structured_date(
            soup,
            fallback_listing_date=item.published_at,
        )

        # 3. Structured Author (Section 17)
        author = extract_structured_author(
            soup,
            fallback_listing_author=item.author,
            disallowed_publisher_names=self.disallowed_publisher_names,
        )

        # 4. Content extraction
        container = self.locate_editorial_container(soup)
        if not container:
            # Fallback to article or main
            container = soup.find("article") or soup.find("main")

        cleaned_text = clean_editorial_html(container) if container else ""

        # Enforce max content chars
        if len(cleaned_text) > max_chars:
            cleaned_text = cleaned_text[:max_chars]

        # Combine metadata
        raw_meta = dict(item.raw_metadata)
        raw_meta["published_at_source"] = date_source
        raw_meta["adapter_code"] = self.adapter_code
        raw_meta["original_url"] = item.url

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
