"""Tests for TopicCanonicalizationService and hierarchy-aware filtering (Bloque 7G)."""

import uuid
import pytest

from app.models.tracking import TrackingTopic
from app.services.topic_canonicalization_service import (
    TopicHierarchy,
    canonicalize_analysis_topics,
    expand_topic_filter,
    expand_topic_code_filter,
)


@pytest.fixture
def sample_hierarchy():
    """Build a sample hierarchy with 2 branches and a 3-level tree:
    Branch 1:
      competition_law_general (root, priority=10)
        ├── merger_control (child, priority=20)
        │     └── merger_remedies (grandchild, priority=25)
        └── antitrust_general (child, priority=30)
    Branch 2:
      private_enforcement (root, priority=10)
        ├── damages_actions (child, priority=15)
        └── collective_actions (child, priority=20)
    """
    mat_id = uuid.uuid4()
    p1 = TrackingTopic(id=uuid.uuid4(), matrix_id=mat_id, code="competition_law_general", name="General Competition", priority=10)
    c1_1 = TrackingTopic(id=uuid.uuid4(), matrix_id=mat_id, parent_id=p1.id, code="merger_control", name="Merger Control", priority=20)
    gc1_1_1 = TrackingTopic(id=uuid.uuid4(), matrix_id=mat_id, parent_id=c1_1.id, code="merger_remedies", name="Merger Remedies", priority=25)
    c1_2 = TrackingTopic(id=uuid.uuid4(), matrix_id=mat_id, parent_id=p1.id, code="antitrust_general", name="Antitrust", priority=30)

    p2 = TrackingTopic(id=uuid.uuid4(), matrix_id=mat_id, code="private_enforcement", name="Private Enforcement", priority=10)
    c2_1 = TrackingTopic(id=uuid.uuid4(), matrix_id=mat_id, parent_id=p2.id, code="damages_actions", name="Damages", priority=15)
    c2_2 = TrackingTopic(id=uuid.uuid4(), matrix_id=mat_id, parent_id=p2.id, code="collective_actions", name="Collective Actions", priority=20)

    all_topics = [p1, c1_1, gc1_1_1, c1_2, p2, c2_1, c2_2]
    hierarchy = TopicHierarchy(all_topics)
    return hierarchy, {t.code: t for t in all_topics}


def test_parent_only_preserved(sample_hierarchy):
    """When only the parent category is selected, it must be preserved."""
    hierarchy, topics = sample_hierarchy
    input_topics = [
        {"topic_id": topics["private_enforcement"].id, "is_primary": True, "confidence": 0.9}
    ]
    res = canonicalize_analysis_topics(input_topics, hierarchy=hierarchy)
    assert len(res.canonical_topics) == 1
    assert res.canonical_topics[0].topic_code == "private_enforcement"
    assert res.canonical_primary.topic_code == "private_enforcement"
    assert res.removed_parent_codes == []


def test_child_only_preserved(sample_hierarchy):
    """When only a child category is selected, it must be preserved."""
    hierarchy, topics = sample_hierarchy
    input_topics = [
        {"topic_id": topics["merger_control"].id, "is_primary": True, "confidence": 0.85}
    ]
    res = canonicalize_analysis_topics(input_topics, hierarchy=hierarchy)
    assert len(res.canonical_topics) == 1
    assert res.canonical_topics[0].topic_code == "merger_control"
    assert res.canonical_primary.topic_code == "merger_control"


def test_parent_and_child_removes_parent(sample_hierarchy):
    """When both parent and child are selected, parent is removed and child survives."""
    hierarchy, topics = sample_hierarchy
    input_topics = [
        {"topic_id": topics["competition_law_general"].id, "is_primary": False, "confidence": 0.7},
        {"topic_id": topics["merger_control"].id, "is_primary": True, "confidence": 0.95},
    ]
    res = canonicalize_analysis_topics(input_topics, hierarchy=hierarchy)
    codes = [t.topic_code for t in res.canonical_topics]
    assert codes == ["merger_control"]
    assert res.removed_parent_codes == ["competition_law_general"]
    assert res.canonical_primary.topic_code == "merger_control"


def test_parent_and_two_children_removes_parent(sample_hierarchy):
    """When parent and two sibling children are selected, parent is removed and both children survive."""
    hierarchy, topics = sample_hierarchy
    input_topics = [
        {"topic_id": topics["private_enforcement"].id, "is_primary": False, "confidence": 0.9},
        {"topic_id": topics["damages_actions"].id, "is_primary": True, "confidence": 0.95},
        {"topic_id": topics["collective_actions"].id, "is_primary": False, "confidence": 0.85},
    ]
    res = canonicalize_analysis_topics(input_topics, hierarchy=hierarchy)
    codes = set(t.topic_code for t in res.canonical_topics)
    assert codes == {"damages_actions", "collective_actions"}
    assert res.removed_parent_codes == ["private_enforcement"]
    assert res.canonical_primary.topic_code == "damages_actions"


def test_two_different_branches(sample_hierarchy):
    """In branch A parent+child (parent removed); in branch B only parent (parent kept)."""
    hierarchy, topics = sample_hierarchy
    input_topics = [
        # Branch 1: parent + child
        {"topic_id": topics["competition_law_general"].id, "is_primary": False, "confidence": 0.7},
        {"topic_id": topics["merger_control"].id, "is_primary": True, "confidence": 0.95},
        # Branch 2: parent only
        {"topic_id": topics["private_enforcement"].id, "is_primary": False, "confidence": 0.8},
    ]
    res = canonicalize_analysis_topics(input_topics, hierarchy=hierarchy)
    codes = set(t.topic_code for t in res.canonical_topics)
    assert codes == {"merger_control", "private_enforcement"}
    assert res.removed_parent_codes == ["competition_law_general"]


def test_three_level_hierarchy_removes_all_ancestors(sample_hierarchy):
    """Grandparent + parent + grandchild -> only grandchild survives."""
    hierarchy, topics = sample_hierarchy
    input_topics = [
        {"topic_id": topics["competition_law_general"].id, "is_primary": True, "confidence": 0.6},
        {"topic_id": topics["merger_control"].id, "is_primary": False, "confidence": 0.8},
        {"topic_id": topics["merger_remedies"].id, "is_primary": False, "confidence": 0.95},
    ]
    res = canonicalize_analysis_topics(input_topics, hierarchy=hierarchy)
    codes = [t.topic_code for t in res.canonical_topics]
    assert codes == ["merger_remedies"]
    assert set(res.removed_parent_codes) == {"competition_law_general", "merger_control"}
    assert res.canonical_primary.topic_code == "merger_remedies"


def test_canonical_primary_raw_survives(sample_hierarchy):
    """If the raw primary topic is a child and survives, it remains canonical primary."""
    hierarchy, topics = sample_hierarchy
    input_topics = [
        {"topic_id": topics["private_enforcement"].id, "is_primary": False, "confidence": 0.99},
        {"topic_id": topics["collective_actions"].id, "is_primary": True, "confidence": 0.70},
    ]
    res = canonicalize_analysis_topics(input_topics, hierarchy=hierarchy)
    assert res.canonical_primary.topic_code == "collective_actions"
    assert res.canonical_primary.is_primary is True


def test_canonical_primary_parent_removed_selects_highest_confidence_descendant(sample_hierarchy):
    """If raw primary is the parent that gets removed, select among descendants by highest confidence."""
    hierarchy, topics = sample_hierarchy
    input_topics = [
        {"topic_id": topics["private_enforcement"].id, "is_primary": True, "confidence": 0.99},
        {"topic_id": topics["damages_actions"].id, "is_primary": False, "confidence": 0.80},
        {"topic_id": topics["collective_actions"].id, "is_primary": False, "confidence": 0.92},
    ]
    res = canonicalize_analysis_topics(input_topics, hierarchy=hierarchy)
    # collective_actions has higher confidence (0.92 > 0.80)
    assert res.canonical_primary.topic_code == "collective_actions"
    assert res.canonical_primary.is_primary is True


def test_confidence_tie_breaks_by_priority(sample_hierarchy):
    """If confidence ties, pick by lowest priority (display_order)."""
    hierarchy, topics = sample_hierarchy
    # damages_actions has priority=15, collective_actions has priority=20
    input_topics = [
        {"topic_id": topics["private_enforcement"].id, "is_primary": True, "confidence": 0.99},
        {"topic_id": topics["damages_actions"].id, "is_primary": False, "confidence": 0.90},
        {"topic_id": topics["collective_actions"].id, "is_primary": False, "confidence": 0.90},
    ]
    res = canonicalize_analysis_topics(input_topics, hierarchy=hierarchy)
    assert res.canonical_primary.topic_code == "damages_actions"


def test_priority_tie_breaks_by_code_asc(sample_hierarchy):
    """If priority also ties, pick by code ASC."""
    mat_id = uuid.uuid4()
    p = TrackingTopic(id=uuid.uuid4(), matrix_id=mat_id, code="parent", name="Parent", priority=10)
    c_b = TrackingTopic(id=uuid.uuid4(), matrix_id=mat_id, parent_id=p.id, code="child_beta", name="Beta", priority=20)
    c_a = TrackingTopic(id=uuid.uuid4(), matrix_id=mat_id, parent_id=p.id, code="child_alpha", name="Alpha", priority=20)

    hierarchy = TopicHierarchy([p, c_b, c_a])
    input_topics = [
        {"topic_id": p.id, "is_primary": True, "confidence": 0.99},
        {"topic_id": c_b.id, "is_primary": False, "confidence": 0.90},
        {"topic_id": c_a.id, "is_primary": False, "confidence": 0.90},
    ]
    res = canonicalize_analysis_topics(input_topics, hierarchy=hierarchy)
    # 'child_alpha' < 'child_beta'
    assert res.canonical_primary.topic_code == "child_alpha"


def test_filter_parent_expands_descendants(sample_hierarchy):
    """Filtering by parent topic expands to include parent and all descendants."""
    hierarchy, topics = sample_hierarchy
    p_id = topics["private_enforcement"].id
    expanded_ids = expand_topic_filter(p_id, hierarchy=hierarchy)
    assert p_id in expanded_ids
    assert topics["damages_actions"].id in expanded_ids
    assert topics["collective_actions"].id in expanded_ids
    assert topics["competition_law_general"].id not in expanded_ids

    # Test code expansion
    expanded_codes = expand_topic_code_filter("private_enforcement", hierarchy=hierarchy)
    assert expanded_codes == {"private_enforcement", "damages_actions", "collective_actions"}

    # Test 3-level expansion
    expanded_merger = expand_topic_code_filter("competition_law_general", hierarchy=hierarchy)
    assert expanded_merger == {
        "competition_law_general",
        "merger_control",
        "merger_remedies",
        "antitrust_general",
    }
