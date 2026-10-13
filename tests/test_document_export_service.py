"""Unit tests for DocumentExportService (Bloque 12 — Fase 1).

Covers:
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
"""

import io
import uuid
from datetime import datetime, timezone

import docx
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
    DocumentExportService,
    document_export_service,
    sanitize_export_filename,
)


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


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

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
    assert "ñ" not in filename  # Convertido a n
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
