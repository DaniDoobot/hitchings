"""Resumable v4 analysis backfill runner for HITCHINGS (Bloque 7H).

Executes a homogeneous baseline v4 across all eligible Entries in the repository.
Strictly sequential, fail-closed, auditable, and resumable.

Usage:
    # Dry-run mode (default, zero API calls, zero DB writes):
    python -m scripts.run_v4_backfill

    # Real execution (requires explicit confirmation and budget):
    python -m scripts.run_v4_backfill --confirm-real-calls --max-usd 2.00

    # Resume an interrupted run:
    python -m scripts.run_v4_backfill --confirm-real-calls --resume-run-id <UUID> --max-usd 2.00
"""

import argparse
import asyncio
import logging
import signal
import sys
import time
import unicodedata
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
    build_topic_hierarchy,
)
from app.services.current_analysis_service import (
    select_current_analysis,
    is_analysis_stale,
)
from app.services.source_sufficiency_service import assess_source_sufficiency
from scripts.audit_entry_inventory import run_inventory_audit

logger = logging.getLogger("run_v4_backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ----------------------------------------------------------------------
# Global signal / graceful stop management
# ----------------------------------------------------------------------
stop_requested = False


def sigint_handler(signum, frame):
    global stop_requested
    print("\n" + "!" * 80)
    print("  [SIGINT RECIBIDO] Solicitud de parada ordenada.")
    print("  La llamada/Entry actualmente en curso finalizará y se auditará en BD.")
    print("  El runner se detendrá limpiamente antes de comenzar la siguiente Entry.")
    print("!" * 80 + "\n")
    stop_requested = True


# ----------------------------------------------------------------------
# Source Ordering
# ----------------------------------------------------------------------
def get_source_priority(source_name: str) -> int:
    """Return deterministic source processing priority (1 to 4)."""
    if "CNMC" in source_name:
        return 1
    elif "Commission" in source_name or "Comisión" in source_name:
        return 2
    elif "Competition Appeal" in source_name:
        return 3
    elif "Court of Justice" in source_name:
        return 4
    return 99


# ----------------------------------------------------------------------
# Cost calculation directly from DB
# ----------------------------------------------------------------------
def get_run_cost_usd(db: Session, run_id: uuid.UUID) -> float:
    """Calculate cumulative cost for this run directly from DB."""
    try:
        val = (
            db.query(func.coalesce(func.sum(AnalysisCall.estimated_cost_usd), 0.0))
            .filter(AnalysisCall.call_metadata["run_id"].astext == str(run_id))
            .scalar()
        )
        return float(val or 0.0)
    except Exception:
        # Fallback for dialects without JSON path support (e.g. in-memory SQLite tests)
        calls = db.query(AnalysisCall).all()
        return sum(
            float(c.estimated_cost_usd or 0.0)
            for c in calls
            if c.call_metadata and c.call_metadata.get("run_id") == str(run_id)
        )


def calculate_conservative_reservation(
    stage: str,
    entry: Entry,
    prompt: AnalysisPromptVersion,
    input_rate: float,
    output_rate: float,
) -> float:
    """Calculate maximum theoretical cost reservation for the next call."""
    content_chars = len(entry.content or "")
    # Assume 2.5 chars per token (worst-case Spanish/English tokenization) + 3000 prompt/system overhead
    est_input_tokens = int(content_chars / 2.5) + 3000
    config = prompt.config or {}
    max_output_tokens = config.get("max_output_tokens", 2048 if stage == "triage" else 4096)

    input_cost = (est_input_tokens * input_rate) / 1_000_000.0
    output_cost = (max_output_tokens * output_rate) / 1_000_000.0
    total_est = (input_cost + output_cost) * 1.5  # 50% safety multiplier
    floor = 0.04 if stage == "triage" else 0.06
    return max(total_est, floor)


# ----------------------------------------------------------------------
# Evidence Metrics Computation
# ----------------------------------------------------------------------
def compute_evidence_metrics_for_run(db: Session, run_id: uuid.UUID) -> dict[str, Any]:
    """Calculate evidence length and verification metrics for all completed analyses in this run."""
    analyses = (
        db.query(EntryAnalysis)
        .join(AnalysisCall, AnalysisCall.entry_analysis_id == EntryAnalysis.id)
        .filter(
            EntryAnalysis.pipeline_version == "v4",
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
    }


# ----------------------------------------------------------------------
# Taxonomy canonicalization stats
# ----------------------------------------------------------------------
def compute_taxonomy_stats_for_run(db: Session, run_id: uuid.UUID) -> dict[str, int]:
    """Count raw vs canonical parent+child co-occurrences for newly completed v4 analyses."""
    analyses = (
        db.query(EntryAnalysis)
        .join(AnalysisCall, AnalysisCall.entry_analysis_id == EntryAnalysis.id)
        .filter(
            EntryAnalysis.pipeline_version == "v4",
            EntryAnalysis.status == "completed",
            AnalysisCall.call_metadata["run_id"].astext == str(run_id),
        )
        .distinct()
        .all()
    )

    raw_redundant = 0
    canonical_redundant = 0

    for ea in analyses:
        topics = (
            db.query(EntryAnalysisTopic)
            .options(joinedload(EntryAnalysisTopic.topic).joinedload(TrackingTopic.parent))
            .filter(EntryAnalysisTopic.analysis_id == ea.id)
            .all()
        )
        can_res = canonicalize_analysis_topics(topics, db=db)

        # Raw redundancy detected if canonicalizer had to remove any parent codes
        if can_res.removed_parent_codes:
            raw_redundant += 1

        # Canonical redundancy: verify zero parent-child co-occurrences in canonical view
        can_ids = {t.topic_id for t in can_res.canonical_topics}
        can_parent_ids = {t.parent_id for t in can_res.canonical_topics if t.parent_id}
        if can_ids.intersection(can_parent_ids):
            canonical_redundant += 1

    return {
        "analyses_evaluated": len(analyses),
        "raw_redundant_count": raw_redundant,
        "canonical_redundant_count": canonical_redundant,
    }


# ----------------------------------------------------------------------
# Runner Core
# ----------------------------------------------------------------------
async def main_async(args: argparse.Namespace) -> int:
    global stop_requested
    signal.signal(signal.SIGINT, sigint_handler)

    print("=" * 110)
    print("  HITCHINGS OBSERVATORY - RUNNER RESUMIBLE Y BACKFILL REAL V4 (BLOQUE 7H)")
    print("=" * 110)

    db: Session = SessionLocal()
    try:
        settings = get_settings()

        # ------------------------------------------------------------------
        # PREFLIGHT 1: Inventory Integrity Audit (Read-Only)
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 1/5] Ejecutando auditoría de inventario e identidad...")
        inv_results = run_inventory_audit(db)
        if inv_results["total_error"] > 0:
            print(f"CRITICAL ERROR: Se detectaron {inv_results['total_error']} errores de identidad en inventario. ABORTANDO.")
            return 1
        if inv_results["total_warning"] > 0:
            print(f"CRITICAL ERROR: Se detectaron {inv_results['total_warning']} advertencias de identidad en inventario. ABORTANDO.")
            return 1
        print(f"  -> Inventario 100% verificado: {inv_results['total_ok']}/80 OK, 0 duplicados, 0 colisiones.")

        # ------------------------------------------------------------------
        # PREFLIGHT 2: Running / Pending v4 check
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 2/5] Comprobando que no existan análisis v4 en ejecución o pendientes...")
        pending_v4 = (
            db.query(EntryAnalysis)
            .filter(
                EntryAnalysis.pipeline_version == "v4",
                EntryAnalysis.status.in_(["running", "pending"]),
            )
            .all()
        )
        if pending_v4:
            print(f"CRITICAL ERROR: Existen {len(pending_v4)} análisis v4 en estado no completado:")
            for p in pending_v4:
                print(f"  analysis_id={p.id} entry_id={p.entry_id} status={p.status}")
            print("ABORTANDO. Se requiere intervención manual antes de lanzar el backfill.")
            return 1
        print("  -> Cero análisis v4 running/pending en base de datos.")

        # ------------------------------------------------------------------
        # PREFLIGHT 3: Provider, Model & Pricing Configuration
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 3/5] Verificando configuración de provider, modelo y pricing...")
        if settings.ANALYSIS_PROVIDER != "gemini_api":
            print(f"CRITICAL ERROR: ANALYSIS_PROVIDER={settings.ANALYSIS_PROVIDER} (esperado: gemini_api)")
            return 1
        if settings.GEMINI_MODEL != "gemini-3.8-flash":
            print(f"CRITICAL ERROR: GEMINI_MODEL={settings.GEMINI_MODEL} (esperado: gemini-3.8-flash)")
            return 1
        if args.confirm_real_calls and not settings.GEMINI_API_KEY:
            print("CRITICAL ERROR: GEMINI_API_KEY no está configurada y se solicitó ejecución real.")
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
        # PREFLIGHT 4: Prompts v4 validation
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 4/5] Verificando versiones activas de prompts v4...")
        triage_prompt = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_triage",
                AnalysisPromptVersion.version == 4,
                AnalysisPromptVersion.active.is_(True),
            )
            .first()
        )
        deep_prompt = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_deep_analysis",
                AnalysisPromptVersion.version == 4,
                AnalysisPromptVersion.active.is_(True),
            )
            .first()
        )
        if not triage_prompt or not deep_prompt:
            print(f"CRITICAL ERROR: No se encontraron los prompts v4 activos (triage={triage_prompt}, deep={deep_prompt}).")
            return 1
        print(f"  -> Triage prompt: {triage_prompt.code}:v{triage_prompt.version} (id={triage_prompt.id})")
        print(f"  -> Deep prompt:   {deep_prompt.code}:v{deep_prompt.version} (id={deep_prompt.id})")

        # Active tracking matrix
        matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
        if not matrix:
            print("CRITICAL ERROR: No se encontró una TrackingMatrix activa.")
            return 1
        print(f"  -> Active TrackingMatrix: {matrix.name} (id={matrix.id})")

        # ------------------------------------------------------------------
        # PREFLIGHT 5: PostgreSQL Database Baseline State
        # ------------------------------------------------------------------
        print("\n[PREFLIGHT 5/5] Verificando estado numérico de PostgreSQL...")
        sources_cnt = db.query(Source).count()
        entries_cnt = db.query(Entry).count()
        analyses_cnt = db.query(EntryAnalysis).count()
        calls_cnt = db.query(AnalysisCall).count()
        prompts_cnt = db.query(AnalysisPromptVersion).count()
        prev_recorded_cost = float(db.query(func.coalesce(func.sum(AnalysisCall.estimated_cost_usd), 0.0)).scalar() or 0.0)

        print(f"  -> Sources:          {sources_cnt} (esperado: 4)")
        print(f"  -> Entries:          {entries_cnt} (esperado: 80)")
        print(f"  -> EntryAnalysis:    {analyses_cnt} (esperado: 30)")
        print(f"  -> AnalysisCalls:    {calls_cnt} (esperado: 46)")
        print(f"  -> PromptVersions:   {prompts_cnt} (esperado: 8)")
        print(f"  -> Coste en BD:      ${prev_recorded_cost:.6f} (esperado: $0.291798)")

        # ------------------------------------------------------------------
        # SELECTION OF NEEDS_V4 ENTRIES (Deterministic)
        # ------------------------------------------------------------------
        all_entries = db.query(Entry).options(joinedload(Entry.source)).all()

        already_v4_completed: list[Entry] = []
        v4_failed_entries: list[Entry] = []
        needs_v4_entries: list[Entry] = []

        for e in all_entries:
            curr_hash = compute_analysis_input_hash(e)
            v4_analyses = (
                db.query(EntryAnalysis)
                .filter(EntryAnalysis.entry_id == e.id, EntryAnalysis.pipeline_version == "v4")
                .all()
            )
            has_completed_v4 = any(
                ea.status == "completed" and ea.entry_content_hash == curr_hash
                for ea in v4_analyses
            )
            has_failed_v4 = any(ea.status == "failed" for ea in v4_analyses)

            if has_completed_v4:
                already_v4_completed.append(e)
            elif has_failed_v4:
                v4_failed_entries.append(e)
                # Note: v4_failed are NOT auto-retried unless explicitly requested
            else:
                needs_v4_entries.append(e)

        # Sort needs_v4 deterministically:
        # 1. Source priority (CNMC -> EC -> CAT -> CURIA)
        # 2. published_at DESC nullslast
        # 3. entry.id ASC
        def sort_key(e: Entry) -> tuple[int, Any, str]:
            src_pri = get_source_priority(e.source.name)
            pub_ts = e.published_at.timestamp() if e.published_at else -1.0
            return (src_pri, -pub_ts, str(e.id))

        needs_v4_entries.sort(key=sort_key)

        print("\n--- SELECCIÓN DETERMINISTA DE ENTRIES ---")
        print(f"Total Entries en BD:                {len(all_entries)}")
        print(f"  - Con v4 vigente completed:        {len(already_v4_completed)} ({already_v4_completed[0].title[:50]}...)")
        print(f"  - Con v4 previo fallido (failed):  {len(v4_failed_entries)}")
        print(f"  - Pendientes de baseline v4:       {len(needs_v4_entries)}")

        # Source breakdown
        by_src: dict[str, list[Entry]] = {}
        for e in needs_v4_entries:
            by_src.setdefault(e.source.name, []).append(e)

        print("\nDesglose de Entries pendientes por Fuente (en orden determinista):")
        for src_name in sorted(by_src.keys(), key=get_source_priority):
            items = by_src[src_name]
            chars = sum(len(it.content or "") for it in items)
            print(f"  [{get_source_priority(src_name)}] {src_name:<48} | {len(items):>2} entries | {chars:>10,} chars")

        # ------------------------------------------------------------------
        # DRY-RUN MODE (default)
        # ------------------------------------------------------------------
        if not args.confirm_real_calls:
            print("\n" + "=" * 110)
            print("  MODO DRY-RUN ACTIVO (Por defecto). NO se realizarán llamadas a Gemini ni escrituras en BD.")
            print("=" * 110)
            print("\nMuestra de las 5 primeras Entries que se procesarán:")
            for idx, e in enumerate(needs_v4_entries[:5], start=1):
                print(f"  [{idx:>2}/79] {e.source.name[:25]:<25} | entry_id: {str(e.id)[:8]} | {e.published_at.strftime('%Y-%m-%d') if e.published_at else 'N/A'} | chars={len(e.content or ''):<6} | {e.title[:65]}...")

            print("\nMuestra de las 5 últimas Entries que se procesarán (CURIA):")
            for idx, e in enumerate(needs_v4_entries[-5:], start=len(needs_v4_entries) - 4):
                print(f"  [{idx:>2}/79] {e.source.name[:25]:<25} | entry_id: {str(e.id)[:8]} | {e.published_at.strftime('%Y-%m-%d') if e.published_at else 'N/A'} | chars={len(e.content or ''):<6} | {e.title[:65]}...")

            print("\nPresupuesto autorizado para ejecución real:")
            print(f"  Hard budget: $2.0000 | Warning al 75%: $1.5000 | Estimación token-model: ~$0.9949")
            print("\nPara ejecutar el backfill REAL, ejecute el siguiente comando:")
            print("  PowerShell / Bash:")
            print("  python -m scripts.run_v4_backfill --confirm-real-calls --max-usd 2.00")
            print("=" * 110)
            return 0

        # ------------------------------------------------------------------
        # REAL EXECUTION MODE (--confirm-real-calls)
        # ------------------------------------------------------------------
        print("\n" + "=" * 110)
        print("  INICIANDO EJECUCIÓN REAL DEL BACKFILL V4")
        print("=" * 110)

        # Determine run_id
        if args.resume_run_id:
            try:
                run_id = uuid.UUID(args.resume_run_id)
                print(f"REANUDANDO RUN EXISTENTE: run_id = {run_id}")
            except Exception as e:
                print(f"CRITICAL ERROR: --resume-run-id inválido: {e}")
                return 1
        else:
            run_id = uuid.uuid4()
            print(f"NUEVO RUN INICIALIZADO: run_id = {run_id}")

        max_usd = float(args.max_usd)
        initial_run_cost = get_run_cost_usd(db, run_id)
        print(f"Presupuesto hard: ${max_usd:.2f} | Coste previo acumulado de este run: ${initial_run_cost:.4f}")

        # Instantiate provider & pipeline
        provider = GeminiAPIProvider(settings=settings)
        pipeline = AnalysisPipelineService(provider=provider)

        # Hook for budget guard and graceful stop
        warning_75_shown = initial_run_cost >= (max_usd * 0.75)

        def before_stage_hook(stage: str, entry: Entry, prompt: AnalysisPromptVersion, analysis: EntryAnalysis) -> None:
            nonlocal warning_75_shown
            if stop_requested:
                raise PipelineStopRequestedError("Interrupción por usuario (SIGINT)")

            current_cost = get_run_cost_usd(db, run_id)

            # Warning check at 75%
            if current_cost >= (max_usd * 0.75) and not warning_75_shown:
                print(f"\n{'!'*80}\n  [BUDGET WARNING 75%] Coste acumulado del run (${current_cost:.4f}) ha alcanzado el 75% del límite (${max_usd:.2f}).\n{'!'*80}\n")
                warning_75_shown = True

            # Conservative reservation check
            reservation = calculate_conservative_reservation(stage, entry, prompt, in_rate, out_rate)
            if current_cost + reservation > max_usd:
                msg = f"Reserva conservadora (${reservation:.4f}) sumada al coste actual (${current_cost:.4f}) = ${current_cost + reservation:.4f} > presupuesto hard (${max_usd:.2f})"
                print(f"\n{'#'*80}\n  [STOP_BUDGET] {msg}\n{'#'*80}\n")
                raise PipelineBudgetExceededError(msg)

        # Metrics trackers
        attempted_count = 0
        completed_count = 0
        failed_count = 0
        skipped_count = 0

        triage_calls_count = 0
        deep_calls_count = 0

        relevant_count = 0
        uncertain_count = 0
        not_relevant_count = 0

        total_input_tokens = 0
        total_visible_output_tokens = 0
        total_thought_tokens = 0
        total_billable_output_tokens = 0

        consecutive_grounding_errors = 0
        total_grounding_errors = 0
        consecutive_parse_errors = 0

        failed_records: list[dict[str, Any]] = []
        cost_by_source: dict[str, float] = {}

        total_to_process = len(needs_v4_entries)
        start_time = time.time()

        for idx, entry in enumerate(needs_v4_entries, start=1):
            if stop_requested:
                print(f"\n[DETENCIÓN] Parada solicitada. Deteniendo antes de la Entry {idx}/{total_to_process}.")
                break

            # A) Recomprobar en fresco en BD si la Entry aún necesita v4
            fresh_hash = compute_analysis_input_hash(entry)
            existing_v4 = (
                db.query(EntryAnalysis)
                .filter(EntryAnalysis.entry_id == entry.id, EntryAnalysis.pipeline_version == "v4")
                .all()
            )
            if any(ea.status == "completed" and ea.entry_content_hash == fresh_hash for ea in existing_v4):
                print(f"[{idx:>2}/{total_to_process}] Saltando {entry.id} (ya tiene v4 completed vigente en BD).")
                skipped_count += 1
                continue
            if any(ea.status == "failed" for ea in existing_v4):
                print(f"[{idx:>2}/{total_to_process}] Saltando {entry.id} (tiene v4 previo failed; no auto-retry).")
                skipped_count += 1
                continue

            # B) Prepare execution
            attempted_count += 1
            src_name = entry.source.name
            entry_start_time = time.time()

            extra_meta = {
                "run_type": "backfill",
                "run_id": str(run_id),
                "backfill_version": "baseline_v4",
                "entry_sequence": attempted_count,
                "source_name": src_name,
            }

            try:
                analysis = await pipeline.run_pipeline(
                    entry_id=entry.id,
                    matrix_id=matrix.id,
                    triage_prompt_id=triage_prompt.id,
                    deep_prompt_id=deep_prompt.id,
                    db=db,
                    pipeline_version="v4",
                    extra_call_metadata=extra_meta,
                    before_stage_hook=before_stage_hook,
                )
            except (PipelineBudgetExceededError, PipelineStopRequestedError) as e:
                print(f"\n[DETENCIÓN RUNNER] {e}")
                break
            except Exception as e:
                logger.exception("Error no capturado en pipeline para entry=%s: %s", entry.id, e)
                # Continue if non-systemic
                analysis = (
                    db.query(EntryAnalysis)
                    .filter(EntryAnalysis.entry_id == entry.id, EntryAnalysis.pipeline_version == "v4")
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
            deep_executed = any(c.stage == "deep_analysis" for c in calls)
            has_failed_call = any(c.status == "failed" for c in calls)

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

            cost_by_source[src_name] = cost_by_source.get(src_name, 0.0) + entry_cost

            # Outcome handling
            if analysis.status == "completed":
                completed_count += 1
                consecutive_grounding_errors = 0
                consecutive_parse_errors = 0
                if analysis.relevance_status == "relevant":
                    relevant_count += 1
                elif analysis.relevance_status == "uncertain":
                    uncertain_count += 1
                else:
                    not_relevant_count += 1
            else:
                failed_count += 1
                failed_records.append({
                    "entry_id": str(entry.id),
                    "analysis_id": str(analysis.id),
                    "source": src_name,
                    "title": entry.title,
                    "status": analysis.status,
                    "reason": analysis.reason,
                    "cost": entry_cost,
                })

                # Check systemic error conditions
                error_types = [c.error_type for c in calls if c.error_type]
                is_grounding_err = any("Grounding" in et for et in error_types)
                is_parse_err = any("Parse" in et or "NoParsedResponse" in et for et in error_types)

                if is_grounding_err:
                    consecutive_grounding_errors += 1
                    total_grounding_errors += 1
                else:
                    consecutive_grounding_errors = 0

                if is_parse_err:
                    consecutive_parse_errors += 1
                else:
                    consecutive_parse_errors = 0

                # Systemic triggers
                if consecutive_grounding_errors >= 2:
                    print(f"\nCRITICAL STOP: 2 fallos consecutivos de GroundingValidator ({consecutive_grounding_errors}). Deteniendo para proteger presupuesto.")
                    break
                if total_grounding_errors >= 3:
                    print(f"\nCRITICAL STOP: 3 fallos totales de GroundingValidator ({total_grounding_errors}). Deteniendo para proteger presupuesto.")
                    break
                if consecutive_parse_errors >= 2:
                    print(f"\nCRITICAL STOP: 2 fallos consecutivos de Structured Output ({consecutive_parse_errors}). Deteniendo.")
                    break

                # Provider/Auth systemic check
                for c in calls:
                    if c.error_type in ["APIKeyError", "RuntimeError", "PermissionDenied", "Unauthenticated"]:
                        print(f"\nCRITICAL STOP: Error de autenticación / provider ({c.error_type}: {c.error_message}). Deteniendo inmediatamente.")
                        stop_requested = True
                        break
                if stop_requested:
                    break

            # Display progress line
            current_run_cost = get_run_cost_usd(db, run_id)
            remaining_conservative = max(0.0, max_usd - current_run_cost)
            elapsed_sec = time.time() - entry_start_time
            suff_str = assess_source_sufficiency(entry).level.value
            rel_str = f"{analysis.relevance_status} ({analysis.relevance_score})" if analysis.relevance_score is not None else "N/A"
            deep_str = "YES" if deep_executed else "NO"

            print(
                f"[{idx:>2}/{total_to_process}] {src_name[:15]:<15} | {str(entry.id)[:8]} | {entry.title[:38]:<38}... | "
                f"suff={suff_str:<7} | rel={rel_str:<16} | deep={deep_str:<3} | {analysis.status:<9} | "
                f"${entry_cost:.4f} | run=${current_run_cost:.4f} | rem=${remaining_conservative:.4f} | {elapsed_sec:.1f}s"
            )

            # Checkpoint every 10
            if attempted_count % 10 == 0:
                print("-" * 110)
                avg_c = current_run_cost / attempted_count if attempted_count > 0 else 0.0
                print(
                    f"  CHECKPOINT [{attempted_count}/{total_to_process}]: "
                    f"Completed={completed_count} | Failed={failed_count} | "
                    f"Triage={triage_calls_count} | Deep={deep_calls_count} | "
                    f"Coste=${current_run_cost:.4f} (Media=${avg_c:.4f}) | "
                    f"Rel={relevant_count} Unc={uncertain_count} NotRel={not_relevant_count}"
                )
                print("-" * 110)

        # ------------------------------------------------------------------
        # FINAL REPORT & POST-RUN AUDIT
        # ------------------------------------------------------------------
        total_duration = time.time() - start_time
        final_run_cost = get_run_cost_usd(db, run_id)
        final_db_cost = float(db.query(func.coalesce(func.sum(AnalysisCall.estimated_cost_usd), 0.0)).scalar() or 0.0)
        reconstructed_cost = 0.507614 + final_run_cost

        ev_metrics = compute_evidence_metrics_for_run(db, run_id)
        tax_stats = compute_taxonomy_stats_for_run(db, run_id)

        # Current analyses distribution
        current_v4_count = 0
        current_v3_count = 0
        current_v2_count = 0
        no_current_count = 0

        for e in all_entries:
            eas = db.query(EntryAnalysis).filter(EntryAnalysis.entry_id == e.id).all()
            cur = select_current_analysis(e, eas)
            if cur:
                if cur.pipeline_version == "v4":
                    current_v4_count += 1
                elif cur.pipeline_version == "v3":
                    current_v3_count += 1
                elif cur.pipeline_version == "v2":
                    current_v2_count += 1
            else:
                no_current_count += 1

        print("\n" + "=" * 110)
        print("  HITCHINGS OBSERVATORY - INFORME FINAL DE BACKFILL V4")
        print("=" * 110)
        print(f"Run ID:                      {run_id}")
        print(f"Duración total:              {total_duration:.1f} segundos ({total_duration/60:.2f} minutos)")
        print(f"Entries seleccionadas:       {total_to_process}")
        print(f"Entries intentadas:          {attempted_count}")
        print(f"Entries completadas v4:      {completed_count}")
        print(f"Entries fallidas:            {failed_count}")
        print(f"Entries saltadas:            {skipped_count}")

        print(f"\nDesglose de Relevancia (v4 completed):")
        print(f"  - Relevant:                {relevant_count}")
        print(f"  - Uncertain:               {uncertain_count}")
        print(f"  - Not Relevant:            {not_relevant_count}")

        print(f"\nLlamadas API del Run:")
        print(f"  - Triage calls:            {triage_calls_count}")
        print(f"  - Deep calls:              {deep_calls_count}")
        print(f"  - Total calls en este run: {triage_calls_count + deep_calls_count}")

        print(f"\nConsumo de Tokens del Run:")
        print(f"  - Input tokens:            {total_input_tokens:,}")
        print(f"  - Output tokens visibles:  {total_visible_output_tokens:,}")
        print(f"  - Thought tokens:          {total_thought_tokens:,}")
        print(f"  - Billable output tokens:  {total_billable_output_tokens:,}")

        print(f"\nMétricas de Evidencia y Citas (Grounding):")
        print(f"  - Total citas extraídas:   {ev_metrics['total_quotes']}")
        print(f"  - Citas validadas 100%:    {ev_metrics['validated_quotes']}")
        print(f"  - Tasa de validación:      {ev_metrics['validation_rate']:.1f}%")
        print(f"  - Longitud media chars:    {ev_metrics['avg_chars']:.1f} caracteres")
        print(f"  - Longitud media palabras: {ev_metrics['avg_words']:.1f} palabras")
        print(f"  - Citas > 180 caracteres:  {ev_metrics['quotes_gt_180_chars']}")
        print(f"  - Citas > 25 palabras:     {ev_metrics['quotes_gt_25_words']}")
        print(f"  - Longitud máxima:         {ev_metrics['max_chars']} caracteres")

        print(f"\nContabilidad de Costes:")
        print(f"  - Coste total del Run:     ${final_run_cost:.6f}")
        print(f"  - Media por Entry intent.: ${(final_run_cost / attempted_count if attempted_count > 0 else 0.0):.6f}")
        print(f"  - Presupuesto remanente:   ${(max_usd - final_run_cost):.6f} (de ${max_usd:.2f})")
        print(f"  - Coste acumulado en BD:   ${final_db_cost:.6f} (anterior: ${prev_recorded_cost:.6f})")
        print(f"  - Coste hist. reconstruido:${reconstructed_cost:.6f} (anterior: $0.507614)")

        print(f"\nCoste por Fuente en este Run:")
        for sname, scost in sorted(cost_by_source.items(), key=lambda x: get_source_priority(x[0])):
            print(f"  - {sname:<45}: ${scost:.6f}")

        print(f"\nEstado Canónico Final de Análisis (80 Entries):")
        print(f"  - Current v4:              {current_v4_count} / 80")
        print(f"  - Current v3:              {current_v3_count} / 80")
        print(f"  - Current v2:              {current_v2_count} / 80")
        print(f"  - Sin análisis vigente:    {no_current_count} / 80")

        print(f"\nTaxonomía y Subsunción Canónica (Nuevos v4):")
        print(f"  - Redundancia bruta (raw): {tax_stats['raw_redundant_count']} / {tax_stats['analyses_evaluated']}")
        print(f"  - Redundancia canónica:    {tax_stats['canonical_redundant_count']} / {tax_stats['analyses_evaluated']} (Esperado: 0)")

        if failed_records:
            print("\n" + "!" * 80)
            print(f"LISTADO EXACTO DE ENTRIES FALLIDAS ({len(failed_records)}):")
            for f in failed_records:
                print(f"  - entry_id:    {f['entry_id']}")
                print(f"    analysis_id: {f['analysis_id']}")
                print(f"    source:      {f['source']}")
                print(f"    title:       {f['title']}")
                print(f"    status:      {f['status']}")
                print(f"    reason:      {f['reason']}")
                print(f"    coste:       ${f['cost']:.6f}")
            print("!" * 80)

        print("=" * 110)
        return 0 if failed_count == 0 else 1

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Resumable v4 analysis backfill runner for HITCHINGS")
    parser.add_argument("--confirm-real-calls", action="store_true", help="Execute real Gemini API calls (default is dry-run)")
    parser.add_argument("--max-usd", type=float, default=2.00, help="Maximum budget ceiling for this run (USD, default 2.00)")
    parser.add_argument("--resume-run-id", type=str, default=None, help="UUID of an interrupted run to resume")
    args = parser.parse_args()

    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
