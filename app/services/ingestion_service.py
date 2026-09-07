"""Ingestion service coordinating source extraction, deduplication, and persistence."""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.models.source import Source
from app.models.entry import Entry
from app.providers.base import BaseSourceProvider, RawEntryData, ProviderError, ProviderDisabledError
from app.providers.native import NativeProvider
from app.providers.brightdata import BrightDataProvider
from app.providers.apify import ApifyProvider
from app.schemas.ingestion import IngestionResult

logger = logging.getLogger(__name__)


def compute_content_hash(title: Optional[str], url: str, excerpt: Optional[str] = None) -> str:
    """Generate a deterministic SHA-256 hash for basic content deduplication."""
    clean_url = (url or "").strip()
    clean_title = (title or "").strip()
    clean_excerpt = (excerpt or "").strip()
    raw = f"{clean_url}|{clean_title}|{clean_excerpt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class IngestionService:
    """Service handling manual or automated ingestion runs for data sources."""

    def __init__(self) -> None:
        self._providers: dict[str, BaseSourceProvider] = {
            "native": NativeProvider(),
            "brightdata": BrightDataProvider(),
            "apify": ApifyProvider(),
        }

    def get_provider(self, provider_name: str) -> BaseSourceProvider:
        """Resolve the provider implementation for a given provider identifier."""
        provider = self._providers.get(provider_name)
        if not provider:
            raise ValueError(f"Unknown provider: '{provider_name}'")
        return provider

    async def ingest_source(self, source_id: uuid.UUID, db: Session) -> IngestionResult:
        """Execute ingestion for a single source, deduplicating and persisting entries."""
        source = db.get(Source, source_id)
        if not source:
            raise ValueError(f"Source with ID {source_id} not found")

        if not source.active:
            raise ValueError(f"Source '{source.name}' is inactive. Cannot ingest.")

        provider = self.get_provider(source.provider)
        if not provider.is_enabled():
            raise ProviderDisabledError(f"Provider '{source.provider}' is disabled.")

        started_at = datetime.now(timezone.utc)
        source.last_run_at = started_at
        db.commit()

        logger.info("Starting ingestion for source '%s' (id=%s, provider=%s)", source.name, source.id, source.provider)

        try:
            raw_entries: list[RawEntryData] = await provider.fetch_entries(source)
        except Exception as exc:
            logger.error("Ingestion failed during fetch for source '%s': %s", source.name, exc)
            # last_run_at is preserved, last_success_at remains un-updated
            raise

        created = 0
        duplicates = 0
        failed = 0

        for raw in raw_entries:
            try:
                c_hash = compute_content_hash(raw.title, raw.url, raw.excerpt)

                # Deduplication hierarchy:
                # 1. source_id + external_id (if available)
                # 2. source_id + url or canonical_url
                # 3. source_id + content_hash (fallback)
                dup_conditions = []
                if raw.external_id:
                    dup_conditions.append(Entry.external_id == raw.external_id)

                dup_conditions.append(Entry.url == raw.url)
                dup_conditions.append(Entry.canonical_url == raw.url)
                dup_conditions.append(Entry.content_hash == c_hash)

                existing = db.execute(
                    select(Entry.id).where(
                        Entry.source_id == source.id,
                        or_(*dup_conditions)
                    ).limit(1)
                ).scalar_one_or_none()

                if existing:
                    logger.debug("Duplicate entry detected for '%s' (url=%s, guid=%s)", source.name, raw.url, raw.external_id)
                    duplicates += 1
                    continue

                new_entry = Entry(
                    source_id=source.id,
                    external_id=raw.external_id,
                    url=raw.url,
                    canonical_url=raw.url,
                    title=raw.title,
                    content=raw.content,
                    excerpt=raw.excerpt,
                    author=raw.author,
                    published_at=raw.published_at,
                    captured_at=datetime.now(timezone.utc),
                    language=raw.language,
                    content_type=raw.content_type or "news_article",
                    content_hash=c_hash,
                    raw_metadata=raw.raw_metadata,
                )
                db.add(new_entry)
                created += 1

            except Exception as item_err:
                logger.warning("Error processing raw entry from '%s' (%s): %s", source.name, raw.url, item_err)
                failed += 1

        finished_at = datetime.now(timezone.utc)
        source.last_success_at = finished_at
        db.commit()

        # Freshness calculation
        latest_published_at = max(
            (raw.published_at for raw in raw_entries if raw.published_at is not None),
            default=None,
        )
        oldest_published_at = min(
            (raw.published_at for raw in raw_entries if raw.published_at is not None),
            default=None,
        )

        logger.info(
            "Ingestion completed for '%s': fetched=%d, created=%d, duplicates=%d, failed=%d (freshness: latest=%s, oldest=%s)",
            source.name, len(raw_entries), created, duplicates, failed,
            latest_published_at.isoformat() if latest_published_at else None,
            oldest_published_at.isoformat() if oldest_published_at else None,
        )

        return IngestionResult(
            source_id=source.id,
            fetched=len(raw_entries),
            created=created,
            duplicates=duplicates,
            failed=failed,
            started_at=started_at,
            finished_at=finished_at,
            latest_published_at=latest_published_at,
            oldest_published_at=oldest_published_at,
        )
