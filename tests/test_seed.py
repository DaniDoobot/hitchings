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
        assert first_run["topics_created"] == 20  # 2 main + 18 subtopics
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
        assert second_run["topics_existing"] == 20
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
            assert len(subtopics) == 18
            subtopics_by_code = {st.code: st for st in subtopics}

            # Verify specific required topics exist
            assert "unfair_competition" in subtopics_by_code
            uc = subtopics_by_code["unfair_competition"]
            assert uc.name == "Competencia desleal"
            assert uc.active is True
            assert "competencia desleal sentencia mercantil" in uc.discovery_queries
            assert "unfair competition damages" in uc.discovery_queries
            assert "competencia desleal" in uc.keywords
            assert "unfair competition" in uc.keywords

            assert "ai_intellectual_property" in subtopics_by_code
            ai_top = subtopics_by_code["ai_intellectual_property"]
            assert ai_top.name == "IA y Propiedad Intelectual"
            assert ai_top.active is True
            assert "IA" in ai_top.keywords
            assert "AI" in ai_top.keywords
            assert "propiedad intelectual" in ai_top.keywords
            assert "copyright" in ai_top.keywords
            assert "artificial intelligence" in ai_top.keywords
            assert "inteligencia artificial propiedad intelectual demanda" in ai_top.discovery_queries
            assert "AI copyright infringement" in ai_top.discovery_queries

            # Verify Articles 101/102 TFEU keywords
            cartels = subtopics_by_code["cartels_agreements"]
            assert "101 TFEU" in cartels.keywords
            assert "Article 101 TFEU" in cartels.keywords

            abuse = subtopics_by_code["abuse_dominance"]
            assert "102 TFEU" in abuse.keywords
            assert "Article 102 TFEU" in abuse.keywords

            # Verify Directive 2014/104 (Damages Directive)
            damages = subtopics_by_code["damages_actions"]
            assert "Directive 2014/104" in damages.keywords
            assert "Directiva 2014/104" in damages.keywords

            # Verify Digital Markets Act and Regulation (EU) 2022/1925
            dma = subtopics_by_code["digital_competition_dma"]
            assert "Digital Markets Act" in dma.keywords
            assert "DMA" in dma.keywords
            assert "Regulation (EU) 2022/1925" in dma.keywords
            assert "Reglamento (UE) 2022/1925" in dma.keywords

            # Verify Collective and Representative Actions
            col = subtopics_by_code["collective_actions"]
            assert "acción colectiva" in col.keywords
            assert "tutela colectiva" in col.keywords
            assert "acciones de representación" in col.keywords
            assert "representative actions" in col.keywords
            assert "Directive 2020/1828" in col.keywords
            assert "Directiva 2020/1828" in col.keywords
            assert "acciones colectivas" in col.keywords
            assert "collective actions" in col.keywords
            assert "class actions" in col.keywords

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
            # Clean up tables committed during seed() so other test modules have an isolated DB
            clean_db = TestingSessionLocal()
            try:
                clean_db.query(TrackedEntityTopic).delete()
                clean_db.query(TrackedEntity).delete()
                clean_db.query(TrackingTopic).delete()
                clean_db.query(TrackingMatrix).delete()
                clean_db.commit()
            finally:
                clean_db.close()
