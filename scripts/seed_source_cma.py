"""Idempotent seed script to create or update the official CMA (UK) source.

Connects to the database, checks for an existing TrackedEntity for CMA,
and ensures the official Source exists with proper configuration.

Execution:
    python -m scripts.seed_source_cma
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

CMA_SOURCE_NAME = "Competition and Markets Authority (UK)"
CMA_BASE_URL = "https://www.gov.uk/cma-cases"


def seed_cma_source() -> Source:
    """Create or update the real CMA source idempotently."""
    db = SessionLocal()
    try:
        # 1. Check if a TrackedEntity exists for CMA
        entity = db.execute(
            select(TrackedEntity).where(
                or_(
                    TrackedEntity.display_name.ilike("%competition and markets authority%"),
                    TrackedEntity.display_name == "CMA",
                )
            )
        ).scalar_one_or_none()

        tracked_entity_id = entity.id if entity else None
        if entity:
            logger.info("Found TrackedEntity '%s' (id=%s)", entity.display_name, entity.id)
        else:
            logger.info("No existing TrackedEntity found for CMA; leaving tracked_entity_id as None.")

        # 2. Check if Source already exists (SELECT before INSERT)
        source = db.execute(
            select(Source).where(
                or_(
                    Source.name == CMA_SOURCE_NAME,
                    Source.name.ilike("%competition and markets authority%"),
                    Source.url == CMA_BASE_URL,
                    Source.url.ilike("%cma-cases%"),
                )
            )
        ).scalar_one_or_none()

        default_config = {
            "lookback_days": 8,
            "freshness_warning_hours": 168,
            "organisation": "competition-and-markets-authority",
            "document_types": ["cma_case", "digital_markets_measure"],
            "search_api_url": "https://www.gov.uk/api/search.json",
            "content_api_base": "https://www.gov.uk/api/content",
        }

        if not source:
            source = Source(
                name=CMA_SOURCE_NAME,
                type=SourceType.INSTITUTIONAL,
                provider="native",
                url=CMA_BASE_URL,
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
            source.name = CMA_SOURCE_NAME
            source.type = SourceType.INSTITUTIONAL
            source.provider = "native"
            source.url = CMA_BASE_URL
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
    src = seed_cma_source()
    print("\n--- CMA SOURCE READY ---")
    print(f"  ID: {src.id}")
    print(f"  Name: {src.name}")
    print(f"  Type: {src.type}")
    print(f"  Provider: {src.provider}")
    print(f"  URL: {src.url}")
