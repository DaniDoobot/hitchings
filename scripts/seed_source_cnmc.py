"""Idempotent seed script to create or update the real CNMC News RSS source.

Connects to the database, locates the 'Comisión Nacional de los Mercados y la Competencia'
TrackedEntity, and ensures the official RSS source exists with proper configuration.

Execution:
    python -m scripts.seed_source_cnmc
"""

import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger(__name__)

CNMC_ENTITY_NAME = "Comisión Nacional de los Mercados y la Competencia"
CNMC_SOURCE_NAME = "CNMC - Noticias"
CNMC_WEBSITE_URL = "https://www.cnmc.es/prensa/noticias"
CNMC_LEGACY_RSS_URL = "https://www.cnmc.es/feed/prensa/noticias"


def seed_cnmc_source() -> Source:
    """Create or update the real CNMC website source idempotently."""
    db = SessionLocal()
    try:
        # 1. Locate CNMC TrackedEntity
        entity = db.execute(
            select(TrackedEntity).where(TrackedEntity.display_name == CNMC_ENTITY_NAME)
        ).scalar_one_or_none()

        if not entity:
            raise ValueError(
                f"TrackedEntity '{CNMC_ENTITY_NAME}' not found. Please run seed_tracking_v01 first."
            )

        logger.info("Found TrackedEntity '%s' (id=%s)", entity.display_name, entity.id)

        # 2. Check if Source already exists by name, entity, or URLs
        source = db.execute(
            select(Source).where(
                (Source.name == CNMC_SOURCE_NAME)
                | (Source.tracked_entity_id == entity.id)
                | (Source.url == CNMC_WEBSITE_URL)
                | (Source.url == CNMC_LEGACY_RSS_URL)
            )
        ).scalar_one_or_none()

        if not source:
            source = Source(
                name=CNMC_SOURCE_NAME,
                type=SourceType.WEBSITE,
                provider="native",
                url=CNMC_WEBSITE_URL,
                active=True,
                category="institutional",
                tracked_entity_id=entity.id,
                config={
                    "initial_fetch_limit": 20,
                    "freshness_warning_hours": 168,
                },
            )
            db.add(source)
            db.commit()
            db.refresh(source)
            logger.info("Created real Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)
        else:
            # Cleanly evolve existing source to website type and current official URL
            source.name = CNMC_SOURCE_NAME
            source.type = SourceType.WEBSITE
            source.provider = "native"
            source.url = CNMC_WEBSITE_URL
            source.tracked_entity_id = entity.id
            source.active = True
            source.category = "institutional"
            new_config = dict(source.config) if (source.config and isinstance(source.config, dict)) else {}
            new_config["initial_fetch_limit"] = 20
            new_config["freshness_warning_hours"] = 168
            source.config = new_config
            db.commit()
            db.refresh(source)
            logger.info("Updated existing Source '%s' to website type (id=%s, url=%s)", source.name, source.id, source.url)

        return source

    finally:
        db.close()


if __name__ == "__main__":
    src = seed_cnmc_source()
    print("\n--- CNMC SOURCE READY ---")
    print(f"  ID: {src.id}")
    print(f"  Name: {src.name}")
    print(f"  Type: {src.type}")
    print(f"  Provider: {src.provider}")
    print(f"  URL: {src.url}")
    print(f"  Tracked Entity ID: {src.tracked_entity_id}")
    print(f"  Config: {src.config}")
