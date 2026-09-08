"""CAT Enrichment Service for HITCHINGS.

Selectively enriches CAT (Competition Appeal Tribunal) entries classified as INSUFFICIENT
by downloading their official judgment PDF from the CAT website, extracting the digital text layer,
normalizing formatting, validating identity, and updating the Entry while preserving provenance.

Zero AI calls. Purely deterministic and idempotent.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Optional, Tuple
import httpx
import pypdf
from sqlalchemy.orm import Session

from app.models.entry import Entry
from app.services.analysis_service import compute_analysis_input_hash
from app.services.source_sufficiency_service import (
    SourceSufficiencyLevel,
    assess_source_sufficiency,
)

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class CatEnrichmentError(Exception):
    """Base exception for CAT enrichment failures."""


class CatEnrichmentService:
    """Service to enrich insufficient CAT entries with official PDF text layer."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def clean_pdf_text(self, raw_text: str) -> str:
        """Normalize mechanical formatting anomalies without altering substantive legal content."""
        if not raw_text:
            return ""

        # Normalize CRLF / CR to LF
        text = raw_text.replace("\r\n", "\n").replace("\r", "\n")

        # Collapse horizontal spaces/tabs into single space (per line)
        lines = []
        for line in text.split("\n"):
            # Replace non-breaking spaces with standard space
            line = line.replace("\xa0", " ").replace("\u200b", "")
            # Collapse multiple spaces
            line = re.sub(r"[ \t]+", " ", line).strip()
            lines.append(line)

        joined = "\n".join(lines)

        # Collapse 3 or more consecutive newlines into 2
        joined = re.sub(r"\n{3,}", "\n\n", joined)

        return joined.strip()

    def extract_text_from_pdf(self, pdf_bytes: bytes) -> tuple[str, int]:
        """Extract digital text layer from PDF bytes using pypdf.

        Returns:
            (cleaned_text, page_count)
        """
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)
        raw_chunks = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            raw_chunks.append(page_text)

        full_raw = "\n\n".join(raw_chunks)
        cleaned = self.clean_pdf_text(full_raw)
        return cleaned, page_count

    def validate_identity(self, entry: Entry, extracted_text: str) -> bool:
        """Verify that the extracted PDF text genuinely matches the Entry citation, case, or parties."""
        meta = entry.raw_metadata or {}
        neutral_citation = meta.get("neutral_citation") or ""
        case_numbers = meta.get("case_numbers") or []
        case_names = meta.get("case_names") or []
        title = entry.title or ""

        text_lower = extracted_text.lower()

        # 1. Match neutral citation if present (e.g. "[2026] CAT 67" -> "cat 67" or "2026] cat 67")
        if neutral_citation:
            clean_cit = neutral_citation.lower().replace("[", "").replace("]", "")
            if clean_cit in text_lower:
                return True

        # 2. Match case number (e.g. "1597/5/7/23")
        for num in case_numbers:
            if num.lower() in text_lower:
                return True

        # 3. Match case names / parties
        for name in case_names:
            words = [w for w in re.split(r"[\s,\.-]+", name.lower()) if len(w) >= 5]
            if words and any(w in text_lower for w in words):
                return True

        # 4. Fallback match on words from title
        title_words = [w for w in re.split(r"[\s,\.-]+", title.lower()) if len(w) >= 6]
        matching_words = [w for w in title_words if w in text_lower]
        if len(matching_words) >= 2:
            return True

        return False

    def enrich_entry(
        self,
        entry: Entry,
        db: Session,
        client: Optional[httpx.Client] = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Selectively enrich a CAT entry if it is classified as INSUFFICIENT.

        Returns:
            (was_enriched, details_dict)
        """
        source_name = (entry.source.name if entry.source else "").lower()
        if "competition appeal tribunal" not in source_name and "cat" not in source_name:
            return False, {"reason": "Not a CAT entry", "entry_id": str(entry.id)}

        meta = dict(entry.raw_metadata or {})
        if meta.get("content_source") == "cat_judgment_pdf_text" and meta.get("full_text_available") is True:
            return False, {"reason": "Already enriched via PDF text (idempotent)", "entry_id": str(entry.id)}

        sufficiency = assess_source_sufficiency(entry)
        if sufficiency.level != SourceSufficiencyLevel.INSUFFICIENT:
            return False, {
                "reason": f"Entry is {sufficiency.level.value}, not insufficient. Enrichment skipped.",
                "entry_id": str(entry.id),
            }

        pdf_url = meta.get("judgment_pdf_url")
        if not pdf_url:
            return False, {"reason": "No judgment_pdf_url available", "entry_id": str(entry.id)}

        headers = {"User-Agent": USER_AGENT}
        try:
            if client is not None:
                resp = client.get(pdf_url, headers=headers, follow_redirects=True, timeout=self.timeout_seconds)
            else:
                with httpx.Client(follow_redirects=True, timeout=self.timeout_seconds) as c:
                    resp = c.get(pdf_url, headers=headers)
        except Exception as exc:
            logger.error("Failed to download PDF for entry %s from %s: %s", entry.id, pdf_url, exc)
            return False, {"reason": f"HTTP download error: {exc}", "entry_id": str(entry.id)}

        if resp.status_code != 200:
            return False, {
                "reason": f"HTTP status {resp.status_code} for {pdf_url}",
                "entry_id": str(entry.id),
            }

        if not resp.content.startswith(b"%PDF-"):
            return False, {
                "reason": f"Downloaded file is not a valid PDF (header: {resp.content[:10]!r})",
                "entry_id": str(entry.id),
            }

        try:
            extracted_text, page_count = self.extract_text_from_pdf(resp.content)
        except Exception as exc:
            logger.error("Failed to extract PDF text for entry %s: %s", entry.id, exc)
            return False, {"reason": f"PDF extraction error: {exc}", "entry_id": str(entry.id)}

        if not extracted_text or len(extracted_text) < 100:
            return False, {
                "reason": "Extracted text is empty or too short (<100 chars)",
                "entry_id": str(entry.id),
            }

        if not self.validate_identity(entry, extracted_text):
            return False, {
                "reason": "Extracted PDF content did not match entry citation/parties",
                "entry_id": str(entry.id),
            }

        old_content = entry.content or ""
        old_content_chars = len(old_content)
        old_content_hash = entry.content_hash

        if old_content and len(old_content) <= 300:
            meta["previous_content"] = old_content
            meta["previous_content_source"] = meta.get("content_source") or "official_html_summary"
            meta["previous_content_chars"] = old_content_chars

        meta["content_source"] = "cat_judgment_pdf_text"
        meta["content_format"] = "text/plain"
        meta["full_text_available"] = True
        meta["pdf_text_extracted"] = True
        meta["pdf_text_chars"] = len(extracted_text)
        meta["pdf_pages"] = page_count

        entry.content = extracted_text
        entry.raw_metadata = meta

        # CRITICAL (Bloque 7D.1): Entry.content_hash is strictly the ingestion deduplication
        # identity (SHA256(canonical_url | clean_title | clean_excerpt)) and MUST NOT be
        # modified upon content enrichment. This ensures future ingestion runs properly detect
        # the entry as already existing.
        #
        # Historical EntryAnalysis.entry_content_hash (SHA256(title | content) at analysis time)
        # also remains untouched.
        #
        # Stale analysis detection is achieved by comparing compute_analysis_input_hash(entry)
        # against historical EntryAnalysis.entry_content_hash.
        current_analysis_input_hash = compute_analysis_input_hash(entry)

        db.flush()

        details = {
            "entry_id": str(entry.id),
            "title": entry.title,
            "http_status": resp.status_code,
            "pdf_bytes": len(resp.content),
            "pages": page_count,
            "extracted_chars": len(extracted_text),
            "old_content_chars": old_content_chars,
            "ingestion_content_hash": entry.content_hash,
            "analysis_input_hash": current_analysis_input_hash,
        }
        return True, details
