"""Service for canonicalizing analysis topic assignments and hierarchy-aware filtering.

Addresses the parent+child redundancy in LLM topic classification:
When a model selects both a general category (parent/ancestor, e.g. 'private_enforcement')
and one or more specific subtopics (child/descendant, e.g. 'damages_actions'),
the specific subtopic logically subsumes the ancestor.

This service produces a canonical topic view WITHOUT altering historical EntryAnalysisTopic records.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence, Union

from sqlalchemy.orm import Session

from app.models.analysis import EntryAnalysisTopic
from app.models.tracking import TrackingMatrix, TrackingTopic


@dataclass(frozen=True)
class CanonicalTopicItem:
    """Represents a topic item in the canonical or raw view."""
    topic_id: uuid.UUID
    topic_code: str
    topic_name: str
    confidence: Optional[float]
    is_primary: bool
    raw_is_primary: bool
    priority: int = 0
    parent_id: Optional[uuid.UUID] = None


@dataclass(frozen=True)
class CanonicalAnalysisTopicsResult:
    """Full canonicalization result for an analysis."""
    canonical_topics: list[CanonicalTopicItem]
    canonical_primary: Optional[CanonicalTopicItem]
    raw_topics: list[CanonicalTopicItem]
    raw_primary: Optional[CanonicalTopicItem]
    removed_parent_codes: list[str]


class TopicHierarchy:
    """In-memory topic hierarchy lookup for fast graph/tree traversal."""

    def __init__(self, topics: Iterable[TrackingTopic]) -> None:
        self.by_id: dict[uuid.UUID, TrackingTopic] = {}
        self.by_code: dict[str, TrackingTopic] = {}
        self.parent_map: dict[uuid.UUID, Optional[uuid.UUID]] = {}
        self.children_map: dict[uuid.UUID, list[uuid.UUID]] = {}

        for t in topics:
            self.by_id[t.id] = t
            self.by_code[t.code] = t
            self.parent_map[t.id] = t.parent_id
            if t.id not in self.children_map:
                self.children_map[t.id] = []
            if t.parent_id:
                self.children_map.setdefault(t.parent_id, []).append(t.id)

    def get_ancestor_ids(self, topic_id: uuid.UUID) -> set[uuid.UUID]:
        """Return all ancestor IDs (parent, grandparent, etc.) for topic_id."""
        ancestors = set()
        curr = self.parent_map.get(topic_id)
        while curr:
            if curr in ancestors:
                break  # guard against cycles
            ancestors.add(curr)
            curr = self.parent_map.get(curr)
        return ancestors

    def get_descendant_ids(self, topic_id: uuid.UUID) -> set[uuid.UUID]:
        """Return all descendant IDs (children, grandchildren, etc.) for topic_id."""
        descendants = set()
        stack = list(self.children_map.get(topic_id, []))
        while stack:
            curr = stack.pop()
            if curr not in descendants:
                descendants.add(curr)
                stack.extend(self.children_map.get(curr, []))
        return descendants


def build_topic_hierarchy(db: Session, matrix_id: Optional[uuid.UUID] = None) -> TopicHierarchy:
    """Load TrackingTopics from database and construct TopicHierarchy.
    
    If matrix_id is None, defaults to the currently active TrackingMatrix.
    """
    if matrix_id is None:
        active_matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
        if active_matrix is not None:
            matrix_id = active_matrix.id
    query = db.query(TrackingTopic)
    if matrix_id is not None:
        query = query.filter(TrackingTopic.matrix_id == matrix_id)
    return TopicHierarchy(query.all())


def canonicalize_analysis_topics(
    analysis_topics: Sequence[Union[EntryAnalysisTopic, dict[str, Any], Any]],
    hierarchy: Optional[TopicHierarchy] = None,
    db: Optional[Session] = None,
) -> CanonicalAnalysisTopicsResult:
    """Compute canonical topics by removing redundant ancestors when descendants are selected.

    Rules:
    1. If both an ancestor and one or more of its descendants are in the selected topics,
       the ancestor is removed from canonical_topics (the specific leaf/child subsumes it).
    2. If an ancestor is selected and NO descendant is selected, the ancestor is KEPT.
    3. Works for arbitrary hierarchy depth (parent -> child -> grandchild).
    4. Canonical Primary Topic selection:
       - If raw primary survives in canonical_topics, canonical_primary = raw_primary.
       - If raw primary was an ancestor removed because one or more descendants are selected:
         choose among its selected descendants:
           a. Highest confidence (treating None as -1.0)
           b. Lowest display_order / priority (ascending)
           c. topic.code ASC (lexicographical)
       - If no raw primary was marked, fallback to highest confidence / lowest priority among canonical topics.
    """
    if hierarchy is None:
        if db is None:
            raise ValueError("Either hierarchy or db must be provided to canonicalize_analysis_topics")
        hierarchy = build_topic_hierarchy(db)

    # 1. Normalize input topics to raw CanonicalTopicItem
    raw_items: list[CanonicalTopicItem] = []
    for at in analysis_topics:
        topic_obj: Optional[TrackingTopic] = None
        topic_id: Optional[uuid.UUID] = None
        confidence: Optional[float] = None
        is_primary: bool = False

        if isinstance(at, EntryAnalysisTopic):
            topic_id = at.topic_id
            confidence = at.confidence
            is_primary = at.is_primary
            topic_obj = at.topic or hierarchy.by_id.get(at.topic_id)
        elif isinstance(at, dict):
            topic_id = at.get("topic_id")
            confidence = at.get("confidence")
            is_primary = at.get("is_primary", False)
            if topic_id and topic_id in hierarchy.by_id:
                topic_obj = hierarchy.by_id[topic_id]
            elif "topic_code" in at and at["topic_code"] in hierarchy.by_code:
                topic_obj = hierarchy.by_code[at["topic_code"]]
                topic_id = topic_obj.id
        else:
            topic_id = getattr(at, "topic_id", None)
            confidence = getattr(at, "confidence", None)
            is_primary = getattr(at, "is_primary", False)
            if hasattr(at, "topic") and getattr(at, "topic"):
                topic_obj = getattr(at, "topic")
            elif topic_id and topic_id in hierarchy.by_id:
                topic_obj = hierarchy.by_id[topic_id]

        if not topic_obj and topic_id and topic_id in hierarchy.by_id:
            topic_obj = hierarchy.by_id[topic_id]

        if topic_obj and topic_id:
            active_topic = hierarchy.by_code.get(topic_obj.code)
            effective_id = active_topic.id if active_topic else topic_id
            effective_name = active_topic.name if active_topic else topic_obj.name
            effective_parent_id = active_topic.parent_id if active_topic else topic_obj.parent_id
            effective_priority = active_topic.priority if active_topic else getattr(topic_obj, "priority", 0)

            raw_items.append(
                CanonicalTopicItem(
                    topic_id=effective_id,
                    topic_code=topic_obj.code,
                    topic_name=effective_name,
                    confidence=confidence,
                    is_primary=is_primary,
                    raw_is_primary=is_primary,
                    priority=effective_priority,
                    parent_id=effective_parent_id,
                )
            )

    selected_ids = {item.topic_id for item in raw_items}
    item_by_id = {item.topic_id: item for item in raw_items}

    raw_primary = next((item for item in raw_items if item.is_primary), None)

    # 2. Identify redundant ancestors
    # An ancestor A is redundant if there exists any D in selected_ids such that D is in descendants(A).
    redundant_ancestor_ids: set[uuid.UUID] = set()
    for item in raw_items:
        descendants = hierarchy.get_descendant_ids(item.topic_id)
        if descendants.intersection(selected_ids):
            redundant_ancestor_ids.add(item.topic_id)

    removed_codes = [
        item_by_id[tid].topic_code for tid in redundant_ancestor_ids if tid in item_by_id
    ]

    surviving_items = [
        item for item in raw_items if item.topic_id not in redundant_ancestor_ids
    ]

    # 3. Resolve Canonical Primary Topic
    canonical_primary_id: Optional[uuid.UUID] = None

    if raw_primary and raw_primary.topic_id not in redundant_ancestor_ids:
        # Raw primary survived
        canonical_primary_id = raw_primary.topic_id
    elif raw_primary and raw_primary.topic_id in redundant_ancestor_ids:
        # Raw primary was an ancestor removed by descendants; choose best candidate among its selected descendants
        raw_primary_descendants = hierarchy.get_descendant_ids(raw_primary.topic_id)
        eligible_descendants = [
            item for item in surviving_items if item.topic_id in raw_primary_descendants
        ]

        if not eligible_descendants:
            # Fallback if no direct surviving descendants (e.g. cross-branch)
            eligible_descendants = list(surviving_items)

        if eligible_descendants:
            # Sort order:
            # 1. Highest confidence (None -> -1.0)
            # 2. Lowest priority / display_order (ascending)
            # 3. topic_code ASC (ascending)
            eligible_descendants.sort(
                key=lambda x: (
                    -(x.confidence if x.confidence is not None else -1.0),
                    x.priority,
                    x.topic_code,
                )
            )
            canonical_primary_id = eligible_descendants[0].topic_id
    elif surviving_items:
        # No raw primary set; pick best from surviving items
        surviving_items_sorted = list(surviving_items)
        surviving_items_sorted.sort(
            key=lambda x: (
                -(x.confidence if x.confidence is not None else -1.0),
                x.priority,
                x.topic_code,
            )
        )
        canonical_primary_id = surviving_items_sorted[0].topic_id

    # 4. Construct final canonical topic items with is_primary updated
    final_canonical_items: list[CanonicalTopicItem] = []
    canonical_primary_item: Optional[CanonicalTopicItem] = None

    for item in surviving_items:
        is_canon_primary = item.topic_id == canonical_primary_id
        canon_item = CanonicalTopicItem(
            topic_id=item.topic_id,
            topic_code=item.topic_code,
            topic_name=item.topic_name,
            confidence=item.confidence,
            is_primary=is_canon_primary,
            raw_is_primary=item.raw_is_primary,
            priority=item.priority,
            parent_id=item.parent_id,
        )
        final_canonical_items.append(canon_item)
        if is_canon_primary:
            canonical_primary_item = canon_item

    return CanonicalAnalysisTopicsResult(
        canonical_topics=final_canonical_items,
        canonical_primary=canonical_primary_item,
        raw_topics=raw_items,
        raw_primary=raw_primary,
        removed_parent_codes=removed_codes,
    )


def expand_topic_filter(
    topic_id_or_code: Union[uuid.UUID, str],
    hierarchy: Optional[TopicHierarchy] = None,
    db: Optional[Session] = None,
) -> set[uuid.UUID]:
    """Expand a topic filter to include the topic itself and all its descendants.

    Given a parent topic (e.g. 'private_enforcement'), returns the topic ID itself
    PLUS all child/descendant IDs (e.g. 'damages_actions', 'collective_actions', etc.).
    This enables seamless filtering without requiring redundant parent tags.
    """
    if hierarchy is None:
        if db is None:
            raise ValueError("Either hierarchy or db must be provided to expand_topic_filter")
        hierarchy = build_topic_hierarchy(db)

    target_id: Optional[uuid.UUID] = None
    if isinstance(topic_id_or_code, uuid.UUID):
        target_id = topic_id_or_code
    elif isinstance(topic_id_or_code, str):
        # Try UUID parse, else code lookup
        try:
            target_id = uuid.UUID(topic_id_or_code)
        except ValueError:
            topic_obj = hierarchy.by_code.get(topic_id_or_code)
            if topic_obj:
                target_id = topic_obj.id

    if not target_id or target_id not in hierarchy.by_id:
        return set()

    result = {target_id}
    result.update(hierarchy.get_descendant_ids(target_id))
    return result


def expand_topic_code_filter(
    topic_code: str,
    hierarchy: Optional[TopicHierarchy] = None,
    db: Optional[Session] = None,
) -> set[str]:
    """Expand a topic code filter to return a set of topic codes including the topic and all descendants."""
    if hierarchy is None:
        if db is None:
            raise ValueError("Either hierarchy or db must be provided to expand_topic_code_filter")
        hierarchy = build_topic_hierarchy(db)

    topic_obj = hierarchy.by_code.get(topic_code)
    if not topic_obj:
        return {topic_code}

    expanded_ids = expand_topic_filter(topic_obj.id, hierarchy=hierarchy)
    return {hierarchy.by_id[tid].code for tid in expanded_ids if tid in hierarchy.by_id}
