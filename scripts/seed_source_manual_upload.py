"""Idempotent seed script to register canonical manual upload source in HITCHINGS (Bloque 11C).

Usage:
    python -m scripts.seed_source_manual_upload
"""

from __future__ import annotations

import logging
import sys

from app.db.session import SessionLocal
from app.models.source import (
    Source,
    SourceType,
    MANUAL_UPLOAD_SOURCE_ID,
    MANUAL_UPLOAD_SOURCE_NAME,
    MANUAL_UPLOAD_SOURCE_CODE,
    MANUAL_UPLOAD_SOURCE_URL,
    get_or_create_manual_source,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def seed_manual_upload_source() -> int:
    """Ensure the canonical manual document upload source exists in the database."""
    db = SessionLocal()
    try:
        source = get_or_create_manual_source(db)
        db.commit()
        logger.info(
            "Canonical manual source verified (id=%s, name='%s', type='%s', active=%s)",
            source.id,
            source.name,
            source.type,
            source.active,
        )
        return 0
    except Exception as exc:
        db.rollback()
        logger.error("Failed to seed manual upload source: %s", exc)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(seed_manual_upload_source())
