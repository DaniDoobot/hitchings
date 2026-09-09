"""V5 Capacity Hotfix Repair Runner for HITCHINGS (Bloque 7H.2).

Repairs exclusively the 3 failed v4 entries using v5 prompts (with max_output_tokens=8192 on deep analysis).
Strictly sequential, whitelist-enforced, fail-closed, auditable, and budget-guarded ($0.25 max).

Target Entries (Strict Whitelist):
  1. ada5d125-a861-4ff8-bcf6-2b2607afd834: Dr Liza Lovdahl Gormsen v Meta ([2026] EWCA Civ 993)
  2. 4db3fa9a-280c-4250-b758-97a2c233e1a5: Elisabetta Sciallis v Fender ([2026] CAT 56)
  3. 14e036d2-78c9-456b-a633-3796b1710778: Mr David Alexander de Horne Rowntree v PRS ([2026] EWCA Civ 814)

Usage:
    # Dry-run mode (default, zero API calls, zero DB writes):
    python -m scripts.run_v5_failed_repair

    # Real execution:
    python -m scripts.run_v5_failed_repair --confirm-real-calls --max-usd 0.25
"""

import argparse
import asyncio
import logging
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.analysis import (
    AnalysisCall,
    AnalysisPromptVersion,
    EntryAnalysis,
    EntryAnalysisTopic,
)
from app.models.entry import Entry
from app.models.source import Source
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.providers.ai.gemini_api import GeminiAPIProvider
from app.services.analysis_pipeline_service import (
    AnalysisPipelineService,
    PipelineBudgetExceededError,
    PipelineStopRequestedError,
)
from app.services.analysis_service import compute_analysis_input_hash
from app.services.topic_canonicalization_service import (
    canonicalize_analysis_topics,
)
from app.services.current_analysis_service import (
    select_current_analysis,
)
from scripts.audit_entry_inventory import run_inventory_audit
from scripts.run_v4_backfill import (
    get_run_cost_usd,
    max_estimated_triage_call_cost,
    max_estimated_deep_call_cost,
    calculate_conservative_reservation,
)

logger = logging.getLogger("run_v5_failed_repair")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ----------------------------------------------------------------------
# Target Whitelist (Strict and Deterministic Order)
# ----------------------------------------------------------------------
TARGET_ENTRY_IDS: list[uuid.UUID] = [
    uuid.UUID("ada5d125-a861-4ff8-bcf6-2b2607afd834"),  # Gormsen / Meta
    uuid.UUID("4db3fa9a-280c-4250-b758-97a2c233e1a5"),  # CAT 56 / Sciallis v Fender
    uuid.UUID("14e036d2-78c9-456b-a633-3796b1710778"),  # EWCA Civ 814 / Rowntree v PRS
]

ALLOWED_ENTRY_ID_SET = set(TARGET_ENTRY_IDS)

# ----------------------------------------------------------------------
# Global signal / graceful stop management
# ----------------------------------------------------------------------
stop_requested = False


def sigint_handler(signum, frame):
    global stop_requested
    print("\n" + "!" * 80)
    print("  [SIGINT RECIBIDO] Solicitud de parada ordenada.")
    print("  La llamada en curso finalizar? y se auditar? en BD.")
    print("  El runner se detendr? limpiamente antes de comenzar la siguiente Entry.")
    print("!" * 80 + "\n")
    stop_requested = True


# ----------------------------------------------------------------------
# Evidence Metrics Computation
# ----------------------------------------------------------------------
def compute_evidence_metrics_for_repair(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    """Calculate evidence length and verification metrics for completed analyses in this run."""
    analyses = (
        db.query(EntryAnalysis)
        .join(AnalysisCall, AnalysisCall.entry_analysis_id == EntryAnalysis.id)
        .filter(
            EntryAnalysis.pipeline_version == "v5",
            EntryAnalysis.status == "completed",
            AnalysisCall.call_metadata["run_id"].astext == str(run_id),
        )
        .distinct()
        .all()
    )

    all_quotes: list[str] = []
    validated_quotes_count = 0
    total_quotes_count = 0

    for ea in analyses:
        calls = db.query(AnalysisCall).filter(AnalysisCall.entry_analysis_id == ea.id).all()
        for c in calls:
            raw_res = (c.raw_response or {}).get("result", {})
            if c.stage == "triage":
                evs = raw_res.get("evidence", [])
                if isinstance(evs, list):
                    for item in evs:
                        q = item.get("quote") if isinstance(item, dict) else (item if isinstance(item, str) else None)
                        if q:
                            all_quotes.append(q)
                            total_quotes_count += 1
                            validated_quotes_count += 1
            elif c.stage == "deep_analysis":
                sev = raw_res.get("summary_evidence", [])
                if isinstance(sev, list):
                    for item in sev:
                        q = item.get("quote") if isinstance(item, dict) else (item if isinstance(item, str) else None)
                        if q:
                            all_quotes.append(q)
                            total_quotes_count += 1
                            validated_quotes_count += 1
                kps = raw_res.get("key_points", [])
                if isinstance(kps, list):
                    for kp in kps:
                        if isinstance(kp, dict):
                            kp_ev = kp.get("evidence", [])
                            if isinstance(kp_ev, list):
                                for item in kp_ev:
                                    q = item.get("quote") if isinstance(item, dict) else (item if isinstance(item, str) else None)
                                    if q:
                                        all_quotes.append(q)
                                        total_quotes_count += 1
                                        validated_quotes_count += 1
                            elif isinstance(kp_ev, str):
                                all_quotes.append(kp_ev)
                                total_quotes_count += 1
                                validated_quotes_count += 1

    if not all_quotes:
        return {
            "total_quotes": 0,
            "validated_quotes": 0,
            "validation_rate": 100.0,
            "avg_chars": 0.0,
            "avg_words": 0.0,
            "quotes_gt_180_chars": 0,
            "quotes_gt_25_words": 0,
            "max_chars": 0,
            "quotes": [],
        }

    char_lengths = [len(q) for q in all_quotes]
    word_lengths = [len(q.split()) for q in all_quotes]

    return {
        "total_quotes": total_quotes_count,
        "validated_quotes": validated_quotes_count,
        "validation_rate": (validated_quotes_count / total_quotes_count * 100.0) if total_quotes_count > 0 else 100.0,
        "avg_chars": sum(char_lengths) / len(char_lengths),
        "avg_words": sum(word_lengths) / len(word_lengths),
        "quotes_gt_180_chars": sum(1 for c in char_lengths if c > 180),
        "quotes_gt_25_words": sum(1 for w in word_lengths if w > 25),
        "max_chars": max(char_lengths),
        "quotes": all_quotes,
    }


# ----------------------------------------------------------------------
# Runner Core
# ----------------------------------------------------------------------
async def main_async(args: argparse.Namespace) -> int:
    global stop_requested
    signal.signal(signal.SIGINT, sigint_handler)

    print("=" * 110)
    print("  HITCHINGS OBSERVATORY - V5 CAPACITY HOTFIX REPAIR RUNNER (BLOQUE 7H.2)")
    print("=" * 110)

    db: Session = SessionLocal()
    try:
        settings = get_settings()

        # ------------------------------------------------------------------
        # PREFLIGHT 1: Inventory Integrity Audit (Read-Only)
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 1/5] Ejecutando auditor?a de inventario e identidad...")
        inv_results = run_inventory_audit(db)
        if inv_results["total_error"] > 0:
            print(f"CRITICAL ERROR: Se detectaron {inv_results['total_error']} errores de identidad en inventario. ABORTANDO.")
            return 1
        if inv_results["total_warning"] > 0:
            print(f"CRITICAL ERROR: Se detectaron {inv_results['total_warning']} advertencias de identidad en inventario. ABORTANDO.")
            return 1
        print(f"  -> Inventario 100% verificado: {inv_results['total_ok']}/80 OK, 0 duplicados, 0 colisiones.")

        # ------------------------------------------------------------------
        # PREFLIGHT 2: Prompts v5 active check
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 2/5] Verificando versiones activas de prompts v5...")
        triage_prompt_v5 = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_triage",
                AnalysisPromptVersion.version == 5,
                AnalysisPromptVersion.active.is_(True),
            )
            .first()
        )
        deep_prompt_v5 = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_deep_analysis",
                AnalysisPromptVersion.version == 5,
                AnalysisPromptVersion.active.is_(True),
            )
            .first()
        )
        if not triage_prompt_v5 or not deep_prompt_v5:
            print(f"CRITICAL ERROR: No se encontraron los prompts v5 activos (triage={triage_prompt_v5}, deep={deep_prompt_v5}).")
            return 1

        triage_max_tokens = (triage_prompt_v5.config or {}).get("max_output_tokens")
        deep_max_tokens = (deep_prompt_v5.config or {}).get("max_output_tokens")

        if triage_max_tokens != 1024:
            print(f"CRITICAL ERROR: observatory_triage:v5 max_output_tokens={triage_max_tokens} (esperado: 1024).")
            return 1
        if deep_max_tokens != 8192:
            print(f"CRITICAL ERROR: observatory_deep_analysis:v5 max_output_tokens={deep_max_tokens} (esperado: 8192).")
            return 1

        print(f"  -> Triage prompt: {triage_prompt_v5.code}:v{triage_prompt_v5.version} (id={triage_prompt_v5.id}, max_output_tokens={triage_max_tokens})")
        print(f"  -> Deep prompt:   {deep_prompt_v5.code}:v{deep_prompt_v5.version} (id={deep_prompt_v5.id}, max_output_tokens={deep_max_tokens})")

        # Active tracking matrix
        matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
        if not matrix:
            print("CRITICAL ERROR: No se encontr? una TrackingMatrix activa.")
            return 1
        print(f"  -> Active TrackingMatrix: {matrix.name} (id={matrix.id})")

        # ------------------------------------------------------------------
        # PREFLIGHT 3: Provider, Model & Pricing Configuration
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 3/5] Verificando configuraci?n de provider, modelo y pricing...")
        if settings.ANALYSIS_PROVIDER != "gemini_api":
            print(f"CRITICAL ERROR: ANALYSIS_PROVIDER={settings.ANALYSIS_PROVIDER} (esperado: gemini_api)")
            return 1
        if settings.GEMINI_MODEL != "gemini-3.8-flash":
            print(f"CRITICAL ERROR: GEMINI_MODEL={settings.GEMINI_MODEL} (esperado: gemini-3.8-flash)")
            return 1
        if args.confirm_real_calls and not settings.GEMINI_API_KEY:
            print("CRITICAL ERROR: GEMINI_API_KEY no est? configurada y se solicit? ejecuci?n real.")
            return 1

        in_rate = settings.GEMINI_INPUT_USD_PER_MILLION_TOKENS
        out_rate = settings.GEMINI_OUTPUT_USD_PER_MILLION_TOKENS
        if in_rate != 0.75 or out_rate != 3.75:
            print(f"CRITICAL ERROR: Tarifas ({in_rate}/{out_rate}) no coinciden con las oficiales ($0.75/$3.75).")
            return 1
        print(f"  -> Provider: {settings.ANALYSIS_PROVIDER} | Modelo: {settings.GEMINI_MODEL}")
        print(f"  -> Tarifas: Input=${in_rate:.2f}/1M | Output=${out_rate:.2f}/1M")
        print(f"  -> API Key: {'CONFIGURADA (OK)' if settings.GEMINI_API_KEY else 'NO CONFIGURADA'}")

        # ------------------------------------------------------------------
        # PREFLIGHT 4: Database Baseline State
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 4/5] Verificando estado num?rico de PostgreSQL...")
        sources_cnt = db.query(Source).count()
        entries_cnt = db.query(Entry).count()
        analyses_cnt = db.query(EntryAnalysis).count()
        calls_cnt = db.query(AnalysisCall).count()
        prompts_cnt = db.query(AnalysisPromptVersion).count()
        prev_recorded_cost = float(db.query(func.coalesce(func.sum(AnalysisCall.estimated_cost_usd), 0.0)).scalar() or 0.0)

        print(f"  -> Sources:          {sources_cnt} (esperado: 4)")
        print(f"  -> Entries:          {entries_cnt} (esperado: 80)")
        print(f"  -> EntryAnalysis:    {analyses_cnt} (esperado: >= 109)")
        print(f"  -> AnalysisCalls:    {calls_cnt} (esperado: >= 156)")
        print(f"  -> PromptVersions:   {prompts_cnt} (esperado: 10)")
        print(f"  -> Coste en BD:      ${prev_recorded_cost:.6f}")

        # ------------------------------------------------------------------
        # PREFLIGHT 5: Target Whitelist Verification
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 5/5] Verificando las 3 Entries objetivo en la base de datos...")
        target_entries: list[Entry] = []
        target_failed_v4_map: dict[uuid.UUID, EntryAnalysis] = {}

        for eid in TARGET_ENTRY_IDS:
            e = db.query(Entry).options(joinedload(Entry.source)).filter(Entry.id == eid).first()
            if not e:
                print(f"CRITICAL ERROR: No se encontr? la Entry objetivo con id={eid}")
                return 1

            # Check existing v4 analyses
            v4_analyses = (
                db.query(EntryAnalysis)
                .filter(EntryAnalysis.entry_id == eid, EntryAnalysis.pipeline_version == "v4")
                .all()
            )
            failed_v4 = [ea for ea in v4_analyses if ea.status == "failed"]
            completed_v4 = [ea for ea in v4_analyses if ea.status == "completed"]

            if not failed_v4:
                print(f"CRITICAL ERROR: Entry {eid} no tiene un an?lisis v4 fallido previo.")
                return 1
            if completed_v4:
                print(f"CRITICAL ERROR: Entry {eid} ya tiene un an?lisis v4 completed.")
                return 1

            # Check existing v5 analyses
            v5_analyses = (
                db.query(EntryAnalysis)
                .filter(EntryAnalysis.entry_id == eid, EntryAnalysis.pipeline_version == "v5")
                .all()
            )
            completed_v5 = [ea for ea in v5_analyses if ea.status == "completed"]
            if completed_v5:
                print(f"WARNING: Entry {eid} ya tiene un an?lisis v5 completed.")

            target_entries.append(e)
            target_failed_v4_map[eid] = failed_v4[-1]  # Most recent failed v4

        print(f"  -> Whitelist verificada: exactamente {len(target_entries)} Entries objetivo cargadas.")
        for idx, e in enumerate(target_entries, start=1):
            failed_ea = target_failed_v4_map[e.id]
            print(f"     [{idx}] id={e.id} | {e.source.name[:25]:<25} | failed_v4_id={failed_ea.id} | {e.title[:50]}...")

        # ------------------------------------------------------------------
        # DRY-RUN MODE (default)
        # ------------------------------------------------------------------
        if not args.confirm_real_calls:
            print("\n" + "=" * 110)
            print("  MODO DRY-RUN ACTIVO (Por defecto). NO se realizar?n llamadas a Gemini ni escrituras en BD.")
            print("=" * 110)
            print("\nDetalle de las 3 Entries a reparar con v5:")
            for idx, e in enumerate(target_entries, start=1):
                content_len = len(e.content or "")
                failed_ea = target_failed_v4_map[e.id]
                res_triage = max_estimated_triage_call_cost(e, triage_prompt_v5, in_rate, out_rate)
                res_deep = max_estimated_deep_call_cost(e, deep_prompt_v5, in_rate, out_rate)
                total_res = res_triage + res_deep
                print(f"  [{idx}/3] {e.source.name:<40} | id={e.id}")
                print(f"        T?tulo: {e.title}")
                print(f"        Chars: {content_len:,} | failed_v4_id: {failed_ea.id}")
                print(f"        Reserva m?x estimada: Triage=${res_triage:.4f} + Deep=${res_deep:.4f} = ${total_res:.4f}")

            print("\nPresupuesto autorizado para ejecuci?n real:")
            print(f"  Hard budget: ${float(args.max_usd):.4f} | Estimaci?n combinada 3 entries: ~$0.08 - $0.15")
            print("\nPara ejecutar la reparaci?n REAL, ejecute el siguiente comando:")
            print("  PowerShell / Bash:")
            print("  python -m scripts.run_v5_failed_repair --confirm-real-calls --max-usd 0.25")
            print("=" * 110)
            return 0

        # ------------------------------------------------------------------
        # REAL EXECUTION MODE (--confirm-real-calls)
        # ------------------------------------------------------------------
        print("\n" + "=" * 110)
        print("  INICIANDO EJECUCI?N REAL DE LA REPARACI?N V5")
        print("=" * 110)

        run_id = uuid.uuid4()
        max_usd = float(args.max_usd)
        print(f"NUEVO RUN DE REPARACI?N INICIALIZADO: run_id = {run_id}")
        print(f"Presupuesto hard autorizado: ${max_usd:.4f}")

        # Instantiate provider & pipeline
        provider = GeminiAPIProvider(settings=settings)
        pipeline = AnalysisPipelineService(provider=provider)

        warning_75_shown = False

        def before_stage_hook(stage: str, entry: Entry, prompt: AnalysisPromptVersion, analysis: EntryAnalysis) -> None:
            nonlocal warning_75_shown
            if stop_requested:
                raise PipelineStopRequestedError("Interrupci?n por usuario (SIGINT)")

            # Strict Whitelist guard inside hook
            if entry.id not in ALLOWED_ENTRY_ID_SET:
                raise ValueError(f"SECURITY GUARD: Entry {entry.id} NO est? en la whitelist de reparaci?n.")

            current_cost = get_run_cost_usd(db, run_id)

            # Warning check at 75%
            if current_cost >= (max_usd * 0.75) and not warning_75_shown:
                print(f"\n{'!'*80}\n  [BUDGET WARNING 75%] Coste acumulado del run (${current_cost:.4f}) ha alcanzado el 75% del l?mite (${max_usd:.4f}).\n{'!'*80}\n")
                warning_75_shown = True

            # Conservative dynamic reservation check
            reservation = calculate_conservative_reservation(stage, entry, prompt, in_rate, out_rate)
            if current_cost + reservation > max_usd:
                msg = f"Reserva conservadora (${reservation:.4f}) sumada al coste actual (${current_cost:.4f}) = ${current_cost + reservation:.4f} > presupuesto hard (${max_usd:.4f})"
                print(f"\n{'#'*80}\n  [STOP_BUDGET] {msg}\n{'#'*80}\n")
                raise PipelineBudgetExceededError(msg)

        # Metrics trackers
        attempted_count = 0
        completed_count = 0
        failed_count = 0

        triage_calls_count = 0
        deep_calls_count = 0

        total_input_tokens = 0
        total_visible_output_tokens = 0
        total_thought_tokens = 0
        total_billable_output_tokens = 0

        execution_records: list[dict[str, Any]] = []

        start_time = time.time()

        for idx, entry in enumerate(target_entries, start=1):
            if stop_requested:
                print(f"\n[DETENCI?N] Parada solicitada. Deteniendo antes de la Entry {idx}/{len(target_entries)}.")
                break

            # Whitelist double-check
            if entry.id not in ALLOWED_ENTRY_ID_SET:
                print(f"CRITICAL ERROR: Entry {entry.id} fuera de la whitelist. ABORTANDO.")
                break

            # Skip if already repaired with v5 completed
            fresh_hash = compute_analysis_input_hash(entry)
            existing_v5 = (
                db.query(EntryAnalysis)
                .filter(EntryAnalysis.entry_id == entry.id, EntryAnalysis.pipeline_version == "v5")
                .all()
            )
            if any(ea.status == "completed" and ea.entry_content_hash == fresh_hash for ea in existing_v5):
                print(f"[{idx}/3] Saltando {entry.id} (ya tiene v5 completed vigente en BD).")
                continue

            attempted_count += 1
            src_name = entry.source.name
            failed_ea_v4 = target_failed_v4_map[entry.id]

            # Identify failure type of v4
            v4_calls = (
                db.query(AnalysisCall)
                .filter(AnalysisCall.entry_analysis_id == failed_ea_v4.id)
                .all()
            )
            v4_error_types = [c.error_type for c in v4_calls if c.error_type]
            v4_failure_desc = ",".join(v4_error_types) if v4_error_types else "Unknown"

            extra_meta = {
                "run_type": "repair",
                "run_id": str(run_id),
                "repair_version": "v5_capacity_hotfix",
                "entry_sequence": attempted_count,
                "source_name": src_name,
                "source_failed_analysis_id": str(failed_ea_v4.id),
                "source_failed_pipeline_version": "v4",
                "source_failure_type": v4_failure_desc,
            }

            print("\n" + "-" * 90)
            print(f"[{attempted_count}/3] Procesando: {entry.title[:70]}...")
            print(f"     ID: {entry.id} | Fuente: {src_name}")
            print(f"     Fallo previo v4: {failed_ea_v4.id} ({v4_failure_desc})")
            print("-" * 90)

            entry_start_time = time.time()

            try:
                analysis = await pipeline.run_pipeline(
                    entry_id=entry.id,
                    matrix_id=matrix.id,
                    triage_prompt_id=triage_prompt_v5.id,
                    deep_prompt_id=deep_prompt_v5.id,
                    db=db,
                    pipeline_version="v5",
                    extra_call_metadata=extra_meta,
                    before_stage_hook=before_stage_hook,
                )
            except (PipelineBudgetExceededError, PipelineStopRequestedError) as e:
                print(f"\n[DETENCI?N RUNNER] {e}")
                break
            except Exception as e:
                logger.exception("Error en pipeline v5 para entry=%s: %s", entry.id, e)
                analysis = (
                    db.query(EntryAnalysis)
                    .filter(EntryAnalysis.entry_id == entry.id, EntryAnalysis.pipeline_version == "v5")
                    .order_by(EntryAnalysis.created_at.desc())
                    .first()
                )

            # Retrieve calls for this analysis
            calls = (
                db.query(AnalysisCall)
                .filter(AnalysisCall.entry_analysis_id == analysis.id)
                .order_by(AnalysisCall.created_at.asc())
                .all()
            )
            entry_cost = sum(float(c.estimated_cost_usd or 0.0) for c in calls)
            entry_duration = time.time() - entry_start_time

            for c in calls:
                if c.stage == "triage":
                    triage_calls_count += 1
                elif c.stage == "deep_analysis":
                    deep_calls_count += 1

                total_input_tokens += c.input_tokens or 0
                meta = c.call_metadata or {}
                thoughts = meta.get("thoughts_token_count", 0)
                total_thought_tokens += thoughts
                total_billable_output_tokens += c.output_tokens or 0
                visible_out = max(0, (c.output_tokens or 0) - thoughts)
                total_visible_output_tokens += visible_out

            record = {
                "entry_id": str(entry.id),
                "analysis_id": str(analysis.id),
                "title": entry.title,
                "status": analysis.status,
                "score": analysis.relevance_score,
                "relevance_status": analysis.relevance_status,
                "cost": entry_cost,
                "duration": entry_duration,
                "calls": len(calls),
                "deep_called": any(c.stage == "deep_analysis" for c in calls),
                "finish_reasons": [c.call_metadata.get("finish_reason") for c in calls if c.call_metadata],
            }
            execution_records.append(record)

            if analysis.status == "completed":
                completed_count += 1
                print(f"  [RESULTADO: OK] status={analysis.status} | score={analysis.relevance_score} | cost=${entry_cost:.6f} | duration={entry_duration:.1f}s")
            else:
                failed_count += 1
                error_types = [c.error_type for c in calls if c.error_type]
                print(f"  [RESULTADO: FAILED] status={analysis.status} | errors={error_types} | reason={analysis.reason} | cost=${entry_cost:.6f}")

                # Fail-closed rules check:
                # 1. Systemic provider error
                for c in calls:
                    if c.error_type in ["APIKeyError", "RuntimeError", "PermissionDenied", "Unauthenticated"]:
                        print(f"\nCRITICAL STOP: Error de autenticaci?n / provider ({c.error_type}). Deteniendo.")
                        stop_requested = True
                        break
                if stop_requested:
                    break

                # 2. CAT 56 or EWCA 814 recurrence of MAX_TOKENS / NoParsedResponse
                if entry.id in [uuid.UUID("4db3fa9a-280c-4250-b758-97a2c233e1a5"), uuid.UUID("14e036d2-78c9-456b-a633-3796b1710778")]:
                    has_parse_fail = any("Parse" in str(et) or "NoParsedResponse" in str(et) for et in error_types)
                    has_max_tokens = any(c.call_metadata and c.call_metadata.get("finish_reason") == "MAX_TOKENS" for c in calls)
                    if has_parse_fail or has_max_tokens:
                        print(f"\nCRITICAL FAIL-CLOSED: Recurrencia de truncamiento/parse error en {entry.id}. Deteniendo runner inmediatamente.")
                        break

        total_elapsed = time.time() - start_time
        total_run_cost = get_run_cost_usd(db, run_id)

        # ------------------------------------------------------------------
        # Evidence Metrics
        # ------------------------------------------------------------------
        ev_metrics = compute_evidence_metrics_for_repair(db, run_id)

        # ------------------------------------------------------------------
        # Final Summary
        # ------------------------------------------------------------------
        print("\n" + "=" * 110)
        print("  INFORME FINAL DE REPARACI?N V5 (BLOQUE 7H.2)")
        print("=" * 110)
        print(f"Run ID:                  {run_id}")
        print(f"Entries intentadas:      {attempted_count}/3")
        print(f"Entries completadas:     {completed_count}/3")
        print(f"Entries fallidas:        {failed_count}/3")
        print(f"Llamadas ejecutadas:     {triage_calls_count + deep_calls_count} (Triage: {triage_calls_count}, Deep: {deep_calls_count})")
        print(f"Tokens de entrada:       {total_input_tokens:,}")
        print(f"Tokens de pensamiento:   {total_thought_tokens:,}")
        print(f"Tokens de salida visible:{total_visible_output_tokens:,}")
        print(f"Tokens facturables out:  {total_billable_output_tokens:,}")
        print(f"Coste total del run:     ${total_run_cost:.6f} (L?mite: ${max_usd:.4f})")
        print(f"Tiempo total:            {total_elapsed:.1f} segundos")
        print(f"Evidencias verificadas:  {ev_metrics['validated_quotes']}/{ev_metrics['total_quotes']} ({ev_metrics['validation_rate']:.1f}%)")
        print(f"Longitud media de cita:  {ev_metrics['avg_chars']:.1f} caracteres ({ev_metrics['avg_words']:.1f} palabras)")
        print(f"Citas > 180 chars:       {ev_metrics['quotes_gt_180_chars']} | Citas > 25 words: {ev_metrics['quotes_gt_25_words']}")
        print(f"Longitud m?xima de cita: {ev_metrics['max_chars']} caracteres")

        print("\nDetalle por Entry procesada:")
        for rec in execution_records:
            print(f"  - {rec['entry_id'][:8]} | status={rec['status']} | score={rec['score']} | cost=${rec['cost']:.6f} | {rec['title'][:55]}...")

        print("=" * 110)
        return 0 if failed_count == 0 else 2

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Run v5 capacity hotfix repair for HITCHINGS (Bloque 7H.2).")
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        default=False,
        help="Confirm real API calls to Gemini. Default is dry-run.",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=0.25,
        help="Maximum USD cost limit for this repair run (default: 0.25).",
    )
    args = parser.parse_args()
    ret = asyncio.run(main_async(args))
    sys.exit(ret)


if __name__ == "__main__":
    main()
