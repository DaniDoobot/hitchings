"""Extractor for the UK Competition and Markets Authority (CMA).

Ingests living case dockets from GOV.UK (cma_case and digital_markets_measure)
using an immutable milestone architecture:
- GOV.UK Search API discovery with pagination and cutoff filtering.
- Discovery across ALL substantive events in change_history within the lookback window.
- Dual content assembly:
  - Latest event: uses current body snapshot + optional matched document.
  - Historical event: uses strictly matched immutable attachment PDF + metadata (fail-closed if insufficient).
- Strict, collision-free deterministic external_id and unique fragment URLs.
- Zero network calls in tests; clean streaming bounded PDF extraction.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Any
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from app.models.entry import Entry
from app.models.source import Source
from app.providers.base import RawEntryData, ProviderError
from app.services.source_sufficiency_service import SourceSufficiencyService, SourceSufficiencyLevel
from app.services.cma_event_service import (
    is_cma_event_substantive,
    get_derivative_family,
    get_primary_family,
    format_cma_external_id,
    format_cma_event_url,
    map_cma_content_type,
    infer_cma_legal_basis,
    infer_cma_parties,
)
from app.services.cma_attachment_service import (
    CMAAttachmentService,
    AttachmentMatchResult,
    PDFExtractionResult,
)

logger = logging.getLogger(__name__)

SEARCH_API_BASE = "https://www.gov.uk/api/search.json"
CONTENT_API_BASE = "https://www.gov.uk/api/content"
USER_AGENT = "HITCHINGS/0.1 (+https://github.com/hitchings; news-observatory)"
DEFAULT_TIMEOUT = 25.0
DEFAULT_LOOKBACK_DAYS = 8


def clean_html_body(html_content: str) -> str:
    """Extract readable text from HTML body preserving structure."""
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    # Remove script and style tags
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n\n").strip()


def parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string into UTC datetime."""
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as exc:
        logger.debug("Failed to parse datetime '%s': %s", dt_str, exc)
        return None


class CMAExtractor:
    """Native extractor for CMA cases and digital markets measures."""

    def __init__(self, attachment_service: Optional[CMAAttachmentService] = None) -> None:
        self.attachment_service = attachment_service or CMAAttachmentService()

    async def extract(
        self,
        client: httpx.AsyncClient,
        source: Source,
        lookback_days: Optional[int] = None,
    ) -> list[RawEntryData]:
        """Discover and extract milestone entries from CMA within lookback window."""
        effective_lookback = lookback_days
        if effective_lookback is None and source.config and isinstance(source.config, dict):
            effective_lookback = source.config.get("lookback_days", DEFAULT_LOOKBACK_DAYS)
        if effective_lookback is None:
            effective_lookback = DEFAULT_LOOKBACK_DAYS

        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=effective_lookback)
        logger.info(
            "CMAExtractor starting extraction for '%s' (lookback=%d days, cutoff=%s)",
            source.name,
            effective_lookback,
            cutoff_dt.isoformat(),
        )

        # 1. Discover active case dossiers via GOV.UK Search API
        discovered_cases = await self.discover_cases(client, cutoff_dt)
        logger.info("CMAExtractor discovered %d active dossiers in lookback window", len(discovered_cases))

        entries: list[RawEntryData] = []

        # 2. Extract detail items and substantive milestones
        for case_item in discovered_cases:
            link = case_item.get("link")
            if not link:
                continue

            case_entries = await self.extract_case_milestones(client, source, link, cutoff_dt)
            entries.extend(case_entries)

        logger.info(
            "CMAExtractor finished: generated %d milestone entries from %d dossiers",
            len(entries),
            len(discovered_cases),
        )
        return entries

    async def discover_cases(
        self,
        client: httpx.AsyncClient,
        cutoff_dt: datetime,
    ) -> list[dict[str, Any]]:
        """Query Search API with pagination to find all dossiers updated since cutoff."""
        cutoff_date_str = cutoff_dt.strftime("%Y-%m-%d")
        discovered: list[dict[str, Any]] = []
        page_size = 100
        start = 0

        while True:
            params = [
                ("filter_organisations", "competition-and-markets-authority"),
                ("filter_content_store_document_type", "cma_case"),
                ("filter_content_store_document_type", "digital_markets_measure"),
                ("filter_public_timestamp", f"from:{cutoff_date_str}"),
                ("order", "-public_timestamp"),
                ("count", str(page_size)),
                ("start", str(start)),
                ("fields", "title,link,public_timestamp,content_id,content_store_document_type,description"),
            ]
            search_url = f"{SEARCH_API_BASE}?{urlencode(params)}"

            try:
                resp = await client.get(search_url, timeout=DEFAULT_TIMEOUT)
                if resp.status_code != 200:
                    logger.warning("Search API returned HTTP %d for URL %s", resp.status_code, search_url)
                    break

                data = resp.json()
                results = data.get("results", [])
                total = data.get("total", 0)

                if not results:
                    break

                for item in results:
                    pub_ts = parse_iso_datetime(item.get("public_timestamp"))
                    if pub_ts and pub_ts < cutoff_dt:
                        # Reached items strictly before cutoff
                        return discovered
                    discovered.append(item)

                start += len(results)
                if start >= total:
                    break

            except Exception as exc:
                logger.error("Error querying Search API at start=%d: %s", start, exc)
                break

        return discovered

    async def extract_case_milestones(
        self,
        client: httpx.AsyncClient,
        source: Source,
        base_path: str,
        cutoff_dt: datetime,
    ) -> list[RawEntryData]:
        """Extract substantive milestone entries from a single case docket."""
        clean_path = base_path if base_path.startswith("/") else f"/{base_path}"
        content_url = f"{CONTENT_API_BASE}{clean_path}"

        try:
            resp = await client.get(content_url, timeout=DEFAULT_TIMEOUT)
            if resp.status_code != 200:
                logger.warning("Content API returned HTTP %d for %s", resp.status_code, content_url)
                return []
            cdata = resp.json()
        except Exception as exc:
            logger.error("Failed to fetch Content API for %s: %s", content_url, exc)
            return []

        case_title = cdata.get("title") or "CMA Case"
        content_id = cdata.get("content_id") or ""
        details = cdata.get("details", {})
        metadata = details.get("metadata", {})
        raw_case_type = metadata.get("case_type")
        if isinstance(raw_case_type, list) and raw_case_type:
            raw_case_type = raw_case_type[0]
        case_type_str = str(raw_case_type) if raw_case_type else None

        document_type = cdata.get("content_store_document_type") or cdata.get("document_type")
        change_history = details.get("change_history", [])
        attachments = details.get("attachments", [])
        raw_body = details.get("body", "")
        clean_body = clean_html_body(raw_body)
        case_public_updated_at = cdata.get("public_updated_at")

        mapped_content_type = map_cma_content_type(case_type_str, document_type)
        if mapped_content_type is None:
            logger.info("Skipping case outside competition scope: '%s' (case_type=%s, doc_type=%s)", case_title, case_type_str, document_type)
            return []

        legal_basis, legal_basis_inferred = infer_cma_legal_basis(mapped_content_type)
        parties, parties_inferred = infer_cma_parties(case_title, mapped_content_type)

        entries: list[RawEntryData] = []

        # 1. Pre-filter substantive events within lookback window
        valid_events = []
        for event_idx, event in enumerate(change_history):
            event_ts_str = event.get("public_timestamp")
            event_note = (event.get("note") or "").strip()
            if not event_ts_str or not event_note:
                continue

            event_dt = parse_iso_datetime(event_ts_str)
            if not event_dt or event_dt < cutoff_dt:
                continue

            if not is_cma_event_substantive(event_note):
                logger.debug("Skipping administrative/non-substantive CMA event: '%s'", event_note)
                continue

            valid_events.append((event_idx, event, event_dt, event_note, event_ts_str))

        # 2. Identify primary milestone families present in this window
        primary_families_present = {
            fam for _, _, _, note, _ in valid_events
            if (fam := get_primary_family(note)) is not None
        }

        # 3. Process candidate events, suppressing derivative summaries if primary exists
        for event_idx, event, event_dt, event_note, event_ts_str in valid_events:
            deriv_fam = get_derivative_family(event_note)
            if deriv_fam and deriv_fam in primary_families_present:
                logger.info(
                    "Skipping derivative summary event '%s' because primary milestone '%s' exists in the same window for '%s'",
                    event_note, deriv_fam, case_title,
                )
                continue

            # Determine whether this is the latest/current event
            is_latest = (event_idx == 0)
            if not is_latest and case_public_updated_at:
                upd_dt = parse_iso_datetime(case_public_updated_at)
                if upd_dt and abs((upd_dt - event_dt).total_seconds()) < 60:
                    is_latest = True

            # Match attachment strictly (fail-closed)
            match_res: AttachmentMatchResult = self.attachment_service.match_event_attachment(
                event_note=event_note,
                event_dt=event_dt,
                attachments=attachments,
            )

            pdf_extract: Optional[PDFExtractionResult] = None
            primary_att = match_res.primary_attachment
            if match_res.level in ("EXACT", "STRONG") and primary_att and primary_att.get("url"):
                pdf_bytes, dl_err = await self.attachment_service.download_pdf_bounded(
                    client=client,
                    pdf_url=primary_att["url"],
                )
                if pdf_bytes and not dl_err:
                    pdf_extract = self.attachment_service.extract_pdf_text_bounded(pdf_bytes)

            # Assemble content
            header_lines = [
                f"Case: {case_title}",
                f"Event: {event_note}",
                f"Date: {event_dt.strftime('%Y-%m-%d')}",
                f"Area: {mapped_content_type}",
            ]
            if legal_basis:
                header_lines.append(f"Legal Basis: {legal_basis}")
            if parties:
                header_lines.append(f"Parties: {', '.join(parties)}")
            header_text = "\n".join(header_lines)

            if is_latest:
                # Latest event: current body snapshot is valid
                content_parts = [header_text, "--- CASE SUMMARY & PROCEEDINGS ---", clean_body]
                if pdf_extract and pdf_extract.text:
                    doc_title = primary_att.get("title", "Official Document")
                    content_parts.extend(["--- OFFICIAL DOCUMENT EXTRACT ({}) ---".format(doc_title), pdf_extract.text])
                full_content = "\n\n".join(part for part in content_parts if part).strip()
            else:
                # Historical event: strictly NO current body. Only immutable matched PDF extract + metadata.
                if pdf_extract and pdf_extract.text:
                    doc_title = primary_att.get("title", "Official Document")
                    content_parts = [
                        header_text,
                        f"--- OFFICIAL EVENT DOCUMENT ({doc_title}) ---",
                        pdf_extract.text,
                    ]
                    full_content = "\n\n".join(content_parts).strip()
                else:
                    # Historical event without matched immutable document
                    full_content = header_text

            # Assess sufficiency
            transient_entry = Entry(
                title=f"{case_title} - {event_note}",
                content=full_content,
                source=source,
                raw_metadata={"content_type": mapped_content_type},
            )
            sufficiency = SourceSufficiencyService.assess(transient_entry)

            # Fail-closed guard for historical events: if not enough immutable text, skip!
            if not is_latest and sufficiency.level in (SourceSufficiencyLevel.PARTIAL, SourceSufficiencyLevel.INSUFFICIENT):
                logger.info(
                    "Skipping historical event without sufficient immutable document: '%s' (%s, sufficiency=%s, chars=%d)",
                    case_title, event_note, sufficiency.level.value, len(full_content)
                )
                continue

            # Construct collision-free identity and URLs
            ext_id = format_cma_external_id(content_id or clean_path, event_dt, event_note)
            event_url = format_cma_event_url(clean_path, event_dt, event_note)
            excerpt = full_content[:300].replace("\n", " ").strip()

            raw_meta: dict[str, Any] = {
                "case_url": f"https://www.gov.uk{clean_path}",
                "case_title": case_title,
                "case_content_id": content_id,
                "case_type_raw": case_type_str,
                "document_type_raw": document_type,
                "event_note": event_note,
                "event_timestamp": event_ts_str,
                "is_latest_event": is_latest,
                "attachment_match_level": match_res.level,
                "attachment_match_reason": match_res.reason,
                "source_sufficiency": sufficiency.level.value,
                "sufficiency_chars": len(full_content),
            }

            if legal_basis:
                raw_meta["legal_basis"] = legal_basis
                raw_meta["legal_basis_inferred"] = legal_basis_inferred
            if parties:
                raw_meta["parties"] = parties
                raw_meta["parties_inferred"] = parties_inferred

            if primary_att:
                raw_meta["attachment_url"] = primary_att.get("url")
                raw_meta["attachment_title"] = primary_att.get("title")
                raw_meta["attachment_content_id"] = primary_att.get("content_id")

            if match_res.related_attachments:
                raw_meta["related_attachments"] = [
                    {"title": a.get("title"), "url": a.get("url")}
                    for a in match_res.related_attachments
                ]

            if pdf_extract:
                raw_meta["pdf_total_pages"] = pdf_extract.total_pages
                raw_meta["pdf_pages_extracted"] = pdf_extract.pages_extracted
                raw_meta["pdf_bytes"] = pdf_extract.bytes_count
                raw_meta["pdf_extracted_chars"] = pdf_extract.extracted_chars
                raw_meta["pdf_truncated"] = pdf_extract.truncated
                if pdf_extract.error:
                    raw_meta["pdf_error"] = pdf_extract.error

            entry_data = RawEntryData(
                source_name=source.name,
                title=f"{case_title} - {event_note}",
                url=event_url,
                content=full_content,
                excerpt=excerpt,
                external_id=ext_id,
                published_at=event_dt,
                language="en",
                content_type=mapped_content_type,
                raw_metadata=raw_meta,
            )
            entries.append(entry_data)

        return entries
