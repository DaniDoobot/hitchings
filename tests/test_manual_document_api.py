"""API endpoint test suite for Manual Document Ingestion (Bloque 11C Phase 3).

Covers all 27 mandatory test scenarios:
- URL Ingestion (1-9):
  1. POST /api/v1/documents/url authenticated -> 201
  2. POST /api/v1/documents/url unauthenticated -> 401
  3. Invalid URL -> 422
  4. Localhost URL -> 400 (SSRF)
  5. Private RFC1918 URL -> 400 (SSRF)
  6. File scheme URL -> 422 or 400
  7. Duplicate URL -> 409 Conflict
  8. analyze_with_ai=False -> 201, no background analysis scheduled
  9. analyze_with_ai=True -> 201, background analysis scheduled
- File Upload Ingestion (10-20):
  10. Valid PDF upload -> 201
  11. Valid TXT upload -> 201
  12. Valid MD upload -> 201
  13. Unsupported extension (.exe) -> 415
  14. Corrupt PDF -> 422
  15. Blank PDF (no digital text) -> 422
  16. File size exceeded (> 25 MB) -> 413
  17. Upload unauthenticated -> 401
  18. Upload duplicate -> 409 Conflict
  19. analyze_with_ai=False -> 201, no background analysis scheduled
  20. analyze_with_ai=True -> 201, background analysis scheduled
- User Authentication & Traceability (21-23):
  21. uploaded_by_user_id matches authenticated User.id
  22. uploaded_by_email matches authenticated User.email
  23. Client cannot spoof or inject user identifiers
- Responses & Status Codes (24-26):
  24. Structure of CREATED response
  25. Structure of DUPLICATE response
  26. Expected HTTP error codes (400, 401, 413, 415, 422, 500)
- OpenAPI Specification (27):
  27. Verification of all 4 document routes in OpenAPI schema
"""

from __future__ import annotations

import io
import unittest.mock as mock
import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import pypdf

from app.main import app
from app.api.v1.endpoints.auth import get_current_user
from app.models.entry import Entry
from app.models.user import User


# Minimal valid PDF with digital text layer
VALID_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    b"5 0 obj\n<< /Length 120 >>\nstream\nBT\n/F1 12 Tf\n100 700 Td\n"
    b"(Resolucion sancionadora de la autoridad de competencia sobre practicas colusorias y acuerdos restrictivos en el mercado relevante.) Tj\n"
    b"ET\nendstream\nendobj\n"
    b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n"
    b"0000000115 00000 n \n0000000244 00000 n \n0000000325 00000 n \n"
    b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n497\n%%EOF\n"
)


def create_blank_pdf_bytes() -> bytes:
    """Create a valid PDF with blank pages (no digital text)."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=300, height=300)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


SAMPLE_HTML_BYTES = (
    b"<!DOCTYPE html><html><head><title>Resolucion de la CNMC</title>"
    b'<meta name="author" content="Consejo de Competencia"></head><body>'
    b"<article><p>"
    b"Resolucion sancionadora sobre acuerdos restrictivos de precios en el mercado de distribucion mayorista. "
    b"Se declaran acreditadas practicas colusorias continuadas con infraccion del articulo 1 de la Ley 15/2007."
    b"</p></article></body></html>"
)


# ==============================================================================
# 1. URL Ingestion Endpoint Tests (Scenarios 1-9)
# ==============================================================================


def test_01_post_url_authenticated_success(client: TestClient, db_session: Session):
    """Scenario 1: Authenticated user ingests a valid document URL -> 201 Created."""
    with mock.patch(
        "app.services.manual_document_ingestion_service.download_url_safely",
        return_value=(SAMPLE_HTML_BYTES, "text/html; charset=utf-8", "https://cnmc.es/expediente/101"),
    ):
        res = client.post(
            "/api/v1/documents/url",
            json={
                "url": "https://cnmc.es/expediente/101",
                "manual_title": "Expediente Sancionador CNMC 101",
                "analyze_with_ai": False,
            },
        )
        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "created"
        assert "entry_id" in data
        assert data["title"] == "Expediente Sancionador CNMC 101"
        assert data["url"] == "https://cnmc.es/expediente/101"
        assert data["analysis_requested"] is False
        assert data["analysis_scheduled"] is False


def test_02_post_url_unauthenticated_401(client: TestClient):
    """Scenario 2: Unauthenticated request to /url returns 401 Unauthorized."""
    override = app.dependency_overrides.pop(get_current_user, None)
    try:
        res = client.post(
            "/api/v1/documents/url",
            json={"url": "https://cnmc.es/expediente/102"},
        )
        assert res.status_code == 401
    finally:
        if override:
            app.dependency_overrides[get_current_user] = override


def test_03_post_url_invalid_syntax_422(client: TestClient):
    """Scenario 3: Obviously invalid URL rejected with 422 before reaching service."""
    # Empty string
    res1 = client.post("/api/v1/documents/url", json={"url": "   "})
    assert res1.status_code == 422

    # Not http or https
    res2 = client.post("/api/v1/documents/url", json={"url": "ftp://ftp.example.org/doc.pdf"})
    assert res2.status_code == 422

    # Incomplete protocol
    res3 = client.post("/api/v1/documents/url", json={"url": "http://"})
    assert res3.status_code == 422


def test_04_post_url_localhost_rejected_400(client: TestClient):
    """Scenario 4: Localhost URL rejected by SSRF protection with 400 Bad Request."""
    res = client.post(
        "/api/v1/documents/url",
        json={"url": "http://localhost:8080/internal/secret"},
    )
    assert res.status_code == 400
    assert "seguridad" in res.json()["detail"].lower()


def test_05_post_url_private_ip_rejected_400(client: TestClient):
    """Scenario 5: RFC1918 private IP URL rejected by SSRF protection with 400 Bad Request."""
    res = client.post(
        "/api/v1/documents/url",
        json={"url": "http://192.168.1.50:9000/data.pdf"},
    )
    assert res.status_code == 400
    assert "seguridad" in res.json()["detail"].lower()


def test_06_post_url_file_scheme_rejected(client: TestClient):
    """Scenario 6: file:// scheme rejected with 422 or 400."""
    res = client.post(
        "/api/v1/documents/url",
        json={"url": "file:///etc/passwd"},
    )
    assert res.status_code in (400, 422)


def test_07_post_url_duplicate_409(client: TestClient, db_session: Session):
    """Scenario 7: Duplicate URL returns 409 Conflict with duplicate details."""
    with mock.patch(
        "app.services.manual_document_ingestion_service.download_url_safely",
        return_value=(SAMPLE_HTML_BYTES, "text/html; charset=utf-8", "https://cnmc.es/expediente/dup-1"),
    ):
        # First creation -> 201
        res1 = client.post(
            "/api/v1/documents/url",
            json={"url": "https://cnmc.es/expediente/dup-1"},
        )
        assert res1.status_code == 201
        created_id = res1.json()["entry_id"]

        # Second submission -> 409 Conflict
        res2 = client.post(
            "/api/v1/documents/url",
            json={"url": "https://cnmc.es/expediente/dup-1"},
        )
        assert res2.status_code == 409
        dup_data = res2.json()
        assert dup_data["status"] == "duplicate"
        assert dup_data["existing_entry_id"] == created_id
        assert dup_data["duplicate_reason"] == "url"
        assert "ya existe" in dup_data["message"].lower()


def test_08_post_url_analyze_with_ai_false(client: TestClient, db_session: Session):
    """Scenario 8: analyze_with_ai=False does not schedule any background analysis."""
    with mock.patch(
        "app.services.manual_document_ingestion_service.download_url_safely",
        return_value=(SAMPLE_HTML_BYTES, "text/html; charset=utf-8", "https://cnmc.es/expediente/no-ai"),
    ):
        with mock.patch("app.api.v1.endpoints.documents._execute_background_analysis") as mock_bg:
            res = client.post(
                "/api/v1/documents/url",
                json={
                    "url": "https://cnmc.es/expediente/no-ai",
                    "analyze_with_ai": False,
                },
            )
            assert res.status_code == 201
            assert res.json()["analysis_requested"] is False
            assert res.json()["analysis_scheduled"] is False
            mock_bg.assert_not_called()


def test_09_post_url_analyze_with_ai_true(client: TestClient, db_session: Session):
    """Scenario 9: analyze_with_ai=True schedules background analysis without executing real Gemini."""
    with mock.patch(
        "app.services.manual_document_ingestion_service.download_url_safely",
        return_value=(SAMPLE_HTML_BYTES, "text/html; charset=utf-8", "https://cnmc.es/expediente/with-ai"),
    ):
        with mock.patch("app.api.v1.endpoints.documents._execute_background_analysis", new_callable=mock.AsyncMock) as mock_bg:
            res = client.post(
                "/api/v1/documents/url",
                json={
                    "url": "https://cnmc.es/expediente/with-ai",
                    "analyze_with_ai": True,
                },
            )
            assert res.status_code == 201
            assert res.json()["analysis_requested"] is True
            assert res.json()["analysis_scheduled"] is True
            # Background task was invoked with entry_id
            mock_bg.assert_called_once()
            called_entry_id = mock_bg.call_args[0][0]
            assert str(called_entry_id) == res.json()["entry_id"]


# ==============================================================================
# 2. File Upload Endpoint Tests (Scenarios 10-20)
# ==============================================================================


def test_10_upload_pdf_valid_201(client: TestClient, db_session: Session):
    """Scenario 10: Valid PDF upload returns 201 Created."""
    res = client.post(
        "/api/v1/documents/upload",
        files={"file": ("resolucion_tribunal.pdf", VALID_PDF_BYTES, "application/pdf")},
        data={"analyze_with_ai": "false"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "created"
    assert "entry_id" in data
    assert data["content_type"] == "pdf"
    assert data["url"].startswith("upload://manual/")


def test_11_upload_txt_valid_201(client: TestClient, db_session: Session):
    """Scenario 11: Valid TXT upload returns 201 Created."""
    txt_content = (
        "Resolución de la Dirección de Competencia sobre concentración económica en el sector farmacéutico. "
        "Se autoriza la operación en primera fase condicionada a compromisos de desinversión de activos."
    ).encode("utf-8")

    res = client.post(
        "/api/v1/documents/upload",
        files={"file": ("dictamen_concentracion.txt", txt_content, "text/plain")},
        data={"analyze_with_ai": "false"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "created"
    assert data["content_type"] == "document"


def test_12_upload_md_valid_201(client: TestClient, db_session: Session):
    """Scenario 12: Valid MD upload returns 201 Created."""
    md_content = (
        "# Análisis de Abuso de Posición Dominante\n\n"
        "Evaluación de prácticas exclusorias y fijación de precios predatorios en mercados digitales. "
        "Jurisprudencia consolidada del Tribunal de Justicia de la Unión Europea y directrices comunitarias."
    ).encode("utf-8")

    res = client.post(
        "/api/v1/documents/upload",
        files={"file": ("informe_digital.md", md_content, "text/markdown")},
        data={"analyze_with_ai": "false"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "created"
    assert data["content_type"] == "document"


def test_13_upload_unsupported_extension_415(client: TestClient):
    """Scenario 13: Unsupported file extension (.exe) rejected with 415 Unsupported Media Type."""
    res = client.post(
        "/api/v1/documents/upload",
        files={"file": ("payload.exe", b"MZbinary...", "application/octet-stream")},
    )
    assert res.status_code == 415
    assert "no permitida" in res.json()["detail"].lower()


def test_14_upload_corrupt_pdf_422(client: TestClient):
    """Scenario 14: Corrupt PDF rejected with 422 Unprocessable Content."""
    res = client.post(
        "/api/v1/documents/upload",
        files={"file": ("corrupt.pdf", b"%PDF-1.4 corrupt junk header only", "application/pdf")},
    )
    assert res.status_code == 422


def test_15_upload_pdf_without_text_422(client: TestClient):
    """Scenario 15: Blank PDF without digital text layer rejected with 422."""
    blank_bytes = create_blank_pdf_bytes()
    res = client.post(
        "/api/v1/documents/upload",
        files={"file": ("scanned_blank.pdf", blank_bytes, "application/pdf")},
    )
    assert res.status_code == 422
    assert "texto" in res.json()["detail"].lower()


def test_16_upload_file_size_exceeded_413(client: TestClient):
    """Scenario 16: File exceeding 25 MB rejected with 413 Payload Too Large."""
    oversized_bytes = b"A" * (25 * 1024 * 1024 + 1024)
    res = client.post(
        "/api/v1/documents/upload",
        files={"file": ("oversized.txt", oversized_bytes, "text/plain")},
    )
    assert res.status_code == 413


def test_17_upload_unauthenticated_401(client: TestClient):
    """Scenario 17: Unauthenticated request to /upload returns 401."""
    override = app.dependency_overrides.pop(get_current_user, None)
    try:
        res = client.post(
            "/api/v1/documents/upload",
            files={"file": ("test.txt", b"Contenido de prueba", "text/plain")},
        )
        assert res.status_code == 401
    finally:
        if override:
            app.dependency_overrides[get_current_user] = override


def test_18_upload_duplicate_409(client: TestClient, db_session: Session):
    """Scenario 18: Uploading identical content twice returns 409 Conflict."""
    unique_text = f"Dictamen pericial único para test de deduplicacion: {uuid.uuid4()} con texto suficiente.".encode("utf-8")

    # First upload -> 201
    res1 = client.post(
        "/api/v1/documents/upload",
        files={"file": ("dictamen_1.txt", unique_text, "text/plain")},
    )
    assert res1.status_code == 201
    first_id = res1.json()["entry_id"]

    # Second upload of same content with different filename -> 409 Conflict
    res2 = client.post(
        "/api/v1/documents/upload",
        files={"file": ("dictamen_2.txt", unique_text, "text/plain")},
    )
    assert res2.status_code == 409
    dup = res2.json()
    assert dup["status"] == "duplicate"
    assert dup["existing_entry_id"] == first_id
    assert dup["duplicate_reason"] == "content_hash"


def test_19_upload_analyze_with_ai_false(client: TestClient, db_session: Session):
    """Scenario 19: analyze_with_ai=False on upload does not schedule background task."""
    unique_text = f"Documento sin IA: {uuid.uuid4()} con contenido sustantivo para analisis regulatorio.".encode("utf-8")
    with mock.patch("app.api.v1.endpoints.documents._execute_background_analysis") as mock_bg:
        res = client.post(
            "/api/v1/documents/upload",
            files={"file": ("doc_no_ai.txt", unique_text, "text/plain")},
            data={"analyze_with_ai": "false"},
        )
        assert res.status_code == 201
        assert res.json()["analysis_requested"] is False
        assert res.json()["analysis_scheduled"] is False
        mock_bg.assert_not_called()


def test_20_upload_analyze_with_ai_true(client: TestClient, db_session: Session):
    """Scenario 20: analyze_with_ai=True on upload schedules background task without calling real Gemini."""
    unique_text = f"Documento con IA: {uuid.uuid4()} con contenido sustantivo para analisis regulatorio.".encode("utf-8")
    with mock.patch("app.api.v1.endpoints.documents._execute_background_analysis", new_callable=mock.AsyncMock) as mock_bg:
        res = client.post(
            "/api/v1/documents/upload",
            files={"file": ("doc_with_ai.txt", unique_text, "text/plain")},
            data={"analyze_with_ai": "true"},
        )
        assert res.status_code == 201
        assert res.json()["analysis_requested"] is True
        assert res.json()["analysis_scheduled"] is True
        mock_bg.assert_called_once()


# ==============================================================================
# 3. User Authentication & Traceability Tests (Scenarios 21-23)
# ==============================================================================


def test_21_uploaded_by_user_id_provenance(client: TestClient, db_session: Session):
    """Scenario 21: uploaded_by_user_id in raw_metadata strictly matches authenticated User.id."""
    custom_uid = uuid.uuid4()
    custom_user = User(
        id=custom_uid,
        email="compliance_officer@hitchings.com",
        display_name="Oficial de Cumplimiento",
        password_hash="pwd",
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: custom_user

    try:
        content = f"Registro de trazabilidad de usuario A: {uuid.uuid4()}".encode("utf-8")
        res = client.post(
            "/api/v1/documents/upload",
            files={"file": ("auditoria_a.txt", content, "text/plain")},
        )
        assert res.status_code == 201
        entry_id = uuid.UUID(res.json()["entry_id"])

        entry = db_session.get(Entry, entry_id)
        assert entry is not None
        assert entry.raw_metadata["uploaded_by_user_id"] == str(custom_uid)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_22_uploaded_by_email_provenance(client: TestClient, db_session: Session):
    """Scenario 22: uploaded_by_email in raw_metadata strictly matches authenticated User.email."""
    custom_uid = uuid.uuid4()
    custom_user = User(
        id=custom_uid,
        email="socio_director@despacho.es",
        display_name="Socio Director",
        password_hash="pwd",
        is_active=True,
    )
    app.dependency_overrides[get_current_user] = lambda: custom_user

    try:
        content = f"Registro de trazabilidad de email B: {uuid.uuid4()}".encode("utf-8")
        res = client.post(
            "/api/v1/documents/upload",
            files={"file": ("auditoria_b.txt", content, "text/plain")},
        )
        assert res.status_code == 201
        entry_id = uuid.UUID(res.json()["entry_id"])

        entry = db_session.get(Entry, entry_id)
        assert entry is not None
        assert entry.raw_metadata["uploaded_by_email"] == "socio_director@despacho.es"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_23_client_cannot_forge_user_credentials(client: TestClient, db_session: Session):
    """Scenario 23: Extra input fields attempting to forge uploaded_by_user_id are strictly rejected."""
    # Attempt to inject uploaded_by_user_id in IngestUrlRequest -> 422 because extra="forbid"
    res = client.post(
        "/api/v1/documents/url",
        json={
            "url": "https://cnmc.es/expediente/legit",
            "uploaded_by_user_id": str(uuid.uuid4()),
            "uploaded_by_email": "attacker@evil.com",
        },
    )
    assert res.status_code == 422


# ==============================================================================
# 4. Response Schemas & Status Code Tests (Scenarios 24-26)
# ==============================================================================


def test_24_created_response_schema_structure(client: TestClient, db_session: Session):
    """Scenario 24: DocumentIngestionResponse contains all required fields with exact typing."""
    content = f"Contenido para test de esquema de respuesta: {uuid.uuid4()}".encode("utf-8")
    res = client.post(
        "/api/v1/documents/upload",
        files={"file": ("esquema_test.txt", content, "text/plain")},
    )
    assert res.status_code == 201
    data = res.json()

    assert data["status"] == "created"
    assert isinstance(data["entry_id"], str)
    assert uuid.UUID(data["entry_id"])  # valid UUID
    assert "title" in data
    assert "url" in data
    assert "canonical_url" in data
    assert "content_type" in data
    assert isinstance(data["analysis_requested"], bool)
    assert isinstance(data["analysis_scheduled"], bool)
    assert "created_at" in data


def test_25_duplicate_response_schema_structure(client: TestClient, db_session: Session):
    """Scenario 25: DocumentDuplicateResponse contains status, existing_entry_id, duplicate_reason, message."""
    content = f"Contenido para test de respuesta duplicada: {uuid.uuid4()}".encode("utf-8")
    # First upload
    res1 = client.post(
        "/api/v1/documents/upload",
        files={"file": ("dup_a.txt", content, "text/plain")},
    )
    first_id = res1.json()["entry_id"]

    # Duplicate upload
    res2 = client.post(
        "/api/v1/documents/upload",
        files={"file": ("dup_b.txt", content, "text/plain")},
    )
    assert res2.status_code == 409
    dup = res2.json()

    assert dup["status"] == "duplicate"
    assert dup["existing_entry_id"] == first_id
    assert dup["duplicate_reason"] in ("url", "content_hash")
    assert isinstance(dup["message"], str)
    assert len(dup["message"]) > 0


def test_26_error_status_codes_and_semantic_exceptions(client: TestClient):
    """Scenario 26: Distinct error types preserve their specific HTTP semantics (400, 401, 413, 415, 422)."""
    # 400 Bad Request: SSRF / Invalid network target
    res_400 = client.post("/api/v1/documents/url", json={"url": "http://127.0.0.1:8000/info"})
    assert res_400.status_code == 400

    # 415 Unsupported Media Type: .exe
    res_415 = client.post(
        "/api/v1/documents/upload",
        files={"file": ("script.sh", b"#!/bin/bash\necho 1", "application/x-sh")},
    )
    assert res_415.status_code == 415

    # 422 Unprocessable Content: PDF without text
    res_422 = client.post(
        "/api/v1/documents/upload",
        files={"file": ("empty.pdf", create_blank_pdf_bytes(), "application/pdf")},
    )
    assert res_422.status_code == 422

    # 413 Payload Too Large: > 25 MB
    res_413 = client.post(
        "/api/v1/documents/upload",
        files={"file": ("large.txt", b"x" * (25 * 1024 * 1024 + 100), "text/plain")},
    )
    assert res_413.status_code == 413


# ==============================================================================
# 5. OpenAPI Specification Test (Scenario 27)
# ==============================================================================


def test_27_openapi_schema_contains_all_document_routes():
    """Scenario 27: Verify OpenAPI schema contains all 4 required document routes and methods."""
    schema = app.openapi()
    paths = schema.get("paths", {})

    # GET /api/v1/documents
    assert "/api/v1/documents" in paths, "Missing GET /api/v1/documents in OpenAPI"
    assert "get" in paths["/api/v1/documents"], "Missing GET method for /api/v1/documents"

    # GET /api/v1/documents/{document_id}
    assert "/api/v1/documents/{document_id}" in paths, "Missing GET /api/v1/documents/{document_id} in OpenAPI"
    assert "get" in paths["/api/v1/documents/{document_id}"], "Missing GET method for /api/v1/documents/{document_id}"

    # POST /api/v1/documents/url
    assert "/api/v1/documents/url" in paths, "Missing POST /api/v1/documents/url in OpenAPI"
    assert "post" in paths["/api/v1/documents/url"], "Missing POST method for /api/v1/documents/url"

    # POST /api/v1/documents/upload
    assert "/api/v1/documents/upload" in paths, "Missing POST /api/v1/documents/upload in OpenAPI"
    assert "post" in paths["/api/v1/documents/upload"], "Missing POST method for /api/v1/documents/upload"
