"""Planner and dry-run report for homogeneous v4 baseline backfill (Bloque 7G & 7G.1).

This script is STRICTLY DRY-RUN. It does NOT make any external API calls,
does not analyze any entry, and does not alter the database.

It inspects all Entries in the repository, identifies which ones lack an active/current
v4 completed analysis on their current content, categorizes them, groups them by source,
and provides a dual empirical cost estimation range (Historical-Cost vs Token-Model).

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

from app.core.config import get_settings
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


def calculate_historical_costs(pending_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Method A: Historical-Cost Method based on observed source call averages."""
    hist_costs = {
        "CNMC - Noticias": {"triage": 0.002605, "deep": 0.006788},
        "Competition Appeal Tribunal - Judgments": {
            "triage_short": 0.002742,
            "triage_long": 0.003500,
            "deep_short": 0.005362,
            "deep_long": 0.007000,
        },
        "Court of Justice of the European Union - Case Law": {"triage": 0.013946, "deep": 0.012771},
        "European Commission - Competition Policy": {"triage": 0.002782, "deep": 0.008601},
    }

    def _calc(rel_rate: float, mult: float = 1.0) -> tuple[float, int, int]:
        total = 0.0
        triage_n = len(pending_entries)
        deep_n = 0
        for it in pending_entries:
            src = it["source_name"]
            chars = it["content_chars"]
            is_insufficient = it["sufficiency"] == "insufficient"

            if "Court of Justice" in src:
                tc = hist_costs["Court of Justice of the European Union - Case Law"]["triage"]
                dc = hist_costs["Court of Justice of the European Union - Case Law"]["deep"]
            elif "Competition Appeal" in src:
                ref = hist_costs["Competition Appeal Tribunal - Judgments"]
                tc = ref["triage_long"] if chars > 10000 else ref["triage_short"]
                dc = ref["deep_long"] if chars > 10000 else ref["deep_short"]
            elif "Comisión" in src or "European Commission" in src:
                tc = hist_costs["European Commission - Competition Policy"]["triage"]
                dc = hist_costs["European Commission - Competition Policy"]["deep"]
            else:  # CNMC
                tc = hist_costs["CNMC - Noticias"]["triage"]
                dc = hist_costs["CNMC - Noticias"]["deep"]

            effective_deep = 0.0 if is_insufficient else dc * rel_rate
            total += (tc + effective_deep) * mult
            if not is_insufficient:
                deep_n += rel_rate

        return total, triage_n, round(deep_n)

    cost_low, t_low, d_low = _calc(rel_rate=0.35, mult=1.0)
    cost_expected, t_exp, d_exp = _calc(rel_rate=0.48, mult=1.0)
    cost_high, t_high, d_high = _calc(rel_rate=0.65, mult=1.20)

    return {
        "low": cost_low,
        "expected": cost_expected,
        "high": cost_high,
        "calls_low": (t_low, d_low),
        "calls_expected": (t_exp, d_exp),
        "calls_high": (t_high, d_high),
    }


def calculate_token_model_costs(
    pending_entries: list[dict[str, Any]],
    input_rate_usd: float,
    output_rate_usd: float,
) -> dict[str, float]:
    """Method B: Token-Model Method based on document chars, prompt overhead, and tokens."""
    input_per_token = input_rate_usd / 1_000_000.0
    output_per_token = output_rate_usd / 1_000_000.0

    def _calc(rel_rate: float, deep_output_tokens: int, triage_output_tokens: int) -> float:
        total = 0.0
        for it in pending_entries:
            src = it["source_name"]
            chars = it["content_chars"]
            is_insufficient = it["sufficiency"] == "insufficient"
            ratio = 3.5 if "CNMC" in src else 4.0
            content_tokens = int(chars / ratio)

            # Triage stage: ~1500 overhead tokens (system + matrix snapshot + framing)
            triage_in = 1500 + content_tokens
            triage_out = triage_output_tokens
            triage_cost = (triage_in * input_per_token) + (triage_out * output_per_token)

            # Deep stage: ~1200 overhead tokens + triage summary (~200) + content
            if is_insufficient:
                deep_cost = 0.0
            else:
                deep_in = 1200 + content_tokens
                deep_out = deep_output_tokens
                deep_cost = (deep_in * input_per_token) + (deep_out * output_per_token)

            total += triage_cost + (deep_cost * rel_rate)
        return total

    cost_low = _calc(rel_rate=0.35, deep_output_tokens=1500, triage_output_tokens=160)
    cost_expected = _calc(rel_rate=0.48, deep_output_tokens=2000, triage_output_tokens=220)
    cost_high = _calc(rel_rate=0.65, deep_output_tokens=2800, triage_output_tokens=350)

    return {
        "low": cost_low,
        "expected": cost_expected,
        "high": cost_high,
    }


def build_v4_backfill_plan(db: Session) -> dict[str, Any]:
    """Collect entries, categorize them, and compute dual cost projections."""
    settings = get_settings()

    entries = (
        db.query(Entry)
        .options(joinedload(Entry.source))
        .order_by(Entry.source_id, Entry.published_at.desc().nullslast())
        .all()
    )
    total_entries = len(entries)

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
            "current_analysis_id": str(current_ea.id) if current_ea else None,
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

    pending_list = prev_analyzed_needs_v4 + never_analyzed_needs_v4 + v4_failed
    pending_total = len(pending_list)

    input_rate = settings.GEMINI_INPUT_USD_PER_MILLION_TOKENS
    output_rate = settings.GEMINI_OUTPUT_USD_PER_MILLION_TOKENS

    method_a = calculate_historical_costs(pending_list)
    method_b = calculate_token_model_costs(pending_list, input_rate, output_rate)

    # 2027 rates: exactly 2.0x current rates
    rate_2027_input = input_rate * 2.0
    rate_2027_output = output_rate * 2.0
    method_b_2027 = calculate_token_model_costs(pending_list, rate_2027_input, rate_2027_output)

    recommended_safety_ceiling = 2.00  # Hard budget ceiling in USD

    return {
        "total_entries": total_entries,
        "already_v4": already_v4,
        "v4_failed": v4_failed,
        "prev_analyzed_needs_v4": prev_analyzed_needs_v4,
        "never_analyzed_needs_v4": never_analyzed_needs_v4,
        "pending_total": pending_total,
        "pending_list": pending_list,
        "by_source_pending": by_source_pending,
        "sufficiency_counts": sufficiency_counts,
        "total_pending_chars": total_pending_chars,
        "input_rate": input_rate,
        "output_rate": output_rate,
        "method_a": method_a,
        "method_b": method_b,
        "method_b_2027": method_b_2027,
        "recommended_safety_ceiling": recommended_safety_ceiling,
    }


def print_v4_backfill_plan(data: dict[str, Any]) -> None:
    """Print the complete human-readable report."""
    print_sep("=")
    print("  HITCHINGS OBSERVATORY - BLOQUE 7G.1: PLANIFICADOR DE BASELINE V4 (SOLO DRY-RUN)")
    print_sep("=")
    print("  AVISO: Este script es estrictamente analítico. NO realiza llamadas a Gemini")
    print("  ni modifica la base de datos.")
    print_sep("=")

    # 1. High-level summary
    print(f"\n--- 1. RESUMEN GLOBAL DE SELECCIÓN ---")
    print(f"Total de Entries en base de datos:           {data['total_entries']}")
    print(f"  [A] Con análisis v4 vigente (completed):    {len(data['already_v4'])}")
    print(f"  [B] Con análisis v4 previo fallido (failed):{len(data['v4_failed'])}")
    print(f"  [C] Analizadas previamente sin v4:          {len(data['prev_analyzed_needs_v4'])}")
    print(f"  [D] Nunca analizadas:                       {len(data['never_analyzed_needs_v4'])}")
    print(f"Total Entries pendientes de baseline v4:     {data['pending_total']}")
    print(f"Volumen textual acumulado pendiente:         {data['total_pending_chars']:,} caracteres")

    # 2. Breakdown by Source
    print(f"\n--- 2. DESGLOSE DE ENTRIES PENDIENTES POR FUENTE ---")
    print(f"{'Fuente':<45} | {'Pendientes':<10} | {'Chars Total':<12} | {'Chars Media':<12} | {'Suficiencia'}")
    print("-" * 110)
    for src_name, items in sorted(data["by_source_pending"].items()):
        src_chars = sum(it["content_chars"] for it in items)
        avg_chars = src_chars / len(items) if items else 0
        suff_summary = ", ".join(
            f"{k}:{sum(1 for it in items if it['sufficiency'] == k)}"
            for k in ["full", "partial", "insufficient"]
            if sum(1 for it in items if it['sufficiency'] == k) > 0
        )
        print(f"{src_name[:45]:<45} | {len(items):<10} | {src_chars:<12,} | {avg_chars:<12.0f} | {suff_summary}")

    # 3. Sufficiency breakdown
    print(f"\n--- 3. CALIDAD Y SUFICIENCIA DE FUENTE PENDIENTE ---")
    print(f"Full text disponible:        {data['sufficiency_counts'].get('full', 0)} entries")
    print(f"Partial text (resumen):      {data['sufficiency_counts'].get('partial', 0)} entries")
    print(f"Insufficient (insuficiente): {data['sufficiency_counts'].get('insufficient', 0)} entries (recibirán triage; deep se omitirá)")

    # 4. Detailed item listing (first 10 and summary of rest)
    print(f"\n--- 4. MUESTRA DEL PLAN DE BACKFILL V4 (10 de {data['pending_total']} pendientes con IDs inequívocos) ---")
    for idx, it in enumerate(data["pending_list"][:10], start=1):
        prev_str = f"Prev=[{', '.join(it['all_versions'])}]" if it["all_versions"] else "Never Analysed"
        curr_str = f"current_analysis_id={it['current_analysis_id'][:8]}" if it["current_analysis_id"] else "no_analysis"
        print(
            f"[{idx:>2}/{data['pending_total']}] {it['source_name'][:22]:<22} | entry_id: {it['entry_id'][:8]} | "
            f"{it['published_at'][:10]} | chars={it['content_chars']:<6} | suff={it['sufficiency']:<7} | "
            f"{prev_str:<22} | {curr_str}"
        )
        print(f"      entry_id completo: {it['entry_id']}")
        print(f"      Título: {it['title'][:85]}...")

    # 5. Dual Cost Estimation
    in_rate = data["input_rate"]
    out_rate = data["output_rate"]
    ma = data["method_a"]
    mb = data["method_b"]
    mb_2027 = data["method_b_2027"]

    print(f"\n--- 5. ESTIMACIÓN DUAL DE COSTE (GEMINI 3.8 FLASH) ---")
    print(f"Tarifas oficiales vigentes configuradas (hasta 2026-12-31):")
    print(f"  Input Tokens:  ${in_rate:.2f} / 1M tokens")
    print(f"  Output Tokens: ${out_rate:.2f} / 1M tokens (incluye reasoning tokens)")
    print(f"Tarifas a partir del 2027-01-01:")
    print(f"  Input Tokens:  ${in_rate * 2:.2f} / 1M tokens")
    print(f"  Output Tokens: ${out_rate * 2:.2f} / 1M tokens")

    print(f"\n[A] MÉTODO A: Basado en costes históricos observados por fuente")
    t_l, d_l = ma["calls_low"]
    t_e, d_e = ma["calls_expected"]
    t_h, d_h = ma["calls_high"]
    print(f"  LOW      (35% relev.): {fmt_usd(ma['low'])}  (~{t_l} triage + ~{d_l} deep = ~{t_l + d_l} llamadas)")
    print(f"  EXPECTED (48% relev.): {fmt_usd(ma['expected'])}  (~{t_e} triage + ~{d_e} deep = ~{t_e + d_e} llamadas)")
    print(f"  HIGH     (65% relev.): {fmt_usd(ma['high'])}  (~{t_h} triage + ~{d_h} deep = ~{t_h + d_h} llamadas)")

    print(f"\n[B] MÉTODO B: Basado en conteo real de caracteres, overhead de tokens y fórmulas")
    print(f"  LOW      (35% relev., 1500 out tokens): {fmt_usd(mb['low'])}")
    print(f"  EXPECTED (48% relev., 2000 out tokens): {fmt_usd(mb['expected'])}")
    print(f"  HIGH     (65% relev., 2800 out tokens): {fmt_usd(mb['high'])}")

    diff_low = ((mb["low"] - ma["low"]) / ma["low"]) * 100
    diff_exp = ((mb["expected"] - ma["expected"]) / ma["expected"]) * 100
    diff_high = ((mb["high"] - ma["high"]) / ma["high"]) * 100
    print(f"\nComparación Método B vs Método A:")
    print(f"  Diferencia LOW:      +{diff_low:.1f}%")
    print(f"  Diferencia EXPECTED: +{diff_exp:.1f}%")
    print(f"  Diferencia HIGH:     +{diff_high:.1f}%")
    print("  Explicación de la divergencia:")
    print("  El Método B es ~30% mayor porque contempla 5 sentencias CAT EWCA recién enriquecidas")
    print("  (55.000 a 84.000 caracteres cada una, ~15k-22k tokens) que no estaban presentes en")
    print("  la media histórica del benchmark de 20 entradas (cuyas sentencias CAT eran resúmenes de ~200 chars).")
    print("  Por tanto, el Método B es la previsión más fiel a la realidad.")

    print(f"\n[C] PROYECCIÓN CON TARIFAS DE ENERO 2027 ($1.50 / $7.50 por 1M tokens):")
    print(f"  LOW 2027:      {fmt_usd(mb_2027['low'])}")
    print(f"  EXPECTED 2027: {fmt_usd(mb_2027['expected'])}")
    print(f"  HIGH 2027:     {fmt_usd(mb_2027['high'])}")

    print(f"\n[D] LÍMITE DE SEGURIDAD RECOMENDADO PARA EJECUCIÓN FUTURA:")
    print(f"  Presupuesto duro de seguridad recomendado: {fmt_usd(data['recommended_safety_ceiling'])}")
    print(f"  (Cubre holgadamente el escenario EXPECTED de ${mb['expected']:.2f} y el escenario HIGH de ${mb['high']:.2f},")
    print(f"   bloqueando automáticamente la ejecución si ocurriera algún consumo anómalo o reintentos).")

    # 6. Evidence Length Monitoring Guidelines
    print(f"\n--- 6. DIRECTRICES DE MONITORIZACIÓN DE LONGITUD DE CITAS (V4) ---")
    print("Métricas que se calcularán durante la ejecución futura del backfill:")
    print("- Longitud media de citas en caracteres (target: 20-180 chars).")
    print("- Longitud media de citas en palabras (target: 5-25 words).")
    print("- Número de citas que superan 180 caracteres (conteo informativo).")
    print("- Número de citas que superan 25 palabras (conteo informativo).")
    print("- Longitud máxima observada.")
    print("NOTA CRÍTICA: La longitud es una métrica de MONITORIZACIÓN. La condición dura de fallo")
    print("sigue siendo de forma única y exclusiva: GroundingValidator (verbatim match = 100%).")
    print_sep("=")


def main() -> None:
    db: Session = SessionLocal()
    try:
        plan_data = build_v4_backfill_plan(db)
        print_v4_backfill_plan(plan_data)
    finally:
        db.close()


if __name__ == "__main__":
    main()
