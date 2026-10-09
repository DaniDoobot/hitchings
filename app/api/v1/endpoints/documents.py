"""Client-facing Documentary Consultation endpoints (BLOQUE 11B).

Allows comprehensive querying and inspection of all captured publications/documents
within the repository, cleanly distinguishing original source attributes from
subsequent AI legal analysis.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.db.session import get_db
from app.schemas.document import DocumentDetail, DocumentListResponse
from app.services import document_service

router = APIRouter(
    prefix="/documents",
    tags=["Documentary Archive API"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List captured documents from the archive with analysis status",
)
def list_documents(
    limit: int = Query(20, ge=1, le=100, description="Max items to return (1-100)"),
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    sort_by: Literal["published_at", "captured_at", "title"] = Query(
        "published_at", description="Field to sort by"
    ),
    sort_order: Literal["asc", "desc"] = Query(
        "desc", description="Sort order: asc or desc"
    ),
    q: Optional[str] = Query(
        None,
        description="Search text across title, content, excerpt, and author",
    ),
    date_from: Optional[date] = Query(
        None, description="Start date for published_at (YYYY-MM-DD), inclusive"
    ),
    date_to: Optional[date] = Query(
        None, description="End date for published_at (YYYY-MM-DD), inclusive"
    ),
    source_id: Optional[uuid.UUID] = Query(
        None, description="Filter by source UUID"
    ),
    origin_category: Optional[Literal["all", "institutional", "linkedin", "expert_analysis"]] = Query(
        None,
        description="Filter by source origin category",
    ),
    has_analysis: Optional[Literal["all", "with_analysis", "without_analysis"]] = Query(
        "all",
        description="Filter by analysis presence: all, with_analysis, or without_analysis",
    ),
    relevance_status: Optional[Literal["relevant", "uncertain", "not_relevant"]] = Query(
        None,
        description="Filter by relevance status if analysis exists",
    ),
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    """Retrieve captured documents in the archive.

    Permits querying all captured entries (including those pending or without analysis).
    """
    return document_service.list_documents(
        db=db,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
        q=q,
        date_from=date_from,
        date_to=date_to,
        source_id=source_id,
        origin_category=origin_category,
        has_analysis=has_analysis,
        relevance_status=relevance_status,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    summary="Get single document detail with complete text and analysis/evidence",
)
def get_document_detail(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> DocumentDetail:
    """Retrieve full detail of a document.

    Returns 404 only if the document does not exist in entries.
    If the document exists without completed analysis, it returns 200 with has_analysis=false.
    """
    detail = document_service.get_document_detail(db=db, document_id=document_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Documento '{document_id}' no encontrado en el archivo",
        )
    return detail
