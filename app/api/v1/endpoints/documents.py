"""Client-facing Documentary Consultation endpoints (BLOQUE 11B).

Allows comprehensive querying and inspection of all captured publications/documents
within the repository, cleanly distinguishing original source attributes from
subsequent AI legal analysis.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.db.session import SessionLocal, get_db
from app.models.user import User
from app.schemas.document import (
    DocumentDetail,
    DocumentDuplicateResponse,
    DocumentIngestionResponse,
    DocumentListResponse,
    IngestUrlRequest,
)
from app.services import document_service
from app.services.document_export_service import document_export_service
from app.services.manual_document_ingestion_service import (
    DocumentExtractionError,
    FileSizeLimitExceededError,
    ManualDocumentIngestionService,
    ManualIngestionError,
    ManualIngestionStatus,
    MAX_FILE_BYTES,
    SSRFSecurityError,
    UnsupportedContentTypeError,
)

logger = logging.getLogger(__name__)


async def _execute_background_analysis(entry_id: uuid.UUID) -> None:
    """Execute AI analysis pipeline in the background using an isolated database session."""
    db = SessionLocal()
    try:
        service = ManualDocumentIngestionService()
        await service.execute_entry_analysis(entry_id, db)
    except Exception as exc:
        logger.error(
            "Error en análisis IA en background para la entrada %s: %s",
            entry_id,
            exc,
            exc_info=True,
        )
    finally:
        db.close()


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


@router.post(
    "/url",
    response_model=DocumentIngestionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_201_CREATED: {
            "model": DocumentIngestionResponse,
            "description": "Documento ingerido correctamente desde URL",
        },
        status.HTTP_409_CONFLICT: {
            "model": DocumentDuplicateResponse,
            "description": "Documento ya existente en el fondo documental",
        },
    },
    summary="Ingest a document from an external URL",
)
def ingest_document_url(
    payload: IngestUrlRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Ingest a document from an external URL with SSRF protection and deduplication."""
    service = ManualDocumentIngestionService(
        background_scheduler=lambda entry_id: background_tasks.add_task(
            _execute_background_analysis, entry_id
        )
    )

    try:
        result = service.ingest_url(
            db=db,
            url=payload.url,
            manual_title=payload.manual_title,
            manual_author=payload.manual_author,
            manual_published_at=payload.manual_published_at,
            analyze_with_ai=payload.analyze_with_ai,
            user_id=current_user.id,
            user_email=current_user.email,
        )
    except SSRFSecurityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"URL rechazada por seguridad: {exc}",
        )
    except FileSizeLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        )
    except UnsupportedContentTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        )
    except DocumentExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except ManualIngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error inesperado en ingesta manual de URL: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno procesando la ingesta del documento.",
        )

    if result.status == ManualIngestionStatus.DUPLICATE:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=DocumentDuplicateResponse(
                status="duplicate",
                existing_entry_id=result.existing_entry_id,
                duplicate_reason=result.duplicate_reason or "unknown",
                message=f"El documento ya existe en el fondo documental (duplicado por {result.duplicate_reason}).",
            ).model_dump(mode="json"),
        )

    db.commit()

    entry = result.entry
    return DocumentIngestionResponse(
        status="created",
        entry_id=entry.id,
        title=entry.title,
        url=entry.url,
        canonical_url=entry.canonical_url,
        content_type=entry.content_type,
        analysis_requested=result.analysis_requested,
        analysis_scheduled=result.analysis_scheduled,
        created_at=entry.captured_at,
    )


@router.post(
    "/upload",
    response_model=DocumentIngestionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_201_CREATED: {
            "model": DocumentIngestionResponse,
            "description": "Archivo documental subido e ingerido correctamente",
        },
        status.HTTP_409_CONFLICT: {
            "model": DocumentDuplicateResponse,
            "description": "Documento ya existente en el fondo documental",
        },
    },
    summary="Upload a document file (.pdf, .txt, .md)",
)
async def upload_document_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Archivo documental a subir (.pdf, .txt, .md)"),
    analyze_with_ai: bool = Form(False, description="Programar análisis IA en background"),
    manual_title: Optional[str] = Form(None, description="Título manual opcional"),
    manual_author: Optional[str] = Form(None, description="Autor manual opcional"),
    manual_published_at: Optional[datetime] = Form(None, description="Fecha de publicación manual opcional"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Upload and ingest a document file (.pdf, .txt, .md) into the archive."""
    # Bounded read to avoid memory exhaustion (max 25 MB + 1 KB safety margin)
    file_bytes = await file.read(MAX_FILE_BYTES + 1024)
    if len(file_bytes) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo supera el tamaño máximo permitido de {MAX_FILE_BYTES // (1024*1024)} MB.",
        )

    service = ManualDocumentIngestionService(
        background_scheduler=lambda entry_id: background_tasks.add_task(
            _execute_background_analysis, entry_id
        )
    )

    filename = file.filename or "uploaded_document"
    content_type = file.content_type

    try:
        result = service.ingest_file(
            db=db,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            manual_title=manual_title,
            manual_author=manual_author,
            manual_published_at=manual_published_at,
            analyze_with_ai=analyze_with_ai,
            user_id=current_user.id,
            user_email=current_user.email,
        )
    except SSRFSecurityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Archivo rechazado por seguridad: {exc}",
        )
    except FileSizeLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        )
    except UnsupportedContentTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        )
    except DocumentExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except ManualIngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error inesperado en subida manual de archivo: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno procesando el archivo subido.",
        )

    if result.status == ManualIngestionStatus.DUPLICATE:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=DocumentDuplicateResponse(
                status="duplicate",
                existing_entry_id=result.existing_entry_id,
                duplicate_reason=result.duplicate_reason or "unknown",
                message=f"El documento ya existe en el fondo documental (duplicado por {result.duplicate_reason}).",
            ).model_dump(mode="json"),
        )

    db.commit()

    entry = result.entry
    return DocumentIngestionResponse(
        status="created",
        entry_id=entry.id,
        title=entry.title,
        url=entry.url,
        canonical_url=entry.canonical_url,
        content_type=entry.content_type,
        analysis_requested=result.analysis_requested,
        analysis_scheduled=result.analysis_scheduled,
        created_at=entry.captured_at,
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


DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get(
    "/{document_id}/export",
    summary="Export document report to Word (.docx)",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "content": {DOCX_MIME_TYPE: {}},
            "description": "Informe jurídico en formato Microsoft Word (.docx)",
        },
        status.HTTP_404_NOT_FOUND: {
            "description": "Documento no encontrado en el archivo",
        },
    },
)
def export_document_docx(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Generate and download a professional Word (.docx) document report.

    Deterministic, in-memory, read-only generation.
    Returns 404 if the document does not exist.
    """
    detail = document_service.get_document_detail(db=db, document_id=document_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Documento '{document_id}' no encontrado en el archivo",
        )

    buffer = document_export_service.generate_docx(document=detail)
    filename = document_export_service.sanitize_filename(
        title=detail.title,
        document_id=detail.id,
        has_analysis=detail.has_analysis,
    )

    return Response(
        content=buffer.getvalue(),
        media_type=DOCX_MIME_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
