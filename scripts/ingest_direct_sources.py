"""CLI tool for executing and dry-running Direct Web Source Ingestion (Bloque 9B).

Usage:
    # Dry-run (default: 0 HTTP, 0 DB writes)
    python -m scripts.ingest_direct_sources

    # Seed the 3 pilot sources idempotently in the database
    python -m scripts.ingest_direct_sources --seed-sources

    # Real execution with explicit safety confirmation
    python -m scripts.ingest_direct_sources --confirm-real-calls --limit 5

    # Filter to a specific source
    python -m scripts.ingest_direct_sources --source chillin_competition --confirm-real-calls
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from typing import Optional

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.source import Source, SourceType
from app.models.tracking import TrackedEntity
from app.providers.direct_web.registry import DirectWebAdapterRegistry
from app.services.direct_web_ingestion_service import DirectWebIngestionService

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ingest_direct_sources")

PILOT_SOURCES_CONFIG = [
    {
        "name": "Kluwer Competition Law Blog",
        "type": SourceType.BLOG,
        "provider": "native",
        "category": "expert_analysis",
        "url": "https://legalblogs.wolterskluwer.com/competition-blog/",
        "config": {
            "adapter": "kluwer_competition",
            "feed_url": "https://legalblogs.wolterskluwer.com/competition-blog/rss.xml",
        },
        "tracked_entity_name": None,
    },
    {
        "name": "Chillin'Competition",
        "type": SourceType.BLOG,
        "provider": "native",
        "category": "expert_analysis",
        "url": "https://chillingcompetition.com/",
        "config": {
            "adapter": "chillin_competition",
            "feed_url": "https://chillingcompetition.com/feed/",
        },
        "tracked_entity_name": None,
    },
    {
        "name": "Almacén de Derecho - Competencia",
        "type": SourceType.BLOG,
        "provider": "native",
        "category": "expert_analysis",
        "url": "https://almacendederecho.org/category/competencia/",
        "config": {
            "adapter": "almacen_derecho",
            "feed_url": "https://almacendederecho.org/category/competencia/feed",
        },
        "tracked_entity_name": None,
    },
]


def seed_pilot_sources(db) -> list[Source]:
    """Seed the 3 pilot direct web sources idempotently into the database."""
    seeded: list[Source] = []
    print("\n--- Seeding Direct Web Pilot Sources ---")
    for cfg in PILOT_SOURCES_CONFIG:
        source_name = cfg["name"]
        existing = db.query(Source).filter(Source.name == source_name).first()

        # Resolve TrackedEntity if configured
        te_id = None
        te_name = cfg["tracked_entity_name"]
        if te_name:
            # Flexible ILIKE match
            name_parts = te_name.split()
            query = db.query(TrackedEntity)
            for part in name_parts:
                query = query.filter(TrackedEntity.display_name.ilike(f"%{part}%"))
            te = query.first()
            if te:
                te_id = te.id
                print(f"  [Link] '{source_name}' -> TrackedEntity '{te.display_name}' ({te.id})")
            else:
                print(f"  [Warning] TrackedEntity '{te_name}' not found for '{source_name}'")

        if existing:
            # Update configuration if necessary
            existing.config = cfg["config"]
            existing.url = cfg["url"]
            existing.category = cfg["category"]
            existing.tracked_entity_id = te_id
            db.commit()
            print(f"  [Updated] Source '{source_name}' (id={existing.id}, te_id={existing.tracked_entity_id})")
            seeded.append(existing)
        else:
            new_source = Source(
                name=source_name,
                type=cfg["type"],
                provider=cfg["provider"],
                category=cfg["category"],
                url=cfg["url"],
                active=True,
                config=cfg["config"],
                tracked_entity_id=te_id,
            )
            db.add(new_source)
            db.commit()
            db.refresh(new_source)
            print(f"  [Created] Source '{source_name}' (id={new_source.id})")
            seeded.append(new_source)

    return seeded


def main():
    parser = argparse.ArgumentParser(description="Ingest direct reference web sources (Bloque 9B)")
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        help="Confirm execution of real network calls and database writes",
    )
    parser.add_argument(
        "--seed-sources",
        action="store_true",
        help="Idempotently seed the 3 pilot Sources in the database",
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Filter execution to a specific source name, ID, or adapter code",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max items to fetch per source (defaults to settings.DIRECT_WEB_MAX_ITEMS_PER_SOURCE)",
    )

    args = parser.parse_args()
    settings = get_settings()

    if args.confirm_real_calls:
        settings.DIRECT_WEB_INGESTION_ENABLED = True

    db = SessionLocal()
    try:
        if args.seed_sources:
            seed_pilot_sources(db)
            if not args.confirm_real_calls:
                print("\nSeeding complete. Use --confirm-real-calls to execute ingestion.")
                return

        # Query active direct web sources
        query = (
            db.query(Source)
            .filter(Source.active == True)
            .filter(Source.type.in_([SourceType.BLOG, SourceType.WEBSITE]))
        )

        all_candidate_sources = query.all()
        matched_sources: list[Source] = []

        for s in all_candidate_sources:
            try:
                adapter = DirectWebAdapterRegistry.get_adapter_for_source(s)
            except Exception:
                continue

            if args.source:
                s_filter = args.source.strip().lower()
                adapter_code = adapter.adapter_code.lower()
                s_name = s.name.lower()
                s_id = str(s.id).lower()

                if s_filter not in adapter_code and s_filter not in s_name and s_filter != s_id:
                    continue

            matched_sources.append(s)

        effective_limit = args.limit or settings.DIRECT_WEB_MAX_ITEMS_PER_SOURCE
        is_dry_run = not (settings.DIRECT_WEB_INGESTION_ENABLED and args.confirm_real_calls)

        print("=" * 70)
        print(" HITCHINGS — DIRECT WEB SOURCE INGESTION (BLOQUE 9B)")
        print("=" * 70)
        print(f" Mode               : {'REAL EXECUTION' if not is_dry_run else 'DRY RUN (0 HTTP, 0 DB writes)'}")
        print(f" Setting Enabled    : {settings.DIRECT_WEB_INGESTION_ENABLED}")
        print(f" Confirm Flag       : {args.confirm_real_calls}")
        print(f" Sources Selected   : {len(matched_sources)}")
        print(f" Max Items / Source : {effective_limit}")
        print("-" * 70)

        for i, s in enumerate(matched_sources, 1):
            adapter = DirectWebAdapterRegistry.get_adapter_for_source(s)
            te_name = s.tracked_entity.display_name if s.tracked_entity else "None"
            feed_url = (s.config or {}).get("feed_url", s.url)
            print(f" [{i}] {s.name} (id: {s.id})")
            print(f"     Adapter       : {adapter.adapter_code}")
            print(f"     TrackedEntity : {te_name}")
            print(f"     Feed/URL      : {feed_url}")
        print("-" * 70)

        if not matched_sources:
            print("No matching direct web sources found.")
            if not args.seed_sources:
                print("Tip: Run with --seed-sources first to register the 3 pilot sources.")
            return

        if is_dry_run:
            print("\nDRY RUN complete. To execute real ingestion, pass:")
            print("  --confirm-real-calls (and ensure DIRECT_WEB_INGESTION_ENABLED=true or overridden)")
            return

        service = DirectWebIngestionService()
        report = service.execute_ingestion(
            db=db,
            sources=matched_sources,
            max_items_per_source=effective_limit,
            confirm_real_calls=args.confirm_real_calls,
        )

        print("\n" + "=" * 70)
        print(" INGESTION RUN REPORT")
        print("=" * 70)
        print(f" Sources Processed       : {report.sources_processed}")
        print(f" Items Discovered        : {report.total_discovered}")
        print(f" Items Fetched           : {report.total_fetched}")
        print(f" Direct Entries Created  : {report.total_created}")
        print(f" Duplicates Skipped      : {report.total_duplicates}")
        print(f" Failed Items            : {report.total_failed}")
        print(f" Google News Matches     : {report.total_google_news_matches}")
        print("-" * 70)
        print(" Sufficiency Breakdown:")
        for lvl, cnt in report.total_sufficiency.items():
            print(f"   - {lvl:<12}: {cnt}")
        print("=" * 70)

        for res in report.results_by_source:
            print(f"\nSource: {res.source_name} [{res.adapter_code}]")
            print(f"  Discovered: {res.items_discovered} | Fetched: {res.items_fetched} | Created: {res.entries_created} | Dups: {res.duplicates_count} | Failed: {res.failed_count}")
            print(f"  Google News Matches: {res.google_news_matches_count}")
            print(f"  Sufficiency: {res.sufficiency_counts}")
            if res.errors:
                print(f"  Errors ({len(res.errors)}):")
                for err in res.errors[:5]:
                    print(f"    * {err}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
