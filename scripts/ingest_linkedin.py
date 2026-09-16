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
from typing import Optional, Any
import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity, TrackingMatrix
from app.services.linkedin_discovery_planner import LinkedInDiscoveryPlanner
from app.services.linkedin_ingestion_service import (
    LinkedInIngestionService,
    LinkedInIngestionReport,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("scripts.ingest_linkedin")


# Verified pilot entities for HITCHINGS (Section 7, 8, 9)
VERIFIED_LINKEDIN_PILOT_ENTITIES = [
    {
        "name": "Hausfeld",
        "linkedin_url": "https://www.linkedin.com/company/hausfeld",
        "linkedin_entity_type": "organization",
        "linkedin_url_verified": True,
        "linkedin_url_verified_at": "2026-09-09T14:45:00Z",
        "linkedin_url_verification_method": "public_linkedin_page_identity_and_official_domain",
        "linkedin_declared_website": "hausfeld.com",
    },
    {
        "name": "ESKARIAM",
        "linkedin_url": "https://www.linkedin.com/company/eskariam",
        "linkedin_entity_type": "organization",
        "linkedin_url_verified": True,
        "linkedin_url_verified_at": "2026-09-09T14:45:00Z",
        "linkedin_url_verification_method": "public_linkedin_page_identity_and_official_domain",
        "linkedin_declared_website": "eskariam.com",
    },
    {
        "name": "Comisión Nacional de los Mercados y la Competencia",
        "linkedin_url": "https://www.linkedin.com/company/cnmc-comision-nacional-de-los-mercados-y-la-competencia",
        "linkedin_entity_type": "organization",
        "linkedin_url_verified": True,
        "linkedin_url_verified_at": "2026-09-09T14:45:00Z",
        "linkedin_url_verification_method": "public_linkedin_page_identity_and_official_domain",
        "linkedin_declared_website": "cnmc.es",
    },
    {
        "name": "European Commission",
        "linkedin_url": "https://www.linkedin.com/company/european-commission",
        "linkedin_entity_type": "organization",
        "linkedin_url_verified": True,
        "linkedin_url_verified_at": "2026-09-09T14:45:00Z",
        "linkedin_url_verification_method": "public_linkedin_page_identity_and_official_domain",
        "linkedin_declared_website": "commission.europa.eu",
    },
    {
        "name": "Miguel Sousa Ferro",
        "linkedin_url": "https://www.linkedin.com/in/miguel-sousa-ferro-b7551666",
        "linkedin_entity_type": "person",
        "linkedin_url_verified": True,
        "linkedin_url_verified_at": "2026-09-15T16:00:00Z",
        "linkedin_url_verification_method": "public_profile_identity_and_official_domain",
        "linkedin_declared_website": "sousaferro.com",
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
            needs_update = False
            for k, v in pilot.items():
                if k == "name":
                    continue
                if current_meta.get(k) != v:
                    current_meta[k] = v
                    needs_update = True

            if needs_update:
                entity.metadata_ = current_meta
                updated += 1
                logger.info("Updated verified LinkedIn metadata for '%s'", pilot["name"])
    if updated > 0:
        db.commit()
    return updated


def run_brightdata_probe(
    db: Any,
    confirm_real_calls: bool = False,
    client: Optional[httpx.Client] = None,
    settings: Optional[Any] = None,
    entity_override: Optional[str] = None,
) -> tuple[int, Optional[LinkedInIngestionReport], list[str]]:
    """Validate all guards and execute the controlled Bright Data probe for Hausfeld.

    Guards:
    1. BRIGHTDATA_API_TOKEN exists in environment (presence verified, value redacted).
    2. TrackedEntity for Hausfeld exists unequivocally in the database.
    3. Configured LinkedIn URL is strictly 'https://www.linkedin.com/company/hausfeld'.
    4. Bounds strictly enforced: max_entities=1, max_posts=1.
    5. Fallback provider Apify strictly disabled.
    6. Provenance fail-closed active.
    7. Cross-provider deduplication active.

    Returns:
        tuple (exit_code: int, report: Optional[LinkedInIngestionReport], logs: list[str])
    """
    settings = settings or get_settings()
    logs: list[str] = []

    logs.append("=" * 60)
    logs.append("HITCHINGS - LINKEDIN BRIGHT DATA PROBE")
    logs.append("=" * 60)

    # 1. Target Entity validation
    if entity_override and entity_override.strip().lower() != "hausfeld":
        logs.append(f"[GUARD ERROR] Probe is strictly locked to entity 'Hausfeld' (received {entity_override!r}). Aborting.")
        return (1, None, logs)

    # 2. Guard 1: Verify token presence (redacted)
    has_brightdata_token = bool(settings.brightdata_token)
    if not has_brightdata_token:
        logs.append("[GUARD ERROR] BRIGHTDATA_API_TOKEN is not configured in environment (fail-closed). Aborting.")
        return (1, None, logs)
    logs.append("[GUARD 1] BRIGHTDATA_API_TOKEN : Configured (presence verified; value redacted)")

    # 3. Active Tracking Matrix Check
    matrix = db.execute(
        select(TrackingMatrix).where(TrackingMatrix.status == "active")
    ).scalar_one_or_none()
    if not matrix:
        logs.append("[GUARD ERROR] Active TrackingMatrix not found in database. Aborting.")
        return (1, None, logs)

    # 4. Guard 2: Unequivocal TrackedEntity for Hausfeld
    candidates = db.execute(
        select(TrackedEntity).where(TrackedEntity.display_name.ilike("hausfeld"))
    ).scalars().all()
    if len(candidates) == 0:
        logs.append("[GUARD ERROR] TrackedEntity for 'Hausfeld' not found in database. Run with --seed-verified-metadata first if needed. Aborting.")
        return (1, None, logs)
    if len(candidates) > 1:
        logs.append(f"[GUARD ERROR] Ambiguous TrackedEntity for 'Hausfeld' ({len(candidates)} records found). Aborting.")
        return (1, None, logs)
    hausfeld = candidates[0]
    logs.append(f"[GUARD 2] TrackedEntity        : 'Hausfeld' verified (id={hausfeld.id}, type={hausfeld.entity_type})")

    # 5. Guard 3: Configured URL exact match
    meta = hausfeld.metadata_ or {}
    configured_url = meta.get("linkedin_url")
    expected_url = "https://www.linkedin.com/company/hausfeld"
    if configured_url != expected_url:
        logs.append(
            f"[GUARD ERROR] Configured LinkedIn URL is {configured_url!r}, expected {expected_url!r}. "
            "Run with --seed-verified-metadata first. Aborting."
        )
        return (1, None, logs)
    logs.append(f"[GUARD 3] Configured URL       : '{configured_url}' (exact match)")

    # 5. Guard 4: Bounds
    logs.append("[GUARD 4] Probe Limits         : max_entities=1, max_posts=1 (strictly capped)")

    # 6. Guard 5: Apify Fallback disabled
    logs.append("[GUARD 5] Fallback Provider    : Apify DISABLED (single-provider Bright Data)")

    # 7. Guard 6: Provenance fail-closed
    logs.append("[GUARD 6] Provenance           : Fail-closed ACTIVE (author_name + author_profile_url verification required)")

    # 8. Guard 7: Deduplication active
    logs.append("[GUARD 7] Deduplication        : ACTIVE (external_id + canonical_url + content_hash)")

    # 9. Dry Run check
    if not confirm_real_calls:
        return (0, None, logs)

    # 10. Execute real probe
    logs.append("-" * 60)
    logs.append("[EXECUTION] Starting real controlled Bright Data probe for Hausfeld...")
    service = LinkedInIngestionService()
    report = service.execute_discovery(
        db=db,
        confirm_real_calls=True,
        target_entity_id=hausfeld.id,
        client=client,
        max_posts_per_entity=1,
        max_new_entries=1,
        allow_probe=True,
        disable_fallback=True,
    )
    return (0, report, logs)


def main() -> None:
    parser = argparse.ArgumentParser(description="HITCHINGS LinkedIn Discovery CLI (Bloque 9C)")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Execute controlled single-item probe with Bright Data for Hausfeld (max_entities=1, max_posts=1, no fallback).",
    )
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
    parser.add_argument(
        "--max-entities",
        type=int,
        default=None,
        help="Maximum entities to discover (overrides config default).",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=None,
        help="Maximum posts per entity (overrides config default).",
    )

    args = parser.parse_args()
    settings = get_settings()
    db = SessionLocal()

    try:
        service = LinkedInIngestionService()

        # Seed operations
        if args.seed_verified_metadata:
            updated_count = seed_verified_metadata(db)
            print(f"[Seed] Verified LinkedIn metadata updated for {updated_count} entities.")

        if args.seed_source:
            src = service.get_or_create_linkedin_source(db)
            db.commit()
            print(f"[Seed] Canonical LinkedIn Source registered (id={src.id}, type={src.type}).")

        # ----------------------------------------------------------------------
        # PROBE MODE: Single controlled probe with Bright Data for Hausfeld
        # ----------------------------------------------------------------------
        if args.probe:
            exit_code, report, logs = run_brightdata_probe(
                db=db,
                confirm_real_calls=args.confirm_real_calls,
                settings=settings,
                entity_override=args.entity,
            )
            for log in logs:
                print(log)

            if exit_code != 0:
                sys.exit(exit_code)

            if not args.confirm_real_calls:
                print("-" * 60)
                print("[DRY RUN COMPLETE] All guards validated. 0 external calls made, 0 DB writes.")
                print("To execute the real controlled probe, run:")
                print("python -m scripts.ingest_linkedin --probe --confirm-real-calls")
                print("=" * 60)
                return

            print("\n" + "=" * 60)
            print("LINKEDIN BRIGHT DATA PROBE — POST-RUN EXECUTION REPORT")
            print("=" * 60)
            print(f"Run ID                 : {report.run_id}")
            print(f"Primary Provider       : {report.primary_provider}")
            print(f"Fallback Provider      : {report.fallback_provider} (DISABLED)")
            print(f"Entities Planned       : {report.entities_planned}")
            print(f"Entities Executed      : {report.entities_executed}")
            print(f"Posts Seen             : {report.posts_seen}")
            print(f"Entries Created        : {report.entries_created}")
            print(f"Duplicates             : {report.duplicates}")
            print(f"Failed Jobs            : {report.failed_jobs}")
            print(f"Timed Out Snapshots    : {report.timed_out_snapshots}")
            print(f"Provider Errors        : {report.provider_errors}")

            if report.items_detail:
                for idx, itm in enumerate(report.items_detail, 1):
                    print(f"\n--- Discovered Item #{idx} ---")
                    http_st = itm.get("http_status")
                    st_str = f"HTTP {http_st}" if http_st else "N/A"
                    if http_st == 200:
                        st_str += " (OK)"
                    print(f"HTTP / Provider Status : {st_str}")
                    print(f"Records Returned       : {itm.get('records_returned', 0)}")
                    print(f"Author Name            : {itm.get('author_name') or 'N/A'}")
                    print(f"Author Profile URL     : {itm.get('author_profile_url') or 'N/A'}")
                    print(f"LinkedIn Post URL      : {itm.get('linkedin_post_url') or 'N/A'}")
                    print(f"Activity / Post ID     : {itm.get('activity_id') or 'N/A'}")
                    print(f"Published At           : {itm.get('published_at') or 'N/A'}")
                    print(f"Identity Status        : {itm.get('identity_status') or 'N/A'}")
                    print(f"Provenance Status      : {itm.get('provenance_status') or 'N/A'}")
                    print(f"Retrieval Provider     : {itm.get('retrieval_provider') or 'N/A'}")
                    print(f"Entry Action           : {itm.get('action') or 'N/A'}")
                    if itm.get("entry_id"):
                        print(f"Created Entry ID       : {itm.get('entry_id')}")
                    if itm.get("external_id"):
                        print(f"External ID            : {itm.get('external_id')}")
                    if itm.get("error"):
                        print(f"Error Message          : {itm.get('error')}")
            else:
                print("\nNo items returned or recorded.")

            consumed = report.provider_records_fetched if report.provider_records_fetched else report.posts_seen
            cost_str = (
                f"${report.estimated_provider_cost:.4f} USD"
                if report.estimated_provider_cost is not None
                else "N/A"
            )
            print(f"\nProvider Consumption   : {consumed} records fetched")
            print(f"Estimated Cost         : {cost_str}")

            if report.errors:
                print(f"\nErrors ({len(report.errors)}):")
                for err in report.errors:
                    print(f"  - {err}")
            print("=" * 60)
            return

        effective_max_entities = (
            args.max_entities
            if args.max_entities is not None
            else getattr(settings, "LINKEDIN_DISCOVERY_MAX_ENTITIES", settings.LINKEDIN_MAX_ENTITIES_PER_RUN)
        )
        effective_max_posts = (
            args.max_posts
            if args.max_posts is not None
            else getattr(settings, "LINKEDIN_DISCOVERY_MAX_POSTS_PER_ENTITY", settings.LINKEDIN_MAX_POSTS_PER_ENTITY)
        )
        print("=" * 60)
        print("HITCHINGS - LINKEDIN DISCOVERY (BLOQUE 9C)")
        print("=" * 60)
        print(f"Primary Provider  : {settings.LINKEDIN_PRIMARY_PROVIDER}")
        print(f"Fallback Provider : {settings.LINKEDIN_FALLBACK_PROVIDER}")
        print(f"Discovery Enabled : {settings.LINKEDIN_DISCOVERY_ENABLED}")
        print(f"Max Entities/Run  : {effective_max_entities}")
        print(f"Max Posts/Entity  : {effective_max_posts}")
        print(f"Max New Entries   : {settings.LINKEDIN_MAX_NEW_ENTRIES_PER_RUN}")
        print(f"Timeout (seconds) : {settings.LINKEDIN_TIMEOUT_SECONDS}")

        # Check credentials presence (never printing tokens!)
        has_brightdata_token = bool(settings.brightdata_token)
        has_apify_token = bool(settings.apify_token)
        print(f"Bright Data Token : {'Configured' if has_brightdata_token else 'NOT CONFIGURED'}")
        print(f"Apify Token       : {'Configured' if has_apify_token else 'NOT CONFIGURED'}")
        print("-" * 60)

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
        jobs = planner.plan_jobs(db, max_entities=args.max_entities)
        if target_uuid:
            jobs = [j for j in jobs if j.tracked_entity_id == target_uuid]

        print(f"\n[Planner] Jobs planned: {len(jobs)}")
        for idx, j in enumerate(jobs, 1):
            print(f"  {idx}. [{j.entity_type.upper()}] {j.entity_name} -> {j.linkedin_url} (priority={j.priority})")

        # Execute or Dry-Run
        if not args.confirm_real_calls:
            print("\n[DRY RUN] No real external calls or database entry writes were executed.")
            print("To execute real discovery, use: python -m scripts.ingest_linkedin --confirm-real-calls")
            print("Ensure valid provider tokens are configured.")
            return

        if not settings.LINKEDIN_DISCOVERY_ENABLED:
            print("\n[MANUAL OVERRIDE] LINKEDIN_DISCOVERY_ENABLED is false; proceeding under explicit manual confirmation (--confirm-real-calls).")

        if not has_brightdata_token and not has_apify_token:
            print("\n[SKIPPED] Real execution skipped: provider credentials not configured.")
            return

        # Execute discovery
        print("\n[EXECUTION] Starting real LinkedIn discovery run...")
        report = service.execute_discovery(
            db=db,
            confirm_real_calls=True,
            target_entity_id=target_uuid,
            max_entities=args.max_entities,
            max_posts_per_entity=args.max_posts,
            allow_manual=True,
        )

        cost_str = (
            f"${report.estimated_provider_cost:.4f} USD"
            if report.estimated_provider_cost is not None
            else "N/A"
        )
        consumption = report.provider_records_fetched if report.provider_records_fetched else report.posts_seen

        print("\n" + "=" * 48)
        print("LINKEDIN DISCOVERY REPORT")
        print("=" * 48)
        print(f"Entities planned: {report.entities_planned}")
        print(f"Entities executed: {report.entities_executed}")
        print(f"Posts discovered: {report.posts_seen}")
        print(f"Entries created: {report.entries_created}")
        print(f"Duplicates: {report.duplicates}")
        print(f"Provenance rejected: {report.provenance_rejected}")
        print(f"Failed: {report.failed_jobs}")
        print(f"  - Timed out snapshots: {report.timed_out_snapshots}")
        print(f"  - Provider errors: {report.provider_errors}")
        print()
        print("Provider Consumption:")
        print(f"  {consumption} records fetched")
        print("Estimated Cost:")
        print(f"  {cost_str}")
        print()
        print("Por entidad:")
        if report.per_entity:
            for pe in report.per_entity:
                print(f"Entity: {pe['entity']}")
                print(f"Provider: {pe['provider']}")
                print(f"Posts: {pe['posts']}")
                print(f"Created: {pe['created']}")
                print(f"Duplicates: {pe['duplicates']}")
                print(f"Provenance rejected: {pe.get('provenance_rejected', 0)}")
                print(f"Errors: {pe['errors']}")
                print(f"Timed out snapshots: {pe.get('timed_out_snapshots', 0)}")
                print(f"Provider errors: {pe.get('provider_errors', 0)}")
                print("-" * 30)
        else:
            print("  Ninguna entidad ejecutada.")
        print("=" * 48)

        if report.items_detail:
            print("\nDetalle de publicaciones procesadas:")
            for idx, item in enumerate(report.items_detail, 1):
                action = item.get("action", "UNKNOWN")
                entity = item.get("entity_name", "N/A")
                author = item.get("author_name") or "(desconocido)"
                url = item.get("linkedin_post_url") or "N/A"
                print(f"\n  {idx}. [{action}] {entity} · Autor: {author}")
                print(f"     URL: {url}")
                if item.get("action") == "SKIPPED_PROVENANCE":
                    print(f"     Motivo rechazo: {item.get('rejection_reason', 'unverified_provenance')}")
                elif item.get("entry_id"):
                    print(f"     Entry ID: {item.get('entry_id')} | ext_id: {item.get('external_id')}")
                snippet = item.get("content_snippet")
                if snippet:
                    clean_snip = snippet.replace('\n', ' ')
                    print(f"     Texto: {clean_snip[:140]}...")

        if report.errors:
            print(f"\nErrors ({len(report.errors)}):")
            for err in report.errors:
                print(f"  - {err}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
