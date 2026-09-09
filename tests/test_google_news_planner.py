"""Unit tests for GoogleNewsQueryPlanner (Bloque 9A)."""

from __future__ import annotations

import uuid
from sqlalchemy.orm import Session

from app.models.tracking import TrackedEntity, TrackedEntityTopic, TrackingMatrix, TrackingTopic
from app.services.google_news_query_planner import GoogleNewsQueryPlanner


def _seed_test_matrix(db: Session) -> TrackingMatrix:
    """Create test tracking matrix with entities, topics, and associations."""
    matrix = TrackingMatrix(
        code="TEST-GN-MATRIX",
        name="Test Matrix for Google News",
        status="active",
    )
    db.add(matrix)
    db.flush()

    # Active topic with discovery queries
    t1 = TrackingTopic(
        matrix_id=matrix.id,
        code="competition_general",
        name="Competencia General",
        priority=100,
        active=True,
        discovery_queries=["derecho de la competencia novedades", "EU competition law updates"],
    )
    # Inactive topic
    t2 = TrackingTopic(
        matrix_id=matrix.id,
        code="inactive_topic",
        name="Inactive Topic",
        priority=80,
        active=False,
        discovery_queries=["should not appear query"],
    )
    db.add_all([t1, t2])
    db.flush()

    # Active institution entity
    e1 = TrackedEntity(
        display_name="Comisión Nacional de los Mercados y la Competencia",
        entity_type="institution",
        active=True,
    )
    # Inactive entity
    e2 = TrackedEntity(
        display_name="Inactive Entity",
        entity_type="institution",
        active=False,
    )
    # Active organization
    e3 = TrackedEntity(
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
    )
    db.add_all([e1, e2, e3])
    db.flush()

    # Associate e1 with t1
    assoc1 = TrackedEntityTopic(
        tracked_entity_id=e1.id,
        tracking_topic_id=t1.id,
        is_primary=True,
    )
    assoc2 = TrackedEntityTopic(
        tracked_entity_id=e3.id,
        tracking_topic_id=t1.id,
        is_primary=True,
    )
    db.add_all([assoc1, assoc2])
    db.commit()

    return matrix


def test_planner_deterministic_output(db_session: Session):
    """Planner produces identical output and order across repeated runs with the same DB state."""
    _seed_test_matrix(db_session)

    planner1 = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=10)
    res1 = planner1.plan_queries(db_session)

    planner2 = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=10)
    res2 = planner2.plan_queries(db_session)

    assert len(res1) == len(res2)
    assert len(res1) > 0
    for q1, q2 in zip(res1, res2):
        assert q1.query_id == q2.query_id
        assert q1.query_text == q2.query_text
        assert q1.language == q2.language
        assert q1.priority == q2.priority


def test_planner_ignores_inactive_entities_and_topics(db_session: Session):
    """Planner completely ignores inactive entities and inactive topics."""
    _seed_test_matrix(db_session)

    planner = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=50)
    queries = planner.plan_queries(db_session)

    all_texts = [q.query_text.lower() for q in queries]
    entity_names = [q.entity_name for q in queries if q.entity_name]

    # Inactive entity must not appear
    assert "Inactive Entity" not in entity_names
    for t in all_texts:
        assert "inactive entity" not in t
        assert "should not appear" not in t


def test_planner_deduplicates_queries(db_session: Session):
    """Planner deduplicates identical queries regardless of casing or extra spaces."""
    _seed_test_matrix(db_session)

    planner = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=50)
    queries = planner.plan_queries(db_session)

    seen = set()
    for q in queries:
        key = (q.query_text.strip().lower(), q.language)
        assert key not in seen, f"Duplicate query detected: {key}"
        seen.add(key)


def test_planner_enforces_max_queries_cap(db_session: Session):
    """Planner strictly caps query list to max_queries parameter."""
    _seed_test_matrix(db_session)

    planner_small = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=2)
    res_small = planner_small.plan_queries(db_session)
    assert len(res_small) == 2

    planner_med = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=5)
    res_med = planner_med.plan_queries(db_session)
    assert len(res_med) <= 5


def test_planner_language_filtering(db_session: Session):
    """Planner only includes queries matching requested languages."""
    _seed_test_matrix(db_session)

    # Spanish only
    planner_es = GoogleNewsQueryPlanner(languages=["es"], max_queries=20)
    res_es = planner_es.plan_queries(db_session)
    assert all(q.language == "es" for q in res_es)
    assert all(q.region == "ES" for q in res_es)

    # English only
    planner_en = GoogleNewsQueryPlanner(languages=["en"], max_queries=20)
    res_en = planner_en.plan_queries(db_session)
    assert all(q.language == "en" for q in res_en)
    assert all(q.region == "GB" for q in res_en)
