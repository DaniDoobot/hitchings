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


def extract_publisher_domain(url_or_host: str | None) -> str:
    """Extract clean, normalized publisher domain or host from a URL or string.

    Strips schemes, ports, credentials, paths, query strings, and leading 'www.'.
    Forces lowercase.

    Examples:
        'https://www.cnmc.es/prensa/noticias' -> 'cnmc.es'
        'http://WWW.FT.COM:8080/world' -> 'ft.com'
        'https://legalblogs.wolterskluwer.com' -> 'legalblogs.wolterskluwer.com'
        'ga-p.com' -> 'ga-p.com'
        'Macfarlanes' -> 'macfarlanes'
    """
    if not url_or_host:
        return ""

    clean = url_or_host.strip().lower()
    if not clean:
        return ""

    # If full URL or protocol-relative
    if "://" in clean or clean.startswith("//"):
        if clean.startswith("//"):
            clean = "https:" + clean
        try:
            parts = urlsplit(clean)
            host = parts.netloc or parts.path
        except Exception:
            host = clean
    else:
        # Strip path if present e.g. "domain.com/path"
        host = clean.split("/")[0]

    # Strip port if present e.g. "host:8080"
    if ":" in host:
        host = host.split(":", 1)[0]

    # Strip userinfo if present e.g. "user@host"
    if "@" in host:
        host = host.split("@", 1)[1]

    # Strip leading 'www.'
    if host.startswith("www."):
        host = host[4:]

    return host.strip()


def normalize_title(title: str | None) -> str:
    """Deterministic title normalization for discovery deduplication fingerprinting.

    Transforms title deterministically:
    1. Unicode NFKC normalization
    2. Lowercase
    3. Strip superficial punctuation and symbols (keeping alphanumeric and spaces)
    4. Collapse whitespace and strip

    Does NOT perform stemming, translation, or fuzzy Levenshtein distance.
    """
    if not title:
        return ""

    import unicodedata

    # Unicode NFKC normalization
    normalized = unicodedata.normalize("NFKC", title)
    # Lowercase
    lowered = normalized.lower()
    # Strip superficial punctuation (replace non-alphanumeric/non-space with whitespace)
    cleaned = re.sub(r"[^\w\s]", " ", lowered)
    # Collapse multiple whitespaces and strip
    return re.sub(r"\s+", " ", cleaned).strip()


def compute_discovery_fingerprint(publisher_domain: str | None, title: str | None) -> str:
    """Compute conservative discovery fingerprint based on normalized publisher domain and title.

    Produces a deterministic SHA256 hex digest:
        SHA256(normalized_publisher_domain + ":" + normalized_title)

    Ensures:
    - Same publisher + same title (or superficial punctuation/whitespace variation) -> identical fingerprint.
    - Different publishers + same title -> different fingerprints (no cross-publisher collision).
    - Same publisher + materially different titles -> different fingerprints.
    """
    import hashlib

    norm_domain = extract_publisher_domain(publisher_domain)
    norm_title = normalize_title(title)
    combined = f"{norm_domain}:{norm_title}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
