"""Taxonomy management service for versioned Areas and Temas in the Observatory."""

import re
import unicodedata
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.tracking import TrackingMatrix, TrackingTopic, utc_now
from app.schemas.taxonomy import (
    CreateAreaRequest,
    CreateTopicRequest,
    TaxonomyAreaNode,
    TaxonomyMatrixResponse,
    TaxonomyTopicItem,
    ToggleStatusRequest,
    UpdateAreaRequest,
    UpdateTopicRequest,
)


def _slugify(text: str) -> str:
    """Normalize text into a clean snake_case slug suitable for a topic code."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[-\s]+", "_", text)
    return slug or "topic"


def _bump_version_code(current_code: str) -> str:
    """Increment the minor version number in the matrix code (e.g. HITCHINGS-v0.1 -> HITCHINGS-v0.2)."""
    match_minor = re.match(r"^(.*v)(\d+)\.(\d+)$", current_code)
    if match_minor:
        prefix = match_minor.group(1)
        major = match_minor.group(2)
        minor = int(match_minor.group(3)) + 1
        return f"{prefix}{major}.{minor}"

    match_major = re.match(r"^(.*v)(\d+)$", current_code)
    if match_major:
        prefix = match_major.group(1)
        major = int(match_major.group(2)) + 1
        return f"{prefix}{major}"

    return f"{current_code}.1"


def _generate_unique_code(existing_codes: set[str], name: str) -> str:
    """Generate a unique slug code within the matrix."""
    base_slug = _slugify(name)
    candidate = base_slug
    counter = 2
    while candidate in existing_codes:
        candidate = f"{base_slug}_{counter}"
        counter += 1
    return candidate


def get_active_taxonomy(db: Session) -> TaxonomyMatrixResponse:
    """Retrieve the current active TrackingMatrix and its entire area/topic hierarchy."""
    matrix = (
        db.query(TrackingMatrix)
        .filter(TrackingMatrix.status == "active")
        .first()
    )
    if matrix is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active tracking matrix found",
        )

    topics = (
        db.query(TrackingTopic)
        .filter(TrackingTopic.matrix_id == matrix.id)
        .order_by(TrackingTopic.priority, TrackingTopic.name)
        .all()
    )

    by_id_code: dict[uuid.UUID, str] = {t.id: t.code for t in topics}

    # Group into Areas (parent_id is None) and Temas (parent_id is not None)
    areas_dict: dict[uuid.UUID, TaxonomyAreaNode] = {}
    child_items: list[tuple[uuid.UUID, TaxonomyTopicItem]] = []

    for t in topics:
        if t.parent_id is None:
            areas_dict[t.id] = TaxonomyAreaNode(
                id=t.id,
                code=t.code,
                name=t.name,
                description=t.description,
                active=t.active,
                priority=t.priority,
                children=[],
            )
        else:
            parent_code = by_id_code.get(t.parent_id)
            child_items.append(
                (
                    t.parent_id,
                    TaxonomyTopicItem(
                        id=t.id,
                        code=t.code,
                        name=t.name,
                        description=t.description,
                        parent_id=t.parent_id,
                        parent_code=parent_code,
                        active=t.active,
                        priority=t.priority,
                    ),
                )
            )

    for parent_id, child in child_items:
        if parent_id in areas_dict:
            areas_dict[parent_id].children.append(child)

    areas_list = sorted(areas_dict.values(), key=lambda a: (a.priority, a.name))

    return TaxonomyMatrixResponse(
        matrix_id=matrix.id,
        code=matrix.code,
        name=matrix.name,
        status=matrix.status,
        updated_at=matrix.updated_at,
        areas=areas_list,
    )


def _clone_active_matrix(
    db: Session, base_matrix_id: uuid.UUID
) -> tuple[TrackingMatrix, TrackingMatrix, dict[uuid.UUID, TrackingTopic]]:
    """Verify base_matrix_id matches the active matrix, clone it to a new version, and map old topic IDs to new topics."""
    active_matrix = (
        db.query(TrackingMatrix)
        .filter(TrackingMatrix.status == "active")
        .first()
    )

    if active_matrix is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active tracking matrix found",
        )

    if active_matrix.id != base_matrix_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La configuración de áreas y temas ha sido modificada por otro usuario. Por favor, recargue los datos para continuar.",
        )

    # Determine unique bumped version code
    candidate_code = _bump_version_code(active_matrix.code)
    counter = 2
    while db.query(TrackingMatrix).filter(TrackingMatrix.code == candidate_code).first():
        candidate_code = f"{candidate_code}_{counter}"
        counter += 1

    now = utc_now()
    new_matrix = TrackingMatrix(
        code=candidate_code,
        name=active_matrix.name,
        description=active_matrix.description,
        status="active",
        relevance_instructions=active_matrix.relevance_instructions,
        exclusion_instructions=active_matrix.exclusion_instructions,
        config=dict(active_matrix.config or {}),
        activated_at=now,
    )
    active_matrix.status = "archived"
    db.add(new_matrix)
    db.flush()

    old_topics = (
        db.query(TrackingTopic)
        .filter(TrackingTopic.matrix_id == active_matrix.id)
        .all()
    )

    id_map: dict[uuid.UUID, TrackingTopic] = {}

    # Clone parents (Areas) first
    for ot in old_topics:
        if ot.parent_id is None:
            nt = TrackingTopic(
                matrix_id=new_matrix.id,
                parent_id=None,
                code=ot.code,
                name=ot.name,
                description=ot.description,
                relevance_instructions=ot.relevance_instructions,
                keywords=list(ot.keywords or []) if ot.keywords else None,
                discovery_queries=list(ot.discovery_queries or []) if ot.discovery_queries else None,
                priority=ot.priority,
                active=ot.active,
                provisional=ot.provisional,
            )
            db.add(nt)
            id_map[ot.id] = nt

    db.flush()

    # Clone children (Temas)
    for ot in old_topics:
        if ot.parent_id is not None:
            new_parent = id_map.get(ot.parent_id)
            nt = TrackingTopic(
                matrix_id=new_matrix.id,
                parent_id=new_parent.id if new_parent else None,
                code=ot.code,
                name=ot.name,
                description=ot.description,
                relevance_instructions=ot.relevance_instructions,
                keywords=list(ot.keywords or []) if ot.keywords else None,
                discovery_queries=list(ot.discovery_queries or []) if ot.discovery_queries else None,
                priority=ot.priority,
                active=ot.active,
                provisional=ot.provisional,
            )
            db.add(nt)
            id_map[ot.id] = nt

    db.flush()

    return active_matrix, new_matrix, id_map


def create_area(db: Session, payload: CreateAreaRequest) -> TaxonomyMatrixResponse:
    """Create a new Área under a newly versioned matrix."""
    _, new_matrix, id_map = _clone_active_matrix(db, payload.base_matrix_id)

    existing_codes = {t.code for t in id_map.values()}
    code = _generate_unique_code(existing_codes, payload.name)

    new_area = TrackingTopic(
        matrix_id=new_matrix.id,
        parent_id=None,
        code=code,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        active=True,
        provisional=False,
    )
    db.add(new_area)
    db.commit()

    return get_active_taxonomy(db)


def update_area(
    db: Session, area_id: uuid.UUID, payload: UpdateAreaRequest
) -> TaxonomyMatrixResponse:
    """Update an Área's name and/or description under a newly versioned matrix."""
    _, _, id_map = _clone_active_matrix(db, payload.base_matrix_id)

    target = id_map.get(area_id)
    if target is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Área no encontrada",
        )

    if target.parent_id is not None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El identificador corresponde a un tema, no a un área",
        )

    if payload.name is not None and payload.name.strip():
        target.name = payload.name.strip()
    if payload.description is not None:
        target.description = payload.description.strip() if payload.description else None

    db.commit()
    return get_active_taxonomy(db)


def archive_area(
    db: Session, area_id: uuid.UUID, payload: ToggleStatusRequest
) -> TaxonomyMatrixResponse:
    """Archive an Área and all its child Temas under a newly versioned matrix."""
    _, _, id_map = _clone_active_matrix(db, payload.base_matrix_id)

    target = id_map.get(area_id)
    if target is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Área no encontrada",
        )

    if target.parent_id is not None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El identificador corresponde a un tema, no a un área",
        )

    target.active = False
    # Cascade archiving to all children of this area
    for t in id_map.values():
        if t.parent_id == target.id:
            t.active = False

    db.commit()
    return get_active_taxonomy(db)


def reactivate_area(
    db: Session, area_id: uuid.UUID, payload: ToggleStatusRequest
) -> TaxonomyMatrixResponse:
    """Reactivate an archived Área under a newly versioned matrix."""
    _, _, id_map = _clone_active_matrix(db, payload.base_matrix_id)

    target = id_map.get(area_id)
    if target is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Área no encontrada",
        )

    if target.parent_id is not None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El identificador corresponde a un tema, no a un área",
        )

    target.active = True
    db.commit()
    return get_active_taxonomy(db)


def create_topic(
    db: Session, payload: CreateTopicRequest
) -> TaxonomyMatrixResponse:
    """Create a new Tema under an Área in a newly versioned matrix."""
    _, new_matrix, id_map = _clone_active_matrix(db, payload.base_matrix_id)

    parent_area = id_map.get(payload.area_id)
    if parent_area is None or parent_area.parent_id is not None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Área padre inválida o no encontrada",
        )

    existing_codes = {t.code for t in id_map.values()}
    code = _generate_unique_code(existing_codes, payload.name)

    new_topic = TrackingTopic(
        matrix_id=new_matrix.id,
        parent_id=parent_area.id,
        code=code,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
        active=True,
        provisional=False,
    )
    db.add(new_topic)
    db.commit()

    return get_active_taxonomy(db)


def update_topic(
    db: Session, topic_id: uuid.UUID, payload: UpdateTopicRequest
) -> TaxonomyMatrixResponse:
    """Update a Tema's name, description, and/or parent Área in a newly versioned matrix."""
    _, _, id_map = _clone_active_matrix(db, payload.base_matrix_id)

    target = id_map.get(topic_id)
    if target is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tema no encontrado",
        )

    if target.parent_id is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El identificador corresponde a un área, use el endpoint de áreas",
        )

    if payload.area_id is not None:
        new_parent = id_map.get(payload.area_id)
        if new_parent is None or new_parent.parent_id is not None:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nueva área de destino inválida o no encontrada",
            )
        target.parent_id = new_parent.id

    if payload.name is not None and payload.name.strip():
        target.name = payload.name.strip()
    if payload.description is not None:
        target.description = payload.description.strip() if payload.description else None

    db.commit()
    return get_active_taxonomy(db)


def archive_topic(
    db: Session, topic_id: uuid.UUID, payload: ToggleStatusRequest
) -> TaxonomyMatrixResponse:
    """Archive a Tema under a newly versioned matrix."""
    _, _, id_map = _clone_active_matrix(db, payload.base_matrix_id)

    target = id_map.get(topic_id)
    if target is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tema no encontrado",
        )

    if target.parent_id is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El identificador corresponde a un área, no a un tema",
        )

    target.active = False
    db.commit()
    return get_active_taxonomy(db)


def reactivate_topic(
    db: Session, topic_id: uuid.UUID, payload: ToggleStatusRequest
) -> TaxonomyMatrixResponse:
    """Reactivate an archived Tema under a newly versioned matrix."""
    _, _, id_map = _clone_active_matrix(db, payload.base_matrix_id)

    target = id_map.get(topic_id)
    if target is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tema no encontrado",
        )

    if target.parent_id is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El identificador corresponde a un área, no a un tema",
        )

    # Validate parent area is active
    parent_area = next((t for t in id_map.values() if t.id == target.parent_id), None)
    if parent_area and not parent_area.active:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se puede reactivar un tema cuya área está archivada. Reactive primero el área.",
        )

    target.active = True
    db.commit()
    return get_active_taxonomy(db)
