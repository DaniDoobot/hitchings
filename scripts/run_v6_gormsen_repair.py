"""V6 Contiguous-Evidence Hotfix Runner for HITCHINGS (Bloque 7H.3).

Repairs exclusively the Gormsen v Meta entry (ada5d125-a861-4ff8-bcf6-2b2607afd834)
using v6 prompts with the generic contiguous-evidence rule to avoid cross-page artefacts.
Strictly single-entry whitelist, non-resumable, no auto-retry, fail-closed, and budget-guarded ($0.12 max).

Usage:
    # Dry-run mode (default, zero API calls, zero DB writes):
    python -m scripts.run_v6_gormsen_repair

    # Real execution:
    python -m scripts.run_v6_gormsen_repair --confirm-real-calls --max-usd 0.12
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
from app.services.current_analysis_service import select_current_analysis
from app.services.source_sufficiency_service import assess_source_sufficiency
from scripts.audit_entry_inventory import run_inventory_audit
from scripts.run_v4_backfill import (
    get_run_cost_usd,
    max_estimated_triage_call_cost,
    max_estimated_deep_call_cost,
    calculate_conservative_reservation,
)

logger = logging.getLogger("run_v6_gormsen_repair")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ----------------------------------------------------------------------
# Target Whitelist: STRICTLY GORMSEN ONLY
# ----------------------------------------------------------------------
GORMSEN_ENTRY_ID = uuid.UUID("ada5d125-a861-4ff8-bcf6-2b2607afd834")
ALLOWED_ENTRY_IDS = {GORMSEN_ENTRY_ID}

# Global signal management
stop_requested = False


def sigint_handler(signum, frame):
    global stop_requested
    print("\n" + "!" * 80)
    print("  [SIGINT RECIBIDO] Solicitud de parada ordenada.")
    print("!" * 80 + "\n")
    stop_requested = True


async def main_async(args: argparse.Namespace) -> int:
    global stop_requested
    signal.signal(signal.SIGINT, sigint_handler)

    print("=" * 110)
    print("  HITCHINGS OBSERVATORY - V6 CONTIGUOUS-EVIDENCE GORMSEN REPAIR RUNNER (BLOQUE 7H.3)")
    print("=" * 110)

    db: Session = SessionLocal()
    try:
        settings = get_settings()

        # ------------------------------------------------------------------
        # PREFLIGHT 1: Inventory Integrity Audit (Read-Only)
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 1/5] Ejecutando auditor?a de inventario e identidad...")
        inv_results = run_inventory_audit(db)
        if inv_results["total_error"] > 0 or inv_results["total_warning"] > 0:
            print(f"CRITICAL ERROR: Inventario no limpio ({inv_results['total_error']} errores, {inv_results['total_warning']} warnings). ABORTANDO.")
            return 1
        print(f"  -> Inventario 100% verificado: {inv_results['total_ok']}/80 OK, 0 duplicados, 0 colisiones.")

        # ------------------------------------------------------------------
        # PREFLIGHT 2: Prompts v6 active check
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 2/5] Verificando versiones activas de prompts v6...")
        triage_prompt_v6 = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_triage",
                AnalysisPromptVersion.version == 6,
                AnalysisPromptVersion.active.is_(True),
            )
            .first()
        )
        deep_prompt_v6 = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_deep_analysis",
                AnalysisPromptVersion.version == 6,
                AnalysisPromptVersion.active.is_(True),
            )
            .first()
        )
        if not triage_prompt_v6 or not deep_prompt_v6:
            print(f"CRITICAL ERROR: No se encontraron los prompts v6 activos (triage={triage_prompt_v6}, deep={deep_prompt_v6}).")
            return 1

        triage_max_tokens = (triage_prompt_v6.config or {}).get("max_output_tokens")
        deep_max_tokens = (deep_prompt_v6.config or {}).get("max_output_tokens")

        if triage_max_tokens != 1024:
            print(f"CRITICAL ERROR: observatory_triage:v6 max_output_tokens={triage_max_tokens} (esperado: 1024).")
            return 1
        if deep_max_tokens != 8192:
            print(f"CRITICAL ERROR: observatory_deep_analysis:v6 max_output_tokens={deep_max_tokens} (esperado: 8192).")
            return 1

        # Check contiguous evidence instruction in deep prompt
        if "SALTO DE ARTEFACTOS O ENCABEZADOS" not in deep_prompt_v6.system_prompt:
            print("CRITICAL ERROR: Prompt deep v6 no contiene la regla de span continuo / salto de p?gina.")
            return 1

        print(f"  -> Triage prompt: {triage_prompt_v6.code}:v{triage_prompt_v6.version} (id={triage_prompt_v6.id}, max_output_tokens={triage_max_tokens})")
        print(f"  -> Deep prompt:   {deep_prompt_v6.code}:v{deep_prompt_v6.version} (id={deep_prompt_v6.id}, max_output_tokens={deep_max_tokens})")

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
        print(f"  -> EntryAnalysis:    {analyses_cnt} (esperado: 112)")
        print(f"  -> AnalysisCalls:    {calls_cnt} (esperado: 162)")
        print(f"  -> PromptVersions:   {prompts_cnt} (esperado: 12)")
        print(f"  -> Coste en BD:      ${prev_recorded_cost:.6f} (esperado: $1.306138)")

        # ------------------------------------------------------------------
        # PREFLIGHT 5: Detailed Forensics of Gormsen Entry
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 5/5] Auditor?a forense exhaustiva de la Entry objetivo (Gormsen)...")
        gormsen = db.query(Entry).options(joinedload(Entry.source)).filter(Entry.id == GORMSEN_ENTRY_ID).first()
        if not gormsen:
            print(f"CRITICAL ERROR: No se encontr? la Entry {GORMSEN_ENTRY_ID} en BD.")
            return 1

        content_chars = len(gormsen.content or "")
        content_hash = compute_analysis_input_hash(gormsen)
        suff_res = assess_source_sufficiency(gormsen)

        # Check existing analyses for Gormsen
        existing_analyses = (
            db.query(EntryAnalysis)
            .filter(EntryAnalysis.entry_id == GORMSEN_ENTRY_ID)
            .order_by(EntryAnalysis.created_at.asc())
            .all()
        )

        v4_failed = next((ea for ea in existing_analyses if ea.pipeline_version == "v4" and ea.status == "failed"), None)
        v5_failed = next((ea for ea in existing_analyses if ea.pipeline_version == "v5" and ea.status == "failed"), None)
        v6_existing = [ea for ea in existing_analyses if ea.pipeline_version == "v6"]

        if not v4_failed:
            print("CRITICAL ERROR: Gormsen no tiene un an?lisis v4 failed registrado.")
            return 1
        if not v5_failed:
            print("CRITICAL ERROR: Gormsen no tiene un an?lisis v5 failed registrado.")
            return 1
        if v6_existing:
            print(f"WARNING: Ya existe(n) {len(v6_existing)} an?lisis v6 para Gormsen.")

        # Retrieve failed call of v5
        v5_failed_call = (
            db.query(AnalysisCall)
            .filter(AnalysisCall.entry_analysis_id == v5_failed.id, AnalysisCall.status == "failed")
            .first()
        )
        v5_failed_quote = (v5_failed_call.error_message if v5_failed_call else v5_failed.reason) or "N/A"

        current_ea = select_current_analysis(gormsen, db=db)
        print(f"  -> Entry ID:             {gormsen.id}")
        print(f"  -> T?tulo:               {gormsen.title}")
        print(f"  -> Fuente:               {gormsen.source.name}")
        print(f"  -> Content Chars:        {content_chars:,}")
        print(f"  -> Source Sufficiency:   {suff_res.level.value} ({suff_res.reason})")
        print(f"  -> Content Hash:         {content_hash}")
        print(f"  -> V4 Failed Analysis:   id={v4_failed.id} | status={v4_failed.status} | error={v4_failed.reason[:80]}...")
        print(f"  -> V5 Failed Analysis:   id={v5_failed.id} | status={v5_failed.status} | error={v5_failed.reason[:80]}...")
        print(f"     Cita fallida en v5:   {v5_failed_quote[:110]}...")
        print(f"  -> Current Analysis:     {current_ea.id if current_ea else 'None (CORRECTO: sin an?lisis vigente)'}")

        if current_ea is not None:
            if args.confirm_real_calls:
                print("CRITICAL ERROR: Gormsen ya tiene un análisis vigente. No requiere reparación.")
                return 1
            else:
                print(f"  -> Aviso dry-run: Gormsen ya tiene un análisis vigente (id={current_ea.id}, versión={current_ea.pipeline_version}).")

        # Calculate dynamic reservations
        res_triage = max_estimated_triage_call_cost(gormsen, triage_prompt_v6, in_rate, out_rate)
        res_deep = max_estimated_deep_call_cost(gormsen, deep_prompt_v6, in_rate, out_rate)
        total_res = res_triage + res_deep
        max_usd = float(args.max_usd)

        print(f"\n  -> Reserva conservadora: Triage=${res_triage:.4f} + Deep=${res_deep:.4f} = ${total_res:.4f} (L?mite: ${max_usd:.4f})")

        # ------------------------------------------------------------------
        # DRY-RUN MODE (default)
        # ------------------------------------------------------------------
        if not args.confirm_real_calls:
            print("\n" + "=" * 110)
            print("  MODO DRY-RUN ACTIVO (Por defecto). NO se realizar?n llamadas a Gemini ni escrituras en BD.")
            print("=" * 110)
            print(f"Real calls:            NO")
            print(f"Provider:              {settings.ANALYSIS_PROVIDER}")
            print(f"Modelo:                {settings.GEMINI_MODEL}")
            print(f"Tarifas:               Input=${in_rate:.2f}/1M | Output=${out_rate:.2f}/1M")
            print(f"Prompts v6:            triage v6 (id={triage_prompt_v6.id}), deep v6 (id={deep_prompt_v6.id})")
            print(f"Entry:                 {gormsen.id} | {gormsen.title[:60]}...")
            print(f"Source Sufficiency:    {suff_res.level.value}")
            print(f"Presupuesto hard:      ${max_usd:.4f}")
            print(f"Reserva m?x Triage:    ${res_triage:.4f}")
            print(f"Reserva m?x Deep:      ${res_deep:.4f}")
            print(f"Reserva Total:         ${total_res:.4f}")
            print("\nPara ejecutar la reparaci?n REAL de Gormsen, ejecute:")
            print("  python -m scripts.run_v6_gormsen_repair --confirm-real-calls --max-usd 0.12")
            print("=" * 110)
            return 0

        # ------------------------------------------------------------------
        # REAL EXECUTION MODE (--confirm-real-calls)
        # ------------------------------------------------------------------
        print("\n" + "=" * 110)
        print("  INICIANDO EJECUCI?N REAL DE LA REPARACI?N V6 DE GORMSEN")
        print("=" * 110)

        run_id = uuid.uuid4()
        print(f"NUEVO RUN DE REPARACI?N INICIALIZADO: run_id = {run_id}")
        print(f"Presupuesto hard autorizado: ${max_usd:.4f}")

        provider = GeminiAPIProvider(settings=settings)
        pipeline = AnalysisPipelineService(provider=provider)

        def before_stage_hook(stage: str, entry: Entry, prompt: AnalysisPromptVersion, analysis: EntryAnalysis) -> None:
            if stop_requested:
                raise PipelineStopRequestedError("Interrupci?n por usuario (SIGINT)")

            # Whitelist guard
            if entry.id != GORMSEN_ENTRY_ID:
                raise ValueError(f"SECURITY GUARD: Entry {entry.id} NO permitida en este runner.")

            current_cost = get_run_cost_usd(db, run_id)
            reservation = calculate_conservative_reservation(stage, entry, prompt, in_rate, out_rate)
            if current_cost + reservation > max_usd:
                msg = f"Reserva conservadora (${reservation:.4f}) + coste actual (${current_cost:.4f}) = ${current_cost + reservation:.4f} > l?mite (${max_usd:.4f})"
                print(f"\n{'#'*80}\n  [STOP_BUDGET] {msg}\n{'#'*80}\n")
                raise PipelineBudgetExceededError(msg)

        extra_meta = {
            "run_type": "repair",
            "run_id": str(run_id),
            "repair_version": "v6_contiguous_evidence",
            "entry_sequence": 1,
            "source_name": gormsen.source.name,
            "source_failed_analysis_id": str(v5_failed.id),
            "source_failed_pipeline_version": "v5",
            "source_failure_type": "AnalysisGroundingError",
        }

        start_time = time.time()
        try:
            analysis = await pipeline.run_pipeline(
                entry_id=gormsen.id,
                matrix_id=matrix.id,
                triage_prompt_id=triage_prompt_v6.id,
                deep_prompt_id=deep_prompt_v6.id,
                db=db,
                pipeline_version="v6",
                extra_call_metadata=extra_meta,
                before_stage_hook=before_stage_hook,
            )
        except Exception as e:
            logger.exception("Error en pipeline v6 para Gormsen: %s", e)
            analysis = (
                db.query(EntryAnalysis)
                .filter(EntryAnalysis.entry_id == gormsen.id, EntryAnalysis.pipeline_version == "v6")
                .order_by(EntryAnalysis.created_at.desc())
                .first()
            )

        duration = time.time() - start_time

        # Retrieve calls for this analysis
        calls = (
            db.query(AnalysisCall)
            .filter(AnalysisCall.entry_analysis_id == analysis.id)
            .order_by(AnalysisCall.created_at.asc())
            .all()
        )
        total_cost = sum(float(c.estimated_cost_usd or 0.0) for c in calls)

        triage_call = next((c for c in calls if c.stage == "triage"), None)
        deep_call = next((c for c in calls if c.stage == "deep_analysis"), None)

        print("\n" + "=" * 110)
        print("  INFORME DE EJECUCI?N V6: GORMSEN V META")
        print("=" * 110)
        print(f"Run ID:                  {run_id}")
        print(f"Estado Final An?lisis:   {analysis.status.upper()}")
        print(f"Relevance Score:         {analysis.relevance_score}")
        print(f"Relevance Status:        {analysis.relevance_status}")
        print(f"Coste Total Run:         ${total_cost:.6f} USD")
        print(f"Duraci?n:                {duration:.1f} segundos")

        if triage_call:
            triage_res = (triage_call.raw_response or {}).get("result", {})
            triage_evs = triage_res.get("evidence", [])
            print(f"\n--- DETALLE TRIAGE (Call ID: {triage_call.id}) ---")
            print(f"  Status:                {triage_call.status}")
            print(f"  Tokens:                in={triage_call.input_tokens}, out={triage_call.output_tokens}")
            print(f"  Coste:                 ${float(triage_call.estimated_cost_usd or 0):.6f} USD")
            print(f"  Evidencias extra?das:  {len(triage_evs)}")
            for idx, ev in enumerate(triage_evs, start=1):
                q = ev.get("quote") if isinstance(ev, dict) else ev
                print(f"    [{idx}] ({len(q)} chars, {len(q.split())} words) {repr(q[:80])}")

        if deep_call:
            deep_res = (deep_call.raw_response or {}).get("result", {})
            deep_meta = deep_call.call_metadata or {}
            thought_toks = deep_meta.get("thoughts_token_count", 0)
            billable_out = deep_call.output_tokens or 0
            visible_out = max(0, billable_out - thought_toks)
            finish_reason = deep_meta.get("finish_reason", "STOP")

            summary_evs = deep_res.get("summary_evidence", [])
            kps = deep_res.get("key_points", [])

            print(f"\n--- DETALLE DEEP ANALYSIS (Call ID: {deep_call.id}) ---")
            print(f"  Status:                {deep_call.status}")
            print(f"  Finish Reason:         {finish_reason}")
            print(f"  Tokens Entrada:        {deep_call.input_tokens}")
            print(f"  Tokens Pensamiento:    {thought_toks}")
            print(f"  Tokens Salida Visible: {visible_out}")
            print(f"  Tokens Facturables:    {billable_out}")
            print(f"  Coste:                 ${float(deep_call.estimated_cost_usd or 0):.6f} USD")
            if deep_call.error_type:
                print(f"  Error:                 {deep_call.error_type}: {deep_call.error_message}")
            print(f"  Summary:               {analysis.summary[:200] if analysis.summary else 'N/A'}...")
            print(f"  Summary Evidences:     {len(summary_evs)}")
            for idx, ev in enumerate(summary_evs, start=1):
                q = ev.get("quote") if isinstance(ev, dict) else ev
                print(f"    [{idx}] ({len(q)} chars, {len(q.split())} words) {repr(q[:80])}")
            print(f"  Key Points:            {len(kps)}")
            for idx, kp in enumerate(kps, start=1):
                pt = kp.get("point", "")
                kp_ev = kp.get("evidence", [])
                print(f"    Punto {idx}: {pt[:80]}...")
                for e in kp_ev:
                    q = e.get("quote") if isinstance(e, dict) else e
                    print(f"      Ev: ({len(q)} chars, {len(q.split())} words) {repr(q[:80])}")

        print("=" * 110)
        return 0 if analysis.status == "completed" else 2

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Run v6 contiguous-evidence Gormsen repair for HITCHINGS (Bloque 7H.3).")
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        default=False,
        help="Confirm real API calls to Gemini. Default is dry-run.",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=0.12,
        help="Maximum USD cost limit for this repair run (default: 0.12).",
    )
    args = parser.parse_args()
    ret = asyncio.run(main_async(args))
    sys.exit(ret)


if __name__ == "__main__":
    main()
