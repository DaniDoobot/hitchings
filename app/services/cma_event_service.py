"""CMA Event Service: deterministic event classification, identity, and metadata mapping."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional, Any

AUTHORITATIVE_STRONG_PATTERNS: list[str] = [
    # Decisions & Outcomes
    "infringement decision",
    "clearance decision",
    "full text decision",
    "non-confidential decision",
    "final decision",
    "proposed decision",
    "decision to refer",
    "reference decision",
    "decision published",
    "decision announced",
    "decision made",
    "decision issued",
    "phase 1 decision",
    "phase 2 referral",
    "referred to phase 2",
    "referral to phase 2",
    # Reports & Findings
    "final report",
    "provisional findings",
    "provisional decision",
    # Undertakings & Commitments & Remedies
    "undertakings accepted",
    "undertaking accepted",
    "acceptance of undertakings",
    "acceptance of final undertakings",
    "acceptance of interim undertakings",
    "interim undertakings accepted",
    "interim undertakings",
    "interim undertaking",
    "commitments accepted",
    "commitment accepted",
    "acceptance of commitments",
    "undertaking",
    "undertakings",
    "remedies working paper",
    # Orders & Remedies
    "final order",
    "interim order",
    "enforcement order",
    "order made",
    "order published",
    "directions issued",
    # Digital Markets (DMCC)
    "conduct requirement",
    "strategic market status",
    "sms designation",
    # Inquiries & Statements & Launches
    "statement of objections",
    "issues statement",
    "statement of issues",
    "working paper published",
    "inquiry launched",
    "investigation launched",
    "commencement notice",
    "director disqualification",
    "penalty imposed",
    "penalty of",
    "first published",
]

THIRD_PARTY_RESPONSE_PATTERNS: list[str] = [
    r"\bresponses?\b",
    r"\bsubmissions?\b",
    r"\brepresentations?\b",
    r"\bthird[- ]part(y|ies)\b",
    r"\bpart(y|ies)['’]? response\b",
    r"\bsupplementa(l|ry) response\b",
    r"\bconsultation response\b",
    r"\broundtable summar(y|ies)\b",
    r"\bconsumer survey\b",
    r"\bhearing summar(y|ies)\b",
]

ADMIN_EXCLUDES: list[str] = [
    "administrative timetable",
    "timetable updated",
    "timetable published",
    "deadline extended",
    "contact details",
    "typographical",
    "inaccessible",
    "extension under section",
    "penalty paid",
    "derogation letters",
    "derogation letter",
    "derogations published",
    "derogation published",
]

GENERAL_SUBSTANTIVE: list[str] = [
    "decision",
    "clearance",
    "infringement",
    "commencement",
    "launched",
    "order",
    "direction",
]

DERIVATIVE_SUMMARY_PATTERNS: dict[str, list[str]] = {
    "final_report": [
        "summary of final report",
        "summary of the final report",
        "summary of final decision report",
        "summary of report",
    ],
    "provisional_findings": [
        "summary of provisional findings",
        "summary of the provisional findings",
        "summary of provisional decision",
    ],
    "decision": [
        "summary of phase 1 decision",
        "summary of final decision",
        "summary of decision",
        "summary of infringement decision",
    ],
}

PRIMARY_MILESTONE_PATTERNS: dict[str, list[str]] = {
    "final_report": [
        "final report",
        "final decision report",
        "full report",
    ],
    "provisional_findings": [
        "full text of provisional findings",
        "notice of provisional findings",
        "provisional findings",
        "provisional decision",
    ],
    "decision": [
        "full text decision",
        "non-confidential decision",
        "infringement decision",
        "clearance decision",
        "final decision published",
        "phase 1 decision",
    ],
}


def _has_response_indicator(text: str) -> Optional[str]:
    """Check if text contains any third-party response/submission pattern."""
    for pat in THIRD_PARTY_RESPONSE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def _has_authoritative_pattern(text: str) -> Optional[str]:
    """Check if text contains any authoritative formal CMA action pattern."""
    for pat in AUTHORITATIVE_STRONG_PATTERNS:
        if pat in text:
            return pat
    return None


def classify_cma_event_note(note: str) -> tuple[str, str]:
    """Classify a change_history event note into SUBSTANTIVE, ADMINISTRATIVE, or NON_SUBSTANTIVE.
    
    Deterministic Precedence:
    1. Independent Authoritative Action (clause free of response indicators) -> SUBSTANTIVE
    2. Third-Party Response / Submission -> ADMINISTRATIVE (SKIP)
    3. Administrative Excludes (timetable, etc.) -> ADMINISTRATIVE (SKIP)
    4. General Substantive (if free of response indicators) -> SUBSTANTIVE
    5. Rest -> NON_SUBSTANTIVE
    
    Returns:
        tuple[str, str]: (classification, matched_rule)
    """
    n = " ".join(note.lower().strip().split())
    if not n:
        return "NON_SUBSTANTIVE", "empty_note"

    # Split note into candidate clauses by commas, semicolons, 'and', '&'
    clauses = [c.strip() for c in re.split(r"[,;&]|\band\b", n) if c.strip()]

    # 1. If an independent clause describes a formal CMA action free of response words:
    for clause in clauses:
        if not _has_response_indicator(clause):
            auth_match = _has_authoritative_pattern(clause)
            if auth_match:
                return "SUBSTANTIVE", f"authoritative:{auth_match}"

    # 2. Third-party response / submission
    resp_match = _has_response_indicator(n)
    if resp_match:
        return "ADMINISTRATIVE", f"third_party_response:{resp_match}"

    # 3. Administrative excludes
    for admin_pat in ADMIN_EXCLUDES:
        if admin_pat in n:
            return "ADMINISTRATIVE", f"admin_exclude:{admin_pat}"

    # 4. General substantive
    for gen_pat in GENERAL_SUBSTANTIVE:
        if gen_pat in n:
            return "SUBSTANTIVE", f"general:{gen_pat}"

    return "NON_SUBSTANTIVE", "no_match"


def is_cma_event_substantive(note: str) -> bool:
    """Return True if the event note is classified as SUBSTANTIVE."""
    cls, _ = classify_cma_event_note(note)
    return cls == "SUBSTANTIVE"


def get_derivative_family(note: str) -> Optional[str]:
    """Return milestone family if note describes a derivative summary document."""
    n = " ".join(note.lower().strip().split())
    for family, patterns in DERIVATIVE_SUMMARY_PATTERNS.items():
        for p in patterns:
            if p in n:
                return family
    if "executive summary" in n:
        return "general_summary"
    return None


def get_primary_family(note: str) -> Optional[str]:
    """Return milestone family if note describes a primary authoritative document."""
    n = " ".join(note.lower().strip().split())
    # If the note is itself a summary, it cannot be the primary document
    if get_derivative_family(note) is not None:
        return None
    for family, patterns in PRIMARY_MILESTONE_PATTERNS.items():
        for p in patterns:
            if p in n:
                return family
    return None


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
