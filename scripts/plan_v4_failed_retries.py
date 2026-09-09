"""Read-only planner for failed v4 analysis retries (Bloque 7H.1).

Exclusively inspects entries whose v4 analysis failed during baseline backfill.
Identifies root failure causes, token usage, and classifies retry feasibility.
STRICTLY READ-ONLY: ZERO Gemini calls, ZERO database writes.

Usage:
    python -m scripts.plan_v4_failed_retries
"""

import sys
import uuid
from pathlib import Path
from typing import Any

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import SessionLocal
from app.models.analysis import AnalysisCall, EntryAnalysis
from app.models.entry import Entry
from app.services.source_sufficiency_service import assess_source_sufficiency


def classify_failure(ea: EntryAnalysis, calls: list[AnalysisCall]) -> dict[str, Any]:
    """Classify the root cause and retry feasibility for a failed v4 analysis."""
    failed_call = next((c for c in reversed(calls) if c.status == "failed"), None)
    if not failed_call:
        return {
            "stage": "unknown",
            "error_type": "Unknown",
            "error_message": ea.reason or "No failed call found",
            "classification": "V4_RETRY_UNLIKELY",
            "recommendation": "Investigate database integrity.",
        }

    err_type = failed_call.error_type or "UnknownError"
    err_msg = failed_call.error_message or ""
    stage = failed_call.stage

    meta = failed_call.call_metadata or {}
    thought_tokens = meta.get("output_thought_tokens", 0)
    text_tokens = meta.get("output_text_tokens", 0)
    total_out = failed_call.output_tokens or 0

    if err_type == "AnalysisGroundingError":
        # Isolated paraphrase in 1 quote out of 14; call was not truncated
        return {
            "stage": stage,
            "error_type": err_type,
            "error_message": err_msg,
            "classification": "RETRY_V4_REASONABLE",
            "recommendation": (
                "Análisis no truncado (13 de 14 citas válidas). Un reintento explícito v4 "
                "con las mismas directrices de extract-first puede seleccionar una cita literal continua."
            ),
            "thought_tokens": thought_tokens,
            "text_tokens": text_tokens,
            "total_out_tokens": total_out,
        }
    elif err_type == "NoParsedResponse":
        # Check if truncated by max_output_tokens
        if total_out >= 4000:
            return {
                "stage": stage,
                "error_type": "NoParsedResponse (MAX_TOKENS / Truncation)",
                "error_message": f"Truncated output: {total_out}/4096 tokens used ({thought_tokens} thought + {text_tokens} text).",
                "classification": "V4_RETRY_UNLIKELY",
                "recommendation": (
                    "El modelo consumió el límite de 4.096 tokens debido al alto número de tokens de "
                    "pensamiento (thinking_level='medium'). Un reintento a ciegas con el mismo prompt v4 "
                    "volverá a truncarse. Se requiere ajuste de pipeline/configuración (e.g. max_output_tokens=8192 "
                    "o thinking_level='low')."
                ),
                "thought_tokens": thought_tokens,
                "text_tokens": text_tokens,
                "total_out_tokens": total_out,
            }
        else:
            return {
                "stage": stage,
                "error_type": err_type,
                "error_message": err_msg,
                "classification": "V4_RETRY_UNLIKELY",
                "recommendation": "Error de parseo JSON no atribuible a longitud máxima.",
                "thought_tokens": thought_tokens,
                "text_tokens": text_tokens,
                "total_out_tokens": total_out,
            }
    else:
        return {
            "stage": stage,
            "error_type": err_type,
            "error_message": err_msg,
            "classification": "V4_RETRY_UNLIKELY",
            "recommendation": f"Fallo clasificado como {err_type}.",
            "thought_tokens": thought_tokens,
            "text_tokens": text_tokens,
            "total_out_tokens": total_out,
        }


def main() -> int:
    db = SessionLocal()
    try:
        print("=" * 105)
        print("  HITCHINGS OBSERVATORY - PLANIFICADOR READ-ONLY DE REINTENTOS FALLIDOS V4 (BLOQUE 7H.1)")
        print("=" * 105)
        print("  AVISO: Este script es estrictamente analítico (READ-ONLY).")
        print("  NO realiza llamadas a Gemini ni modifica la base de datos.")
        print("=" * 105)

        # 1. Query all v4 failed analyses that do not have a completed v4
        failed_eas = (
            db.query(EntryAnalysis)
            .filter(EntryAnalysis.pipeline_version == "v4", EntryAnalysis.status == "failed")
            .order_by(EntryAnalysis.created_at.asc())
            .all()
        )

        active_failed = []
        for ea in failed_eas:
            # Check if entry has a subsequent or prior completed v4
            has_comp = (
                db.query(EntryAnalysis)
                .filter(
                    EntryAnalysis.entry_id == ea.entry_id,
                    EntryAnalysis.pipeline_version == "v4",
                    EntryAnalysis.status == "completed",
                )
                .first()
            )
            if not has_comp:
                active_failed.append(ea)

        print(f"\nTotal Entries con análisis v4 fallido y sin versión vigente: {len(active_failed)}")
        if not active_failed:
            print("No hay Entries fallidas pendientes de reintento.")
            return 0

        total_hist_cost = 0.0

        for idx, ea in enumerate(active_failed, start=1):
            entry = ea.entry
            calls = (
                db.query(AnalysisCall)
                .filter(AnalysisCall.entry_analysis_id == ea.id)
                .order_by(AnalysisCall.started_at.asc())
                .all()
            )
            diag = classify_failure(ea, calls)
            ea_cost = float(sum(c.estimated_cost_usd or 0.0 for c in calls))
            total_hist_cost += ea_cost
            suff = assess_source_sufficiency(entry)

            print("\n" + "-" * 105)
            print(f"[{idx}/{len(active_failed)}] ENTRY: {entry.id}")
            print(f"  Título:                  {entry.title}")
            print(f"  Fuente:                  {entry.source.name if entry.source else 'N/A'}")
            print(f"  Caracteres texto:        {len(entry.content or ''):,} | Suficiencia: {suff.level.value}")
            print(f"  Analysis ID (failed):    {ea.id}")
            print(f"  Etapa que falló:         {diag['stage']}")
            print(f"  Tipo de error:           {diag['error_type']}")
            print(f"  Tokens output llamada:   {diag.get('total_out_tokens', 0):,} (pensamiento: {diag.get('thought_tokens', 0):,}, texto: {diag.get('text_tokens', 0):,})")
            print(f"  Coste histórico en BD:   ${ea_cost:.6f} USD")
            print(f"  CLASIFICACIÓN RETRY:     >>> {diag['classification']} <<<")
            print(f"  Diagnóstico/Dictamen:    {diag['recommendation']}")

        print("\n" + "=" * 105)
        print("  RESUMEN DE PLANIFICACIÓN DE REINTENTOS")
        print("=" * 105)
        print(f"Total Entries fallidas:                  {len(active_failed)}")
        print(f"Coste histórico acumulado en BD:         ${total_hist_cost:.6f} USD")
        
        reasonable_cnt = sum(
            1 for ea in active_failed
            if classify_failure(ea, db.query(AnalysisCall).filter(AnalysisCall.entry_analysis_id == ea.id).all())["classification"] == "RETRY_V4_REASONABLE"
        )
        unlikely_cnt = len(active_failed) - reasonable_cnt

        print(f"Candidatas a RETRY_V4_REASONABLE:        {reasonable_cnt} (Gormsen: paráfrasis aislada, no truncada)")
        print(f"Candidatas a V4_RETRY_UNLIKELY:          {unlikely_cnt} (CAT 56 y EWCA 814: truncamiento sistemático por max_output_tokens)")
        print("\nRECOMENDACIÓN TÉCNICA:")
        print("1. NO ejecutar reintentos a ciegas sobre las 2 sentencias truncadas (CAT 56 y EWCA 814) con la config v4 actual.")
        print("2. Para CAT 56 y EWCA 814 se recomienda evaluar ampliar max_output_tokens a 8192 o reducir thinking_level a 'low'.")
        print("3. La entrada de Gormsen puede beneficiarse de un reintento explícito controlado con v4.")
        print("=" * 105)
        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
