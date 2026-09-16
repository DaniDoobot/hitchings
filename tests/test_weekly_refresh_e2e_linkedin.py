"""E2E Test: Weekly Refresh LinkedIn Full Flow with Incremental Gemini Analysis."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.analysis import EntryAnalysis, AnalysisCall
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix, TrackingTopic, TrackedEntity
from app.providers.linkedin.base import LinkedInDiscoveredPost
from app.services.analysis_pipeline_service import AnalysisPipelineService
from app.services.incremental_analysis_service import IncrementalAnalysisService
from app.services.linkedin_discovery_planner import LinkedInDiscoveryJob
from app.services.linkedin_ingestion_service import LinkedInIngestionService
from app.services.weekly_refresh_service import WeeklyRefreshService


def _setup_matrix_and_prompts(db: Session) -> TrackingMatrix:
    """Seed analysis prompts and active tracking matrix."""
    from scripts.seed_analysis_prompts import seed_analysis_prompts

    seed_analysis_prompts(db)

    matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    if not matrix:
        matrix = TrackingMatrix(
            code=f"HITCH-E2E-{uuid.uuid4().hex[:6]}",
            name="Matriz HITCHINGS E2E",
            status="active",
            relevance_instructions="Focus on cartel damages, antitrust, competition litigation.",
            exclusion_instructions="Exclude corporate announcements and non-legal social posts.",
        )
        db.add(matrix)
        db.flush()

    area = db.query(TrackingTopic).filter(TrackingTopic.matrix_id == matrix.id, TrackingTopic.parent_id.is_(None)).first()
    if not area:
        area = TrackingTopic(
            matrix_id=matrix.id,
            parent_id=None,
            code="competencia_general",
            name="Competencia General",
            priority=1,
            active=True,
        )
        db.add(area)
        db.flush()

    topic = db.query(TrackingTopic).filter(TrackingTopic.matrix_id == matrix.id, TrackingTopic.parent_id.is_not(None)).first()
    if not topic:
        topic = TrackingTopic(
            matrix_id=matrix.id,
            parent_id=area.id,
            code="carteles_antidanos",
            name="Cárteles y Daños",
            description="Reclamaciones de daños por infracciones de cárteles",
            keywords=["cartel", "danos", "antitrust"],
            priority=1,
            active=True,
        )
        db.add(topic)
        db.flush()

    db.commit()
    return matrix


from app.providers.ai.mock import MockAIProvider


def test_weekly_refresh_linkedin_full_flow(db_session: Session):
    """Complete E2E validation:
    1. Discovery creates 2 entries (one relevant, one not relevant) with content >= 50 chars.
    2. Both entries enter new_entry_ids and pass to IncrementalAnalysis.
    3. Relevant entry gets Triage + Deep Analysis -> EntryAnalysis created (relevant).
    4. Non-relevant entry gets Triage only -> EntryAnalysis created (not_relevant, 0 deep calls).
    5. Duplicate run -> 0 new entries, 0 new analyses.
    """
    matrix = _setup_matrix_and_prompts(db_session)

    # 1. Setup LinkedIn canonical Source
    source = Source(
        name="LinkedIn",
        type=SourceType.LINKEDIN,
        url="https://www.linkedin.com",
        active=True,
        category="social_network",
        provider="external",
        config={
            "enabled": True,
            "max_entities": 2,
            "max_posts_per_entity": 2,
            "max_concurrent_jobs": 1,
        },
    )
    db_session.add(source)
    db_session.commit()

    ent_hausfeld = TrackedEntity(
        id=uuid.uuid4(),
        display_name="Hausfeld",
        entity_type="organization",
        active=True,
        metadata_={
            "linkedin_url": "https://www.linkedin.com/company/hausfeld",
            "linkedin_entity_type": "organization",
        },
    )
    db_session.add(ent_hausfeld)
    db_session.commit()

    # Create mock discovered posts (content > 50 chars for FULL sufficiency)
    now = datetime(2026, 9, 16, 10, 0, 0, tzinfo=timezone.utc)
    post_rel = LinkedInDiscoveredPost(
        linkedin_post_url="https://www.linkedin.com/posts/hausfeld_cartel-litigation-activity-739110001",
        provider_item_id="urn:li:activity:739110001",
        text="Importante victoria en el litigio de cartel de camiones y reclamacion de danos antitrust admitida a tramite.",
        author_name="Hausfeld",
        author_profile_url="https://www.linkedin.com/company/hausfeld",
        published_at=now,
        provider="brightdata",
        engagement={"likes": 25, "comments": 4, "shares": 3},
        raw_metadata={"http_status": 200},
    )

    post_nonrel = LinkedInDiscoveredPost(
        linkedin_post_url="https://www.linkedin.com/posts/hausfeld_summer-gathering-activity-739110002",
        provider_item_id="urn:li:activity:739110002",
        text="Celebrando el almuerzo anual de verano con todo el equipo en las oficinas corporativas centrales de la firma.",
        author_name="Hausfeld",
        author_profile_url="https://www.linkedin.com/company/hausfeld",
        published_at=now,
        provider="brightdata",
        engagement={"likes": 40, "comments": 2, "shares": 1},
        raw_metadata={"http_status": 200},
    )

    mock_primary = MagicMock()
    mock_primary.provider_name = "brightdata"
    mock_primary.discover_posts.return_value = [post_rel, post_nonrel]

    mock_fallback = MagicMock()
    mock_fallback.provider_name = "apify"

    mock_planner = MagicMock()
    mock_planner.plan_jobs.return_value = [
        LinkedInDiscoveryJob(
            job_id=f"job-{ent_hausfeld.id}",
            tracked_entity_id=ent_hausfeld.id,
            entity_name="Hausfeld",
            linkedin_url="https://www.linkedin.com/company/hausfeld",
            entity_type="organization",
            provider="brightdata",
            priority=1,
        )
    ]

    li_service = LinkedInIngestionService(
        planner=mock_planner,
        primary_provider=mock_primary,
        fallback_provider=mock_fallback,
    )

    mock_ai = MockAIProvider()
    settings = Settings(
        LINKEDIN_DISCOVERY_ENABLED=True,
        BRIGHTDATA_API_TOKEN="token-prod",
        LINKEDIN_MAX_CONCURRENT_JOBS=1,
        LINKEDIN_MAX_ENTITIES_PER_RUN=1,
        LINKEDIN_MAX_POSTS_PER_ENTITY=2,
    )
    incremental_analysis = IncrementalAnalysisService(provider=mock_ai, settings=settings)

    weekly_service = WeeklyRefreshService(
        settings=settings,
        linkedin_service=li_service,
        incremental_analysis_service=incremental_analysis,
    )

    # ── RUN 1: Fresh Discovery & Analysis ─────────────────────────────────────
    report1 = weekly_service.run_weekly_refresh(
        db=db_session,
        confirm_real_calls=True,
        sources_filter=["LinkedIn"],
    )

    assert report1.status == "completed"
    assert report1.sources_attempted == 1
    assert report1.total_new_entries == 2
    assert report1.total_duplicates == 0

    detail1 = report1.per_source[0]
    assert detail1.source_name == "LinkedIn"
    assert detail1.status == "success"
    assert len(detail1.new_entry_ids) == 2

    # Check analyses in database
    created_entries = db_session.query(Entry).filter(Entry.source_id == source.id).all()
    assert len(created_entries) == 2

    rel_entry = next(e for e in created_entries if "739110001" in e.external_id)
    nonrel_entry = next(e for e in created_entries if "739110002" in e.external_id)

    # Verify relevant entry analysis
    assert len(rel_entry.analyses) == 1
    analysis_rel = rel_entry.analyses[0]
    assert analysis_rel.relevance_status == "relevant"
    assert analysis_rel.relevance_score >= 70
    assert analysis_rel.status == "completed"
    # Verify deep analysis was triggered
    calls_rel = db_session.query(AnalysisCall).filter(AnalysisCall.entry_analysis_id == analysis_rel.id).all()
    stage_names_rel = {c.stage for c in calls_rel}
    assert "triage" in stage_names_rel
    assert "deep_analysis" in stage_names_rel

    # Verify non-relevant entry analysis
    assert len(nonrel_entry.analyses) == 1
    analysis_nonrel = nonrel_entry.analyses[0]
    assert analysis_nonrel.relevance_status == "not_relevant"
    assert analysis_nonrel.relevance_score < 50
    assert analysis_nonrel.status == "completed"
    # Verify deep analysis was NOT triggered for corporate noise
    calls_nonrel = db_session.query(AnalysisCall).filter(AnalysisCall.entry_analysis_id == analysis_nonrel.id).all()
    stage_names_nonrel = {c.stage for c in calls_nonrel}
    assert "triage" in stage_names_nonrel
    assert "deep_analysis" not in stage_names_nonrel

    # ── RUN 2: Duplicate Run (same posts returned) ───────────────────────────
    initial_analysis_count = db_session.query(EntryAnalysis).count()

    report2 = weekly_service.run_weekly_refresh(
        db=db_session,
        confirm_real_calls=True,
        sources_filter=["LinkedIn"],
    )

    assert report2.sources_attempted == 1
    assert report2.total_new_entries == 0
    assert report2.total_duplicates == 2

    detail2 = report2.per_source[0]
    assert len(detail2.new_entry_ids) == 0

    # Invariance check: zero new analyses created on duplicate run
    final_analysis_count = db_session.query(EntryAnalysis).count()
    assert final_analysis_count == initial_analysis_count


def test_weekly_refresh_linkedin_dynamic_config_overrides_settings(db_session: Session):
    """Verify that source.config dynamically overrides Settings for enabled and limits."""
    matrix = _setup_matrix_and_prompts(db_session)

    source = Source(
        name="LinkedIn Dynamic",
        type=SourceType.LINKEDIN,
        url="https://www.linkedin.com",
        active=True,
        category="social_network",
        provider="external",
        config={
            "enabled": True,
            "max_entities": 7,
            "max_posts": 4,
            "max_concurrent_jobs": 2,
        },
    )
    db_session.add(source)
    db_session.commit()

    mock_li_service = MagicMock()
    from app.services.linkedin_ingestion_service import LinkedInIngestionReport
    mock_li_service.execute_discovery.return_value = LinkedInIngestionReport(
        run_id=uuid.uuid4(),
        primary_provider="brightdata",
        fallback_provider="apify",
        posts_seen=0,
        entries_created=0,
        duplicates=0,
    )

    # Global setting has discovery disabled, but source.config has enabled=True
    settings = Settings(
        LINKEDIN_DISCOVERY_ENABLED=False,
        BRIGHTDATA_API_TOKEN="token-dyn",
    )

    weekly_service = WeeklyRefreshService(
        settings=settings,
        linkedin_service=mock_li_service,
    )

    report = weekly_service.run_weekly_refresh(
        db=db_session,
        confirm_real_calls=True,
        sources_filter=["LinkedIn Dynamic"],
    )

    assert report.sources_attempted == 1
    assert report.per_source[0].status == "success"

    # Verify execute_discovery was invoked with the dynamic values from source.config
    mock_li_service.execute_discovery.assert_called_once_with(
        db=db_session,
        confirm_real_calls=True,
        max_entities=7,
        max_posts_per_entity=4,
        max_concurrent=2,
        allow_manual=True,
    )

