"""Client-facing Observatory API endpoints (BLOQUE 8A).

All endpoints operate under the /api/v1/observatory namespace.
Exclusively product-facing:
  - Consumes only the current valid analysis per entry (select_current_analysis).
  - Canonical topics (no parent+child redundancy).
  - Cleaned evidence (no raw_response, no internal metrics/costs/hashes).
  - Strict input validation with FastAPI types (422 on invalid parameters).
  - 0 Gemini calls.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.db.session import get_db
from app.schemas.observatory import (
    ObservatoryDashboard,
    ObservatoryEntryDetail,
    ObservatoryListResponse,
    ObservatorySourceDetail,
    ObservatoryTopicNode,
)
from app.services import observatory_query_service as service

router = APIRouter(
    prefix="/observatory",
    tags=["Observatory Client API"],
    dependencies=[Depends(get_current_user)],
)


# ---------------------------------------------------------------------------
# 1. Main entries listing
# ---------------------------------------------------------------------------


@router.get(
    "/entries",
    response_model=ObservatoryListResponse,
    summary="List observatory publications with current analysis",
)
def list_observatory_entries(
    limit: int = Query(20, ge=1, le=100, description="Max items to return (1-100)"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    sort_by: Literal["published_at", "relevance_score"] = Query(
        "published_at", description="Field to sort by"
    ),
    sort_order: Literal["asc", "desc"] = Query(
        "desc", description="Sort order: asc or desc"
    ),
    q: Optional[str] = Query(
        None,
        description="Search text across title, excerpt, summary, and key points (case-insensitive)",
    ),
    date_from: Optional[date] = Query(
        None, description="Start date for published_at (YYYY-MM-DD), inclusive"
    ),
    date_to: Optional[date] = Query(
        None, description="End date for published_at (YYYY-MM-DD), inclusive"
    ),
    source_id: Optional[uuid.UUID] = Query(
        None, description="Filter by single source UUID"
    ),
    source_ids: Optional[list[uuid.UUID]] = Query(
        None, description="Filter by multiple source UUIDs"
    ),
    relevance_status: Optional[Literal["relevant", "uncertain", "not_relevant"]] = Query(
        None, description="Filter by relevance status"
    ),
    min_relevance_score: Optional[int] = Query(
        None, ge=0, le=100, description="Minimum relevance score (0-100)"
    ),
    topic_code: Optional[str] = Query(
        None,
        description="Topic code. Automatically expands to include all child subtopics.",
    ),
    db: Session = Depends(get_db),
) -> ObservatoryListResponse:
    """Retrieve observatory publications with their current validated analysis.

    Excludes unanalysed entries or entries whose current analysis failed.
    Total reflects the count after applying all filters.
    """
    return service.list_entries(
        db=db,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
        q=q,
        date_from=date_from,
        date_to=date_to,
        source_id=source_id,
        source_ids=source_ids,
        relevance_status=relevance_status,
        min_relevance_score=min_relevance_score,
        topic_code=topic_code,
    )


# ---------------------------------------------------------------------------
# 2. Entry detail with evidence
# ---------------------------------------------------------------------------


@router.get(
    "/entries/{entry_id}",
    response_model=ObservatoryEntryDetail,
    summary="Get single observatory publication detail with client-facing evidence",
)
def get_observatory_entry_detail(
    entry_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ObservatoryEntryDetail:
    """Retrieve full detail of a publication and its current analysis.

    Returns 404 if the entry does not exist or has no current completed analysis.
    Evidence is extracted from the deep analysis call (or triage fallback) without
    exposing internal audit records, raw responses, or pricing data.
    """
    detail = service.get_entry_detail(entry_id=entry_id, db=db)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Observatory entry '{entry_id}' not found or has no current analysis",
        )
    return detail


# ---------------------------------------------------------------------------
# 3. Sources discovery endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/sources",
    response_model=list[ObservatorySourceDetail],
    summary="List available sources for observatory filtering",
)
def get_observatory_sources(
    db: Session = Depends(get_db),
) -> list[ObservatorySourceDetail]:
    """Return all available sources with publication count and latest publication date."""
    return service.get_sources(db=db)


# ---------------------------------------------------------------------------
# 4. Topics taxonomy endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/topics",
    response_model=list[ObservatoryTopicNode],
    summary="Get hierarchical topic taxonomy from the active matrix",
)
def get_observatory_topics(
    db: Session = Depends(get_db),
) -> list[ObservatoryTopicNode]:
    """Return active tracking topics arranged as a hierarchy (parents with children).

    Exclusively uses the active tracking matrix. Returns empty list if no active matrix exists.
    """
    return service.get_topics(db=db)


# ---------------------------------------------------------------------------
# 5. Dashboard KPIs endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/dashboard",
    response_model=ObservatoryDashboard,
    summary="Get observatory dashboard KPIs, trends, and top categories",
)
def get_observatory_dashboard(
    db: Session = Depends(get_db),
) -> ObservatoryDashboard:
    """Provide aggregated dashboard metrics derived strictly from current analyses.

    Includes total counts, relevance distribution, last 7/30 days trends,
    top canonical topics (without parent double-counting), top sources,
    and the 5 most recent relevant publications.
    """
    return service.get_dashboard(db=db)