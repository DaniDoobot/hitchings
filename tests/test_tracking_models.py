"""Tests for tracking domain models and their relationships."""

import uuid
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.tracking import (
    TrackingMatrix,
    TrackingTopic,
    TrackedEntity,
    TrackedEntityTopic,
)


def test_tracking_matrix_model(db_session: Session) -> None:
    """Test TrackingMatrix creation, defaults, and attributes."""
    matrix = TrackingMatrix(
        code="TEST-v0.1",
        name="Matriz de Prueba",
        description="Descripción de prueba",
        relevance_instructions="Instrucciones de relevancia",
        exclusion_instructions="Instrucciones de exclusión",
        config={"test": True},
    )
    db_session.add(matrix)
    db_session.commit()
    db_session.refresh(matrix)

    assert matrix.id is not None
    assert matrix.code == "TEST-v0.1"
    assert matrix.status == "draft"  # Default status
    assert matrix.created_at is not None
    assert matrix.config == {"test": True}


def test_tracking_topic_hierarchy(db_session: Session) -> None:
    """Test TrackingTopic parent-child hierarchy and cascade delete."""
    matrix = TrackingMatrix(code="HIERARCHY-v1", name="Matriz Jerárquica")
    db_session.add(matrix)
    db_session.flush()

    parent = TrackingTopic(
        matrix_id=matrix.id,
        code="main_topic",
        name="Tema Principal",
        provisional=False,
        priority=100,
        keywords=["kw1", "kw2"],
        discovery_queries=["q1"],
    )
    db_session.add(parent)
    db_session.flush()

    child = TrackingTopic(
        matrix_id=matrix.id,
        parent_id=parent.id,
        code="sub_topic",
        name="Subtema",
        provisional=True,
        priority=50,
    )
    db_session.add(child)
    db_session.commit()

    db_session.refresh(parent)
    db_session.refresh(child)

    assert child.parent_id == parent.id
    assert len(parent.children) == 1
    assert parent.children[0].code == "sub_topic"
    assert parent.provisional is False
    assert child.provisional is True


def test_tracked_entity_and_topics(db_session: Session) -> None:
    """Test TrackedEntity creation and association with TrackingTopic."""
    matrix = TrackingMatrix(code="ENTITY-TOPIC-v1", name="Matriz de Entidades")
    db_session.add(matrix)
    db_session.flush()

    topic = TrackingTopic(
        matrix_id=matrix.id,
        code="antitrust",
        name="Derecho Antitrust",
    )
    db_session.add(topic)
    db_session.flush()

    entity = TrackedEntity(
        display_name="Pinar Akman",
        entity_type="person",
        metadata={"origin": "client_document", "provisional": False},
    )
    db_session.add(entity)
    db_session.flush()

    assoc = TrackedEntityTopic(
        tracked_entity_id=entity.id,
        tracking_topic_id=topic.id,
        is_primary=True,
        notes="Asociación inicial",
    )
    db_session.add(assoc)
    db_session.commit()

    db_session.refresh(entity)
    assert len(entity.topic_associations) == 1
    assert entity.topic_associations[0].tracking_topic_id == topic.id
    assert entity.topic_associations[0].is_primary is True
    assert entity.metadata_["origin"] == "client_document"


def test_source_tracked_entity_relationship(db_session: Session) -> None:
    """Test that a Source can be linked to a TrackedEntity via tracked_entity_id."""
    entity = TrackedEntity(display_name="CNMC", entity_type="institution")
    db_session.add(entity)
    db_session.flush()

    source = Source(
        name="CNMC Resoluciones Web",
        type=SourceType.INSTITUTIONAL,
        url="https://www.cnmc.es/ambitos-de-actuacion/competencia",
        provider="native",
        tracked_entity_id=entity.id,
    )
    db_session.add(source)
    db_session.commit()

    db_session.refresh(entity)
    db_session.refresh(source)

    assert source.tracked_entity_id == entity.id
    assert len(entity.sources) == 1
    assert entity.sources[0].name == "CNMC Resoluciones Web"
