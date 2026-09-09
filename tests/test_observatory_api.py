"""Comprehensive tests for Client-Facing Observatory API (BLOQUE 8A)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.analysis import (
    AnalysisCall,
    AnalysisPromptVersion,
    EntryAnalysis,
    EntryAnalysisTopic,
)
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.services.analysis_service import compute_analysis_input_hash


def create_matrix_and_topics(db: Session, active: bool = True) -> tuple[TrackingMatrix, dict[str, TrackingTopic]]:
    matrix = TrackingMatrix(
        code=f"MATRIX-{uuid.uuid4().hex[:6]}",
        name="Observatory Active Matrix",
        status="active" if active else "draft",
    )
    db.add(matrix)
    db.flush()

    parent_topic = TrackingTopic(
        matrix_id=matrix.id,
        code="private_enforcement",
        name="Private Enforcement",
        priority=1,
        active=True,
    )
    db.add(parent_topic)
    db.flush()

    child1 = TrackingTopic(
        matrix_id=matrix.id,
        parent_id=parent_topic.id,
        code="damages_actions",
        name="Actions for Damages",
        priority=2,
        active=True,
    )
    child2 = TrackingTopic(
        matrix_id=matrix.id,
        parent_id=parent_topic.id,
        code="collective_actions",
        name="Collective Actions",
        priority=3,
        active=True,
    )
    independent = TrackingTopic(
        matrix_id=matrix.id,
        code="merger_control",
        name="Merger Control",
        priority=4,
        active=True,
    )
    db.add_all([child1, child2, independent])
    db.flush()

    topic_map = {
        "parent": parent_topic,
        "damages": child1,
        "collective": child2,
        "merger": independent,
    }
    return matrix, topic_map


def create_source(db: Session, name: str = "Test Source") -> Source:
    source = Source(
        name=name,
        type=SourceType.WEBSITE,
        provider="native",
        url="https://example.com",
    )
    db.add(source)
    db.flush()
    return source


def create_entry(
    db: Session,
    source: Source,
    title: str = "Test Judgment",
    content: str = "Substantive legal content of the case.",
    excerpt: Optional[str] = "Brief excerpt of the case.",
    published_at: Optional[datetime] = None,
) -> Entry:
    entry = Entry(
        source_id=source.id,
        url=f"https://example.com/item/{uuid.uuid4().hex[:8]}",
        title=title,
        content=content,
        excerpt=excerpt,
        content_hash=None,
        published_at=published_at,
    )
    db.add(entry)
    db.flush()
    return entry


def create_analysis(
    db: Session,
    entry: Entry,
    matrix: TrackingMatrix,
    pipeline_version: str = "v4",
    status: str = "completed",
    relevance_status: str = "relevant",
    relevance_score: int = 85,
    confidence: float = 0.95,
    summary: str = "Executive legal summary.",
    key_points: Optional[list[str]] = None,
    topics: Optional[list[tuple[TrackingTopic, bool]]] = None,
    is_stale: bool = False,
) -> EntryAnalysis:
    content_hash = "stale_hash" if is_stale else compute_analysis_input_hash(entry)
    analysis = EntryAnalysis(
        entry_id=entry.id,
        matrix_id=matrix.id,
        pipeline_version=pipeline_version,
        status=status,
        entry_content_hash=content_hash,
        matrix_snapshot={"code": matrix.code},
        matrix_snapshot_hash="dummy_snapshot_hash",
        relevance_status=relevance_status if status == "completed" else None,
        relevance_score=relevance_score if status == "completed" else None,
        confidence=confidence if status == "completed" else None,
        summary=summary if status == "completed" else None,
        reason="Test justification" if status == "completed" else "Failed test reason",
        key_points=key_points or (["Point 1", "Point 2"] if status == "completed" else None),
    )
    db.add(analysis)
    db.flush()

    if topics and status == "completed":
        for t, is_primary in topics:
            eat = EntryAnalysisTopic(
                analysis_id=analysis.id,
                topic_id=t.id,
                is_primary=is_primary,
                confidence=0.9,
            )
            db.add(eat)
        db.flush()

    return analysis


def create_call(
    db: Session,
    analysis: EntryAnalysis,
    stage: str = "deep_analysis",
    status: str = "completed",
    raw_response: Optional[dict] = None,
    created_at: Optional[datetime] = None,
) -> AnalysisCall:
    prompt = db.query(AnalysisPromptVersion).first()
    if not prompt:
        prompt = AnalysisPromptVersion(
            code=f"prompt_{stage}",
            version=1,
            stage=stage,
            name="Prompt",
            system_prompt="sys",
            user_prompt_template="user",
            response_schema_version="v1",
        )
        db.add(prompt)
        db.flush()

    call = AnalysisCall(
        entry_analysis_id=analysis.id,
        prompt_version_id=prompt.id,
        stage=stage,
        provider="gemini_api",
        model="gemini-3.8-flash",
        status=status,
        raw_response=raw_response or {},
    )
    if created_at:
        call.created_at = created_at
    db.add(call)
    db.flush()
    return call
# ===========================================================================
# 1. LISTING ENDPOINT TESTS
# ===========================================================================


def test_list_entries_returns_only_current_analysis(client: TestClient, db_session: Session) -> None:
    """Verify list returns entries with current completed analysis; excludes unanalysed/failed/stale."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    e1 = create_entry(db_session, source, title="Completed Case")
    create_analysis(db_session, e1, matrix, status="completed", relevance_score=90)

    e2 = create_entry(db_session, source, title="Failed Case")
    create_analysis(db_session, e2, matrix, status="failed")

    create_entry(db_session, source, title="Unanalysed Case")

    e4 = create_entry(db_session, source, title="Stale Case")
    create_analysis(db_session, e4, matrix, status="completed", is_stale=True)
    db_session.commit()

    resp = client.get("/api/v1/observatory/entries")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["entry_id"] == str(e1.id)
    assert item["title"] == "Completed Case"
    assert item["relevance"]["score"] == 90
    assert item["relevance"]["status"] == "relevant"


def test_list_entries_newest_valid_pipeline_wins(client: TestClient, db_session: Session) -> None:
    """Verify that among multiple completed analyses, the newest valid pipeline version wins."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)
    entry = create_entry(db_session, source, title="Multi-Version Case")

    create_analysis(db_session, entry, matrix, pipeline_version="v2", relevance_score=70)
    create_analysis(db_session, entry, matrix, pipeline_version="v4", relevance_score=95)
    db_session.commit()

    resp = client.get("/api/v1/observatory/entries")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    assert data["total"] == 1
    item = data["items"][0]
    assert item["relevance"]["score"] == 95


def test_list_entries_pagination(client: TestClient, db_session: Session) -> None:
    """Verify limit and offset pagination semantics and bounds."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    for i in range(15):
        e = create_entry(db_session, source, title=f"Case #{i:02d}")
        create_analysis(db_session, e, matrix, relevance_score=50 + i)
    db_session.commit()

    r1 = client.get("/api/v1/observatory/entries?limit=5&offset=0")
    assert r1.status_code == status.HTTP_200_OK
    d1 = r1.json()
    assert d1["total"] == 15
    assert len(d1["items"]) == 5
    assert d1["limit"] == 5
    assert d1["offset"] == 0

    r2 = client.get("/api/v1/observatory/entries?limit=5&offset=5")
    assert r2.status_code == status.HTTP_200_OK
    d2 = r2.json()
    assert len(d2["items"]) == 5
    assert d2["offset"] == 5
    assert d1["items"][0]["entry_id"] != d2["items"][0]["entry_id"]


def test_list_entries_filter_by_source(client: TestClient, db_session: Session) -> None:
    """Verify filtering by single source_id and multiple source_ids."""
    matrix, topics = create_matrix_and_topics(db_session)
    s1 = create_source(db_session, name="Source Alpha")
    s2 = create_source(db_session, name="Source Beta")
    s3 = create_source(db_session, name="Source Gamma")

    e1 = create_entry(db_session, s1, title="Alpha 1")
    create_analysis(db_session, e1, matrix)
    e2 = create_entry(db_session, s2, title="Beta 1")
    create_analysis(db_session, e2, matrix)
    e3 = create_entry(db_session, s3, title="Gamma 1")
    create_analysis(db_session, e3, matrix)
    db_session.commit()

    r1 = client.get(f"/api/v1/observatory/entries?source_id={s1.id}")
    assert r1.status_code == status.HTTP_200_OK
    assert r1.json()["total"] == 1
    assert r1.json()["items"][0]["source"]["name"] == "Source Alpha"

    r2 = client.get(f"/api/v1/observatory/entries?source_ids={s1.id}&source_ids={s2.id}")
    assert r2.status_code == status.HTTP_200_OK
    assert r2.json()["total"] == 2


def test_list_entries_filter_by_relevance_status_and_min_score(
    client: TestClient, db_session: Session
) -> None:
    """Verify filtering by relevance_status and min_relevance_score."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    e1 = create_entry(db_session, source, title="High Relevant")
    create_analysis(db_session, e1, matrix, relevance_status="relevant", relevance_score=95)

    e2 = create_entry(db_session, source, title="Moderate Relevant")
    create_analysis(db_session, e2, matrix, relevance_status="relevant", relevance_score=72)

    e3 = create_entry(db_session, source, title="Uncertain Case")
    create_analysis(db_session, e3, matrix, relevance_status="uncertain", relevance_score=45)

    e4 = create_entry(db_session, source, title="Not Relevant Case")
    create_analysis(db_session, e4, matrix, relevance_status="not_relevant", relevance_score=10)
    db_session.commit()

    r_rel = client.get("/api/v1/observatory/entries?relevance_status=relevant")
    assert r_rel.status_code == status.HTTP_200_OK
    assert r_rel.json()["total"] == 2

    r_score = client.get("/api/v1/observatory/entries?min_relevance_score=80")
    assert r_score.status_code == status.HTTP_200_OK
    assert r_score.json()["total"] == 1
    assert r_score.json()["items"][0]["title"] == "High Relevant"


def test_list_entries_filter_by_date_range(client: TestClient, db_session: Session) -> None:
    """Verify date_from and date_to filters (inclusive)."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    e1 = create_entry(db_session, source, title="Early", published_at=datetime(2026, 8, 15, tzinfo=timezone.utc))
    create_analysis(db_session, e1, matrix)

    e2 = create_entry(db_session, source, title="Mid", published_at=datetime(2026, 8, 20, 14, 30, tzinfo=timezone.utc))
    create_analysis(db_session, e2, matrix)

    e3 = create_entry(db_session, source, title="Late", published_at=datetime(2026, 9, 5, tzinfo=timezone.utc))
    create_analysis(db_session, e3, matrix)
    db_session.commit()

    r = client.get("/api/v1/observatory/entries?date_from=2026-08-18&date_to=2026-08-25")
    assert r.status_code == status.HTTP_200_OK
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Mid"


def test_list_entries_text_search_q(client: TestClient, db_session: Session) -> None:
    """Verify search q across title, excerpt, summary, and key_points without losing matches."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    e1 = create_entry(db_session, source, title="Cartel in the Automotive sector")
    create_analysis(db_session, e1, matrix, summary="General summary", key_points=["kp1"])

    e2 = create_entry(db_session, source, title="Generic Title", excerpt="Contains monopoly abuse excerpt")
    create_analysis(db_session, e2, matrix, summary="General summary", key_points=["kp1"])

    # Match ONLY in summary
    e3 = create_entry(db_session, source, title="Unrelated Header", excerpt="Unrelated snippet")
    create_analysis(db_session, e3, matrix, summary="Analysis revealed algorithmic collusion tactics", key_points=["kp1"])

    # Match ONLY in key_points
    e4 = create_entry(db_session, source, title="Another Case", excerpt="Another snippet")
    create_analysis(db_session, e4, matrix, summary="Normal summary", key_points=["Commitments under Article 9", "Pass-on defence confirmed"])
    db_session.commit()

    # Search in title
    assert client.get("/api/v1/observatory/entries?q=automotive").json()["total"] == 1
    # Search in excerpt
    assert client.get("/api/v1/observatory/entries?q=monopoly").json()["total"] == 1
    # Search ONLY in summary
    r_collusion = client.get("/api/v1/observatory/entries?q=algorithmic+collusion")
    assert r_collusion.json()["total"] == 1
    assert r_collusion.json()["items"][0]["entry_id"] == str(e3.id)
    # Search ONLY in key_points
    r_passon = client.get("/api/v1/observatory/entries?q=pass-on")
    assert r_passon.json()["total"] == 1
    assert r_passon.json()["items"][0]["entry_id"] == str(e4.id)


def test_list_entries_sorting(client: TestClient, db_session: Session) -> None:
    """Verify sorting by published_at (asc/desc) and relevance_score (asc/desc)."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    e1 = create_entry(db_session, source, title="Case A", published_at=datetime(2026, 8, 10, tzinfo=timezone.utc))
    create_analysis(db_session, e1, matrix, relevance_score=50)

    e2 = create_entry(db_session, source, title="Case B", published_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    create_analysis(db_session, e2, matrix, relevance_score=95)
    db_session.commit()

    r_score_desc = client.get("/api/v1/observatory/entries?sort_by=relevance_score&sort_order=desc")
    items = r_score_desc.json()["items"]
    assert items[0]["title"] == "Case B"
    assert items[1]["title"] == "Case A"

    r_score_asc = client.get("/api/v1/observatory/entries?sort_by=relevance_score&sort_order=asc")
    items_asc = r_score_asc.json()["items"]
    assert items_asc[0]["title"] == "Case A"
    assert items_asc[1]["title"] == "Case B"

    r_date_desc = client.get("/api/v1/observatory/entries?sort_by=published_at&sort_order=desc")
    assert r_date_desc.json()["items"][0]["title"] == "Case B"

    r_date_asc = client.get("/api/v1/observatory/entries?sort_by=published_at&sort_order=asc")
    assert r_date_asc.json()["items"][0]["title"] == "Case A"


def test_list_entries_validation_errors(client: TestClient) -> None:
    """Verify FastAPI returns HTTP 422 for invalid parameters."""
    assert client.get("/api/v1/observatory/entries?sort_by=invalid_field").status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert client.get("/api/v1/observatory/entries?sort_order=diagonal").status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert client.get("/api/v1/observatory/entries?relevance_status=maybe").status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert client.get("/api/v1/observatory/entries?min_relevance_score=-5").status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert client.get("/api/v1/observatory/entries?min_relevance_score=150").status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert client.get("/api/v1/observatory/entries?limit=101").status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert client.get("/api/v1/observatory/entries?date_from=not-a-date").status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
# ===========================================================================
# 2. TOPIC HIERARCHY & CANONICALISATION TESTS
# ===========================================================================


def test_topic_expansion_and_canonicalisation(client: TestClient, db_session: Session) -> None:
    """Verify topic filter expansion and canonical topic representation in response."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    e1 = create_entry(db_session, source, title="Damages Case")
    create_analysis(db_session, e1, matrix, topics=[(topics["parent"], False), (topics["damages"], True)])

    e2 = create_entry(db_session, source, title="Class Action Case")
    create_analysis(db_session, e2, matrix, topics=[(topics["collective"], True)])

    e3 = create_entry(db_session, source, title="General Private Enforcement")
    create_analysis(db_session, e3, matrix, topics=[(topics["parent"], True)])

    e4 = create_entry(db_session, source, title="Merger Case")
    create_analysis(db_session, e4, matrix, topics=[(topics["merger"], True)])
    db_session.commit()

    r_parent = client.get("/api/v1/observatory/entries?topic_code=private_enforcement")
    assert r_parent.status_code == status.HTTP_200_OK
    p_data = r_parent.json()
    assert p_data["total"] == 3
    found_ids = {item["entry_id"] for item in p_data["items"]}
    assert str(e1.id) in found_ids
    assert str(e2.id) in found_ids
    assert str(e3.id) in found_ids
    assert str(e4.id) not in found_ids

    r_child = client.get("/api/v1/observatory/entries?topic_code=damages_actions")
    assert r_child.json()["total"] == 1
    assert r_child.json()["items"][0]["entry_id"] == str(e1.id)

    e1_item = next(i for i in p_data["items"] if i["entry_id"] == str(e1.id))
    canon_codes = [t["code"] for t in e1_item["canonical_topics"]]
    assert "damages_actions" in canon_codes
    assert "private_enforcement" not in canon_codes
    assert e1_item["canonical_primary_topic"]["code"] == "damages_actions"

    e3_item = next(i for i in p_data["items"] if i["entry_id"] == str(e3.id))
    canon_codes_3 = [t["code"] for t in e3_item["canonical_topics"]]
    assert canon_codes_3 == ["private_enforcement"]
    assert e3_item["canonical_primary_topic"]["code"] == "private_enforcement"


# ===========================================================================
# 3. DETAIL ENDPOINT & EVIDENCE TESTS
# ===========================================================================


def test_get_entry_detail_success_with_deep_evidence(client: TestClient, db_session: Session) -> None:
    """Verify detail endpoint returns 200, clean evidence from deep call, and no internal fields."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session, name="CAT Tribunal")
    entry = create_entry(db_session, source, title="Dr Rachael Kent v Apple")

    analysis = create_analysis(
        db_session, entry, matrix,
        relevance_score=95,
        summary="Court granted collective proceedings order.",
        key_points=["Point A", "Point B"],
        topics=[(topics["damages"], True)],
    )

    deep_raw = {
        "result": {
            "summary": "Court granted collective proceedings order.",
            "summary_evidence": [
                {"source_field": "content", "quote": "The Tribunal considers that the claims are suitable to be brought in collective proceedings."}
            ],
            "key_points": [
                {
                    "point": "Point A",
                    "evidence": [
                        {"source_field": "content", "quote": "The opt-out basis is appropriate in this case."}
                    ]
                }
            ]
        }
    }
    create_call(db_session, analysis, stage="deep_analysis", raw_response=deep_raw)
    db_session.commit()

    resp = client.get(f"/api/v1/observatory/entries/{entry.id}")
    assert resp.status_code == status.HTTP_200_OK
    detail = resp.json()

    assert detail["entry_id"] == str(entry.id)
    assert detail["title"] == "Dr Rachael Kent v Apple"
    assert detail["source"]["name"] == "CAT Tribunal"
    assert detail["relevance"]["score"] == 95
    assert detail["summary"] == "Court granted collective proceedings order."
    assert detail["key_points"] == ["Point A", "Point B"]

    ev = detail["evidence"]
    assert ev["source"] == "deep"
    assert len(ev["summary_quotes"]) == 1
    assert ev["summary_quotes"][0]["quote"] == "The Tribunal considers that the claims are suitable to be brought in collective proceedings."
    assert ev["summary_quotes"][0]["source_field"] == "content"
    assert len(ev["key_points"]) == 1
    assert ev["key_points"][0]["point"] == "Point A"
    assert ev["key_points"][0]["quotes"][0]["quote"] == "The opt-out basis is appropriate in this case."

    forbidden_keys = {
        "pipeline_version", "prompt_version", "entry_content_hash",
        "matrix_snapshot", "matrix_snapshot_hash", "raw_response",
        "estimated_cost_usd", "provider", "model", "status"
    }
    for k in forbidden_keys:
        assert k not in detail


def test_get_entry_detail_triage_fallback(client: TestClient, db_session: Session) -> None:
    """Verify detail evidence falls back to triage when deep call does not exist."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)
    entry = create_entry(db_session, source, title="Triage-Only Entry")
    analysis = create_analysis(db_session, entry, matrix, relevance_status="uncertain", relevance_score=40)

    triage_raw = {
        "result": {
            "evidence": [
                {"source_field": "title", "quote": "Triage-Only Entry"}
            ]
        }
    }
    create_call(db_session, analysis, stage="triage", raw_response=triage_raw)
    db_session.commit()

    resp = client.get(f"/api/v1/observatory/entries/{entry.id}")
    assert resp.status_code == status.HTTP_200_OK
    ev = resp.json()["evidence"]
    assert ev["source"] == "triage"
    assert len(ev["summary_quotes"]) == 1
    assert ev["summary_quotes"][0]["quote"] == "Triage-Only Entry"


def test_get_entry_detail_deterministic_call_selection(client: TestClient, db_session: Session) -> None:
    """Verify that if multiple calls exist, the most recent call is selected deterministically."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)
    entry = create_entry(db_session, source, title="Multi-Call Entry")
    analysis = create_analysis(db_session, entry, matrix)

    old_raw = {"result": {"summary_evidence": [{"quote": "Old Quote"}]}}
    new_raw = {"result": {"summary_evidence": [{"quote": "New Quote"}]}}

    t_old = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    t_new = datetime(2026, 9, 1, 11, 0, tzinfo=timezone.utc)

    create_call(db_session, analysis, stage="deep_analysis", raw_response=old_raw, created_at=t_old)
    create_call(db_session, analysis, stage="deep_analysis", raw_response=new_raw, created_at=t_new)
    db_session.commit()

    resp = client.get(f"/api/v1/observatory/entries/{entry.id}")
    assert resp.status_code == status.HTTP_200_OK
    quotes = resp.json()["evidence"]["summary_quotes"]
    assert len(quotes) == 1
    assert quotes[0]["quote"] == "New Quote"


def test_get_entry_detail_404_when_missing_or_no_current(client: TestClient, db_session: Session) -> None:
    """Verify 404 when entry does not exist or has no valid current analysis."""
    r_nonexistent = client.get(f"/api/v1/observatory/entries/{uuid.uuid4()}")
    assert r_nonexistent.status_code == status.HTTP_404_NOT_FOUND

    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)
    e_failed = create_entry(db_session, source, title="Only Failed")
    create_analysis(db_session, e_failed, matrix, status="failed")
    db_session.commit()

    r_failed = client.get(f"/api/v1/observatory/entries/{e_failed.id}")
    assert r_failed.status_code == status.HTTP_404_NOT_FOUND


def test_evidence_does_not_invent_source_field(client: TestClient, db_session: Session) -> None:
    """Verify that source_field is None if not provided in raw response, never defaulted to content."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)
    entry = create_entry(db_session, source, title="No Source Field Case")
    analysis = create_analysis(db_session, entry, matrix)

    raw_no_sf = {
        "result": {
            "summary_evidence": [
                {"quote": "Quote without declared source field"}
            ]
        }
    }
    create_call(db_session, analysis, stage="deep_analysis", raw_response=raw_no_sf)
    db_session.commit()

    resp = client.get(f"/api/v1/observatory/entries/{entry.id}")
    assert resp.status_code == status.HTTP_200_OK
    ev_quote = resp.json()["evidence"]["summary_quotes"][0]
    assert ev_quote["quote"] == "Quote without declared source field"
    assert ev_quote["source_field"] is None


# ===========================================================================
# 4. DASHBOARD ENDPOINT TESTS
# ===========================================================================


def test_get_dashboard_kpis_and_aggregates(client: TestClient, db_session: Session) -> None:
    """Verify dashboard KPI calculations, trends, top topics, top sources, and latest relevant."""
    matrix, topics = create_matrix_and_topics(db_session)
    s_cat = create_source(db_session, name="CAT")
    s_ec = create_source(db_session, name="EC")

    now = datetime.now(timezone.utc)
    d_3_days_ago = now - timedelta(days=3)
    d_15_days_ago = now - timedelta(days=15)
    d_45_days_ago = now - timedelta(days=45)

    e1 = create_entry(db_session, s_cat, title="Recent CAT", published_at=d_3_days_ago)
    create_analysis(db_session, e1, matrix, relevance_status="relevant", relevance_score=90, topics=[(topics["damages"], True)])

    e2 = create_entry(db_session, s_ec, title="Mid EC", published_at=d_15_days_ago)
    create_analysis(db_session, e2, matrix, relevance_status="relevant", relevance_score=85, topics=[(topics["merger"], True)])

    e3 = create_entry(db_session, s_cat, title="Uncertain CAT", published_at=d_3_days_ago)
    create_analysis(db_session, e3, matrix, relevance_status="uncertain", relevance_score=40)

    e4 = create_entry(db_session, s_ec, title="Old Not Relevant", published_at=d_45_days_ago)
    create_analysis(db_session, e4, matrix, relevance_status="not_relevant", relevance_score=10)
    db_session.commit()

    resp = client.get("/api/v1/observatory/dashboard")
    assert resp.status_code == status.HTTP_200_OK
    d = resp.json()

    assert d["total_publications"] == 4
    assert d["relevant_count"] == 2
    assert d["uncertain_count"] == 1
    assert d["not_relevant_count"] == 1

    assert d["publications_last_7_days"] == 2
    assert d["publications_last_30_days"] == 3
    assert d["relevant_last_30_days"] == 2

    sources_dict = {s["name"]: s for s in d["top_sources"]}
    assert sources_dict["CAT"]["publication_count"] == 2
    assert sources_dict["CAT"]["relevant_count"] == 1
    assert sources_dict["EC"]["publication_count"] == 2
    assert sources_dict["EC"]["relevant_count"] == 1

    assert len(d["latest_relevant_entries"]) == 2
    assert d["latest_relevant_entries"][0]["title"] == "Recent CAT"


# ===========================================================================
# 5. SOURCES & TOPICS ENDPOINTS TESTS
# ===========================================================================


def test_get_sources_counts_and_latest(client: TestClient, db_session: Session) -> None:
    """Verify /sources returns entry counts and latest published date."""
    s1 = create_source(db_session, name="Source 1")
    s2 = create_source(db_session, name="Source 2")

    dt1 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    dt2 = datetime(2026, 8, 20, tzinfo=timezone.utc)

    create_entry(db_session, s1, title="S1 E1", published_at=dt1)
    create_entry(db_session, s1, title="S1 E2", published_at=dt2)
    db_session.commit()

    resp = client.get("/api/v1/observatory/sources")
    assert resp.status_code == status.HTTP_200_OK
    items = resp.json()
    assert len(items) == 2

    s1_item = next(s for s in items if s["name"] == "Source 1")
    assert s1_item["entry_count"] == 2
    assert s1_item["latest_published_at"] is not None

    s2_item = next(s for s in items if s["name"] == "Source 2")
    assert s2_item["entry_count"] == 0
    assert s2_item["latest_published_at"] is None


def test_get_topics_hierarchy_active_matrix_only(client: TestClient, db_session: Session) -> None:
    """Verify /topics returns hierarchical tree only for active matrix, empty if none active."""
    matrix, topics = create_matrix_and_topics(db_session, active=True)
    db_session.commit()

    r_active = client.get("/api/v1/observatory/topics")
    assert r_active.status_code == status.HTTP_200_OK
    tree = r_active.json()

    root_codes = {t["code"] for t in tree}
    assert "private_enforcement" in root_codes
    assert "merger_control" in root_codes

    parent_node = next(t for t in tree if t["code"] == "private_enforcement")
    child_codes = {c["code"] for c in parent_node["children"]}
    assert "damages_actions" in child_codes
    assert "collective_actions" in child_codes

    matrix.status = "draft"
    db_session.commit()

    r_empty = client.get("/api/v1/observatory/topics")
    assert r_empty.status_code == status.HTTP_200_OK
    assert r_empty.json() == []


# ===========================================================================
# 6. EDGE CASES: NAIVE DATETIMES & ANTI-N+1
# ===========================================================================


def test_naive_datetime_handling(client: TestClient, db_session: Session) -> None:
    """Verify naive datetimes (e.g. from SQLite) do not cause comparison crashes."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    naive_dt = datetime(2026, 8, 15, 12, 0, 0)
    e = create_entry(db_session, source, title="Naive Date Entry", published_at=naive_dt)
    create_analysis(db_session, e, matrix)
    db_session.commit()

    r = client.get("/api/v1/observatory/entries?date_from=2026-08-01&date_to=2026-08-31")
    assert r.status_code == status.HTTP_200_OK
    assert r.json()["total"] == 1

    r_dash = client.get("/api/v1/observatory/dashboard")
    assert r_dash.status_code == status.HTTP_200_OK


def test_none_published_at_handling(client: TestClient, db_session: Session) -> None:
    """Verify entries with published_at=None sort cleanly and do not crash date filters."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    e1 = create_entry(db_session, source, title="No Date Entry", published_at=None)
    create_analysis(db_session, e1, matrix)
    e2 = create_entry(db_session, source, title="With Date Entry", published_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    create_analysis(db_session, e2, matrix)
    db_session.commit()

    r_sort = client.get("/api/v1/observatory/entries?sort_by=published_at&sort_order=desc")
    assert r_sort.status_code == status.HTTP_200_OK
    items = r_sort.json()["items"]
    assert items[0]["title"] == "With Date Entry"
    assert items[1]["title"] == "No Date Entry"

    # date_from filter excludes entry with published_at=None
    r_date = client.get("/api/v1/observatory/entries?date_from=2026-08-01")
    assert r_date.status_code == status.HTTP_200_OK
    assert r_date.json()["total"] == 1
    assert r_date.json()["items"][0]["title"] == "With Date Entry"


def test_anti_n_plus_one_list_does_not_load_analysis_calls(client: TestClient, db_session: Session) -> None:
    """Verify that listing and dashboard queries do NOT query or load AnalysisCall objects."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    e = create_entry(db_session, source, title="Call Isolation Entry")
    an = create_analysis(db_session, e, matrix)
    create_call(db_session, an, stage="deep_analysis")
    db_session.commit()

    from app.services.observatory_query_service import _load_entries_for_list_or_dashboard
    from sqlalchemy import inspect

    loaded_entries = _load_entries_for_list_or_dashboard(db_session)
    target_entry = next(item for item in loaded_entries if item.id == e.id)
    target_analysis = target_entry.analyses[0]

    # Inspect analysis state: 'calls' must NOT be loaded!
    insp = inspect(target_analysis)
    assert "calls" in insp.unloaded, "Calls must not be pre-loaded for list/dashboard"

def test_list_entries_inconsistent_analysis_excluded(client: TestClient, db_session: Session) -> None:
    """Verify that an entry whose analysis lacks relevance_status or score is excluded, not invented."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)

    e1 = create_entry(db_session, source, title="Valid Entry")
    create_analysis(db_session, e1, matrix, relevance_status="relevant", relevance_score=80)

    # Inconsistent entry: completed but status or score is None
    e2 = create_entry(db_session, source, title="Inconsistent Entry")
    an_inconsistent = EntryAnalysis(
        entry_id=e2.id,
        matrix_id=matrix.id,
        pipeline_version="v4",
        status="completed",
        entry_content_hash=compute_analysis_input_hash(e2),
        matrix_snapshot={"code": matrix.code},
        matrix_snapshot_hash="h1",
        relevance_status=None,  # Missing!
        relevance_score=None,   # Missing!
        summary="Summary",
    )
    db_session.add(an_inconsistent)
    db_session.commit()

    resp = client.get("/api/v1/observatory/entries")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["entry_id"] == str(e1.id)

    # Detail on inconsistent entry must return 404
    r_det = client.get(f"/api/v1/observatory/entries/{e2.id}")
    assert r_det.status_code == status.HTTP_404_NOT_FOUND


def test_search_q_combined_with_filters(client: TestClient, db_session: Session) -> None:
    """Verify q combined with source and min_score filters."""
    matrix, topics = create_matrix_and_topics(db_session)
    s1 = create_source(db_session, name="Source 1")
    s2 = create_source(db_session, name="Source 2")

    e1 = create_entry(db_session, s1, title="Antitrust Google investigation")
    create_analysis(db_session, e1, matrix, relevance_score=90)

    e2 = create_entry(db_session, s2, title="Antitrust Apple investigation")
    create_analysis(db_session, e2, matrix, relevance_score=85)

    e3 = create_entry(db_session, s1, title="Antitrust Microsoft investigation")
    create_analysis(db_session, e3, matrix, relevance_score=60)
    db_session.commit()

    # Search antitrust + source 1 + min score 70 -> only e1
    r = client.get(f"/api/v1/observatory/entries?q=antitrust&source_id={s1.id}&min_relevance_score=70")
    assert r.status_code == status.HTTP_200_OK
    d = r.json()
    assert d["total"] == 1
    assert d["items"][0]["entry_id"] == str(e1.id)


def test_dashboard_empty_db(client: TestClient, db_session: Session) -> None:
    """Verify dashboard returns clean zero counts on empty database."""
    resp = client.get("/api/v1/observatory/dashboard")
    assert resp.status_code == status.HTTP_200_OK
    d = resp.json()
    assert d["total_publications"] == 0
    assert d["relevant_count"] == 0
    assert d["uncertain_count"] == 0
    assert d["not_relevant_count"] == 0
    assert d["top_topics"] == []
    assert d["top_sources"] == []
    assert d["latest_relevant_entries"] == []


def test_detail_evidence_when_no_calls(client: TestClient, db_session: Session) -> None:
    """Verify detail handles analysis without calls gracefully (source='none')."""
    matrix, topics = create_matrix_and_topics(db_session)
    source = create_source(db_session)
    entry = create_entry(db_session, source, title="No Calls Entry")
    create_analysis(db_session, entry, matrix)
    db_session.commit()

    resp = client.get(f"/api/v1/observatory/entries/{entry.id}")
    assert resp.status_code == status.HTTP_200_OK
    ev = resp.json()["evidence"]
    assert ev["source"] == "none"
    assert ev["summary_quotes"] == []
    assert ev["key_points"] == []