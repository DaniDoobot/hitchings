"""Bundeskartellamt PDF Enrichment Service for HITCHINGS.

Selectively enriches Bundeskartellamt 'case_report' (Fallberichte) entries
whose HTML content is classified as PARTIAL by downloading their official short
Fallbericht PDF, extracting the digital text layer with pypdf, enforcing
conservative safety limits, and combining it with the official HTML text.

Zero AI calls. Purely deterministic and idempotent.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Optional
import httpx
import pypdf
from pydantic import BaseModel

from app.models.entry import Entry
from app.models.source import Source
from app.services.source_sufficiency_service import (
    SourceSufficiencyLevel,
    SourceSufficiencyService,
)

logger = logging.getLogger(__name__)

USER_AGENT = "HITCHINGS/0.1 (+https://github.com/hitchings; news-observatory)"

# Conservative safety limits for Fallberichte enrichment
MAX_PDF_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_PDF_PAGES = 20                # Fallberichte are 2-5 pages; long decisions (166 pages) must not enter
MAX_EXTRACTED_CHARS = 50_000      # Generous headroom over typical 5k-12k chars


class BundeskartellamtPDFEnrichmentResult(BaseModel):
    """Result of attempting to enrich a Bundeskartellamt item with official PDF text."""

    success: bool
    enriched_content: str
    pdf_url: Optional[str] = None
    pdf_pages: int = 0
    pdf_bytes: int = 0
    pdf_extracted_chars: int = 0
    error_reason: Optional[str] = None


class BundeskartellamtPDFEnrichmentService:
    """Service to enrich PARTIAL Bundeskartellamt case reports with official PDF text."""

    def __init__(
        self,
        max_pdf_bytes: int = MAX_PDF_BYTES,
        max_pdf_pages: int = MAX_PDF_PAGES,
        max_extracted_chars: int = MAX_EXTRACTED_CHARS,
    ) -> None:
        self.max_pdf_bytes = max_pdf_bytes
        self.max_pdf_pages = max_pdf_pages
        self.max_extracted_chars = max_extracted_chars

    @staticmethod
    def is_eligible_for_enrichment(
        source_name: str,
        content_type: str,
        content: str,
        pdf_url: Optional[str],
    ) -> bool:
        """Strict gate check for PDF enrichment activation.

        Must satisfy ALL:
        - source == Bundeskartellamt
        - content_type == "case_report"
        - SourceSufficiencyService.assess(content) == PARTIAL
        - pdf_url is present
        """
        if "bundeskartellamt" not in (source_name or "").lower():
            return False
        if content_type != "case_report":
            return False
        if not pdf_url:
            return False

        # Evaluate sufficiency of existing HTML content
        transient = Entry(
            content=content,
            source=Source(name="Bundeskartellamt"),
        )
        res = SourceSufficiencyService.assess(transient)
        return res.level == SourceSufficiencyLevel.PARTIAL

    def clean_pdf_text(self, raw_text: str) -> str:
        """Normalize whitespace and newlines without modifying legal text."""
        if not raw_text:
            return ""

        text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = []
        for line in text.split("\n"):
            line = line.replace("\xa0", " ").replace("\u200b", "")
            line = re.sub(r"[ \t]+", " ", line).strip()
            lines.append(line)

        joined = "\n".join(lines)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return joined.strip()

    def extract_text_from_pdf(self, pdf_bytes: bytes) -> tuple[str, int]:
        """Extract text page by page using pypdf.

        Returns:
            (cleaned_text, page_count)
        """
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)
        raw_chunks = []
        for page in reader.pages:
            ptxt = page.extract_text() or ""
            raw_chunks.append(ptxt)

        full_raw = "\n\n".join(raw_chunks)
        cleaned = self.clean_pdf_text(full_raw)
        return cleaned, page_count

    async def enrich_item_async(
        self,
        client: httpx.AsyncClient,
        pdf_url: str,
        base_content: str,
    ) -> BundeskartellamtPDFEnrichmentResult:
        """Fetch official PDF in memory, enforce safety bounds, and extract text layer."""
        headers = {"User-Agent": USER_AGENT}

        try:
            resp = await client.get(pdf_url, headers=headers)
        except Exception as exc:
            logger.warning("Network error fetching Bundeskartellamt PDF %s: %s", pdf_url, exc)
            return BundeskartellamtPDFEnrichmentResult(
                success=False,
                enriched_content=base_content,
                pdf_url=pdf_url,
                error_reason=f"network_error: {exc}",
            )

        if resp.status_code != 200:
            logger.warning("Bundeskartellamt PDF %s returned HTTP %d", pdf_url, resp.status_code)
            return BundeskartellamtPDFEnrichmentResult(
                success=False,
                enriched_content=base_content,
                pdf_url=pdf_url,
                error_reason=f"http_{resp.status_code}",
            )

        pdf_bytes = resp.content
        byte_len = len(pdf_bytes)
        if byte_len > self.max_pdf_bytes:
            logger.warning(
                "Bundeskartellamt PDF %s exceeds max bytes (%d > %d)",
                pdf_url,
                byte_len,
                self.max_pdf_bytes,
            )
            return BundeskartellamtPDFEnrichmentResult(
                success=False,
                enriched_content=base_content,
                pdf_url=pdf_url,
                pdf_bytes=byte_len,
                error_reason="max_pdf_bytes_exceeded",
            )

        try:
            cleaned_text, page_count = self.extract_text_from_pdf(pdf_bytes)
        except Exception as exc:
            logger.warning("Failed parsing Bundeskartellamt PDF %s: %s", pdf_url, exc)
            return BundeskartellamtPDFEnrichmentResult(
                success=False,
                enriched_content=base_content,
                pdf_url=pdf_url,
                pdf_bytes=byte_len,
                error_reason=f"pdf_parse_error: {exc}",
            )

        if page_count > self.max_pdf_pages:
            logger.warning(
                "Bundeskartellamt PDF %s exceeds max pages (%d > %d)",
                pdf_url,
                page_count,
                self.max_pdf_pages,
            )
            return BundeskartellamtPDFEnrichmentResult(
                success=False,
                enriched_content=base_content,
                pdf_url=pdf_url,
                pdf_pages=page_count,
                pdf_bytes=byte_len,
                error_reason="max_pdf_pages_exceeded",
            )

        if page_count == 0:
            return BundeskartellamtPDFEnrichmentResult(
                success=False,
                enriched_content=base_content,
                pdf_url=pdf_url,
                pdf_pages=0,
                pdf_bytes=byte_len,
                error_reason="empty_pdf",
            )

        extracted_chars = len(cleaned_text)
        if extracted_chars == 0:
            logger.warning("Bundeskartellamt PDF %s contains no extractable text layer", pdf_url)
            return BundeskartellamtPDFEnrichmentResult(
                success=False,
                enriched_content=base_content,
                pdf_url=pdf_url,
                pdf_pages=page_count,
                pdf_bytes=byte_len,
                pdf_extracted_chars=0,
                error_reason="no_extractable_text",
            )

        if extracted_chars > self.max_extracted_chars:
            logger.warning(
                "Bundeskartellamt PDF %s exceeds max extracted chars (%d > %d)",
                pdf_url,
                extracted_chars,
                self.max_extracted_chars,
            )
            return BundeskartellamtPDFEnrichmentResult(
                success=False,
                enriched_content=base_content,
                pdf_url=pdf_url,
                pdf_pages=page_count,
                pdf_bytes=byte_len,
                pdf_extracted_chars=extracted_chars,
                error_reason="max_extracted_chars_exceeded",
            )

        # Clean combination of original HTML + Fallbericht PDF text
        combined = f"{base_content}\n\nAmtlicher Fallbericht (Volltext PDF):\n{cleaned_text}"
        return BundeskartellamtPDFEnrichmentResult(
            success=True,
            enriched_content=combined,
            pdf_url=pdf_url,
            pdf_pages=page_count,
            pdf_bytes=byte_len,
            pdf_extracted_chars=extracted_chars,
        )
