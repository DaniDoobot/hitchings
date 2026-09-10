"""Idempotent seed script to create or update the OECD Competition Law and Policy source.

Connects to the database, locates the 'OECD Competition Law and Policy' TrackedEntity,
and ensures the official OECD Competition source exists with proper configuration.

Execution:
    python -m scripts.seed_source_oecd_competition
"""

from __future__ import annotations

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

OECD_ENTITY_NAME = "OECD Competition Law and Policy"
OECD_SOURCE_NAME = "OECD - Competition Law and Policy"
OECD_PORTAL_URL = "https://www.oecd.org/en/topics/policy-issues/competition.html"
OECD_SERIES_URL = "https://www.oecd.org/en/publications/oecd-roundtables-on-competition-policy-papers_20758677.html"


def seed_oecd_source() -> Source:
    """Create or update the OECD Competition source idempotently."""
    db = SessionLocal()
    try:
        # 1. Locate TrackedEntity
        entity = db.execute(
            select(TrackedEntity).where(TrackedEntity.display_name == OECD_ENTITY_NAME)
        ).scalar_one_or_none()

        if not entity:
            # Fallback search by partial name
            entity = db.execute(
                select(TrackedEntity).where(TrackedEntity.display_name.ilike("%OECD%"))
            ).scalar_one_or_none()

        if not entity:
            raise ValueError(
                f"TrackedEntity '{OECD_ENTITY_NAME}' not found in database. "
                "Please run seed_tracking_v01 first."
            )

        logger.info("Found TrackedEntity '%s' (id=%s, type=%s)", entity.display_name, entity.id, entity.entity_type)

        # 2. Check if Source already exists by name, entity, or URL
        source = db.execute(
            select(Source).where(
                (Source.name == OECD_SOURCE_NAME)
                | (Source.tracked_entity_id == entity.id)
                | (Source.url == OECD_PORTAL_URL)
                | (Source.url == OECD_SERIES_URL)
            )
        ).scalar_one_or_none()

        default_config = {
            "initial_fetch_limit": 20,
            "freshness_warning_hours": 336,
            "issn": "2075-8677",
            "series_url": OECD_SERIES_URL,
            "portal_url": OECD_PORTAL_URL,
        }

        if not source:
            source = Source(
                name=OECD_SOURCE_NAME,
                type=SourceType.INSTITUTIONAL,
                provider="native",
                url=OECD_PORTAL_URL,
                active=True,
                category="institutional",
                tracked_entity_id=entity.id,
                config=default_config,
            )
            db.add(source)
            db.commit()
            db.refresh(source)
            logger.info("Created real Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)
        else:
            # Update existing source idempotently
            source.name = OECD_SOURCE_NAME
            source.type = SourceType.INSTITUTIONAL
            source.provider = "native"
            source.url = OECD_PORTAL_URL
            source.tracked_entity_id = entity.id
            source.active = True
            source.category = "institutional"
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
    try:
        source = seed_oecd_source()
        print(f"Successfully seeded Source: id={source.id}, name='{source.name}', url='{source.url}'")
    except Exception as exc:
        logger.error("Seeding failed: %s", exc)
        sys.exit(1)
