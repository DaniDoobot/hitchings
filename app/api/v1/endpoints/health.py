"""Health check endpoints."""

import logging
from fastapi import APIRouter, Depends, status, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.health import HealthResponse, DbHealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Basic health check ensuring API process is alive and responsive."""
    return HealthResponse(status="ok", service="hitchings")


@router.get(
    "/health/db",
    response_model=DbHealthResponse,
    responses={
        status.HTTP_200_OK: {"description": "Database connection healthy"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Database unreachable"}
    }
)
def get_db_health(response: Response, db: Session = Depends(get_db)) -> DbHealthResponse:
    """Database connectivity health check performing a lightweight SELECT 1.
    
    A failure here does not impair the primary /health endpoint.
    """
    try:
        db.execute(text("SELECT 1"))
        return DbHealthResponse(status="ok", database="connected")
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return DbHealthResponse(
            status="error",
            database="disconnected",
            detail="Database connection failed"
        )
