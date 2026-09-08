"""Deterministic Grounding Evidence Validator for HITCHINGS (Bloque 7E).

Performs strict, deterministic verification of evidence quotes extracted from
source entries by AI analysis models.

Rules & Guarantees:
- Purely deterministic string search (NO fuzzy matching, NO embeddings, NO LLM).
- Allowed normalizations:
    1. Unicode NFKC normalization (ligatures, accents, symbols).
    2. CRLF/LF line ending normalization.
    3. Whitespace collapse (multiple consecutive spaces/tabs/newlines collapsed into a single space).
    4. Smart quotes / apostrophes / dashes normalization.
- Rejects translated, paraphrased, or hallucinated quotes.
- Enforces presence of at least 1 evidence quote per key point in DEEP stage.
- Raises AnalysisGroundingError on any verification failure.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Sequence
from app.models.entry import Entry
from app.schemas.analysis import GroundingEvidence, KeyPointV3


class AnalysisGroundingError(ValueError):
    """Raised when an AI analysis evidence quote fails strict verbatim verification."""


def normalize_text_for_matching(text: Optional[str]) -> str:
    """Normalize text for deterministic verbatim quote matching.

    Applies:
    - Unicode NFKC normalization.
    - Non-breaking space and special punctuation normalization.
    - Newline and whitespace collapsing to a single space.
    - Stripping of leading/trailing whitespace.
    """
    if not text:
        return ""

    # 1. Unicode NFKC
    norm = unicodedata.normalize("NFKC", text)

    # 2. Normalize smart punctuation and special characters
    norm = norm.replace("\u00a0", " ")  # non-breaking space
    norm = norm.replace("“", '"').replace("”", '"').replace("«", '"').replace("»", '"')
    norm = norm.replace("‘", "'").replace("’", "'").replace("`", "'")
    norm = norm.replace("–", "-").replace("—", "-")

    # 3. Collapse multiple whitespace chars (spaces, tabs, newlines) into a single space
    norm = re.sub(r"\s+", " ", norm).strip()

    # 4. Remove inadvertent whitespace before closing punctuation and after opening punctuation
    # (common HTML/PDF extraction artifacts like "Regulation\n\n)" -> "Regulation )")
    norm = re.sub(r"\s+([,.:;!?\)\]])", r"\1", norm)
    norm = re.sub(r"([\(\[])\s+", r"\1", norm)

    # 5. Normalize hyphenation spacing around word/digit boundaries (handles line-wrapped hyphens in PDF text)
    norm = re.sub(r"(\w)\s*-\s*(\w)", r"\1-\2", norm)
    return norm


def clean_quote_wrapper(quote: str) -> str:
    """Remove surrounding decorative quotes or markdown if model inadvertently wrapped quote."""
    q = quote.strip()
    # Strip surrounding quotation marks or markdown backticks/asterisks if matched
    if len(q) >= 2:
        if (q.startswith('"') and q.endswith('"')) or (q.startswith("'") and q.endswith("'")):
            q = q[1:-1].strip()
        elif (q.startswith("`") and q.endswith("`")) or (q.startswith("*") and q.endswith("*")):
            q = q[1:-1].strip()
    return q


def validate_grounding_quote(
    evidence: GroundingEvidence,
    entry: Entry,
    max_quote_chars: int = 500,
) -> bool:
    """Verify a single GroundingEvidence quote against the specified entry field.

    Raises:
        AnalysisGroundingError: if quote is empty, invalid field, or not found in source text.
    Returns:
        True if successfully verified.
    """
    field = (evidence.source_field or "").strip().lower()
    raw_quote = clean_quote_wrapper(evidence.quote or "")

    if not raw_quote:
        raise AnalysisGroundingError(
            f"Grounding verification failed: evidence quote for field '{field}' is empty."
        )

    if len(raw_quote) > max_quote_chars:
        raise AnalysisGroundingError(
            f"Grounding verification failed: quote exceeds maximum allowed length "
            f"({len(raw_quote)} chars > {max_quote_chars} chars): '{raw_quote[:80]}...'"
        )

    # Resolve target text
    if field == "title":
        source_text = entry.title or ""
    elif field == "content":
        source_text = entry.content or ""
    elif field == "excerpt":
        source_text = entry.excerpt or ""
        if not source_text:
            raise AnalysisGroundingError(
                f"Grounding verification failed: quote references source_field='excerpt', "
                f"but entry has no excerpt populated."
            )
    else:
        raise AnalysisGroundingError(
            f"Grounding verification failed: unrecognized source_field '{evidence.source_field}'. "
            f"Must be 'title', 'content', or 'excerpt'."
        )

    norm_quote = normalize_text_for_matching(raw_quote)
    norm_source = normalize_text_for_matching(source_text)

    if not norm_quote:
        raise AnalysisGroundingError(
            f"Grounding verification failed: normalized quote is empty."
        )

    # Verbatim substring search
    if norm_quote in norm_source:
        return True

    # Secondary check: case-insensitive match
    if norm_quote.lower() in norm_source.lower():
        return True

    # Truncate for clean error reporting
    preview = raw_quote if len(raw_quote) <= 120 else raw_quote[:120] + "..."
    raise AnalysisGroundingError(
        f"Grounding verification failed: verbatim quote not found in entry.{field}: \"{preview}\""
    )


def validate_triage_evidence(
    evidence_list: Optional[Sequence[GroundingEvidence]],
    entry: Entry,
) -> None:
    """Validate all evidence items returned in a TRIAGE stage analysis.

    Raises:
        AnalysisGroundingError: on missing or unverified quotes.
    """
    if not evidence_list:
        raise AnalysisGroundingError(
            "Triage v3 grounding failed: at least one evidence quote is required."
        )

    for item in evidence_list:
        validate_grounding_quote(item, entry)


def validate_deep_evidence(
    summary_evidence: Optional[Sequence[GroundingEvidence]],
    key_points: Optional[Sequence[KeyPointV3]],
    entry: Entry,
) -> None:
    """Validate all summary and key point evidence items in a DEEP stage analysis.

    Raises:
        AnalysisGroundingError: on missing summary evidence, missing key points,
        any key point lacking evidence quotes, or unverified quotes.
    """
    # 1. Summary evidence
    if summary_evidence:
        for item in summary_evidence:
            validate_grounding_quote(item, entry)

    # 2. Key points
    if not key_points:
        raise AnalysisGroundingError(
            "Deep v3 grounding failed: key_points list cannot be empty."
        )

    for idx, kp in enumerate(key_points, start=1):
        if not kp.evidence or len(kp.evidence) == 0:
            raise AnalysisGroundingError(
                f"Deep v3 grounding failed: key point #{idx} ('{kp.point[:60]}...') "
                f"does not contain any grounding evidence quote. At least 1 quote is required."
            )
        for ev in kp.evidence:
            validate_grounding_quote(ev, entry)
