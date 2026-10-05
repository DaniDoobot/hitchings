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

    Prevents combinatorial explosion and starvation by allocating quotas across
    institutional entities and substantive topics, with two-round fair interleaving (4T : 1E).
    """

    def __init__(
        self,
        languages: Sequence[str] = ("es", "en"),
        region: str = "ES",
        max_queries: int = 25,
        max_entity_queries: int = 5,
    ) -> None:
        self.languages = [lang.strip().lower() for lang in languages if lang.strip()]
        if not self.languages:
            self.languages = ["es", "en"]
        self.region = region.upper()
        self.max_queries = max(1, min(max_queries, 50))  # Capped between 1 and 50
        self.max_entity_queries = max(0, min(max_entity_queries, self.max_queries))

    def plan_queries(self, db: Session) -> list[PlannedQuery]:
        """Generate deduplicated, prioritized list of search queries from database tracking models."""
        active_matrix = (
            db.query(TrackingMatrix)
            .filter(TrackingMatrix.status == "active")
            .first()
        )
        if not active_matrix:
            return []

        # Load active topics from active matrix, ordered by priority DESC, code ASC
        topics = (
            db.query(TrackingTopic)
            .filter(
                TrackingTopic.matrix_id == active_matrix.id,
                TrackingTopic.active == True,
            )
            .order_by(TrackingTopic.priority.desc(), TrackingTopic.code.asc())
            .all()
        )

        # Load active entities
        entities = (
            db.query(TrackedEntity)
            .filter(TrackedEntity.active == True)
            .all()
        )

        seen_queries: set[tuple[str, str]] = set()  # (normalized_text_lower, language)
        counter = 0

        def build_planned_query(
            text: str,
            lang: str,
            priority: int,
            entity: Optional[TrackedEntity] = None,
            topic_codes: Sequence[str] = (),
        ) -> Optional[PlannedQuery]:
            nonlocal counter
            clean_text = _normalize_query_text(text)
            if not clean_text:
                return None
            key = (clean_text.lower(), lang.lower())
            if key in seen_queries:
                return None
            seen_queries.add(key)
            counter += 1
            reg = "ES" if lang == "es" else ("GB" if self.region == "ES" else self.region)
            return PlannedQuery(
                query_id=f"gn_{lang}_{counter:03d}",
                query_text=clean_text,
                language=lang,
                region=reg,
                priority=priority,
                entity_id=entity.id if entity else None,
                entity_name=entity.display_name if entity else None,
                topic_codes=tuple(topic_codes),
            )

        # 1. High-Priority Institutional & Organization Entities
        target_entities = [
            e for e in entities if e.entity_type in {"institution", "organization"}
        ]
        # Sort deterministically: institutions (priority 90) before organizations (priority 75), then display_name
        target_entities.sort(
            key=lambda e: (0 if e.entity_type == "institution" else 1, e.display_name.lower())
        )

        entity_candidates: list[PlannedQuery] = []
        for lang in self.languages:
            for ent in target_entities:
                clean_name = ent.display_name.replace('"', "").strip()
                if not clean_name:
                    continue

                assoc_topic_codes = [
                    assoc.topic.code
                    for assoc in ent.topic_associations
                    if assoc.topic and assoc.topic.active
                ]
                primary_topic = assoc_topic_codes[0] if assoc_topic_codes else "competition_law_general"
                topic_codes = assoc_topic_codes or [primary_topic]
                prio = 90 if ent.entity_type == "institution" else 75

                suffix = "competencia" if lang == "es" else "competition"
                q = build_planned_query(f'"{clean_name}" {suffix}', lang, prio, ent, topic_codes)
                if q:
                    entity_candidates.append(q)

        # 2. Topic Discovery Queries (Round 1 guaranteed anchor + Round 2 prioritized)
        round1_topic_queries: list[PlannedQuery] = []
        round2_topic_queries: list[PlannedQuery] = []

        for topic in topics:
            if not topic.discovery_queries:
                continue

            topic_candidates: list[PlannedQuery] = []
            for query_template in topic.discovery_queries:
                clean_template = _normalize_query_text(query_template)
                if not clean_template:
                    continue

                is_spanish = any(
                    word in clean_template.lower()
                    for word in (
                        "competencia", "daños", "cártel", "acuerdos", "reclamación",
                        "resolución", "sanción", "tribunal", "derecho", "sentencia",
                        "juzgado", "cuantificación", "exhibición", "financiación",
                        "desleal", "inteligencia", "propiedad"
                    )
                )
                query_lang = "es" if is_spanish else "en"

                if query_lang in self.languages:
                    q = build_planned_query(
                        clean_template,
                        query_lang,
                        priority=topic.priority,
                        topic_codes=[topic.code],
                    )
                    if q:
                        topic_candidates.append(q)

            if topic_candidates:
                # First valid query is the topic's Anchor for Round 1
                round1_topic_queries.append(topic_candidates[0])
                # Subsequent valid queries become candidates for Round 2
                for q in topic_candidates[1:]:
                    round2_topic_queries.append(q)

        # 3. Quota Resolution and Selection
        effective_max_entities = min(self.max_entity_queries, len(entity_candidates))
        # If total budget is small, reserve at least 1 slot for topics when topics exist
        if len(round1_topic_queries) > 0 and self.max_queries > 1:
            effective_max_entities = min(effective_max_entities, self.max_queries - 1)

        selected_entity_queries = entity_candidates[:effective_max_entities]
        topic_budget = max(0, self.max_queries - len(selected_entity_queries))

        if len(round1_topic_queries) >= topic_budget:
            selected_topic_queries = round1_topic_queries[:topic_budget]
        else:
            remaining_topic_slots = topic_budget - len(round1_topic_queries)
            selected_topic_queries = round1_topic_queries + round2_topic_queries[:remaining_topic_slots]

        # Elastic reallocation: if topics didn't fill their budget, entities can use surplus
        unused_topic_budget = topic_budget - len(selected_topic_queries)
        if unused_topic_budget > 0:
            extra_entities = entity_candidates[
                len(selected_entity_queries) : len(selected_entity_queries) + unused_topic_budget
            ]
            selected_entity_queries.extend(extra_entities)

        # 4. Deterministic Interleaving: 4 Topics per 1 Entity (4T : 1E)
        interleaved: list[PlannedQuery] = []
        t_idx = 0
        e_idx = 0
        t_len = len(selected_topic_queries)
        e_len = len(selected_entity_queries)

        while t_idx < t_len or e_idx < e_len:
            # Take up to 4 topic queries
            for _ in range(4):
                if t_idx < t_len:
                    interleaved.append(selected_topic_queries[t_idx])
                    t_idx += 1
            # Take 1 entity query
            if e_idx < e_len:
                interleaved.append(selected_entity_queries[e_idx])
                e_idx += 1

        final_queries = interleaved[: self.max_queries]

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
