"""CMA Attachment Service: strict, fail-closed matching and bounded extraction of official CMA PDFs."""

from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from dataclasses import dataclass, field

import httpx
import pypdf

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB ceiling
MAX_PAGES_FULL_EXTRACTION = 40
MAX_PAGES_BOUNDED_EXTRACTION = 30
MAX_EXTRACTED_CHARS = 60_000

# Canonical document keyphrases
CANONICAL_MATCH_PAIRS: list[tuple[str, str]] = [
    ("full text decision", "full text decision"),
    ("non confidential infringement decision", "non confidential infringement decision"),
    ("non confidential decision", "non confidential decision"),
    ("infringement decision", "infringement decision"),
    ("clearance decision", "clearance decision"),
    ("phase 1 decision", "phase 1 decision"),
    ("final decision", "final decision"),
    ("provisional findings", "provisional findings"),
    ("commencement notice", "commencement notice"),
    ("final report", "final report"),
    ("final undertakings", "final undertakings"),
    ("interim enforcement order", "interim enforcement order"),
    ("initial enforcement order", "initial enforcement order"),
    ("terms of reference", "terms of reference"),
    ("issues statement", "issues statement"),
    ("statement of issues", "statement of issues"),
    ("conduct requirement", "conduct requirement"),
    ("direction", "direction"),
]

# Strict cross-type exclusions: If note contains term A, attachment with term B is STRICTLY REJECTED
STRICT_CROSS_EXCLUSIONS: list[tuple[str, str]] = [
    ("final report", "provisional findings"),
    ("final report", "summary of responses"),
    ("final report", "working paper"),
    ("final report", "invitation to comment"),
    ("final decision", "proposed decision"),
    ("final decision", "provisional decision"),
    ("conduct requirement", "investigation notice"),
    ("infringement decision", "statement of objections"),
    ("clearance decision", "issues statement"),
    ("clearance decision", "commencement notice"),
]


@dataclass
class AttachmentMatchResult:
    level: str  # "EXACT", "STRONG", "AMBIGUOUS", "NONE"
    reason: str
    primary_attachment: Optional[dict[str, Any]] = None
    related_attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PDFExtractionResult:
    text: str
    total_pages: int
    pages_extracted: int
    bytes_count: int
    extracted_chars: int
    truncated: bool
    error: Optional[str] = None


def normalize_title(title: str) -> str:
    """Normalize note or attachment title for lexical comparison."""
    # Remove file extension and size annotations e.g. "(PDF, 345KB)"
    cleaned = re.sub(r"\s*\(pdf[^\)]*\)", "", title, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned.lower().strip())
    return " ".join(cleaned.split())


class CMAAttachmentService:
    """Service for strict matching and bounded extraction of CMA attachments."""

    @staticmethod
    def match_event_attachment(
        event_note: str,
        event_dt: datetime,
        attachments: list[dict[str, Any]],
    ) -> AttachmentMatchResult:
        """Strictly match a change_history event note with an official attachment.
        
        Fail-closed rules:
        - Must be within temporal window: event_dt - 2 days to event_dt + 1 day.
        - Must satisfy exact canonical match or strong document phrase match.
        - Strict cross-type exclusions (e.g. final report != provisional findings).
        - If multiple attachments share top score without a clear primary -> AMBIGUOUS.
        """
        if not attachments:
            return AttachmentMatchResult(level="NONE", reason="No attachments present on case page")

        norm_note = normalize_title(event_note)
        e_utc = event_dt.astimezone(timezone.utc) if event_dt.tzinfo else event_dt.replace(tzinfo=timezone.utc)
        min_date = (e_utc - timedelta(days=2)).date()
        max_date = (e_utc + timedelta(days=1)).date()

        candidates: list[tuple[int, str, dict[str, Any]]] = []

        for att in attachments:
            content_type = (att.get("content_type") or "").lower()
            url = (att.get("url") or "").lower()
            if not (content_type == "application/pdf" or url.endswith(".pdf")):
                continue

            raw_title = att.get("title") or ""
            norm_title = normalize_title(raw_title)

            # Check strict cross-exclusions
            is_excluded = False
            for note_term, att_term in STRICT_CROSS_EXCLUSIONS:
                if note_term in norm_note and att_term in norm_title:
                    is_excluded = True
                    break
            if is_excluded:
                continue

            # Temporal window check
            c_at_str = att.get("created_at") or att.get("updated_at")
            in_window = False
            if c_at_str:
                try:
                    att_dt = datetime.fromisoformat(c_at_str.replace("Z", "+00:00"))
                    att_date = att_dt.date()
                    if min_date <= att_date <= max_date:
                        in_window = True
                except Exception:
                    pass

            # Score matching
            score = 0
            match_type = "NONE"

            # 1. Exact canonical phrase match
            for note_phrase, att_phrase in CANONICAL_MATCH_PAIRS:
                if note_phrase in norm_note and att_phrase in norm_title:
                    score = 100 if in_window else 70
                    match_type = "EXACT" if in_window else "STRONG"
                    break

            # 2. Strong overlap fallback (only if within window)
            if score == 0 and in_window:
                overlap_words = set(norm_note.split()) & set(norm_title.split())
                meaningful = {w for w in overlap_words if len(w) > 4 and w not in {"published", "notice", "update"}}
                if len(meaningful) >= 2:
                    score = 50 + len(meaningful) * 5
                    match_type = "STRONG"

            if score > 0:
                # Penalize non-primary docs (glossary, appendices, annexes, summaries)
                if any(x in norm_title for x in ["appendix", "appendices", "annex", "glossary", "summary of"]):
                    score -= 20
                candidates.append((score, match_type, att))

        if not candidates:
            return AttachmentMatchResult(level="NONE", reason="No attachment satisfied strict temporal and lexical criteria")

        # Sort by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        top_score, top_type, primary_att = candidates[0]

        # Check for ambiguity at top score
        top_tier = [c for c in candidates if c[0] == top_score]
        if len(top_tier) > 1:
            # If multiple docs share the same top score and none is clearly marked primary
            titles = [c[2].get("title", "") for c in top_tier]
            return AttachmentMatchResult(
                level="AMBIGUOUS",
                reason=f"Multiple candidate attachments shared top score ({top_score}): {titles[:3]}",
                related_attachments=[c[2] for c in candidates],
            )

        related = [c[2] for c in candidates[1:]]

        return AttachmentMatchResult(
            level=top_type,
            reason=f"Matched {top_type} document '{primary_att.get('title')}' (score={top_score})",
            primary_attachment=primary_att,
            related_attachments=related,
        )

    @staticmethod
    async def download_pdf_bounded(
        client: httpx.AsyncClient,
        pdf_url: str,
        max_bytes: int = MAX_PDF_BYTES,
    ) -> tuple[Optional[bytes], Optional[str]]:
        """Stream download PDF in-memory with strict byte limit abort.
        
        Returns (bytes, error_message).
        """
        try:
            async with client.stream("GET", pdf_url, follow_redirects=True, timeout=30.0) as response:
                if response.status_code != 200:
                    return None, f"HTTP {response.status_code} downloading PDF"

                # Check Content-Length header if present
                cl_header = response.headers.get("Content-Length")
                if cl_header:
                    try:
                        content_len = int(cl_header)
                        if content_len > max_bytes:
                            return None, f"PDF Content-Length ({content_len} bytes) exceeds limit of {max_bytes} bytes"
                    except ValueError:
                        pass

                buffer = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    buffer.extend(chunk)
                    if len(buffer) > max_bytes:
                        return None, f"PDF streaming aborted: exceeded limit of {max_bytes} bytes"

                return bytes(buffer), None
        except Exception as exc:
            return None, f"Network error downloading PDF: {exc}"

    @staticmethod
    def extract_pdf_text_bounded(pdf_bytes: bytes) -> PDFExtractionResult:
        """Extract text from PDF using pypdf with strict page and char boundaries."""
        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            total_pages = len(reader.pages)
            if total_pages == 0:
                return PDFExtractionResult(
                    text="",
                    total_pages=0,
                    pages_extracted=0,
                    bytes_count=len(pdf_bytes),
                    extracted_chars=0,
                    truncated=False,
                    error="PDF contains 0 pages",
                )

            # Determine page extraction limit
            if total_pages <= MAX_PAGES_FULL_EXTRACTION:
                pages_to_extract = total_pages
                truncated_by_pages = False
            else:
                pages_to_extract = min(total_pages, MAX_PAGES_BOUNDED_EXTRACTION)
                truncated_by_pages = True

            extracted_chunks = []
            chars_count = 0
            truncated_by_chars = False

            for idx in range(pages_to_extract):
                page = reader.pages[idx]
                page_text = page.extract_text() or ""
                if chars_count + len(page_text) > MAX_EXTRACTED_CHARS:
                    allowed = MAX_EXTRACTED_CHARS - chars_count
                    extracted_chunks.append(page_text[:allowed])
                    chars_count += allowed
                    truncated_by_chars = True
                    break
                else:
                    extracted_chunks.append(page_text)
                    chars_count += len(page_text)

            full_text = "\n\n".join(extracted_chunks).strip()
            is_truncated = truncated_by_pages or truncated_by_chars

            return PDFExtractionResult(
                text=full_text,
                total_pages=total_pages,
                pages_extracted=len(extracted_chunks),
                bytes_count=len(pdf_bytes),
                extracted_chars=len(full_text),
                truncated=is_truncated,
            )
        except Exception as exc:
            return PDFExtractionResult(
                text="",
                total_pages=0,
                pages_extracted=0,
                bytes_count=len(pdf_bytes),
                extracted_chars=0,
                truncated=False,
                error=f"PDF extraction error: {exc}",
            )
