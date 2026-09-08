"""Benchmark script: controlled AI analysis run on 20 unanalysed Entries.

Selects the 5 most recent unanalysed Entries per Source (deterministic) and runs the
triage -> deep pipeline against Gemini Developer API.

SAFETY:
  - Requires --confirm-real-calls to make actual Gemini API calls.
  - Without that flag: shows preflight and sample selection only (dry-run mode).
  - ANALYSIS_PROVIDER must be 'gemini_api' for real calls.
  - Budget hard stop: aborts before each new Entry if cost >= --max-usd (starts at $0.00 for this run).
  - Metrics isolation: benchmark metrics are strictly isolated by benchmark_run_id.

Usage:
    # Dry-run (no API calls, shows what would be done):
    python -m scripts.run_analysis_benchmark

    # Real calls (requires ANALYSIS_PROVIDER=gemini_api + GEMINI_API_KEY):
    python -m scripts.run_analysis_benchmark --confirm-real-calls [--max-usd 1.00]
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, func
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.analysis import AnalysisCall, AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.source import Source
from app.models.tracking import TrackingMatrix
from app.providers.ai.gemini_api import GeminiAPIProvider
from app.services.analysis_pipeline_service import AnalysisPipelineService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("benchmark")


SAMPLE_STRATEGY = "latest_5_unanalysed_per_source"
ENTRIES_PER_SOURCE = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def short_title(title: Optional[str], max_len: int = 60) -> str:
    if not title:
        return "(sin título)"
    return title[:max_len] + "..." if len(title) > max_len else title


def fmt_usd(value: float) -> str:
    return f"${value:.6f}"


def fmt_ms(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return f"{value}ms"


def print_separator(char: str = "-", width: int = 100) -> None:
    print(char * width)


# ---------------------------------------------------------------------------
# Sample selection
# ---------------------------------------------------------------------------

def select_sample(db: Session) -> list[Entry]:
    """Select the 5 most recent unanalysed Entries per Source, deterministically ordered."""
    sources = db.query(Source).order_by(Source.name).all()
    if not sources:
        logger.error("No Sources found in database.")
        return []

    analysed_entry_ids_subquery = select(EntryAnalysis.entry_id).scalar_subquery()

    selected: list[Entry] = []
    for source in sources:
        entries = (
            db.query(Entry)
            .filter(
                Entry.source_id == source.id,
                ~Entry.id.in_(analysed_entry_ids_subquery)
            )
            .options(joinedload(Entry.source))
            .order_by(Entry.published_at.desc().nullslast(), Entry.created_at.desc(), Entry.id.asc())
            .limit(ENTRIES_PER_SOURCE)
            .all()
        )
        selected.extend(entries)

    selected.sort(
        key=lambda e: (
            e.source.name if e.source else "",
            -(e.published_at.timestamp() if e.published_at else 0),
            str(e.id)
        )
    )
    return selected


def check_no_prior_analyses(entries: list[Entry], db: Session) -> list[Entry]:
    """Verify none of the selected entries already have EntryAnalysis records."""
    entry_ids = [e.id for e in entries]
    existing = (
        db.query(EntryAnalysis)
        .filter(EntryAnalysis.entry_id.in_(entry_ids))
        .all()
    )
    if not existing:
        return []
    conflicting_ids = {ea.entry_id for ea in existing}
    return [e for e in entries if e.id in conflicting_ids]


# ---------------------------------------------------------------------------
# Preflight display
# ---------------------------------------------------------------------------

def print_preflight(
    settings,
    entries: list[Entry],
    max_usd: float,
    real_calls: bool,
) -> None:
    print_separator("=")
    print("  HITCHINGS - BENCHMARK PREFLIGHT")
    print_separator("=")
    print(f"  Provider:                 {settings.ANALYSIS_PROVIDER}")
    print(f"  Model:                    {settings.GEMINI_MODEL}")
    has_key = bool(settings.GEMINI_API_KEY.strip()) if settings.GEMINI_API_KEY else False
    print(f"  API key configured:       {'YES' if has_key else 'NO (not configured)'}")
    print(f"  Thinking TRIAGE:          {settings.ANALYSIS_TRIAGE_THINKING_LEVEL}")
    print(f"  Thinking DEEP:            {settings.ANALYSIS_DEEP_THINKING_LEVEL}")
    print(f"  Max input chars (triage): {settings.ANALYSIS_TRIAGE_MAX_INPUT_CHARS:,}")
    print(f"  Max input chars (deep):   {settings.ANALYSIS_DEEP_MAX_INPUT_CHARS:,}")
    print_separator()
    print(f"  Pricing (input):          ${settings.GEMINI_INPUT_USD_PER_MILLION_TOKENS} / 1M tokens")
    print(f"  Pricing (output):         ${settings.GEMINI_OUTPUT_USD_PER_MILLION_TOKENS} / 1M tokens")
    print(f"  Benchmark Budget limit:   {fmt_usd(max_usd)}")
    print_separator()
    print(f"  Sample strategy:          {SAMPLE_STRATEGY} ({ENTRIES_PER_SOURCE} per Source)")
    total_chars = sum(len(e.content or "") for e in entries)
    max_chars = max((len(e.content or "") for e in entries), default=0)
    print(f"  Entries selected:         {len(entries)}")
    print(f"  Total content chars:      {total_chars:,}")
    print(f"  Max document chars:       {max_chars:,}")
    print(f"  Real calls mode:          {'YES [REAL API CALLS]' if real_calls else 'NO (dry-run)'}")
    print_separator("=")


def print_sample_table(entries: list[Entry]) -> None:
    print("\n  SELECTED ENTRIES")
    print_separator()
    header = f"  {'#':<3} {'Source':<30} {'Published':<12} {'Chars':>8}  Title"
    print(header)
    print_separator()
    for i, e in enumerate(entries, 1):
        source_name = (e.source.name if e.source else "?")[:28]
        pub = e.published_at.strftime("%Y-%m-%d") if e.published_at else "?"
        chars = len(e.content or "")
        title = short_title(e.title, 50)
        print(f"  {i:<3} {source_name:<30} {pub:<12} {chars:>8,}  {title}")
    print_separator()


# ---------------------------------------------------------------------------
# Prompt resolution
# ---------------------------------------------------------------------------

def resolve_prompts(db: Session) -> tuple[AnalysisPromptVersion, AnalysisPromptVersion]:
    """Find the active v2 triage and deep analysis prompts."""
    triage = db.execute(
        select(AnalysisPromptVersion).where(
            AnalysisPromptVersion.code == "observatory_triage",
            AnalysisPromptVersion.version == 2,
            AnalysisPromptVersion.active == True,  # noqa: E712
        )
    ).scalar_one_or_none()

    deep = db.execute(
        select(AnalysisPromptVersion).where(
            AnalysisPromptVersion.code == "observatory_deep_analysis",
            AnalysisPromptVersion.version == 2,
            AnalysisPromptVersion.active == True,  # noqa: E712
        )
    ).scalar_one_or_none()

    if not triage:
        print("\n  ERROR: observatory_triage v2 not found in database.")
        print("  Run: python -m scripts.seed_analysis_prompts")
        sys.exit(1)
    if not deep:
        print("\n  ERROR: observatory_deep_analysis v2 not found in database.")
        print("  Run: python -m scripts.seed_analysis_prompts")
        sys.exit(1)

    return triage, deep


def resolve_matrix(db: Session) -> TrackingMatrix:
    """Find the active TrackingMatrix."""
    matrix = (
        db.query(TrackingMatrix)
        .filter(TrackingMatrix.status == "active")
        .first()
    )
    if not matrix:
        print("\n  ERROR: No active TrackingMatrix found.")
        sys.exit(1)
    return matrix


# ---------------------------------------------------------------------------
# Result display helpers
# ---------------------------------------------------------------------------

def print_results_table(results: list[dict]) -> None:
    print_separator("=")
    print("  BENCHMARK RESULTS TABLE")
    print_separator("=")
    cols = (
        f"  {'#':<3} {'Source':<20} {'Score':>5} {'Status':<14} {'Conf':>5} "
        f"{'T-In':>8} {'T-Out':>8} {'T-Cost':>10} {'T-ms':>7} "
        f"{'Deep':>5} {'D-Cost':>10} {'Total$':>10} {'EA-Status':<12}  Title"
    )
    print(cols)
    print_separator()
    for r in results:
        score = str(r.get("relevance_score") or "-")
        status = (r.get("relevance_status") or "-")[:13]
        conf = f"{r['confidence']:.2f}" if r.get("confidence") is not None else "-"
        t_in = f"{r.get('triage_input_tokens', 0):,}"
        t_out = f"{r.get('triage_output_tokens', 0):,}"
        t_cost = fmt_usd(r.get("triage_cost", 0.0))
        t_ms = fmt_ms(r.get("triage_latency_ms"))
        deep_called = "YES" if r.get("deep_called") else "no"
        d_cost = fmt_usd(r.get("deep_cost", 0.0))
        total_cost = fmt_usd(r.get("total_cost", 0.0))
        ea_status = (r.get("ea_status") or "-")[:11]
        source_name = (r.get("source") or "")[:18]
        title = short_title(r.get("title"), 40)
        print(
            f"  {r['idx']:<3} {source_name:<20} {score:>5} {status:<14} {conf:>5} "
            f"{t_in:>8} {t_out:>8} {t_cost:>10} {t_ms:>7} "
            f"{deep_called:>5} {d_cost:>10} {total_cost:>10} {ea_status:<12}  {title}"
        )
    print_separator()


def print_detailed_results(results: list[dict]) -> None:
    print_separator("=")
    print("  INDIVIDUAL DETAILED RESULTS")
    print_separator("=")
    for r in results:
        status = r.get("relevance_status")
        idx = r["idx"]
        src = r.get("source", "")
        title = r.get("title", "")
        ea_id = r.get("entry_analysis_id", "-")

        print_separator("-")
        print(f"  [{idx}/20] {src}")
        print(f"  Title:              {title}")
        print(f"  Entry ID:           {r.get('entry_id')}")
        print(f"  EntryAnalysis ID:   {ea_id}")
        print(f"  Status:             {r.get('ea_status')}")
        print(f"  Relevance Score:    {r.get('relevance_score')}")
        print(f"  Relevance Status:   {status}")
        print(f"  Confidence:         {r.get('confidence')}")
        print(f"  Primary Topic:      {r.get('primary_topic') or '-'}")
        secondaries = [c for c in (r.get("topic_codes") or []) if c != r.get("primary_topic")]
        print(f"  Secondary Topics:   {secondaries if secondaries else '[]'}")
        print(f"  Reason:             {r.get('reason') or '-'}")

        print(f"\n  TRIAGE Metrics:")
        print(f"    Input Tokens:           {r.get('triage_input_tokens', 0):,}")
        print(f"    Output Text Tokens:     {r.get('triage_text_tokens', 0):,}")
        print(f"    Output Thought Tokens:  {r.get('triage_thought_tokens', 0):,}")
        print(f"    Output Billable Tokens: {r.get('triage_output_tokens', 0):,}")
        print(f"    Cost:                   {fmt_usd(r.get('triage_cost', 0.0))}")
        print(f"    Latency:                {fmt_ms(r.get('triage_latency_ms'))}")

        if r.get("deep_called"):
            print(f"\n  DEEP ANALYSIS Metrics:")
            print(f"    Input Tokens:           {r.get('deep_input_tokens', 0):,}")
            print(f"    Output Text Tokens:     {r.get('deep_text_tokens', 0):,}")
            print(f"    Output Thought Tokens:  {r.get('deep_thought_tokens', 0):,}")
            print(f"    Output Billable Tokens: {r.get('deep_output_tokens', 0):,}")
            print(f"    Cost:                   {fmt_usd(r.get('deep_cost', 0.0))}")
            print(f"    Latency:                {fmt_ms(r.get('deep_latency_ms'))}")
            print(f"\n  Summary:\n  {r.get('summary') or '-'}")
            print(f"\n  Key Points:")
            for kp in (r.get("key_points") or []):
                print(f"    * {kp}")
        else:
            print(f"\n  DEEP ANALYSIS: Skipped (status={status})")

        print(f"\n  Total Entry Cost:   {fmt_usd(r.get('total_cost', 0.0))}")
    print_separator("=")


def print_benchmark_aggregates(
    results: list[dict],
    benchmark_run_id: str,
    budget_limit: float,
    budget_stopped: bool,
) -> None:
    total = len(results)
    relevant = sum(1 for r in results if r.get("relevance_status") == "relevant")
    uncertain = sum(1 for r in results if r.get("relevance_status") == "uncertain")
    not_relevant = sum(1 for r in results if r.get("relevance_status") == "not_relevant")
    failed = sum(1 for r in results if r.get("ea_status") == "failed")
    triage_calls = total
    deep_calls = sum(1 for r in results if r.get("deep_called"))

    triage_in = sum(r.get("triage_input_tokens", 0) for r in results)
    triage_txt = sum(r.get("triage_text_tokens", 0) for r in results)
    triage_thk = sum(r.get("triage_thought_tokens", 0) for r in results)
    triage_billable_out = sum(r.get("triage_output_tokens", 0) for r in results)
    triage_cost = sum(r.get("triage_cost", 0.0) for r in results)

    deep_in = sum(r.get("deep_input_tokens", 0) for r in results)
    deep_txt = sum(r.get("deep_text_tokens", 0) for r in results)
    deep_thk = sum(r.get("deep_thought_tokens", 0) for r in results)
    deep_billable_out = sum(r.get("deep_output_tokens", 0) for r in results)
    deep_cost = sum(r.get("deep_cost", 0.0) for r in results)

    total_in = triage_in + deep_in
    total_txt = triage_txt + deep_txt
    total_thk = triage_thk + deep_thk
    total_billable_out = triage_billable_out + deep_billable_out
    total_cost = triage_cost + deep_cost
    avg_cost = total_cost / total if total else 0.0

    triage_latencies = [r["triage_latency_ms"] for r in results if r.get("triage_latency_ms")]
    deep_latencies = [r["deep_latency_ms"] for r in results if r.get("deep_latency_ms")]
    avg_triage_lat = sum(triage_latencies) / len(triage_latencies) if triage_latencies else 0
    avg_deep_lat = sum(deep_latencies) / len(deep_latencies) if deep_latencies else 0

    print_separator("=")
    print("  BENCHMARK AGGREGATES (ISOLATED TO THIS RUN)")
    print_separator("=")
    print(f"  Benchmark Run ID:            {benchmark_run_id}")
    print(f"  Total Analyses Processed:    {total}")
    print(f"    - Relevant:                {relevant}")
    print(f"    - Uncertain:               {uncertain}")
    print(f"    - Not Relevant:            {not_relevant}")
    print(f"    - Failed:                  {failed}")
    print_separator()
    print(f"  Total Calls:                 {triage_calls + deep_calls}")
    print(f"    - Triage Calls:            {triage_calls}")
    print(f"    - Deep Calls:              {deep_calls}")
    print_separator()
    print(f"  Tokens Breakdown:")
    print(f"    - Input Tokens:            {total_in:,} (triage: {triage_in:,}, deep: {deep_in:,})")
    print(f"    - Output Text Tokens:      {total_txt:,} (triage: {triage_txt:,}, deep: {deep_txt:,})")
    print(f"    - Output Thought Tokens:   {total_thk:,} (triage: {triage_thk:,}, deep: {deep_thk:,})")
    print(f"    - Billable Output Tokens:  {total_billable_out:,} (triage: {triage_billable_out:,}, deep: {deep_billable_out:,})")
    print(f"    - Total Tokens:            {total_in + total_billable_out:,}")
    print_separator()
    print(f"  Cost Breakdown:")
    print(f"    - Triage Cost:             {fmt_usd(triage_cost)}")
    print(f"    - Deep Cost:               {fmt_usd(deep_cost)}")
    print(f"    - TOTAL BENCHMARK COST:    {fmt_usd(total_cost)}")
    print(f"    - Average Cost per Entry:  {fmt_usd(avg_cost)}")
    print_separator()
    print(f"  Latency Averages:")
    print(f"    - Average Triage Latency:  {avg_triage_lat:.0f}ms")
    print(f"    - Average Deep Latency:    {avg_deep_lat:.0f}ms")
    print_separator()
    print(f"  Benchmark Budget Limit:      {fmt_usd(budget_limit)}")
    print(f"  Benchmark Budget Used:       {fmt_usd(total_cost)}")
    if budget_stopped:
        print("  [!] BUDGET LIMIT REACHED - benchmark stopped early")
    print_separator("=")


def print_cost_by_source(results: list[dict]) -> None:
    print_separator("=")
    print("  COST & METRICS BREAKDOWN BY SOURCE")
    print_separator("=")

    by_src: dict[str, list[dict]] = {}
    for r in results:
        src = r.get("source") or "Unknown"
        by_src.setdefault(src, []).append(r)

    print(f"  {'Source':<32} {'Entries':>7} {'In-Tokens':>10} {'Out-Text':>9} {'Thoughts':>9} {'Billable-Out':>13} {'Total Cost':>12} {'Avg/Entry':>11}")
    print_separator()
    for src, items in sorted(by_src.items()):
        cnt = len(items)
        s_in = sum(i.get("triage_input_tokens", 0) + i.get("deep_input_tokens", 0) for i in items)
        s_txt = sum(i.get("triage_text_tokens", 0) + i.get("deep_text_tokens", 0) for i in items)
        s_thk = sum(i.get("triage_thought_tokens", 0) + i.get("deep_thought_tokens", 0) for i in items)
        s_billable_out = sum(i.get("triage_output_tokens", 0) + i.get("deep_output_tokens", 0) for i in items)
        s_cost = sum(i.get("total_cost", 0.0) for i in items)
        avg = s_cost / cnt if cnt else 0.0
        print(
            f"  {src[:30]:<32} {cnt:>7} {s_in:>10,} {s_txt:>9,} {s_thk:>9,} {s_billable_out:>13,} {fmt_usd(s_cost):>12} {fmt_usd(avg):>11}"
        )
    print_separator("=")


def print_global_usage(db: Session) -> None:
    """Query and display the global cumulative usage across all runs in PostgreSQL."""
    calls = db.query(AnalysisCall).filter(AnalysisCall.status == "completed").all()
    total_calls = len(calls)
    total_in = sum(c.input_tokens or 0 for c in calls)
    total_out = sum(c.output_tokens or 0 for c in calls)
    total_cost = sum(float(c.estimated_cost_usd or 0) for c in calls)
    ea_count = db.query(EntryAnalysis).count()

    print_separator("=")
    print("  GLOBAL DATABASE USAGE (Smoke Test + Benchmark Combined)")
    print_separator("=")
    print(f"  Total EntryAnalyses in DB:    {ea_count}")
    print(f"  Total Completed Calls in DB:  {total_calls}")
    print(f"  Total Input Tokens:           {total_in:,}")
    print(f"  Total Output Tokens:          {total_out:,}")
    print(f"  Total Estimated Cost:         {fmt_usd(total_cost)}")
    print_separator("=")


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------

async def run_benchmark(
    real_calls: bool,
    max_usd: float,
    db: Session,
) -> None:
    settings = get_settings()

    # Validate provider and API key
    if real_calls:
        if settings.ANALYSIS_PROVIDER.lower() != "gemini_api":
            print(
                f"\n  ERROR: --confirm-real-calls requires ANALYSIS_PROVIDER=gemini_api, "
                f"but current value is '{settings.ANALYSIS_PROVIDER}'.\n"
                "  Update .env and reload settings."
            )
            sys.exit(1)
        if not settings.GEMINI_API_KEY.strip():
            print(
                "\n  ERROR: GEMINI_API_KEY is not configured.\n"
                "  Configure GEMINI_API_KEY in .env before executing real calls."
            )
            sys.exit(1)

    # Select sample
    entries = select_sample(db)
    if not entries:
        print("\n  ERROR: No unanalysed entries found. All entries may already be analysed.")
        sys.exit(1)

    print_preflight(settings, entries, max_usd, real_calls)
    print_sample_table(entries)

    if not real_calls:
        print("\n  DRY-RUN MODE - No Gemini API calls will be made.")
        print("  Add --confirm-real-calls to execute the benchmark.")
        return

    # Check for prior analyses
    conflicts = check_no_prior_analyses(entries, db)
    if conflicts:
        print("\n  ERROR: Some selected entries already have EntryAnalysis records:")
        for e in conflicts:
            print(f"    - [{e.id}] {short_title(e.title)}")
        print("  The benchmark requires clean entries. Investigate before proceeding.")
        sys.exit(1)

    # Resolve prompts and matrix
    triage_prompt, deep_prompt = resolve_prompts(db)
    matrix = resolve_matrix(db)

    print(f"\n  Triage prompt:  {triage_prompt.code}:v{triage_prompt.version} [{triage_prompt.id}]")
    print(f"  Deep prompt:    {deep_prompt.code}:v{deep_prompt.version} [{deep_prompt.id}]")
    print(f"  Matrix:         {matrix.code} [{matrix.id}]")

    provider = GeminiAPIProvider(settings=settings)
    pipeline = AnalysisPipelineService(provider=provider)

    benchmark_run_id = str(uuid.uuid4())
    print(f"\n  Benchmark run ID: {benchmark_run_id}")
    print_separator("=")

    results: list[dict] = []
    accumulated_cost: float = 0.0
    budget_stopped: bool = False

    for idx, entry in enumerate(entries, 1):
        if accumulated_cost >= max_usd:
            print(
                f"\n  [!] BUDGET LIMIT REACHED before Entry #{idx}. "
                f"Accumulated: {fmt_usd(accumulated_cost)} / Limit: {fmt_usd(max_usd)}"
            )
            budget_stopped = True
            break

        print(f"\n  [{idx}/{len(entries)}] {entry.source.name if entry.source else '?'} - {short_title(entry.title, 70)}")
        print(f"         Content: {len(entry.content or ''):,} chars | Published: {entry.published_at}")

        extra_meta = {
            "benchmark": True,
            "benchmark_run_id": benchmark_run_id,
            "sample_strategy": SAMPLE_STRATEGY,
        }

        try:
            analysis = await pipeline.run_pipeline(
                entry_id=entry.id,
                matrix_id=matrix.id,
                triage_prompt_id=triage_prompt.id,
                deep_prompt_id=deep_prompt.id,
                db=db,
                pipeline_version="v2",
                extra_call_metadata=extra_meta,
            )
        except Exception as exc:
            logger.exception("Pipeline failed for entry %s: %s", entry.id, exc)
            results.append({
                "idx": idx,
                "entry_id": str(entry.id),
                "entry_analysis_id": None,
                "source": entry.source.name if entry.source else "?",
                "title": entry.title,
                "relevance_score": None,
                "relevance_status": None,
                "confidence": None,
                "primary_topic": None,
                "topic_codes": [],
                "reason": str(exc),
                "summary": None,
                "key_points": [],
                "triage_input_tokens": 0,
                "triage_text_tokens": 0,
                "triage_thought_tokens": 0,
                "triage_output_tokens": 0,
                "triage_cost": 0.0,
                "triage_latency_ms": None,
                "deep_called": False,
                "deep_input_tokens": 0,
                "deep_text_tokens": 0,
                "deep_thought_tokens": 0,
                "deep_output_tokens": 0,
                "deep_cost": 0.0,
                "deep_latency_ms": None,
                "total_cost": 0.0,
                "ea_status": "failed",
            })
            continue

        db.refresh(analysis)
        calls = sorted(analysis.calls, key=lambda c: c.created_at)
        triage_call = next((c for c in calls if c.stage == "triage"), None)
        deep_call = next((c for c in calls if c.stage == "deep_analysis"), None)

        triage_in = triage_call.input_tokens or 0 if triage_call else 0
        triage_out = triage_call.output_tokens or 0 if triage_call else 0
        triage_meta = triage_call.call_metadata or {} if triage_call else {}
        triage_txt = triage_meta.get("output_text_tokens", triage_out)
        triage_thk = triage_meta.get("output_thought_tokens", 0)
        triage_cost = float(triage_call.estimated_cost_usd or 0) if triage_call else 0.0
        triage_lat = triage_call.latency_ms if triage_call else None

        deep_in = deep_call.input_tokens or 0 if deep_call else 0
        deep_out = deep_call.output_tokens or 0 if deep_call else 0
        deep_meta = deep_call.call_metadata or {} if deep_call else {}
        deep_txt = deep_meta.get("output_text_tokens", deep_out)
        deep_thk = deep_meta.get("output_thought_tokens", 0)
        deep_cost = float(deep_call.estimated_cost_usd or 0) if deep_call else 0.0
        deep_lat = deep_call.latency_ms if deep_call else None

        entry_total_cost = triage_cost + deep_cost
        accumulated_cost += entry_total_cost

        topic_codes = [t.topic.code for t in analysis.topics if t.topic]
        primary_topic = next((t.topic.code for t in analysis.topics if t.is_primary and t.topic), None)

        results.append({
            "idx": idx,
            "entry_id": str(entry.id),
            "entry_analysis_id": str(analysis.id),
            "source": entry.source.name if entry.source else "?",
            "title": entry.title,
            "relevance_score": analysis.relevance_score,
            "relevance_status": analysis.relevance_status,
            "confidence": analysis.confidence,
            "primary_topic": primary_topic,
            "topic_codes": topic_codes,
            "reason": analysis.reason,
            "summary": analysis.summary,
            "key_points": analysis.key_points or [],
            "triage_input_tokens": triage_in,
            "triage_text_tokens": triage_txt,
            "triage_thought_tokens": triage_thk,
            "triage_output_tokens": triage_out,
            "triage_cost": triage_cost,
            "triage_latency_ms": triage_lat,
            "deep_called": deep_call is not None,
            "deep_input_tokens": deep_in,
            "deep_text_tokens": deep_txt,
            "deep_thought_tokens": deep_thk,
            "deep_output_tokens": deep_out,
            "deep_cost": deep_cost,
            "deep_latency_ms": deep_lat,
            "total_cost": entry_total_cost,
            "ea_status": analysis.status,
        })

        status_icon = {"relevant": "[OK]", "uncertain": "[!]", "not_relevant": "[X]", None: "?"}.get(analysis.relevance_status, "?")
        print(
            f"         -> {status_icon} score={analysis.relevance_score} "
            f"status={analysis.relevance_status} "
            f"cost={fmt_usd(entry_total_cost)} "
            f"run_total={fmt_usd(accumulated_cost)}"
        )

    # 1. Summary table
    print_results_table(results)

    # 2. Detailed individual reports
    print_detailed_results(results)

    # 3. Benchmark aggregates (isolated to this run)
    print_benchmark_aggregates(results, benchmark_run_id, max_usd, budget_stopped)

    # 4. Cost and metrics breakdown by source
    print_cost_by_source(results)

    # 5. Global database cumulative usage
    print_global_usage(db)

    print(f"\n  [OK] Benchmark run {benchmark_run_id} completed successfully.")
    print("  DO NOT analyze the remaining 59 entries until these results have been reviewed.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="HITCHINGS - Controlled AI analysis benchmark (Gemini Developer API)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        default=False,
        help=(
            "Actually call Gemini Developer API. Without this flag, only preflight and sample "
            "selection are shown (dry-run). Requires ANALYSIS_PROVIDER=gemini_api."
        ),
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=None,
        help="Maximum USD budget for this run. Defaults to ANALYSIS_BENCHMARK_MAX_USD from settings.",
    )
    args = parser.parse_args()

    settings = get_settings()
    max_usd = args.max_usd if args.max_usd is not None else settings.ANALYSIS_BENCHMARK_MAX_USD

    db: Session = SessionLocal()
    try:
        asyncio.run(run_benchmark(
            real_calls=args.confirm_real_calls,
            max_usd=max_usd,
            db=db,
        ))
    finally:
        db.close()


if __name__ == "__main__":
    main()
