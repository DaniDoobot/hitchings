"""Deterministic query and job planner for LinkedIn post discovery (Bloque 9C)."""

import logging
import uuid
from dataclasses import dataclass
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tracking import TrackingMatrix, TrackedEntity

logger = logging.getLogger(__name__)


@dataclass
class LinkedInDiscoveryJob:
    """Represents a scheduled discovery job for a single verified LinkedIn entity."""

    job_id: str
    tracked_entity_id: uuid.UUID
    entity_name: str
    linkedin_url: str
    entity_type: str
    provider: str
    priority: int


class LinkedInDiscoveryPlanner:
    """Plans discovery jobs strictly for active tracked entities with verified LinkedIn URLs."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def plan_jobs(
        self,
        db: Session,
        max_entities: Optional[int] = None,
    ) -> list[LinkedInDiscoveryJob]:
        """Generate a deterministic list of LinkedIn discovery jobs.
        
        Rules:
        1. Must find active TrackingMatrix.
        2. Must only evaluate active TrackedEntity records.
        3. Must only include entities with verified 'linkedin_url' in metadata.
        4. Deduplicates LinkedIn URLs (only one job per unique URL).
        5. Orders deterministically by priority DESC, display_name ASC.
        6. Applies max_entities limit.
        """
        cap = max_entities if max_entities is not None else self.settings.LINKEDIN_MAX_ENTITIES_PER_RUN
        primary_provider = self.settings.LINKEDIN_PRIMARY_PROVIDER

        # 1. Active matrix
        matrix = db.execute(
            select(TrackingMatrix).where(TrackingMatrix.status == "active")
        ).scalar_one_or_none()

        if not matrix:
            logger.warning("No active TrackingMatrix found; skipping LinkedIn discovery planning.")
            return []

        # 2. Active entities
        entities = db.execute(
            select(TrackedEntity).where(TrackedEntity.active.is_(True))
        ).scalars().all()

        candidate_jobs: list[LinkedInDiscoveryJob] = []
        seen_urls: set[str] = set()

        for entity in entities:
            meta = entity.metadata_ or {}
            raw_url = meta.get("linkedin_url")
            if not raw_url or not isinstance(raw_url, str):
                continue

            clean_url = raw_url.strip()
            if not (clean_url.startswith("https://") or clean_url.startswith("http://")):
                continue

            # Normalize URL for deduplication (strip trailing slash and lowercase domain/path)
            normalized_url = clean_url.rstrip("/").lower()
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)

            # Assign priority based on entity type
            etype = str(entity.entity_type).lower()
            if etype == "institution":
                priority = 90
            elif etype == "organization":
                priority = 80
            elif etype == "person":
                priority = 70
            else:
                priority = 50

            # Deterministic job UUID
            job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"linkedin-job:{entity.id}:{normalized_url}"))

            job = LinkedInDiscoveryJob(
                job_id=job_id,
                tracked_entity_id=entity.id,
                entity_name=entity.display_name,
                linkedin_url=clean_url,
                entity_type=meta.get("linkedin_entity_type") or entity.entity_type,
                provider=primary_provider,
                priority=priority,
            )
            candidate_jobs.append(job)

        # 3. Deterministic sort: priority DESC, display_name ASC
        candidate_jobs.sort(key=lambda j: (-j.priority, j.entity_name))

        # 4. Apply cap
        selected_jobs = candidate_jobs[:cap]

        logger.info(
            "LinkedIn discovery planner: %d verified candidates found, %d jobs planned (cap=%d)",
            len(candidate_jobs),
            len(selected_jobs),
            cap,
        )
        return selected_jobs
