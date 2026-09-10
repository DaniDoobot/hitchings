"""CLI command for manual and scheduled execution of the Weekly Source Refresh (Bloque 11C).

Usage:
    # Dry run (default: 0 real scraping, 0 AI calls, 0 DB mutations):
    python -m scripts.weekly_refresh

    # Real execution over all active sources:
    python -m scripts.weekly_refresh --confirm-real-calls

    # Real execution with custom lookback window (e.g. 10 days):
    python -m scripts.weekly_refresh --confirm-real-calls --lookback-days 10

    # Filter to specific sources:
    python -m scripts.weekly_refresh --sources "CNMC - Noticias" "Chillin'Competition"
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.weekly_refresh_service import WeeklyRefreshService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scripts.weekly_refresh")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="HITCHINGS Weekly Source Refresh & Incremental Analysis (Bloque 11C)"
    )
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        default=False,
        help="Confirm real scraping and AI analysis calls (requires API credentials).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Force dry-run simulation mode (0 external calls, 0 DB writes).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="Window of days to look back for recent content (default: 8 days).",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=None,
        help="Optional list of source names or UUIDs to restrict the refresh to.",
    )

    args = parser.parse_args()
    settings = get_settings()

    is_real = args.confirm_real_calls and not args.dry_run

    db = SessionLocal()
    try:
        service = WeeklyRefreshService(settings=settings)
        report = service.run_weekly_refresh(
            db=db,
            lookback_days=args.lookback_days,
            confirm_real_calls=is_real,
            sources_filter=args.sources,
        )

        if report.status == "already_running":
            print("\n[WeeklyRefresh] Notice: A weekly refresh run is already in progress.")
            print("Finished cleanly without overlapping.\n")
            return 0

        # Print standard summary block
        print("\n" + "=" * 60)
        print(report.summary_text())
        print("=" * 60)

        # Print per-source breakdown
        if report.per_source:
            print("\nPer-source breakdown:")
            for s in report.per_source:
                status_label = s.status.upper()
                err_info = f" | errors={len(s.errors)}" if s.errors else ""
                print(
                    f"  - [{status_label}] {s.source_name}: found={s.found}, new={s.new_entries}, "
                    f"dupes={s.duplicates}, duration={s.duration_seconds}s{err_info}"
                )
            print()

        if report.status == "failed":
            logger.error("Weekly refresh finished with status: FAILED")
            return 1

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
