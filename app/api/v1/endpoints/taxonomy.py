"""Taxonomy API endpoints for managing versioned Areas and Temas in the Observatory."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.endpoints.auth import get_current_user
from app.db.session import get_db
from app.schemas.taxonomy import (
    CreateAreaRequest,
    CreateTopicRequest,
    TaxonomyMatrixResponse,
    ToggleStatusRequest,
    UpdateAreaRequest,
    UpdateTopicRequest,
)
from app.services import taxonomy_service

router = APIRouter(
    prefix="",
    tags=["Taxonomy"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=TaxonomyMatrixResponse)
def get_taxonomy(db: Session = Depends(get_db)) -> TaxonomyMatrixResponse:
    """Retrieve the current active tracking matrix and its area/topic hierarchy."""
    return taxonomy_service.get_active_taxonomy(db)


@router.post("/areas", response_model=TaxonomyMatrixResponse, status_code=status.HTTP_201_CREATED)
def create_area(
    payload: CreateAreaRequest,
    db: Session = Depends(get_db),
) -> TaxonomyMatrixResponse:
    """Create a new Area, versioning the active matrix atomically."""
    return taxonomy_service.create_area(db, payload)


@router.patch("/areas/{area_id}", response_model=TaxonomyMatrixResponse)
def update_area(
    area_id: uuid.UUID,
    payload: UpdateAreaRequest,
    db: Session = Depends(get_db),
) -> TaxonomyMatrixResponse:
    """Update an Area's name and/or description, versioning the active matrix atomically."""
    return taxonomy_service.update_area(db, area_id, payload)


@router.post("/areas/{area_id}/archive", response_model=TaxonomyMatrixResponse)
def archive_area(
    area_id: uuid.UUID,
    payload: ToggleStatusRequest,
    db: Session = Depends(get_db),
) -> TaxonomyMatrixResponse:
    """Archive an Area and all its child Temas, versioning the active matrix atomically."""
    return taxonomy_service.archive_area(db, area_id, payload)


@router.post("/areas/{area_id}/reactivate", response_model=TaxonomyMatrixResponse)
def reactivate_area(
    area_id: uuid.UUID,
    payload: ToggleStatusRequest,
    db: Session = Depends(get_db),
) -> TaxonomyMatrixResponse:
    """Reactivate an archived Area, versioning the active matrix atomically."""
    return taxonomy_service.reactivate_area(db, area_id, payload)


@router.post("/topics", response_model=TaxonomyMatrixResponse, status_code=status.HTTP_201_CREATED)
def create_topic(
    payload: CreateTopicRequest,
    db: Session = Depends(get_db),
) -> TaxonomyMatrixResponse:
    """Create a new Tema under an Area, versioning the active matrix atomically."""
    return taxonomy_service.create_topic(db, payload)


@router.patch("/topics/{topic_id}", response_model=TaxonomyMatrixResponse)
def update_topic(
    topic_id: uuid.UUID,
    payload: UpdateTopicRequest,
    db: Session = Depends(get_db),
) -> TaxonomyMatrixResponse:
    """Update a Tema's name, description, or parent Area, versioning the active matrix atomically."""
    return taxonomy_service.update_topic(db, topic_id, payload)


@router.post("/topics/{topic_id}/archive", response_model=TaxonomyMatrixResponse)
def archive_topic(
    topic_id: uuid.UUID,
    payload: ToggleStatusRequest,
    db: Session = Depends(get_db),
) -> TaxonomyMatrixResponse:
    """Archive a Tema, versioning the active matrix atomically."""
    return taxonomy_service.archive_topic(db, topic_id, payload)


@router.post("/topics/{topic_id}/reactivate", response_model=TaxonomyMatrixResponse)
def reactivate_topic(
    topic_id: uuid.UUID,
    payload: ToggleStatusRequest,
    db: Session = Depends(get_db),
) -> TaxonomyMatrixResponse:
    """Reactivate an archived Tema, versioning the active matrix atomically."""
    return taxonomy_service.reactivate_topic(db, topic_id, payload)
