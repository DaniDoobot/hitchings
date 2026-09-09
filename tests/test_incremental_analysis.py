"""Tests for IncrementalAnalysisPlanner and IncrementalAnalysisService (Bloque 9D)."""

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.analysis import EntryAnalysis
from app.models.tracking import TrackingMatrix, TrackingTopic
from app.providers.ai.mock import MockAIProvider
from app.services.analysis_service import compute_analysis_input_hash
from app.services.incremental_analysis_planner import IncrementalAnalysisPlanner
from app.services.incremental_analysis_service import (
    IncrementalAnalysisService,
    IncrementalAnalysisReport,
)
from scripts.seed_analysis_prompts import seed_analysis_prompts


# ==============================================================================
# Helpers
# ==============================================================================

def create_source(db: Session, name: str = "Test Blog", type_: SourceType = SourceType.BLOG) -> Source:
    source = Source(
        name=name,
        type=type_,
        provider="native",
        url=f"https://example.com/{name.lower().replace(' ', '_')}",
    )
    db.add(source)
    db.flush()
    return source


def create_entry(
    db: Session,
    source: Source,
    title: str = "Test Post",
    content: str = "Content for analysis exceeding threshold." * 50,  # > 1500 chars -> FULL
    published_at: datetime | None = None,
) -> Entry:
    if published_at is None:
        published_at = datetime.now(timezone.utc)
    entry = Entry(
        source_id=source.id,
        url=f"https://example.com/posts/{uuid.uuid4()}",
        title=title,
        content=content,
        excerpt=content[:100] if content else None,
        published_at=published_at,
    )
    db.add(entry)
    db.flush()
    return entry


def setup_matrix_and_prompts(db: Session) -> TrackingMatrix:
    # Seed prompt versions (including v6)
    seed_analysis_prompts(db)

    # Create active matrix
    matrix = TrackingMatrix(
        code=f"TEST-MTX-{uuid.uuid4().hex[:8]}",
        name="Test Matrix",
        status="active",
        relevance_instructions="Focus on antitrust and competition law",
        exclusion_instructions="Exclude general business or unrelated law",
    )
    db.add(matrix)
    db.flush()

    topic = TrackingTopic(
        matrix_id=matrix.id,
        code="antitrust_cartels",
        name="Antitrust & Cartels",
        description="Cartels and horizontal agreements",
        keywords=["cartel", "price-fixing", "antitrust"],
        active=True,
    )
    db.add(topic)
    db.flush()
    return matrix


# ==============================================================================
# Planner Tests
# ==============================================================================

def test_planner_identifies_eligible_entry(db_session: Session):
    """Entry with FULL sufficiency (>=1500 chars) and no analysis is eligible."""
    source = create_source(db_session)
    entry = create_entry(db_session, source, content="A" * 2000)

    planner = IncrementalAnalysisPlanner(db=db_session)
    plan = planner.plan()

    assert plan.total_entries_inspected == 1
    assert plan.eligible_count == 1
    assert len(plan.candidates) == 1
    assert plan.candidates[0].entry_id == entry.id
    assert plan.candidates[0].reason == "eligible"
    assert plan.candidates[0].estimated_stage_plan == "triage_then_deep_if_relevant"


def test_planner_excludes_partial_content(db_session: Session):
    """Entry with PARTIAL sufficiency (300-1499 chars) is classified as partial and excluded."""
    source = create_source(db_session)
    create_entry(db_session, source, content="A" * 500)

    planner = IncrementalAnalysisPlanner(db=db_session)
    plan = planner.plan()

    assert plan.total_entries_inspected == 1
    assert plan.eligible_count == 0
    assert plan.partial_count == 1
    assert len(plan.candidates) == 0


def test_planner_excludes_insufficient_content(db_session: Session):
    """Entry with zero chars content is classified as insufficient."""
    source = create_source(db_session)
    create_entry(db_session, source, content="")

    planner = IncrementalAnalysisPlanner(db=db_session)
    plan = planner.plan()

    assert plan.total_entries_inspected == 1
    assert plan.eligible_count == 0
    assert plan.insufficient_count == 1
    assert len(plan.candidates) == 0


def test_planner_excludes_already_current_analysis(db_session: Session):
    """Entry with completed analysis matching current input hash is already_current."""
    source = create_source(db_session)
    entry = create_entry(db_session, source, content="A" * 2000)

    current_hash = compute_analysis_input_hash(entry)
    ea = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        matrix_id=uuid.uuid4(),
        matrix_snapshot={},
        pipeline_version="v6",
        status="completed",
        entry_content_hash=current_hash,
        matrix_snapshot_hash="some_hash",
        relevance_status="relevant",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(ea)
    db_session.flush()

    planner = IncrementalAnalysisPlanner(db=db_session)
    plan = planner.plan()

    assert plan.total_entries_inspected == 1
    assert plan.eligible_count == 0
    assert plan.already_current_count == 1
    assert len(plan.candidates) == 0


def test_planner_detects_stale_reanalysis(db_session: Session):
    """Entry with completed analysis but modified content is marked stale_reanalysis and is eligible."""
    source = create_source(db_session)
    entry = create_entry(db_session, source, content="A" * 2000)

    ea = EntryAnalysis(
        id=uuid.uuid4(),
        entry_id=entry.id,
        matrix_id=uuid.uuid4(),
        matrix_snapshot={},
        pipeline_version="v6",
        status="completed",
        entry_content_hash="old_obsolete_hash",
        matrix_snapshot_hash="some_hash",
        relevance_status="relevant",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(ea)
    db_session.flush()

    planner = IncrementalAnalysisPlanner(db=db_session)
    plan = planner.plan()

    assert plan.total_entries_inspected == 1
    assert plan.eligible_count == 1
    assert plan.stale_count == 1
    assert len(plan.candidates) == 1
    assert plan.candidates[0].reason == "stale_reanalysis"


def test_planner_ordering_and_filters(db_session: Session):
    """Planner sorts by published_at ASC NULLS LAST, entry_id ASC, and respects limit, source_id, entry_id."""
    source1 = create_source(db_session, "Blog A")
    source2 = create_source(db_session, "Blog B")

    t1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

    e1 = create_entry(db_session, source1, title="Oldest", content="X" * 2000, published_at=t1)
    e2 = create_entry(db_session, source2, title="Middle", content="Y" * 2000, published_at=t2)
    e3 = create_entry(db_session, source1, title="Newest", content="Z" * 2000, published_at=t3)

    planner = IncrementalAnalysisPlanner(db=db_session)

    # All eligible candidates in order
    p_all = planner.plan()
    assert [c.entry_id for c in p_all.candidates] == [e1.id, e2.id, e3.id]

    # Limit
    p_lim = planner.plan(limit=2)
    assert len(p_lim.candidates) == 2
    assert [c.entry_id for c in p_lim.candidates] == [e1.id, e2.id]

    # Source filter
    p_src = planner.plan(source_id=source2.id)
    assert len(p_src.candidates) == 1
    assert p_src.candidates[0].entry_id == e2.id

    # Entry filter
    p_ent = planner.plan(entry_id=e3.id)
    assert len(p_ent.candidates) == 1
    assert p_ent.candidates[0].entry_id == e3.id


# ==============================================================================
# Service Execution Tests (with MockAIProvider)
# ==============================================================================

@pytest.mark.asyncio
async def test_service_dry_run_makes_no_calls(db_session: Session):
    """Dry run produces report but makes zero AI calls and zero DB writes."""
    source = create_source(db_session)
    create_entry(db_session, source, content="A" * 2000)

    mock_ai = MockAIProvider()
    service = IncrementalAnalysisService(provider=mock_ai)

    report = await service.execute_incremental_analysis(db=db_session, confirm_real_calls=False)
    assert report.is_dry_run is True
    assert report.planned == 1
    assert report.attempted == 0
    assert report.completed == 0
    assert report.triage_calls == 0


@pytest.mark.asyncio
async def test_service_executes_eligible_entry_triage_only_if_not_relevant(db_session: Session):
    """If triage decides not_relevant, deep analysis is skipped."""
    setup_matrix_and_prompts(db_session)
    source = create_source(db_session)
    # Content without competition keywords -> MockAIProvider assigns score=25 (not_relevant)
    create_entry(db_session, source, title="Unrelated Tech Gossip", content="General tech news with no antitrust concepts. " * 50)

    mock_ai = MockAIProvider(fixed_score=20)

    service = IncrementalAnalysisService(provider=mock_ai)
    report = await service.execute_incremental_analysis(
        db=db_session,
        confirm_real_calls=True,
        max_estimated_cost_usd=1.0,
    )

    assert report.attempted == 1
    assert report.completed == 1
    assert report.triage_calls == 1
    assert report.deep_calls == 0
    assert report.not_relevant == 1
    assert report.details[0]["status"] == "completed"
    assert report.details[0]["relevance_status"] == "not_relevant"
    assert report.details[0]["has_deep"] is False


@pytest.mark.asyncio
async def test_service_executes_deep_analysis_if_relevant(db_session: Session):
    """If triage decides relevant, deep analysis runs."""
    setup_matrix_and_prompts(db_session)
    source = create_source(db_session)
    content = "The European Commission has sanctioned a dairy cartel under Article 101 TFEU for price fixing. "
    create_entry(db_session, source, title="Cartel Fine Decision", content=content * 50)

    mock_ai = MockAIProvider(fixed_score=90)
    service = IncrementalAnalysisService(provider=mock_ai)
    report = await service.execute_incremental_analysis(
        db=db_session,
        confirm_real_calls=True,
        max_estimated_cost_usd=1.0,
    )

    assert report.attempted == 1
    assert report.completed == 1
    assert report.triage_calls == 1
    assert report.deep_calls == 1
    assert report.relevant == 1
    assert report.details[0]["relevance_status"] == "relevant"
    assert report.details[0]["has_deep"] is True

    # Check idempotent rerun: planner sees it as already_current
    planner = IncrementalAnalysisPlanner(db=db_session)
    p2 = planner.plan()
    assert p2.eligible_count == 0
    assert p2.already_current_count == 1


@pytest.mark.asyncio
async def test_service_budget_reservation_stops_before_exceeding(db_session: Session):
    """When budget is set to near zero, reservation check prevents calls."""
    setup_matrix_and_prompts(db_session)
    source = create_source(db_session)
    create_entry(db_session, source, content="A" * 2000)

    mock_ai = MockAIProvider()
    service = IncrementalAnalysisService(provider=mock_ai)
    report = await service.execute_incremental_analysis(
        db=db_session,
        confirm_real_calls=True,
        max_estimated_cost_usd=0.000001,
    )

    assert report.skipped_budget == 1
    assert report.attempted == 1
    assert report.completed == 0
    assert report.triage_calls == 0


@pytest.mark.asyncio
async def test_service_failure_isolation(db_session: Session):
    """If an entry fails during analysis, subsequent entries still proceed."""
    setup_matrix_and_prompts(db_session)
    source = create_source(db_session)
    t1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
    create_entry(db_session, source, title="Failing Entry", content="A" * 2000, published_at=t1)
    create_entry(db_session, source, title="Succeeding Entry", content="B" * 2000, published_at=t2)

    # Use a custom provider that fails only on the first call
    class FlakyProvider(MockAIProvider):
        def __init__(self):
            super().__init__()
            self.call_num = 0

        async def analyze(self, *args, **kwargs):
            self.call_num += 1
            if self.call_num == 1:
                raise RuntimeError("Simulated transient provider error")
            return await super().analyze(*args, **kwargs)

    service = IncrementalAnalysisService(provider=FlakyProvider())
    report = await service.execute_incremental_analysis(
        db=db_session,
        confirm_real_calls=True,
        max_estimated_cost_usd=1.0,
    )

    assert report.planned == 2
    assert report.attempted == 2
    assert report.failed == 1
    assert report.completed == 1
    assert report.details[0]["status"] == "failed"
    err_str = report.details[0].get("error") or report.details[0].get("reason") or ""
    assert "Simulated transient provider error" in err_str
    assert report.details[1]["status"] == "completed"
