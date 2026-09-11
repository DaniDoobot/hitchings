"""Ingestion service coordinating source extraction, deduplication, traceability, and persistence."""

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from app.models.source import Source
from app.models.entry import Entry
from app.models.ingestion_run import IngestionRun, IngestionRunStatus
from app.providers.base import BaseSourceProvider, RawEntryData, ProviderError, ProviderDisabledError
from app.providers.native import NativeProvider
from app.providers.brightdata import BrightDataProvider
from app.providers.apify import ApifyProvider
from app.schemas.ingestion import (
    IngestionResult,
    SourceStatusResponse,
    FreshnessDetail,
    FreshnessStatus,
    IngestionRunSummary,
)

logger = logging.getLogger(__name__)


def compute_ingestion_dedupe_hash(title: Optional[str], url: str, excerpt: Optional[str] = None) -> str:
    """Generate a deterministic SHA-256 hash for ingestion deduplication (URL | title | excerpt)."""
    clean_url = (url or "").strip()
    clean_title = (title or "").strip()
    clean_excerpt = (excerpt or "").strip()
    raw = f"{clean_url}|{clean_title}|{clean_excerpt}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Backward-compatibility alias
compute_content_hash = compute_ingestion_dedupe_hash


class IngestionVolumeLimitExceededError(Exception):
    """Raised when prospective or actual new entries exceed the configured volume limit."""
    pass


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

    def _check_duplicate_entry(
        self, raw: RawEntryData, source: Source, db: Session
    ) -> tuple[bool, Optional[Entry], Optional[str]]:
        """Determine if a raw entry is already present in the database according to deduplication rules."""
        c_hash = compute_content_hash(raw.title, raw.url, raw.excerpt)

        # 0. Cross-source deduplication for European Commission (e.g. DMA vs DG COMP Press Corner)
        presscorner_ref = raw.raw_metadata.get("presscorner_ref") if raw.raw_metadata else None
        presscorner_url = raw.raw_metadata.get("presscorner_url") if raw.raw_metadata else None

        if presscorner_ref or presscorner_url:
            ref_conditions = []
            if presscorner_url:
                ref_conditions.append(Entry.url == presscorner_url)
                ref_conditions.append(Entry.canonical_url == presscorner_url)
            if presscorner_ref:
                ref_slug = presscorner_ref.lower().replace("/", "_")
                ref_conditions.append(Entry.url.ilike(f"%{ref_slug}%"))
                ref_conditions.append(Entry.canonical_url.ilike(f"%{ref_slug}%"))
                ref_conditions.append(Entry.external_id.ilike(f"%{ref_slug}%"))

            existing_cross = db.execute(
                select(Entry).where(or_(*ref_conditions)).limit(1)
            ).scalar_one_or_none()

            if existing_cross:
                return True, existing_cross, "presscorner"

        # 1. Within-source deduplication:
        # - source_id + external_id (if available)
        # - source_id + url or canonical_url
        # - source_id + content_hash (fallback)
        dup_conditions = []
        if raw.external_id:
            dup_conditions.append(Entry.external_id == raw.external_id)

        resolved_canonical_url = presscorner_url if presscorner_url else raw.url

        dup_conditions.append(Entry.url == raw.url)
        dup_conditions.append(Entry.canonical_url == raw.url)
        dup_conditions.append(Entry.canonical_url == resolved_canonical_url)
        dup_conditions.append(Entry.content_hash == c_hash)

        existing = db.execute(
            select(Entry).where(
                Entry.source_id == source.id,
                or_(*dup_conditions),
            ).limit(1)
        ).scalar_one_or_none()

        if existing:
            return True, existing, "regular"

        return False, None, None

    async def ingest_source(
        self,
        source_id: uuid.UUID,
        db: Session,
        client: Optional[httpx.AsyncClient] = None,
        max_new_entries: Optional[int] = None,
    ) -> IngestionResult:
        """Execute ingestion for a single source, deduplicating and persisting entries."""
        source = db.get(Source, source_id)
        if not source:
            raise ValueError(f"Source with ID {source_id} not found")

        if not source.active:
            raise ValueError(f"Source '{source.name}' is inactive. Cannot ingest.")

        provider = self.get_provider(source.provider)
        if not provider.is_enabled():
            raise ProviderDisabledError(f"Provider '{source.provider}' is disabled.")

        # 1. Initialize IngestionRun in RUNNING state
        started_at = datetime.now(timezone.utc)
        source.last_run_at = started_at

        run = IngestionRun(
            source_id=source.id,
            started_at=started_at,
            status=IngestionRunStatus.RUNNING.value,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        logger.info(
            "Starting ingestion run %s for source '%s' (id=%s, provider=%s)",
            run.id, source.name, source.id, source.provider
        )

        # 2. Execute provider extraction with failure capture
        try:
            import inspect
            sig = inspect.signature(provider.fetch_entries)
            if "client" in sig.parameters:
                raw_entries: list[RawEntryData] = await provider.fetch_entries(source, client=client)
            else:
                raw_entries = await provider.fetch_entries(source)

            # Circuit-breaker: Fail-closed if prospective new entries exceed max_new_entries
            if max_new_entries is not None:
                prospective_new = sum(
                    1 for raw in raw_entries
                    if not self._check_duplicate_entry(raw, source, db)[0]
                )
                if prospective_new > max_new_entries:
                    raise IngestionVolumeLimitExceededError(
                        f"Ingestion volume guard triggered: discovered {prospective_new} new entries, "
                        f"which exceeds --max-new-entries limit of {max_new_entries}."
                    )

        except IngestionVolumeLimitExceededError as vol_exc:
            logger.warning(
                "Ingestion volume guard triggered for source '%s' (run_id=%s): %s",
                source.name, run.id, vol_exc
            )
            run.finished_at = datetime.now(timezone.utc)
            run.status = IngestionRunStatus.FAILED.value
            run.error_type = type(vol_exc).__name__
            run.error_message = str(vol_exc)[:1000]
            try:
                db.commit()
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.error(
                "Ingestion fetch failed for source '%s' (run_id=%s): %s",
                source.name, run.id, exc
            )
            # Persist failure record in IngestionRun
            run.finished_at = datetime.now(timezone.utc)
            run.status = IngestionRunStatus.FAILED.value
            run.error_type = type(exc).__name__
            run.error_message = str(exc)[:1000]
            db.commit()
            raise

        # 3. Process, deduplicate, and persist entries
        created = 0
        duplicates = 0
        failed = 0

        for raw in raw_entries:
            try:
                with db.begin_nested():
                    if max_new_entries is not None and created >= max_new_entries:
                        raise IngestionVolumeLimitExceededError(
                            f"Ingestion volume guard triggered: created entries reached limit of {max_new_entries}."
                        )

                    is_dup, existing_entry, dup_type = self._check_duplicate_entry(raw, source, db)
                    if is_dup:
                        if dup_type == "presscorner" and existing_entry:
                            meta = dict(existing_entry.raw_metadata or {})
                            meta["cross_source_matched"] = True
                            meta["cross_source_matched_from"] = source.name
                            if (
                                "digital-markets-act" in raw.url.lower()
                                or "dma" in (source.name or "").lower()
                                or "digital markets act" in (source.name or "").lower()
                            ):
                                meta["dma_portal_url"] = raw.url
                            existing_entry.raw_metadata = meta
                            logger.info(
                                "Cross-source deduplication: Entry '%s' matched existing Entry id=%s via Press Corner %s",
                                raw.title, existing_entry.id, raw.raw_metadata.get("presscorner_ref") or raw.raw_metadata.get("presscorner_url")
                            )
                        duplicates += 1
                        continue

                    c_hash = compute_content_hash(raw.title, raw.url, raw.excerpt)
                    presscorner_url = raw.raw_metadata.get("presscorner_url") if raw.raw_metadata else None
                    resolved_canonical_url = presscorner_url if presscorner_url else raw.url

                    new_entry = Entry(
                        source_id=source.id,
                        external_id=raw.external_id,
                        url=raw.url,
                        canonical_url=resolved_canonical_url,
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

            except IngestionVolumeLimitExceededError:
                raise
            except Exception as item_err:
                logger.warning(
                    "Error processing raw entry for source '%s' (run_id=%s, url=%s): %s",
                    source.name, run.id, raw.url, item_err
                )
                failed += 1

        # 4. Finalize IngestionRun metrics & status
        finished_at = datetime.now(timezone.utc)
        run.finished_at = finished_at
        run.fetched_count = len(raw_entries)
        run.created_count = created
        run.duplicate_count = duplicates
        run.failed_count = failed

        latest_published_at = max(
            (raw.published_at for raw in raw_entries if raw.published_at is not None),
            default=None,
        )
        oldest_published_at = min(
            (raw.published_at for raw in raw_entries if raw.published_at is not None),
            default=None,
        )
        run.latest_published_at = latest_published_at
        run.oldest_published_at = oldest_published_at

        # Determine run status: success, partial, or failed
        if failed > 0:
            if created > 0 or duplicates > 0:
                run.status = IngestionRunStatus.PARTIAL.value
                run.error_type = "PartialItemProcessingError"
                run.error_message = f"{failed} of {len(raw_entries)} items failed processing"
            else:
                run.status = IngestionRunStatus.FAILED.value
                run.error_type = "AllItemsProcessingFailedError"
                run.error_message = f"All {failed} items failed processing"
        else:
            run.status = IngestionRunStatus.SUCCESS.value

        # Update source.last_success_at strictly on SUCCESS (never on partial or failed)
        if run.status == IngestionRunStatus.SUCCESS.value:
            source.last_success_at = finished_at

        db.commit()
        db.refresh(run)

        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        logger.info(
            "Ingestion completed for '%s' (run_id=%s, status=%s, duration=%dms): fetched=%d, created=%d, duplicates=%d, failed=%d (freshness: latest=%s, oldest=%s)",
            source.name, run.id, run.status, duration_ms, len(raw_entries), created, duplicates, failed,
            latest_published_at.isoformat() if latest_published_at else None,
            oldest_published_at.isoformat() if oldest_published_at else None,
        )

        return IngestionResult(
            ingestion_run_id=run.id,
            source_id=source.id,
            status=run.status,
            fetched=len(raw_entries),
            created=created,
            duplicates=duplicates,
            failed=failed,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            latest_published_at=latest_published_at,
            oldest_published_at=oldest_published_at,
        )

    @staticmethod
    def get_source_status(source: Source, db: Session) -> SourceStatusResponse:
        """Compute technical observability status and dynamic freshness for a Source."""
        # 1. Fetch most recent IngestionRun
        latest_run = db.execute(
            select(IngestionRun)
            .where(IngestionRun.source_id == source.id)
            .order_by(IngestionRun.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        # 2. Determine latest publication date from current entries (or historical runs fallback)
        latest_pub = db.execute(
            select(func.max(Entry.published_at)).where(Entry.source_id == source.id)
        ).scalar()

        if not latest_pub:
            latest_pub = db.execute(
                select(func.max(IngestionRun.latest_published_at)).where(
                    IngestionRun.source_id == source.id,
                    IngestionRun.status.in_([IngestionRunStatus.SUCCESS.value, IngestionRunStatus.PARTIAL.value])
                )
            ).scalar()

        # 3. Freshness evaluation
        warning_hours = 168
        if source.config and isinstance(source.config, dict):
            warning_hours = source.config.get("freshness_warning_hours", 168)

        if latest_pub is None:
            freshness = FreshnessDetail(
                status=FreshnessStatus.UNKNOWN,
                warning_hours=warning_hours,
                hours_since_latest=None
            )
        else:
            now_utc = datetime.now(timezone.utc)
            if latest_pub.tzinfo is None:
                pub_tz = latest_pub.replace(tzinfo=timezone.utc)
            else:
                pub_tz = latest_pub.astimezone(timezone.utc)

            hours_diff = max(0.0, (now_utc - pub_tz).total_seconds() / 3600.0)
            status_val = FreshnessStatus.FRESH if hours_diff <= warning_hours else FreshnessStatus.STALE
            freshness = FreshnessDetail(
                status=status_val,
                warning_hours=warning_hours,
                hours_since_latest=round(hours_diff, 1)
            )

        # 4. Construct last_run summary
        last_run_summary: Optional[IngestionRunSummary] = None
        if latest_run:
            last_run_summary = IngestionRunSummary(
                id=latest_run.id,
                started_at=latest_run.started_at,
                finished_at=latest_run.finished_at,
                status=latest_run.status,
                fetched=latest_run.fetched_count,
                created=latest_run.created_count,
                duplicates=latest_run.duplicate_count,
                failed=latest_run.failed_count,
                duration_ms=latest_run.duration_ms,
            )

        return SourceStatusResponse(
            source_id=source.id,
            name=source.name,
            active=source.active,
            last_run_at=source.last_run_at,
            last_success_at=source.last_success_at,
            last_run_status=latest_run.status if latest_run else None,
            latest_published_at=latest_pub,
            freshness=freshness,
            last_run=last_run_summary,
        )
