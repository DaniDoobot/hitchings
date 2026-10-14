"""API integration tests for Document Word Export endpoint (Bloque 12 — Fase 2).

Covers all mandatory verification scenarios:
1. Authenticated user + analysed document: HTTP 200, DOCX MIME type, attachment disposition, valid DOCX content.
2. Authenticated user + unanalysed document: HTTP 200, DOCX MIME type, valid DOCX with 'expediente-' prefix.
3. Non-existent document: HTTP 404.
4. Unauthenticated request: HTTP 401.
5. Read-only guarantee: database state is completely untouched.
6. AI model isolation: 0 calls to Gemini or AI providers.
7. Filename sanitization: accents stripped, punctuation normalized to hyphens, .docx extension.
8. DOCX structural integrity: python-docx opens the document and sections are present.
9. Invalid UUID parameter: HTTP 422.
10. OpenAPI schema registry: /api/v1/documents/{document_id}/export present with correct MIME type.
11. Existing documentary endpoints regression check: /api/v1/documents and /api/v1/documents/{id} unaffected.
"""

from __future__ import annotations

import io
import unittest.mock as mock
import uuid
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session
import docx

from app.main import app
from app.api.v1.endpoints.auth import get_current_user
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity, TrackingMatrix, TrackingTopic
from app.models.analysis import EntryAnalysis, EntryAnalysisTopic, AnalysisCall
from app.api.v1.endpoints.documents import DOCX_MIME_TYPE


@pytest.fixture
def setup_export_data(db_session: Session):
    """Seed sample analysed and unanalysed documentary records for export tests."""
    matrix = TrackingMatrix(
        code="MATRIX-EXP-12",
        name="Matriz Exportacion",
        status="active",
    )
    db_session.add(matrix)
    db_session.flush()

    topic = TrackingTopic(
        matrix_id=matrix.id,
        code="antitrust_cartels",
        name="Cárteles y Colusión",
    )
    db_session.add(topic)

    entity = TrackedEntity(
        display_name="Tribunal de Defensa de la Competencia",
        entity_type="institution",
    )
    db_session.add(entity)
    db_session.flush()

    source = Source(
        name="Boletín Oficial de Competencia",
        url="https://boc.example.org",
        type=SourceType.WEBSITE,
        category="official",
        tracked_entity_id=entity.id,
    )
    db_session.add(source)
    db_session.flush()

    now = datetime.now(timezone.utc)

    # Document 1: Fully analysed legal document
    entry_analysed = Entry(
        source_id=source.id,
        title="Resolución Sancionadora: Cártel de Distribución Eléctrica & Suministros",
        url="https://boc.example.org/res-2026-101",
        canonical_url="https://boc.example.org/res-2026-101",
        content=(
            "La Comisión Nacional de los Mercados y la Competencia resuelve declarar la comisión "
            "de una infracción continuada del artículo 1 de la Ley 15/2007 de Defensa de la Competencia. "
            "Los acuerdos colusorios de fijación de precios y reparto de mercado en licitaciones públicas "
            "constituyen una restricción por objeto de máxima gravedad."
        ),
        excerpt="La Comisión Nacional resuelve declarar la comisión de infracción...",
        author="Consejo de la Comisión de Competencia",
        published_at=now - timedelta(days=5),
        captured_at=now - timedelta(days=5),
        content_type="decision",
        language="es",
        raw_metadata={"expediente_num": "S/0012/23", "organismo": "CNMC"},
    )
    db_session.add(entry_analysed)
    db_session.flush()

    from app.services.analysis_service import compute_analysis_input_hash
    content_hash = compute_analysis_input_hash(entry_analysed)

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

    analysis = EntryAnalysis(
        entry_id=entry_analysed.id,
        matrix_id=matrix.id,
        pipeline_version="v7",
        status="completed",
        entry_content_hash=content_hash,
        matrix_snapshot={"topics": [{"code": "antitrust_cartels", "name": "Cárteles y Colusión"}]},
        matrix_snapshot_hash="hash-123",
        relevance_status="relevant",
        relevance_score=94,
        confidence=0.92,
        summary="Sanción por cártel colusorio en distribución eléctrica con acreditación de reparto de mercado.",
        key_points=[
            "Infracción por objeto tipificada en el art. 1 LDC.",
            "Reparto sistemático de licitaciones públicas durante un periodo de cuatro años.",
        ],
        completed_at=now - timedelta(days=4),
    )
    db_session.add(analysis)
    db_session.flush()

    eat = EntryAnalysisTopic(
        analysis_id=analysis.id,
        topic_id=topic.id,
        is_primary=True,
    )
    db_session.add(eat)

    call = AnalysisCall(
        entry_analysis_id=analysis.id,
        prompt_version_id=prompt_version.id,
        stage="deep_analysis",
        provider="gemini_api",
        model="gemini-3.8-flash",
        status="completed",
        raw_response={
            "result": {
                "summary_evidence": [
                    {
                        "source_field": "content",
                        "quote": "Los acuerdos colusorios de fijación de precios y reparto de mercado en licitaciones públicas constituyen una restricción por objeto de máxima gravedad.",
                    }
                ],
                "key_points_evidence": [
                    {
                        "point": "Infracción por objeto tipificada en el art. 1 LDC.",
                        "quotes": [
                            {
                                "source_field": "content",
                                "quote": "Los acuerdos colusorios de fijación de precios y reparto de mercado en licitaciones públicas constituyen una restricción por objeto de máxima gravedad.",
                            }
                        ],
                    }
                ],
            }
        },
    )
    db_session.add(call)

    # Document 2: Unanalysed archive entry
    entry_unanalysed = Entry(
        source_id=source.id,
        title="Informe Preliminar de Mercado sin Análisis Posterior",
        url="https://boc.example.org/doc-unanalysed-202",
        canonical_url="https://boc.example.org/doc-unanalysed-202",
        content="Texto íntegro del informe de consulta pública sectorial sobre condiciones de acceso a redes.",
        excerpt="Texto íntegro del informe de consulta pública...",
        author="Dirección de Energía",
        published_at=now - timedelta(days=1),
        captured_at=now - timedelta(days=1),
        content_type="report",
        language="es",
    )
    db_session.add(entry_unanalysed)
    db_session.commit()

    return {
        "entry_analysed": entry_analysed,
        "entry_unanalysed": entry_unanalysed,
        "analysis": analysis,
    }


# ==============================================================================
# 1. Successful Export Tests
# ==============================================================================


def test_01_export_analysed_document_success(client: TestClient, setup_export_data):
    """Scenario 1: Authenticated user exporting an analysed document gets HTTP 200 and valid DOCX."""
    doc_id = str(setup_export_data["entry_analysed"].id)
    res = client.get(f"/api/v1/documents/{doc_id}/export")

    assert res.status_code == 200
    assert res.headers["content-type"] == DOCX_MIME_TYPE
    assert "attachment; filename=" in res.headers["content-disposition"]
    assert "dictamen-" in res.headers["content-disposition"]
    assert res.headers["content-disposition"].endswith('.docx"')
    assert res.headers.get("x-content-type-options") == "nosniff"

    # Verify DOCX content is valid and readable by python-docx
    docx_doc = docx.Document(io.BytesIO(res.content))
    all_text = " ".join(p.text for p in docx_doc.paragraphs)

    assert "HITCHINGS — OBSERVATORIO LEGAL Y FONDO DOCUMENTAL" in all_text
    assert "DICTAMEN JURÍDICO" in all_text
    assert "Resolución Sancionadora" in all_text
    assert "RELEVANTE" in all_text
    assert "94/100" in all_text
    assert "Infracción por objeto tipificada en el art. 1 LDC" in all_text
    assert "TEXTO DOCUMENTAL ORIGINAL" in all_text


def test_02_export_unanalysed_document_success(client: TestClient, setup_export_data):
    """Scenario 2: Authenticated user exporting an unanalysed document gets valid DOCX with expediente prefix."""
    doc_id = str(setup_export_data["entry_unanalysed"].id)
    res = client.get(f"/api/v1/documents/{doc_id}/export")

    assert res.status_code == 200
    assert res.headers["content-type"] == DOCX_MIME_TYPE
    assert "attachment; filename=" in res.headers["content-disposition"]
    assert "expediente-" in res.headers["content-disposition"]
    assert res.headers["content-disposition"].endswith('.docx"')

    docx_doc = docx.Document(io.BytesIO(res.content))
    all_text = " ".join(p.text for p in docx_doc.paragraphs)

    assert "EXPEDIENTE DOCUMENTAL" in all_text
    assert "Informe Preliminar de Mercado" in all_text
    assert "TEXTO DOCUMENTAL ORIGINAL" in all_text
    # Should NOT have AI analysis sections
    assert "DICTAMEN JURÍDICO Y CALIFICACIÓN DE RELEVANCIA" not in all_text
    assert "PUNTOS CLAVE Y CRITERIOS DOCTRINALES" not in all_text


# ==============================================================================
# 2. Error Handling & Security Tests
# ==============================================================================


def test_03_export_document_not_found_404(client: TestClient):
    """Scenario 3: Requesting export of a non-existent document ID returns HTTP 404."""
    random_id = str(uuid.uuid4())
    res = client.get(f"/api/v1/documents/{random_id}/export")

    assert res.status_code == 404
    assert "no encontrado" in res.json()["detail"].lower()


def test_04_export_unauthenticated_returns_401(client: TestClient, setup_export_data):
    """Scenario 4: Requesting export without authentication returns HTTP 401."""
    doc_id = str(setup_export_data["entry_analysed"].id)
    override = app.dependency_overrides.pop(get_current_user, None)
    try:
        res = client.get(f"/api/v1/documents/{doc_id}/export")
        assert res.status_code == 401
    finally:
        if override:
            app.dependency_overrides[get_current_user] = override


def test_05_export_invalid_uuid_returns_422(client: TestClient):
    """Scenario 5: Malformed UUID format is rejected with HTTP 422 validation error."""
    res = client.get("/api/v1/documents/not-a-valid-uuid/export")
    assert res.status_code == 422


# ==============================================================================
# 3. Read-Only & Zero-Side-Effect Guarantees
# ==============================================================================


def test_06_export_does_not_modify_database(client: TestClient, db_session: Session, setup_export_data):
    """Scenario 6: Verify that exporting does not mutate or insert any database records."""
    doc_id = str(setup_export_data["entry_analysed"].id)

    count_entries_before = db_session.execute(select(func.count(Entry.id))).scalar()
    count_analyses_before = db_session.execute(select(func.count(EntryAnalysis.id))).scalar()
    count_calls_before = db_session.execute(select(func.count(AnalysisCall.id))).scalar()

    res = client.get(f"/api/v1/documents/{doc_id}/export")
    assert res.status_code == 200

    count_entries_after = db_session.execute(select(func.count(Entry.id))).scalar()
    count_analyses_after = db_session.execute(select(func.count(EntryAnalysis.id))).scalar()
    count_calls_after = db_session.execute(select(func.count(AnalysisCall.id))).scalar()

    assert count_entries_before == count_entries_after
    assert count_analyses_before == count_analyses_after
    assert count_calls_before == count_calls_after


def test_07_export_does_not_call_gemini_or_ai_providers(client: TestClient, setup_export_data):
    """Scenario 7: Verify that export does not trigger any AI model generation or Gemini calls."""
    doc_id = str(setup_export_data["entry_analysed"].id)

    with mock.patch("app.providers.ai.base.BaseAIProvider.analyze", side_effect=AssertionError("AI call triggered!")):
        with mock.patch("google.genai.Client", side_effect=AssertionError("Gemini client triggered!")):
            res = client.get(f"/api/v1/documents/{doc_id}/export")
            assert res.status_code == 200


# ==============================================================================
# 4. Filename & Format Verification
# ==============================================================================


def test_08_export_filename_sanitization(client: TestClient, db_session: Session, setup_export_data):
    """Scenario 8: Filename is sanitized to ASCII lowercase with hyphens and no special characters."""
    from app.services.analysis_service import compute_analysis_input_hash
    doc = setup_export_data["entry_analysed"]
    doc.title = "Resolución CNMC: Cárteles en España (Exp. 123)!"
    setup_export_data["analysis"].entry_content_hash = compute_analysis_input_hash(doc)
    db_session.commit()

    res = client.get(f"/api/v1/documents/{doc.id}/export")
    assert res.status_code == 200

    disposition = res.headers["content-disposition"]
    # Should not contain accents (ó, á, é, ñ), exclamation marks or parentheses
    assert "resolucion" in disposition
    assert "carteles" in disposition
    assert "espana" in disposition
    assert "ñ" not in disposition
    assert "á" not in disposition
    assert "&" not in disposition
    assert "!" not in disposition


def test_09_export_docx_structural_integrity(client: TestClient, setup_export_data):
    """Scenario 9: DOCX generated has correct structural layout (metadata table, paragraphs)."""
    doc_id = str(setup_export_data["entry_analysed"].id)
    res = client.get(f"/api/v1/documents/{doc_id}/export")
    assert res.status_code == 200

    docx_doc = docx.Document(io.BytesIO(res.content))

    # Must contain metadata table
    assert len(docx_doc.tables) >= 1
    table_text = " ".join(c.text for row in docx_doc.tables[0].rows for c in row.cells)
    assert "Boletín Oficial de Competencia" in table_text
    assert "Consejo de la Comisión de Competencia" in table_text

    # Verify footer exists
    footer = docx_doc.sections[0].footer
    assert footer is not None
    assert "HITCHINGS" in footer.paragraphs[0].text
    assert "Documento confidencial" in footer.paragraphs[0].text


# ==============================================================================
# 5. OpenAPI & Routing Invariants
# ==============================================================================


def test_10_export_route_registered_in_openapi():
    """Scenario 10: Verify that the export route is registered in the main FastAPI OpenAPI schema."""
    openapi_paths = app.openapi()["paths"]
    export_path = "/api/v1/documents/{document_id}/export"

    assert export_path in openapi_paths, f"Route {export_path} missing from OpenAPI schema"
    assert "get" in openapi_paths[export_path], f"GET method missing on {export_path}"

    responses = openapi_paths[export_path]["get"]["responses"]
    assert "200" in responses
    assert "404" in responses
    assert DOCX_MIME_TYPE in responses["200"]["content"]


def test_11_existing_document_routes_remain_functional(client: TestClient, setup_export_data):
    """Scenario 11: Ensure existing GET /documents and GET /documents/{id} routes continue working."""
    # 1. Listing
    res_list = client.get("/api/v1/documents?limit=10")
    assert res_list.status_code == 200
    data_list = res_list.json()
    assert "items" in data_list
    assert data_list["total"] >= 2

    # 2. Detail
    doc_id = str(setup_export_data["entry_analysed"].id)
    res_detail = client.get(f"/api/v1/documents/{doc_id}")
    assert res_detail.status_code == 200
    data_detail = res_detail.json()
    assert data_detail["id"] == doc_id
    assert data_detail["has_analysis"] is True
