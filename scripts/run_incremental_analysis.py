"""CLI for Incremental AI Analysis (Bloque 9D).

Usage:
    # 1. Dry run (default: 0 Gemini calls, 0 DB writes):
    python -m scripts.run_incremental_analysis

    # 2. Targeted dry run for a single entry:
    python -m scripts.run_incremental_analysis --entry-id <UUID>

    # 3. Real execution (requires --confirm-real-calls):
    python -m scripts.run_incremental_analysis --confirm-real-calls --limit 9 --max-estimated-cost-usd 0.50
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from typing import Optional

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.entry import Entry
from app.models.analysis import AnalysisCall, EntryAnalysis
from app.services.incremental_analysis_planner import IncrementalAnalysisPlanner
from app.services.incremental_analysis_service import IncrementalAnalysisService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("scripts.run_incremental_analysis")


def print_plan_summary(plan, settings, max_cost: float, limit: int) -> None:
    """Format and print the incremental analysis planning summary."""
    print("=" * 80)
    print("HITCHINGS - INCREMENTAL ANALYSIS (BLOQUE 9D)")
    print("=" * 80)
    print(f"AI Provider            : {settings.ANALYSIS_PROVIDER}")
    print(f"Gemini Model           : {settings.GEMINI_MODEL}")
    print(f"Pipeline Version       : v6 (Contiguous-Evidence & Strict Grounding)")
    print(f"Budget Cap Authorized  : ${max_cost:.4f}")
    print(f"Limit Cap (Max Entries): {limit}")
    print("-" * 80)
    print(f"Total Entries Inspected: {plan.total_entries_inspected}")
    print(f"  -> Already Current   : {plan.already_current_count}")
    print(f"  -> Insufficient      : {plan.insufficient_count}")
    print(f"  -> Partial           : {plan.partial_count}")
    print(f"  -> Missing Content   : {plan.missing_content_count}")
    print(f"  -> Stale Reanalyses  : {plan.stale_count}")
    print(f"  -> TOTAL ELIGIBLE    : {plan.eligible_count}")
    print("-" * 80)

    candidates = plan.candidates
    if not candidates:
        print("No candidates eligible for incremental analysis.")
        return

    print(f"Planned Candidates Queue ({len(candidates)} entries scheduled):")
    for i, c in enumerate(candidates, 1):
        pub_str = c.published_at.strftime("%Y-%m-%d %H:%M") if c.published_at else "No Date"
        print(f"  {i}. [{c.source_name}] {c.title[:65]}...")
        print(f"     ID: {c.entry_id} | Published: {pub_str} | Chars: {c.content_chars} | Sufficiency: {c.sufficiency}")
        print(f"     Reason: {c.reason} | Plan: {c.estimated_stage_plan}")

    min_calls = len(candidates)  # at least 1 triage per candidate
    max_calls = len(candidates) * 2  # at most triage + deep
    print("-" * 80)
    print(f"Estimated LLM Calls Queue: Min={min_calls} (all non-relevant), Max={max_calls} (all relevant)")


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="HITCHINGS Incremental AI Analysis (Bloque 9D)")
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        help="Confirm real LLM calls against Gemini Developer API (requires GEMINI_API_KEY).",
    )
    parser.add_argument(
        "--entry-id",
        type=str,
        default=None,
        help="Target a specific entry UUID for incremental analysis.",
    )
    parser.add_argument(
        "--source-id",
        type=str,
        default=None,
        help="Filter candidates by source UUID.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of eligible entries to analyze (default: settings limit).",
    )
    parser.add_argument(
        "--max-estimated-cost-usd",
        type=float,
        default=None,
        help="Hard safety budget limit in USD (default: settings max cost).",
    )

    args = parser.parse_args()
    settings = get_settings()
    db = SessionLocal()

    target_entry_id = uuid.UUID(args.entry_id) if args.entry_id else None
    target_source_id = uuid.UUID(args.source_id) if args.source_id else None
    max_cost = (
        args.max_estimated_cost_usd
        if args.max_estimated_cost_usd is not None
        else settings.INCREMENTAL_ANALYSIS_MAX_ESTIMATED_COST_USD
    )
    limit = (
        args.limit
        if args.limit is not None
        else settings.INCREMENTAL_ANALYSIS_DEFAULT_LIMIT
    )

    try:
        # Generate and display plan
        planner = IncrementalAnalysisPlanner(db=db)
        plan = planner.plan(
            entry_id=target_entry_id,
            source_id=target_source_id,
            limit=limit,
        )
        print_plan_summary(plan, settings, max_cost, limit)

        # DRY RUN MODE
        if not args.confirm_real_calls:
            print("\n[DRY RUN] 0 Gemini API calls were made. 0 database records written.")
            print("To execute real analysis, re-run with:")
            print("  python -m scripts.run_incremental_analysis --confirm-real-calls")
            return 0

        # REAL EXECUTION MODE
        print("\n" + "=" * 80)
        print("EXECUTING REAL INCREMENTAL ANALYSIS (v6 PIPELINE)")
        print("=" * 80)

        service = IncrementalAnalysisService(settings=settings)
        report = await service.execute_incremental_analysis(
            db=db,
            confirm_real_calls=True,
            limit=limit,
            max_estimated_cost_usd=max_cost,
            entry_id=target_entry_id,
            source_id=target_source_id,
        )

        print("\n" + "=" * 80)
        print("INCREMENTAL ANALYSIS EXECUTION REPORT")
        print("=" * 80)
        print(f"Run ID                 : {report.run_id}")
        print(f"Planned Entries        : {report.planned}")
        print(f"Attempted Entries      : {report.attempted}")
        print(f"Completed Successfully : {report.completed}")
        print(f"Failed Entries         : {report.failed}")
        print(f"Skipped Due to Budget  : {report.skipped_budget}")
        print(f"Triage Calls Executed  : {report.triage_calls}")
        print(f"Deep Calls Executed    : {report.deep_calls}")
        print(f"Relevance Breakdown    : {report.relevant} relevant, {report.uncertain} uncertain, {report.not_relevant} not_relevant")
        print(f"Total Estimated Spend  : ${report.estimated_cost:.6f}")
        print(f"Duration               : {(report.finished_at - report.started_at).total_seconds():.2f} seconds")
        print("-" * 80)

        print("Per-Entry Results:")
        for idx, d in enumerate(report.details, 1):
            status = d.get("status")
            rel = d.get("relevance_status") or "N/A"
            score = d.get("relevance_score") or "N/A"
            deep_str = "YES" if d.get("has_deep") else "NO"
            cost_str = f"${d.get('estimated_cost_usd', 0.0):.4f}"
            print(f"  {idx}. [{d.get('source_name')}] {d.get('title', '')[:55]}...")
            print(f"     Status: {status} | Relevance: {rel} (score={score}) | Deep: {deep_str} | Cost: {cost_str}")
            if d.get("error"):
                print(f"     Error: {d.get('error')}")

        return 0 if report.failed == 0 else 1

    finally:
        db.close()


def main() -> None:
    code = asyncio.run(main_async())
    sys.exit(code)


if __name__ == "__main__":
    main()
