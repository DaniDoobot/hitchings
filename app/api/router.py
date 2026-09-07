"""API router aggregator."""

from fastapi import APIRouter
from app.api.v1.endpoints import health, sources, tracking, entries

api_router = APIRouter()

# Health endpoints (root level: /health, /health/db)
api_router.include_router(health.router)

# Versioned API endpoints (/api/v1/...)
v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(sources.router)
v1_router.include_router(tracking.router)
v1_router.include_router(entries.router)

api_router.include_router(v1_router)
