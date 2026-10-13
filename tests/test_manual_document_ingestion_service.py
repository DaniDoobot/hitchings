"""Comprehensive test suite for ManualDocumentIngestionService (Bloque 11C Phase 2).

Covers all 37 mandatory test scenarios:
- URL ingestion (HTML, PDF, title, author, date, canonical, 404, 403, timeout)
- SSRF security (localhost, 127.0.0.1, 10.x, 172.16.x, 192.168.x, 169.254.169.254, file://, redirect to private IP)
- File upload ingestion (PDF, TXT, MD, invalid extension, size limit, blank PDF, corrupt PDF, path traversal)
- Deduplication (same URL, same content different URL, no second Entry created)
- Persistence (canonical source, raw_metadata, synthetic URL, content_hash)
- AI Analysis scheduling (analyze_with_ai=False, analyze_with_ai=True with mock provider)
"""

from __future__ import annotations

import io
import socket
import unittest.mock as mock
import uuid
from datetime import datetime, timezone
from pathlib import Path
import pytest
import httpx
import pypdf
from sqlalchemy.orm import Session

from app.models.analysis import AnalysisPromptVersion, EntryAnalysis, AnalysisCall
from app.models.entry import Entry
from app.models.source import (
    Source,
    SourceType,
    MANUAL_UPLOAD_SOURCE_ID,
    MANUAL_UPLOAD_SOURCE_NAME,
    get_or_create_manual_source,
)
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.providers.ai.base import BaseAIProvider, AIProviderResult
from app.services.manual_document_ingestion_service import (
    ManualDocumentIngestionService,
    ManualIngestionStatus,
    ManualIngestionError,
    SSRFSecurityError,
    UnsupportedContentTypeError,
    DocumentExtractionError,
    FileSizeLimitExceededError,
    sanitize_filename,
    validate_url_security,
    extract_pdf_digital_text,
    extract_plain_text,
    normalize_pdf_page_text,
    clean_document_title,
    extract_pdf_metadata_title,
    MAX_FILE_BYTES,
)


# Minimal raw PDF containing valid digital text layer
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


from app.providers.ai.mock import MockAIProvider


# ==============================================================================
# 1. URL Ingestion Tests (Scenarios 1 to 12)
# ==============================================================================


def test_01_url_html_valid(db_session: Session):
    """Scenario 1: Valid HTML URL ingestion creates Entry."""
    html_content = (
        "<!DOCTYPE html><html><head><title>Resolución CNMC - Competencia</title>"
        '<meta name="author" content="Dra. Carmen Martínez">'
        '<meta property="article:published_time" content="2026-05-10T12:00:00Z">'
        "</head><body><main><h1>Resolución CNMC - Competencia</h1>"
        "<p>La Comisión Nacional de los Mercados y la Competencia ha resuelto el expediente sancionador "
        "relativo a acuerdos restrictivos en el sector de distribución eléctrica.</p></main></body></html>"
    )

    def handler(request: httpx.Request):
        return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, content=html_content.encode("utf-8"))

    transport = httpx.MockTransport(handler)
    mock_client = httpx.Client(transport=transport)

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        res = svc.ingest_url(
            db=db_session,
            url="https://example.com/resolucion-123",
            client=mock_client,
        )
        db_session.commit()

    assert res.status == ManualIngestionStatus.CREATED
    assert res.entry is not None
    assert "Resolución CNMC - Competencia" in res.entry.title
    assert "sector de distribución eléctrica" in res.entry.content
    assert res.entry.source_id == MANUAL_UPLOAD_SOURCE_ID


def test_02_url_title_extraction(db_session: Session):
    """Scenario 2: HTML Title extraction (automatic and manual override)."""
    html_content = "<html><head><title>Título Original del Sitio</title></head><body><p>Texto sustantivo sobre derecho de la competencia con suficiente longitud para superar el limite minimo.</p></body></html>"

    def handler(request: httpx.Request):
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=html_content.encode("utf-8"))

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        # Case A: Auto-extracted
        res_auto = svc.ingest_url(db=db_session, url="https://example.com/auto-title", client=mock_client)
        assert res_auto.entry.title == "Título Original del Sitio"

        # Case B: Manual override
        res_manual = svc.ingest_url(
            db=db_session,
            url="https://example.com/manual-title",
            manual_title="Título Manual Personalizado",
            client=mock_client,
        )
        assert res_manual.entry.title == "Título Manual Personalizado"


def test_03_url_author_extraction(db_session: Session):
    """Scenario 3: Author extraction from meta tags."""
    html = '<html><head><meta name="author" content="Prof. Daniel Hitchings"></head><body><p>Texto legal con mas de cincuenta caracteres para ser considerado contenido valido por el sistema.</p></body></html>'
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"Content-Type": "text/html"}, content=html.encode("utf-8"))))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        res = svc.ingest_url(db=db_session, url="https://example.com/author-test", client=mock_client)
        assert res.entry.author == "Prof. Daniel Hitchings"


def test_04_url_date_extraction(db_session: Session):
    """Scenario 4: Date extraction from article:published_time."""
    html = '<html><head><meta property="article:published_time" content="2026-08-15T09:30:00Z"></head><body><p>Texto legal con mas de cincuenta caracteres para ser considerado contenido valido por el sistema.</p></body></html>'
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"Content-Type": "text/html"}, content=html.encode("utf-8"))))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        res = svc.ingest_url(db=db_session, url="https://example.com/date-test", client=mock_client)
        assert res.entry.published_at is not None
        assert res.entry.published_at.year == 2026
        assert res.entry.published_at.month == 8


def test_05_url_content_cleaning(db_session: Session):
    """Scenario 5: Clean editorial content strips scripts and styling."""
    html = (
        "<html><head><style>.ad { display:none; }</style></head><body>"
        "<nav>Navigation Menu</nav>"
        "<main><p>Primer párrafo doctrinal sobre abuso de posición dominante en plataformas digitales.</p>"
        "<script>alert('malicious')</script>"
        "<p>Segundo párrafo con fundamentos jurídicos y citas jurisprudenciales aplicables.</p></main>"
        "<footer>Pie de página y avisos legales</footer></body></html>"
    )
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"Content-Type": "text/html"}, content=html.encode("utf-8"))))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        res = svc.ingest_url(db=db_session, url="https://example.com/clean-content", client=mock_client)
        assert "alert(" not in res.entry.content
        assert "Navigation Menu" not in res.entry.content
        assert "abuso de posición dominante" in res.entry.content
        assert "fundamentos jurídicos" in res.entry.content


def test_06_url_canonical_url_usage(db_session: Session):
    """Scenario 6: Canonical URL extracted from link rel=canonical if same site."""
    html = '<html><head><link rel="canonical" href="https://example.com/canonical-article"></head><body><p>Texto legal con mas de cincuenta caracteres para ser considerado contenido valido por el sistema.</p></body></html>'
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"Content-Type": "text/html"}, content=html.encode("utf-8"))))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        res = svc.ingest_url(db=db_session, url="https://example.com/original-article?utm_source=twitter", client=mock_client)
        assert res.entry.canonical_url == "https://example.com/canonical-article"


def test_07_url_pdf_valid(db_session: Session):
    """Scenario 7: URL returning application/pdf is successfully ingested."""
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=VALID_PDF_BYTES)))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        res = svc.ingest_url(db=db_session, url="https://example.com/decision.pdf", client=mock_client)
        assert res.status == ManualIngestionStatus.CREATED
        assert "acuerdos restrictivos" in res.entry.content
        assert res.entry.content_type == "pdf"


def test_08_url_pdf_with_text(db_session: Session):
    """Scenario 8: PDF text layer extraction saves content in entry."""
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=VALID_PDF_BYTES)))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        res = svc.ingest_url(db=db_session, url="https://example.com/full_text.pdf", client=mock_client)
        assert len(res.entry.content) > 50


def test_09_url_pdf_insufficient_text(db_session: Session):
    """Scenario 9: PDF URL without digital text raises DocumentExtractionError."""
    blank_pdf = create_blank_pdf_bytes()
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=blank_pdf)))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        with pytest.raises(DocumentExtractionError, match="suficiente texto digital"):
            svc.ingest_url(db=db_session, url="https://example.com/blank.pdf", client=mock_client)


def test_10_url_http_404(db_session: Session):
    """Scenario 10: HTTP 404 raises ManualIngestionError."""
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        with pytest.raises(ManualIngestionError, match="Error HTTP 404"):
            svc.ingest_url(db=db_session, url="https://example.com/not-found", client=mock_client)


def test_11_url_http_403(db_session: Session):
    """Scenario 11: HTTP 403 raises ManualIngestionError."""
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        with pytest.raises(ManualIngestionError, match="Error HTTP 403"):
            svc.ingest_url(db=db_session, url="https://example.com/forbidden", client=mock_client)


def test_12_url_timeout(db_session: Session):
    """Scenario 12: Network timeout raises ManualIngestionError."""
    def timeout_handler(r):
        raise httpx.ReadTimeout("Timeout connecting to remote host")

    mock_client = httpx.Client(transport=httpx.MockTransport(timeout_handler))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        with pytest.raises(ManualIngestionError, match="Tiempo de espera agotado"):
            svc.ingest_url(db=db_session, url="https://example.com/timeout", client=mock_client)


# ==============================================================================
# 2. SSRF Protection Tests (Scenarios 13 to 20)
# ==============================================================================


def test_13_ssrf_localhost():
    """Scenario 13: Blocking localhost hostname."""
    with pytest.raises(SSRFSecurityError, match="localhost bloqueado"):
        validate_url_security("http://localhost/admin")
    with pytest.raises(SSRFSecurityError, match="localhost bloqueado"):
        validate_url_security("http://localhost:8080/metrics")


def test_14_ssrf_127_0_0_1():
    """Scenario 14: Blocking loopback 127.0.0.1 IP."""
    with pytest.raises(SSRFSecurityError, match="IP privada/reservada"):
        validate_url_security("http://127.0.0.1/internal")
    with pytest.raises(SSRFSecurityError, match="IP privada/reservada"):
        validate_url_security("http://127.0.0.2:9000/info")


def test_15_ssrf_10_x_x_x():
    """Scenario 15: Blocking RFC1918 10.0.0.0/8 network."""
    with pytest.raises(SSRFSecurityError, match="IP privada/reservada"):
        validate_url_security("http://10.0.0.1/secret")
    with pytest.raises(SSRFSecurityError, match="IP privada/reservada"):
        validate_url_security("https://10.254.254.1/secret")


def test_16_ssrf_172_16_x_x():
    """Scenario 16: Blocking RFC1918 172.16.0.0/12 network."""
    with pytest.raises(SSRFSecurityError, match="IP privada/reservada"):
        validate_url_security("http://172.16.0.1/status")
    with pytest.raises(SSRFSecurityError, match="IP privada/reservada"):
        validate_url_security("http://172.31.255.255/status")


def test_17_ssrf_192_168_x_x():
    """Scenario 17: Blocking RFC1918 192.168.0.0/16 network."""
    with pytest.raises(SSRFSecurityError, match="IP privada/reservada"):
        validate_url_security("http://192.168.1.1/router")
    with pytest.raises(SSRFSecurityError, match="IP privada/reservada"):
        validate_url_security("http://192.168.0.254/router")


def test_18_ssrf_cloud_metadata():
    """Scenario 18: Blocking cloud metadata 169.254.169.254."""
    with pytest.raises(SSRFSecurityError, match="IP privada/reservada"):
        validate_url_security("http://169.254.169.254/latest/meta-data/")


def test_19_ssrf_file_scheme():
    """Scenario 19: Blocking file:// and non-http schemes."""
    with pytest.raises(SSRFSecurityError, match="Esquema no permitido"):
        validate_url_security("file:///etc/passwd")
    with pytest.raises(SSRFSecurityError, match="Esquema no permitido"):
        validate_url_security("ftp://ftp.example.com/file.txt")


def test_20_ssrf_redirect_to_private_ip(db_session: Session):
    """Scenario 20: Blocking redirect chain targeting private network."""
    def redirect_handler(request: httpx.Request):
        if "public.example.com" in request.url.host:
            # Redirect to internal 127.0.0.1
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8000/internal-data"})
        return httpx.Response(200, content=b"Secret data")

    mock_client = httpx.Client(transport=httpx.MockTransport(redirect_handler))

    def fake_getaddrinfo(host, port):
        if host == "public.example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]

    with mock.patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
        svc = ManualDocumentIngestionService()
        with pytest.raises(SSRFSecurityError, match="IP privada/reservada"):
            svc.ingest_url(db=db_session, url="https://public.example.com/start", client=mock_client)


# ==============================================================================
# 3. File Upload Tests (Scenarios 21 to 28)
# ==============================================================================


def test_21_file_pdf_valid(db_session: Session):
    """Scenario 21: Valid PDF upload succeeds."""
    svc = ManualDocumentIngestionService()
    res = svc.ingest_file(
        db=db_session,
        file_bytes=VALID_PDF_BYTES,
        filename="sentencia_audiencia.pdf",
        content_type="application/pdf",
        user_email="test@hitchings.com",
    )
    assert res.status == ManualIngestionStatus.CREATED
    assert res.entry is not None
    assert "sentencia audiencia" in res.entry.title.lower()
    assert res.entry.content_type == "pdf"
    assert res.entry.url.startswith("upload://manual/")
    assert res.entry.raw_metadata["uploaded_by_email"] == "test@hitchings.com"


def test_22_file_txt_valid(db_session: Session):
    """Scenario 22: Valid TXT upload succeeds."""
    text_content = "Dictamen legal emitido sobre la legalidad de los acuerdos verticales de distribucion exclusiva en la Union Europea con mas de cincuenta caracteres."
    svc = ManualDocumentIngestionService()
    res = svc.ingest_file(
        db=db_session,
        file_bytes=text_content.encode("utf-8"),
        filename="dictamen_vertical.txt",
        content_type="text/plain",
    )
    assert res.status == ManualIngestionStatus.CREATED
    assert "acuerdos verticales" in res.entry.content
    assert res.entry.content_type == "document"


def test_23_file_md_valid(db_session: Session):
    """Scenario 23: Valid Markdown upload succeeds."""
    md_content = "# Análisis de Daños Antimonopolio\n\nEl cálculo del sobreprecio en cárteles de camiones requiere modelos econometricos avanzados con mas de cincuenta caracteres utiles."
    svc = ManualDocumentIngestionService()
    res = svc.ingest_file(
        db=db_session,
        file_bytes=md_content.encode("utf-8"),
        filename="analisis_sobreprecio.md",
        content_type="text/markdown",
    )
    assert res.status == ManualIngestionStatus.CREATED
    assert "sobreprecio en cárteles" in res.entry.content


def test_24_file_disallowed_extension(db_session: Session):
    """Scenario 24: Disallowed file extension raises UnsupportedContentTypeError."""
    svc = ManualDocumentIngestionService()
    with pytest.raises(UnsupportedContentTypeError, match="Extensión '.exe' no permitida"):
        svc.ingest_file(db=db_session, file_bytes=b"malicious_bytes", filename="malware.exe")

    with pytest.raises(UnsupportedContentTypeError, match="Extensión '.docx' no permitida"):
        svc.ingest_file(db=db_session, file_bytes=b"word_doc_bytes", filename="document.docx")


def test_25_file_size_exceeded(db_session: Session):
    """Scenario 25: File exceeding 25 MB raises FileSizeLimitExceededError."""
    svc = ManualDocumentIngestionService()
    huge_bytes = b"X" * (MAX_FILE_BYTES + 1024)
    with pytest.raises(FileSizeLimitExceededError, match="supera el tamaño máximo"):
        svc.ingest_file(db=db_session, file_bytes=huge_bytes, filename="huge.pdf")


def test_26_file_pdf_without_text(db_session: Session):
    """Scenario 26: PDF without digital text raises DocumentExtractionError."""
    blank_pdf = create_blank_pdf_bytes()
    svc = ManualDocumentIngestionService()
    with pytest.raises(DocumentExtractionError, match="suficiente texto digital"):
        svc.ingest_file(db=db_session, file_bytes=blank_pdf, filename="scanned.pdf")


def test_27_file_pdf_corrupt(db_session: Session):
    """Scenario 27: Corrupt PDF raises DocumentExtractionError."""
    svc = ManualDocumentIngestionService()
    with pytest.raises(DocumentExtractionError, match="no es un documento PDF válido"):
        svc.ingest_file(db=db_session, file_bytes=b"corrupt_header_bytes", filename="corrupt.pdf")


def test_28_file_path_traversal_sanitized(db_session: Session):
    """Scenario 28: Path traversal in filename is sanitized safely."""
    raw_name = "../../../etc/passwd.pdf"
    cleaned = sanitize_filename(raw_name)
    assert cleaned == "passwd.pdf"
    assert ".." not in cleaned
    assert "/" not in cleaned
    assert "\\" not in cleaned


# ==============================================================================
# 4. Deduplication Tests (Scenarios 29 to 31)
# ==============================================================================


def test_29_dedupe_same_url(db_session: Session):
    """Scenario 29: Ingesting the exact same URL twice returns DUPLICATE status."""
    html = "<html><body><p>Texto legal con mas de cincuenta caracteres para ser considerado contenido valido por el sistema.</p></body></html>"
    mock_client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"Content-Type": "text/html"}, content=html.encode("utf-8"))))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        res1 = svc.ingest_url(db=db_session, url="https://example.com/repeat-url", client=mock_client)
        db_session.commit()
        assert res1.status == ManualIngestionStatus.CREATED

        res2 = svc.ingest_url(db=db_session, url="https://example.com/repeat-url", client=mock_client)
        assert res2.status == ManualIngestionStatus.DUPLICATE
        assert res2.existing_entry_id == res1.entry.id
        assert res2.duplicate_reason == "url"


def test_30_dedupe_same_content_different_url(db_session: Session):
    """Scenario 30: Ingesting the same textual content under different uploads returns DUPLICATE."""
    text_sample = "Contenido idéntico sobre acuerdos anticompetitivos en el sector del transporte con mas de cincuenta caracteres requeridos."
    svc = ManualDocumentIngestionService()

    res1 = svc.ingest_file(db=db_session, file_bytes=text_sample.encode("utf-8"), filename="doc1.txt")
    db_session.commit()
    assert res1.status == ManualIngestionStatus.CREATED

    res2 = svc.ingest_file(db=db_session, file_bytes=text_sample.encode("utf-8"), filename="doc2.txt")
    assert res2.status == ManualIngestionStatus.DUPLICATE
    assert res2.existing_entry_id == res1.entry.id
    assert res2.duplicate_reason == "content_hash"


def test_31_dedupe_no_second_entry_created(db_session: Session):
    """Scenario 31: Verifies total Entry count does not increase on duplicate ingestion."""
    text_sample = "Prueba de conteo estricto de entradas en la base de datos documental con suficiente longitud de caracteres."
    svc = ManualDocumentIngestionService()

    count_before = db_session.query(Entry).count()
    res1 = svc.ingest_file(db=db_session, file_bytes=text_sample.encode("utf-8"), filename="count1.txt")
    db_session.commit()
    assert db_session.query(Entry).count() == count_before + 1

    # Attempt duplicate
    res2 = svc.ingest_file(db=db_session, file_bytes=text_sample.encode("utf-8"), filename="count2.txt")
    db_session.commit()
    assert res2.status == ManualIngestionStatus.DUPLICATE
    assert db_session.query(Entry).count() == count_before + 1


# ==============================================================================
# 5. Persistence Tests (Scenarios 32 to 35)
# ==============================================================================


def test_32_entry_associated_to_manual_source(db_session: Session):
    """Scenario 32: Entry is strictly linked to canonical manual source."""
    text = "Texto doctrinal asociado a la fuente manual del archivo con mas de cincuenta caracteres validos."
    svc = ManualDocumentIngestionService()
    res = svc.ingest_file(db=db_session, file_bytes=text.encode("utf-8"), filename="fuente_test.txt")
    db_session.commit()

    entry = db_session.get(Entry, res.entry.id)
    assert entry.source_id == MANUAL_UPLOAD_SOURCE_ID
    assert entry.source.name == MANUAL_UPLOAD_SOURCE_NAME


def test_33_raw_metadata_completeness(db_session: Session):
    """Scenario 33: raw_metadata preserves origin, filename, file size, user tracking."""
    test_user_id = uuid.uuid4()
    svc = ManualDocumentIngestionService()
    res = svc.ingest_file(
        db=db_session,
        file_bytes=VALID_PDF_BYTES,
        filename="sentencia_2026.pdf",
        content_type="application/pdf",
        user_id=test_user_id,
        user_email="abogado@despacho.com",
    )
    db_session.commit()

    raw = res.entry.raw_metadata
    assert raw["origin"] == "manual_upload"
    assert raw["filename"] == "sentencia_2026.pdf"
    assert raw["file_size_bytes"] == len(VALID_PDF_BYTES)
    assert raw["uploaded_by_user_id"] == str(test_user_id)
    assert raw["uploaded_by_email"] == "abogado@despacho.com"
    assert "content_text_hash" in raw


def test_34_synthetic_url_pattern(db_session: Session):
    """Scenario 34: Synthetic URL format upload://manual/<uuid>/<sanitized_filename>."""
    svc = ManualDocumentIngestionService()
    res = svc.ingest_file(db=db_session, file_bytes=VALID_PDF_BYTES, filename="resolucion cnmc #1.pdf")
    db_session.commit()

    assert res.entry.url.startswith("upload://manual/")
    parts = res.entry.url.split("/")
    # upload: , , manual, <uuid>, <filename>
    assert parts[2] == "manual"
    assert uuid.UUID(parts[3]) == res.entry.id
    assert parts[4] == "resolucion_cnmc_1.pdf"


def test_35_content_hash_populated(db_session: Session):
    """Scenario 35: Entry content_hash is 64-character SHA-256."""
    svc = ManualDocumentIngestionService()
    res = svc.ingest_file(db=db_session, file_bytes=VALID_PDF_BYTES, filename="hash_test.pdf")
    db_session.commit()

    assert res.entry.content_hash is not None
    assert len(res.entry.content_hash) == 64


# ==============================================================================
# 6. AI Analysis Scheduling Tests (Scenarios 36 and 37)
# ==============================================================================


def test_36_analyze_with_ai_false(db_session: Session):
    """Scenario 36: analyze_with_ai=False does NOT trigger or schedule analysis."""
    scheduler_called = False

    def mock_scheduler(entry_id: uuid.UUID):
        nonlocal scheduler_called
        scheduler_called = True

    svc = ManualDocumentIngestionService(background_scheduler=mock_scheduler)
    res = svc.ingest_file(
        db=db_session,
        file_bytes=VALID_PDF_BYTES,
        filename="no_ai.pdf",
        analyze_with_ai=False,
    )
    db_session.commit()

    assert res.analysis_requested is False
    assert res.analysis_scheduled is False
    assert scheduler_called is False
    assert len(res.entry.analyses) == 0


@pytest.mark.asyncio
async def test_37_analyze_with_ai_true_and_execution(db_session: Session):
    """Scenario 37: analyze_with_ai=True schedules analysis and executes via mock provider without real LLM calls."""
    scheduled_entries = []

    def mock_scheduler(entry_id: uuid.UUID):
        scheduled_entries.append(entry_id)

    mock_provider = MockAIProvider()
    svc = ManualDocumentIngestionService(
        background_scheduler=mock_scheduler,
        ai_provider=mock_provider,
    )

    sample_doc_text = (
        "Resolución sancionadora de la autoridad de competencia sobre cárteles y acuerdos restrictivos. "
        "En el marco del procedimiento sancionador incoado por presuntas prácticas colusorias contrarias "
        "al artículo 1 de la Ley de Defensa de la Competencia y al artículo 101 del TFUE, se constata "
        "la fijación coordinada de precios y el reparto de cuotas de mercado entre los principales "
        "operadores del sector. Se imponen multas coercitivas y medidas sancionadoras por importe superior "
        "a diez millones de euros, desestimando las alegaciones de las partes incoadas."
    )

    res = svc.ingest_file(
        db=db_session,
        file_bytes=sample_doc_text.encode("utf-8"),
        filename="resolucion_competencia.txt",
        analyze_with_ai=True,
    )
    db_session.commit()

    assert res.analysis_requested is True
    assert res.analysis_scheduled is True
    assert res.entry.id in scheduled_entries

    # Seed required active matrix and prompts for analysis pipeline in test DB
    matrix = TrackingMatrix(code="MATRIX-TEST-11C", name="Matriz 11C", status="active")
    db_session.add(matrix)
    db_session.flush()

    topic = TrackingTopic(matrix_id=matrix.id, code="antitrust_cartels", name="Cárteles")
    db_session.add(topic)

    triage_prompt = AnalysisPromptVersion(
        code="observatory_triage",
        version=1,
        stage="triage",
        name="Triage Prompt",
        system_prompt="System prompt triage",
        user_prompt_template="User prompt template",
        response_schema_version="v1",
        active=True,
    )
    deep_prompt = AnalysisPromptVersion(
        code="observatory_deep_analysis",
        version=1,
        stage="deep_analysis",
        name="Deep Prompt",
        system_prompt="System prompt deep",
        user_prompt_template="User prompt template",
        response_schema_version="v1",
        active=True,
    )
    db_session.add(triage_prompt)
    db_session.add(deep_prompt)
    db_session.commit()

    # Execute analysis pipeline with mock provider
    analysis = await svc.execute_entry_analysis(res.entry.id, db_session, provider=mock_provider)

    assert analysis is not None
    assert analysis.entry_id == res.entry.id
    assert analysis.status == "completed"
    assert analysis.relevance_status == "relevant"
    assert analysis.relevance_score == 85
    assert analysis.summary is not None
    assert "Executive summary for" in analysis.summary
    assert len(analysis.key_points) > 0


# ==============================================================================
# Document Presentation & Normalization Tests (Bloque 11C Presentation Fixes)
# ==============================================================================


def test_clean_document_title_percent_encoded_and_tech_prefix():
    """Test percent-decoding, extension removal, and tech-prefix removal."""
    raw_cat_url = (
        "https://www.catribunal.org.uk/sites/cat/files/2026-10/"
        "17497725%20Association%20of%20Consumer%20Support%20Organisations%20Ltd%20"
        "v%20Amazon.com%2C%20Inc%20%26%20Ors%20-%20Ruling%20%28CPO%20Decision%29%20%2013%20Oct%202026.pdf"
    )
    cleaned = clean_document_title(filename_or_url=raw_cat_url)
    assert "%20" not in cleaned
    assert "%26" not in cleaned
    assert "%28" not in cleaned
    assert "%29" not in cleaned
    assert "%2C" not in cleaned
    assert not cleaned.endswith(".pdf")
    assert not cleaned.startswith("17497725")
    assert cleaned == (
        "Association of Consumer Support Organisations Ltd v Amazon.com, "
        "Inc & Ors - Ruling (CPO Decision) 13 Oct 2026"
    )

    # 4-digit years should not be stripped
    assert clean_document_title(filename_or_url="2024_annual_competition_report.pdf") == "2024 annual competition report"

    # 16+ hex hash prefix should be stripped
    assert clean_document_title(filename_or_url="a1b2c3d4e5f67890123456789abcdef0_decision.pdf") == "decision"

    # Precedence: manual_title overrides metadata and url
    assert (
        clean_document_title(
            manual_title="Manual Title Override",
            metadata_title="Metadata Title",
            filename_or_url="17497725_file.pdf",
        )
        == "Manual Title Override"
    )

    # Precedence: metadata_title overrides url
    assert (
        clean_document_title(
            metadata_title="Title from PDF Metadata",
            filename_or_url="17497725_file.pdf",
        )
        == "Title from PDF Metadata"
    )

    # Fallback when empty
    assert clean_document_title(default_fallback="Fallback Document") == "Fallback Document"


def test_extract_pdf_metadata_title():
    """Test extraction of metadata title from PDF bytes."""
    # VALID_PDF_BYTES has no metadata title
    assert extract_pdf_metadata_title(VALID_PDF_BYTES) is None

    # Invalid PDF bytes
    assert extract_pdf_metadata_title(b"not a pdf") is None

    # Construct PDF with title metadata using pypdf
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.add_metadata({"/Title": "Resolucion de la CNMC Exp. S/0012/23"})
    buf = io.BytesIO()
    writer.write(buf)
    pdf_with_meta = buf.getvalue()

    extracted = extract_pdf_metadata_title(pdf_with_meta)
    assert extracted == "Resolucion de la CNMC Exp. S/0012/23"


def test_normalize_pdf_page_text_drop_caps_and_split_words():
    """Test healing of stylized drop caps, hyphenated words, and uppercase fragments."""
    raw_text = (
        "Ne\n"
        "utral citation [2026] CAT 77\n"
        "Bef\n"
        "ore:\n"
        "SI\n"
        "R PETER ROTH\n"
        "BETW\n"
        "EEN:\n"
        "TH\n"
        "E ASSOCIATION OF CONSUMER SUPPORT\n"
        "s\n"
        "ummary judgment and/or strike out\n"
        "di\n"
        "sproportionate cost\n"
        "dis-\n"
        "tribution agreement\n"
    )
    normalized = normalize_pdf_page_text(raw_text)

    assert "Neutral citation [2026] CAT 77" in normalized
    assert "Before:" in normalized
    assert "SIR PETER ROTH" in normalized
    assert "BETWEEN:" in normalized
    assert "THE ASSOCIATION OF CONSUMER SUPPORT" in normalized
    assert "summary judgment and/or strike out" in normalized
    assert "disproportionate cost" in normalized
    assert "distribution agreement" in normalized


def test_normalize_pdf_page_text_preserves_legal_structure():
    """Test that numbered clauses, lists, headings, and paragraphs are preserved."""
    raw_text = (
        "1. This is the first numbered paragraph of the ruling. It has a soft wrap\n"
        "here that continues the sentence.\n\n"
        "2. This is the second paragraph.\n"
        "(a) First sub-clause with condition.\n"
        "(b) Second sub-clause with condition.\n"
        "(i) Sub-sub-clause in roman numeral.\n\n"
        "ORDER:\n\n"
        "The Tribunal orders as follows."
    )
    normalized = normalize_pdf_page_text(raw_text)

    # Soft line break inside 1. should be unwrapped
    assert "first numbered paragraph of the ruling. It has a soft wrap here that continues the sentence." in normalized
    # Numbered structure preserved
    assert "1. This is" in normalized
    assert "2. This is" in normalized
    assert "(a) First sub-clause" in normalized
    assert "(b) Second sub-clause" in normalized
    assert "(i) Sub-sub-clause" in normalized
    assert "ORDER:" in normalized


def test_pdf_url_ingestion_with_percent_encoded_title(db_session: Session):
    """Test full URL ingestion flow with percent-encoded PDF URL."""
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=VALID_PDF_BYTES,
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))

    with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]):
        svc = ManualDocumentIngestionService()
        res = svc.ingest_url(
            db=db_session,
            url="https://example.com/files/17497725%20Ruling%20%28Amazon%20Case%29.pdf",
            client=mock_client,
        )

    assert res.status == ManualIngestionStatus.CREATED
    assert res.entry is not None
    assert "%20" not in res.entry.title
    assert "%28" not in res.entry.title
    assert not res.entry.title.endswith(".pdf")
    assert not res.entry.title.startswith("17497725")
    assert res.entry.title == "Ruling (Amazon Case)"
