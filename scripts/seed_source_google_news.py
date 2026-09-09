"""Idempotent seed script to register Google News as a discovery source in HITCHINGS (Bloque 9A).

Usage:
    python -m scripts.seed_source_google_news
"""

from __future__ import annotations

import logging
import sys

from app.db.session import SessionLocal
from app.models.source import Source, SourceType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def seed_google_news_source() -> int:
    """Ensure the canonical Google News source exists in the database."""
    db = SessionLocal()
    try:
        existing = (
            db.query(Source)
            .filter(Source.type == SourceType.GOOGLE_NEWS)
            .first()
        )
        if existing:
            logger.info(
                "Source 'Google News' already exists (id=%s, type=%s, active=%s). Nothing to do.",
                existing.id,
                existing.type,
                existing.active,
            )
            return 0

        source = Source(
            name="Google News",
            type=SourceType.GOOGLE_NEWS,
            provider="native",
            category="news_aggregator",
            url="https://news.google.com",
            active=True,
            config={
                "discovery": True,
                "languages": ["es", "en"],
                "region": "ES",
                "freshness_warning_hours": 72,
            },
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        logger.info(
            "Created canonical Google News source (id=%s, name='%s', type='%s')",
            source.id,
            source.name,
            source.type,
        )
        return 0
    except Exception as exc:
        db.rollback()
        logger.error("Failed to seed Google News source: %s", exc)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(seed_google_news_source())
