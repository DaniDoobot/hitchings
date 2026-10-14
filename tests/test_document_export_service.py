"""Unit tests for DocumentExportService (Bloque 12 — Fase 1 y Fase 4A).

Covers:
Fase 1 (Individual document export):
1. Generación correcta de DOCX con análisis.
2. Generación correcta de DOCX sin análisis.
3. DOCX válido que pueda abrirse con python-docx.
4. Presencia de título y secciones principales.
5. Presencia de datos de la ficha técnica.
6. Presencia del summary.
7. Presencia de key_points.
8. Presencia de evidencias (citas verbatim).
9. Presencia del texto documental original.
10. Fallback de Entry.content a Entry.excerpt.
11. No generación de secciones de IA cuando no existe análisis.
12. Sanitización de filename con y sin análisis.
13. Caracteres especiales y tildes en títulos.
14. Documento con campos opcionales ausentes.
15. Documento largo sin errores de generación.
16. Compatibilidad directa con el modelo ORM Entry.

Fase 4A (Thematic multidocument dossier export):
17. Dossier de 1 documento.
18. Dossier de 2 documentos.
19. Dossier de varios documentos (5 documentos).
20. Dossier con todos los documentos con análisis.
21. Dossier con documentos sin análisis.
22. Dossier mixto con y sin análisis.
23. Sección individual por documento.
24. Salto de página entre documentos.
25. Título personalizado del dossier.
26. Título por defecto del dossier.
27. Número correcto de documentos en portada e índice.
28. Metadatos documentales de cada expediente.
29. Relevancia y puntuación cuando existe análisis.
30. Resumen analítico cuando existe análisis.
31. Puntos clave cuando existen.
32. Topics cuando existen.
33. Evidencias y citas literales de respaldo cuando existen.
34. Documento sin análisis claramente identificado.
35. Contenido original incluido con reflow limpio.
36. Colección vacía -> excepción DossierEmptyError (ValueError).
37. Más de 30 documentos -> excepción DossierLimitExceededError (ValueError).
38. Generación 100% en memoria sin archivos temporales en disco.
39. Validación de estructura y márgenes del DOCX.
40. Compatibilidad regresiva de exportación individual.
41. Test de integración real del generador con inspección estructural.
42. Sanitización de nombres de archivo de dossier.
"""

import io
import os
import tempfile
import uuid
from datetime import datetime, timezone

import docx
from docx.oxml.ns import qn
import pytest

from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity
from app.models.analysis import EntryAnalysis
from app.schemas.document import (
    DocumentAnalysisSummary,
    DocumentDetail,
    DocumentSourceRef,
)
from app.schemas.observatory import (
    ObservatoryEvidence,
    ObservatoryEvidenceQuote,
    ObservatoryKeyPointEvidence,
    ObservatoryTopicItem,
)
from app.services.document_export_service import (
    DEFAULT_DOSSIER_TITLE,
    MAX_DOSSIER_DOCUMENTS,
    DocumentExportService,
    DossierEmptyError,
    DossierError,
    DossierLimitExceededError,
    document_export_service,
    sanitize_dossier_filename,
    sanitize_export_filename,
)


def _count_page_breaks(doc: docx.Document) -> int:
    """Count page break elements in docx paragraphs."""
    count = 0
    for p in doc.paragraphs:
        for r in p.runs:
            for br in r._r.xpath("./w:br"):
                if br.get(qn("w:type")) == "page":
                    count += 1
    return count


@pytest.fixture
def sample_analysed_document() -> DocumentDetail:
    """Fixture providing a rich DocumentDetail with completed AI analysis and evidence."""
    doc_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    source = DocumentSourceRef(
        id=uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        name="Tribunal Supremo — Sala Tercera",
        type="judicial",
        category="official",
        tracked_entity_name="Sala de lo Contencioso-Administrativo",
    )
    analysis = DocumentAnalysisSummary(
        id=uuid.UUID("99999999-8888-7777-6666-555555555555"),
        status="completed",
        relevance_status="relevant",
        relevance_score=95,
        confidence=0.94,
        summary="La Sala desestima el recurso y confirma la existencia de cártel colusorio sancionado.",
        key_points=[
            "Infracción muy grave del artículo 1 de la Ley de Defensa de la Competencia.",
            "Desestimación de la prescripción extintiva alegada por la recurrente.",
        ],
        canonical_topics=[
            ObservatoryTopicItem(code="antitrust_cartels", name="Cárteles y Colusión"),
            ObservatoryTopicItem(code="procedural_law", name="Derecho Procesal Competencial"),
        ],
        analyzed_at=datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc),
    )
    evidence = ObservatoryEvidence(
        source="deep",
        summary_quotes=[
            ObservatoryEvidenceQuote(
                source_field="content",
                quote="Los intercambios de información sensible sobre precios futuros constituyen una infracción por objeto.",
            )
        ],
        key_points=[
            ObservatoryKeyPointEvidence(
                point="Infracción muy grave del artículo 1 de la Ley de Defensa de la Competencia.",
                quotes=[
                    ObservatoryEvidenceQuote(
                        source_field="content",
                        quote="Queda acreditada la concurrencia de voluntades para falsear la competencia en el mercado nacional.",
                    )
                ],
            )
        ],
    )
    return DocumentDetail(
        id=doc_id,
        title="Sentencia TS 452/2026 sobre Cártel de Fabricantes",
        url="https://supremo.poderjudicial.es/sentencias/452-2026",
        canonical_url="https://supremo.poderjudicial.es/sentencias/452-2026",
        author="Magistrado Ponente D. Fernando Gómez",
        published_at=datetime(2026, 5, 14, 0, 0, 0, tzinfo=timezone.utc),
        captured_at=datetime(2026, 5, 14, 12, 30, 0, tzinfo=timezone.utc),
        content_type="judicial_decision",
        language="es",
        source=source,
        content=(
            "ANTECEDENTES DE HECHO:\n\n"
            "PRIMERO.- La mercantil recurrente interpuso recurso contencioso-administrativo.\n\n"
            "FUNDAMENTOS DE DERECHO:\n\n"
            "PRIMERO.- Sobre la existencia de cártel y la afectación al comercio.\n"
            "Los intercambios de información sensible sobre precios futuros constituyen una infracción por objeto.\n"
            "Queda acreditada la concurrencia de voluntades para falsear la competencia en el mercado nacional.\n\n"
            "FALLO:\n\n"
            "Desestimamos íntegramente el recurso con imposición de costas."
        ),
        excerpt="Desestimación de recurso sobre cártel colusorio...",
        has_analysis=True,
        analysis=analysis,
        evidence=evidence,
    )


@pytest.fixture
def sample_second_analysed_document() -> DocumentDetail:
    """Fixture providing a second distinct analysed document from CNMC."""
    doc_id = uuid.UUID("33333333-4444-5555-6666-777777777777")
    source = DocumentSourceRef(
        id=uuid.UUID("cccccccc-dddd-eeee-ffff-000000000000"),
        name="Comisión Nacional de los Mercados y la Competencia",
        type="website",
        category="official",
        tracked_entity_name="Dirección de Competencia",
    )
    analysis = DocumentAnalysisSummary(
        id=uuid.UUID("88888888-7777-6666-5555-444444444444"),
        status="completed",
        relevance_status="relevant",
        relevance_score=88,
        confidence=0.91,
        summary="La CNMC sanciona prácticas restrictivas consistentes en fijación vertical de precios de distribución.",
        key_points=[
            "Fijación vertical de precios de reventa prohibida por el artículo 1.1.a LDC.",
            "Obligación de cesación inmediata bajo apercibimiento de multa coercitiva.",
        ],
        canonical_topics=[
            ObservatoryTopicItem(code="vertical_restraints", name="Restricciones Verticales"),
        ],
        analyzed_at=datetime(2026, 5, 20, 9, 15, 0, tzinfo=timezone.utc),
    )
    evidence = ObservatoryEvidence(
        source="deep",
        summary_quotes=[
            ObservatoryEvidenceQuote(
                source_field="content",
                quote="Las cláusulas contractuales limitaban la libertad de los distribuidores para fijar descuentos comerciales.",
            )
        ],
        key_points=[
            ObservatoryKeyPointEvidence(
                point="Fijación vertical de precios de reventa prohibida por el artículo 1.1.a LDC.",
                quotes=[
                    ObservatoryEvidenceQuote(
                        source_field="content",
                        quote="Se constata la imposición sistemática de precios mínimos de venta al público en toda la red.",
                    )
                ],
            )
        ],
    )
    return DocumentDetail(
        id=doc_id,
        title="Resolución CNMC S/0045/25 sobre Sector Distribución",
        url="https://cnmc.es/expedientes/s-0045-25",
        canonical_url="https://cnmc.es/expedientes/s-0045-25",
        author="Consejo de la CNMC",
        published_at=datetime(2026, 5, 20, 0, 0, 0, tzinfo=timezone.utc),
        captured_at=datetime(2026, 5, 20, 11, 0, 0, tzinfo=timezone.utc),
        content_type="judicial_decision",
        language="es",
        source=source,
        content=(
            "RESOLUCIÓN DEL CONSEJO DE LA CNMC:\n\n"
            "PRIMERO.- Se declara la existencia de infracción del artículo 1 LDC.\n"
            "Las cláusulas contractuales limitaban la libertad de los distribuidores para fijar descuentos comerciales.\n"
            "Se constata la imposición sistemática de precios mínimos de venta al público en toda la red.\n\n"
            "SEGUNDO.- Se imponen sanciones por importe acumulado de 3.500.000 euros."
        ),
        excerpt="Resolución sancionadora por fijación vertical de precios...",
        has_analysis=True,
        analysis=analysis,
        evidence=evidence,
    )


@pytest.fixture
def sample_unanalysed_document() -> DocumentDetail:
    """Fixture providing a DocumentDetail from archive with no AI analysis."""
    doc_id = uuid.UUID("22222222-3333-4444-5555-666666666666")
    source = DocumentSourceRef(
        id=uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff"),
        name="Boletín Oficial del Estado",
        type="website",
        category="official",
    )
    return DocumentDetail(
        id=doc_id,
        title="Real Decreto-ley 7/2026 sobre Transparencia de Mercados",
        url="https://boe.es/diario_boe/txt.php?id=BOE-A-2026-789",
        author="Jefatura del Estado",
        published_at=datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        captured_at=datetime(2026, 6, 1, 8, 0, 0, tzinfo=timezone.utc),
        content_type="article",
        language="es",
        source=source,
        content="I. Disposiciones Generales.\n\nArtículo único. Medidas de transparencia y supervisión.",
        excerpt="Disposición general sobre mercados...",
        has_analysis=False,
        analysis=None,
        evidence=None,
    )


@pytest.fixture
def sample_third_document() -> DocumentDetail:
    """Fixture providing a manual upload document without analysis."""
    doc_id = uuid.UUID("44444444-5555-6666-7777-888888888888")
    source = DocumentSourceRef(
        id=uuid.UUID("dddddddd-eeee-ffff-0000-111111111111"),
        name="Aportación Manual",
        type="manual_upload",
        category="internal",
    )
    return DocumentDetail(
        id=doc_id,
        title="Manual Interno de Cumplimiento Antitrust 2026",
        url="upload://manual/44444444/compliance.pdf",
        author="Área de Compliance",
        published_at=datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc),
        captured_at=datetime(2026, 6, 15, 9, 30, 0, tzinfo=timezone.utc),
        content_type="manual_upload",
        language="es",
        source=source,
        content="Directrices internas de prevención y detección temprana de conductas colusorias y acuerdos prohibidos.",
        excerpt="Manual corporativo de compliance...",
        raw_metadata={"filename": "compliance.pdf", "file_size_bytes": 1048576},
        has_analysis=False,
        analysis=None,
        evidence=None,
    )


# ===========================================================================
# Bloque 12 — Fase 1: Tests Existentes de Exportación Individual
# ===========================================================================

def test_1_generate_docx_with_analysis(sample_analysed_document: DocumentDetail):
    """1. Generación correcta de DOCX con análisis."""
    buf = document_export_service.generate_docx(sample_analysed_document)
    assert isinstance(buf, io.BytesIO)
    bytes_data = buf.getvalue()
    assert len(bytes_data) > 3000
    assert bytes_data.startswith(b"PK")  # ZIP header of .docx


def test_2_generate_docx_without_analysis(sample_unanalysed_document: DocumentDetail):
    """2. Generación correcta de DOCX sin análisis."""
    buf = document_export_service.generate_docx(sample_unanalysed_document)
    assert isinstance(buf, io.BytesIO)
    bytes_data = buf.getvalue()
    assert len(bytes_data) > 3000
    assert bytes_data.startswith(b"PK")


def test_3_docx_valid_readable_by_python_docx(sample_analysed_document: DocumentDetail):
    """3. DOCX válido que pueda abrirse e inspeccionarse con python-docx."""
    buf = document_export_service.generate_docx(sample_analysed_document)
    doc = docx.Document(buf)
    assert len(doc.paragraphs) > 0
    assert len(doc.tables) > 0
    assert len(doc.sections) == 1


def test_4_presence_of_title_and_main_sections(sample_analysed_document: DocumentDetail):
    """4. Presencia de título y secciones principales en documento analizado."""
    buf = document_export_service.generate_docx(sample_analysed_document)
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "HITCHINGS — OBSERVATORIO LEGAL Y FONDO DOCUMENTAL" in all_text
    assert "Sentencia TS 452/2026 sobre Cártel de Fabricantes" in all_text
    assert "1. FICHA TÉCNICA DEL EXPEDIENTE" in all_text
    assert "2. DICTAMEN JURÍDICO Y CALIFICACIÓN DE RELEVANCIA" in all_text
    assert "3. PUNTOS CLAVE Y CRITERIOS DOCTRINALES" in all_text
    assert "4. FUNDAMENTACIÓN Y CITAS LITERALES DE RESPALDO" in all_text
    assert "5. ANEXO: TEXTO DOCUMENTAL ORIGINAL" in all_text


def test_5_presence_of_ficha_tecnica_data(sample_analysed_document: DocumentDetail):
    """5. Presencia de datos de la ficha técnica."""
    buf = document_export_service.generate_docx(sample_analysed_document)
    doc = docx.Document(buf)

    table_cells = [cell.text for t in doc.tables for row in t.rows for cell in row.cells]
    table_content = " ".join(table_cells)

    assert "Tribunal Supremo — Sala Tercera" in table_content
    assert "Sala de lo Contencioso-Administrativo" in table_content
    assert "Magistrado Ponente D. Fernando Gómez" in table_content
    assert "14/05/2026" in table_content
    assert "Resolución Judicial" in table_content
    assert "https://supremo.poderjudicial.es/sentencias/452-2026" in table_content


def test_6_presence_of_summary(sample_analysed_document: DocumentDetail):
    """6. Presencia del summary analítico."""
    buf = document_export_service.generate_docx(sample_analysed_document)
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "La Sala desestima el recurso y confirma la existencia de cártel colusorio sancionado." in all_text
    assert "Calificación de impacto: RELEVANTE" in all_text
    assert "Puntuación de relevancia: 95/100" in all_text


def test_7_presence_of_key_points(sample_analysed_document: DocumentDetail):
    """7. Presencia de key_points."""
    buf = document_export_service.generate_docx(sample_analysed_document)
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "Infracción muy grave del artículo 1 de la Ley de Defensa de la Competencia." in all_text
    assert "Desestimación de la prescripción extintiva alegada por la recurrente." in all_text


def test_8_presence_of_evidences(sample_analysed_document: DocumentDetail):
    """8. Presencia de evidencias y citas literales (Grounding)."""
    buf = document_export_service.generate_docx(sample_analysed_document)
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "Los intercambios de información sensible sobre precios futuros constituyen una infracción por objeto." in all_text
    assert "Queda acreditada la concurrencia de voluntades para falsear la competencia en el mercado nacional." in all_text
    assert "[Cita literal" in all_text


def test_9_presence_of_original_document_content(sample_analysed_document: DocumentDetail):
    """9. Presencia del texto documental original."""
    buf = document_export_service.generate_docx(sample_analysed_document)
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "ANTECEDENTES DE HECHO:" in all_text
    assert "Desestimamos íntegramente el recurso con imposición de costas." in all_text


def test_10_fallback_content_to_excerpt():
    """10. Fallback de Entry.content a Entry.excerpt cuando content es None."""
    doc = DocumentDetail(
        id=uuid.uuid4(),
        title="Documento con solo extracto",
        url="https://example.org/doc",
        captured_at=datetime.now(timezone.utc),
        source=DocumentSourceRef(id=uuid.uuid4(), name="Boletín"),
        content=None,
        excerpt="Extracto resumido de prueba por falta de cuerpo íntegro.",
        has_analysis=False,
    )
    buf = document_export_service.generate_docx(doc)
    doc_opened = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc_opened.paragraphs)

    assert "Extracto resumido de prueba por falta de cuerpo íntegro." in all_text


def test_11_no_ai_sections_when_no_analysis(sample_unanalysed_document: DocumentDetail):
    """11. No generación de secciones de IA cuando no existe análisis."""
    buf = document_export_service.generate_docx(sample_unanalysed_document)
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    # Verifica encabezado y aviso de archivo
    assert "HITCHINGS — EXPEDIENTE DOCUMENTAL DE ARCHIVO" in all_text
    assert "Expediente en archivo documental original sin análisis automatizado vigente en la matriz de seguimiento." in all_text
    assert "1. FICHA TÉCNICA DEL EXPEDIENTE" in all_text
    assert "2. ANEXO: TEXTO DOCUMENTAL ORIGINAL" in all_text

    # Verifica ausencia de secciones de IA
    assert "DICTAMEN JURÍDICO" not in all_text
    assert "PUNTOS CLAVE Y CRITERIOS DOCTRINALES" not in all_text
    assert "FUNDAMENTACIÓN Y CITAS LITERALES" not in all_text
    assert "Calificación de impacto" not in all_text


def test_12_sanitization_of_filename():
    """12. Sanitización de filename con y sin análisis."""
    doc_id = uuid.UUID("12345678-aaaa-bbbb-cccc-dddddddddddd")

    # Con análisis
    name_analysed = sanitize_export_filename(
        "Sentencia TS sobre Competencia",
        document_id=doc_id,
        has_analysis=True,
    )
    assert name_analysed == "dictamen-sentencia-ts-sobre-competencia.docx"

    # Sin análisis
    name_unanalysed = sanitize_export_filename(
        "Sentencia TS sobre Competencia",
        document_id=doc_id,
        has_analysis=False,
    )
    assert name_unanalysed == "expediente-sentencia-ts-sobre-competencia.docx"

    # Fallback con título vacío
    fallback = sanitize_export_filename(None, document_id=doc_id, has_analysis=True)
    assert fallback == "dictamen-hitchings-12345678.docx"

    fallback_no_ana = sanitize_export_filename("", document_id=doc_id, has_analysis=False)
    assert fallback_no_ana == "expediente-hitchings-12345678.docx"


def test_13_special_characters_and_accents_in_filename():
    """13. Caracteres especiales y tildes en títulos."""
    title = "¿Resolución de la CNMC (Sala de Competencia) nº 14/2026: 'Cártel de Fabricantes'?"
    filename = sanitize_export_filename(title, has_analysis=True)

    # Acentos eliminados, signos de interrogación y barras convertidos a guiones
    assert filename.startswith("dictamen-")
    assert filename.endswith(".docx")
    assert "resolucion-de-la-cnmc-sala-de-competencia" in filename
    assert "¿" not in filename
    assert "?" not in filename
    assert "/" not in filename
    assert "'" not in filename
    assert "ñ" not in filename
    assert len(filename) <= len("dictamen-") + 50 + len(".docx")


def test_14_document_with_missing_optional_fields():
    """14. Documento con campos opcionales ausentes no genera errores ni filas vacías."""
    doc = DocumentDetail(
        id=uuid.uuid4(),
        title=None,
        url="upload://manual/123/file.pdf",
        captured_at=datetime.now(timezone.utc),
        source=DocumentSourceRef(id=uuid.uuid4(), name="Aportación Manual"),
        author=None,
        published_at=None,
        content_type=None,
        content=None,
        excerpt=None,
        has_analysis=False,
    )
    buf = document_export_service.generate_docx(doc)
    doc_opened = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc_opened.paragraphs)

    assert "Documento sin título" in all_text
    assert "El texto documental íntegro se encuentra disponible directamente en el enlace oficial de origen." in all_text


def test_15_large_document_generation():
    """15. Documento largo sin errores de memoria ni generación."""
    long_content = "\n\n".join(
        f"Párrafo extenso {i}: Razonamientos jurídicos dogmáticos sobre el fondo del litigio en materia de competencia. " * 50
        for i in range(25)
    )
    doc = DocumentDetail(
        id=uuid.uuid4(),
        title="Dictamen sobre Expediente Judicial Complejo de Gran Longitud",
        url="https://tribunal.es/fallo-extenso",
        captured_at=datetime.now(timezone.utc),
        source=DocumentSourceRef(id=uuid.uuid4(), name="Tribunal de Apelación"),
        content=long_content,
        has_analysis=False,
    )
    buf = document_export_service.generate_docx(doc)
    bytes_data = buf.getvalue()
    assert len(bytes_data) > 30000

    doc_opened = docx.Document(buf)
    assert len(doc_opened.paragraphs) >= 10


def test_16_generate_docx_from_entry_model_directly():
    """16. Generación directa recibiendo una entidad ORM Entry."""
    entry_id = uuid.uuid4()
    source = Source(
        id=uuid.uuid4(),
        name="Comisión Nacional de los Mercados",
        type=SourceType.WEBSITE,
    )
    entry = Entry(
        id=entry_id,
        source_id=source.id,
        url="https://cnmc.es/expediente-123",
        title="Resolución CNMC sobre Telecomunicaciones",
        content="Resolución firme acordando sanción por abuso de posición dominante.",
        captured_at=datetime.now(timezone.utc),
    )
    entry.source = source

    buf = document_export_service.generate_docx(entry)
    doc_opened = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc_opened.paragraphs)
    table_cells = [c.text for t in doc_opened.tables for r in t.rows for c in r.cells]
    table_text = " ".join(table_cells)

    assert "Resolución CNMC sobre Telecomunicaciones" in all_text
    assert "Comisión Nacional de los Mercados" in table_text
    assert "Expediente en archivo documental original sin análisis automatizado" in all_text


# ===========================================================================
# Bloque 12 — Fase 4A: Tests Específicos para Dossier Temático / Multidocumento
# ===========================================================================

def test_17_dossier_1_single_document(sample_analysed_document: DocumentDetail):
    """17. Dossier de 1 documento genera DOCX válido con portada, índice y capítulo."""
    buf = document_export_service.generate_dossier_docx([sample_analysed_document])
    assert isinstance(buf, io.BytesIO)
    bytes_data = buf.getvalue()
    assert bytes_data.startswith(b"PK")

    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "DOSSIER DOCUMENTAL" in all_text
    assert "Dossier documental HITCHINGS" in all_text
    assert "ÍNDICE DE EXPEDIENTES Y DOCUMENTOS INCLUIDOS" in all_text
    assert "DOCUMENTO 1 DE 1" in all_text
    assert "Sentencia TS 452/2026 sobre Cártel de Fabricantes" in all_text


def test_18_dossier_2_two_documents(
    sample_analysed_document: DocumentDetail,
    sample_unanalysed_document: DocumentDetail,
):
    """18. Dossier de 2 documentos contiene ambos títulos, índice de 2 filas y capítulos numerados."""
    buf = document_export_service.generate_dossier_docx([
        sample_analysed_document,
        sample_unanalysed_document,
    ])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "DOCUMENTO 1 DE 2" in all_text
    assert "DOCUMENTO 2 DE 2" in all_text
    assert "Sentencia TS 452/2026 sobre Cártel de Fabricantes" in all_text
    assert "Real Decreto-ley 7/2026 sobre Transparencia de Mercados" in all_text

    # Verifica tabla del índice (la segunda tabla del documento, o la tabla con 5 columnas)
    index_table = next(t for t in doc.tables if len(t.columns) == 5)
    assert len(index_table.rows) == 3  # 1 cabecera + 2 expedientes
    assert "Sentencia TS 452/2026" in index_table.rows[1].cells[1].text
    assert "Real Decreto-ley 7/2026" in index_table.rows[2].cells[1].text


def test_19_dossier_3_multiple_documents(
    sample_analysed_document: DocumentDetail,
    sample_second_analysed_document: DocumentDetail,
    sample_unanalysed_document: DocumentDetail,
    sample_third_document: DocumentDetail,
):
    """19. Dossier de varios documentos (4 documentos) refleja orden y total exacto."""
    docs = [
        sample_analysed_document,
        sample_unanalysed_document,
        sample_second_analysed_document,
        sample_third_document,
    ]
    buf = document_export_service.generate_dossier_docx(docs)
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    for i in range(1, 5):
        assert f"DOCUMENTO {i} DE 4" in all_text

    index_table = next(t for t in doc.tables if len(t.columns) == 5)
    assert len(index_table.rows) == 5


def test_20_dossier_4_all_documents_analysed(
    sample_analysed_document: DocumentDetail,
    sample_second_analysed_document: DocumentDetail,
):
    """20. Dossier con todos los documentos analizados contiene dictámenes y ninguna advertencia de archivo."""
    buf = document_export_service.generate_dossier_docx([
        sample_analysed_document,
        sample_second_analysed_document,
    ])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "Expediente en archivo documental original sin análisis" not in all_text
    assert "Documento incorporado sin análisis IA" not in all_text

    table_cells = [c.text for t in doc.tables for r in t.rows for c in r.cells]
    table_text = " ".join(table_cells)

    assert "RELEVANTE (95/100)" in table_text
    assert "RELEVANTE (88/100)" in table_text


def test_21_dossier_5_all_documents_unanalysed(
    sample_unanalysed_document: DocumentDetail,
    sample_third_document: DocumentDetail,
):
    """21. Dossier con documentos sin análisis no inventa conclusiones y los etiqueta claramente."""
    buf = document_export_service.generate_dossier_docx([
        sample_unanalysed_document,
        sample_third_document,
    ])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "DICTAMEN JURÍDICO Y CALIFICACIÓN DE RELEVANCIA" not in all_text
    assert "PUNTOS CLAVE Y CRITERIOS DOCTRINALES" not in all_text
    assert "FUNDAMENTACIÓN Y CITAS LITERALES" not in all_text
    assert "Documento incorporado sin análisis IA" in all_text

    index_table = next(t for t in doc.tables if len(t.columns) == 5)
    assert "Sin análisis IA" in index_table.rows[1].cells[4].text
    assert "Sin análisis IA" in index_table.rows[2].cells[4].text


def test_22_dossier_6_mixed_documents(
    sample_analysed_document: DocumentDetail,
    sample_unanalysed_document: DocumentDetail,
):
    """22. Dossier mixto con y sin análisis maneja ambos tipos de forma limpia y diferenciada."""
    buf = document_export_service.generate_dossier_docx([
        sample_analysed_document,
        sample_unanalysed_document,
    ])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    # El documento analizado tiene dictamen
    assert "DICTAMEN JURÍDICO Y CALIFICACIÓN DE RELEVANCIA" in all_text
    assert "Calificación de impacto: RELEVANTE" in all_text
    assert "95/100" in all_text

    # El documento sin análisis tiene aviso explícito
    assert "Documento incorporado sin análisis IA" in all_text


def test_23_dossier_7_individual_sections_per_document(
    sample_analysed_document: DocumentDetail,
    sample_unanalysed_document: DocumentDetail,
):
    """23. Cada documento cuenta con su propia ficha técnica y capítulo segregado."""
    buf = document_export_service.generate_dossier_docx([
        sample_analysed_document,
        sample_unanalysed_document,
    ])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "DOCUMENTO 1 DE 2" in all_text
    assert "DOCUMENTO 2 DE 2" in all_text

    table_cells = [c.text for t in doc.tables for r in t.rows for c in r.cells]
    table_text = " ".join(table_cells)

    assert "Tribunal Supremo — Sala Tercera" in table_text
    assert "Boletín Oficial del Estado" in table_text


def test_24_dossier_8_page_breaks_between_documents(
    sample_analysed_document: DocumentDetail,
    sample_second_analysed_document: DocumentDetail,
    sample_unanalysed_document: DocumentDetail,
):
    """24. Saltos de página reales separan portada, índice y cada documento."""
    # 1 documento -> Portada + Índice = 2 saltos
    doc1 = docx.Document(document_export_service.generate_dossier_docx([sample_analysed_document]))
    assert _count_page_breaks(doc1) == 2

    # 2 documentos -> Portada + Índice + Doc 1 = 3 saltos
    doc2 = docx.Document(document_export_service.generate_dossier_docx([
        sample_analysed_document,
        sample_unanalysed_document,
    ]))
    assert _count_page_breaks(doc2) == 3

    # 3 documentos -> Portada + Índice + Doc 1 + Doc 2 = 4 saltos
    doc3 = docx.Document(document_export_service.generate_dossier_docx([
        sample_analysed_document,
        sample_second_analysed_document,
        sample_unanalysed_document,
    ]))
    assert _count_page_breaks(doc3) == 4


def test_25_dossier_9_custom_title(sample_analysed_document: DocumentDetail):
    """25. Título personalizado del dossier se plasma en portada y metadatos."""
    custom = "Dossier Estratégico de Cárteles y Colusión Empresarial 2026"
    buf = document_export_service.generate_dossier_docx([sample_analysed_document], title=custom)
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert custom in all_text


def test_26_dossier_10_default_title(sample_analysed_document: DocumentDetail):
    """26. Título por defecto se utiliza cuando title es None o espacios en blanco."""
    buf_none = document_export_service.generate_dossier_docx([sample_analysed_document], title=None)
    doc_none = docx.Document(buf_none)
    assert DEFAULT_DOSSIER_TITLE in "\n".join(p.text for p in doc_none.paragraphs)

    buf_empty = document_export_service.generate_dossier_docx([sample_analysed_document], title="   ")
    doc_empty = docx.Document(buf_empty)
    assert DEFAULT_DOSSIER_TITLE in "\n".join(p.text for p in doc_empty.paragraphs)


def test_27_dossier_11_correct_documents_count(
    sample_analysed_document: DocumentDetail,
    sample_second_analysed_document: DocumentDetail,
    sample_unanalysed_document: DocumentDetail,
):
    """27. Número correcto de documentos reflejado en portada e índice."""
    docs = [sample_analysed_document, sample_second_analysed_document, sample_unanalysed_document]
    buf = document_export_service.generate_dossier_docx(docs)
    doc = docx.Document(buf)

    # Portada metadatos
    cover_table = doc.tables[0]
    cover_content = " ".join(c.text for r in cover_table.rows for c in r.cells)
    assert "Total de documentos incluidos 3" in cover_content

    # Índice tabla
    index_table = next(t for t in doc.tables if len(t.columns) == 5)
    assert len(index_table.rows) == 4  # 1 cabecera + 3 docs


def test_28_dossier_12_documentary_metadata(sample_analysed_document: DocumentDetail):
    """28. Metadatos documentales de cada expediente integrados en la Ficha Técnica."""
    buf = document_export_service.generate_dossier_docx([sample_analysed_document])
    doc = docx.Document(buf)

    table_cells = [c.text for t in doc.tables for r in t.rows for c in r.cells]
    table_text = " ".join(table_cells)

    assert "Tribunal Supremo — Sala Tercera" in table_text
    assert "Sala de lo Contencioso-Administrativo" in table_text
    assert "Magistrado Ponente D. Fernando Gómez" in table_text
    assert "14/05/2026" in table_text
    assert "Resolución Judicial" in table_text
    assert "https://supremo.poderjudicial.es/sentencias/452-2026" in table_text


def test_29_dossier_13_relevance_when_present(sample_analysed_document: DocumentDetail):
    """29. Relevancia y puntuación cuando existe análisis."""
    buf = document_export_service.generate_dossier_docx([sample_analysed_document])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "Calificación de impacto: RELEVANTE" in all_text
    assert "Puntuación de relevancia: 95/100" in all_text


def test_30_dossier_14_summary_when_present(sample_analysed_document: DocumentDetail):
    """30. Resumen analítico cuando existe análisis."""
    buf = document_export_service.generate_dossier_docx([sample_analysed_document])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "La Sala desestima el recurso y confirma la existencia de cártel colusorio sancionado." in all_text


def test_31_dossier_15_key_points_when_present(sample_analysed_document: DocumentDetail):
    """31. Puntos clave cuando existen en el análisis."""
    buf = document_export_service.generate_dossier_docx([sample_analysed_document])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "Infracción muy grave del artículo 1 de la Ley de Defensa de la Competencia." in all_text
    assert "Desestimación de la prescripción extintiva alegada por la recurrente." in all_text


def test_32_dossier_16_topics_when_present(sample_analysed_document: DocumentDetail):
    """32. Topics cuando existen en el análisis."""
    buf = document_export_service.generate_dossier_docx([sample_analysed_document])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "Cárteles y Colusión" in all_text
    assert "Derecho Procesal Competencial" in all_text


def test_33_dossier_17_evidence_quotes_when_present(sample_analysed_document: DocumentDetail):
    """33. Evidencias y citas literales de respaldo (Grounding) cuando existen."""
    buf = document_export_service.generate_dossier_docx([sample_analysed_document])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "Los intercambios de información sensible sobre precios futuros constituyen una infracción por objeto." in all_text
    assert "Queda acreditada la concurrencia de voluntades para falsear la competencia en el mercado nacional." in all_text
    assert "[Cita literal" in all_text


def test_34_dossier_18_unanalysed_document_clearly_identified(sample_unanalysed_document: DocumentDetail):
    """34. Documento sin análisis claramente identificado como registro de archivo original."""
    buf = document_export_service.generate_dossier_docx([sample_unanalysed_document])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "Documento incorporado sin análisis IA" in all_text
    assert "Expediente en archivo documental original sin análisis automatizado vigente en la matriz de seguimiento." in all_text
    assert "DICTAMEN JURÍDICO" not in all_text


def test_35_dossier_19_original_content_included(
    sample_analysed_document: DocumentDetail,
    sample_unanalysed_document: DocumentDetail,
):
    """35. Contenido original incluido con reflow limpio sin saltos artificiales."""
    buf = document_export_service.generate_dossier_docx([
        sample_analysed_document,
        sample_unanalysed_document,
    ])
    doc = docx.Document(buf)
    all_text = "\n".join(p.text for p in doc.paragraphs)

    assert "ANTECEDENTES DE HECHO:" in all_text
    assert "Desestimamos íntegramente el recurso con imposición de costas." in all_text
    assert "Artículo único. Medidas de transparencia y supervisión." in all_text


def test_36_dossier_20_empty_collection_raises_error():
    """36. Colección vacía eleva DossierEmptyError (subclase de ValueError)."""
    with pytest.raises(DossierEmptyError) as exc_info:
        document_export_service.generate_dossier_docx([])

    assert "al menos 1 documento" in str(exc_info.value)
    assert issubclass(DossierEmptyError, ValueError)
    assert issubclass(DossierEmptyError, DossierError)


def test_37_dossier_21_more_than_30_documents_raises_error(sample_unanalysed_document: DocumentDetail):
    """37. Más de 30 documentos eleva DossierLimitExceededError (subclase de ValueError)."""
    oversized = [sample_unanalysed_document] * 31
    with pytest.raises(DossierLimitExceededError) as exc_info:
        document_export_service.generate_dossier_docx(oversized)

    assert "límite máximo de 30 documentos" in str(exc_info.value)
    assert issubclass(DossierLimitExceededError, ValueError)
    assert issubclass(DossierLimitExceededError, DossierError)

    # Exactamente 30 documentos debe permitirse
    exact_30 = [sample_unanalysed_document] * MAX_DOSSIER_DOCUMENTS
    buf = document_export_service.generate_dossier_docx(exact_30)
    assert isinstance(buf, io.BytesIO)


def test_38_dossier_22_no_temporary_files_created(sample_analysed_document: DocumentDetail):
    """38. Generación 100% en memoria sin creación de archivos temporales en disco."""
    cwd_files_before = set(os.listdir("."))
    temp_files_before = set(os.listdir(tempfile.gettempdir()))

    buf = document_export_service.generate_dossier_docx([sample_analysed_document])

    cwd_files_after = set(os.listdir("."))
    temp_files_after = set(os.listdir(tempfile.gettempdir()))

    assert cwd_files_after == cwd_files_before
    # No .docx or temporary export files left in temp
    new_temp = temp_files_after - temp_files_before
    assert not any(f.endswith(".docx") for f in new_temp)

    assert isinstance(buf, io.BytesIO)
    assert buf.tell() == 0


def test_39_dossier_23_valid_docx_structure(sample_analysed_document: DocumentDetail):
    """39. Validación de estructura y márgenes del DOCX generado."""
    buf = document_export_service.generate_dossier_docx([sample_analysed_document])
    doc = docx.Document(buf)

    assert len(doc.sections) >= 1
    section = doc.sections[0]
    # Márgenes de 1 pulgada (914400 EMUs)
    assert section.top_margin.inches == pytest.approx(1.0, 0.01)
    assert section.bottom_margin.inches == pytest.approx(1.0, 0.01)
    assert section.left_margin.inches == pytest.approx(1.0, 0.01)
    assert section.right_margin.inches == pytest.approx(1.0, 0.01)


def test_40_dossier_24_existing_single_export_still_works(
    sample_analysed_document: DocumentDetail,
    sample_unanalysed_document: DocumentDetail,
):
    """40. Compatibilidad regresiva total de la exportación individual existente."""
    buf_ana = document_export_service.generate_docx(sample_analysed_document)
    assert isinstance(buf_ana, io.BytesIO)
    doc_ana = docx.Document(buf_ana)
    assert "Sentencia TS 452/2026 sobre Cártel de Fabricantes" in "\n".join(p.text for p in doc_ana.paragraphs)

    buf_unan = document_export_service.generate_docx(sample_unanalysed_document)
    assert isinstance(buf_unan, io.BytesIO)
    doc_unan = docx.Document(buf_unan)
    assert "Real Decreto-ley 7/2026 sobre Transparencia de Mercados" in "\n".join(p.text for p in doc_unan.paragraphs)


def test_41_dossier_25_real_integration_inspection(
    sample_analysed_document: DocumentDetail,
    sample_second_analysed_document: DocumentDetail,
    sample_third_document: DocumentDetail,
):
    """41. Test de integración real del generador con inspección exhaustiva de todos los elementos."""
    docs = [sample_analysed_document, sample_second_analysed_document, sample_third_document]
    title = "Dossier Integral de Derecho de la Competencia y Regulación de Mercados"

    buf = document_export_service.generate_dossier_docx(docs, title=title)
    doc = docx.Document(buf)

    # 1. Portada
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "HITCHINGS" in all_text
    assert title in all_text

    # 2. Número de documentos
    assert "DOCUMENTO 1 DE 3" in all_text
    assert "DOCUMENTO 2 DE 3" in all_text
    assert "DOCUMENTO 3 DE 3" in all_text

    # 3. Títulos en orden exacto
    assert "Sentencia TS 452/2026 sobre Cártel de Fabricantes" in all_text
    assert "Resolución CNMC S/0045/25 sobre Sector Distribución" in all_text
    assert "Manual Interno de Cumplimiento Antitrust 2026" in all_text

    # 4. Estados de análisis
    assert "RELEVANTE" in all_text
    assert "Documento incorporado sin análisis IA" in all_text

    # 5. Contenidos originales
    assert "ANTECEDENTES DE HECHO:" in all_text
    assert "RESOLUCIÓN DEL CONSEJO DE LA CNMC:" in all_text
    assert "Directrices internas de prevención y detección temprana" in all_text

    # 6. Saltos de página
    # Portada (1) + Índice (1) + Doc 1 (1) + Doc 2 (1) = 4 saltos
    assert _count_page_breaks(doc) == 4

    # 7. Cero HTML accidental
    for bad_tag in ["<html", "<body", "<div>", "<span>", "<script", "</p>"]:
        assert bad_tag not in all_text


def test_42_dossier_filename_sanitization():
    """42. Sanitización determinista de nombres de fichero de dossier."""
    # Título estándar
    name1 = sanitize_dossier_filename("Dossier de Cárteles y Competencia 2026")
    assert name1 == "dossier-carteles-y-competencia-2026.docx"

    # Título con tildes y caracteres especiales (nº normaliza 'º' a 'o' en NFKD)
    name2 = sanitize_dossier_filename("¿Resolución nº 14/2026: 'Sector Lácteo'?")
    assert name2 == "dossier-resolucion-no-14-2026-sector-lacteo.docx"

    # Fallback sin título
    assert sanitize_dossier_filename(None) == "dossier-documental-hitchings.docx"
    assert sanitize_dossier_filename("") == "dossier-documental-hitchings.docx"
    assert sanitize_dossier_filename("   ") == "dossier-documental-hitchings.docx"
