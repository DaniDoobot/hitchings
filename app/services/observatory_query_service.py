"""Observatory Query Service (BLOQUE 8A).

Single-responsibility service for all client-facing observatory queries.

Design principles:
- Anti-N+1: uses selectinload to batch-load necessary relationships.
- Does NOT load EntryAnalysis.calls in list/dashboard (only loaded in get_entry_detail).
- select_current_analysis() called with pre-loaded analyses (no extra DB queries).
- Filters applied AFTER resolving current analysis to guarantee correct semantics.
- total count computed on the filtered set (not raw entry count).
- No internal/technical fields exposed in output.
- No invented default data (source_field, relevance_status, relevance_score).
- Deterministic UTC timezone normalization for naive/aware datetimes.
- Active matrix only for topics taxonomy (never fallback to arbitrary matrix).
- 0 Gemini calls. Strictly read-only.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Any, Sequence, Union

from sqlalchemy.orm import Session, selectinload

from app.models.analysis import AnalysisCall, EntryAnalysis, EntryAnalysisTopic
from app.models.entry import Entry
from app.models.source import Source
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.schemas.observatory import (
    ObservatoryDashboard,
    ObservatoryEntryDetail,
    ObservatoryEntryListItem,
    ObservatoryEvidenceQuote,
    ObservatoryEvidence,
    ObservatoryKeyPointEvidence,
    ObservatoryLatestRelevantEntry,
    ObservatoryListResponse,
    ObservatoryRelevance,
    ObservatorySourceCount,
    ObservatorySourceDetail,
    ObservatorySourceRef,
    ObservatoryTopicCount,
    ObservatoryTopicItem,
    ObservatoryTopicNode,
)
from app.services.current_analysis_service import select_current_analysis
from app.services.topic_canonicalization_service import (
    TopicHierarchy,
    build_topic_hierarchy,
    canonicalize_analysis_topics,
    expand_topic_code_filter,
)

# ---------------------------------------------------------------------------
# Valid sort/filter whitelists (no SQL injection risk)
# ---------------------------------------------------------------------------

VALID_SORT_FIELDS = {"published_at", "relevance_score"}
VALID_SORT_ORDERS = {"asc", "desc"}
VALID_RELEVANCE_STATUSES = {"relevant", "uncertain", "not_relevant"}


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_utc_datetime(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalize naive or aware datetime to timezone-aware UTC datetime.

    Handles:
    - None -> None
    - Naive (e.g. SQLite tests) -> assumed UTC and made aware
    - Aware (e.g. PostgreSQL) -> converted to UTC
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Evidence extraction (deep preferred, triage fallback)
# ---------------------------------------------------------------------------


def _extract_evidence(analysis: EntryAnalysis) -> ObservatoryEvidence:
    """Extract client-facing evidence from current analysis calls.

    Prefers completed deep_analysis call; fallback to completed triage call.
    When multiple completed calls exist for a stage, picks the most recent deterministically
    by (created_at DESC, id DESC).
    Does NOT invent source_field if not provided in raw response.
    Never exposes raw_response, AnalysisCall IDs, costs, or provider details.
    """
    completed_calls = [c for c in (analysis.calls or []) if c.status == "completed"]
    if not completed_calls:
        return ObservatoryEvidence(source="none")

    def call_sort_key(c: AnalysisCall) -> tuple[datetime, str]:
        dt = to_utc_datetime(c.created_at) or datetime.min.replace(tzinfo=timezone.utc)
        return (dt, str(c.id))

    completed_calls.sort(key=call_sort_key, reverse=True)

    deep_call = next((c for c in completed_calls if c.stage == "deep_analysis"), None)
    triage_call = next((c for c in completed_calls if c.stage == "triage"), None)

    target_call = deep_call or triage_call
    if target_call is None:
        return ObservatoryEvidence(source="none")

    source_label = "deep" if target_call is deep_call else "triage"
    raw = (target_call.raw_response or {}).get("result", {})
    if not isinstance(raw, dict):
        return ObservatoryEvidence(source=source_label)

    summary_quotes: list[ObservatoryEvidenceQuote] = []
    key_point_items: list[ObservatoryKeyPointEvidence] = []

    if source_label == "deep":
        for ev in raw.get("summary_evidence", []):
            if isinstance(ev, dict) and ev.get("quote"):
                sf = ev.get("source_field")
                summary_quotes.append(
                    ObservatoryEvidenceQuote(
                        source_field=sf if isinstance(sf, str) and sf.strip() else None,
                        quote=ev["quote"],
                    )
                )
        for kp in raw.get("key_points", []):
            if not isinstance(kp, dict):
                continue
            point_text = kp.get("point", "")
            quotes: list[ObservatoryEvidenceQuote] = []
            for ev in kp.get("evidence", []):
                if isinstance(ev, dict) and ev.get("quote"):
                    sf = ev.get("source_field")
                    quotes.append(
                        ObservatoryEvidenceQuote(
                            source_field=sf if isinstance(sf, str) and sf.strip() else None,
                            quote=ev["quote"],
                        )
                    )
            if point_text:
                key_point_items.append(
                    ObservatoryKeyPointEvidence(point=point_text, quotes=quotes)
                )
    else:
        for ev in raw.get("evidence", []):
            if isinstance(ev, dict) and ev.get("quote"):
                sf = ev.get("source_field")
                summary_quotes.append(
                    ObservatoryEvidenceQuote(
                        source_field=sf if isinstance(sf, str) and sf.strip() else None,
                        quote=ev["quote"],
                    )
                )

    return ObservatoryEvidence(
        summary_quotes=summary_quotes,
        key_points=key_point_items,
        source=source_label,
    )


# ---------------------------------------------------------------------------
# Canonical topics helper
# ---------------------------------------------------------------------------


def _canonicalize(
    analysis: EntryAnalysis, hierarchy: TopicHierarchy
) -> tuple[list[ObservatoryTopicItem], Optional[ObservatoryTopicItem]]:
    """Return (canonical_topics, canonical_primary_topic) with minimal client schema."""
    if not analysis.topics:
        return [], None

    result = canonicalize_analysis_topics(analysis.topics, hierarchy=hierarchy)

    items: list[ObservatoryTopicItem] = [
        ObservatoryTopicItem(
            code=ct.topic_code,
            name=ct.topic_name,
        )
        for ct in result.canonical_topics
    ]
    primary: Optional[ObservatoryTopicItem] = None
    if result.canonical_primary:
        primary = ObservatoryTopicItem(
            code=result.canonical_primary.topic_code,
            name=result.canonical_primary.topic_name,
        )
    return items, primary


# ---------------------------------------------------------------------------
# Normalise key_points (historical dicts or strings)
# ---------------------------------------------------------------------------


def _normalize_key_points(raw: Any) -> list[str]:
    """Normalise key_points field: list[str | dict{point:...}] -> list[str]."""
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            result.append(str(item.get("point", item)))
        else:
            result.append(str(item))
    return result


# ---------------------------------------------------------------------------
# Build entry list item / detail from entry + current analysis
# ---------------------------------------------------------------------------


def _build_list_item(
    entry: Entry,
    analysis: EntryAnalysis,
    hierarchy: TopicHierarchy,
) -> ObservatoryEntryListItem:
    canonical_topics, primary = _canonicalize(analysis, hierarchy)
    return ObservatoryEntryListItem(
        entry_id=entry.id,
        title=entry.title,
        source=ObservatorySourceRef(id=entry.source.id, name=entry.source.name),
        author=entry.author,
        published_at=entry.published_at,
        url=entry.url,
        content_type=entry.content_type,
        relevance=ObservatoryRelevance(
            status=analysis.relevance_status,
            score=analysis.relevance_score,
            confidence=analysis.confidence,
        ),
        summary=analysis.summary,
        canonical_topics=canonical_topics,
        canonical_primary_topic=primary,
        key_points=_normalize_key_points(analysis.key_points),
    )


def _build_detail(
    entry: Entry,
    analysis: EntryAnalysis,
    hierarchy: TopicHierarchy,
) -> ObservatoryEntryDetail:
    canonical_topics, primary = _canonicalize(analysis, hierarchy)
    evidence = _extract_evidence(analysis)
    return ObservatoryEntryDetail(
        entry_id=entry.id,
        title=entry.title,
        source=ObservatorySourceRef(id=entry.source.id, name=entry.source.name),
        author=entry.author,
        published_at=entry.published_at,
        url=entry.url,
        content_type=entry.content_type,
        relevance=ObservatoryRelevance(
            status=analysis.relevance_status,
            score=analysis.relevance_score,
            confidence=analysis.confidence,
        ),
        summary=analysis.summary,
        canonical_topics=canonical_topics,
        canonical_primary_topic=primary,
        key_points=_normalize_key_points(analysis.key_points),
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Text search helper
# ---------------------------------------------------------------------------


def _matches_query(
    entry: Entry, analysis: EntryAnalysis, q: str
) -> bool:
    """Case-insensitive text search across title, excerpt, summary, and key_points."""
    q_lower = q.lower()
    if entry.title and q_lower in entry.title.lower():
        return True
    if entry.excerpt and q_lower in entry.excerpt.lower():
        return True
    if analysis.summary and q_lower in analysis.summary.lower():
        return True
    for kp in _normalize_key_points(analysis.key_points):
        if q_lower in kp.lower():
            return True
    return False


# ---------------------------------------------------------------------------
# Loading strategies (Anti-N+1: list/dashboard do NOT load AnalysisCall)
# ---------------------------------------------------------------------------


def _load_entries_for_list_or_dashboard(db: Session) -> list[Entry]:
    """Load all entries with source, analyses, and topics in a single query batch.

    Excludes AnalysisCall to avoid loading unnecessary call records.
    """
    return (
        db.query(Entry)
        .options(
            selectinload(Entry.source),
            selectinload(Entry.analyses)
            .selectinload(EntryAnalysis.topics)
            .selectinload(EntryAnalysisTopic.topic),
        )
        .all()
    )


def _load_entry_for_detail(entry_id: uuid.UUID, db: Session) -> Optional[Entry]:
    """Load single entry with source, analyses, topics, and analysis calls."""
    return (
        db.query(Entry)
        .options(
            selectinload(Entry.source),
            selectinload(Entry.analyses)
            .selectinload(EntryAnalysis.topics)
            .selectinload(EntryAnalysisTopic.topic),
            selectinload(Entry.analyses)
            .selectinload(EntryAnalysis.calls),
        )
        .filter(Entry.id == entry_id)
        .first()
    )


# ---------------------------------------------------------------------------
# list_entries - main listing endpoint logic
# ---------------------------------------------------------------------------


def list_entries(
    db: Session,
    *,
    limit: int = 20,
    offset: int = 0,
    sort_by: str = "published_at",
    sort_order: str = "desc",
    q: Optional[str] = None,
    date_from: Optional[Union[date, str]] = None,
    date_to: Optional[Union[date, str]] = None,
    source_id: Optional[uuid.UUID] = None,
    source_ids: Optional[list[uuid.UUID]] = None,
    relevance_status: Optional[str] = None,
    min_relevance_score: Optional[int] = None,
    topic_code: Optional[str] = None,
) -> ObservatoryListResponse:
    """Return paginated, filtered list of entries with current analysis.

    All filters operate on entries that have a current valid analysis.
    total reflects the count after ALL filters (not raw entry count).
    """
    if sort_by not in VALID_SORT_FIELDS:
        sort_by = "published_at"
    if sort_order not in VALID_SORT_ORDERS:
        sort_order = "desc"

    hierarchy = build_topic_hierarchy(db)
    entries = _load_entries_for_list_or_dashboard(db)

    expanded_topic_codes: Optional[set[str]] = None
    if topic_code:
        expanded_topic_codes = expand_topic_code_filter(topic_code, hierarchy=hierarchy)
        if not expanded_topic_codes:
            expanded_topic_codes = {topic_code}

    effective_source_ids: Optional[set[uuid.UUID]] = None
    if source_id is not None or source_ids:
        effective_source_ids = set()
        if source_id is not None:
            effective_source_ids.add(source_id)
        if source_ids:
            effective_source_ids.update(source_ids)

    # Parse date bounds into timezone-aware UTC datetimes
    dt_from: Optional[datetime] = None
    dt_to: Optional[datetime] = None
    if date_from is not None:
        if isinstance(date_from, str):
            try:
                d = date.fromisoformat(date_from)
            except ValueError:
                d = None
        else:
            d = date_from
        if d:
            dt_from = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)

    if date_to is not None:
        if isinstance(date_to, str):
            try:
                d = date.fromisoformat(date_to)
            except ValueError:
                d = None
        else:
            d = date_to
        if d:
            dt_to = datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)

    results: list[tuple[Entry, EntryAnalysis]] = []

    for entry in entries:
        current = select_current_analysis(entry, analyses=list(entry.analyses))
        if current is None:
            continue

        # Inconsistency guard: a completed analysis must have status and score
        if current.relevance_status is None or current.relevance_score is None:
            continue

        # Source filter
        if effective_source_ids and entry.source_id not in effective_source_ids:
            continue

        # Date filter (applied to normalized published_at, inclusive)
        pub_utc = to_utc_datetime(entry.published_at)
        if dt_from and (pub_utc is None or pub_utc < dt_from):
            continue
        if dt_to and (pub_utc is None or pub_utc > dt_to):
            continue

        # Relevance status filter
        if relevance_status and current.relevance_status != relevance_status:
            continue

        # Min score filter
        if min_relevance_score is not None:
            if current.relevance_score < min_relevance_score:
                continue

        # Topic filter (expanded hierarchy)
        if expanded_topic_codes:
            topic_codes_on_analysis = {
                t.topic.code for t in current.topics if t.topic
            }
            if not topic_codes_on_analysis.intersection(expanded_topic_codes):
                continue

        # Text search (full OR across title, excerpt, summary, key_points)
        if q:
            if not _matches_query(entry, current, q):
                continue

        results.append((entry, current))

    # Sort
    reverse = sort_order == "desc"
    if sort_by == "relevance_score":
        results.sort(key=lambda x: x[1].relevance_score, reverse=reverse)
    else:  # published_at
        results.sort(
            key=lambda x: (to_utc_datetime(x[0].published_at) or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=reverse,
        )

    total = len(results)
    page_results = results[offset: offset + limit]
    items = [_build_list_item(e, a, hierarchy) for e, a in page_results]

    return ObservatoryListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# get_entry_detail
# ---------------------------------------------------------------------------


def get_entry_detail(
    entry_id: uuid.UUID,
    db: Session,
) -> Optional[ObservatoryEntryDetail]:
    """Return full entry detail for a single entry.

    Returns None if entry not found or has no current analysis.
    Loads calls only for this single entry.
    """
    entry = _load_entry_for_detail(entry_id, db)
    if entry is None:
        return None

    current = select_current_analysis(entry, analyses=list(entry.analyses))
    if current is None:
        return None

    if current.relevance_status is None or current.relevance_score is None:
        return None

    hierarchy = build_topic_hierarchy(db)
    return _build_detail(entry, current, hierarchy)


# ---------------------------------------------------------------------------
# get_sources
# ---------------------------------------------------------------------------


def get_sources(db: Session) -> list[ObservatorySourceDetail]:
    """Return sources with at least one current analyzed publication.

    Excludes sources with zero client-visible publications (such as unanalysed
    discovery feeds or unconfigured providers), providing an accurate, noise-free catalog.
    """
    raw_entries = _load_entries_for_list_or_dashboard(db)

    source_stats: dict[uuid.UUID, tuple[int, Optional[datetime]]] = {}
    sources_by_id: dict[uuid.UUID, Source] = {}

    for entry in raw_entries:
        current = select_current_analysis(entry, entry.analyses)
        if current is not None and current.status == "completed":
            src_id = entry.source_id
            if entry.source:
                sources_by_id[src_id] = entry.source
            prev_cnt, prev_max_dt = source_stats.get(src_id, (0, None))
            new_cnt = prev_cnt + 1
            pub_dt = to_utc_datetime(entry.published_at)
            if prev_max_dt is None or (pub_dt and pub_dt > prev_max_dt):
                new_max_dt = pub_dt
            else:
                new_max_dt = prev_max_dt
            source_stats[src_id] = (new_cnt, new_max_dt)

    result: list[ObservatorySourceDetail] = []
    # Return sources with entry_count > 0, sorted alphabetically by name
    sorted_sources = sorted(sources_by_id.values(), key=lambda s: s.name)
    for src in sorted_sources:
        cnt, latest_dt = source_stats[src.id]
        result.append(
            ObservatorySourceDetail(
                id=src.id,
                name=src.name,
                type=src.type.value if hasattr(src.type, "value") else str(src.type),
                url=src.url,
                entry_count=cnt,
                latest_published_at=latest_dt,
            )
        )
    return result


# ---------------------------------------------------------------------------
# get_topics
# ---------------------------------------------------------------------------


def get_topics(db: Session) -> list[ObservatoryTopicNode]:
    """Return topic taxonomy from the active matrix in a tree structure.

    Returns empty list if no active matrix exists. Never falls back to arbitrary matrix.
    """
    active_matrix = (
        db.query(TrackingMatrix)
        .filter(TrackingMatrix.status == "active")
        .first()
    )
    if active_matrix is None:
        return []

    topics = (
        db.query(TrackingTopic)
        .filter(
            TrackingTopic.matrix_id == active_matrix.id,
            TrackingTopic.active == True,  # noqa: E712
        )
        .order_by(TrackingTopic.priority, TrackingTopic.name)
        .all()
    )

    by_id_code: dict[uuid.UUID, str] = {t.id: t.code for t in topics}
    nodes: dict[uuid.UUID, ObservatoryTopicNode] = {}
    for t in topics:
        parent_code = by_id_code.get(t.parent_id) if t.parent_id else None
        nodes[t.id] = ObservatoryTopicNode(
            code=t.code,
            name=t.name,
            parent_code=parent_code,
            description=t.description,
            children=[],
        )

    roots: list[ObservatoryTopicNode] = []
    for t in topics:
        node = nodes[t.id]
        if t.parent_id and t.parent_id in nodes:
            nodes[t.parent_id].children.append(node)
        else:
            roots.append(node)

    return roots


# ---------------------------------------------------------------------------
# get_dashboard
# ---------------------------------------------------------------------------


def get_dashboard(db: Session) -> ObservatoryDashboard:
    """Compute dashboard KPIs from current analyses only.

    All aggregates derive from select_current_analysis semantics.
    top_topics uses canonical topics (no parent+child double-counting).
    Does NOT load AnalysisCall.
    """
    entries = _load_entries_for_list_or_dashboard(db)
    hierarchy = build_topic_hierarchy(db)

    now = utc_now()
    cutoff_7 = now - timedelta(days=7)
    cutoff_30 = now - timedelta(days=30)

    total = 0
    relevant_count = 0
    uncertain_count = 0
    not_relevant_count = 0
    pubs_last_7 = 0
    pubs_last_30 = 0
    relevant_last_30 = 0

    topic_counter: dict[str, tuple[str, int]] = {}
    source_pubs: dict[uuid.UUID, tuple[str, int, int]] = {}
    relevant_entries: list[tuple[datetime, Entry, EntryAnalysis]] = []

    for entry in entries:
        current = select_current_analysis(entry, analyses=list(entry.analyses))
        if current is None:
            continue

        if current.relevance_status is None or current.relevance_score is None:
            continue

        total += 1
        status = current.relevance_status
        if status == "relevant":
            relevant_count += 1
        elif status == "uncertain":
            uncertain_count += 1
        elif status == "not_relevant":
            not_relevant_count += 1

        pub_utc = to_utc_datetime(entry.published_at)
        if pub_utc:
            if pub_utc >= cutoff_7:
                pubs_last_7 += 1
            if pub_utc >= cutoff_30:
                pubs_last_30 += 1
                if status == "relevant":
                    relevant_last_30 += 1

        # Top topics using canonical topics (no double-counting)
        if current.topics:
            canon_result = canonicalize_analysis_topics(current.topics, hierarchy=hierarchy)
            for ct in canon_result.canonical_topics:
                code = ct.topic_code
                name = ct.topic_name
                prev = topic_counter.get(code, (name, 0))
                topic_counter[code] = (prev[0], prev[1] + 1)

        # Source aggregates
        src_id = entry.source_id
        src_name = entry.source.name if entry.source else str(src_id)
        prev_src = source_pubs.get(src_id, (src_name, 0, 0))
        new_rel = prev_src[2] + (1 if status == "relevant" else 0)
        source_pubs[src_id] = (prev_src[0], prev_src[1] + 1, new_rel)

        if status == "relevant" and pub_utc:
            relevant_entries.append((pub_utc, entry, current))

    top_topics = sorted(
        [
            ObservatoryTopicCount(
                code=code,
                name=hierarchy.by_code[code].name if code in hierarchy.by_code else info[0],
                count=info[1],
            )
            for code, info in topic_counter.items()
            if (code not in hierarchy.by_code or hierarchy.by_code[code].active)
        ],
        key=lambda x: x.count,
        reverse=True,
    )[:10]

    top_sources = sorted(
        [
            ObservatorySourceCount(
                source_id=src_id,
                name=info[0],
                publication_count=info[1],
                relevant_count=info[2],
            )
            for src_id, info in source_pubs.items()
        ],
        key=lambda x: x.publication_count,
        reverse=True,
    )[:10]

    relevant_entries.sort(key=lambda x: x[0], reverse=True)
    latest_relevant: list[ObservatoryLatestRelevantEntry] = []
    for pub_utc, entry, current in relevant_entries[:5]:
        canon_topics, _ = _canonicalize(current, hierarchy)
        latest_relevant.append(
            ObservatoryLatestRelevantEntry(
                entry_id=entry.id,
                title=entry.title,
                source=ObservatorySourceRef(
                    id=entry.source.id, name=entry.source.name
                ),
                published_at=entry.published_at,
                score=current.relevance_score,
                summary=current.summary,
                canonical_topics=canon_topics,
            )
        )

    return ObservatoryDashboard(
        total_publications=total,
        relevant_count=relevant_count,
        uncertain_count=uncertain_count,
        not_relevant_count=not_relevant_count,
        publications_last_7_days=pubs_last_7,
        publications_last_30_days=pubs_last_30,
        relevant_last_30_days=relevant_last_30,
        top_topics=top_topics,
        top_sources=top_sources,
        latest_relevant_entries=latest_relevant,
    )