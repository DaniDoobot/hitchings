"""Unit tests for Google News RSS parser and URL normalization (Bloque 9A)."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from app.core.url_utils import normalize_url
from app.providers.extractors.google_news import (
    clean_article_title,
    clean_html_text,
    parse_google_news_rss,
    parse_rfc822_date,
)

SAMPLE_GOOGLE_NEWS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>CNMC competencia - Google News</title>
    <link>https://news.google.com/rss/search?q=CNMC+competencia</link>
    <item>
      <title>La cúpula de la CNMC tiene solo dos consejeros especializados en energía - The Objective</title>
      <link>https://news.google.com/rss/articles/CBMipgFBVV95cUxOMDA2VndUWTRZemJX?oc=5&amp;utm_source=feed&amp;utm_medium=rss</link>
      <guid isPermaLink="false">CBMipgFBVV95cUxOMDA2VndUWTRZemJX</guid>
      <pubDate>Wed, 09 Sep 2026 03:25:00 GMT</pubDate>
      <description>&lt;a href="https://news.google.com/rss/articles/CBMipgFB"&gt;La cúpula de la CNMC&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;The Objective&lt;/font&gt;</description>
      <source url="https://theobjective.com">The Objective</source>
    </item>
    <item>
      <title>Ganuza abre su mandato en la CNMC &quot;garantizando independencia&quot; - Cinco Días</title>
      <link>https://news.google.com/rss/articles/CBMipAFBVV95cUxQTmhx?oc=5</link>
      <guid isPermaLink="false">CBMipAFBVV95cUxQTmhx</guid>
      <pubDate>Wed, 09 Sep 2026 12:45:08 +0200</pubDate>
      <description>Declaraciones del nuevo presidente de la autoridad de competencia.</description>
      <source url="https://cincodias.elpais.com">Cinco Días</source>
    </item>
    <item>
      <title>Investigación por cártel en el sector farmacéutico</title>
      <link>https://news.google.com/rss/articles/CBMi1wFBVV95cUxNQi1u</link>
      <guid isPermaLink="false">CBMi1wFBVV95cUxNQi1u</guid>
      <!-- Missing pubDate and missing source -->
      <description>Breve resumen sin fuente explícita.</description>
    </item>
  </channel>
</rss>
"""


def test_url_normalization():
    """normalize_url strips marketing/tracking parameters and fragments while preserving functional params."""
    # Strips utm params and oc parameter
    raw = "https://example.com/noticia/?utm_source=twitter&utm_medium=social&oc=5&id=123#comentarios"
    expected = "https://example.com/noticia?id=123"
    assert normalize_url(raw) == expected

    # Lowercase domain and preserve query order
    raw2 = "HTTPS://WWW.Example.COM/path/?b=2&a=1&fbclid=IwAR123"
    expected2 = "https://www.example.com/path?a=1&b=2"
    assert normalize_url(raw2) == expected2

    # Empty string or None
    assert normalize_url("") == ""
    assert normalize_url(None) == ""


def test_clean_article_title():
    """clean_article_title removes publisher suffix and decodes HTML entities."""
    # Suffix removal
    assert clean_article_title("Título de la noticia - El País", "El País") == "Título de la noticia"
    # HTML entities decoded
    assert clean_article_title("Mandato &quot;independiente&quot; - Cinco Días", "Cinco Días") == 'Mandato "independiente"'
    # No publisher provided
    assert clean_article_title("Solo título", None) == "Solo título"


def test_clean_html_text():
    """clean_html_text strips HTML tags, unescapes entities, and normalizes spaces."""
    raw = '<a href="https://example.com">Texto &amp; enlace</a>&nbsp;<font color="#666">Medio</font>'
    assert clean_html_text(raw) == "Texto & enlace Medio"
    assert clean_html_text("") == ""
    assert clean_html_text(None) == ""


def test_parse_rfc822_date():
    """parse_rfc822_date parses GMT and timezone offsets to UTC."""
    # GMT
    dt_gmt = parse_rfc822_date("Wed, 09 Sep 2026 03:25:00 GMT")
    assert dt_gmt is not None
    assert dt_gmt.tzinfo == timezone.utc
    assert dt_gmt.year == 2026
    assert dt_gmt.hour == 3
    assert dt_gmt.minute == 25

    # +0200
    dt_offset = parse_rfc822_date("Wed, 09 Sep 2026 12:45:08 +0200")
    assert dt_offset is not None
    assert dt_offset.tzinfo == timezone.utc
    assert dt_offset.hour == 10  # 12:45 +0200 -> 10:45 UTC

    # Invalid date
    assert parse_rfc822_date("invalid-date-string") is None
    assert parse_rfc822_date(None) is None


def test_parse_google_news_rss_full():
    """parse_google_news_rss extracts items, cleans titles, normalizes URLs, and handles missing fields."""
    items = parse_google_news_rss(SAMPLE_GOOGLE_NEWS_XML, language="es")
    assert len(items) == 3

    # First item: full fields
    it1 = items[0]
    assert it1.title == "La cúpula de la CNMC tiene solo dos consejeros especializados en energía"
    assert "utm_source" not in it1.canonical_url
    assert "oc=5" not in it1.canonical_url
    assert it1.publisher == "The Objective"
    assert it1.publisher_url == "https://theobjective.com"
    assert it1.guid == "CBMipgFBVV95cUxOMDA2VndUWTRZemJX"
    assert it1.published_at is not None
    assert it1.published_at.hour == 3
    assert "The Objective" in it1.excerpt
    assert it1.language == "es"

    # Second item: with quotes and +0200 offset
    it2 = items[1]
    assert it2.title == 'Ganuza abre su mandato en la CNMC "garantizando independencia"'
    assert it2.publisher == "Cinco Días"
    assert it2.publisher_url == "https://cincodias.elpais.com"
    assert it2.published_at is not None
    assert it2.published_at.hour == 10  # converted to UTC

    # Third item: missing pubDate and source
    it3 = items[2]
    assert it3.title == "Investigación por cártel en el sector farmacéutico"
    assert it3.publisher is None
    assert it3.publisher_url is None
    assert it3.published_at is None
    assert it3.excerpt == "Breve resumen sin fuente explícita."


def test_parse_google_news_rss_malformed_and_empty():
    """parse_google_news_rss handles malformed XML or empty strings gracefully."""
    assert parse_google_news_rss("") == []
    assert parse_google_news_rss("   ") == []
    assert parse_google_news_rss("<not-xml>unclosed tag") == []
    assert parse_google_news_rss("<rss><channel></channel></rss>") == []
