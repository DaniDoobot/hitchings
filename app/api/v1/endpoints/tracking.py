"""Tracking configuration API endpoints: Matrices, Topics, Entities, and Associations."""

import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.tracking import TrackingMatrix, TrackingTopic, TrackedEntity, TrackedEntityTopic
from app.schemas.tracking import (
    TrackingMatrixCreate,
    TrackingMatrixUpdate,
    TrackingMatrixRead,
    TrackingTopicCreate,
    TrackingTopicUpdate,
    TrackingTopicRead,
    TrackedEntityCreate,
    TrackedEntityUpdate,
    TrackedEntityRead,
    TrackedEntityTopicLink,
    TrackedEntityTopicRead,
    EntityTopicDetail,
)

router = APIRouter(prefix="/tracking", tags=["Tracking"])


# ==============================================================================
# 1. TRACKING MATRICES
# ==============================================================================

@router.get("/matrices", response_model=List[TrackingMatrixRead])
def list_matrices(db: Session = Depends(get_db)) -> List[TrackingMatrix]:
    """List all tracking matrices sorted by creation date."""
    stmt = select(TrackingMatrix).order_by(TrackingMatrix.created_at.desc())
    return list(db.execute(stmt).scalars().all())


@router.get("/matrices/{matrix_id}", response_model=TrackingMatrixRead)
def get_matrix(matrix_id: uuid.UUID, db: Session = Depends(get_db)) -> TrackingMatrix:
    """Retrieve a single tracking matrix by ID."""
    matrix = db.get(TrackingMatrix, matrix_id)
    if not matrix:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking matrix not found")
    return matrix


@router.post("/matrices", response_model=TrackingMatrixRead, status_code=status.HTTP_201_CREATED)
def create_matrix(payload: TrackingMatrixCreate, db: Session = Depends(get_db)) -> TrackingMatrix:
    """Create a new tracking matrix."""
    existing = db.execute(
        select(TrackingMatrix).where(TrackingMatrix.code == payload.code)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Matrix with code '{payload.code}' already exists"
        )

    matrix = TrackingMatrix(
        code=payload.code,
        name=payload.name,
        description=payload.description,
        status=payload.status,
        relevance_instructions=payload.relevance_instructions,
        exclusion_instructions=payload.exclusion_instructions,
        config=payload.config,
    )
    db.add(matrix)
    db.commit()
    db.refresh(matrix)
    return matrix


@router.patch("/matrices/{matrix_id}", response_model=TrackingMatrixRead)
def update_matrix(
    matrix_id: uuid.UUID,
    payload: TrackingMatrixUpdate,
    db: Session = Depends(get_db),
) -> TrackingMatrix:
    """Update fields of an existing tracking matrix."""
    matrix = db.get(TrackingMatrix, matrix_id)
    if not matrix:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking matrix not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(matrix, field, value)

    db.commit()
    db.refresh(matrix)
    return matrix


@router.post("/matrices/{matrix_id}/activate", response_model=TrackingMatrixRead)
def activate_matrix(matrix_id: uuid.UUID, db: Session = Depends(get_db)) -> TrackingMatrix:
    """Activate a tracking matrix.
    
    Any previously active matrix is safely archived. At most one matrix is active at a time.
    """
    matrix = db.get(TrackingMatrix, matrix_id)
    if not matrix:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking matrix not found")

    now = datetime.now(timezone.utc)
    
    # Archive currently active matrices
    db.execute(
        update(TrackingMatrix)
        .where(TrackingMatrix.status == "active", TrackingMatrix.id != matrix_id)
        .values(status="archived", updated_at=now)
    )

    # Activate selected matrix
    matrix.status = "active"
    matrix.activated_at = now
    matrix.updated_at = now

    db.commit()
    db.refresh(matrix)
    return matrix


# ==============================================================================
# 2. TRACKING TOPICS
# ==============================================================================

@router.get("/topics", response_model=List[TrackingTopicRead])
def list_topics(
    matrix_id: Optional[uuid.UUID] = None,
    parent_id: Optional[uuid.UUID] = None,
    active: Optional[bool] = None,
    db: Session = Depends(get_db),
) -> List[TrackingTopic]:
    """List tracking topics with optional filters for matrix, parent hierarchy, and status."""
    stmt = select(TrackingTopic)
    if matrix_id is not None:
        stmt = stmt.where(TrackingTopic.matrix_id == matrix_id)
    if parent_id is not None:
        stmt = stmt.where(TrackingTopic.parent_id == parent_id)
    if active is not None:
        stmt = stmt.where(TrackingTopic.active == active)

    stmt = stmt.order_by(TrackingTopic.priority.desc(), TrackingTopic.name.asc())
    return list(db.execute(stmt).scalars().all())


@router.get("/topics/{topic_id}", response_model=TrackingTopicRead)
def get_topic(topic_id: uuid.UUID, db: Session = Depends(get_db)) -> TrackingTopic:
    """Retrieve a single tracking topic by ID."""
    topic = db.get(TrackingTopic, topic_id)
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking topic not found")
    return topic


@router.post("/topics", response_model=TrackingTopicRead, status_code=status.HTTP_201_CREATED)
def create_topic(payload: TrackingTopicCreate, db: Session = Depends(get_db)) -> TrackingTopic:
    """Create a new tracking topic or subtopic."""
    # Check matrix exists
    if not db.get(TrackingMatrix, payload.matrix_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Referenced matrix does not exist")

    # Check parent exists if supplied
    if payload.parent_id and not db.get(TrackingTopic, payload.parent_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Referenced parent topic does not exist")

    # Check unique code within matrix
    existing = db.execute(
        select(TrackingTopic).where(
            TrackingTopic.matrix_id == payload.matrix_id,
            TrackingTopic.code == payload.code
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Topic with code '{payload.code}' already exists in this matrix"
        )

    topic = TrackingTopic(
        matrix_id=payload.matrix_id,
        parent_id=payload.parent_id,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        relevance_instructions=payload.relevance_instructions,
        keywords=payload.keywords,
        discovery_queries=payload.discovery_queries,
        priority=payload.priority,
        active=payload.active,
        provisional=payload.provisional,
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


@router.patch("/topics/{topic_id}", response_model=TrackingTopicRead)
def update_topic(
    topic_id: uuid.UUID,
    payload: TrackingTopicUpdate,
    db: Session = Depends(get_db),
) -> TrackingTopic:
    """Update an existing tracking topic."""
    topic = db.get(TrackingTopic, topic_id)
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking topic not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(topic, field, value)

    db.commit()
    db.refresh(topic)
    return topic


@router.delete("/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(topic_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    """Delete a tracking topic (and subtopics via cascade)."""
    topic = db.get(TrackingTopic, topic_id)
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking topic not found")

    db.delete(topic)
    db.commit()


# ==============================================================================
# 3. TRACKED ENTITIES
# ==============================================================================

@router.get("/entities", response_model=List[TrackedEntityRead])
def list_entities(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    active: Optional[bool] = None,
    entity_type: Optional[str] = None,
    db: Session = Depends(get_db),
) -> List[TrackedEntity]:
    """List tracked entities with pagination and optional filtering."""
    stmt = select(TrackedEntity)
    if active is not None:
        stmt = stmt.where(TrackedEntity.active == active)
    if entity_type is not None:
        stmt = stmt.where(TrackedEntity.entity_type == entity_type)

    stmt = stmt.order_by(TrackedEntity.display_name.asc()).offset(offset).limit(limit)
    return list(db.execute(stmt).scalars().all())


@router.get("/entities/{entity_id}", response_model=TrackedEntityRead)
def get_entity(entity_id: uuid.UUID, db: Session = Depends(get_db)) -> TrackedEntity:
    """Retrieve a single tracked entity by ID."""
    entity = db.get(TrackedEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracked entity not found")
    return entity


@router.post("/entities", response_model=TrackedEntityRead, status_code=status.HTTP_201_CREATED)
def create_entity(payload: TrackedEntityCreate, db: Session = Depends(get_db)) -> TrackedEntity:
    """Register a new tracked entity (person, organization, institution, publication)."""
    entity = TrackedEntity(
        display_name=payload.display_name,
        entity_type=payload.entity_type,
        active=payload.active,
        notes=payload.notes,
        metadata_=payload.metadata,
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return entity


@router.patch("/entities/{entity_id}", response_model=TrackedEntityRead)
def update_entity(
    entity_id: uuid.UUID,
    payload: TrackedEntityUpdate,
    db: Session = Depends(get_db),
) -> TrackedEntity:
    """Update fields of an existing tracked entity."""
    entity = db.get(TrackedEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracked entity not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "metadata" in update_data:
        entity.metadata_ = update_data.pop("metadata")
    for field, value in update_data.items():
        setattr(entity, field, value)

    db.commit()
    db.refresh(entity)
    return entity


@router.delete("/entities/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entity(entity_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    """Delete a tracked entity."""
    entity = db.get(TrackedEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracked entity not found")

    db.delete(entity)
    db.commit()


# ==============================================================================
# 4. ENTITY <-> TOPIC ASSOCIATIONS
# ==============================================================================

@router.post(
    "/entities/{entity_id}/topics/{topic_id}",
    response_model=TrackedEntityTopicRead,
    status_code=status.HTTP_201_CREATED
)
def link_entity_to_topic(
    entity_id: uuid.UUID,
    topic_id: uuid.UUID,
    link_data: Optional[TrackedEntityTopicLink] = None,
    db: Session = Depends(get_db),
) -> TrackedEntityTopic:
    """Link a tracked entity to an initial tracking topic."""
    entity = db.get(TrackedEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")

    topic = db.get(TrackingTopic, topic_id)
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")

    existing = db.execute(
        select(TrackedEntityTopic).where(
            TrackedEntityTopic.tracked_entity_id == entity_id,
            TrackedEntityTopic.tracking_topic_id == topic_id
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Entity is already associated with this topic"
        )

    assoc = TrackedEntityTopic(
        tracked_entity_id=entity_id,
        tracking_topic_id=topic_id,
        is_primary=link_data.is_primary if link_data else False,
        priority=link_data.priority if link_data else None,
        notes=link_data.notes if link_data else None,
    )
    db.add(assoc)
    db.commit()
    db.refresh(assoc)
    return assoc


@router.delete("/entities/{entity_id}/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_entity_from_topic(
    entity_id: uuid.UUID,
    topic_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    """Remove link between a tracked entity and a tracking topic."""
    assoc = db.execute(
        select(TrackedEntityTopic).where(
            TrackedEntityTopic.tracked_entity_id == entity_id,
            TrackedEntityTopic.tracking_topic_id == topic_id
        )
    ).scalar_one_or_none()
    if not assoc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Association not found")

    db.delete(assoc)
    db.commit()


@router.get("/entities/{entity_id}/topics", response_model=List[EntityTopicDetail])
def list_entity_topics(entity_id: uuid.UUID, db: Session = Depends(get_db)) -> List[dict]:
    """Retrieve all topics currently associated with a given entity."""
    entity = db.get(TrackedEntity, entity_id)
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")

    stmt = (
        select(TrackedEntityTopic, TrackingTopic)
        .join(TrackingTopic, TrackedEntityTopic.tracking_topic_id == TrackingTopic.id)
        .where(TrackedEntityTopic.tracked_entity_id == entity_id)
        .order_by(TrackedEntityTopic.is_primary.desc(), TrackingTopic.name.asc())
    )
    results = db.execute(stmt).all()
    
    return [
        {
            "topic_id": topic.id,
            "code": topic.code,
            "name": topic.name,
            "is_primary": assoc.is_primary,
            "priority": assoc.priority,
            "notes": assoc.notes,
        }
        for assoc, topic in results
    ]
