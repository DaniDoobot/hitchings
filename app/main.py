"""Main entry point for HITCHINGS FastAPI application."""

from contextlib import asynccontextmanager
import logging
from typing import AsyncGenerator
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.api.router import api_router

settings = get_settings()
setup_logging(settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager for startup and shutdown events."""
    logger.info("Starting %s in %s mode...", settings.PROJECT_NAME, settings.ENV)
    yield
    logger.info("Shutting down %s...", settings.PROJECT_NAME)


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="HITCHINGS - Automated News and Observability Backend (Block 0 Foundation)",
    version="0.1.0",
    lifespan=lifespan,
)


class AppCORSMiddleware(CORSMiddleware):
    """Dynamic CORS middleware reading allowed origins from settings on every request.

    Guarantees:
    - Empty configuration does not open wildcard.
    - Never uses wildcard '*' with credentials.
    - Reflects environment/settings changes without requiring app recreation.
    """

    def is_allowed_origin(self, origin: str) -> bool:
        current_origins = get_settings().CORS_ALLOWED_ORIGINS
        if not current_origins:
            return False
        if "*" in current_origins:
            return False
        return origin in current_origins


app.add_middleware(
    AppCORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes (including /health and /health/db)
app.include_router(api_router)
