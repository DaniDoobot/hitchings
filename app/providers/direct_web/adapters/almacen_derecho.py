"""Adapter for Almacén de Derecho - Competencia (Bloque 9B)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional
import httpx
from bs4 import BeautifulSoup

from app.core.url_utils import normalize_url
from app.models.source import Source
from app.providers.direct_web.base import BaseWebSourceAdapter, DirectWebExtractionError
from app.providers.direct_web.html_cleaner import _safe_parse_datetime
from app.providers.direct_web.models import DiscoveredItem


class AlmacenDerechoAdapter(BaseWebSourceAdapter):
    """Adapter for Almacén de Derecho (Competencia section)."""

    adapter_code = "almacen_derecho"
    default_language = "es"
    disallowed_publisher_names = [
        "Almacén de Derecho",
        "Almacen de Derecho",
    ]

    DEFAULT_FEED_URL = "https://almacendederecho.org/category/competencia/feed"

    def discover(
        self,
        source: Source,
        limit: int = 20,
        client: Optional[httpx.Client] = None,
    ) -> list[DiscoveredItem]:
        feed_url = (source.config or {}).get("feed_url") or self.DEFAULT_FEED_URL
        should_close = False
        if client is None:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            client = httpx.Client(headers=headers, timeout=15.0, follow_redirects=True)
            should_close = True

        try:
            resp = client.get(feed_url)
            if resp.status_code != 200:
                raise DirectWebExtractionError(
                    f"HTTP {resp.status_code} fetching Almacen feed from {feed_url}"
                )
            root = ET.fromstring(resp.content)
            channel = root.find("channel")
            if channel is None:
                return []

            items: list[DiscoveredItem] = []
            for it in channel.findall("item"):
                if len(items) >= limit:
                    break

                title_el = it.find("title")
                link_el = it.find("link")
                guid_el = it.find("guid")
                pub_date_el = it.find("pubDate")
                creator_el = it.find("{http://purl.org/dc/elements/1.1/}creator")
                desc_el = it.find("description")

                title = title_el.text.strip() if title_el is not None and title_el.text else ""
                raw_url = link_el.text.strip() if link_el is not None and link_el.text else ""
                url = normalize_url(raw_url)
                if not title or not url:
                    continue

                guid = guid_el.text.strip() if guid_el is not None and guid_el.text else url
                author = creator_el.text.strip() if creator_el is not None and creator_el.text else None
                excerpt = desc_el.text.strip() if desc_el is not None and desc_el.text else None

                published_at = None
                if pub_date_el is not None and pub_date_el.text:
                    published_at = _safe_parse_datetime(pub_date_el.text)

                items.append(
                    DiscoveredItem(
                        url=url,
                        title=title,
                        external_id=guid,
                        published_at=published_at,
                        author=author,
                        excerpt=excerpt,
                        raw_metadata={
                            "feed_url": feed_url,
                            "guid": guid,
                            "publisher": "Almacén de Derecho",
                        },
                    )
                )

            return items
        except Exception as exc:
            if isinstance(exc, DirectWebExtractionError):
                raise
            raise DirectWebExtractionError(f"Error parsing Almacen feed: {exc}") from exc
        finally:
            if should_close:
                client.close()

    def locate_editorial_container(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """Almacén de Derecho places article content inside div.entry-content."""
        return soup.find("div", class_="entry-content") or soup.find("article") or soup.find("main")
