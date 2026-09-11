"""Idempotent seed script to create or update the official Bundeskartellamt source.

Connects to the database, checks for an existing TrackedEntity for Bundeskartellamt,
and ensures the official Source exists with proper configuration.

Execution:
    python -m scripts.seed_source_bundeskartellamt
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, or_
from app.db.session import SessionLocal
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger(__name__)

BUNDESKARTELLAMT_SOURCE_NAME = "Bundeskartellamt"
BUNDESKARTELLAMT_BASE_URL = "https://www.bundeskartellamt.de/"
BUNDESKARTELLAMT_RSS_URL = "https://www.bundeskartellamt.de/DE/Service/RSS/_documents/rssnewsfeed.xml"


def seed_bundeskartellamt_source() -> Source:
    """Create or update the real Bundeskartellamt source idempotently."""
    db = SessionLocal()
    try:
        # 1. Check if a TrackedEntity exists for Bundeskartellamt
        entity = db.execute(
            select(TrackedEntity).where(
                TrackedEntity.display_name.ilike("%bundeskartellamt%")
            )
        ).scalar_one_or_none()

        tracked_entity_id = entity.id if entity else None
        if entity:
            logger.info("Found TrackedEntity '%s' (id=%s)", entity.display_name, entity.id)
        else:
            logger.info("No existing TrackedEntity found for Bundeskartellamt; leaving tracked_entity_id as None.")

        # 2. Check if Source already exists (SELECT before INSERT)
        source = db.execute(
            select(Source).where(
                or_(
                    Source.name == BUNDESKARTELLAMT_SOURCE_NAME,
                    Source.name.ilike("%bundeskartellamt%"),
                    Source.url == BUNDESKARTELLAMT_BASE_URL,
                    Source.url.ilike("%bundeskartellamt.de%"),
                )
            )
        ).scalar_one_or_none()

        default_config = {
            "initial_fetch_limit": 30,
            "freshness_warning_hours": 168,
            "rss_url": BUNDESKARTELLAMT_RSS_URL,
            "lookback_days": 8,
        }

        if not source:
            source = Source(
                name=BUNDESKARTELLAMT_SOURCE_NAME,
                type=SourceType.INSTITUTIONAL,
                provider="native",
                url=BUNDESKARTELLAMT_BASE_URL,
                active=True,
                category="institutional",
                tracked_entity_id=tracked_entity_id,
                config=default_config,
            )
            db.add(source)
            db.commit()
            db.refresh(source)
            logger.info("Created real Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)
        else:
            source.name = BUNDESKARTELLAMT_SOURCE_NAME
            source.type = SourceType.INSTITUTIONAL
            source.provider = "native"
            source.url = BUNDESKARTELLAMT_BASE_URL
            source.active = True
            source.category = "institutional"
            if tracked_entity_id:
                source.tracked_entity_id = tracked_entity_id
            new_config = dict(source.config) if (source.config and isinstance(source.config, dict)) else {}
            new_config.update(default_config)
            source.config = new_config
            db.commit()
            db.refresh(source)
            logger.info("Updated existing Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)

        return source

    finally:
        db.close()


if __name__ == "__main__":
    src = seed_bundeskartellamt_source()
    print("\n--- BUNDESKARTELLAMT SOURCE READY ---")
    print(f"  ID: {src.id}")
    print(f"  Name: {src.name}")
    print(f"  Type: {src.type}")
    print(f"  Provider: {src.provider}")
    print(f"  URL: {src.url}")
    print(f"  Tracked Entity ID: {src.tracked_entity_id}")
    print(f"  Config: {src.config}")
