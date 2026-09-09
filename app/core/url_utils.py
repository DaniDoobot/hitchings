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

    Returns empty string if input does not contain a valid dot-separated hostname
    (e.g. a bare publisher name like 'Macfarlanes' is NOT a domain).

    Examples:
        'https://www.cnmc.es/prensa/noticias' -> 'cnmc.es'
        'http://WWW.FT.COM:8080/world' -> 'ft.com'
        'https://legalblogs.wolterskluwer.com' -> 'legalblogs.wolterskluwer.com'
        'ga-p.com' -> 'ga-p.com'
        'Macfarlanes' -> '' (bare name is not a domain)
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

    host = host.strip()

    # A valid domain must contain at least one dot (e.g. example.com, cnmc.es).
    # A bare publisher name (e.g. 'Macfarlanes') is not a domain.
    if "." not in host:
        return ""

    return host


def domains_belong_to_same_site(domain_a: str | None, domain_b: str | None) -> bool:
    """Check if two domain names belong to the same website or parent/subdomain hierarchy.

    Conservative comparison:
        normalized_a == normalized_b
        OR normalized_a.endswith("." + normalized_b)
        OR normalized_b.endswith("." + normalized_a)

    Both domains must be non-empty and contain at least one dot.
    Does NOT use substring matching (prevents 'example.com' matching 'example.com.evil.test'
    or 'fake-ec.europa.eu' matching 'ec.europa.eu').

    Examples:
        ('competition-policy.ec.europa.eu', 'ec.europa.eu') -> True
        ('infocuria.curia.europa.eu', 'curia.europa.eu') -> True
        ('news.example.com', 'example.com') -> True
        ('ec.europa.eu', 'fake-ec.europa.eu') -> False
        ('example.com', 'example.com.evil.test') -> False
        ('curia.europa.eu', 'curia.europa.eu.example.org') -> False
    """
    if not domain_a or not domain_b:
        return False

    a = extract_publisher_domain(domain_a)
    b = extract_publisher_domain(domain_b)

    if not a or not b or "." not in a or "." not in b:
        return False

    return a == b or a.endswith("." + b) or b.endswith("." + a)


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


def compute_discovery_fingerprint(
    publisher_domain: str | None,
    title: str | None,
    publisher_name: str | None = None,
) -> str:
    """Compute conservative discovery fingerprint based on normalized publisher domain/name and title.

    Produces a deterministic SHA256 hex digest:
        SHA256(identity_prefix + ":" + normalized_title)

    Where identity_prefix is:
    - normalized publisher_domain (if present and valid FQDN)
    - "pub:" + normalize_title(publisher_name) (fallback if publisher_domain is missing/empty)
    - "" (if neither is present)

    Ensures:
    - Same publisher + same title (or superficial punctuation/whitespace variation) -> identical fingerprint.
    - Different publishers + same title -> different fingerprints (no cross-publisher collision).
    - Same publisher + materially different titles -> different fingerprints.
    - Bare publisher name ('Macfarlanes') is treated as publisher name fallback, NOT a fake hostname.
    """
    import hashlib

    norm_domain = extract_publisher_domain(publisher_domain)
    norm_title = normalize_title(title)

    if norm_domain:
        identity_prefix = norm_domain
    elif publisher_name:
        identity_prefix = f"pub:{normalize_title(publisher_name)}"
    else:
        identity_prefix = ""

    combined = f"{identity_prefix}:{norm_title}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
