"""PostgreSQL advisory lock manager with fallback for test environments."""

import logging
import threading
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Deterministic 64-bit signed integer key for HITCHINGS weekly refresh
WEEKLY_REFRESH_LOCK_KEY: int = 849372019482710481

# In-memory lock registry for SQLite / non-PostgreSQL testing environments
_MEMORY_LOCKS: set[int] = set()
_MEMORY_LOCK_MUTEX = threading.Lock()


def _is_postgres(db: Session) -> bool:
    """Check if the session bind dialect is PostgreSQL."""
    try:
        bind = db.get_bind()
        return bind.dialect.name == "postgresql"
    except Exception:
        return False


class RefreshAdvisoryLock:
    """Acquires a global session-level PostgreSQL advisory lock, or in-memory lock for testing."""

    def __init__(self, db: Session, lock_key: int = WEEKLY_REFRESH_LOCK_KEY) -> None:
        self.db = db
        self.lock_key = lock_key
        self.is_acquired = False
        self.is_postgres = _is_postgres(db)

    def acquire(self) -> bool:
        """Attempt to acquire the advisory lock non-blockingly.
        
        Returns True if acquired, False if already held by another process.
        """
        if self.is_postgres:
            try:
                res = self.db.execute(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": self.lock_key},
                ).scalar()
                self.is_acquired = bool(res)
                if self.is_acquired:
                    logger.info("Acquired PostgreSQL advisory lock (key=%d)", self.lock_key)
                else:
                    logger.warning("PostgreSQL advisory lock (key=%d) is already held", self.lock_key)
                return self.is_acquired
            except Exception as exc:
                logger.error("Error attempting to acquire PostgreSQL advisory lock: %s", exc)
                return False
        else:
            with _MEMORY_LOCK_MUTEX:
                if self.lock_key in _MEMORY_LOCKS:
                    logger.warning("In-memory advisory lock (key=%d) is already held", self.lock_key)
                    self.is_acquired = False
                    return False
                _MEMORY_LOCKS.add(self.lock_key)
                self.is_acquired = True
                logger.info("Acquired in-memory advisory lock (key=%d)", self.lock_key)
                return True

    def release(self) -> None:
        """Release the advisory lock if held."""
        if not self.is_acquired:
            return

        if self.is_postgres:
            try:
                self.db.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": self.lock_key},
                )
                logger.info("Released PostgreSQL advisory lock (key=%d)", self.lock_key)
            except Exception as exc:
                logger.warning("Error releasing PostgreSQL advisory lock: %s", exc)
        else:
            with _MEMORY_LOCK_MUTEX:
                _MEMORY_LOCKS.discard(self.lock_key)
                logger.info("Released in-memory advisory lock (key=%d)", self.lock_key)

        self.is_acquired = False

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type: Optional[type], exc_val: Optional[BaseException], exc_tb: Optional[object]) -> None:
        self.release()
