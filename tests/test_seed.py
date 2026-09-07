"""Tests for seed_tracking_v01 idempotency and correctness."""

from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import TestingSessionLocal
from scripts.seed_tracking_v01 import seed
from app.models.source import Source
from app.models.tracking import TrackingMatrix, TrackingTopic, TrackedEntity, TrackedEntityTopic


def test_seed_idempotency_and_integrity() -> None:
    """Test that seed_tracking_v01 runs idempotently and populates expected counts."""
    with patch("scripts.seed_tracking_v01.SessionLocal", TestingSessionLocal):
        # 1. First execution
        first_run = seed()
        assert first_run["matrices_created"] == 1
        assert first_run["matrices_existing"] == 0
        assert first_run["topics_created"] == 18  # 2 main + 16 subtopics
        assert first_run["topics_existing"] == 0
        assert first_run["entities_created"] == 38  # 9 general + 20 private + 5 inst + 4 pub
        assert first_run["entities_existing"] == 0
        assert first_run["associations_created"] == 38
        assert first_run["associations_existing"] == 0

        # 2. Second execution (must not duplicate anything)
        second_run = seed()
        assert second_run["matrices_created"] == 0
        assert second_run["matrices_existing"] == 1
        assert second_run["topics_created"] == 0
        assert second_run["topics_existing"] == 18
        assert second_run["entities_created"] == 0
        assert second_run["entities_existing"] == 38
        assert second_run["associations_created"] == 0
        assert second_run["associations_existing"] == 38

        # 3. Verify database state
        db = TestingSessionLocal()
        try:
            # Check matrix
            matrix = db.execute(select(TrackingMatrix).where(TrackingMatrix.code == "HITCHINGS-v0.1")).scalar_one()
            assert matrix.status == "active"
            assert matrix.activated_at is not None

            # Check main topics
            main_topics = db.execute(
                select(TrackingTopic).where(
                    TrackingTopic.matrix_id == matrix.id,
                    TrackingTopic.parent_id.is_(None)
                )
            ).scalars().all()
            assert len(main_topics) == 2
            main_codes = {t.code for t in main_topics}
            assert main_codes == {"competition_law_general", "private_enforcement"}
            for t in main_topics:
                assert t.provisional is False  # Derived directly from client doc

            # Check subtopics
            subtopics = db.execute(
                select(TrackingTopic).where(
                    TrackingTopic.matrix_id == matrix.id,
                    TrackingTopic.parent_id.is_not(None)
                )
            ).scalars().all()
            assert len(subtopics) == 16
            for st in subtopics:
                assert st.provisional is True  # Proposed internally

            # Check entities
            entities = db.execute(select(TrackedEntity)).scalars().all()
            assert len(entities) == 38

            # Check that NO technical sources were created with invented URLs
            sources_count = len(db.execute(select(Source)).scalars().all())
            assert sources_count == 0

            # Check associations
            associations = db.execute(select(TrackedEntityTopic)).scalars().all()
            assert len(associations) == 38

        finally:
            db.close()
