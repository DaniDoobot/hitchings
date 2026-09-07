"""Idempotent seed script to create or update the European Commission DG Competition source.

Connects to the database, locates the 'European Commission' TrackedEntity (ID 002dde9c-af40-443c-80f4-c9eca5b9f57f),
and ensures the official Competition Policy RSS source exists with proper configuration.

Execution:
    python -m scripts.seed_source_european_commission
"""

import logging
import sys
import uuid
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger(__name__)

EC_ENTITY_NAME = "European Commission"
EC_EXPECTED_ENTITY_ID = uuid.UUID("002dde9c-af40-443c-80f4-c9eca5b9f57f")
EC_SOURCE_NAME = "European Commission - Competition Policy"
EC_RSS_URL = "https://competition-policy.ec.europa.eu/node/38/rss_en"


def seed_ec_source() -> Source:
    """Create or update the European Commission competition source idempotently."""
    db = SessionLocal()
    try:
        # 1. Locate European Commission TrackedEntity
        entity = db.get(TrackedEntity, EC_EXPECTED_ENTITY_ID)
        if not entity:
            entity = db.execute(
                select(TrackedEntity).where(TrackedEntity.display_name == EC_ENTITY_NAME)
            ).scalar_one_or_none()

        if not entity:
            raise ValueError(
                f"TrackedEntity '{EC_ENTITY_NAME}' not found. Please run seed_tracking_v01 first."
            )

        logger.info("Found TrackedEntity '%s' (id=%s, type=%s)", entity.display_name, entity.id, entity.entity_type)

        # 2. Check if Source already exists by name, entity, or URL
        source = db.execute(
            select(Source).where(
                (Source.name == EC_SOURCE_NAME)
                | (Source.tracked_entity_id == entity.id)
                | (Source.url == EC_RSS_URL)
            )
        ).scalar_one_or_none()

        if not source:
            source = Source(
                name=EC_SOURCE_NAME,
                type=SourceType.RSS,
                provider="native",
                url=EC_RSS_URL,
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
            # Update existing source idempotently
            source.name = EC_SOURCE_NAME
            source.type = SourceType.RSS
            source.provider = "native"
            source.url = EC_RSS_URL
            source.tracked_entity_id = entity.id
            source.active = True
            source.category = "institutional"
            new_config = dict(source.config) if (source.config and isinstance(source.config, dict)) else {}
            new_config["initial_fetch_limit"] = 20
            new_config["freshness_warning_hours"] = 168
            source.config = new_config
            db.commit()
            db.refresh(source)
            logger.info("Updated existing Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)

        return source

    finally:
        db.close()


if __name__ == "__main__":
    src = seed_ec_source()
    print("\n--- EUROPEAN COMMISSION SOURCE READY ---")
    print(f"  ID: {src.id}")
    print(f"  Name: {src.name}")
    print(f"  Type: {src.type}")
    print(f"  Provider: {src.provider}")
    print(f"  URL: {src.url}")
    print(f"  Tracked Entity ID: {src.tracked_entity_id}")
    print(f"  Config: {src.config}")
