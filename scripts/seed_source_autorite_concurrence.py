"""Idempotent seed script to create or update the official Autorité de la concurrence (France) source.

Connects to the database, checks for an existing TrackedEntity for Autorité de la concurrence,
and ensures the official Source exists with proper configuration.

Execution:
    python -m scripts.seed_source_autorite_concurrence [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, or_
from app.db.session import SessionLocal
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity
from app.providers.extractors.autorite_concurrence import ADLC_SOURCE_NAME, ADLC_BASE_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_ADLC_CONFIG = {
    "lookback_days": 8,
    "freshness_warning_hours": 168,
    "base_url": ADLC_BASE_URL,
    "max_pages_per_section": 10,
    "sections": ["decisions", "concentrations", "avis", "communiques"],
    "fetch_pdf_for_complex_mergers": True,
    "max_pdf_bytes": 20 * 1024 * 1024,
    "max_pdf_pages_extract": 30,
    "max_pdf_chars_extract": 60000,
}


def seed_autorite_concurrence_source(dry_run: bool = False) -> Source | None:
    """Create or update the real Autorité de la concurrence source idempotently."""
    db = SessionLocal()
    try:
        # 1. Check if a TrackedEntity exists for ADLC
        entity = db.execute(
            select(TrackedEntity).where(
                or_(
                    TrackedEntity.display_name.ilike("%autorit%concurrence%"),
                    TrackedEntity.display_name == "ADLC",
                )
            )
        ).scalar_one_or_none()

        tracked_entity_id = entity.id if entity else None
        if entity:
            logger.info("Found TrackedEntity '%s' (id=%s)", entity.display_name, entity.id)
        else:
            logger.info("No existing TrackedEntity found for ADLC; leaving tracked_entity_id as None.")

        # 2. Check if Source already exists (SELECT before INSERT)
        source = db.execute(
            select(Source).where(
                or_(
                    Source.name == ADLC_SOURCE_NAME,
                    Source.name.ilike("%autorit%concurrence%"),
                    Source.url == ADLC_BASE_URL,
                    Source.url.ilike("%autoritedelaconcurrence.fr%"),
                )
            )
        ).scalar_one_or_none()

        if dry_run:
            logger.info(
                "[DRY RUN] Would %s source '%s' (url=%s, tracked_entity_id=%s)",
                "update" if source else "create",
                ADLC_SOURCE_NAME,
                ADLC_BASE_URL,
                tracked_entity_id,
            )
            return source

        if not source:
            source = Source(
                name=ADLC_SOURCE_NAME,
                type=SourceType.INSTITUTIONAL,
                provider="native",
                url=ADLC_BASE_URL,
                active=True,
                category="institutional",
                tracked_entity_id=tracked_entity_id,
                config=DEFAULT_ADLC_CONFIG,
            )
            db.add(source)
            db.commit()
            db.refresh(source)
            logger.info("Created real Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)
        else:
            source.name = ADLC_SOURCE_NAME
            source.type = SourceType.INSTITUTIONAL
            source.provider = "native"
            source.url = ADLC_BASE_URL
            source.active = True
            source.category = "institutional"
            if tracked_entity_id:
                source.tracked_entity_id = tracked_entity_id
            new_config = dict(source.config) if (source.config and isinstance(source.config, dict)) else {}
            new_config.update(DEFAULT_ADLC_CONFIG)
            source.config = new_config
            db.commit()
            db.refresh(source)
            logger.info("Updated existing Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)

        return source

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Autorité de la concurrence source.")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without modifying database.")
    args = parser.parse_args()

    src = seed_autorite_concurrence_source(dry_run=args.dry_run)
    if src:
        print("\n--- AUTORITÉ DE LA CONCURRENCE SOURCE READY ---")
        print(f"  ID: {getattr(src, 'id', None)}")
        print(f"  Name: {src.name}")
        print(f"  Type: {src.type}")
        print(f"  Provider: {src.provider}")
        print(f"  URL: {src.url}")
    else:
        print("\n[DRY RUN] Finished without persistent modifications.")


if __name__ == "__main__":
    main()
