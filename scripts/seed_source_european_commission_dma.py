"""Idempotent seed script to create or update the European Commission Digital Markets Act source.

Connects to the database, locates the 'European Commission / Digital Markets Act' TrackedEntity,
and ensures the official DMA direct website source exists with proper configuration.

Execution:
    python -m scripts.seed_source_european_commission_dma
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

DMA_ENTITY_NAME = "European Commission / Digital Markets Act"
DMA_FALLBACK_ENTITY_NAME = "European Commission"
DMA_SOURCE_NAME = "European Commission - Digital Markets Act"
DMA_WEBSITE_URL = "https://digital-markets-act.ec.europa.eu/news_en"


def seed_ec_dma_source() -> Source:
    """Create or update the European Commission DMA source idempotently."""
    db = SessionLocal()
    try:
        # 1. Locate TrackedEntity
        entity = db.execute(
            select(TrackedEntity).where(TrackedEntity.display_name == DMA_ENTITY_NAME)
        ).scalar_one_or_none()

        if not entity:
            entity = db.execute(
                select(TrackedEntity).where(TrackedEntity.display_name == DMA_FALLBACK_ENTITY_NAME)
            ).scalar_one_or_none()

        if not entity:
            raise ValueError(
                f"Neither TrackedEntity '{DMA_ENTITY_NAME}' nor '{DMA_FALLBACK_ENTITY_NAME}' found. "
                "Please run seed_tracking_v01 first."
            )

        logger.info("Found TrackedEntity '%s' (id=%s, type=%s)", entity.display_name, entity.id, entity.entity_type)

        # 2. Check if Source already exists by name, entity, or URL
        source = db.execute(
            select(Source).where(
                (Source.name == DMA_SOURCE_NAME)
                | (Source.tracked_entity_id == entity.id)
                | (Source.url == DMA_WEBSITE_URL)
                | (Source.url == "https://digital-markets-act.ec.europa.eu/latest-news_en")
            )
        ).scalar_one_or_none()

        if not source:
            source = Source(
                name=DMA_SOURCE_NAME,
                type=SourceType.WEBSITE,
                provider="native",
                url=DMA_WEBSITE_URL,
                active=True,
                category="institutional",
                tracked_entity_id=entity.id,
                config={
                    "initial_fetch_limit": 20,
                    "freshness_warning_hours": 168,
                    "portal_url": "https://digital-markets-act.ec.europa.eu/",
                },
            )
            db.add(source)
            db.commit()
            db.refresh(source)
            logger.info("Created real Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)
        else:
            # Update existing source idempotently
            source.name = DMA_SOURCE_NAME
            source.type = SourceType.WEBSITE
            source.provider = "native"
            source.url = DMA_WEBSITE_URL
            source.tracked_entity_id = entity.id
            source.active = True
            source.category = "institutional"
            new_config = dict(source.config) if (source.config and isinstance(source.config, dict)) else {}
            new_config["initial_fetch_limit"] = 20
            new_config["freshness_warning_hours"] = 168
            new_config["portal_url"] = "https://digital-markets-act.ec.europa.eu/"
            source.config = new_config
            db.commit()
            db.refresh(source)
            logger.info("Updated existing Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)

        return source

    finally:
        db.close()


if __name__ == "__main__":
    try:
        source = seed_ec_dma_source()
        print(f"Successfully seeded Source: id={source.id}, name='{source.name}', url='{source.url}'")
    except Exception as exc:
        logger.error("Seeding failed: %s", exc)
        sys.exit(1)
