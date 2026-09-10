"""Read-only 90-day source discovery preview for Geradin, DMA, and OECD (Bloque 12F).

Performs real discovery against the three target sources, evaluates deduplication
against the database in STRICT READ-ONLY mode, computes content sufficiency and
AI analysis eligibility for new candidates, and outputs actionable metrics with
ZERO database mutations and ZERO Gemini calls.

Usage:
    # Run all 3 sources with 90-day lookback (default)
    python -m scripts.preview_source_discovery --lookback-days 90

    # Filter to a specific source
    python -m scripts.preview_source_discovery --source geradin --lookback-days 90
    python -m scripts.preview_source_discovery --source dma --lookback-days 90
    python -m scripts.preview_source_discovery --source oecd --lookback-days 90
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from sqlalchemy import event, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.url_utils import (
    domains_belong_to_same_site,
    extract_publisher_domain,
    normalize_title,
    normalize_url,
)
from app.db.session import SessionLocal
from app.models.analysis import AnalysisCall, EntryAnalysis
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun
from app.models.source import Source, SourceType
from app.providers.base import RawEntryData
from app.providers.direct_web.adapters.geradin_partners import GeradinPartnersAdapter
from app.providers.direct_web.models import DirectWebArticle, DiscoveredItem
from app.providers.extractors.european_commission_dma import EuropeanCommissionDMAExtractor
from app.providers.extractors.oecd_competition import (
    OECDCompetitionExtractor,
    DEFAULT_OECD_ISSN,
)
from app.services.incremental_analysis_planner import IncrementalAnalysisPlanner
from app.services.ingestion_service import compute_content_hash, compute_ingestion_dedupe_hash
from app.services.source_sufficiency_service import (
    SourceSufficiencyLevel,
    SourceSufficiencyService,
)

logger = logging.getLogger("preview_source_discovery")

GERADIN_SOURCE_NAME = "Geradin Partners - EU Competition & Litigation"
DMA_SOURCE_NAME = "European Commission - Digital Markets Act"
OECD_SOURCE_NAME = "OECD - Competition Law and Policy"

TARGET_SOURCE_NAMES = [
    GERADIN_SOURCE_NAME,
    DMA_SOURCE_NAME,
    OECD_SOURCE_NAME,
]


# ==============================================================================
# READ-ONLY SESSION HARDENING (POSTGRESQL + ORM + RAW SQL)
# ==============================================================================

def configure_read_only_session(db: Session) -> list[Callable[[], None]]:
    """Enforce fail-closed read-only protection at transaction, ORM, and cursor levels.

    Guarantees:
    1. PostgreSQL: Issues 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
       as the very first statement of the transaction before any SELECT, guaranteeing
       an immutable point-in-time snapshot and engine-level rejection of any write attempt.
    2. Raw SQL Blocker: Intercepts before_cursor_execute to reject direct mutating SQL
       statements (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE) across all dialects.
    3. ORM Flush Blocker: Intercepts before_flush to fail-closed if session.new,
       session.dirty, or session.deleted are non-empty.
    """
    cleanups: list[Callable[[], None]] = []
    bind = db.get_bind()
    dialect_name = getattr(bind.dialect, "name", "") if bind else ""

    # 1. PostgreSQL transaction-level isolation & read-only enforcement
    if dialect_name == "postgresql":
        logger.info("Enforcing PostgreSQL transaction: REPEATABLE READ, READ ONLY")
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))

    # 2. Raw SQL cursor execution listener (blocks direct mutating SQL across all dialects)
    def block_mutating_sql(conn, cursor, statement, parameters, context, executemany):
        cleaned = statement.strip().upper()
        if any(cleaned.startswith(kw) for kw in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE")):
            raise RuntimeError(f"READ-ONLY VIOLATION: Mutating SQL execution blocked: {statement[:80]}")
        return statement, parameters

    if bind is not None:
        event.listen(bind, "before_cursor_execute", block_mutating_sql)
        cleanups.append(lambda: event.remove(bind, "before_cursor_execute", block_mutating_sql) if event.contains(bind, "before_cursor_execute", block_mutating_sql) else None)

    # 3. ORM flush listener
    def fail_on_any_flush(session, flush_context, instances):
        if session.new or session.dirty or session.deleted:
            raise RuntimeError(
                f"READ-ONLY VIOLATION: Preview attempted to modify database! "
                f"new={len(session.new)}, dirty={len(session.dirty)}, deleted={len(session.deleted)}"
            )

    event.listen(db, "before_flush", fail_on_any_flush)
    cleanups.append(lambda: event.remove(db, "before_flush", fail_on_any_flush) if event.contains(db, "before_flush", fail_on_any_flush) else None)

    return cleanups


@contextmanager
def read_only_session_scope(db: Session):
    """Context manager wrapping a Session with guaranteed read-only protections and cleanup."""
    cleanups = configure_read_only_session(db)
    try:
        yield db
    finally:
        for cleanup in cleanups:
            try:
                cleanup()
            except Exception as e:
                logger.debug("Error cleaning up read-only listener: %s", e)
        db.rollback()
        db.close()


# ==============================================================================
# READ-ONLY DEDUPLICATION INSPECTOR
# ==============================================================================

class ReadOnlyDeduplicationResult(BaseModel):
    """Result of pure read-only deduplication check against the database."""

    is_duplicate: bool
    duplicate_reason: Optional[str] = None  # external_id, canonical_url, presscorner_ref, content_hash, google_news_match
    matched_entry_id: Optional[str] = None
    matched_title: Optional[str] = None


class ReadOnlyDeduplicationInspector:
    """Evaluates candidate items against existing entries with zero database mutations."""

    @staticmethod
    def check_dma_item(
        db: Session,
        source_id: uuid.UUID,
        raw: RawEntryData,
    ) -> ReadOnlyDeduplicationResult:
        """Evaluate European Commission DMA candidate item against database."""
        meta = raw.raw_metadata or {}
        presscorner_ref = meta.get("presscorner_ref")
        presscorner_url = meta.get("presscorner_url")

        # 1. Cross-source Press Corner deduplication
        if presscorner_ref or presscorner_url:
            ref_conditions = []
            if presscorner_url:
                ref_conditions.append(Entry.url == presscorner_url)
                ref_conditions.append(Entry.canonical_url == presscorner_url)
            if presscorner_ref:
                ref_slug = presscorner_ref.lower().replace("/", "_")
                ref_conditions.append(Entry.url.ilike(f"%{ref_slug}%"))
                ref_conditions.append(Entry.canonical_url.ilike(f"%{ref_slug}%"))
                ref_conditions.append(Entry.external_id.ilike(f"%{ref_slug}%"))

            existing_cross = db.execute(
                select(Entry).where(or_(*ref_conditions)).limit(1)
            ).scalar_one_or_none()

            if existing_cross:
                return ReadOnlyDeduplicationResult(
                    is_duplicate=True,
                    duplicate_reason="presscorner_ref",
                    matched_entry_id=str(existing_cross.id),
                    matched_title=existing_cross.title,
                )

        # 2. Within-source deduplication
        c_hash = compute_content_hash(raw.title, raw.url, raw.excerpt)
        resolved_canonical_url = presscorner_url if presscorner_url else raw.url

        dup_conditions = [
            Entry.url == raw.url,
            Entry.canonical_url == raw.url,
            Entry.canonical_url == resolved_canonical_url,
            Entry.content_hash == c_hash,
        ]
        if raw.external_id:
            dup_conditions.append(Entry.external_id == raw.external_id)

        existing = db.execute(
            select(Entry).where(
                Entry.source_id == source_id,
                or_(*dup_conditions),
            ).limit(1)
        ).scalar_one_or_none()

        if existing:
            reason = "external_id" if (raw.external_id and existing.external_id == raw.external_id) else (
                "content_hash" if existing.content_hash == c_hash else "canonical_url"
            )
            return ReadOnlyDeduplicationResult(
                is_duplicate=True,
                duplicate_reason=reason,
                matched_entry_id=str(existing.id),
                matched_title=existing.title,
            )

        return ReadOnlyDeduplicationResult(is_duplicate=False)

    @staticmethod
    def check_oecd_item(
        db: Session,
        source_id: uuid.UUID,
        raw: RawEntryData,
    ) -> ReadOnlyDeduplicationResult:
        """Evaluate OECD Competition candidate publication against database."""
        doi = raw.external_id or (raw.raw_metadata or {}).get("doi")
        resource_url = (raw.raw_metadata or {}).get("resource_url")
        c_hash = compute_content_hash(raw.title, raw.url, raw.excerpt)

        # 1. Official DOI check (global across observatorio)
        if doi:
            existing_doi = db.execute(
                select(Entry).where(Entry.external_id == doi).limit(1)
            ).scalar_one_or_none()
            if existing_doi:
                return ReadOnlyDeduplicationResult(
                    is_duplicate=True,
                    duplicate_reason="external_id",
                    matched_entry_id=str(existing_doi.id),
                    matched_title=existing_doi.title,
                )

        # 2. Canonical URL or Resource URL check
        url_conds = [Entry.url == raw.url, Entry.canonical_url == raw.url]
        if resource_url:
            url_conds.extend([Entry.url == resource_url, Entry.canonical_url == resource_url])

        existing_url = db.execute(
            select(Entry).where(or_(*url_conds)).limit(1)
        ).scalar_one_or_none()
        if existing_url:
            return ReadOnlyDeduplicationResult(
                is_duplicate=True,
                duplicate_reason="canonical_url",
                matched_entry_id=str(existing_url.id),
                matched_title=existing_url.title,
            )

        # 3. Content hash check within source
        existing_hash = db.execute(
            select(Entry).where(
                Entry.source_id == source_id,
                Entry.content_hash == c_hash,
            ).limit(1)
        ).scalar_one_or_none()
        if existing_hash:
            return ReadOnlyDeduplicationResult(
                is_duplicate=True,
                duplicate_reason="content_hash",
                matched_entry_id=str(existing_hash.id),
                matched_title=existing_hash.title,
            )

        return ReadOnlyDeduplicationResult(is_duplicate=False)

    @staticmethod
    def check_geradin_item(
        db: Session,
        source_id: uuid.UUID,
        url: str,
        canonical_url: Optional[str],
        title: str,
        excerpt: Optional[str],
        existing_gn_entries: Optional[list[Entry]] = None,
    ) -> ReadOnlyDeduplicationResult:
        """Evaluate Geradin Partners direct web candidate against database."""
        norm_url = normalize_url(url)
        norm_canonical = normalize_url(canonical_url) if canonical_url else None
        c_hash = compute_ingestion_dedupe_hash(title, canonical_url or url, excerpt)

        # 1. URL / Canonical URL check
        variants = set()
        for u in (url, norm_url, canonical_url, norm_canonical):
            if u:
                variants.add(u)
                variants.add(u.rstrip("/") + "/")
                variants.add(u.rstrip("/"))
        url_conds = []
        for v in variants:
            url_conds.append(Entry.url == v)
            url_conds.append(Entry.canonical_url == v)
            url_conds.append(Entry.external_id == v)

        existing = db.execute(
            select(Entry).where(
                Entry.source_id == source_id,
                or_(*url_conds),
            ).limit(1)
        ).scalar_one_or_none()

        if existing:
            return ReadOnlyDeduplicationResult(
                is_duplicate=True,
                duplicate_reason="canonical_url",
                matched_entry_id=str(existing.id),
                matched_title=existing.title,
            )

        # 2. Content hash check
        existing_hash = db.execute(
            select(Entry).where(
                Entry.source_id == source_id,
                Entry.content_hash == c_hash,
            ).limit(1)
        ).scalar_one_or_none()

        if existing_hash:
            return ReadOnlyDeduplicationResult(
                is_duplicate=True,
                duplicate_reason="content_hash",
                matched_entry_id=str(existing_hash.id),
                matched_title=existing_hash.title,
            )

        # 3. Google News cross-match (Bloque 9B)
        if existing_gn_entries is not None:
            norm_article_title = normalize_title(title)
            article_domain = extract_publisher_domain(url) or "geradinpartners.com"
            for gn_entry in existing_gn_entries:
                gn_pub_domain = (gn_entry.raw_metadata or {}).get("publisher_domain") or extract_publisher_domain(gn_entry.url)
                if domains_belong_to_same_site(article_domain, gn_pub_domain):
                    if normalize_title(gn_entry.title) == norm_article_title:
                        return ReadOnlyDeduplicationResult(
                            is_duplicate=True,
                            duplicate_reason="google_news_match",
                            matched_entry_id=str(gn_entry.id),
                            matched_title=gn_entry.title,
                        )

        return ReadOnlyDeduplicationResult(is_duplicate=False)


# ==============================================================================
# PREVIEW DATA MODELS
# ==============================================================================

class PreviewCandidateItem(BaseModel):
    """Candidate item evaluated during preview."""

    date: Optional[str] = None
    source_name: str
    title: str
    url: str
    is_duplicate: bool
    duplicate_reason: Optional[str] = None
    sufficiency: str = "insufficient"  # full, partial, insufficient
    content_chars: int = 0
    eligible_for_analysis: bool = False


class SourcePreviewSummary(BaseModel):
    """Summary of preview execution for a single source."""

    source_name: str
    discovered_total: int = 0
    inside_lookback: int = 0
    excluded_editorially: int = 0
    excluded_future: int = 0
    duplicates: int = 0
    new_candidates: int = 0
    full_count: int = 0
    partial_count: int = 0
    insufficient_count: int = 0
    eligible_for_analysis: int = 0
    new_candidate_chars: int = 0
    eligible_input_chars: int = 0
    estimated_input_chars: int = 0  # retained for backwards compatibility
    new_items: list[PreviewCandidateItem] = Field(default_factory=list)


class GlobalPreviewReport(BaseModel):
    """Consolidated preview report across all evaluated sources."""

    lookback_days: int
    cutoff_date: str
    reference_date: str
    sources_summaries: list[SourcePreviewSummary] = Field(default_factory=list)
    total_discovered: int = 0
    total_duplicates: int = 0
    total_new: int = 0
    total_full: int = 0
    total_partial: int = 0
    total_insufficient: int = 0
    potential_gemini_analyses: int = 0
    total_new_candidate_chars: int = 0
    total_eligible_input_chars: int = 0
    total_estimated_input_chars: int = 0  # retained for backwards compatibility
    new_candidates_table: list[PreviewCandidateItem] = Field(default_factory=list)


# ==============================================================================
# PREVIEW SERVICE
# ==============================================================================

class SourceDiscoveryPreviewService:
    """Orchestrates read-only 90-day discovery preview for target sources."""

    def __init__(self, db: Session, now: Optional[datetime] = None) -> None:
        self.db = db
        self.now = now or datetime.now(timezone.utc)
        self.current_date = self.now.date()

    def run_preview(
        self,
        lookback_days: int = 90,
        source_filter: Optional[str] = None,
        async_client: Optional[httpx.AsyncClient] = None,
        sync_client: Optional[httpx.Client] = None,
    ) -> GlobalPreviewReport:
        """Execute discovery preview across target sources."""
        cutoff_date = (self.now - timedelta(days=lookback_days)).date()

        report = GlobalPreviewReport(
            lookback_days=lookback_days,
            cutoff_date=cutoff_date.isoformat(),
            reference_date=self.current_date.isoformat(),
        )

        sources_to_run = self._resolve_sources(source_filter)

        for source in sources_to_run:
            if "geradin" in source.name.lower():
                summary = self._preview_geradin(
                    source=source,
                    cutoff_date=cutoff_date,
                    sync_client=sync_client,
                )
            elif "digital markets act" in source.name.lower() or "dma" in source.name.lower():
                summary = asyncio.run(
                    self._preview_dma_async(
                        source=source,
                        cutoff_date=cutoff_date,
                        lookback_days=lookback_days,
                        async_client=async_client,
                    )
                )
            elif "oecd" in source.name.lower():
                summary = asyncio.run(
                    self._preview_oecd_async(
                        source=source,
                        cutoff_date=cutoff_date,
                        lookback_days=lookback_days,
                        async_client=async_client,
                    )
                )
            else:
                logger.warning("Unrecognized target source: %s", source.name)
                continue

            report.sources_summaries.append(summary)
            report.total_discovered += summary.discovered_total
            report.total_duplicates += summary.duplicates
            report.total_new += summary.new_candidates
            report.total_full += summary.full_count
            report.total_partial += summary.partial_count
            report.total_insufficient += summary.insufficient_count
            report.potential_gemini_analyses += summary.eligible_for_analysis
            report.total_new_candidate_chars += summary.new_candidate_chars
            report.total_eligible_input_chars += summary.eligible_input_chars
            report.total_estimated_input_chars += summary.estimated_input_chars
            report.new_candidates_table.extend(summary.new_items)

        return report

    def _resolve_sources(self, source_filter: Optional[str]) -> list[Source]:
        """Resolve Source entities from database or instantiate transient representations."""
        sources: list[Source] = []
        normalized_filter = (source_filter or "").strip().lower()

        for name in TARGET_SOURCE_NAMES:
            if normalized_filter:
                if normalized_filter not in name.lower():
                    # Check shortcut aliases
                    if normalized_filter == "geradin" and "geradin" not in name.lower():
                        continue
                    elif normalized_filter == "dma" and "digital markets act" not in name.lower():
                        continue
                    elif normalized_filter == "oecd" and "oecd" not in name.lower():
                        continue

            db_source = self.db.execute(
                select(Source).where(Source.name == name).limit(1)
            ).scalar_one_or_none()

            if db_source:
                sources.append(db_source)
            else:
                # Construct transient source for isolated testing environments
                sources.append(self._create_transient_source(name))

        return sources

    def _create_transient_source(self, name: str) -> Source:
        """Create fallback transient Source when database is unseeded."""
        if name == GERADIN_SOURCE_NAME:
            return Source(
                id=uuid.uuid4(),
                name=GERADIN_SOURCE_NAME,
                type=SourceType.BLOG,
                provider="native",
                url="https://www.geradinpartners.com/news/",
                config={"adapter": "geradin_partners", "listing_url": "https://www.geradinpartners.com/news/"},
                active=True,
            )
        elif name == DMA_SOURCE_NAME:
            return Source(
                id=uuid.uuid4(),
                name=DMA_SOURCE_NAME,
                type=SourceType.WEBSITE,
                provider="native",
                url="https://digital-markets-act.ec.europa.eu/news_en",
                config={"initial_fetch_limit": 50, "portal_url": "https://digital-markets-act.ec.europa.eu/"},
                active=True,
            )
        else:
            return Source(
                id=uuid.uuid4(),
                name=OECD_SOURCE_NAME,
                type=SourceType.INSTITUTIONAL,
                provider="native",
                url="https://www.oecd.org/en/topics/policy-issues/competition.html",
                config={"issn": DEFAULT_OECD_ISSN, "initial_fetch_limit": 50},
                active=True,
            )

    # --------------------------------------------------------------------------
    # 1. GERADIN PARTNERS PREVIEW
    # --------------------------------------------------------------------------
    def _preview_geradin(
        self,
        source: Source,
        cutoff_date: Any,
        sync_client: Optional[httpx.Client] = None,
    ) -> SourcePreviewSummary:
        """Execute real discovery and read-only preview for Geradin Partners."""
        adapter = GeradinPartnersAdapter()
        summary = SourcePreviewSummary(source_name=source.name)

        # Pre-load Google News entries for cross-deduplication check
        gn_entries = self.db.query(Entry).join(Source).filter(Source.type == SourceType.GOOGLE_NEWS).all()

        client = sync_client
        should_close = False
        if client is None:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            client = httpx.Client(headers=headers, timeout=15.0, follow_redirects=True)
            should_close = True

        try:
            listing_url = (source.config or {}).get("listing_url") or source.url or adapter.DEFAULT_LISTING_URL
            current_page_url = listing_url
            page_num = 1
            max_pages = 10
            reached_cutoff = False

            while page_num <= max_pages and not reached_cutoff:
                logger.info("Fetching Geradin page %d: %s", page_num, current_page_url)
                try:
                    resp = client.get(current_page_url)
                except httpx.RequestError as exc:
                    logger.error("HTTP error fetching Geradin listing: %s", exc)
                    break

                if resp.status_code != 200:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("li", class_="wp-block-post")
                if not cards:
                    break

                for card in cards:
                    summary.discovered_total += 1

                    card_a = card.find("a", class_="gp-news-post-card") or card.find("a", href=True)
                    if not card_a or not card_a.get("href"):
                        continue

                    raw_url = str(httpx.URL(current_page_url).join(card_a["href"]))
                    url = normalize_url(raw_url)
                    if not url:
                        continue

                    title_el = card.find(["h2", "h1", "h3"], class_="post-title") or card.find(["h2", "h1", "h3"])
                    title = title_el.get_text(strip=True) if title_el else ""
                    if not title:
                        continue

                    cat_span = card.find("span", class_="post-category")
                    category = cat_span.get_text(strip=True) if cat_span else "News"

                    # Editorial corporate filter
                    is_substantive, filter_reason = adapter.is_substantive_article(category, title)
                    if not is_substantive:
                        summary.excluded_editorially += 1
                        continue

                    # Date parsing & lookback check
                    time_el = card.find("time", class_="post-date") or card.find("time")
                    published_at = None
                    if time_el and time_el.get_text(strip=True):
                        from app.providers.direct_web.html_cleaner import _safe_parse_datetime
                        published_at = _safe_parse_datetime(time_el.get_text(strip=True))

                    if published_at:
                        pub_date = published_at.date()
                        if pub_date > self.current_date:
                            summary.excluded_future += 1
                            continue
                        if pub_date < cutoff_date:
                            # Reached items older than lookback
                            reached_cutoff = True
                            continue

                    summary.inside_lookback += 1

                    # Fast preliminary deduplication check by URL before detail fetch
                    fast_dedupe = ReadOnlyDeduplicationInspector.check_geradin_item(
                        db=self.db,
                        source_id=source.id,
                        url=url,
                        canonical_url=None,
                        title=title,
                        excerpt=None,
                        existing_gn_entries=gn_entries,
                    )
                    if fast_dedupe.is_duplicate:
                        summary.duplicates += 1
                        continue

                    # Fetch detail for new candidate
                    try:
                        disc_item = DiscoveredItem(
                            url=url,
                            external_id=url,
                            title=title,
                            published_at=published_at,
                            category=category,
                        )
                        raw_html = adapter.fetch_detail(disc_item, client=client)
                        article = adapter.parse_detail(raw_html, disc_item)
                    except Exception as exc:
                        logger.warning("Failed fetching detail for %s: %s", url, exc)
                        summary.insufficient_count += 1
                        continue

                    # Complete deduplication check with canonical URL & content hash
                    full_dedupe = ReadOnlyDeduplicationInspector.check_geradin_item(
                        db=self.db,
                        source_id=source.id,
                        url=article.url,
                        canonical_url=article.canonical_url,
                        title=article.title,
                        excerpt=article.excerpt,
                        existing_gn_entries=gn_entries,
                    )
                    if full_dedupe.is_duplicate:
                        summary.duplicates += 1
                        continue

                    # Item is NEW: assess sufficiency and analysis eligibility
                    summary.new_candidates += 1
                    transient_entry = Entry(
                        id=uuid.uuid4(),
                        source_id=source.id,
                        source=source,
                        external_id=article.external_id,
                        url=article.url,
                        canonical_url=article.canonical_url or article.url,
                        title=article.title,
                        content=article.content,
                        excerpt=article.excerpt,
                        author=article.author,
                        published_at=article.published_at,
                        captured_at=self.now,
                        language=article.language or "en",
                        content_type="article",
                        raw_metadata=article.raw_metadata or {},
                    )

                    suff_res = SourceSufficiencyService.assess(transient_entry)
                    suff_level = suff_res.level.value

                    if suff_level == SourceSufficiencyLevel.FULL.value:
                        summary.full_count += 1
                    elif suff_level == SourceSufficiencyLevel.PARTIAL.value:
                        summary.partial_count += 1
                    else:
                        summary.insufficient_count += 1

                    planner = IncrementalAnalysisPlanner(self.db)
                    cand = planner.evaluate_entry(transient_entry)
                    eligible = (cand.reason == "eligible")
                    if eligible:
                        summary.eligible_for_analysis += 1

                    content_len = len(article.content or "")
                    summary.new_candidate_chars += content_len
                    if eligible:
                        summary.eligible_input_chars += content_len
                    summary.estimated_input_chars += content_len

                    pub_str = article.published_at.strftime("%Y-%m-%d") if article.published_at else "Unknown"
                    summary.new_items.append(
                        PreviewCandidateItem(
                            date=pub_str,
                            source_name=source.name,
                            title=article.title,
                            url=article.url,
                            is_duplicate=False,
                            sufficiency=suff_level,
                            content_chars=content_len,
                            eligible_for_analysis=eligible,
                        )
                    )

                # Pagination: find next page link
                next_link = soup.find("a", class_="wp-block-query-pagination-next") or soup.find("a", rel="next")
                if next_link and next_link.get("href"):
                    current_page_url = str(httpx.URL(current_page_url).join(next_link["href"]))
                    page_num += 1
                else:
                    break

        finally:
            if should_close:
                client.close()

        return summary

    # --------------------------------------------------------------------------
    # 2. EUROPEAN COMMISSION DMA PREVIEW
    # --------------------------------------------------------------------------
    async def _preview_dma_async(
        self,
        source: Source,
        cutoff_date: Any,
        lookback_days: int,
        async_client: Optional[httpx.AsyncClient] = None,
    ) -> SourcePreviewSummary:
        """Execute real discovery and read-only preview for European Commission DMA."""
        extractor = EuropeanCommissionDMAExtractor()
        summary = SourcePreviewSummary(source_name=source.name)

        client = async_client
        should_close = False
        if client is None:
            headers = {"User-Agent": "HITCHINGS/0.1 (+https://github.com/hitchings; news-observatory)"}
            client = httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True)
            should_close = True

        try:
            base_url = source.url or "https://digital-markets-act.ec.europa.eu/news_en"

            # 1. Fetch listing cards covering the lookback window
            cards = await extractor._fetch_listing_cards(
                client=client,
                base_url=base_url,
                limit=100,
                lookback_days=lookback_days,
            )
            summary.discovered_total = len(cards)

            # 2. Process discovered items
            for card in cards:
                pub_dt = card.get("published_at")
                if pub_dt:
                    pub_date = pub_dt.date()
                    if pub_date > self.current_date:
                        summary.excluded_future += 1
                        continue
                    if pub_date < cutoff_date:
                        continue

                summary.inside_lookback += 1

                # Enrich item with substantive detail text
                raw = await extractor._enrich_item(client=client, card=card)

                # Deduplicate against database in read-only mode
                dedupe = ReadOnlyDeduplicationInspector.check_dma_item(
                    db=self.db,
                    source_id=source.id,
                    raw=raw,
                )
                if dedupe.is_duplicate:
                    summary.duplicates += 1
                    continue

                # Item is NEW
                summary.new_candidates += 1
                transient_entry = Entry(
                    id=uuid.uuid4(),
                    source_id=source.id,
                    source=source,
                    external_id=raw.external_id,
                    url=raw.url,
                    canonical_url=raw.url,
                    title=raw.title,
                    content=raw.content,
                    excerpt=raw.excerpt,
                    author=raw.author,
                    published_at=raw.published_at,
                    captured_at=self.now,
                    language=raw.language or "en",
                    content_type=raw.content_type or "news_article",
                    raw_metadata=raw.raw_metadata or {},
                )

                suff_res = SourceSufficiencyService.assess(transient_entry)
                suff_level = suff_res.level.value

                if suff_level == SourceSufficiencyLevel.FULL.value:
                    summary.full_count += 1
                elif suff_level == SourceSufficiencyLevel.PARTIAL.value:
                    summary.partial_count += 1
                else:
                    summary.insufficient_count += 1

                planner = IncrementalAnalysisPlanner(self.db)
                cand = planner.evaluate_entry(transient_entry)
                eligible = (cand.reason == "eligible")
                if eligible:
                    summary.eligible_for_analysis += 1

                content_len = len(raw.content or "")
                summary.new_candidate_chars += content_len
                if eligible:
                    summary.eligible_input_chars += content_len
                summary.estimated_input_chars += content_len

                pub_str = raw.published_at.strftime("%Y-%m-%d") if raw.published_at else "Unknown"
                summary.new_items.append(
                    PreviewCandidateItem(
                        date=pub_str,
                        source_name=source.name,
                        title=raw.title,
                        url=raw.url,
                        is_duplicate=False,
                        sufficiency=suff_level,
                        content_chars=content_len,
                        eligible_for_analysis=eligible,
                    )
                )

        finally:
            if should_close:
                await client.aclose()

        return summary

    # --------------------------------------------------------------------------
    # 3. OECD COMPETITION PREVIEW
    # --------------------------------------------------------------------------
    async def _preview_oecd_async(
        self,
        source: Source,
        cutoff_date: Any,
        lookback_days: int,
        async_client: Optional[httpx.AsyncClient] = None,
    ) -> SourcePreviewSummary:
        """Execute real discovery and read-only preview for OECD Competition."""
        extractor = OECDCompetitionExtractor()
        summary = SourcePreviewSummary(source_name=source.name)

        client = async_client
        should_close = False
        if client is None:
            headers = {"User-Agent": "HITCHINGS/0.1 (+https://github.com/hitchings; news-observatory)"}
            client = httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True)
            should_close = True

        try:
            config = source.config or {}
            issn = config.get("issn", DEFAULT_OECD_ISSN)

            # 1. Fetch records from Crossref API
            fetch_rows = 50
            url = f"https://api.crossref.org/works?filter=issn:{issn}&sort=published&order=desc&rows={fetch_rows}"
            headers = {
                "Accept": "application/json",
                "User-Agent": "HITCHINGS/0.1 (+https://github.com/hitchings; news-observatory; mailto:info@hitchings.eu)",
            }
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.error("Crossref API returned status %d", resp.status_code)
                return summary

            items = resp.json().get("message", {}).get("items", [])
            summary.discovered_total = len(items)

            # 2. Process records applying future date guard and lookback
            for it in items:
                pub_date = extractor.parse_crossref_date(it)
                if not pub_date:
                    continue

                pub_cal_date = pub_date.date()

                # Future date guard
                if pub_cal_date > self.current_date:
                    summary.excluded_future += 1
                    continue

                # Lookback filter
                if pub_cal_date < cutoff_date:
                    continue

                summary.inside_lookback += 1

                # Enrich publication via OpenAlex / portal
                raw = await extractor._enrich_publication(client=client, record=it)

                # Deduplicate against database in read-only mode
                dedupe = ReadOnlyDeduplicationInspector.check_oecd_item(
                    db=self.db,
                    source_id=source.id,
                    raw=raw,
                )
                if dedupe.is_duplicate:
                    summary.duplicates += 1
                    continue

                # Item is NEW
                summary.new_candidates += 1
                transient_entry = Entry(
                    id=uuid.uuid4(),
                    source_id=source.id,
                    source=source,
                    external_id=raw.external_id,
                    url=raw.url,
                    canonical_url=raw.url,
                    title=raw.title,
                    content=raw.content,
                    excerpt=raw.excerpt,
                    author=raw.author,
                    published_at=raw.published_at,
                    captured_at=self.now,
                    language=raw.language or "en",
                    content_type=raw.content_type or "report",
                    raw_metadata=raw.raw_metadata or {},
                )

                suff_res = SourceSufficiencyService.assess(transient_entry)
                suff_level = suff_res.level.value

                if suff_level == SourceSufficiencyLevel.FULL.value:
                    summary.full_count += 1
                elif suff_level == SourceSufficiencyLevel.PARTIAL.value:
                    summary.partial_count += 1
                else:
                    summary.insufficient_count += 1

                planner = IncrementalAnalysisPlanner(self.db)
                cand = planner.evaluate_entry(transient_entry)
                eligible = (cand.reason == "eligible")
                if eligible:
                    summary.eligible_for_analysis += 1

                content_len = len(raw.content or "")
                summary.new_candidate_chars += content_len
                if eligible:
                    summary.eligible_input_chars += content_len
                summary.estimated_input_chars += content_len

                pub_str = raw.published_at.strftime("%Y-%m-%d") if raw.published_at else "Unknown"
                summary.new_items.append(
                    PreviewCandidateItem(
                        date=pub_str,
                        source_name=source.name,
                        title=raw.title,
                        url=raw.url,
                        is_duplicate=False,
                        sufficiency=suff_level,
                        content_chars=content_len,
                        eligible_for_analysis=eligible,
                    )
                )

        finally:
            if should_close:
                await client.aclose()

        return summary


# ==============================================================================
# REPORT FORMATTING
# ==============================================================================

def print_preview_report(report: GlobalPreviewReport) -> None:
    """Render structured human-readable preview report to stdout."""
    print("\n" + "=" * 95)
    print("  HITCHINGS OBSERVATORY — READ-ONLY SOURCE DISCOVERY PREVIEW (BLOQUE 12F)")
    print("=" * 95)
    print(f"  Lookback Window      : {report.lookback_days} days (from {report.cutoff_date} to {report.reference_date})")
    print(f"  Mode                 : STRICT READ-ONLY (0 DB writes, 0 Gemini calls)")
    print("-" * 95)

    for summary in report.sources_summaries:
        print(f"\nSOURCE: {summary.source_name}")
        print(f"  discovered_total       : {summary.discovered_total}")
        print(f"  inside_lookback        : {summary.inside_lookback}")
        if summary.excluded_editorially > 0:
            print(f"  excluded_editorially   : {summary.excluded_editorially}")
        if summary.excluded_future > 0:
            print(f"  excluded_future        : {summary.excluded_future}")
        print(f"  duplicates             : {summary.duplicates}")
        print(f"  new_candidates         : {summary.new_candidates}")
        print(f"  FULL                   : {summary.full_count}")
        print(f"  PARTIAL                : {summary.partial_count}")
        print(f"  INSUFFICIENT           : {summary.insufficient_count}")
        print(f"  eligible_for_analysis  : {summary.eligible_for_analysis}")
        print(f"  new_candidate_chars    : {summary.new_candidate_chars}")
        print(f"  eligible_input_chars   : {summary.eligible_input_chars}")

    print("\n" + "=" * 95)
    print("  TOTAL GLOBAL")
    print("=" * 95)
    print(f"  discovered                 : {report.total_discovered}")
    print(f"  duplicates                 : {report.total_duplicates}")
    print(f"  new                        : {report.total_new}")
    print(f"  FULL                       : {report.total_full}")
    print(f"  PARTIAL                    : {report.total_partial}")
    print(f"  INSUFFICIENT               : {report.total_insufficient}")
    print(f"  potential_gemini_analyses  : {report.potential_gemini_analyses}")
    print(f"  new_candidate_chars        : {report.total_new_candidate_chars}")
    print(f"  eligible_input_chars       : {report.total_eligible_input_chars}")
    print("-" * 95)
    print("  Coste de análisis          : Se calculará formalmente durante el plan de backfill v6.")
    print("                               (0 llamadas de Gemini ejecutadas en este preview).")

    print("\n" + "=" * 95)
    print("  LISTADO RESUMIDO DE CANDIDATOS NUEVOS")
    print("=" * 95)
    if not report.new_candidates_table:
        print("  (Ningún candidato nuevo descubierto en la ventana indicada)")
    else:
        header = f"  {'FECHA':<12} | {'FUENTE':<22} | {'SUF':<8} | {'ELIG':<6} | {'TÍTULO'}"
        print(header)
        print("  " + "-" * 91)
        for item in report.new_candidates_table:
            source_abbrev = (
                "Geradin Partners" if "geradin" in item.source_name.lower() else (
                    "EC DMA" if "dma" in item.source_name.lower() or "digital" in item.source_name.lower() else "OECD"
                )
            )
            elig_str = "SÍ" if item.eligible_for_analysis else "NO"
            title_trunc = item.title[:45] + ("..." if len(item.title) > 45 else "")
            print(f"  {item.date:<12} | {source_abbrev:<22} | {item.sufficiency.upper():<8} | {elig_str:<6} | {title_trunc}")

    print("=" * 95 + "\n")


# ==============================================================================
# CLI ENTRYPOINT
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only discovery preview for 90-day backfill evaluation (Bloque 12F)"
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=90,
        help="Lookback window in days (default: 90)",
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        choices=["geradin", "dma", "oecd"],
        help="Limit preview to a specific source (default: all three)",
    )

    args = parser.parse_args()

    db = SessionLocal()
    try:
        with read_only_session_scope(db) as ro_db:
            # Pre-execution DB counts baseline (inside REPEATABLE READ snapshot)
            counts_before = {
                "entries": ro_db.query(Entry).count(),
                "entry_analyses": ro_db.query(EntryAnalysis).count(),
                "analysis_calls": ro_db.query(AnalysisCall).count(),
                "ingestion_runs": ro_db.query(IngestionRun).count(),
                "sources": ro_db.query(Source).count(),
            }

            service = SourceDiscoveryPreviewService(db=ro_db)
            report = service.run_preview(
                lookback_days=args.lookback_days,
                source_filter=args.source,
            )

            print_preview_report(report)

            # Post-execution DB counts verification (inside the same consistent snapshot)
            counts_after = {
                "entries": ro_db.query(Entry).count(),
                "entry_analyses": ro_db.query(EntryAnalysis).count(),
                "analysis_calls": ro_db.query(AnalysisCall).count(),
                "ingestion_runs": ro_db.query(IngestionRun).count(),
                "sources": ro_db.query(Source).count(),
            }

            assert counts_before == counts_after, (
                f"DATABASE MUTATION DETECTED! Before: {counts_before}, After: {counts_after}"
            )
            print("  [SEGURIDAD] Verificación read-only: 0 escrituras en DB confirmadas (invariantes idénticos).")
            return 0

    except Exception as exc:
        logger.error("Error executing preview: %s", exc, exc_info=True)
        print(f"\nERROR: Falló el preview de discovery: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
