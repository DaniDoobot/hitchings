"""Canonical normalizer for LinkedIn posts, URNs, and URLs (Bloque 9C / Provenance Hardening)."""

from __future__ import annotations

import hashlib
import re
from typing import Optional, Tuple


# Regex patterns matching LinkedIn activity/share/post IDs across URLs and URN strings.
# Supports arbitrary-length numeric IDs (does NOT assume 19 digits).
_URN_PATTERNS = [
    re.compile(r"urn:li:(?:activity|share|ugcPost):(\d+)", re.IGNORECASE),
    re.compile(r"/feed/update/urn:li:(?:activity|share|ugcPost):(\d+)", re.IGNORECASE),
    re.compile(r"-activity-(\d+)", re.IGNORECASE),
    re.compile(r"/activity/(\d+)", re.IGNORECASE),
    re.compile(r"/update/urn:li:(?:activity|share|ugcPost):(\d+)", re.IGNORECASE),
    re.compile(r"/update/(\d+)", re.IGNORECASE),
    re.compile(r"^(\d+)$"),
]


def extract_linkedin_activity_id(
    item_id: Optional[str] = None,
    post_url: Optional[str] = None,
) -> Optional[str]:
    """Extract canonical numeric LinkedIn activity/post ID from raw IDs or URLs.

    Evaluates item_id first, then post_url, checking multiple URN and URL formats.
    Does not assume fixed-length (e.g. 19 digits).
    Returns string of digits or None.
    """
    candidates = [item_id, post_url]
    for candidate in candidates:
        if not candidate or not isinstance(candidate, str):
            continue
        cleaned = candidate.strip()
        for pattern in _URN_PATTERNS:
            match = pattern.search(cleaned)
            if match:
                return match.group(1)
    return None


def normalize_linkedin_canonical_url(url: str) -> str:
    """Normalize LinkedIn post URL to canonical form.

    - Strips query string / tracking parameters (?utm_*, ?rcm=*, ?trk=*)
    - Strips URL fragments (#...)
    - Normalizes http -> https
    - Strips trailing slash
    - Preserves valid path
    """
    if not url or not isinstance(url, str):
        return ""
    clean = url.strip()
    # Normalize protocol
    clean = re.sub(r"^http://", "https://", clean)
    # Strip query string and fragment
    clean = clean.split("?")[0].split("#")[0].strip().rstrip("/")
    return clean


def normalize_linkedin_profile_url(url: Optional[str]) -> str:
    """Normalize a LinkedIn profile or company page URL for strict provenance matching.

    - Lowercases
    - Strips protocol (http/https)
    - Normalizes regional/www subdomains (e.g., es., uk., pt., www. -> linkedin.com)
    - Strips query parameters and fragments
    - Strips trailing slashes
    """
    if not url or not isinstance(url, str):
        return ""
    clean = url.strip().lower()
    clean = re.sub(r"^https?://", "", clean)
    clean = re.sub(r"^(?:[a-z]{2}\.)?linkedin\.com/", "linkedin.com/", clean)
    clean = re.sub(r"^www\.linkedin\.com/", "linkedin.com/", clean)
    clean = clean.split("?")[0].split("#")[0].strip().rstrip("/")
    return clean


def is_author_profile_coherent(
    author_profile_url: Optional[str],
    expected_entity_url: Optional[str],
) -> bool:
    """Verify that author_profile_url matches the configured entity LinkedIn URL."""
    norm_author = normalize_linkedin_profile_url(author_profile_url)
    norm_expected = normalize_linkedin_profile_url(expected_entity_url)
    if not norm_author or not norm_expected:
        return False
    return norm_author == norm_expected


def resolve_canonical_identity(
    post_url_or_id: str,
    post_url: Optional[str] = None,
) -> Tuple[str, str, Optional[str], str]:
    """Resolve canonical external_id, canonical_url, activity_id, and identity_status.

    Can be called as:
      resolve_canonical_identity(post_url)
      resolve_canonical_identity(item_id, post_url)

    Returns:
        tuple: (external_id, canonical_url, activity_id, identity_status)
        where identity_status is either "activity_id" or "canonical_url_fallback".
    """
    if post_url is None:
        target_url = post_url_or_id
        item_id = None
    else:
        item_id = post_url_or_id
        target_url = post_url

    canon_url = normalize_linkedin_canonical_url(target_url)
    activity_id = extract_linkedin_activity_id(item_id, target_url)

    if activity_id:
        external_id = f"urn:li:activity:{activity_id}"
        identity_status = "activity_id"
    else:
        # Deterministic fallback based on normalized canonical URL
        url_hash = hashlib.sha256(canon_url.encode("utf-8")).hexdigest()[:24]
        external_id = f"linkedin:post:{url_hash}"
        identity_status = "canonical_url_fallback"

    return external_id, canon_url, activity_id, identity_status
