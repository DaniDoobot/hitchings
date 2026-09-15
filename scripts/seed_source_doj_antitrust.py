"""Idempotent seed script to create or update the official DOJ Antitrust Division source.

Audited and implemented under Bloque 18 of the HITCHINGS project (Source 17/17).
Connects to the database, checks for an existing TrackedEntity for DOJ ATR,
and ensures the official Source exists with proper configuration.

Execution:
    python -m scripts.seed_source_doj_antitrust [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity
from app.providers.extractors.doj_antitrust import (
    DOJ_ATR_SOURCE_NAME,
    DOJ_ATR_BASE_URL,
    DEFAULT_DOJ_ATR_CONFIG,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger(__name__)

DOJ_ATR_ALIASES = [
    "doj",
    "doj atr",
    "doj antitrust",
    "antitrust division",
    "department of justice - antitrust division",
    "us doj antitrust",
]


def seed_doj_antitrust_source(
    dry_run: bool = False,
    db: Optional[Session] = None,
) -> Source | None:
    """Create or update the real DOJ Antitrust Division source idempotently."""
    owns_session = False
    if db is None:
        db = SessionLocal()
        owns_session = True

    try:
        # 1. Check if a TrackedEntity exists for DOJ Antitrust
        entity = db.execute(
            select(TrackedEntity).where(
                or_(
                    TrackedEntity.display_name.ilike("%antitrust division%"),
                    TrackedEntity.display_name.ilike("%department of justice%"),
                    TrackedEntity.display_name == "DOJ",
                    TrackedEntity.display_name == "DOJ ATR",
                )
            )
        ).scalar_one_or_none()

        tracked_entity_id = entity.id if entity else None
        if entity:
            logger.info("Found TrackedEntity '%s' (id=%s)", entity.display_name, entity.id)
        else:
            logger.info("No existing TrackedEntity found for DOJ ATR; leaving tracked_entity_id as None.")

        # 2. Check if Source already exists (SELECT before INSERT)
        source = db.execute(
            select(Source).where(
                or_(
                    Source.name == DOJ_ATR_SOURCE_NAME,
                    Source.name.ilike("%antitrust division%"),
                    Source.name.ilike("%doj antitrust%"),
                    Source.url == DOJ_ATR_BASE_URL,
                    Source.url.ilike("%justice.gov/atr%"),
                )
            )
        ).scalar_one_or_none()

        if dry_run:
            logger.info(
                "[DRY RUN] Would %s source '%s' (url=%s, tracked_entity_id=%s)",
                "update" if source else "create",
                DOJ_ATR_SOURCE_NAME,
                DOJ_ATR_BASE_URL,
                tracked_entity_id,
            )
            return source

        if not source:
            source = Source(
                name=DOJ_ATR_SOURCE_NAME,
                type=SourceType.INSTITUTIONAL,
                provider="native",
                url=DOJ_ATR_BASE_URL,
                active=True,
                category="institutional",
                tracked_entity_id=tracked_entity_id,
                config=DEFAULT_DOJ_ATR_CONFIG,
            )
            db.add(source)
            db.commit()
            db.refresh(source)
            logger.info("Created real Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)
        else:
            source.name = DOJ_ATR_SOURCE_NAME
            source.provider = "native"
            source.type = SourceType.INSTITUTIONAL
            source.url = DOJ_ATR_BASE_URL
            source.active = True
            source.category = "institutional"
            if tracked_entity_id and not source.tracked_entity_id:
                source.tracked_entity_id = tracked_entity_id
            # Merge default config without losing existing user overrides
            cfg = dict(DEFAULT_DOJ_ATR_CONFIG)
            if source.config:
                cfg.update(source.config)
            source.config = cfg
            db.commit()
            db.refresh(source)
            logger.info("Updated existing Source '%s' (id=%s, url=%s)", source.name, source.id, source.url)

        return source
    except Exception as exc:
        db.rollback()
        logger.error("Error seeding DOJ Antitrust source: %s", exc)
        raise
    finally:
        if owns_session:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed official DOJ Antitrust Division source into database.")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without committing to DB")
    args = parser.parse_args()

    seed_doj_antitrust_source(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
