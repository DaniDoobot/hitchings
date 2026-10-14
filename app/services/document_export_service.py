"""Document Export Service (Bloque 12 — Fase 1 y Fase 4A).

Generates high-fidelity, professional Microsoft Word (.docx) documents in-memory
for individual legal documents and judgements, as well as multi-document thematic dossiers.

Guarantees:
- Strictly decoupled from FastAPI (no Request, Response, cookies, or user sessions).
- 100% in-memory generation via io.BytesIO (0 temporary files on disk).
- Deterministic and read-only (0 DB mutations, 0 Gemini / AI model calls).
- Zero sensitive text leakage into log output.
- Clean handling of both analysed documents and unanalysed archive records.
- Contract enforcement: 1 <= documents <= 30 per dossier.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional, Sequence, Union

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from app.models.entry import Entry
from app.models.analysis import EntryAnalysis
from app.schemas.document import DocumentDetail, DocumentAnalysisSummary
from app.schemas.observatory import (
    ObservatoryEvidence,
    ObservatoryEvidenceQuote,
    ObservatoryKeyPointEvidence,
    ObservatoryTopicItem,
)
from app.services.observatory_query_service import _extract_evidence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Visual Palette & Formatting Constants
# ---------------------------------------------------------------------------
FONT_NAME = "Calibri"

COLOR_NAVY = RGBColor(0x0F, 0x17, 0x2A)       # #0F172A - Titulares y membrete institucional
COLOR_SLATE_DARK = RGBColor(0x1E, 0x29, 0x3B) # #1E293B - Encabezados de sección
COLOR_SLATE_TEXT = RGBColor(0x33, 0x41, 0x55) # #334155 - Cuerpo de texto principal
COLOR_MUTED = RGBColor(0x64, 0x74, 0x8B)      # #64748B - Metadatos secundarios y notas
COLOR_GOLD = RGBColor(0xB4, 0x53, 0x09)       # #B45309 - Acentos legales y citas
HEX_TABLE_SHADE = "F8FAFC"                    # #F8FAFC - Fondo tenue para cabeceras de tabla
HEX_NOTE_SHADE = "F1F5F9"                     # #F1F5F9 - Fondo para avisos

MAX_DOSSIER_DOCUMENTS: int = 30
DEFAULT_DOSSIER_TITLE: str = "Dossier documental HITCHINGS"


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------
class DossierError(ValueError):
    """Base exception for dossier export errors."""


class DossierEmptyError(DossierError):
    """Raised when attempting to generate a dossier with zero documents."""


class DossierLimitExceededError(DossierError):
    """Raised when attempting to generate a dossier exceeding MAX_DOSSIER_DOCUMENTS."""


# ---------------------------------------------------------------------------
# Internal Normalized Data Transfer Object
# ---------------------------------------------------------------------------
@dataclass
class _ExportDocumentData:
    """Internal normalized representation of a document prepared for Word export."""

    doc_id: Optional[uuid.UUID]
    title: str
    author: Optional[str]
    published_at: Optional[Union[datetime, str]]
    captured_at: Optional[Union[datetime, str]]
    content_type: Optional[str]
    content: Optional[str]
    excerpt: Optional[str]
    raw_metadata: dict[str, Any]
    url: Optional[str]
    source_name: str
    tracked_entity_name: Optional[str]
    has_analysis: bool
    relevance_status: Optional[str]
    relevance_score: Optional[int]
    summary_text: Optional[str]
    key_points: list[str]
    canonical_topics: list[str]
    evidence_data: Optional[ObservatoryEvidence]


# ---------------------------------------------------------------------------
# Filename Sanitizers
# ---------------------------------------------------------------------------
def sanitize_export_filename(
    title: Optional[str],
    document_id: Optional[uuid.UUID] = None,
    has_analysis: bool = True,
) -> str:
    """Generate a sanitized, cross-platform filename for single .docx downloads.

    Rules:
    - Prefix: 'dictamen-' if analysed, 'expediente-' if unanalysed.
    - Unicode NFKD normalized to pure ASCII (removes accents/tildes).
    - Lowercase alphanumeric characters.
    - Non-alphanumeric characters replaced by hyphens.
    - No slashes, quotes, or reserved characters.
    - Maximum 50 characters for the normalized slug.
    - Guaranteed '.docx' extension.
    - Deterministic fallback if title is empty or invalid.
    """
    prefix = "dictamen-" if has_analysis else "expediente-"

    if title and title.strip():
        # 1. Normalize unicode diacritics to ASCII
        normalized = (
            unicodedata.normalize("NFKD", title.strip())
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        # 2. Lowercase and replace non-alphanumerics with hyphens
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", normalized.lower())
        # 3. Strip duplicate and boundary hyphens
        cleaned = re.sub(r"-+", "-", cleaned).strip("-")
        # 4. Limit slug length to 50 characters
        if len(cleaned) > 50:
            cleaned = cleaned[:50].rstrip("-")

        if cleaned:
            return f"{prefix}{cleaned}.docx"

    # Fallback to UUID-based filename
    if document_id:
        doc_suffix = str(document_id).replace("-", "")[:8]
        return f"{prefix}hitchings-{doc_suffix}.docx"

    return f"{prefix}hitchings-documento.docx"


def sanitize_dossier_filename(
    title: Optional[str] = None,
) -> str:
    """Generate a sanitized, cross-platform filename for multi-document dossier downloads.

    Rules:
    - Prefix: 'dossier-'
    - Unicode NFKD normalized to pure ASCII (removes accents/tildes).
    - Lowercase alphanumeric characters and hyphens.
    - Eliminates redundant 'dossier-' or 'dossier-de-' prefixes in title.
    - Maximum 50 characters for the normalized slug.
    - Guaranteed '.docx' extension.
    - Fallback to 'dossier-documental-hitchings.docx'.
    """
    raw = (title or "").strip()
    if not raw:
        raw = "documental-hitchings"

    # 1. Normalize unicode diacritics to ASCII
    normalized = (
        unicodedata.normalize("NFKD", raw)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    # 2. Lowercase and replace non-alphanumerics with hyphens
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", normalized.lower())
    # 3. Strip duplicate and boundary hyphens
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")

    # 4. Strip redundant 'dossier-de-' or 'dossier-' if title already had it
    if cleaned.startswith("dossier-de-"):
        cleaned = cleaned[len("dossier-de-"):]
    elif cleaned.startswith("dossier-"):
        cleaned = cleaned[len("dossier-"):]

    # 5. Limit slug length to 50 characters
    if len(cleaned) > 50:
        cleaned = cleaned[:50].rstrip("-")

    if not cleaned:
        cleaned = "documental-hitchings"

    return f"dossier-{cleaned}.docx"


# ---------------------------------------------------------------------------
# Word XML & Formatting Helpers
# ---------------------------------------------------------------------------
def _add_page_number_to_run(run) -> None:
    """Inject a dynamic Word PAGE field into a text run."""
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run._r.append(fld)


def _set_cell_shading(cell, hex_color: str) -> None:
    """Apply background color shading to a table cell."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def _format_date(dt: Optional[Union[datetime, str]]) -> Optional[str]:
    """Format datetime or ISO string to standard Spanish date format."""
    if not dt:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return dt[:10]
    try:
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return str(dt)[:10]


def _format_content_type(content_type: Optional[str]) -> Optional[str]:
    """Translate technical content_type strings into clean legal categories."""
    if not content_type:
        return None
    mapping = {
        "judicial_decision": "Resolución Judicial",
        "article": "Artículo Doctrinal / Noticia",
        "manual_upload": "Aportación Manual",
        "social_post": "Publicación Profesional",
        "rss_feed": "Boletín Informativo",
        "official_gazette": "Publicación Oficial",
    }
    return mapping.get(content_type.lower(), content_type.capitalize())


# ---------------------------------------------------------------------------
# Document Export Service
# ---------------------------------------------------------------------------
class DocumentExportService:
    """Service responsible for rendering legal documents and dossiers into .docx format."""

    def sanitize_filename(
        self,
        title: Optional[str],
        document_id: Optional[uuid.UUID] = None,
        has_analysis: bool = True,
    ) -> str:
        """Sanitize filename using the canonical single-document export strategy."""
        return sanitize_export_filename(title, document_id, has_analysis)

    def sanitize_dossier_filename(
        self,
        title: Optional[str] = None,
    ) -> str:
        """Sanitize filename using the canonical dossier export strategy."""
        return sanitize_dossier_filename(title)

    def _extract_document_data(
        self,
        document: Union[DocumentDetail, Entry],
        analysis: Optional[EntryAnalysis] = None,
        evidence: Optional[ObservatoryEvidence] = None,
    ) -> _ExportDocumentData:
        """Extract and normalize document attributes and AI analysis from either a schema or model."""
        is_detail = isinstance(document, DocumentDetail)

        doc_id = document.id
        title = document.title or "Documento sin título"
        author = document.author
        published_at = document.published_at
        captured_at = document.captured_at
        content_type = document.content_type
        content = document.content
        excerpt = document.excerpt
        raw_metadata = document.raw_metadata or {}

        # Resolve URL
        url = document.canonical_url or document.url
        if url and url.startswith("upload://"):
            url = None  # Synthetic internal upload URL

        # Resolve source information
        source_name = "Desconocida"
        tracked_entity_name = None
        if is_detail:
            source_name = document.source.name
            tracked_entity_name = document.source.tracked_entity_name
        elif hasattr(document, "source") and document.source:
            source_name = document.source.name
            if getattr(document.source, "tracked_entity", None):
                tracked_entity_name = document.source.tracked_entity.display_name

        # Resolve analysis and intelligence state
        has_analysis = False
        relevance_status = None
        relevance_score = None
        summary_text = None
        key_points: list[str] = []
        canonical_topics: list[str] = []
        evidence_data: Optional[ObservatoryEvidence] = None

        if is_detail:
            has_analysis = bool(document.has_analysis and document.analysis)
            if has_analysis and document.analysis:
                relevance_status = document.analysis.relevance_status
                relevance_score = document.analysis.relevance_score
                summary_text = document.analysis.summary
                key_points = document.analysis.key_points or []
                canonical_topics = [t.name for t in document.analysis.canonical_topics if t.name]
                evidence_data = document.evidence
        else:
            # Entry model directly passed
            target_analysis = analysis
            if target_analysis is None and getattr(document, "analyses", None):
                for a in document.analyses:
                    if a.status == "completed":
                        target_analysis = a
                        break

            if target_analysis and target_analysis.status == "completed":
                has_analysis = True
                relevance_status = target_analysis.relevance_status
                relevance_score = target_analysis.relevance_score
                summary_text = target_analysis.summary
                key_points = target_analysis.key_points or []
                for eat in getattr(target_analysis, "topics", []) or []:
                    if getattr(eat, "topic", None) and eat.topic.name:
                        canonical_topics.append(eat.topic.name)
                if evidence is not None:
                    evidence_data = evidence
                else:
                    evidence_data = _extract_evidence(target_analysis)

        return _ExportDocumentData(
            doc_id=doc_id,
            title=title,
            author=author,
            published_at=published_at,
            captured_at=captured_at,
            content_type=content_type,
            content=content,
            excerpt=excerpt,
            raw_metadata=raw_metadata,
            url=url,
            source_name=source_name,
            tracked_entity_name=tracked_entity_name,
            has_analysis=has_analysis,
            relevance_status=relevance_status,
            relevance_score=relevance_score,
            summary_text=summary_text,
            key_points=key_points,
            canonical_topics=canonical_topics,
            evidence_data=evidence_data,
        )

    def _render_heading_1(self, doc: docx.Document, text: str) -> None:
        """Render a major section heading (Heading 1 equivalent)."""
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(text)
        run.font.name = FONT_NAME
        run.font.size = Pt(13.5)
        run.font.bold = True
        run.font.color.rgb = COLOR_SLATE_DARK

    def _render_heading_2(self, doc: docx.Document, text: str) -> None:
        """Render a subsection heading (Heading 2 equivalent)."""
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(text)
        run.font.name = FONT_NAME
        run.font.size = Pt(11.5)
        run.font.bold = True
        run.font.color.rgb = COLOR_SLATE_DARK

    def _render_metadata_table(
        self,
        doc: docx.Document,
        meta_rows: list[tuple[str, str]],
    ) -> None:
        """Render a 2-column key-value metadata table."""
        if not meta_rows:
            return
        table = doc.add_table(rows=len(meta_rows), cols=2)
        table.style = "Table Grid"
        table.autofit = False

        for idx, (label, val) in enumerate(meta_rows):
            row = table.rows[idx]
            c0 = row.cells[0]
            c1 = row.cells[1]

            c0.width = Inches(1.9)
            c1.width = Inches(4.6)

            _set_cell_shading(c0, HEX_TABLE_SHADE)

            p0 = c0.paragraphs[0]
            p0.paragraph_format.space_before = Pt(3)
            p0.paragraph_format.space_after = Pt(3)
            r0 = p0.add_run(label)
            r0.font.name = FONT_NAME
            r0.font.size = Pt(10)
            r0.font.bold = True
            r0.font.color.rgb = COLOR_SLATE_DARK

            p1 = c1.paragraphs[0]
            p1.paragraph_format.space_before = Pt(3)
            p1.paragraph_format.space_after = Pt(3)
            r1 = p1.add_run(val)
            r1.font.name = FONT_NAME
            r1.font.size = Pt(10)
            r1.font.color.rgb = COLOR_SLATE_TEXT

        p_space = doc.add_paragraph()
        p_space.paragraph_format.space_before = Pt(0)
        p_space.paragraph_format.space_after = Pt(8)

    def _render_analysis_sections(
        self,
        doc: docx.Document,
        data: _ExportDocumentData,
        heading_prefix: str = "2.",
    ) -> None:
        """Render dictamen, key points, and evidence sections."""
        # Dictamen Jurídico
        self._render_heading_1(doc, f"{heading_prefix} DICTAMEN JURÍDICO Y CALIFICACIÓN DE RELEVANCIA")

        p_eval = doc.add_paragraph()
        p_eval.paragraph_format.space_before = Pt(2)
        p_eval.paragraph_format.space_after = Pt(4)

        status_label = "RELEVANTE" if data.relevance_status == "relevant" else (
            "NO RELEVANTE" if data.relevance_status == "not_relevant" else "EN REVISIÓN"
        )
        r_eval_lbl = p_eval.add_run("Calificación de impacto: ")
        r_eval_lbl.font.bold = True
        r_eval_lbl.font.size = Pt(11)

        r_eval_val = p_eval.add_run(status_label)
        r_eval_val.font.bold = True
        r_eval_val.font.size = Pt(11)
        r_eval_val.font.color.rgb = COLOR_GOLD if data.relevance_status == "relevant" else COLOR_SLATE_DARK

        if data.relevance_score is not None:
            r_score = p_eval.add_run(f"  |  Puntuación de relevancia: {data.relevance_score}/100")
            r_score.font.size = Pt(11)

        if data.canonical_topics:
            p_topics = doc.add_paragraph()
            p_topics.paragraph_format.space_before = Pt(0)
            p_topics.paragraph_format.space_after = Pt(6)
            r_top_lbl = p_topics.add_run("Áreas temáticas: ")
            r_top_lbl.font.bold = True
            r_top_lbl.font.size = Pt(10)
            r_top_val = p_topics.add_run(" • ".join(data.canonical_topics))
            r_top_val.font.size = Pt(10)
            r_top_val.font.color.rgb = COLOR_SLATE_DARK

        if data.summary_text:
            self._render_heading_2(doc, "Síntesis Analítica:")
            for paragraph in data.summary_text.splitlines():
                clean_p = paragraph.strip()
                if clean_p:
                    p_sum = doc.add_paragraph()
                    p_sum.paragraph_format.space_before = Pt(2)
                    p_sum.paragraph_format.space_after = Pt(6)
                    p_sum.paragraph_format.line_spacing = 1.15
                    r_sum = p_sum.add_run(clean_p)
                    r_sum.font.size = Pt(11)
                    r_sum.font.color.rgb = COLOR_SLATE_TEXT

        # Puntos Clave
        if data.key_points:
            self._render_heading_1(doc, "3. PUNTOS CLAVE Y CRITERIOS DOCTRINALES")
            for kp in data.key_points:
                clean_kp = kp.strip()
                if clean_kp:
                    p_kp = doc.add_paragraph(style="List Bullet")
                    p_kp.paragraph_format.space_before = Pt(1)
                    p_kp.paragraph_format.space_after = Pt(4)
                    p_kp.paragraph_format.line_spacing = 1.15
                    r_kp = p_kp.add_run(clean_kp)
                    r_kp.font.size = Pt(10.5)
                    r_kp.font.color.rgb = COLOR_SLATE_TEXT

        # Evidencias
        quotes_to_render: list[tuple[str, str, Optional[str]]] = []
        if data.evidence_data:
            for sq in data.evidence_data.summary_quotes:
                if sq.quote and sq.quote.strip():
                    quotes_to_render.append((
                        sq.quote.strip(),
                        sq.source_field or "cuerpo documental",
                        None,
                    ))
            for kpe in data.evidence_data.key_points:
                for kq in kpe.quotes:
                    if kq.quote and kq.quote.strip():
                        quotes_to_render.append((
                            kq.quote.strip(),
                            kq.source_field or "cuerpo documental",
                            kpe.point,
                        ))

        if quotes_to_render:
            self._render_heading_1(doc, "4. FUNDAMENTACIÓN Y CITAS LITERALES DE RESPALDO")
            p_desc = doc.add_paragraph()
            p_desc.paragraph_format.space_before = Pt(0)
            p_desc.paragraph_format.space_after = Pt(6)
            r_desc = p_desc.add_run(
                "Citas textuales verificadas extraídas del documento original (Grounding verificado):"
            )
            r_desc.font.size = Pt(10)
            r_desc.font.italic = True
            r_desc.font.color.rgb = COLOR_MUTED

            for quote_text, src_field, associated_point in quotes_to_render:
                p_quote = doc.add_paragraph()
                p_quote.paragraph_format.left_indent = Inches(0.5)
                p_quote.paragraph_format.space_before = Pt(3)
                p_quote.paragraph_format.space_after = Pt(1)

                r_q = p_quote.add_run(f'“{quote_text}”')
                r_q.font.name = FONT_NAME
                r_q.font.size = Pt(10)
                r_q.font.italic = True
                r_q.font.color.rgb = COLOR_SLATE_TEXT

                p_attr = doc.add_paragraph()
                p_attr.paragraph_format.left_indent = Inches(0.5)
                p_attr.paragraph_format.space_before = Pt(0)
                p_attr.paragraph_format.space_after = Pt(6)

                caption = f"[Cita literal — Campo de origen: {src_field}]"
                if associated_point:
                    caption = f"[Cita literal sobre: '{associated_point}' — Origen: {src_field}]"

                r_attr = p_attr.add_run(caption)
                r_attr.font.name = FONT_NAME
                r_attr.font.size = Pt(8.5)
                r_attr.font.italic = True
                r_attr.font.color.rgb = COLOR_MUTED

    def _render_original_text(
        self,
        doc: docx.Document,
        content: Optional[str],
        excerpt: Optional[str],
    ) -> None:
        """Render original document content with clean paragraph reflow."""
        raw_body = content.strip() if content and content.strip() else (
            excerpt.strip() if excerpt and excerpt.strip() else None
        )

        if not raw_body:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(6)
            r = p.add_run(
                "El texto documental íntegro se encuentra disponible directamente en el enlace oficial de origen."
            )
            r.font.name = FONT_NAME
            r.font.size = Pt(9.5)
            r.font.italic = True
            r.font.color.rgb = COLOR_MUTED
            return

        # Split into readable paragraphs preserving natural line breaks
        paragraphs = re.split(r"\n{2,}", raw_body)
        for raw_p in paragraphs:
            # Flatten soft line breaks within paragraphs
            clean_lines = [l.strip() for l in raw_p.splitlines() if l.strip()]
            if not clean_lines:
                continue
            paragraph_text = " ".join(clean_lines)

            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(5)
            p.paragraph_format.line_spacing = 1.08

            run = p.add_run(paragraph_text)
            run.font.name = FONT_NAME
            run.font.size = Pt(9.5)
            run.font.color.rgb = COLOR_SLATE_TEXT

    def generate_docx(
        self,
        document: Union[DocumentDetail, Entry],
        analysis: Optional[EntryAnalysis] = None,
        evidence: Optional[ObservatoryEvidence] = None,
    ) -> io.BytesIO:
        """Render an individual legal document into a Word .docx buffer.

        Returns an in-memory io.BytesIO buffer positioned at the beginning,
        ready for streaming to the HTTP response.
        """
        data = self._extract_document_data(document, analysis=analysis, evidence=evidence)

        # 2. Initialize Word Document
        doc = docx.Document()

        # Set 2.54 cm (1 inch) margins
        for sec in doc.sections:
            sec.top_margin = Inches(1)
            sec.bottom_margin = Inches(1)
            sec.left_margin = Inches(1)
            sec.right_margin = Inches(1)

        # Configure default Normal font style
        normal_style = doc.styles["Normal"]
        normal_style.font.name = FONT_NAME
        normal_style.font.size = Pt(11)
        normal_style.font.color.rgb = COLOR_SLATE_TEXT

        # 3. Setup Header & Footer
        section = doc.sections[0]

        # Top section header
        header = section.header
        p_header = header.paragraphs[0]
        p_header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r_head = p_header.add_run(
            "HITCHINGS — OBSERVATORIO LEGAL Y FONDO DOCUMENTAL"
            if data.has_analysis
            else "HITCHINGS — EXPEDIENTE DOCUMENTAL DE ARCHIVO"
        )
        r_head.font.name = FONT_NAME
        r_head.font.size = Pt(8.5)
        r_head.font.color.rgb = COLOR_MUTED
        r_head.font.italic = True

        # Footer with legal notice and page number
        footer = section.footer
        p_footer = footer.paragraphs[0]
        p_footer.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r_foot_left = p_footer.add_run("HITCHINGS Legal Intelligence — Documento confidencial")
        r_foot_left.font.name = FONT_NAME
        r_foot_left.font.size = Pt(8.5)
        r_foot_left.font.color.rgb = COLOR_MUTED
        r_foot_left.font.italic = True

        r_foot_tab = p_footer.add_run("\t\tPágina ")
        r_foot_tab.font.name = FONT_NAME
        r_foot_tab.font.size = Pt(8.5)
        r_foot_tab.font.color.rgb = COLOR_MUTED

        r_foot_page = p_footer.add_run()
        r_foot_page.font.name = FONT_NAME
        r_foot_page.font.size = Pt(8.5)
        r_foot_page.font.color.rgb = COLOR_MUTED
        _add_page_number_to_run(r_foot_page)

        # 4. Top Membrete on first page body
        p_membrete = doc.add_paragraph()
        p_membrete.paragraph_format.space_before = Pt(0)
        p_membrete.paragraph_format.space_after = Pt(4)
        r_memb = p_membrete.add_run(
            "HITCHINGS — OBSERVATORIO LEGAL Y FONDO DOCUMENTAL"
            if data.has_analysis
            else "HITCHINGS — EXPEDIENTE DOCUMENTAL DE ARCHIVO"
        )
        r_memb.font.name = FONT_NAME
        r_memb.font.size = Pt(9.5)
        r_memb.font.bold = True
        r_memb.font.color.rgb = COLOR_GOLD

        # 5. Document Title
        p_title = doc.add_paragraph()
        p_title.paragraph_format.space_before = Pt(0)
        p_title.paragraph_format.space_after = Pt(14)
        r_title = p_title.add_run(data.title)
        r_title.font.name = FONT_NAME
        r_title.font.size = Pt(20)
        r_title.font.bold = True
        r_title.font.color.rgb = COLOR_NAVY

        # 6. Section 1: FICHA TÉCNICA DEL EXPEDIENTE
        self._render_heading_1(doc, "1. FICHA TÉCNICA DEL EXPEDIENTE")

        meta_rows: list[tuple[str, str]] = []
        if data.source_name and data.source_name != "Desconocida":
            meta_rows.append(("Fuente oficial / Emisor", data.source_name))
        if data.tracked_entity_name:
            meta_rows.append(("Entidad / Tribunal", data.tracked_entity_name))
        if data.author:
            meta_rows.append(("Autor / Ponente", data.author))
        if data.published_at:
            f_pub = _format_date(data.published_at)
            if f_pub:
                meta_rows.append(("Fecha de resolución", f_pub))
        if data.captured_at:
            f_cap = _format_date(data.captured_at)
            if f_cap:
                meta_rows.append(("Fecha de registro", f_cap))
        if data.content_type:
            meta_rows.append(("Tipología", _format_content_type(data.content_type) or data.content_type))
        if data.url:
            meta_rows.append(("Enlace oficial de origen", data.url))

        if data.raw_metadata.get("filename"):
            f_name = data.raw_metadata["filename"]
            f_size = data.raw_metadata.get("file_size_bytes")
            if f_size and isinstance(f_size, int):
                meta_rows.append(("Archivo original", f"{f_name} ({f_size / 1024:.1f} KB)"))
            else:
                meta_rows.append(("Archivo original", f_name))

        self._render_metadata_table(doc, meta_rows)

        # -------------------------------------------------------------------
        # Render Sections depending on analysis presence
        # -------------------------------------------------------------------
        if data.has_analysis:
            self._render_analysis_sections(doc, data, heading_prefix="2.")
            self._render_heading_1(doc, "5. ANEXO: TEXTO DOCUMENTAL ORIGINAL")
            self._render_original_text(doc, data.content, data.excerpt)
        else:
            p_note = doc.add_paragraph()
            p_note.paragraph_format.space_before = Pt(8)
            p_note.paragraph_format.space_after = Pt(12)
            p_note.paragraph_format.left_indent = Inches(0.2)
            r_note = p_note.add_run(
                "Expediente en archivo documental original sin análisis automatizado vigente en la matriz de seguimiento."
            )
            r_note.font.name = FONT_NAME
            r_note.font.size = Pt(10.5)
            r_note.font.italic = True
            r_note.font.color.rgb = COLOR_MUTED

            self._render_heading_1(doc, "2. ANEXO: TEXTO DOCUMENTAL ORIGINAL")
            self._render_original_text(doc, data.content, data.excerpt)

        # 11. Save to in-memory buffer
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)

        logger.info(
            "Documento Word exportado exitosamente en memoria (id: %s, bytes: %d, has_analysis: %s)",
            data.doc_id,
            buffer.getbuffer().nbytes,
            data.has_analysis,
        )

        return buffer

    def generate_dossier_docx(
        self,
        documents: Sequence[Union[DocumentDetail, Entry]],
        title: Optional[str] = None,
    ) -> io.BytesIO:
        """Render a thematic dossier compiling multiple documents into a Word .docx buffer.

        Guarantees:
        - 100% in-memory via io.BytesIO (no temp files on disk).
        - Deterministic and read-only (0 DB mutations, 0 Gemini / AI model calls).
        - Contract: 1 <= len(documents) <= MAX_DOSSIER_DOCUMENTS (30).
        - Professional cover page (Portada) and executive index (Índice de expedientes).
        - Clear separation of each document chapter onto its own page.
        - Explicit labeling for unanalysed archive records without hallucinating data.
        """
        doc_list = list(documents)
        if len(doc_list) == 0:
            raise DossierEmptyError("El dossier debe contener al menos 1 documento.")
        if len(doc_list) > MAX_DOSSIER_DOCUMENTS:
            raise DossierLimitExceededError(
                f"El dossier no puede exceder el límite máximo de {MAX_DOSSIER_DOCUMENTS} documentos (recibidos: {len(doc_list)})."
            )

        effective_title = (title.strip() if title and title.strip() else DEFAULT_DOSSIER_TITLE)
        total_docs = len(doc_list)

        # 1. Extract data for each document in sequence
        extracted_docs = [self._extract_document_data(d) for d in doc_list]

        # 2. Initialize Word Document
        doc = docx.Document()

        # Set 2.54 cm (1 inch) margins
        for sec in doc.sections:
            sec.top_margin = Inches(1)
            sec.bottom_margin = Inches(1)
            sec.left_margin = Inches(1)
            sec.right_margin = Inches(1)

        # Configure default Normal font style
        normal_style = doc.styles["Normal"]
        normal_style.font.name = FONT_NAME
        normal_style.font.size = Pt(11)
        normal_style.font.color.rgb = COLOR_SLATE_TEXT

        # 3. Setup Header & Footer
        section = doc.sections[0]

        header = section.header
        p_header = header.paragraphs[0]
        p_header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r_head = p_header.add_run("HITCHINGS — DOSSIER DOCUMENTAL MULTIDOCUMENTO")
        r_head.font.name = FONT_NAME
        r_head.font.size = Pt(8.5)
        r_head.font.color.rgb = COLOR_MUTED
        r_head.font.italic = True

        footer = section.footer
        p_footer = footer.paragraphs[0]
        p_footer.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r_foot_left = p_footer.add_run("HITCHINGS Legal Intelligence — Dossier Confidencial")
        r_foot_left.font.name = FONT_NAME
        r_foot_left.font.size = Pt(8.5)
        r_foot_left.font.color.rgb = COLOR_MUTED
        r_foot_left.font.italic = True

        r_foot_tab = p_footer.add_run("\t\tPágina ")
        r_foot_tab.font.name = FONT_NAME
        r_foot_tab.font.size = Pt(8.5)
        r_foot_tab.font.color.rgb = COLOR_MUTED

        r_foot_page = p_footer.add_run()
        r_foot_page.font.name = FONT_NAME
        r_foot_page.font.size = Pt(8.5)
        r_foot_page.font.color.rgb = COLOR_MUTED
        _add_page_number_to_run(r_foot_page)

        # 4. Portada (Cover Page)
        p_brand = doc.add_paragraph()
        p_brand.paragraph_format.space_before = Pt(36)
        p_brand.paragraph_format.space_after = Pt(2)
        r_brand = p_brand.add_run("HITCHINGS")
        r_brand.font.name = FONT_NAME
        r_brand.font.size = Pt(24)
        r_brand.font.bold = True
        r_brand.font.color.rgb = COLOR_NAVY

        p_brand_sub = doc.add_paragraph()
        p_brand_sub.paragraph_format.space_before = Pt(0)
        p_brand_sub.paragraph_format.space_after = Pt(36)
        r_sub = p_brand_sub.add_run("OBSERVATORIO JURÍDICO & FONDO DOCUMENTAL")
        r_sub.font.name = FONT_NAME
        r_sub.font.size = Pt(10)
        r_sub.font.color.rgb = COLOR_MUTED

        p_badge = doc.add_paragraph()
        p_badge.paragraph_format.space_before = Pt(20)
        p_badge.paragraph_format.space_after = Pt(6)
        r_badge = p_badge.add_run("DOSSIER DOCUMENTAL")
        r_badge.font.name = FONT_NAME
        r_badge.font.size = Pt(12)
        r_badge.font.bold = True
        r_badge.font.color.rgb = COLOR_GOLD

        p_title = doc.add_paragraph()
        p_title.paragraph_format.space_before = Pt(0)
        p_title.paragraph_format.space_after = Pt(16)
        r_title = p_title.add_run(effective_title)
        r_title.font.name = FONT_NAME
        r_title.font.size = Pt(22)
        r_title.font.bold = True
        r_title.font.color.rgb = COLOR_NAVY

        # Cover metadata block
        cover_rows: list[tuple[str, str]] = [
            ("Fecha de generación", datetime.now(timezone.utc).strftime("%d/%m/%Y")),
            ("Total de documentos incluidos", str(total_docs)),
            ("Ámbito de compilación", "Fondo Documental y Dictámenes Automatizados HITCHINGS"),
        ]
        self._render_metadata_table(doc, cover_rows)

        # Page break after Cover
        doc.add_page_break()

        # 5. Índice / Resumen de Documentos (Executive Index)
        self._render_heading_1(doc, "ÍNDICE DE EXPEDIENTES Y DOCUMENTOS INCLUIDOS")

        p_intro = doc.add_paragraph()
        p_intro.paragraph_format.space_before = Pt(2)
        p_intro.paragraph_format.space_after = Pt(8)
        r_intro = p_intro.add_run(
            "Relación sistemática de los expedientes documentales compilados en el presente dossier, "
            "con indicación de su fuente oficial, fecha y estado de análisis en la plataforma HITCHINGS:"
        )
        r_intro.font.name = FONT_NAME
        r_intro.font.size = Pt(9.5)
        r_intro.font.italic = True
        r_intro.font.color.rgb = COLOR_MUTED

        index_table = doc.add_table(rows=total_docs + 1, cols=5)
        index_table.style = "Table Grid"
        index_table.autofit = False

        index_headers = ["Nº", "Título del Expediente", "Organismo / Fuente", "Fecha", "Calificación / Estado"]
        col_widths = [Inches(0.45), Inches(2.55), Inches(1.5), Inches(0.85), Inches(1.65)]

        # Header row
        hdr_row = index_table.rows[0]
        for c_idx, h_text in enumerate(index_headers):
            cell = hdr_row.cells[c_idx]
            cell.width = col_widths[c_idx]
            _set_cell_shading(cell, HEX_TABLE_SHADE)
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(3)
            p.paragraph_format.space_after = Pt(3)
            r = p.add_run(h_text)
            r.font.name = FONT_NAME
            r.font.size = Pt(9.5)
            r.font.bold = True
            r.font.color.rgb = COLOR_SLATE_DARK

        # Data rows
        for d_idx, d_data in enumerate(extracted_docs, start=1):
            row = index_table.rows[d_idx]

            # Format status cell
            if d_data.has_analysis:
                status_label = "RELEVANTE" if d_data.relevance_status == "relevant" else (
                    "NO RELEVANTE" if d_data.relevance_status == "not_relevant" else "EN REVISIÓN"
                )
                if d_data.relevance_score is not None:
                    status_text = f"{status_label} ({d_data.relevance_score}/100)"
                else:
                    status_text = status_label
            else:
                status_text = "Sin análisis IA"

            row_data = [
                str(d_idx),
                d_data.title,
                d_data.source_name,
                _format_date(d_data.published_at or d_data.captured_at) or "—",
                status_text,
            ]

            for c_idx, val in enumerate(row_data):
                cell = row.cells[c_idx]
                cell.width = col_widths[c_idx]
                p = cell.paragraphs[0]
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.space_after = Pt(3)
                r = p.add_run(val)
                r.font.name = FONT_NAME
                r.font.size = Pt(9)
                r.font.color.rgb = COLOR_SLATE_TEXT
                if c_idx == 0:
                    r.font.bold = True

        p_spacer = doc.add_paragraph()
        p_spacer.paragraph_format.space_before = Pt(0)
        p_spacer.paragraph_format.space_after = Pt(8)

        # Page break after Index
        doc.add_page_break()

        # 6. Chapters per Document
        for doc_num, d_data in enumerate(extracted_docs, start=1):
            if doc_num > 1:
                doc.add_page_break()

            # Chapter Banner
            p_banner = doc.add_paragraph()
            p_banner.paragraph_format.space_before = Pt(0)
            p_banner.paragraph_format.space_after = Pt(2)
            r_banner = p_banner.add_run(f"DOCUMENTO {doc_num} DE {total_docs}")
            r_banner.font.name = FONT_NAME
            r_banner.font.size = Pt(10)
            r_banner.font.bold = True
            r_banner.font.color.rgb = COLOR_GOLD

            # Document Title
            p_title = doc.add_paragraph()
            p_title.paragraph_format.space_before = Pt(0)
            p_title.paragraph_format.space_after = Pt(12)
            r_title = p_title.add_run(d_data.title)
            r_title.font.name = FONT_NAME
            r_title.font.size = Pt(18)
            r_title.font.bold = True
            r_title.font.color.rgb = COLOR_NAVY

            # Section 1: Ficha Técnica
            self._render_heading_1(doc, "1. FICHA TÉCNICA DEL EXPEDIENTE")
            meta_rows: list[tuple[str, str]] = []
            if d_data.source_name and d_data.source_name != "Desconocida":
                meta_rows.append(("Fuente oficial / Emisor", d_data.source_name))
            if d_data.tracked_entity_name:
                meta_rows.append(("Entidad / Tribunal", d_data.tracked_entity_name))
            if d_data.author:
                meta_rows.append(("Autor / Ponente", d_data.author))
            if d_data.published_at:
                f_pub = _format_date(d_data.published_at)
                if f_pub:
                    meta_rows.append(("Fecha de resolución", f_pub))
            if d_data.captured_at:
                f_cap = _format_date(d_data.captured_at)
                if f_cap:
                    meta_rows.append(("Fecha de registro", f_cap))
            if d_data.content_type:
                meta_rows.append(("Tipología", _format_content_type(d_data.content_type) or d_data.content_type))
            if d_data.url:
                meta_rows.append(("Enlace oficial de origen", d_data.url))
            if d_data.raw_metadata.get("filename"):
                f_name = d_data.raw_metadata["filename"]
                f_size = d_data.raw_metadata.get("file_size_bytes")
                if f_size and isinstance(f_size, int):
                    meta_rows.append(("Archivo original", f"{f_name} ({f_size / 1024:.1f} KB)"))
                else:
                    meta_rows.append(("Archivo original", f_name))

            # Status row in Ficha Técnica
            if d_data.has_analysis:
                status_str = "RELEVANTE" if d_data.relevance_status == "relevant" else (
                    "NO RELEVANTE" if d_data.relevance_status == "not_relevant" else "EN REVISIÓN"
                )
                score_str = f" (Puntuación: {d_data.relevance_score}/100)" if d_data.relevance_score is not None else ""
                meta_rows.append(("Estado de análisis", f"Dictamen automatizado disponible — {status_str}{score_str}"))
            else:
                meta_rows.append(("Estado de análisis", "Documento incorporado sin análisis IA (Archivo documental original)"))

            self._render_metadata_table(doc, meta_rows)

            if d_data.has_analysis:
                self._render_analysis_sections(doc, d_data, heading_prefix="2.")
                self._render_heading_1(doc, "5. ANEXO: TEXTO DOCUMENTAL ORIGINAL")
                self._render_original_text(doc, d_data.content, d_data.excerpt)
            else:
                p_note = doc.add_paragraph()
                p_note.paragraph_format.space_before = Pt(8)
                p_note.paragraph_format.space_after = Pt(12)
                p_note.paragraph_format.left_indent = Inches(0.2)
                r_note = p_note.add_run(
                    "Documento incorporado sin análisis IA. Expediente en archivo documental original sin análisis automatizado vigente en la matriz de seguimiento."
                )
                r_note.font.name = FONT_NAME
                r_note.font.size = Pt(10.5)
                r_note.font.italic = True
                r_note.font.color.rgb = COLOR_MUTED

                self._render_heading_1(doc, "2. ANEXO: TEXTO DOCUMENTAL ORIGINAL")
                self._render_original_text(doc, d_data.content, d_data.excerpt)

        # 7. Save to in-memory buffer
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)

        logger.info(
            "Dossier Word exportado exitosamente en memoria (documentos: %d, bytes: %d, title: %s)",
            total_docs,
            buffer.getbuffer().nbytes,
            effective_title,
        )

        return buffer


document_export_service = DocumentExportService()
