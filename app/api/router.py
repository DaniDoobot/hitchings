"""API router aggregator."""

from fastapi import APIRouter
from app.api.v1.endpoints import health, sources, tracking, entries, ingestion_runs, analysis, observatory, auth

api_router = APIRouter()

# Health endpoints (root level: /health, /health/db)
api_router.include_router(health.router)

# Versioned API endpoints (/api/v1/...)
v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(auth.router, prefix="/auth")
v1_router.include_router(sources.router)
v1_router.include_router(tracking.router)
v1_router.include_router(entries.router)
v1_router.include_router(ingestion_runs.router)
v1_router.include_router(analysis.router)
v1_router.include_router(observatory.router)

api_router.include_router(v1_router)
