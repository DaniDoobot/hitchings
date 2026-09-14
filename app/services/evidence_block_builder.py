"""Evidence Block Builder and Reference Resolution Service (Bloque 15C.3).

Constructs contiguous, deterministic Evidence Blocks from source text (title, content, excerpt)
and provides bidirectional resolution between Evidence Block IDs (T0001, C0001, E0001)
and exact verbatim text quotes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Any, Sequence

from app.models.entry import Entry
from app.schemas.analysis import GroundingEvidence
from app.services.grounding_validator import (
    AnalysisGroundingError,
    get_allowed_evidence_fields,
)


@dataclass(frozen=True)
class EvidenceBlock:
    """A contiguous, verifiable slice of original source text."""
    id: str  # e.g. 'T0001', 'C0001', 'E0001'
    source_field: str  # 'title', 'content', or 'excerpt'
    start: int  # start offset in original source text
    end: int  # end offset in original source text
    text: str  # exact contiguous substring: source_text[start:end]


@dataclass
class EvidenceBlockSet:
    """Ordered set of Evidence Blocks covering an entry's provided text fields."""
    blocks: list[EvidenceBlock]
    by_id: dict[str, EvidenceBlock]
    title_blocks: list[EvidenceBlock]
    content_blocks: list[EvidenceBlock]
    excerpt_blocks: list[EvidenceBlock]

    def get_block(self, block_id: str) -> Optional[EvidenceBlock]:
        """Lookup an evidence block by its unique ID."""
        return self.by_id.get(block_id)

    def render_title(self) -> str:
        """Render title with block IDs, e.g. '[T0001] Case Title'."""
        if not self.title_blocks:
            return ""
        return " ".join(f"[{b.id}] {b.text}" for b in self.title_blocks)

    def render_content(self) -> str:
        """Render content or excerpt blocks, one block per line with ID."""
        target_blocks = self.content_blocks if self.content_blocks else self.excerpt_blocks
        return "\n".join(f"[{b.id}] {b.text}" for b in target_blocks)


def segment_text_into_spans(
    text: str,
    target_chars: int = 140,
    min_chars: int = 50,
    max_chars: int = 220,
) -> list[tuple[int, int]]:
    """Segment source text into contiguous spans (start, end) without gaps.

    Rules:
    - Pure contiguous slices of source_text: text[start:end].
    - No reordering, no skipping intermediate non-whitespace text.
    - No semantic cleanup, no OCR repair, no modification of punctuation.
    - Deterministic.
    - Punctuation / newline-aware boundaries.
    - Only exterior whitespace between spans is trimmed.
    """
    if not text:
        return []

    spans: list[tuple[int, int]] = []
    n = len(text)
    curr = 0

    while curr < n:
        # Skip leading whitespace for the block
        while curr < n and text[curr].isspace():
            curr += 1
        if curr >= n:
            break

        remaining = n - curr
        if remaining <= max_chars:
            # Check if there is a strong paragraph break (\n\n) that can naturally divide remaining
            double_nl = text.find("\n\n", curr)
            if double_nl != -1 and (double_nl - curr) >= min_chars and (n - (double_nl + 2)) >= min_chars:
                cut = double_nl
                end = cut
                while end > curr and text[end - 1].isspace():
                    end -= 1
                if end > curr:
                    spans.append((curr, end))
                curr = double_nl + 2
                continue
            else:
                end = n
                while end > curr and text[end - 1].isspace():
                    end -= 1
                if end > curr:
                    spans.append((curr, end))
                break

        # Search window for optimal cut point
        window_start = curr + min_chars
        window_end = min(n, curr + max_chars)
        window = text[window_start:window_end]

        cut_offset = -1
        advance = 0

        # Priority 1: paragraph break (\n\n)
        p1 = window.find("\n\n")
        if p1 != -1:
            cut_offset = window_start + p1
            advance = 2
        else:
            # Priority 2: line break (\n)
            nl_indices = [m.start() for m in re.finditer(r"\n", window)]
            if nl_indices:
                best_idx = min(nl_indices, key=lambda idx: abs((window_start + idx) - (curr + target_chars)))
                cut_offset = window_start + best_idx
                advance = 1
            else:
                # Priority 3: Sentence ending ('. ', '? ', '! ')
                sent_matches = [m.start() + 1 for m in re.finditer(r"[.?!]\s", window)]
                if sent_matches:
                    best_idx = min(sent_matches, key=lambda idx: abs((window_start + idx) - (curr + target_chars)))
                    cut_offset = window_start + best_idx
                    advance = 0
                else:
                    # Priority 4: Semicolon or colon ('; ', ': ')
                    punct_matches = [m.start() + 1 for m in re.finditer(r"[:;]\s", window)]
                    if punct_matches:
                        best_idx = min(punct_matches, key=lambda idx: abs((window_start + idx) - (curr + target_chars)))
                        cut_offset = window_start + best_idx
                        advance = 0
                    else:
                        # Priority 5: Comma (', ')
                        comma_matches = [m.start() + 1 for m in re.finditer(r",\s", window)]
                        if comma_matches:
                            best_idx = min(comma_matches, key=lambda idx: abs((window_start + idx) - (curr + target_chars)))
                            cut_offset = window_start + best_idx
                            advance = 0
                        else:
                            # Priority 6: Word boundary (whitespace)
                            ws_matches = [m.start() for m in re.finditer(r"\s", window)]
                            if ws_matches:
                                best_idx = min(ws_matches, key=lambda idx: abs((window_start + idx) - (curr + target_chars)))
                                cut_offset = window_start + best_idx
                                advance = 0

        if cut_offset == -1:
            # Fallback: no whitespace found in entire window
            cut_offset = window_end
            advance = 0

        end = cut_offset
        while end > curr and text[end - 1].isspace():
            end -= 1

        if end > curr:
            spans.append((curr, end))

        curr = cut_offset + advance

    return spans


def build_evidence_block_set(entry: Entry) -> EvidenceBlockSet:
    """Build the deterministic EvidenceBlockSet for a given Entry.

    Follows the strict content section contract:
    - Allowed fields: 'title', and either 'content' or 'excerpt'.
    - Title blocks are prefixed with 'T' (e.g. 'T0001').
    - Content blocks are prefixed with 'C' (e.g. 'C0001').
    - Excerpt blocks are prefixed with 'E' (e.g. 'E0001').
    """
    allowed_fields = get_allowed_evidence_fields(entry)

    blocks: list[EvidenceBlock] = []
    by_id: dict[str, EvidenceBlock] = {}
    title_blocks: list[EvidenceBlock] = []
    content_blocks: list[EvidenceBlock] = []
    excerpt_blocks: list[EvidenceBlock] = []

    # 1. Title
    if "title" in allowed_fields and entry.title and entry.title.strip():
        title_spans = segment_text_into_spans(entry.title)
        for idx, (s, e) in enumerate(title_spans, start=1):
            block_id = f"T{idx:04d}"
            block = EvidenceBlock(
                id=block_id,
                source_field="title",
                start=s,
                end=e,
                text=entry.title[s:e],
            )
            blocks.append(block)
            by_id[block_id] = block
            title_blocks.append(block)

    # 2. Content
    if "content" in allowed_fields and entry.content and entry.content.strip():
        content_spans = segment_text_into_spans(entry.content)
        for idx, (s, e) in enumerate(content_spans, start=1):
            block_id = f"C{idx:04d}"
            block = EvidenceBlock(
                id=block_id,
                source_field="content",
                start=s,
                end=e,
                text=entry.content[s:e],
            )
            blocks.append(block)
            by_id[block_id] = block
            content_blocks.append(block)

    # 3. Excerpt (fallback if content not present)
    elif "excerpt" in allowed_fields and entry.excerpt and entry.excerpt.strip():
        excerpt_spans = segment_text_into_spans(entry.excerpt)
        for idx, (s, e) in enumerate(excerpt_spans, start=1):
            block_id = f"E{idx:04d}"
            block = EvidenceBlock(
                id=block_id,
                source_field="excerpt",
                start=s,
                end=e,
                text=entry.excerpt[s:e],
            )
            blocks.append(block)
            by_id[block_id] = block
            excerpt_blocks.append(block)

    return EvidenceBlockSet(
        blocks=blocks,
        by_id=by_id,
        title_blocks=title_blocks,
        content_blocks=content_blocks,
        excerpt_blocks=excerpt_blocks,
    )


def resolve_single_evidence_block(
    evidence: GroundingEvidence,
    block_set: EvidenceBlockSet,
) -> None:
    """Resolve a single GroundingEvidence block ID into its exact verbatim text quote.

    Enforces:
    1. Strict format check: exactly one block ID (e.g. 'T0001', 'C0001', 'E0001').
    2. Existence in block_set.by_id.
    3. Exact match between evidence.source_field and block.source_field.
    4. Replaces evidence.quote in-place with block.text.

    Raises:
        AnalysisGroundingError: on format error, multiple IDs, unknown ID, or field mismatch.
    """
    raw_quote = (evidence.quote or "").strip()

    # Check for strict block ID regex pattern
    if not re.match(r"^[TCE]\d{4,}$", raw_quote):
        # Reject composite IDs or multiple IDs (e.g. 'C0147 C0156', 'C0147+C0156', 'C0147, C0156')
        if re.search(r"[TCE]\d{4,}[\s,;+]+[TCE]\d{4,}", raw_quote) or (re.match(r"^[TCE]\d{4,}", raw_quote) and any(c in raw_quote for c in " ,+;")):
            raise AnalysisGroundingError(
                f"Grounding block reference format invalid: multiple block IDs or composite reference detected: '{raw_quote}'. "
                f"Exactly one single block ID (e.g. 'C0001', 'T0001', 'E0001') is required per evidence item."
            )
        preview = raw_quote[:80] + ("..." if len(raw_quote) > 80 else "")
        raise AnalysisGroundingError(
            f"Grounding block reference format invalid: expected Evidence Block ID (e.g. 'C0001', 'T0001', 'E0001'), "
            f"got free-form or malformed text: \"{preview}\""
        )

    # Check existence in block set
    block = block_set.by_id.get(raw_quote)
    if block is None:
        raise AnalysisGroundingError(
            f"Grounding block reference '{raw_quote}' not found in document evidence blocks."
        )

    # Check source_field alignment
    if evidence.source_field != block.source_field:
        raise AnalysisGroundingError(
            f"Grounding block reference mismatch: block '{raw_quote}' belongs to source_field '{block.source_field}', "
            f"but evidence specifies source_field='{evidence.source_field}'."
        )

    # Substitute quote in-place with verbatim block text
    evidence.quote = block.text


def resolve_grounding_evidence_blocks(
    payload: Any,
    block_set: EvidenceBlockSet,
    stage: str = "triage",
) -> None:
    """Traverse all evidence items in payload and resolve block IDs to exact verbatim text quotes.

    Supports:
    - AIAnalysisResponsePayload
    - TriageAnalysisResultV3
    - DeepAnalysisResultV3
    - list/sequence of GroundingEvidence

    Raises:
        AnalysisGroundingError: on any invalid block reference.
    """
    if payload is None:
        return

    visited: set[int] = set()

    def _resolve(ev: GroundingEvidence) -> None:
        if id(ev) in visited:
            return
        visited.add(id(ev))
        resolve_single_evidence_block(ev, block_set)

    # Direct list of GroundingEvidence
    if isinstance(payload, (list, tuple)):
        for item in payload:
            if isinstance(item, GroundingEvidence):
                _resolve(item)
        return

    # Triage evidence list
    if stage in ("triage", "all"):
        triage_evidence = getattr(payload, "evidence", None)
        if triage_evidence:
            for ev in triage_evidence:
                if isinstance(ev, GroundingEvidence):
                    _resolve(ev)

    # Deep summary evidence
    if stage in ("deep_analysis", "deep", "all"):
        summary_evidence = getattr(payload, "summary_evidence", None)
        if summary_evidence:
            for ev in summary_evidence:
                if isinstance(ev, GroundingEvidence):
                    _resolve(ev)

        # Deep key points
        key_points = getattr(payload, "key_point_items", None) or getattr(payload, "key_points", None)
        if key_points and isinstance(key_points, (list, tuple)):
            for kp in key_points:
                kp_ev = getattr(kp, "evidence", None)
                if kp_ev and isinstance(kp_ev, (list, tuple)):
                    for ev in kp_ev:
                        if isinstance(ev, GroundingEvidence):
                            _resolve(ev)
