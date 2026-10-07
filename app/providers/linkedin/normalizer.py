"""Canonical normalizer for LinkedIn posts, URNs, and URLs (Bloque 9C / Provenance Hardening)."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import urllib.parse
from typing import Iterable, Optional, Tuple, Union


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
    - Decodes percent-encoded characters (e.g. %c3%b6 -> ö) and normalizes unicode (NFC)
    - Strips protocol (http/https)
    - Normalizes regional/www subdomains (e.g., de., es., nl., fr., www., www.de. -> linkedin.com)
    - Strips query parameters and fragments
    - Strips trailing slashes
    """
    if not url or not isinstance(url, str):
        return ""
    clean = urllib.parse.unquote(url.strip())
    clean = unicodedata.normalize("NFC", clean).lower()
    clean = re.sub(r"^https?://", "", clean)
    clean = re.sub(r"^(?:[a-z0-9\-]+\.)*linkedin\.com(?=/|$)", "linkedin.com", clean)
    clean = clean.split("?")[0].split("#")[0].strip().rstrip("/")
    return clean


def canonicalize_linkedin_profile_url(url: Optional[str]) -> str:
    """Convert any raw/regional LinkedIn profile URL into canonical https://www.linkedin.com/... format."""
    norm = normalize_linkedin_profile_url(url)
    if not norm:
        return ""
    if norm == "linkedin.com":
        return "https://www.linkedin.com"
    if norm.startswith("linkedin.com/"):
        return f"https://www.{norm}"
    return f"https://www.linkedin.com/{norm.lstrip('/')}"


def _clean_name_tokens(name: Optional[str]) -> list[str]:
    """Clean and normalize a personal or entity name into lowercase ASCII tokens, stripping titles."""
    if not name or not isinstance(name, str):
        return []
    # Map special base Latin characters that do not decompose under NFKD (e.g. Turkish dotless i)
    special_latin = str.maketrans({
        "ı": "i", "İ": "i",
        "ß": "ss",
        "ø": "o", "Ø": "o",
        "æ": "ae", "Æ": "ae",
        "ł": "l", "Ł": "l",
        "ð": "d", "Ð": "d",
        "þ": "th", "Þ": "th",
    })
    trans = name.translate(special_latin)
    # Strip diacritics / accents (e.g. Günter -> Gunter, José -> Jose)
    normalized = unicodedata.normalize("NFKD", trans).encode("ASCII", "ignore").decode("utf-8")
    text = normalized.lower()

    # Common academic/professional titles, honorifics, and suffixes to ignore
    titles = {
        "dr", "prof", "professor", "mr", "mrs", "ms", "llm", "phd", "esq",
        "abogado", "lic", "ing", "avocat", "rechtsanwalt",
    }
    # Strip dots so abbreviations like LL.M., Ph.D., Dr. become llm, phd, dr
    text = text.replace(".", "")
    # Replace non-alphanumeric with whitespace
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = text.split()
    return [t for t in tokens if t not in titles]


def is_profile_name_match(expected_name: Optional[str], candidate_name: Optional[str]) -> bool:
    """Conservative check to verify candidate profile name matches expected entity name.

    Handles:
    - Case-insensitivity and diacritic normalization (e.g. Pınar -> Pinar)
    - Professional titles / honorifics (Dr., Prof., LL.M., PhD, etc.)
    - Middle initials and middle names (e.g. "Thomas Funke" matches "Dr. Thomas G. Funke")
    - Requires both first and last name alignment when multiple tokens exist.
    """
    if not expected_name or not candidate_name:
        return False

    exp_tokens = _clean_name_tokens(expected_name)
    cand_tokens = _clean_name_tokens(candidate_name)

    if not exp_tokens or not cand_tokens:
        return False

    # Exact token equality
    if exp_tokens == cand_tokens:
        return True

    # Multi-token conservative matching
    if len(exp_tokens) >= 2 and len(cand_tokens) >= 2:
        # First name and last name must strictly match
        if exp_tokens[0] != cand_tokens[0] or exp_tokens[-1] != cand_tokens[-1]:
            return False
        # All non-initial tokens in expected must be present in candidate
        exp_significant = [t for t in exp_tokens if len(t) > 1]
        cand_significant = set(t for t in cand_tokens if len(t) > 1)
        return all(t in cand_significant for t in exp_significant)

    # Single-token fallback: exact match required
    return exp_tokens == cand_tokens


def is_author_profile_coherent(
    author_profile_url: Optional[str],
    expected_entity_url: Union[str, Iterable[str], None],
) -> bool:
    """Verify that author_profile_url matches the configured entity LinkedIn URL(s).

    Accepts either a single URL string or an iterable of URL strings (e.g. configured URL
    and safely recovered canonical URL).
    """
    norm_author = normalize_linkedin_profile_url(author_profile_url)
    if not norm_author or expected_entity_url is None:
        return False

    if isinstance(expected_entity_url, str):
        candidates = [expected_entity_url]
    else:
        candidates = list(expected_entity_url)

    for cand in candidates:
        norm_cand = normalize_linkedin_profile_url(cand)
        if norm_cand and norm_author == norm_cand:
            return True

    return False


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
