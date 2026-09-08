"""Planner and dry-run report for homogeneous v4 baseline backfill (Bloque 7G).

This script is STRICTLY DRY-RUN. It does NOT make any external API calls,
does not analyze any entry, and does not alter the database.

It inspects all Entries in the repository, identifies which ones lack an active/current
v4 completed analysis on their current content, categorizes them, groups them by source,
and provides an empirical cost estimation range based on observed historical metrics.

Usage:
    python -m scripts.plan_v4_backfill
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import Session, joinedload

from app.db.session import SessionLocal
from app.models.analysis import EntryAnalysis, AnalysisCall
from app.models.entry import Entry
from app.models.source import Source
from app.services.analysis_service import compute_analysis_input_hash
from app.services.current_analysis_service import (
    select_current_analysis,
    is_analysis_stale,
    parse_pipeline_version_num,
)
from app.services.source_sufficiency_service import assess_source_sufficiency


def fmt_usd(value: float) -> str:
    return f"${value:.4f}"


def print_sep(char: str = "=", width: int = 110) -> None:
    print(char * width)


def main() -> None:
    print_sep("=")
    print("  HITCHINGS OBSERVATORY - BLOQUE 7G: PLANIFICADOR DE BASELINE V4 (SOLO DRY-RUN)")
    print_sep("=")
    print("  AVISO: Este script es estrictamente analítico. NO realiza llamadas a Gemini")
    print("  ni modifica la base de datos.")
    print_sep("=")

    db: Session = SessionLocal()
    try:
        entries = (
            db.query(Entry)
            .options(joinedload(Entry.source))
            .order_by(Entry.source_id, Entry.published_at.desc().nullslast())
            .all()
        )
        total_entries = len(entries)

        # Categorize entries
        already_v4: list[dict[str, Any]] = []
        v4_failed: list[dict[str, Any]] = []
        prev_analyzed_needs_v4: list[dict[str, Any]] = []
        never_analyzed_needs_v4: list[dict[str, Any]] = []

        by_source_pending: dict[str, list[dict[str, Any]]] = {}
        sufficiency_counts: dict[str, int] = {"full": 0, "partial": 0, "insufficient": 0}
        total_pending_chars = 0

        for entry in entries:
            eas = (
                db.query(EntryAnalysis)
                .filter(EntryAnalysis.entry_id == entry.id)
                .order_by(EntryAnalysis.created_at.desc())
                .all()
            )
            current_hash = compute_analysis_input_hash(entry)
            current_ea = select_current_analysis(entry, eas)
            suff = assess_source_sufficiency(entry)
            content_chars = len(entry.content or "")

            all_versions = [ea.pipeline_version for ea in eas]
            v4_analyses = [ea for ea in eas if ea.pipeline_version == "v4"]
            v4_completed_current = [
                ea for ea in v4_analyses
                if ea.status == "completed" and ea.entry_content_hash == current_hash
            ]
            v4_failed_matches = [
                ea for ea in v4_analyses if ea.status == "failed"
            ]

            info = {
                "entry_id": str(entry.id),
                "source_name": entry.source.name,
                "title": entry.title,
                "published_at": entry.published_at.isoformat() if entry.published_at else "N/A",
                "content_chars": content_chars,
                "sufficiency": suff.level.value,
                "content_source": suff.signals.content_source,
                "all_versions": all_versions,
                "current_version": current_ea.pipeline_version if current_ea else None,
                "current_status": current_ea.relevance_status if current_ea else None,
                "has_stale_analyses": any(ea.entry_content_hash != current_hash for ea in eas if ea.status == "completed"),
                "needs_v4": len(v4_completed_current) == 0,
            }

            if v4_completed_current:
                already_v4.append(info)
            elif v4_failed_matches:
                v4_failed.append(info)
            elif eas:
                prev_analyzed_needs_v4.append(info)
            else:
                never_analyzed_needs_v4.append(info)

            if info["needs_v4"]:
                src = entry.source.name
                by_source_pending.setdefault(src, []).append(info)
                sufficiency_counts[suff.level.value] = sufficiency_counts.get(suff.level.value, 0) + 1
                total_pending_chars += content_chars

        pending_total = len(prev_analyzed_needs_v4) + len(never_analyzed_needs_v4) + len(v4_failed)

        # 1. High-level summary
        print(f"\n--- RESUMEN GLOBAL DE SELECCIÓN ---")
        print(f"Total de Entries en base de datos:           {total_entries}")
        print(f"  [A] Con análisis v4 vigente (completed):    {len(already_v4)}")
        print(f"  [B] Con análisis v4 previo fallido (failed):{len(v4_failed)}")
        print(f"  [C] Analizadas previamente sin v4:          {len(prev_analyzed_needs_v4)}")
        print(f"  [D] Nunca analizadas:                       {len(never_analyzed_needs_v4)}")
        print(f"Total Entries pendientes de baseline v4:     {pending_total}")
        print(f"Volumen textual acumulado pendiente:         {total_pending_chars:,} caracteres")

        # 2. Breakdown by Source
        print(f"\n--- DESGLOSE DE ENTRIES PENDIENTES POR FUENTE ---")
        print(f"{'Fuente':<45} | {'Pendientes':<10} | {'Chars Total':<12} | {'Chars Media':<12} | {'Suficiencia'}")
        print("-" * 110)
        for src_name, items in sorted(by_source_pending.items()):
            src_chars = sum(it["content_chars"] for it in items)
            avg_chars = src_chars / len(items) if items else 0
            suff_summary = ", ".join(
                f"{k}:{sum(1 for it in items if it['sufficiency'] == k)}"
                for k in ["full", "partial", "insufficient"]
                if sum(1 for it in items if it['sufficiency'] == k) > 0
            )
            print(f"{src_name[:45]:<45} | {len(items):<10} | {src_chars:<12,} | {avg_chars:<12.0f} | {suff_summary}")

        # 3. Sufficiency breakdown
        print(f"\n--- CALIDAD Y SUFICIENCIA DE FUENTE PENDIENTE ---")
        print(f"Full text disponible:        {sufficiency_counts.get('full', 0)} entries")
        print(f"Partial text (resumen):      {sufficiency_counts.get('partial', 0)} entries")
        print(f"Insufficient (insuficiente): {sufficiency_counts.get('insufficient', 0)} entries (recibirán triage; deep se omitirá)")

        # 4. Detailed item listing (first 10 and summary of rest)
        print(f"\n--- MUESTRA DEL PLAN DE BACKFILL V4 ({min(10, pending_total)} de {pending_total} pendientes) ---")
        all_pending = prev_analyzed_needs_v4 + never_analyzed_needs_v4
        for idx, it in enumerate(all_pending[:10], start=1):
            prev_str = f"Prev=[{', '.join(it['all_versions'])}]" if it["all_versions"] else "Never Analysed"
            curr_str = f"Current={it['current_version']}" if it["current_version"] else "No Current"
            print(
                f"[{idx:>2}/{pending_total}] {it['source_name'][:20]:<20} | {it['entry_id'][:8]} | "
                f"{it['published_at'][:10]} | chars={it['content_chars']:<6} | suff={it['sufficiency']:<7} | "
                f"{prev_str:<22} | {curr_str}"
            )
            print(f"      Título: {it['title'][:90]}...")

        # 5. Cost Estimation
        print(f"\n--- ESTIMACIÓN DE COSTE DEL BACKFILL V4 (DATOS HISTÓRICOS LOCALES) ---")
        print("Metodología:")
        print("- Precios Gemini 3.8 Flash: Entrada=$0.15/1M tokens, Salida=$0.60/1M tokens (incluye razonamiento).")
        print("- Todas las 79 entradas ejecutan TRIAGE (1 llamada).")
        print("- Entradas relevantes con suficiencia (full/partial) ejecutan DEEP ANALYSIS (1 llamada adicional).")
        print("- Entradas insuficientes omiten DEEP por compuerta de suficiencia.")
        print("- Rango LOW: 35% tasa de relevancia observada, salidas concisas.")
        print("- Rango EXPECTED: 45-50% tasa de relevancia observada, consumos medios por fuente.")
        print("- Rango HIGH: 65% tasa de relevancia observada, documentos extensos CAT/CURIA con razonamiento medio-alto.")

        # Historical averages per call stage
        # Triage averages: CNMC ~$0.0026, CAT ~$0.0027, CURIA ~$0.0139, EC ~$0.0028
        # Deep averages: CNMC ~$0.0068, CAT ~$0.0054, CURIA ~$0.0128, EC ~$0.0086
        cost_low = 0.0
        cost_expected = 0.0
        cost_high = 0.0

        for it in all_pending:
            src = it["source_name"]
            is_insufficient = it["sufficiency"] == "insufficient"

            if "Court of Justice" in src:
                triage_c = 0.0139
                deep_c = 0.0128
            elif "Competition Appeal" in src:
                triage_c = 0.0035 if it["content_chars"] > 10000 else 0.0027
                deep_c = 0.0070 if it["content_chars"] > 10000 else 0.0054
            elif "Comisión" in src or "European Commission" in src:
                triage_c = 0.0028
                deep_c = 0.0086
            else:  # CNMC
                triage_c = 0.0026
                deep_c = 0.0068

            # Low: 35% deep probability
            cost_low += triage_c + (0.0 if is_insufficient else deep_c * 0.35)
            # Expected: 48% deep probability
            cost_expected += triage_c + (0.0 if is_insufficient else deep_c * 0.48)
            # High: 65% deep probability
            cost_high += triage_c * 1.2 + (0.0 if is_insufficient else deep_c * 0.65 * 1.25)

        print(f"\nEstimación para {pending_total} Entries:")
        print(f"  Rango LOW:      {fmt_usd(cost_low)}  (~79 triage + ~27 deep = ~106 llamadas)")
        print(f"  Rango EXPECTED: {fmt_usd(cost_expected)}  (~79 triage + ~38 deep = ~117 llamadas)")
        print(f"  Rango HIGH:     {fmt_usd(cost_high)}  (~79 triage + ~51 deep = ~130 llamadas)")

        # 6. Evidence Length Monitoring Guidelines
        print(f"\n--- DIRECTRICES DE MONITORIZACIÓN DE LONGITUD DE CITAS (V4) ---")
        print("Métricas que se calcularán durante la ejecución futura del backfill:")
        print("- Longitud media de citas en caracteres (target: 20-180 chars).")
        print("- Longitud media de citas en palabras (target: 5-25 words).")
        print("- Número de citas que superan 180 caracteres (conteo informativo).")
        print("- Número de citas que superan 25 palabras (conteo informativo).")
        print("- Longitud máxima observada.")
        print("NOTA CRÍTICA: La longitud es una métrica de MONITORIZACIÓN. La condición dura de fallo")
        print("sigue siendo de forma única y exclusiva: GroundingValidator (verbatim match = 100%).")
        print_sep("=")

    finally:
        db.close()


if __name__ == "__main__":
    main()
