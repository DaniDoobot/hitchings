"""Source Sufficiency Service for HITCHINGS.

Evaluates whether the textual content captured for an Entry is sufficient
to support downstream AI analysis (triage and deep analysis) with full grounding,
avoiding hallucinations or external ungrounded inferences.

Levels:
- FULL: Complete official text available (e.g. CURIA judgments, CNMC/EC editorial press releases,
        full PDF judgment text).
- PARTIAL: Substantive official summary or excerpt containing specific procedural and factual
           milestones (e.g. CAT substantive summaries with parties, dates, directions).
- INSUFFICIENT: Generic placeholder, one-line procedural notice without facts, or empty content.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field

from app.models.entry import Entry


class SourceSufficiencyLevel(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class SourceSufficiencySignals(BaseModel):
    content_chars: int = 0
    content_source: Optional[str] = None
    full_text_available: bool = False
    official_summary_available: bool = False
    pdf_available: bool = False
    substantive_content: bool = False


class SourceSufficiencyResult(BaseModel):
    level: SourceSufficiencyLevel
    reason: str
    signals: SourceSufficiencySignals


GENERIC_CAT_PATTERNS = [
    r"^ruling of the tribunal on costs\.?$",
    r"^ruling of the tribunal\.?$",
    r"^judgment of the tribunal\.?$",
    r"^order of the tribunal\.?$",
    r"^ruling of the chair\.?$",
    r"^order of the chair\.?$",
    r"^judgment of the court of appeal\.?$",
]

SUBSTANTIVE_CAT_SIGNALS = [
    "permission to serve",
    "service out",
    "cut-off",
    "host cases",
    "umbrella proceedings",
    "trial 1",
    "trial 2",
    "trial 3",
    "opt-out",
    "opt-in",
    "class representative",
    "carriage dispute",
    "disclosure",
    "confidentiality",
    "funder",
    "funding",
    "costs reduced",
    "reasonable and proportionate",
    "economides",
    "merricks",
    "abuse of dominant",
    "infringement",
    "damages",
    "settlement",
    "distribution plan",
    "expert reports",
    "strike out",
    "summary judgment",
]


class SourceSufficiencyService:
    """Deterministic, rule-based service to evaluate source sufficiency."""

    @staticmethod
    def assess(entry: Entry) -> SourceSufficiencyResult:
        """Assess the sufficiency level of an entry based on origin, format, and content."""
        content = (entry.content or "").strip()
        content_chars = len(content)
        meta = entry.raw_metadata or {}
        source_name = (entry.source.name if entry.source else "").lower()

        content_source = meta.get("content_source")
        full_text_avail = bool(meta.get("full_text_available"))
        official_summary_avail = bool(meta.get("has_summary"))
        pdf_avail = bool(meta.get("judgment_pdf_url"))

        # 1. Enriched CAT via PDF text layer
        if content_source == "cat_judgment_pdf_text" or meta.get("pdf_text_extracted") is True:
            signals = SourceSufficiencySignals(
                content_chars=content_chars,
                content_source=content_source,
                full_text_available=True,
                official_summary_available=official_summary_avail,
                pdf_available=pdf_avail,
                substantive_content=True,
            )
            return SourceSufficiencyResult(
                level=SourceSufficiencyLevel.FULL,
                reason="Texto íntegro oficial extraído del PDF de la resolución judicial del CAT.",
                signals=signals,
            )

        # 2. CURIA (Court of Justice of the European Union)
        if "curia" in source_name or "court of justice" in source_name:
            if content_chars == 0:
                signals = SourceSufficiencySignals(
                    content_chars=0,
                    content_source=content_source,
                    full_text_available=False,
                    official_summary_available=False,
                    pdf_available=pdf_avail,
                    substantive_content=False,
                )
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.INSUFFICIENT,
                    reason="Documento judicial de CURIA sin contenido de texto disponible.",
                    signals=signals,
                )
            signals = SourceSufficiencySignals(
                content_chars=content_chars,
                content_source=content_source or "infocuria_html",
                full_text_available=True,
                official_summary_available=False,
                pdf_available=pdf_avail,
                substantive_content=True,
            )
            return SourceSufficiencyResult(
                level=SourceSufficiencyLevel.FULL,
                reason="Resolución / Conclusiones íntegras del TJUE extraídas de InfoCuria.",
                signals=signals,
            )

        # 3. CNMC (Comisión Nacional de los Mercados y la Competencia)
        if "cnmc" in source_name:
            if content_chars < 300:
                signals = SourceSufficiencySignals(
                    content_chars=content_chars,
                    content_source=content_source or "cnmc_web",
                    full_text_available=False,
                    official_summary_available=False,
                    pdf_available=pdf_avail,
                    substantive_content=False,
                )
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.INSUFFICIENT,
                    reason="Nota de la CNMC con contenido insuficiente para análisis sustantivo.",
                    signals=signals,
                )
            signals = SourceSufficiencySignals(
                content_chars=content_chars,
                content_source=content_source or "cnmc_web",
                full_text_available=True,
                official_summary_available=False,
                pdf_available=pdf_avail,
                substantive_content=True,
            )
            return SourceSufficiencyResult(
                level=SourceSufficiencyLevel.FULL,
                reason="Nota de prensa / comunicado oficial íntegro de la CNMC.",
                signals=signals,
            )

        # 4. European Commission (DG Competition)
        if "european commission" in source_name:
            if content_chars < 300:
                signals = SourceSufficiencySignals(
                    content_chars=content_chars,
                    content_source=content_source or "presscorner_api",
                    full_text_available=False,
                    official_summary_available=False,
                    pdf_available=pdf_avail,
                    substantive_content=False,
                )
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.INSUFFICIENT,
                    reason="Comunicado de la Comisión Europea con contenido insuficiente.",
                    signals=signals,
                )
            signals = SourceSufficiencySignals(
                content_chars=content_chars,
                content_source=content_source or "presscorner_api",
                full_text_available=True,
                official_summary_available=False,
                pdf_available=pdf_avail,
                substantive_content=True,
            )
            return SourceSufficiencyResult(
                level=SourceSufficiencyLevel.FULL,
                reason="Comunicado de prensa / nota editorial íntegra de la Comisión Europea.",
                signals=signals,
            )

        # 5. Competition Appeal Tribunal (CAT)
        if "competition appeal tribunal" in source_name or "cat" in source_name:
            if content_chars == 0:
                signals = SourceSufficiencySignals(
                    content_chars=0,
                    content_source=content_source or "cat_website",
                    full_text_available=False,
                    official_summary_available=False,
                    pdf_available=pdf_avail,
                    substantive_content=False,
                )
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.INSUFFICIENT,
                    reason="Resolución del CAT sin texto de contenido ni sumario (content vacío).",
                    signals=signals,
                )

            content_lower = content.lower().strip()
            is_generic = any(re.match(p, content_lower) for p in GENERIC_CAT_PATTERNS)
            has_substantive = any(sig in content_lower for sig in SUBSTANTIVE_CAT_SIGNALS)

            if (is_generic or content_chars < 80) and not has_substantive:
                signals = SourceSufficiencySignals(
                    content_chars=content_chars,
                    content_source=content_source or "official_html_summary",
                    full_text_available=False,
                    official_summary_available=official_summary_avail,
                    pdf_available=pdf_avail,
                    substantive_content=False,
                )
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.INSUFFICIENT,
                    reason=f"Texto genérico de resolución ('{content[:80]}') sin fundamentación fáctica ni fallo sustantivo.",
                    signals=signals,
                )

            signals = SourceSufficiencySignals(
                content_chars=content_chars,
                content_source=content_source or "official_html_summary",
                full_text_available=False,
                official_summary_available=True,
                pdf_available=pdf_avail,
                substantive_content=True,
            )
            return SourceSufficiencyResult(
                level=SourceSufficiencyLevel.PARTIAL,
                reason="Sumario oficial sustantivo del CAT con hechos y pronunciamientos procesales específicos.",
                signals=signals,
            )

        # 6. OECD (Organisation for Economic Co-operation and Development)
        if "oecd" in source_name:
            if content_chars >= 500:
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.FULL,
                    reason="Publicación oficial de la OCDE con abstract o texto sustantivo suficiente.",
                    signals=SourceSufficiencySignals(
                        content_chars=content_chars,
                        content_source=content_source or "crossref_abstract",
                        full_text_available=True,
                        pdf_available=pdf_avail,
                        substantive_content=True,
                    ),
                )
            elif content_chars >= 200:
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.PARTIAL,
                    reason="Publicación oficial de la OCDE con metadatos catalográficos básicos.",
                    signals=SourceSufficiencySignals(
                        content_chars=content_chars,
                        content_source=content_source or "metadata_fallback",
                        full_text_available=False,
                        pdf_available=pdf_avail,
                        substantive_content=True,
                    ),
                )
            else:
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.INSUFFICIENT,
                    reason="Publicación oficial de la OCDE sin contenido suficiente.",
                    signals=SourceSufficiencySignals(
                        content_chars=content_chars,
                        content_source=content_source or "metadata_fallback",
                        full_text_available=False,
                        pdf_available=pdf_avail,
                        substantive_content=False,
                    ),
                )

        # 7. Bundeskartellamt (German Federal Cartel Office)
        if "bundeskartellamt" in source_name or "bkart" in source_name:
            if content_chars >= 250:
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.FULL,
                    reason="Publicación oficial del Bundeskartellamt con texto íntegro.",
                    signals=SourceSufficiencySignals(
                        content_chars=content_chars,
                        content_source=content_source or "bundeskartellamt_portal",
                        full_text_available=True,
                        pdf_available=pdf_avail,
                        substantive_content=True,
                    ),
                )
            elif content_chars >= 150:
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.PARTIAL,
                    reason="Extracto oficial o sumario de publicación del Bundeskartellamt.",
                    signals=SourceSufficiencySignals(
                        content_chars=content_chars,
                        content_source=content_source or "bundeskartellamt_portal",
                        full_text_available=False,
                        pdf_available=pdf_avail,
                        substantive_content=True,
                    ),
                )
            else:
                return SourceSufficiencyResult(
                    level=SourceSufficiencyLevel.INSUFFICIENT,
                    reason="Publicación del Bundeskartellamt sin contenido textual suficiente.",
                    signals=SourceSufficiencySignals(
                        content_chars=content_chars,
                        content_source=content_source or "bundeskartellamt_portal",
                        full_text_available=False,
                        pdf_available=pdf_avail,
                        substantive_content=False,
                    ),
                )

        # Fallback for generic sources
        if content_chars >= 1500:
            return SourceSufficiencyResult(
                level=SourceSufficiencyLevel.FULL,
                reason="Contenido textual suficiente.",
                signals=SourceSufficiencySignals(
                    content_chars=content_chars,
                    content_source=content_source,
                    full_text_available=True,
                    pdf_available=pdf_avail,
                    substantive_content=True,
                ),
            )
        elif content_chars >= 300:
            return SourceSufficiencyResult(
                level=SourceSufficiencyLevel.PARTIAL,
                reason="Contenido textual parcial.",
                signals=SourceSufficiencySignals(
                    content_chars=content_chars,
                    content_source=content_source,
                    full_text_available=False,
                    pdf_available=pdf_avail,
                    substantive_content=True,
                ),
            )
        else:
            return SourceSufficiencyResult(
                level=SourceSufficiencyLevel.INSUFFICIENT,
                reason="Contenido textual insuficiente.",
                signals=SourceSufficiencySignals(
                    content_chars=content_chars,
                    content_source=content_source,
                    full_text_available=False,
                    pdf_available=pdf_avail,
                    substantive_content=False,
                ),
            )


def assess_source_sufficiency(entry: Entry) -> SourceSufficiencyResult:
    """Convenience helper function for SourceSufficiencyService.assess."""
    return SourceSufficiencyService.assess(entry)
