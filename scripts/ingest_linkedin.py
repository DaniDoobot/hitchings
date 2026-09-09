"""CLI for LinkedIn Discovery Ingestion (Bloque 9C).

Usage:
    # 1. Dry run (default: 0 external calls, 0 DB writes)
    python -m scripts.ingest_linkedin

    # 2. Seed canonical Source & verified metadata
    python -m scripts.ingest_linkedin --seed-source --seed-verified-metadata

    # 3. Real execution (requires explicit flag, LINKEDIN_DISCOVERY_ENABLED=true, and credentials)
    python -m scripts.ingest_linkedin --confirm-real-calls

    # 4. Target a specific entity
    python -m scripts.ingest_linkedin --entity "Hausfeld" --confirm-real-calls
"""

import argparse
import logging
import sys
import uuid
from typing import Optional
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity
from app.services.linkedin_discovery_planner import LinkedInDiscoveryPlanner
from app.services.linkedin_ingestion_service import LinkedInIngestionService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("scripts.ingest_linkedin")


# Verified pilot entities for HITCHINGS (Section 7, 8, 9)
VERIFIED_LINKEDIN_PILOT_ENTITIES = [
    {
        "name": "Hausfeld",
        "linkedin_url": "https://www.linkedin.com/company/hausfeld",
        "linkedin_entity_type": "organization",
    },
    {
        "name": "ESKARIAM",
        "linkedin_url": "https://www.linkedin.com/company/eskariam",
        "linkedin_entity_type": "organization",
    },
    {
        "name": "Comisión Nacional de los Mercados y la Competencia",
        "linkedin_url": "https://www.linkedin.com/company/cnmc",
        "linkedin_entity_type": "organization",
    },
    {
        "name": "European Commission",
        "linkedin_url": "https://www.linkedin.com/company/european-commission",
        "linkedin_entity_type": "organization",
    },
]


def seed_verified_metadata(db) -> int:
    """Seed verified LinkedIn URLs into TrackedEntity.metadata_ without modifying existing attributes."""
    updated = 0
    for pilot in VERIFIED_LINKEDIN_PILOT_ENTITIES:
        entity = db.execute(
            select(TrackedEntity).where(TrackedEntity.display_name == pilot["name"])
        ).scalar_one_or_none()

        if entity:
            current_meta = dict(entity.metadata_ or {})
            if (
                current_meta.get("linkedin_url") != pilot["linkedin_url"]
                or current_meta.get("linkedin_entity_type") != pilot["linkedin_entity_type"]
            ):
                current_meta["linkedin_url"] = pilot["linkedin_url"]
                current_meta["linkedin_entity_type"] = pilot["linkedin_entity_type"]
                entity.metadata_ = current_meta
                updated += 1
                logger.info("Updated verified LinkedIn metadata for '%s'", pilot["name"])
    if updated > 0:
        db.commit()
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="HITCHINGS LinkedIn Discovery CLI (Bloque 9C)")
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        help="Confirm real HTTP requests to external provider (requires credentials and LINKEDIN_DISCOVERY_ENABLED=true).",
    )
    parser.add_argument(
        "--seed-source",
        action="store_true",
        help="Idempotently seed the canonical LinkedIn Source in the database.",
    )
    parser.add_argument(
        "--seed-verified-metadata",
        action="store_true",
        help="Update metadata_ of verified tracked entities with their official LinkedIn URLs.",
    )
    parser.add_argument(
        "--entity",
        type=str,
        default=None,
        help="Target a single entity by display name or UUID for discovery.",
    )

    args = parser.parse_args()
    settings = get_settings()
    db = SessionLocal()

    try:
        print("=" * 60)
        print("HITCHINGS - LINKEDIN DISCOVERY (BLOQUE 9C)")
        print("=" * 60)
        print(f"Primary Provider  : {settings.LINKEDIN_PRIMARY_PROVIDER}")
        print(f"Fallback Provider : {settings.LINKEDIN_FALLBACK_PROVIDER}")
        print(f"Discovery Enabled : {settings.LINKEDIN_DISCOVERY_ENABLED}")
        print(f"Max Entities/Run  : {settings.LINKEDIN_MAX_ENTITIES_PER_RUN}")
        print(f"Max Posts/Entity  : {settings.LINKEDIN_MAX_POSTS_PER_ENTITY}")
        print(f"Max New Entries   : {settings.LINKEDIN_MAX_NEW_ENTRIES_PER_RUN}")
        print(f"Timeout (seconds) : {settings.LINKEDIN_TIMEOUT_SECONDS}")

        # Check credentials presence (never printing tokens!)
        has_brightdata_token = bool(settings.brightdata_token)
        has_apify_token = bool(settings.apify_token)
        print(f"Bright Data Token : {'Configured' if has_brightdata_token else 'NOT CONFIGURED'}")
        print(f"Apify Token       : {'Configured' if has_apify_token else 'NOT CONFIGURED'}")
        print("-" * 60)

        service = LinkedInIngestionService()

        # Seed operations
        if args.seed_verified_metadata:
            updated_count = seed_verified_metadata(db)
            print(f"[Seed] Verified LinkedIn metadata updated for {updated_count} entities.")

        if args.seed_source:
            src = service.get_or_create_linkedin_source(db)
            db.commit()
            print(f"[Seed] Canonical LinkedIn Source registered (id={src.id}, type={src.type}).")

        # Resolve target entity filter if specified
        target_uuid: Optional[uuid.UUID] = None
        if args.entity:
            try:
                target_uuid = uuid.UUID(args.entity)
            except ValueError:
                found_entity = db.execute(
                    select(TrackedEntity).where(TrackedEntity.display_name.ilike(f"%{args.entity}%"))
                ).scalar_one_or_none()
                if found_entity:
                    target_uuid = found_entity.id
                    print(f"Filter resolved: '{args.entity}' -> id={target_uuid}")
                else:
                    print(f"Error: Entity '{args.entity}' not found in database.")
                    sys.exit(1)

        # Plan discovery jobs
        planner = LinkedInDiscoveryPlanner()
        jobs = planner.plan_jobs(db)
        if target_uuid:
            jobs = [j for j in jobs if j.tracked_entity_id == target_uuid]

        print(f"\n[Planner] Jobs planned: {len(jobs)}")
        for idx, j in enumerate(jobs, 1):
            print(f"  {idx}. [{j.entity_type.upper()}] {j.entity_name} -> {j.linkedin_url} (priority={j.priority})")

        # Execute or Dry-Run
        if not args.confirm_real_calls:
            print("\n[DRY RUN] No real external calls or database entry writes were executed.")
            print("To execute real discovery, use: python -m scripts.ingest_linkedin --confirm-real-calls")
            print("Ensure LINKEDIN_DISCOVERY_ENABLED=true and valid provider tokens are configured.")
            return

        if not settings.LINKEDIN_DISCOVERY_ENABLED:
            print("\n[BLOCKED] LINKEDIN_DISCOVERY_ENABLED=false in settings. Execution aborted.")
            return

        if not has_brightdata_token and not has_apify_token:
            print("\n[SKIPPED] Real smoke skipped: provider credentials not configured.")
            return

        # Execute discovery
        print("\n[EXECUTION] Starting real LinkedIn discovery run...")
        report = service.execute_discovery(
            db=db,
            confirm_real_calls=True,
            target_entity_id=target_uuid,
        )

        print("\n" + "=" * 60)
        print("DISCOVERY EXECUTION REPORT")
        print("=" * 60)
        print(f"Run ID            : {report.run_id}")
        print(f"Primary Provider  : {report.primary_provider}")
        print(f"Entities Planned  : {report.entities_planned}")
        print(f"Entities Executed : {report.entities_executed}")
        print(f"Posts Seen        : {report.posts_seen}")
        print(f"Entries Created   : {report.entries_created}")
        print(f"Duplicates        : {report.duplicates}")
        print(f"Failed Jobs       : {report.failed_jobs}")
        print(f"Fallback Count    : {report.fallback_count}")
        print(f"Stopped By Cap    : {report.stopped_by_cap}")
        print(f"Estimated Cost    : {report.estimated_provider_cost}")
        if report.errors:
            print(f"Errors ({len(report.errors)}):")
            for err in report.errors:
                print(f"  - {err}")
        print("=" * 60)

    finally:
        db.close()


if __name__ == "__main__":
    main()
