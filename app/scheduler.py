"""Background scheduler daemon for periodic refresh of HITCHINGS Observatorio (Bloque 10 / Bloque 11C)."""

import logging
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.weekly_refresh_service import WeeklyRefreshService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app.scheduler")

DAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def compute_next_run(
    now_dt: datetime,
    target_day: str = "monday",
    target_hour: int = 6,
    target_minute: int = 0,
    tz_str: str = "Europe/Madrid",
    cadence: str = "weekly",
) -> datetime:
    """Compute the next scheduled occurrence in the given timezone.

    Supports both 'daily' and 'weekly' cadences.
    Guaranteed to return a timestamp strictly in the future (never now or in the past).
    """
    # Auto-detect if caller passed cadence positionally as target_day:
    # e.g., compute_next_run(now_dt, "daily", 6, 0)
    clean_target_day = target_day.lower().strip()
    effective_cadence = cadence.lower().strip()
    if clean_target_day in ("daily", "weekly") and cadence == "weekly":
        effective_cadence = clean_target_day
        clean_target_day = "monday"

    if effective_cadence not in ("daily", "weekly"):
        raise ValueError(f"Invalid cadence: '{cadence}'. Must be 'daily' or 'weekly'.")

    if not (0 <= target_hour <= 23):
        raise ValueError(f"Invalid target_hour: {target_hour}. Must be between 0 and 23.")
    if not (0 <= target_minute <= 59):
        raise ValueError(f"Invalid target_minute: {target_minute}. Must be between 0 and 59.")

    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = ZoneInfo("UTC")

    now_local = now_dt.astimezone(tz)
    candidate = now_local.replace(
        hour=target_hour,
        minute=target_minute,
        second=0,
        microsecond=0,
    )

    if effective_cadence == "daily":
        if now_local < candidate:
            target_date = now_local.date()
        else:
            target_date = now_local.date() + timedelta(days=1)

        return datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            target_hour,
            target_minute,
            0,
            0,
            tzinfo=tz,
        )

    # Weekly cadence
    target_weekday = DAY_MAP.get(clean_target_day)
    if target_weekday is None:
        raise ValueError(f"Invalid target_day: '{target_day}'. Must be one of {list(DAY_MAP.keys())}.")

    if now_local.weekday() == target_weekday and now_local < candidate:
        target_date = now_local.date()
    else:
        days_ahead = (target_weekday - now_local.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_date = now_local.date() + timedelta(days=days_ahead)

    return datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        target_hour,
        target_minute,
        0,
        0,
        tzinfo=tz,
    )


def run_scheduler_loop(
    stop_event: Optional[threading.Event] = None,
    run_once: bool = False,
) -> None:
    """Run the periodic scheduler loop waiting for each scheduled trigger (daily or weekly)."""
    settings = get_settings()
    event = stop_event or threading.Event()

    cadence = settings.SCHEDULER_CADENCE.lower().strip()
    is_enabled = settings.scheduler_is_enabled
    tz_str = settings.scheduler_timezone

    if cadence == "daily":
        target_hour = settings.SCHEDULER_DAILY_HOUR
        target_minute = settings.SCHEDULER_DAILY_MINUTE
        lookback_days = settings.SCHEDULER_DAILY_LOOKBACK_DAYS
        schedule_desc = f"every day at {target_hour:02d}:{target_minute:02d} ({tz_str})"
    else:
        target_hour = settings.WEEKLY_REFRESH_HOUR
        target_minute = settings.WEEKLY_REFRESH_MINUTE
        lookback_days = settings.WEEKLY_REFRESH_LOOKBACK_DAYS
        schedule_desc = f"every {settings.WEEKLY_REFRESH_DAY} at {target_hour:02d}:{target_minute:02d} ({tz_str})"

    logger.info("Initializing HITCHINGS Source Refresh Scheduler...")
    logger.info("  Cadence: %s (%s)", cadence.upper(), schedule_desc)
    logger.info("  Enabled: %s", is_enabled)
    logger.info("  Timezone: %s", tz_str)
    logger.info("  Lookback window: %d days", lookback_days)

    if not is_enabled:
        logger.warning("Scheduler is disabled. Daemon will idle.")
        while not event.is_set():
            event.wait(timeout=60)
        logger.info("Scheduler stopped.")
        return

    # Calculate next execution time immediately (does NOT execute on startup)
    while not event.is_set():
        now_utc = datetime.now(timezone.utc)
        next_run = compute_next_run(
            now_dt=now_utc,
            target_day=settings.WEEKLY_REFRESH_DAY,
            target_hour=target_hour,
            target_minute=target_minute,
            tz_str=tz_str,
            cadence=cadence,
        )

        sleep_seconds = (next_run - now_utc).total_seconds()
        logger.info(
            "[Scheduler] Next %s refresh scheduled for %s (%s) [in %.1f hours / %d seconds]",
            cadence,
            next_run.strftime("%Y-%m-%d %H:%M:%S %Z"),
            tz_str,
            sleep_seconds / 3600.0,
            int(sleep_seconds),
        )

        # Sleep in chunks to allow responsive shutdown signals
        while sleep_seconds > 0 and not event.is_set():
            chunk = min(sleep_seconds, 10.0)
            event.wait(timeout=chunk)
            now_utc = datetime.now(timezone.utc)
            sleep_seconds = (next_run - now_utc).total_seconds()

        if event.is_set():
            break

        # Scheduled moment reached: trigger refresh
        logger.info("[Scheduler] Target scheduled time reached. Triggering %s refresh...", cadence)
        db = SessionLocal()
        try:
            service = WeeklyRefreshService(settings=settings)
            report = service.run_weekly_refresh(
                db=db,
                lookback_days=lookback_days,
                confirm_real_calls=True,
                cadence=cadence,
            )
            logger.info(
                "[Scheduler] Completed %s refresh (status=%s, new=%d, analyzed=%d, errors=%d)",
                cadence,
                report.status,
                report.total_new_entries,
                report.total_analyzed,
                report.total_errors,
            )
        except Exception as exc:
            logger.error("[Scheduler] Error executing scheduled %s refresh: %s", cadence, exc, exc_info=True)
        finally:
            db.close()

        if run_once:
            break

    logger.info("Scheduler daemon shut down cleanly.")


def main() -> int:
    stop_event = threading.Event()

    def handle_signal(sig: int, frame: object) -> None:
        logger.info("Received termination signal (%s). Shutting down scheduler...", sig)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        run_scheduler_loop(stop_event=stop_event)
        return 0
    except Exception as exc:
        logger.critical("Fatal scheduler crash: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
