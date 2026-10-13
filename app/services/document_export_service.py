"""Document Export Service (Bloque 12 — Fase 1).

Generates high-fidelity, professional Microsoft Word (.docx) documents in-memory
for individual legal documents and judgements in the documentary archive.

Guarantees:
- Strictly decoupled from FastAPI (no Request, Response, cookies, or user sessions).
- 100% in-memory generation via io.BytesIO (0 temporary files on disk).
- Deterministic and read-only (0 DB mutations, 0 Gemini / AI model calls).
- Zero sensitive text leakage into log output.
- Clean handling of both analysed documents and unanalysed archive records.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
import uuid
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


def sanitize_export_filename(
    title: Optional[str],
    document_id: Optional[uuid.UUID] = None,
    has_analysis: bool = True,
) -> str:
    """Generate a sanitized, cross-platform filename for .docx downloads.

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


class DocumentExportService:
    """Service responsible for rendering legal documents into .docx format."""

    def sanitize_filename(
        self,
        title: Optional[str],
        document_id: Optional[uuid.UUID] = None,
        has_analysis: bool = True,
    ) -> str:
        """Sanitize filename using the canonical export strategy."""
        return sanitize_export_filename(title, document_id, has_analysis)

    def generate_docx(
        self,
        document: Union[DocumentDetail, Entry],
        analysis: Optional[EntryAnalysis] = None,
        evidence: Optional[ObservatoryEvidence] = None,
    ) -> io.BytesIO:
        """Render a legal document (with or without AI analysis) into a Word .docx buffer.

        Returns an in-memory io.BytesIO buffer positioned at the beginning,
        ready for streaming to the HTTP response.
        """
        # 1. Unify and extract document metadata
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
                # Pick the first completed analysis if present
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
                # Extract topics
                for eat in getattr(target_analysis, "topics", []) or []:
                    if getattr(eat, "topic", None) and eat.topic.name:
                        canonical_topics.append(eat.topic.name)
                # Extract evidence
                if evidence is not None:
                    evidence_data = evidence
                else:
                    evidence_data = _extract_evidence(target_analysis)

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
            if has_analysis
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
            if has_analysis
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
        r_title = p_title.add_run(title)
        r_title.font.name = FONT_NAME
        r_title.font.size = Pt(20)
        r_title.font.bold = True
        r_title.font.color.rgb = COLOR_NAVY

        # 6. Section 1: FICHA TÉCNICA DEL EXPEDIENTE
        self._render_heading_1(doc, "1. FICHA TÉCNICA DEL EXPEDIENTE")

        # Collect metadata rows
        meta_rows: list[tuple[str, str]] = []

        if source_name and source_name != "Desconocida":
            meta_rows.append(("Fuente oficial / Emisor", source_name))
        if tracked_entity_name:
            meta_rows.append(("Entidad / Tribunal", tracked_entity_name))
        if author:
            meta_rows.append(("Autor / Ponente", author))
        if published_at:
            f_pub = _format_date(published_at)
            if f_pub:
                meta_rows.append(("Fecha de resolución", f_pub))
        if captured_at:
            f_cap = _format_date(captured_at)
            if f_cap:
                meta_rows.append(("Fecha de registro", f_cap))
        if content_type:
            meta_rows.append(("Tipología", _format_content_type(content_type) or content_type))
        if url:
            meta_rows.append(("Enlace oficial de origen", url))

        # Check manual upload metadata
        if raw_metadata.get("filename"):
            f_name = raw_metadata["filename"]
            f_size = raw_metadata.get("file_size_bytes")
            if f_size and isinstance(f_size, int):
                meta_rows.append(("Archivo original", f"{f_name} ({f_size / 1024:.1f} KB)"))
            else:
                meta_rows.append(("Archivo original", f_name))

        if meta_rows:
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

            # Spacer after table
            p_space = doc.add_paragraph()
            p_space.paragraph_format.space_before = Pt(0)
            p_space.paragraph_format.space_after = Pt(8)

        # -------------------------------------------------------------------
        # Render Sections depending on analysis presence
        # -------------------------------------------------------------------
        if has_analysis:
            # 7. Section 2: DICTAMEN JURÍDICO Y CALIFICACIÓN DE RELEVANCIA
            self._render_heading_1(doc, "2. DICTAMEN JURÍDICO Y CALIFICACIÓN DE RELEVANCIA")

            # Status and score badge
            p_eval = doc.add_paragraph()
            p_eval.paragraph_format.space_before = Pt(2)
            p_eval.paragraph_format.space_after = Pt(4)

            status_label = "RELEVANTE" if relevance_status == "relevant" else (
                "NO RELEVANTE" if relevance_status == "not_relevant" else "EN REVISIÓN"
            )
            r_eval_lbl = p_eval.add_run("Calificación de impacto: ")
            r_eval_lbl.font.bold = True
            r_eval_lbl.font.size = Pt(11)

            r_eval_val = p_eval.add_run(status_label)
            r_eval_val.font.bold = True
            r_eval_val.font.size = Pt(11)
            r_eval_val.font.color.rgb = COLOR_GOLD if relevance_status == "relevant" else COLOR_SLATE_DARK

            if relevance_score is not None:
                r_score = p_eval.add_run(f"  |  Puntuación de relevancia: {relevance_score}/100")
                r_score.font.size = Pt(11)

            if canonical_topics:
                p_topics = doc.add_paragraph()
                p_topics.paragraph_format.space_before = Pt(0)
                p_topics.paragraph_format.space_after = Pt(6)
                r_top_lbl = p_topics.add_run("Áreas temáticas: ")
                r_top_lbl.font.bold = True
                r_top_lbl.font.size = Pt(10)
                r_top_val = p_topics.add_run(" • ".join(canonical_topics))
                r_top_val.font.size = Pt(10)
                r_top_val.font.color.rgb = COLOR_SLATE_DARK

            if summary_text:
                self._render_heading_2(doc, "Síntesis Analítica:")
                for paragraph in summary_text.splitlines():
                    clean_p = paragraph.strip()
                    if clean_p:
                        p_sum = doc.add_paragraph()
                        p_sum.paragraph_format.space_before = Pt(2)
                        p_sum.paragraph_format.space_after = Pt(6)
                        p_sum.paragraph_format.line_spacing = 1.15
                        r_sum = p_sum.add_run(clean_p)
                        r_sum.font.size = Pt(11)
                        r_sum.font.color.rgb = COLOR_SLATE_TEXT

            # 8. Section 3: PUNTOS CLAVE Y CRITERIOS DOCTRINALES
            if key_points:
                self._render_heading_1(doc, "3. PUNTOS CLAVE Y CRITERIOS DOCTRINALES")
                for kp in key_points:
                    clean_kp = kp.strip()
                    if clean_kp:
                        p_kp = doc.add_paragraph(style="List Bullet")
                        p_kp.paragraph_format.space_before = Pt(1)
                        p_kp.paragraph_format.space_after = Pt(4)
                        p_kp.paragraph_format.line_spacing = 1.15
                        r_kp = p_kp.add_run(clean_kp)
                        r_kp.font.size = Pt(10.5)
                        r_kp.font.color.rgb = COLOR_SLATE_TEXT

            # 9. Section 4: FUNDAMENTACIÓN Y CITAS LITERALES DE RESPALDO
            quotes_to_render: list[tuple[str, str, Optional[str]]] = []
            if evidence_data:
                # Summary quotes
                for sq in evidence_data.summary_quotes:
                    if sq.quote and sq.quote.strip():
                        quotes_to_render.append((
                            sq.quote.strip(),
                            sq.source_field or "cuerpo documental",
                            None,
                        ))
                # Keypoint quotes
                for kpe in evidence_data.key_points:
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

                    # Caption / attribution
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

            # 10. Section 5: ANEXO: TEXTO DOCUMENTAL ORIGINAL
            self._render_heading_1(doc, "5. ANEXO: TEXTO DOCUMENTAL ORIGINAL")
            self._render_original_text(doc, content, excerpt)

        else:
            # Unanalysed archive document:
            # Note of unanalysed archive status
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

            # Section 2 for unanalysed: ANEXO: TEXTO DOCUMENTAL ORIGINAL
            self._render_heading_1(doc, "2. ANEXO: TEXTO DOCUMENTAL ORIGINAL")
            self._render_original_text(doc, content, excerpt)

        # 11. Save to in-memory buffer
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)

        logger.info(
            "Documento Word exportado exitosamente en memoria (id: %s, bytes: %d, has_analysis: %s)",
            doc_id,
            buffer.getbuffer().nbytes,
            has_analysis,
        )

        return buffer

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


document_export_service = DocumentExportService()
