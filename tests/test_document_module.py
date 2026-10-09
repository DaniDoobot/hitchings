"""Unit and integration tests for the Documentary Archive Module (BLOQUE 11B).

Covers:
- Listing documents with pagination and total count
- Filtering by source_id
- Filtering by date range (published_at)
- Searching text (q in title, content, excerpt, author)
- Filtering by has_analysis (all, with_analysis, without_analysis)
- Filtering by relevance_status
- Getting document detail with completed analysis and verbatim evidence
- Getting document detail for unanalysed entry (returns 200 with has_analysis=False)
- Getting document detail not found (404)
- Fail-closed auth enforcement (401 when no session)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity, TrackingMatrix, TrackingTopic
from app.models.analysis import EntryAnalysis, EntryAnalysisTopic, AnalysisCall
from app.main import app
from app.api.v1.endpoints.auth import get_current_user


@pytest.fixture
def setup_documentary_data(db_session: Session):
    """Seed test data for documentary archive tests."""
    matrix = TrackingMatrix(
        code="MATRIX-11B",
        name="Matriz Documental",
        status="active",
    )
    db_session.add(matrix)
    db_session.flush()

    topic = TrackingTopic(
        matrix_id=matrix.id,
        code="competition_antitrust",
        name="Derecho de la Competencia",
    )
    db_session.add(topic)

    entity = TrackedEntity(
        display_name="Tribunal de Defensa de la Competencia",
        entity_type="institution",
    )
    db_session.add(entity)
    db_session.flush()

    source1 = Source(
        name="Boletin Oficial de Competencia",
        url="https://boc.example.org",
        type=SourceType.WEBSITE,
        category="official",
        tracked_entity_id=entity.id,
    )
    source2 = Source(
        name="LinkedIn Expert Insights",
        url="https://linkedin.com/company/insights",
        type=SourceType.LINKEDIN_COMPANY,
        category="expert_analysis",
    )
    db_session.add_all([source1, source2])
    db_session.flush()

    now = datetime.now(timezone.utc)

    # Entry 1: Analysed document
    entry_analysed = Entry(
        source_id=source1.id,
        title="Sentencia sobre Cártel de Distribución",
        url="https://boc.example.org/sentencia-101",
        content="Resolución judicial confirmando sanción por cártel y fijación de precios.",
        excerpt="Resolución judicial confirmando sanción...",
        author="Magistrado Ponente",
        published_at=now - timedelta(days=2),
        captured_at=now - timedelta(days=2),
        content_type="article",
        language="es",
    )
    db_session.add(entry_analysed)
    db_session.flush()

    from app.services.analysis_service import compute_analysis_input_hash
    content_hash = compute_analysis_input_hash(entry_analysed)

    analysis = EntryAnalysis(
        entry_id=entry_analysed.id,
        matrix_id=matrix.id,
        pipeline_version="v7",
        status="completed",
        entry_content_hash=content_hash,
        matrix_snapshot={"topics": [{"code": "competition_antitrust", "name": "Derecho de la Competencia"}]},
        matrix_snapshot_hash="hash123",
        relevance_status="relevant",
        relevance_score=95,
        confidence=0.92,
        summary="Análisis exhaustivo confirmando la existencia de cártel y afectación al mercado.",
        key_points=["Confirmación de infracción del artículo 1 LDC", "Inadmisión de atenuantes"],
    )
    db_session.add(analysis)
    db_session.flush()

    eat = EntryAnalysisTopic(
        analysis_id=analysis.id,
        topic_id=topic.id,
        is_primary=True,
    )
    db_session.add(eat)

    from app.models.analysis import AnalysisPromptVersion
    prompt_version = AnalysisPromptVersion(
        code="prompt_deep_analysis_v1",
        version=1,
        stage="deep_analysis",
        name="Deep Analysis Prompt",
        system_prompt="System instructions",
        user_prompt_template="User template",
        response_schema_version="v1",
    )
    db_session.add(prompt_version)
    db_session.flush()

    call = AnalysisCall(
        entry_analysis_id=analysis.id,
        prompt_version_id=prompt_version.id,
        stage="deep_analysis",
        provider="gemini_api",
        model="gemini-3.8-flash",
        status="completed",
        raw_response={
            "result": {
                "summary_evidence": [{"source_field": "content", "quote": "Resolución judicial confirmando sanción"}],
                "key_points": [
                    {
                        "point": "Confirmación de infracción del artículo 1 LDC",
                        "evidence": [{"source_field": "content", "quote": "fijación de precios"}],
                    }
                ],
            }
        },
    )
    db_session.add(call)

    # Entry 2: Unanalysed document (raw capture)
    entry_unanalysed = Entry(
        source_id=source2.id,
        title="Actualización breve en redes profesionales",
        url="https://linkedin.com/posts/update-202",
        content="Comentario sobre novedades normativas en materia de indemnizaciones por daños.",
        excerpt="Comentario sobre novedades normativas...",
        author="Dr. Especialista",
        published_at=now - timedelta(days=5),
        captured_at=now - timedelta(days=5),
        content_type="social_post",
        language="es",
    )
    db_session.add(entry_unanalysed)
    db_session.commit()

    return {
        "source1": source1,
        "source2": source2,
        "entry_analysed": entry_analysed,
        "entry_unanalysed": entry_unanalysed,
    }


def test_list_documents_unauthenticated(client: TestClient):
    """Accessing documents without auth dependency override or cookie must return 401."""
    # Temporarily remove override to verify fail-closed security
    override = app.dependency_overrides.pop(get_current_user, None)
    try:
        res = client.get("/api/v1/documents")
        assert res.status_code == 401
    finally:
        if override:
            app.dependency_overrides[get_current_user] = override


def test_list_documents_pagination_and_all(client: TestClient, setup_documentary_data):
    """List documents returns both analysed and unanalysed entries."""
    res = client.get("/api/v1/documents?limit=10&offset=0")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    by_id = {item["id"]: item for item in data["items"]}
    analysed_id = str(setup_documentary_data["entry_analysed"].id)
    unanalysed_id = str(setup_documentary_data["entry_unanalysed"].id)

    assert by_id[analysed_id]["has_analysis"] is True
    assert by_id[analysed_id]["analysis"]["relevance_score"] == 95
    assert by_id[analysed_id]["analysis"]["relevance_status"] == "relevant"
    assert by_id[analysed_id]["source"]["tracked_entity_name"] == "Tribunal de Defensa de la Competencia"

    assert by_id[unanalysed_id]["has_analysis"] is False
    assert by_id[unanalysed_id]["analysis"] is None


def test_list_documents_filter_has_analysis(client: TestClient, setup_documentary_data):
    """Filter by has_analysis with_analysis vs without_analysis."""
    res_with = client.get("/api/v1/documents?has_analysis=with_analysis")
    assert res_with.status_code == 200
    data_with = res_with.json()
    assert data_with["total"] == 1
    assert data_with["items"][0]["id"] == str(setup_documentary_data["entry_analysed"].id)

    res_without = client.get("/api/v1/documents?has_analysis=without_analysis")
    assert res_without.status_code == 200
    data_without = res_without.json()
    assert data_without["total"] == 1
    assert data_without["items"][0]["id"] == str(setup_documentary_data["entry_unanalysed"].id)


def test_list_documents_filter_by_source(client: TestClient, setup_documentary_data):
    """Filter by source_id returns only items from that source."""
    source1_id = str(setup_documentary_data["source1"].id)
    res = client.get(f"/api/v1/documents?source_id={source1_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["source"]["id"] == source1_id


def test_list_documents_search_text(client: TestClient, setup_documentary_data):
    """Search q matches title, content, excerpt, or author."""
    res = client.get("/api/v1/documents?q=Cártel")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Sentencia sobre Cártel de Distribución"

    res_author = client.get("/api/v1/documents?q=Especialista")
    assert res_author.status_code == 200
    data_author = res_author.json()
    assert data_author["total"] == 1
    assert data_author["items"][0]["author"] == "Dr. Especialista"


def test_list_documents_date_filter(client: TestClient, setup_documentary_data):
    """Filter by date_from and date_to."""
    today = datetime.now(timezone.utc).date()
    past = today - timedelta(days=10)

    # Date range covering only unanalysed entry (5 days ago)
    res = client.get(f"/api/v1/documents?date_from={past.isoformat()}&date_to={(today - timedelta(days=4)).isoformat()}")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == str(setup_documentary_data["entry_unanalysed"].id)


def test_get_document_detail_with_analysis(client: TestClient, setup_documentary_data):
    """Get detail of analysed document returns full text, analysis, and verbatim quotes."""
    doc_id = str(setup_documentary_data["entry_analysed"].id)
    res = client.get(f"/api/v1/documents/{doc_id}")
    assert res.status_code == 200
    data = res.json()

    assert data["id"] == doc_id
    assert data["has_analysis"] is True
    assert "Resolución judicial confirmando sanción" in data["content"]
    assert data["analysis"]["relevance_score"] == 95
    assert len(data["analysis"]["canonical_topics"]) == 1
    assert data["analysis"]["canonical_topics"][0]["code"] == "competition_antitrust"

    # Verbatim evidence
    assert data["evidence"] is not None
    assert len(data["evidence"]["summary_quotes"]) == 1
    assert data["evidence"]["summary_quotes"][0]["quote"] == "Resolución judicial confirmando sanción"


def test_get_document_detail_without_analysis(client: TestClient, setup_documentary_data):
    """Get detail of unanalysed document returns 200 with has_analysis=False and null analysis."""
    doc_id = str(setup_documentary_data["entry_unanalysed"].id)
    res = client.get(f"/api/v1/documents/{doc_id}")
    assert res.status_code == 200
    data = res.json()

    assert data["id"] == doc_id
    assert data["has_analysis"] is False
    assert data["analysis"] is None
    assert data["evidence"] is None
    assert "Comentario sobre novedades normativas" in data["content"]
    assert data["source"]["name"] == "LinkedIn Expert Insights"


def test_get_document_detail_not_found(client: TestClient):
    """Non-existent document ID returns 404."""
    random_id = str(uuid.uuid4())
    res = client.get(f"/api/v1/documents/{random_id}")
    assert res.status_code == 404
    assert "no encontrado" in res.json()["detail"].lower()
