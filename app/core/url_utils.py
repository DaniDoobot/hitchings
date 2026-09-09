"""URL normalization utilities for deduplication and canonical identity (Bloque 9A)."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Known marketing/tracking query parameter prefixes or exact keys to strip
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_reader",
    "gclid",
    "gclsrc",
    "dclid",
    "fbclid",
    "msclkid",
    "zanpid",
    "oc",
    "ved",
    "usqp",
    "ref",
    "ref_src",
    "ref_url",
    "_ga",
    "_gl",
    "mc_cid",
    "mc_eid",
}


def normalize_url(url: str | None) -> str:
    """Normalize URL by stripping tracking parameters, fragments, and formatting uniformly.
    
    Preserves all functional query parameters.
    Returns normalized URL string. Returns empty string if input is empty.
    """
    if not url:
        return ""

    clean = url.strip()
    if not clean:
        return ""

    try:
        parts = urlsplit(clean)
    except Exception:
        return clean

    # Scheme and hostname to lowercase
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    # Standardize empty path or strip trailing slash on paths longer than 1 character
    path = parts.path
    if path and path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Parse and filter query parameters
    filtered_query = []
    if parts.query:
        for k, v in parse_qsl(parts.query, keep_blank_values=True):
            k_lower = k.lower()
            if k_lower in TRACKING_PARAMS:
                continue
            if k_lower.startswith("utm_"):
                continue
            filtered_query.append((k, v))

    # Sort query parameters for deterministic canonical identity
    filtered_query.sort(key=lambda item: item[0])
    query = urlencode(filtered_query)

    # Empty fragment (strip '#...')
    fragment = ""

    return urlunsplit((scheme, netloc, path, query, fragment))
