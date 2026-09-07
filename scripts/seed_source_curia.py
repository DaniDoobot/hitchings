"""Idempotent seed script to create or update the CJEU / CURIA case law source.

Connects to the database, locates 'El Tribunal de Justicia de la Unión Europea' TrackedEntity
(ID 46c36f17-fca8-49ae-ad5c-080d5492b400), and ensures the official Case Law website
source exists with proper configuration.

Execution:
    python -m scripts.seed_source_curia
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

CURIA_ENTITY_NAME = "El Tribunal de Justicia de la Unión Europea"
CURIA_EXPECTED_ENTITY_ID = uuid.UUID("46c36f17-fca8-49ae-ad5c-080d5492b400")
CURIA_SOURCE_NAME = "Court of Justice of the European Union - Case Law"
CURIA_WEBSITE_URL = "https://infocuria.curia.europa.eu/tabs/jurisprudence"


def seed_curia_source() -> Source:
    """Create or update the CJEU / CURIA source idempotently."""
    db = SessionLocal()
    try:
        # 1. Locate TrackedEntity
        entity = db.get(TrackedEntity, CURIA_EXPECTED_ENTITY_ID)
        if not entity:
            entity = db.execute(
                select(TrackedEntity).where(TrackedEntity.display_name == CURIA_ENTITY_NAME)
            ).scalar_one_or_none()

        if not entity:
            raise ValueError(
                f"TrackedEntity '{CURIA_ENTITY_NAME}' not found. Please run seed_tracking_v01 first."
            )

        logger.info("Found TrackedEntity '%s' (id=%s, type=%s)", entity.display_name, entity.id, entity.entity_type)

        # 2. Check if Source already exists by name, entity, or URL
        source = db.execute(
            select(Source).where(
                (Source.name == CURIA_SOURCE_NAME)
                | (Source.tracked_entity_id == entity.id)
                | (Source.url == CURIA_WEBSITE_URL)
                | (Source.url == "https://curia.europa.eu/jurisprudence")
            )
        ).scalar_one_or_none()

        if not source:
            source = Source(
                name=CURIA_SOURCE_NAME,
                type=SourceType.WEBSITE,
                provider="native",
                url=CURIA_WEBSITE_URL,
                active=True,
                category="institutional",
                tracked_entity_id=entity.id,
                config={
                    "initial_fetch_limit": 20,
                    "freshness_warning_hours": 336,
                },
            )
            db.add(source)
            db.commit()
            db.refresh(source)
            logger.info("Created real Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)
        else:
            source.name = CURIA_SOURCE_NAME
            source.type = SourceType.WEBSITE
            source.provider = "native"
            source.url = CURIA_WEBSITE_URL
            source.tracked_entity_id = entity.id
            source.active = True
            source.category = "institutional"
            new_config = dict(source.config) if (source.config and isinstance(source.config, dict)) else {}
            new_config["initial_fetch_limit"] = 20
            new_config["freshness_warning_hours"] = 336
            source.config = new_config
            db.commit()
            db.refresh(source)
            logger.info("Updated existing Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)

        return source

    finally:
        db.close()


if __name__ == "__main__":
    src = seed_curia_source()
    print("\n--- CJEU / CURIA SOURCE READY ---")
    print(f"  ID: {src.id}")
    print(f"  Name: {src.name}")
    print(f"  Type: {src.type}")
    print(f"  Provider: {src.provider}")
    print(f"  URL: {src.url}")
    print(f"  Tracked Entity ID: {src.tracked_entity_id}")
    print(f"  Config: {src.config}")
