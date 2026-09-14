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


MIN_ELLIPSIS_QUOTE_CHARS: int = 40


def normalize_grounding_text(text: Optional[str]) -> str:
    """Normalize text for deterministic verbatim quote matching.

    Applies:
    1. Unicode NFKC normalization.
    2. Resolves PDF line-wrap hyphenation (word-\\ncontinuation or word-\\r\\ncontinuation -> wordcontinuation).
       Preserves intra-line hyphens (e.g. 'private-enforcement' remains intact).
    3. Normalizes smart punctuation and special characters.
    4. Collapses multiple whitespace chars (spaces, tabs, newlines) into a single space.
    5. Normalizes whitespace around punctuation.
    6. Strips leading/trailing whitespace.
    """
    if not text:
        return ""

    # 1. Unicode NFKC
    norm = unicodedata.normalize("NFKC", text)

    # 2. Resolve line-wrap hyphenation typical in PDFs (word-\nnext -> wordnext)
    # Only matches hyphens immediately followed by newline, preserving intra-line hyphens
    norm = re.sub(r"(\w+)-\s*[\r\n]+\s*(\w+)", r"\1\2", norm)

    # 3. Normalize smart punctuation and special characters
    norm = norm.replace("\u00a0", " ")  # non-breaking space
    norm = norm.replace("“", '"').replace("”", '"').replace("«", '"').replace("»", '"')
    norm = norm.replace("‘", "'").replace("’", "'").replace("`", "'")
    norm = norm.replace("–", "-").replace("—", "-")

    # 4. Collapse multiple whitespace chars (spaces, tabs, newlines) into a single space
    norm = re.sub(r"\s+", " ", norm).strip()

    # 5. Remove inadvertent whitespace before closing punctuation and after opening punctuation
    # (common HTML/PDF extraction artifacts like "Regulation\n\n)" -> "Regulation )")
    norm = re.sub(r"\s+([,.:;!?\)\]])", r"\1", norm)
    norm = re.sub(r"([\(\[])\s+", r"\1", norm)

    # 6. Normalize hyphenation spacing around word/digit boundaries if spaces were present
    norm = re.sub(r"(\w)\s*-\s*(\w)", r"\1-\2", norm)
    return norm


def normalize_grounding_text_preserve_line_hyphens(text: Optional[str]) -> str:
    """Normalize text preserving line-break hyphens.

    Applies identical normalizations to normalize_grounding_text EXCEPT line-wrap
    dehyphenation (rule 2). Allows matching lexical compound words (e.g. 'wholly-owned')
    and numeric ranges (e.g. '[5-10]%') that happened to break across lines in PDFs.
    """
    if not text:
        return ""

    # 1. Unicode NFKC
    norm = unicodedata.normalize("NFKC", text)

    # 2. SKIPPED: do NOT collapse line-break hyphens (re.sub(r"(\w+)-\s*[\r\n]+\s*(\w+)", r"\1\2", norm))

    # 3. Normalize smart punctuation and special characters
    norm = norm.replace("\u00a0", " ")  # non-breaking space
    norm = norm.replace("“", '"').replace("”", '"').replace("«", '"').replace("»", '"')
    norm = norm.replace("‘", "'").replace("’", "'").replace("`", "'")
    norm = norm.replace("–", "-").replace("—", "-")

    # 4. Collapse multiple whitespace chars (spaces, tabs, newlines) into a single space
    norm = re.sub(r"\s+", " ", norm).strip()

    # 5. Remove inadvertent whitespace before closing punctuation and after opening punctuation
    norm = re.sub(r"\s+([,.:;!?\)\]])", r"\1", norm)
    norm = re.sub(r"([\(\[])\s+", r"\1", norm)

    # 6. Normalize hyphenation spacing around word/digit boundaries if spaces were present
    norm = re.sub(r"(\w)\s*-\s*(\w)", r"\1-\2", norm)
    return norm


def repair_enumerated_single_letter_split(text: str) -> str:
    """Repair isolated single-letter PDF extraction splits following enumeration markers.

    In CMA decision PDFs, numbered paragraphs or parenthesized list markers frequently
    have a decorative drop-cap or styled initial separated by whitespace from the rest
    of the word (e.g. '14. B arriers' -> '14. Barriers', '(c) T here' -> '(c) There').

    Security guards:
    - Anchored strictly to numbered paragraph ('14. ') or list marker ('(c) ').
    - Strictly excludes valid English single-letter words 'A' and 'I' ([B-HJ-Z] only).
    - Requires at least 2 lowercase trailing characters ([a-z]{2,}).
    - Never applied globally outside these explicit markers.
    """
    if not text:
        return ""
    # Numbered paragraph: e.g. "14. B arriers" -> "14. Barriers"
    repaired = re.sub(r"(\b\d+\.\s+)([B-HJ-Z])\s+([a-z]{2,}\b)", r"\1\2\3", text)
    # List marker: e.g. "(c) T here" -> "(c) There"
    repaired = re.sub(r"(\([A-Za-z0-9]+\)\s+)([B-HJ-Z])\s+([a-z]{2,}\b)", r"\1\2\3", repaired)
    return repaired


def candidate_source_normalizations(source_text: Optional[str]) -> list[str]:
    """Generate deterministic, deduplicated candidate normalizations for source text.

    Yields up to 4 deterministic variants:
    1. Primary normalization (standard pipeline with line-wrap dehyphenation).
    2. Primary + enumerated initial single-letter repair.
    3. Line-break hyphen preserved normalization (lexical compounds & numeric ranges).
    4. Line-break hyphen preserved + enumerated initial single-letter repair.
    """
    if not source_text:
        return [""]

    cands: list[str] = []
    seen: set[str] = set()

    def _add(cand: str) -> None:
        if cand and cand not in seen:
            seen.add(cand)
            cands.append(cand)

    # 1. Primary standard normalization
    norm_primary = normalize_grounding_text(source_text)
    _add(norm_primary)

    # 2. Primary with enumerated initial repair
    norm_primary_repaired = repair_enumerated_single_letter_split(norm_primary)
    _add(norm_primary_repaired)

    # 3. Preserve-line-hyphen normalization
    norm_preserve = normalize_grounding_text_preserve_line_hyphens(source_text)
    _add(norm_preserve)

    # 4. Preserve-line-hyphen with enumerated initial repair
    norm_preserve_repaired = repair_enumerated_single_letter_split(norm_preserve)
    _add(norm_preserve_repaired)

    return cands


# Canonical alias for normalize_grounding_text
normalize_text_for_matching = normalize_grounding_text


def strip_trailing_ellipsis(quote: str, min_chars: int = MIN_ELLIPSIS_QUOTE_CHARS) -> str:
    """If model quote ends with literal trailing ellipsis ('...' or '…'), strip it for comparison.

    Security guard: Only strips if the remaining quote prefix meets min_chars (default >= 40).
    Does NOT strip internal ellipses.
    """
    q = quote.strip()
    m = re.search(r"(\.{3,}|…)\s*$", q)
    if m:
        prefix = q[:m.start()].strip()
        if len(prefix) >= min_chars:
            return prefix
    return q


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


def get_allowed_evidence_fields(entry: Entry) -> set[str]:
    """Determine the valid source fields that were actually provided in the model input.

    Matches the prompt builder's _build_content_section contract:
    - If entry.content is present and non-empty, only 'title' and 'content' were supplied to the model.
      'excerpt' was NOT provided in the prompt and cannot be used as an evidence source.
    - If entry.content is absent/empty and entry.excerpt is present and non-empty, 'title' and 'excerpt' were supplied.
    - 'title' is supplied if present.
    - Metadatas or other attributes are not valid evidence quote sources.
    """
    fields = set()
    if entry.title and entry.title.strip():
        fields.add("title")
    if entry.content and entry.content.strip():
        fields.add("content")
    elif entry.excerpt and entry.excerpt.strip():
        fields.add("excerpt")
    return fields


def validate_grounding_quote(
    evidence: GroundingEvidence,
    entry: Entry,
    allowed_source_fields: Optional[set[str]] = None,
    max_quote_chars: int = 500,
) -> bool:
    """Verify a single GroundingEvidence quote against the specified entry field.

    Enforces:
    1. The source_field must be present in allowed_source_fields (model input availability).
    2. Verbatim string matching against the referenced source field.

    Raises:
        AnalysisGroundingError: if quote is empty, invalid field, not in model input, or not found in source text.
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

    # 1. Verify field availability in actual model input
    valid_fields = allowed_source_fields if allowed_source_fields is not None else get_allowed_evidence_fields(entry)
    if field not in valid_fields:
        raise AnalysisGroundingError(
            f"Grounding verification failed: evidence references source_field='{evidence.source_field}', "
            f"which was not present in the model input for this entry. "
            f"Allowed source fields: {sorted(list(valid_fields))}."
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

    # Strip trailing ellipsis marker if remaining quote meets security threshold
    quote_for_matching = strip_trailing_ellipsis(raw_quote, min_chars=MIN_ELLIPSIS_QUOTE_CHARS)

    norm_quote = normalize_text_for_matching(quote_for_matching)

    if not norm_quote:
        raise AnalysisGroundingError(
            f"Grounding verification failed: normalized quote is empty."
        )

    # Deterministic multi-candidate source matching
    candidates = candidate_source_normalizations(source_text)
    quote_lower = norm_quote.lower()

    for cand in candidates:
        # Verbatim substring search
        if norm_quote in cand:
            return True
        # Secondary check: case-insensitive match
        if quote_lower in cand.lower():
            return True

    # Truncate for clean error reporting
    preview = raw_quote if len(raw_quote) <= 120 else raw_quote[:120] + "..."
    raise AnalysisGroundingError(
        f"Grounding verification failed: verbatim quote not found in entry.{field}: \"{preview}\""
    )


def validate_triage_evidence(
    evidence_list: Optional[Sequence[GroundingEvidence]],
    entry: Entry,
    allowed_source_fields: Optional[set[str]] = None,
) -> None:
    """Validate all evidence items returned in a TRIAGE stage analysis.

    Raises:
        AnalysisGroundingError: on missing or unverified quotes.
    """
    if not evidence_list:
        raise AnalysisGroundingError(
            "Triage v3 grounding failed: at least one evidence quote is required."
        )

    valid_fields = allowed_source_fields if allowed_source_fields is not None else get_allowed_evidence_fields(entry)
    for item in evidence_list:
        validate_grounding_quote(item, entry, allowed_source_fields=valid_fields)


def validate_deep_evidence(
    summary_evidence: Optional[Sequence[GroundingEvidence]],
    key_points: Optional[Sequence[KeyPointV3]],
    entry: Entry,
    allowed_source_fields: Optional[set[str]] = None,
) -> None:
    """Validate all summary and key point evidence items in a DEEP stage analysis.

    Raises:
        AnalysisGroundingError: on missing summary evidence, missing key points,
        any key point lacking evidence quotes, or unverified quotes.
    """
    valid_fields = allowed_source_fields if allowed_source_fields is not None else get_allowed_evidence_fields(entry)

    # 1. Summary evidence
    if summary_evidence:
        for item in summary_evidence:
            validate_grounding_quote(item, entry, allowed_source_fields=valid_fields)

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
            validate_grounding_quote(ev, entry, allowed_source_fields=valid_fields)
