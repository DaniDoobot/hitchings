"""CMA Event Service: deterministic event classification, identity, and metadata mapping."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional, Any

STRONG_SUBSTANTIVE: list[str] = [
    "infringement decision",
    "full text decision",
    "final decision",
    "clearance decision",
    "final report",
    "provisional findings",
    "provisional decision",
    "undertaking",
    "undertakings",
    "commitment",
    "commitments",
    "phase 2",
    "referral",
    "remedies",
    "conduct requirement",
    "strategic market status",
    "final order",
    "director disqualification",
]

GENERAL_SUBSTANTIVE: list[str] = [
    "decision",
    "clearance",
    "infringement",
    "commencement",
    "investigation launched",
    "inquiry launched",
    "launched",
    "statement of issues",
    "issues statement",
    "order",
    "direction",
    "penalty",
    "first published",
]

ADMIN_EXCLUDES: list[str] = [
    "administrative timetable",
    "timetable updated",
    "timetable published",
    "responses to areas of focus",
    "response to areas of focus",
    "third party response",
    "third party responses",
    "deadline extended",
    "contact details",
    "typographical",
    "inaccessible",
    "extension under section",
    "hearing summaries",
    "submissions published",
    "submission published",
    "summary of hearing",
    "penalty paid",
    "derogation letters",
    "derogation letter",
    "derogations published",
    "derogation published",
]


def classify_cma_event_note(note: str) -> tuple[str, str]:
    """Classify a change_history event note into SUBSTANTIVE, ADMINISTRATIVE, or NON_SUBSTANTIVE.
    
    Deterministic Precedence:
    1. STRONG_SUBSTANTIVE -> "SUBSTANTIVE"
    2. ADMIN_EXCLUDES -> "ADMINISTRATIVE"
    3. GENERAL_SUBSTANTIVE -> "SUBSTANTIVE"
    4. Rest -> "NON_SUBSTANTIVE"
    
    Returns:
        tuple[str, str]: (classification, matched_rule)
    """
    n = " ".join(note.lower().strip().split())
    if not n:
        return "NON_SUBSTANTIVE", "empty_note"

    # 1. STRONG_SUBSTANTIVE takes absolute precedence
    for pattern in STRONG_SUBSTANTIVE:
        if pattern in n:
            return "SUBSTANTIVE", f"strong:{pattern}"

    # 2. ADMIN_EXCLUDES
    for pattern in ADMIN_EXCLUDES:
        if pattern in n:
            return "ADMINISTRATIVE", f"admin_exclude:{pattern}"

    # 3. GENERAL_SUBSTANTIVE
    for pattern in GENERAL_SUBSTANTIVE:
        if pattern in n:
            return "SUBSTANTIVE", f"general:{pattern}"

    return "NON_SUBSTANTIVE", "no_match"


def is_cma_event_substantive(note: str) -> bool:
    """Return True if the event note is classified as SUBSTANTIVE."""
    cls, _ = classify_cma_event_note(note)
    return cls == "SUBSTANTIVE"


def normalize_event_note(note: str) -> str:
    """Deterministic normalization of note text for hashing and comparison."""
    clean = re.sub(r"[^a-z0-9]+", " ", note.lower().strip())
    return " ".join(clean.split())


def compute_note_short_hash(note: str) -> str:
    """Compute deterministic 8-hex SHA-256 fingerprint of normalized note."""
    norm = normalize_event_note(note)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:8]


def format_cma_timestamp_slug(event_dt: datetime) -> str:
    """Format UTC datetime into ISO-like slug with full second precision: YYYYMMDDTHHMMSSZ."""
    dt_utc = event_dt.astimezone(timezone.utc) if event_dt.tzinfo else event_dt.replace(tzinfo=timezone.utc)
    return dt_utc.strftime("%Y%m%dT%H%M%SZ")


def format_cma_external_id(content_id: str, event_dt: datetime, note: str) -> str:
    """Generate collision-free, deterministic external_id for a CMA milestone event.
    
    Format: govuk:cma:{content_id}:{timestamp_utc}:{short_hash}
    Example: govuk:cma:a9accd55-2b09-47fa-9d5f-aa26b74cbf02:20260617T110049Z:a1f8d49e
    """
    ts_slug = format_cma_timestamp_slug(event_dt)
    short_hash = compute_note_short_hash(note)
    return f"govuk:cma:{content_id}:{ts_slug}:{short_hash}"


def format_cma_event_url(base_path: str, event_dt: datetime, note: str) -> str:
    """Generate unique event URL with anchor fragment to prevent core deduplication collisions.
    
    Format: https://www.gov.uk{base_path}#hitchings-event-{timestamp}-{hash}
    """
    clean_base = base_path if base_path.startswith("/") else f"/{base_path}"
    ts_slug = format_cma_timestamp_slug(event_dt)
    short_hash = compute_note_short_hash(note)
    return f"https://www.gov.uk{clean_base}#hitchings-event-{ts_slug}-{short_hash}"


def map_cma_content_type(case_type_raw: Optional[str], document_type_raw: Optional[str]) -> str:
    """Map GOV.UK metadata to standard HITCHINGS content_type."""
    doc_type = (document_type_raw or "").lower().strip()
    if doc_type == "digital_markets_measure":
        return "digital_markets"

    case_type = (case_type_raw or "").lower().strip()
    if "digital" in case_type:
        return "digital_markets"
    elif "merger" in case_type:
        return "merger"
    elif "ca98" in case_type or ("cartel" in case_type and "criminal" not in case_type):
        return "antitrust"
    elif "criminal" in case_type:
        return "criminal_cartel"
    elif "market" in case_type:
        return "market_investigation"
    elif "regulatory" in case_type or "appeal" in case_type:
        return "regulatory_appeal"
    elif "disqualification" in case_type:
        return "director_disqualification"
    return "institutional_publication"


def infer_cma_legal_basis(content_type: str) -> tuple[Optional[str], bool]:
    """Derive legal basis from procedural content type if not explicit.
    
    Returns (legal_basis, is_inferred).
    """
    if content_type == "merger":
        return "Enterprise Act 2002 (Part 3)", True
    elif content_type == "antitrust":
        return "Competition Act 1998", True
    elif content_type == "criminal_cartel":
        return "Enterprise Act 2002 (Section 188)", True
    elif content_type == "market_investigation":
        return "Enterprise Act 2002 (Part 4)", True
    elif content_type == "digital_markets":
        return "Digital Markets, Competition and Consumers Act 2024 (DMCC)", True
    return None, False


def infer_cma_parties(title: str, content_type: str) -> tuple[Optional[list[str]], bool]:
    """Extract parties from mergers case title (e.g. 'Company A / Company B merger inquiry').
    
    Returns (parties_list, is_inferred).
    """
    if content_type == "merger":
        clean = re.sub(r"\s+merger\s+(inquiry|investigation).*$", "", title, flags=re.IGNORECASE).strip()
        parts = [p.strip() for p in re.split(r"\s+/\s+|\s+slash\s+", clean, flags=re.IGNORECASE) if p.strip()]
        if len(parts) >= 2:
            return parts, True
    return None, False
