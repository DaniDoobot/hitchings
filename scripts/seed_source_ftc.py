"""Idempotent seed script to create or update the official FTC (Bureau of Competition) source.

Audited and implemented under Bloque 17B of the HITCHINGS project.
Connects to the database, checks for an existing TrackedEntity for FTC,
and ensures the official Source exists with proper configuration.

Execution:
    python -m scripts.seed_source_ftc [--dry-run]
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
from app.providers.extractors.ftc import (
    FTC_SOURCE_NAME,
    FTC_BASE_URL,
    DEFAULT_FTC_CONFIG,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger(__name__)

FTC_ALIASES = [
    "ftc",
    "federal trade commission",
    "ftc competition",
    "bureau of competition",
    "ftc - bureau of competition",
]


def seed_ftc_source(dry_run: bool = False) -> Source | None:
    """Create or update the real Federal Trade Commission source idempotently."""
    db = SessionLocal()
    try:
        # 1. Check if a TrackedEntity exists for FTC
        entity = db.execute(
            select(TrackedEntity).where(
                or_(
                    TrackedEntity.display_name.ilike("%federal trade commission%"),
                    TrackedEntity.display_name == "FTC",
                    TrackedEntity.display_name.ilike("%bureau of competition%"),
                )
            )
        ).scalar_one_or_none()

        tracked_entity_id = entity.id if entity else None
        if entity:
            logger.info("Found TrackedEntity '%s' (id=%s)", entity.display_name, entity.id)
        else:
            logger.info("No existing TrackedEntity found for FTC; leaving tracked_entity_id as None.")

        # 2. Check if Source already exists (SELECT before INSERT)
        source = db.execute(
            select(Source).where(
                or_(
                    Source.name == FTC_SOURCE_NAME,
                    Source.name.ilike("%federal trade commission%"),
                    Source.url == FTC_BASE_URL,
                    Source.url.ilike("%ftc.gov/enforcement/competition%"),
                )
            )
        ).scalar_one_or_none()

        if dry_run:
            logger.info(
                "[DRY RUN] Would %s source '%s' (url=%s, tracked_entity_id=%s)",
                "update" if source else "create",
                FTC_SOURCE_NAME,
                FTC_BASE_URL,
                tracked_entity_id,
            )
            return source

        if not source:
            source = Source(
                name=FTC_SOURCE_NAME,
                type=SourceType.INSTITUTIONAL,
                provider="native",
                url=FTC_BASE_URL,
                active=True,
                category="institutional",
                tracked_entity_id=tracked_entity_id,
                config=DEFAULT_FTC_CONFIG,
            )
            db.add(source)
            db.commit()
            db.refresh(source)
            logger.info("Created real Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)
        else:
            source.name = FTC_SOURCE_NAME
            source.provider = "native"
            source.type = SourceType.INSTITUTIONAL
            source.url = FTC_BASE_URL
            source.active = True
            source.category = "institutional"
            if tracked_entity_id and not source.tracked_entity_id:
                source.tracked_entity_id = tracked_entity_id
            # Merge default config without losing existing user overrides
            cfg = dict(DEFAULT_FTC_CONFIG)
            if source.config:
                cfg.update(source.config)
            source.config = cfg
            db.commit()
            db.refresh(source)
            logger.info("Updated existing Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)

        return source
    except Exception as exc:
        db.rollback()
        logger.error("Error seeding FTC source: %s", exc)
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed official FTC Bureau of Competition source into database.")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without committing to DB")
    args = parser.parse_args()

    seed_ftc_source(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
