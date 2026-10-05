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


def _seed_16_topic_matrix(db: Session) -> TrackingMatrix:
    """Seed complete matrix matching the 16 active topics (14 with queries + 2 semantic) and 4 archived."""
    matrix = TrackingMatrix(
        code="TEST-16-TOPIC-MATRIX",
        name="Test 16-Topic Matrix",
        status="active",
    )
    db.add(matrix)
    db.flush()

    # 14 Topics with discovery queries (2 queries each: 1 anchor + 1 secondary)
    topic_configs = [
        ("competition_law_general", "Derecho de la competencia – General", 90, ["derecho de la competencia novedades", "competition law developments"]),
        ("cartels_agreements", "Cárteles y acuerdos colusorios", 90, ["cártel sanción CNMC", "cartel infringement decision"]),
        ("abuse_dominance", "Abuso de posición de dominio", 90, ["abuso de posición dominante sentencia", "abuse of dominance ruling"]),
        ("damages_actions", "Acciones de daños", 90, ["reclamación de daños cártel", "antitrust damages litigation"]),
        ("collective_actions", "Acciones colectivas", 85, ["acciones colectivas competencia", "representative actions antitrust"]),
        ("digital_competition_dma", "Competencia digital DMA", 85, ["Digital Markets Act gatekeeper compliance", "DMA investigation"]),
        ("competition_case_law", "Jurisprudencia de competencia", 85, ["TJUE competencia sentencia", "Court of Justice antitrust judgment"]),
        ("unfair_competition", "Competencia desleal", 75, ["competencia desleal sentencia mercantil", "unfair competition damages"]),
        ("ai_intellectual_property", "IA y Propiedad Intelectual", 70, ["inteligencia artificial propiedad intelectual demanda", "AI copyright infringement"]),
        ("damages_quantification", "Cuantificación de daños", 65, ["cuantificación sobreprecio cártel", "overcharge pass-on damages"]),
        ("evidence_disclosure", "Exhibición de pruebas", 60, ["exhibición de pruebas cártel", "access to evidence directive"]),
        ("litigation_funding", "Financiación de litigios", 55, ["financiación de litigios cártel", "litigation funding antitrust"]),
        ("merger_control", "Control de concentraciones", 50, ["control de concentraciones CNMC", "merger control clearance"]),
        ("competition_policy", "Política de competencia", 50, ["política de competencia directrices", "antitrust guidelines reform"]),
    ]

    for code, name, prio, queries in topic_configs:
        t = TrackingTopic(
            matrix_id=matrix.id,
            code=code,
            name=name,
            priority=prio,
            active=True,
            discovery_queries=queries,
            keywords=["keyword1", "keyword2"],
        )
        db.add(t)

    # 2 Purely semantic topics (active=True, but discovery_queries empty or None)
    t_sem1 = TrackingTopic(
        matrix_id=matrix.id,
        code="private_enforcement",
        name="Aplicación privada (Semántico)",
        priority=95,
        active=True,
        discovery_queries=[],  # Explicitly empty
        keywords=["private enforcement", "directiva de daños"],
    )
    t_sem2 = TrackingTopic(
        matrix_id=matrix.id,
        code="jurisdiction_procedure",
        name="Jurisdicción y procedimiento (Semántico)",
        priority=50,
        active=True,
        discovery_queries=None,  # Explicitly None
        keywords=["prescripción", "limitation period", "competencia judicial"],
    )
    db.add_all([t_sem1, t_sem2])

    # 4 Archived topics (active=False)
    archived_codes = [
        "antitrust_general",
        "private_enforcement_general",
        "private_enforcement_case_law",
        "competition_litigation",
    ]
    for code in archived_codes:
        t_arch = TrackingTopic(
            matrix_id=matrix.id,
            code=code,
            name=f"Archived {code}",
            priority=50,
            active=False,
            discovery_queries=["should not run query"],
            keywords=["archived keyword"],
        )
        db.add(t_arch)

    # 9 Entities (5 institutions, 4 organizations)
    entities = [
        TrackedEntity(display_name="CNMC", entity_type="institution", active=True),
        TrackedEntity(display_name="TJUE", entity_type="institution", active=True),
        TrackedEntity(display_name="European Commission", entity_type="institution", active=True),
        TrackedEntity(display_name="Audiencia Nacional", entity_type="institution", active=True),
        TrackedEntity(display_name="Tribunal Supremo", entity_type="institution", active=True),
        TrackedEntity(display_name="Hausfeld", entity_type="organization", active=True),
        TrackedEntity(display_name="ESKARIAM", entity_type="organization", active=True),
        TrackedEntity(display_name="Redi Abogados", entity_type="organization", active=True),
        TrackedEntity(display_name="ALI Litigation", entity_type="organization", active=True),
    ]
    db.add_all(entities)
    db.commit()
    return matrix


def test_planner_14_topics_and_5_entities_quota(db_session: Session):
    """Test standard execution with 14 query topics, 5 entity quota, and 25 total max queries."""
    _seed_16_topic_matrix(db_session)

    planner = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=25, max_entity_queries=5)
    planned = planner.plan_queries(db_session)

    # 1. Total count strictly 25
    assert len(planned) == 25

    # 2. Entity queries strictly capped to 5
    entity_queries = [q for q in planned if q.entity_name is not None]
    assert len(entity_queries) == 5

    # 3. Topic queries count is 20 (14 Round 1 + 6 Round 2)
    topic_queries = [q for q in planned if q.entity_name is None]
    assert len(topic_queries) == 20

    # 4. Round 1 guaranteed coverage: all 14 active query topics appear in planned queries
    distinct_topic_codes = set()
    for q in topic_queries:
        distinct_topic_codes.update(q.topic_codes)

    expected_topics = {
        "competition_law_general", "cartels_agreements", "abuse_dominance", "damages_actions",
        "collective_actions", "digital_competition_dma", "competition_case_law", "unfair_competition",
        "ai_intellectual_property", "damages_quantification", "evidence_disclosure", "litigation_funding",
        "merger_control", "competition_policy"
    }
    assert expected_topics.issubset(distinct_topic_codes)
    assert len(distinct_topic_codes) == 14


def test_planner_semantic_and_archived_topics_excluded(db_session: Session):
    """Purely semantic topics (discovery_queries empty/None) and archived topics (active=False) generate zero queries."""
    _seed_16_topic_matrix(db_session)

    planner = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=25)
    planned = planner.plan_queries(db_session)

    planned_topic_codes = set()
    for q in planned:
        planned_topic_codes.update(q.topic_codes)

    # Semantic topics must NOT appear
    assert "private_enforcement" not in planned_topic_codes
    assert "jurisdiction_procedure" not in planned_topic_codes

    # Archived topics must NOT appear
    assert "antitrust_general" not in planned_topic_codes
    assert "private_enforcement_general" not in planned_topic_codes
    assert "private_enforcement_case_law" not in planned_topic_codes
    assert "competition_litigation" not in planned_topic_codes


def test_planner_round2_prioritization(db_session: Session):
    """Round 2 slots (6 slots) are assigned to the topics with the highest priority."""
    _seed_16_topic_matrix(db_session)

    planner = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=25, max_entity_queries=5)
    planned = planner.plan_queries(db_session)

    topic_queries = [q for q in planned if q.entity_name is None]

    # Count queries per topic code
    from collections import Counter
    topic_counts: Counter[str] = Counter()
    for q in topic_queries:
        for code in q.topic_codes:
            topic_counts[code] += 1

    # Exactly 6 topics should receive a second query (count == 2)
    topics_with_two_queries = [code for code, count in topic_counts.items() if count == 2]
    assert len(topics_with_two_queries) == 6

    # The top priority topics (priority >= 85) must be the ones receiving 2 queries
    top_tier = {"competition_law_general", "cartels_agreements", "abuse_dominance", "damages_actions", "collective_actions", "digital_competition_dma", "competition_case_law"}
    for code in topics_with_two_queries:
        assert code in top_tier

    # Lower priority topics (P <= 75) must only receive 1 query
    lower_tier = ["unfair_competition", "ai_intellectual_property", "damages_quantification", "evidence_disclosure", "litigation_funding", "merger_control", "competition_policy"]
    for code in lower_tier:
        assert topic_counts[code] == 1


def test_planner_interleaving_4t_1e_pattern(db_session: Session):
    """Queries follow an interleaving pattern with at most 4 consecutive topic queries per entity query."""
    _seed_16_topic_matrix(db_session)

    planner = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=25, max_entity_queries=5)
    planned = planner.plan_queries(db_session)

    is_entity = [q.entity_name is not None for q in planned]

    # Indices 4, 9, 14, 19, 24 should be entity queries in a 4T:1E rhythm
    assert is_entity[4] is True
    assert is_entity[9] is True
    assert is_entity[14] is True
    assert is_entity[19] is True
    assert is_entity[24] is True

    # No more than 4 consecutive topic queries anywhere in the list
    consecutive_topics = 0
    for ie in is_entity:
        if not ie:
            consecutive_topics += 1
            assert consecutive_topics <= 4
        else:
            consecutive_topics = 0


def test_planner_early_termination_diversity(db_session: Session):
    """Simulates early termination (e.g. by max_new_entries cap) and verifies diversity."""
    _seed_16_topic_matrix(db_session)

    planner = GoogleNewsQueryPlanner(languages=["es", "en"], max_queries=25, max_entity_queries=5)
    planned = planner.plan_queries(db_session)

    # In first 10 queries (stopped at query 10):
    first_10 = planned[:10]
    first_10_topics = {code for q in first_10 for code in q.topic_codes}
    first_10_entities = {q.entity_name for q in first_10 if q.entity_name}
    assert len(first_10_topics) == 8  # Exactly 8 distinct topics
    assert len(first_10_entities) == 2  # Exactly 2 entities

    # In first 15 queries (stopped at query 15):
    first_15 = planned[:15]
    first_15_topics = {code for q in first_15 for code in q.topic_codes}
    first_15_entities = {q.entity_name for q in first_15 if q.entity_name}
    assert len(first_15_topics) == 12  # Exactly 12 distinct topics
    assert len(first_15_entities) == 3  # Exactly 3 entities


def test_planner_elastic_reallocation(db_session: Session):
    """Surplus quota is elastically reallocated when one pool has fewer queries than its cap."""
    matrix = TrackingMatrix(code="TEST-ELASTIC", name="Test Elastic", status="active")
    db_session.add(matrix)
    db_session.flush()

    # Case A: Only 1 topic (2 queries) exists, 5 entities exist. Total budget = 6.
    t = TrackingTopic(
        matrix_id=matrix.id,
        code="solo_topic",
        name="Solo Topic",
        priority=100,
        active=True,
        discovery_queries=["competencia caso 1", "competencia caso 2"],
    )
    db_session.add(t)

    for i in range(5):
        db_session.add(TrackedEntity(display_name=f"Entity_{i}", entity_type="institution", active=True))
    db_session.commit()

    planner = GoogleNewsQueryPlanner(languages=["es"], max_queries=6, max_entity_queries=2)
    planned = planner.plan_queries(db_session)

    assert len(planned) == 6
    topic_q = [q for q in planned if q.entity_name is None]
    entity_q = [q for q in planned if q.entity_name is not None]
    # Topic only had 2 queries, so entities absorbed the remaining 4 slots
    assert len(topic_q) == 2
    assert len(entity_q) == 4
