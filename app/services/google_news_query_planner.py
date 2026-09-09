"""Deterministic query planner for Google News discovery ingestion (Bloque 9A)."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional, Sequence
import uuid

from sqlalchemy.orm import Session

from app.models.tracking import TrackedEntity, TrackingMatrix, TrackingTopic


@dataclass(frozen=True)
class PlannedQuery:
    """Represents a planned Google News search query."""
    query_id: str
    query_text: str
    language: str
    region: str
    priority: int
    entity_id: Optional[uuid.UUID] = None
    entity_name: Optional[str] = None
    topic_codes: tuple[str, ...] = ()


def _normalize_query_text(text: str) -> str:
    """Normalize query text for deduplication: collapse whitespace and strip."""
    return re.sub(r"\s+", " ", text).strip()


class GoogleNewsQueryPlanner:
    """Generates a controlled, deterministic set of search queries from the tracking matrix.
    
    Prevents combinatorial explosion by prioritizing institutional entities,
    known litigation organizations, and top-tier canonical topics.
    """

    def __init__(
        self,
        languages: Sequence[str] = ("es", "en"),
        region: str = "ES",
        max_queries: int = 20,
    ) -> None:
        self.languages = [lang.strip().lower() for lang in languages if lang.strip()]
        if not self.languages:
            self.languages = ["es", "en"]
        self.region = region.upper()
        self.max_queries = max(1, min(max_queries, 50))  # Capped between 1 and 50

    def plan_queries(self, db: Session) -> list[PlannedQuery]:
        """Generate deduplicated, prioritized list of search queries from database tracking models."""
        active_matrix = (
            db.query(TrackingMatrix)
            .filter(TrackingMatrix.status == "active")
            .first()
        )
        if not active_matrix:
            return []

        # Load active topics from active matrix
        topics = (
            db.query(TrackingTopic)
            .filter(
                TrackingTopic.matrix_id == active_matrix.id,
                TrackingTopic.active == True,
            )
            .order_by(TrackingTopic.priority.desc())
            .all()
        )

        # Load active entities
        entities = (
            db.query(TrackedEntity)
            .filter(TrackedEntity.active == True)
            .all()
        )

        seen_queries: set[tuple[str, str]] = set()  # (normalized_text_lower, language)
        candidates: list[PlannedQuery] = []
        counter = 0

        def add_query(
            text: str,
            lang: str,
            priority: int,
            entity: Optional[TrackedEntity] = None,
            topic_codes: Sequence[str] = (),
        ) -> None:
            nonlocal counter
            clean_text = _normalize_query_text(text)
            if not clean_text:
                return
            key = (clean_text.lower(), lang.lower())
            if key in seen_queries:
                return
            seen_queries.add(key)
            counter += 1
            reg = "ES" if lang == "es" else ("GB" if self.region == "ES" else self.region)
            candidates.append(
                PlannedQuery(
                    query_id=f"gn_{lang}_{counter:03d}",
                    query_text=clean_text,
                    language=lang,
                    region=reg,
                    priority=priority,
                    entity_id=entity.id if entity else None,
                    entity_name=entity.display_name if entity else None,
                    topic_codes=tuple(topic_codes),
                )
            )

        # 1. High-Priority Institutional & Organization Entities
        # Focus on institutions and competition organizations first
        target_entities = [
            e for e in entities if e.entity_type in {"institution", "organization"}
        ]
        # Sort deterministically by display name
        target_entities.sort(key=lambda e: e.display_name)

        for ent in target_entities:
            name = ent.display_name
            # Resolve associated topic codes
            assoc_topic_codes = [
                assoc.topic.code
                for assoc in ent.topic_associations
                if assoc.topic and assoc.topic.active
            ]
            primary_topic = assoc_topic_codes[0] if assoc_topic_codes else "competition_law_general"

            if "Comisión Nacional de los Mercados y la Competencia" in name or "CNMC" in name:
                if "es" in self.languages:
                    add_query('CNMC competencia', "es", 95, ent, [primary_topic])
                    add_query('"Comisión Nacional de los Mercados y la Competencia" competencia', "es", 90, ent, [primary_topic])
            elif "European Commission" in name:
                if "en" in self.languages:
                    add_query('"European Commission" competition antitrust', "en", 95, ent, [primary_topic])
                    add_query('"European Commission" merger clearance', "en", 85, ent, ["merger_control"])
                if "es" in self.languages:
                    add_query('"Comisión Europea" competencia', "es", 90, ent, [primary_topic])
            elif "Competition Appeal Tribunal" in name or "CAT" in name:
                if "en" in self.languages:
                    add_query('"Competition Appeal Tribunal" judgment', "en", 90, ent, [primary_topic])
            elif "Tribunal de Justicia de la Unión Europea" in name or "CURIA" in name or "TJUE" in name:
                if "es" in self.languages:
                    add_query('"Tribunal de Justicia de la Unión Europea" competencia', "es", 90, ent, [primary_topic])
                if "en" in self.languages:
                    add_query('"Court of Justice of the European Union" competition', "en", 90, ent, [primary_topic])
            elif ent.entity_type == "organization":
                # Known private enforcement litigation firms/entities
                if "en" in self.languages:
                    add_query(f'"{name}" competition cartel', "en", 75, ent, [primary_topic])
                if "es" in self.languages:
                    add_query(f'"{name}" cártel competencia', "es", 75, ent, [primary_topic])

        # 2. Topic Discovery Queries (extracted from active TrackingTopic discovery_queries)
        for topic in topics:
            if not topic.discovery_queries:
                continue

            for query_template in topic.discovery_queries:
                clean_template = _normalize_query_text(query_template)
                if not clean_template:
                    continue

                # Determine language heuristic of the discovery query string
                is_spanish = any(
                    word in clean_template.lower()
                    for word in ("competencia", "daños", "cártel", "acuerdos", "reclamación")
                )
                query_lang = "es" if is_spanish else "en"

                if query_lang in self.languages:
                    add_query(
                        clean_template,
                        query_lang,
                        priority=topic.priority,
                        topic_codes=[topic.code],
                    )

        # 3. Deterministic Sorting and Truncation to max_queries
        # Sort by: priority DESC, then query_text ASC (for 100% stable deterministic ordering)
        candidates.sort(key=lambda q: (-q.priority, q.query_text))

        final_queries = candidates[: self.max_queries]
        # Re-index query IDs to be sequential and clean
        return [
            PlannedQuery(
                query_id=f"gn_{q.language}_{idx + 1:03d}",
                query_text=q.query_text,
                language=q.language,
                region=q.region,
                priority=q.priority,
                entity_id=q.entity_id,
                entity_name=q.entity_name,
                topic_codes=q.topic_codes,
            )
            for idx, q in enumerate(final_queries)
        ]
