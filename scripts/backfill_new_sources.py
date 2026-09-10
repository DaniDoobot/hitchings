"""CLI Runner for Controlled New Sources Backfill (Bloque 12G).

Coordinates discovery, idempotent persistence, and selective v6 AI analysis
for the three new sources:
1. Geradin Partners - EU Competition & Litigation
2. European Commission - Digital Markets Act
3. OECD - Competition Law and Policy

Usage:
    # 1. Dry run (default: 0 HTTP, 0 DB writes, 0 Gemini calls):
    python -m scripts.backfill_new_sources --lookback-days 90

    # 2. Targeted dry run for a single source:
    python -m scripts.backfill_new_sources --source geradin --lookback-days 90

    # 3. Real controlled execution:
    python -m scripts.backfill_new_sources \
      --lookback-days 90 \
      --confirm-real-calls \
      --max-new-entries 30 \
      --max-analysis-calls 15
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.db.session import SessionLocal
from app.services.new_sources_backfill_service import (
    NewSourcesBackfillReport,
    NewSourcesBackfillService,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("scripts.backfill_new_sources")


def print_backfill_report(report: NewSourcesBackfillReport) -> None:
    """Format and print comprehensive backfill metrics and inventory counts."""
    print("\n" + "=" * 95)
    print("  HITCHINGS - CONTROLLED NEW SOURCES BACKFILL REPORT (BLOQUE 12G)")
    print("=" * 95)
    print(f"  Run ID                 : {report.run_id}")
    print(f"  Status                 : {report.status.upper()}")
    if report.guard_triggered:
        print(f"  Guard Notice           : {report.guard_triggered}")
    print(f"  Mode                   : {'REAL EXECUTION' if not report.is_dry_run else 'DRY RUN (0 calls, 0 writes)'}")
    print(f"  Lookback Window        : {report.lookback_days} days")
    print(f"  Active Matrix          : {report.active_matrix_code or 'None'}")
    print(f"  Max New Entries Guard  : {report.max_new_entries}")
    print(f"  Max Analysis Entries   : {report.max_analysis_entries}")
    print(f"  Max LLM Calls Guard    : {report.max_analysis_calls}")
    print(f"  Actual LLM Calls Made  : {report.actual_analysis_calls}")
    print(f"  Duration               : {report.duration_seconds}s")
    print("-" * 95)

    print("\n  DESGLOSE POR FUENTE:")
    for s in report.per_source:
        print(f"  [{s.source_name}]")
        print(f"    Status               : {s.status}")
        print(f"    Discovered           : {s.discovered}")
        print(f"    Duplicates           : {s.duplicates}")
        print(f"    Created              : {s.created}")
        print(f"    FULL                 : {s.full_count}")
        print(f"    PARTIAL              : {s.partial_count}")
        print(f"    INSUFFICIENT         : {s.insufficient_count}")
        print(f"    Eligible for Analysis: {s.eligible}")
        print(f"    Analyzed (AI)        : {s.analyzed}")
        print(f"    Analysis Failed      : {s.analysis_failed}")
        print(f"    Ingestion Failed     : {s.ingestion_failed}")
        if s.errors:
            print(f"    Errors ({len(s.errors)})      : {s.errors[:3]}")

    print("\n" + "=" * 95)
    print("  TOTAL GLOBAL")
    print("-" * 95)
    print(f"  Sources Processed      : {report.sources_processed}")
    print(f"  Entries Created        : {report.entries_created}")
    print(f"  Duplicates Skipped     : {report.duplicates}")
    print(f"  Potential Analysis     : {report.potential_analysis}")
    print(f"  Analysis Completed     : {report.analysis_completed}")
    print(f"  Analysis Failed        : {report.analysis_failed}")
    print(f"  Actual LLM Calls Made  : {report.actual_analysis_calls}")
    print("-" * 95)

    if report.db_counts_before and report.db_counts_after:
        cb = report.db_counts_before
        ca = report.db_counts_after
        print("\n  DATABASE INVENTORY COUNTS (ANTES vs DESPUÉS):")
        print(f"    Sources              : {cb.sources:4d}  -> {ca.sources:4d}  (delta: +{ca.sources - cb.sources})")
        print(f"    Entries              : {cb.entries:4d}  -> {ca.entries:4d}  (delta: +{ca.entries - cb.entries})")
        print(f"    EntryAnalysis        : {cb.entry_analyses:4d}  -> {ca.entry_analyses:4d}  (delta: +{ca.entry_analyses - cb.entry_analyses})")
        print(f"    AnalysisCalls        : {cb.analysis_calls:4d}  -> {ca.analysis_calls:4d}  (delta: +{ca.analysis_calls - cb.analysis_calls})")
        print(f"    IngestionRuns        : {cb.ingestion_runs:4d}  -> {ca.ingestion_runs:4d}  (delta: +{ca.ingestion_runs - cb.ingestion_runs})")
    print("=" * 95 + "\n")


async def main_async() -> int:
    parser = argparse.ArgumentParser(
        description="Controlled backfill runner for new sources (Geradin, DMA, OECD) - Bloque 12G",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=90,
        help="Lookback window in days for discovering recent publications (default: 90).",
    )
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        help="Required confirmation flag to execute real discovery, DB persistence, and Gemini analysis.",
    )
    parser.add_argument(
        "--source",
        choices=["geradin", "dma", "oecd"],
        default=None,
        help="Optional single source filter: 'geradin', 'dma', or 'oecd' (default: all three).",
    )
    parser.add_argument(
        "--max-new-entries",
        type=int,
        default=30,
        help="Safety volume circuit breaker: aborts before persistence if new items exceed this limit (default: 30).",
    )
    parser.add_argument(
        "--max-analysis-entries",
        type=int,
        default=15,
        help="Safety cost circuit breaker: aborts before calling Gemini if eligible entries exceed this limit (default: 15).",
    )
    parser.add_argument(
        "--max-analysis-calls",
        type=int,
        default=30,
        help="Safety cost circuit breaker: aborts if actual LLM provider calls exceed this limit (default: 30).",
    )

    args = parser.parse_args()

    db = SessionLocal()
    try:
        service = NewSourcesBackfillService()
        report = await service.execute_backfill(
            db=db,
            lookback_days=args.lookback_days,
            confirm_real_calls=args.confirm_real_calls,
            source_filter=args.source,
            max_new_entries=args.max_new_entries,
            max_analysis_entries=args.max_analysis_entries,
            max_analysis_calls=args.max_analysis_calls,
        )
        print_backfill_report(report)

        if report.status in {"aborted_max_new_entries", "aborted_max_analysis_calls", "failed"}:
            return 1
        return 0

    finally:
        db.close()


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
