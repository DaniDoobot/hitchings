"""Benchmark script: controlled AI analysis run on 20 selected Entries.

Selects the 5 most recent Entries per Source (deterministic) and runs the
triage â†’ deep pipeline against Vertex AI.

SAFETY:
  - Requires --confirm-real-calls to make actual Vertex AI API calls.
  - Without that flag: shows preflight and sample selection only (dry-run mode).
  - ANALYSIS_PROVIDER must be 'vertex_ai' for real calls.
  - Budget hard stop: aborts before each new Entry if cost >= --max-usd.

Usage:
    # Dry-run (no API calls, shows what would be done):
    python -m scripts.run_analysis_benchmark

    # Real calls (requires ANALYSIS_PROVIDER=vertex_ai + ADC configured):
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
from app.models.analysis import AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.source import Source
from app.models.tracking import TrackingMatrix
from app.services.analysis_pipeline_service import AnalysisPipelineService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("benchmark")


SAMPLE_STRATEGY = "latest_5_per_source"
ENTRIES_PER_SOURCE = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def short_title(title: Optional[str], max_len: int = 60) -> str:
    if not title:
        return "(sin tÃ­tulo)"
    return title[:max_len] + "â€¦" if len(title) > max_len else title


def fmt_usd(value: float) -> str:
    return f"${value:.6f}"


def fmt_ms(value: Optional[int]) -> str:
    if value is None:
        return "â€”"
    return f"{value}ms"


def print_separator(char: str = "-", width: int = 100) -> None:
    print(char * width)


# ---------------------------------------------------------------------------
# Sample selection
# ---------------------------------------------------------------------------

def select_sample(db: Session) -> list[Entry]:
    """Select the 5 most recent Entries per Source, deterministically ordered."""
    sources = db.query(Source).order_by(Source.name).all()
    if not sources:
        logger.error("No Sources found in database.")
        return []

    selected: list[Entry] = []
    for source in sources:
        entries = (
            db.query(Entry)
            .filter(Entry.source_id == source.id)
            .options(joinedload(Entry.source))
            .order_by(Entry.published_at.desc().nullslast(), Entry.created_at.desc())
            .limit(ENTRIES_PER_SOURCE)
            .all()
        )
        selected.extend(entries)

    # Sort final list by source name then published_at desc for stable display
    selected.sort(
        key=lambda e: (e.source.name if e.source else "", -(e.published_at.timestamp() if e.published_at else 0))
    )
    return selected


def check_no_prior_analyses(entries: list[Entry], db: Session) -> list[Entry]:
    """Verify none of the selected entries already have EntryAnalysis records.

    Returns list of entries that DO have existing analyses (should be empty).
    """
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
    print_separator("â•")
    print("  HITCHINGS â€” BENCHMARK PREFLIGHT")
    print_separator("â•")
    print(f"  Provider:       {settings.ANALYSIS_PROVIDER}")
    print(f"  Model:          {settings.VERTEX_AI_MODEL}")
    print(f"  Project:        {settings.VERTEX_AI_PROJECT or '(not set â€” will fail on real calls)'}")
    print(f"  Location:       {settings.VERTEX_AI_LOCATION}")
    print(f"  Thinking TRIAGE:{settings.ANALYSIS_TRIAGE_THINKING_LEVEL}")
    print(f"  Thinking DEEP:  {settings.ANALYSIS_DEEP_THINKING_LEVEL}")
    print(f"  Max input chars (triage): {settings.ANALYSIS_TRIAGE_MAX_INPUT_CHARS:,}")
    print(f"  Max input chars (deep):   {settings.ANALYSIS_DEEP_MAX_INPUT_CHARS:,}")
    print_separator()
    print(f"  Pricing (input):  ${settings.VERTEX_INPUT_USD_PER_MILLION_TOKENS} / 1M tokens")
    print(f"  Pricing (output): ${settings.VERTEX_OUTPUT_USD_PER_MILLION_TOKENS} / 1M tokens")
    print(f"  Budget limit:     {fmt_usd(max_usd)}")
    print_separator()
    print(f"  Sample strategy: {SAMPLE_STRATEGY} ({ENTRIES_PER_SOURCE} per Source)")
    total_chars = sum(len(e.content or "") for e in entries)
    max_chars = max((len(e.content or "") for e in entries), default=0)
    print(f"  Entries selected: {len(entries)}")
    print(f"  Total content chars: {total_chars:,}")
    print(f"  Max document chars:  {max_chars:,}")
    print(f"  Real calls mode: {'YES âš ï¸' if real_calls else 'NO (dry-run)'}")
    print_separator("â•")


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
    """Find the active v2 triage and deep analysis prompts.

    Returns:
        (triage_prompt, deep_prompt)

    Raises:
        SystemExit: if the prompts are not found.
    """
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
    print_separator("â•")
    print("  BENCHMARK RESULTS")
    print_separator("â•")
    cols = (
        f"  {'#':<3} {'Source':<20} {'Score':>5} {'Status':<14} {'Conf':>5} "
        f"{'T-In':>8} {'T-Out':>8} {'T-Cost':>10} {'T-ms':>7} "
        f"{'Deep':>5} {'D-Cost':>10} {'Total$':>10} {'EA-Status':<12}  Title"
    )
    print(cols)
    print_separator()
    for r in results:
        score = str(r.get("relevance_score") or "â€”")
        status = (r.get("relevance_status") or "â€”")[:13]
        conf = f"{r['confidence']:.2f}" if r.get("confidence") is not None else "â€”"
        t_in = f"{r.get('triage_input_tokens', 0):,}"
        t_out = f"{r.get('triage_output_tokens', 0):,}"
        t_cost = fmt_usd(r.get("triage_cost", 0.0))
        t_ms = fmt_ms(r.get("triage_latency_ms"))
        deep_called = "YES" if r.get("deep_called") else "no"
        d_cost = fmt_usd(r.get("deep_cost", 0.0))
        total_cost = fmt_usd(r.get("total_cost", 0.0))
        ea_status = (r.get("ea_status") or "â€”")[:11]
        source_name = (r.get("source") or "")[:18]
        title = short_title(r.get("title"), 40)
        print(
            f"  {r['idx']:<3} {source_name:<20} {score:>5} {status:<14} {conf:>5} "
            f"{t_in:>8} {t_out:>8} {t_cost:>10} {t_ms:>7} "
            f"{deep_called:>5} {d_cost:>10} {total_cost:>10} {ea_status:<12}  {title}"
        )
    print_separator()


def print_textual_results(results: list[dict]) -> None:
    for r in results:
        if r.get("ea_status") == "failed":
            continue
        relevance = r.get("relevance_status")
        print_separator("â”€")
        print(f"  [{r['idx']}] {r.get('source', '')} â€” {short_title(r.get('title'), 80)}")
        print(f"      Score: {r.get('relevance_score')} | Status: {relevance} | "
              f"Primary topic: {r.get('primary_topic') or 'â€”'}")

        if relevance == "relevant":
            reason = r.get("reason") or "â€”"
            summary = r.get("summary") or "â€”"
            key_points = r.get("key_points") or []
            print(f"\n      Reason:  {reason}")
            print(f"\n      Summary:\n      {summary}")
            if key_points:
                print("\n      Key points:")
                for kp in key_points:
                    print(f"        â€¢ {kp}")
        elif relevance == "uncertain":
            reason = r.get("reason") or "â€”"
            topics = r.get("topic_codes") or []
            print(f"\n      Reason:  {reason}")
            print(f"      Topics:  {', '.join(topics) or 'â€”'}")
        else:
            reason = r.get("reason") or "â€”"
            print(f"\n      Reason:  {reason}")
    print_separator("â”€")


def print_aggregates(results: list[dict], budget_limit: float, budget_stopped: bool) -> None:
    total = len(results)
    relevant = sum(1 for r in results if r.get("relevance_status") == "relevant")
    uncertain = sum(1 for r in results if r.get("relevance_status") == "uncertain")
    not_relevant = sum(1 for r in results if r.get("relevance_status") == "not_relevant")
    failed = sum(1 for r in results if r.get("ea_status") == "failed")
    triage_calls = total
    deep_calls = sum(1 for r in results if r.get("deep_called"))
    total_input_tokens = sum(r.get("triage_input_tokens", 0) + r.get("deep_input_tokens", 0) for r in results)
    total_output_tokens = sum(r.get("triage_output_tokens", 0) + r.get("deep_output_tokens", 0) for r in results)
    total_cost = sum(r.get("total_cost", 0.0) for r in results)
    avg_cost = total_cost / total if total else 0.0

    triage_latencies = [r["triage_latency_ms"] for r in results if r.get("triage_latency_ms")]
    deep_latencies = [r["deep_latency_ms"] for r in results if r.get("deep_latency_ms")]
    avg_triage_lat = sum(triage_latencies) / len(triage_latencies) if triage_latencies else 0
    avg_deep_lat = sum(deep_latencies) / len(deep_latencies) if deep_latencies else 0

    # Cost by source
    cost_by_source: dict[str, float] = {}
    for r in results:
        src = r.get("source") or "unknown"
        cost_by_source[src] = cost_by_source.get(src, 0.0) + r.get("total_cost", 0.0)

    print_separator("â•")
    print("  BENCHMARK AGGREGATES")
    print_separator("â•")
    print(f"  Total analyses:        {total}")
    print(f"  Relevant:              {relevant}")
    print(f"  Uncertain:             {uncertain}")
    print(f"  Not relevant:          {not_relevant}")
    print(f"  Failed:                {failed}")
    print_separator()
    print(f"  Triage calls:          {triage_calls}")
    print(f"  Deep calls:            {deep_calls}")
    print_separator()
    print(f"  Total input tokens:    {total_input_tokens:,}")
    print(f"  Total output tokens:   {total_output_tokens:,}")
    print(f"  Total estimated cost:  {fmt_usd(total_cost)}")
    print(f"  Avg cost per Entry:    {fmt_usd(avg_cost)}")
    print(f"  Avg triage latency:    {avg_triage_lat:.0f}ms")
    print(f"  Avg deep latency:      {avg_deep_lat:.0f}ms")
    print_separator()
    print("  Cost by Source:")
    for src, cost in sorted(cost_by_source.items()):
        print(f"    {src:<40} {fmt_usd(cost)}")
    print_separator()
    print(f"  Budget limit:          {fmt_usd(budget_limit)}")
    print(f"  Budget used:           {fmt_usd(total_cost)}")
    if budget_stopped:
        print("  âš ï¸  BUDGET LIMIT REACHED â€” benchmark stopped early")
    print_separator("â•")


# ---------------------------------------------------------------------------
# Main benchmark runner
# ---------------------------------------------------------------------------

async def run_benchmark(
    real_calls: bool,
    max_usd: float,
    db: Session,
) -> None:
    settings = get_settings()

    # Validate provider
    if real_calls:
        if settings.ANALYSIS_PROVIDER.lower() != "vertex_ai":
            print(
                f"\n  ERROR: --confirm-real-calls requires ANALYSIS_PROVIDER=vertex_ai, "
                f"but current value is '{settings.ANALYSIS_PROVIDER}'.\n"
                "  Update .env and reload the settings cache."
            )
            sys.exit(1)
        if not settings.VERTEX_AI_PROJECT:
            print(
                "\n  ERROR: VERTEX_AI_PROJECT is not configured.\n"
                "  Set VERTEX_AI_PROJECT=<your-gcp-project-id> in .env.\n"
                "  Current active gcloud project can be checked with: gcloud config get-value project"
            )
            sys.exit(1)

    # Select sample
    entries = select_sample(db)
    if not entries:
        print("\n  ERROR: No entries found. Run ingestion first.")
        sys.exit(1)

    print_preflight(settings, entries, max_usd, real_calls)
    print_sample_table(entries)

    if not real_calls:
        print("\n  DRY-RUN MODE â€” No Vertex AI calls will be made.")
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

    # Initialize pipeline
    from app.providers.ai.vertex_ai import VertexAIProvider
    provider = VertexAIProvider(settings=settings)
    pipeline = AnalysisPipelineService(provider=provider)

    benchmark_run_id = str(uuid.uuid4())
    print(f"\n  Benchmark run ID: {benchmark_run_id}")
    print_separator("â•")

    results: list[dict] = []
    accumulated_cost: float = 0.0
    budget_stopped: bool = False

    for idx, entry in enumerate(entries, 1):
        # Budget check BEFORE calling
        if accumulated_cost >= max_usd:
            print(
                f"\n  âš ï¸  BUDGET LIMIT REACHED before Entry #{idx}. "
                f"Accumulated: {fmt_usd(accumulated_cost)} / Limit: {fmt_usd(max_usd)}"
            )
            budget_stopped = True
            break

        print(f"\n  [{idx}/{len(entries)}] {entry.source.name if entry.source else '?'} â€” {short_title(entry.title, 70)}")
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
                "triage_output_tokens": 0,
                "triage_cost": 0.0,
                "triage_latency_ms": None,
                "deep_called": False,
                "deep_input_tokens": 0,
                "deep_output_tokens": 0,
                "deep_cost": 0.0,
                "deep_latency_ms": None,
                "total_cost": 0.0,
                "ea_status": "failed",
            })
            continue

        # Load calls for metrics
        db.refresh(analysis)
        calls = sorted(analysis.calls, key=lambda c: c.created_at)
        triage_call = next((c for c in calls if c.stage == "triage"), None)
        deep_call = next((c for c in calls if c.stage == "deep_analysis"), None)

        triage_in = triage_call.input_tokens or 0 if triage_call else 0
        triage_out = triage_call.output_tokens or 0 if triage_call else 0
        triage_cost = float(triage_call.estimated_cost_usd or 0) if triage_call else 0.0
        triage_lat = triage_call.latency_ms if triage_call else None
        deep_in = deep_call.input_tokens or 0 if deep_call else 0
        deep_out = deep_call.output_tokens or 0 if deep_call else 0
        deep_cost = float(deep_call.estimated_cost_usd or 0) if deep_call else 0.0
        deep_lat = deep_call.latency_ms if deep_call else None
        entry_total_cost = triage_cost + deep_cost

        accumulated_cost += entry_total_cost

        # Load topics for reporting
        topic_codes = [t.topic.code for t in analysis.topics if t.topic]
        primary_topic = next((t.topic.code for t in analysis.topics if t.is_primary and t.topic), None)

        results.append({
            "idx": idx,
            "entry_id": str(entry.id),
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
            "triage_output_tokens": triage_out,
            "triage_cost": triage_cost,
            "triage_latency_ms": triage_lat,
            "deep_called": deep_call is not None,
            "deep_input_tokens": deep_in,
            "deep_output_tokens": deep_out,
            "deep_cost": deep_cost,
            "deep_latency_ms": deep_lat,
            "total_cost": entry_total_cost,
            "ea_status": analysis.status,
        })

        status_icon = {"relevant": "âœ…", "uncertain": "âš ï¸", "not_relevant": "âŒ", None: "?"}.get(analysis.relevance_status, "?")
        print(
            f"         â†’ {status_icon} score={analysis.relevance_score} "
            f"status={analysis.relevance_status} "
            f"cost={fmt_usd(entry_total_cost)} "
            f"budget_used={fmt_usd(accumulated_cost)}"
        )

    # Print results
    print_results_table(results)
    print_textual_results(results)
    print_aggregates(results, max_usd, budget_stopped)

    print(f"\n  âœ… Benchmark complete. Benchmark run ID: {benchmark_run_id}")
    print("  Review results above before running any additional analyses.")
    print("  DO NOT analyze the remaining 60 entries until you have reviewed these results.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="HITCHINGS â€” Controlled AI analysis benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        default=False,
        help=(
            "Actually call Vertex AI. Without this flag, only preflight and sample "
            "selection are shown (dry-run). Requires ANALYSIS_PROVIDER=vertex_ai."
        ),
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=None,
        help="Maximum USD budget. Defaults to ANALYSIS_BENCHMARK_MAX_USD from settings.",
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


