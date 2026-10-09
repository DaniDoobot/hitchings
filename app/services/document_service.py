"""Documentary Consultation Service (BLOQUE 11B).

Specialized read-only service for querying and inspecting the documentary archive
(Entry items) alongside their associated AI intelligence when available.

Guarantees:
- Strictly read-only: 0 Gemini calls, 0 scraping requests.
- Anti-N+1: uses selectinload for source, tracked entity, analyses, topics and calls.
- Fail-safe: entries without completed analysis are cleanly returned with has_analysis=False.
- Canonical topics: canonicalizes matrix topics avoiding parent/child duplication.
- Secure filtering: parameter whitelists and safe text searching.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional, Literal, Sequence

from sqlalchemy import select, func, or_, desc, asc
from sqlalchemy.orm import Session, selectinload

from app.models.entry import Entry
from app.models.source import Source
from app.models.tracking import TrackedEntity, TrackingMatrix
from app.models.analysis import EntryAnalysis, EntryAnalysisTopic
from app.schemas.document import (
    DocumentAnalysisSummary,
    DocumentDetail,
    DocumentListItem,
    DocumentListResponse,
    DocumentSourceRef,
)
from app.schemas.observatory import (
    ObservatoryEvidence,
    ObservatoryTopicItem,
)
from app.services.current_analysis_service import select_current_analysis
from app.services.topic_canonicalization_service import (
    build_topic_hierarchy,
    canonicalize_analysis_topics,
)
from app.services.observatory_query_service import _extract_evidence, to_utc_datetime

VALID_SORT_FIELDS = {"published_at", "captured_at", "title"}
VALID_SORT_ORDERS = {"asc", "desc"}


def _build_source_ref(source: Optional[Source]) -> DocumentSourceRef:
    """Build DocumentSourceRef from a loaded Source relationship."""
    if not source:
        return DocumentSourceRef(
            id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            name="Desconocida",
        )
    tracked_name = None
    if source.tracked_entity:
        tracked_name = source.tracked_entity.display_name

    return DocumentSourceRef(
        id=source.id,
        name=source.name,
        type=source.type,
        category=source.category,
        tracked_entity_name=tracked_name,
    )


def _build_analysis_summary(
    analysis: Optional[EntryAnalysis],
    hierarchy: Optional[TopicHierarchy],
) -> Optional[DocumentAnalysisSummary]:
    """Build DocumentAnalysisSummary from an EntryAnalysis if present and valid."""
    if not analysis:
        return None

    # Canonicalize topics if hierarchy is present
    canonical_topics: list[ObservatoryTopicItem] = []
    canonical_primary: Optional[ObservatoryTopicItem] = None

    if hierarchy:
        canon_res = canonicalize_analysis_topics(analysis.topics or [], hierarchy=hierarchy)
        for t in canon_res.canonical_topics:
            canonical_topics.append(ObservatoryTopicItem(code=t.topic_code, name=t.topic_name))
        if canon_res.canonical_primary:
            canonical_primary = ObservatoryTopicItem(
                code=canon_res.canonical_primary.topic_code,
                name=canon_res.canonical_primary.topic_name,
            )
    else:
        # Fallback to direct analysis topics
        for eat in getattr(analysis, "topics", []) or []:
            if eat.topic:
                item = ObservatoryTopicItem(code=eat.topic.code, name=eat.topic.name)
                canonical_topics.append(item)
                if eat.is_primary and not canonical_primary:
                    canonical_primary = item

    return DocumentAnalysisSummary(
        id=analysis.id,
        status=analysis.status,
        relevance_status=analysis.relevance_status,
        relevance_score=analysis.relevance_score,
        confidence=analysis.confidence,
        summary=analysis.summary,
        key_points=analysis.key_points or [],
        canonical_topics=canonical_topics,
        canonical_primary_topic=canonical_primary,
        analyzed_at=to_utc_datetime(analysis.completed_at or analysis.created_at),
    )


def list_documents(
    db: Session,
    limit: int = 20,
    offset: int = 0,
    sort_by: str = "published_at",
    sort_order: str = "desc",
    q: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    source_id: Optional[uuid.UUID] = None,
    origin_category: Optional[str] = None,
    has_analysis: Optional[Literal["all", "with_analysis", "without_analysis"]] = "all",
    relevance_status: Optional[str] = None,
) -> DocumentListResponse:
    """Query documents with filtering and pagination while avoiding N+1 queries."""
    if sort_by not in VALID_SORT_FIELDS:
        sort_by = "published_at"
    if sort_order not in VALID_SORT_ORDERS:
        sort_order = "desc"

    # Pre-build topic hierarchy once
    hierarchy = build_topic_hierarchy(db)

    stmt = select(Entry).options(
        selectinload(Entry.source).selectinload(Source.tracked_entity),
        selectinload(Entry.analyses).selectinload(EntryAnalysis.topics).selectinload(EntryAnalysisTopic.topic),
    )

    # Date filters
    if date_from is not None:
        dt_start = datetime(date_from.year, date_from.month, date_from.day, 0, 0, 0, tzinfo=timezone.utc)
        stmt = stmt.where(Entry.published_at >= dt_start)
    if date_to is not None:
        dt_end = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
        stmt = stmt.where(Entry.published_at <= dt_end)

    # Source filter
    if source_id is not None:
        stmt = stmt.where(Entry.source_id == source_id)

    # Text search
    if q and q.strip():
        search_pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Entry.title.ilike(search_pattern),
                Entry.content.ilike(search_pattern),
                Entry.excerpt.ilike(search_pattern),
                Entry.author.ilike(search_pattern),
            )
        )

    # Order
    order_col = Entry.published_at if sort_by == "published_at" else (
        Entry.title if sort_by == "title" else Entry.captured_at
    )
    if sort_order == "desc":
        stmt = stmt.order_by(order_col.desc().nullslast(), Entry.captured_at.desc())
    else:
        stmt = stmt.order_by(order_col.asc().nullsfirst(), Entry.captured_at.asc())

    # Execute query
    all_entries = list(db.execute(stmt).scalars().all())

    # In-memory post filtering for properties & analysis attributes
    filtered_items: list[DocumentListItem] = []

    for entry in all_entries:
        # Category check
        if origin_category and origin_category != "all":
            if entry.source_origin_category != origin_category:
                continue

        # Resolve current analysis
        current_ana = select_current_analysis(entry, analyses=list(entry.analyses))
        has_current_ana = current_ana is not None and current_ana.status == "completed"

        # Filter by has_analysis
        if has_analysis == "with_analysis" and not has_current_ana:
            continue
        if has_analysis == "without_analysis" and has_current_ana:
            continue

        # Filter by relevance_status
        if relevance_status:
            if not current_ana or current_ana.relevance_status != relevance_status:
                continue

        source_ref = _build_source_ref(entry.source)
        analysis_summary = _build_analysis_summary(current_ana, hierarchy) if has_current_ana else None

        filtered_items.append(
            DocumentListItem(
                id=entry.id,
                title=entry.title,
                url=entry.url,
                canonical_url=entry.canonical_url,
                author=entry.author,
                published_at=to_utc_datetime(entry.published_at),
                captured_at=to_utc_datetime(entry.captured_at),
                content_type=entry.content_type,
                language=entry.language,
                source=source_ref,
                is_linkedin=entry.is_linkedin,
                source_origin_category=entry.source_origin_category,
                excerpt=entry.excerpt,
                has_analysis=has_current_ana,
                analysis=analysis_summary,
            )
        )

    total_count = len(filtered_items)
    paginated_items = filtered_items[offset : offset + limit]

    return DocumentListResponse(
        items=paginated_items,
        total=total_count,
        limit=limit,
        offset=offset,
    )


def get_document_detail(db: Session, document_id: uuid.UUID) -> Optional[DocumentDetail]:
    """Retrieve full detail of a document including full text and evidence if analyzed."""
    hierarchy = build_topic_hierarchy(db)

    stmt = (
        select(Entry)
        .where(Entry.id == document_id)
        .options(
            selectinload(Entry.source).selectinload(Source.tracked_entity),
            selectinload(Entry.analyses).selectinload(EntryAnalysis.topics).selectinload(EntryAnalysisTopic.topic),
            selectinload(Entry.analyses).selectinload(EntryAnalysis.calls),
        )
    )

    entry = db.execute(stmt).scalars().first()
    if not entry:
        return None

    current_ana = select_current_analysis(entry, analyses=list(entry.analyses))
    has_current_ana = current_ana is not None and current_ana.status == "completed"

    source_ref = _build_source_ref(entry.source)
    analysis_summary = _build_analysis_summary(current_ana, hierarchy) if has_current_ana else None
    evidence: Optional[ObservatoryEvidence] = None

    if has_current_ana and current_ana:
        evidence = _extract_evidence(current_ana)

    return DocumentDetail(
        id=entry.id,
        title=entry.title,
        url=entry.url,
        canonical_url=entry.canonical_url,
        author=entry.author,
        published_at=to_utc_datetime(entry.published_at),
        captured_at=to_utc_datetime(entry.captured_at),
        content_type=entry.content_type,
        language=entry.language,
        source=source_ref,
        is_linkedin=entry.is_linkedin,
        source_origin_category=entry.source_origin_category,
        content=entry.content,
        excerpt=entry.excerpt,
        raw_metadata=entry.raw_metadata,
        has_analysis=has_current_ana,
        analysis=analysis_summary,
        evidence=evidence,
    )
