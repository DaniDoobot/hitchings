"""Background scheduler daemon for periodic weekly refresh of HITCHINGS Observatorio (Bloque 11C)."""

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
) -> datetime:
    """Compute the next scheduled occurrence in the given timezone.
    
    Guaranteed to return a timestamp in the future (never now or in the past).
    """
    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = ZoneInfo("UTC")

    now_local = now_dt.astimezone(tz)
    target_weekday = DAY_MAP.get(target_day.lower().strip(), 0)

    candidate = now_local.replace(
        hour=target_hour,
        minute=target_minute,
        second=0,
        microsecond=0,
    )

    if now_local.weekday() == target_weekday and now_local < candidate:
        return candidate

    days_ahead = (target_weekday - now_local.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7

    return candidate + timedelta(days=days_ahead)


def run_scheduler_loop(
    stop_event: Optional[threading.Event] = None,
    run_once: bool = False,
) -> None:
    """Run the periodic scheduler loop waiting for each scheduled weekly trigger."""
    settings = get_settings()
    event = stop_event or threading.Event()

    logger.info("Initializing HITCHINGS Weekly Refresh Scheduler...")
    logger.info("  Enabled: %s", settings.WEEKLY_REFRESH_ENABLED)
    logger.info("  Timezone: %s", settings.WEEKLY_REFRESH_TIMEZONE)
    logger.info("  Day: %s, Time: %02d:%02d", settings.WEEKLY_REFRESH_DAY, settings.WEEKLY_REFRESH_HOUR, settings.WEEKLY_REFRESH_MINUTE)
    logger.info("  Lookback window: %d days", settings.WEEKLY_REFRESH_LOOKBACK_DAYS)

    if not settings.WEEKLY_REFRESH_ENABLED:
        logger.warning("Weekly refresh is disabled (WEEKLY_REFRESH_ENABLED=false). Scheduler will idle.")
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
            target_hour=settings.WEEKLY_REFRESH_HOUR,
            target_minute=settings.WEEKLY_REFRESH_MINUTE,
            tz_str=settings.WEEKLY_REFRESH_TIMEZONE,
        )

        sleep_seconds = (next_run - now_utc).total_seconds()
        logger.info(
            "[Scheduler] Next weekly refresh scheduled for %s (%s) [in %.1f hours / %d seconds]",
            next_run.strftime("%Y-%m-%d %H:%M:%S %Z"),
            settings.WEEKLY_REFRESH_TIMEZONE,
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

        # Scheduled moment reached: trigger weekly refresh
        logger.info("[Scheduler] Target scheduled time reached. Triggering weekly refresh...")
        db = SessionLocal()
        try:
            service = WeeklyRefreshService(settings=settings)
            report = service.run_weekly_refresh(
                db=db,
                lookback_days=settings.WEEKLY_REFRESH_LOOKBACK_DAYS,
                confirm_real_calls=True,
            )
            logger.info(
                "[Scheduler] Completed weekly refresh (status=%s, new=%d, analyzed=%d, errors=%d)",
                report.status,
                report.total_new_entries,
                report.total_analyzed,
                report.total_errors,
            )
        except Exception as exc:
            logger.error("[Scheduler] Error executing scheduled weekly refresh: %s", exc, exc_info=True)
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
