"""Tests for evaluate_linkedin_triage script (BLOQUE 9C / TAREA 6)."""

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session

from app.models.analysis import EntryAnalysis, AnalysisCall, AnalysisPromptVersion
from app.models.entry import Entry
from app.models.source import Source, SourceType
from app.models.tracking import TrackingMatrix
from scripts.evaluate_linkedin_triage import evaluate_linkedin_triage


def test_evaluate_linkedin_triage_empirical_distribution(db_session: Session):
    """Verify evaluate_linkedin_triage calculates distribution, scores, and audit signals."""
    matrix = TrackingMatrix(code="TEST-EVAL-MTRX", name="Eval Matrix", status="active")
    db_session.add(matrix)
    db_session.flush()

    src_li = Source(name="LinkedIn Feed", type=SourceType.LINKEDIN, active=True)
    src_web = Source(name="Web Source", type=SourceType.WEBSITE, active=True)
    db_session.add_all([src_li, src_web])
    db_session.flush()

    # 1. Relevant LinkedIn post (e.g. Hausfeld CAT judgment)
    e_rel = Entry(
        id=uuid.uuid4(),
        source_id=src_li.id,
        url="https://www.linkedin.com/posts/hausfeld-cat-1",
        title="CAT Collective Proceedings Trucks Cartel Judgment",
        content="Groundbreaking CAT judgment establishing damages in trucks cartel collective action.",
        author="Hausfeld",
        raw_metadata={"author_type": "organization"},
    )
    # 2. Non-relevant LinkedIn post (corporate vanity/events)
    e_not_rel = Entry(
        id=uuid.uuid4(),
        source_id=src_li.id,
        url="https://www.linkedin.com/posts/generic-firm-cocktail-2",
        title="Summer Cocktail & Networking Event",
        content="Join us for drinks and networking at our summer cocktail party.",
        author="Generic Law Firm",
        raw_metadata={"author_type": "organization"},
    )
    # 3. Uncertain LinkedIn post
    e_unc = Entry(
        id=uuid.uuid4(),
        source_id=src_li.id,
        url="https://www.linkedin.com/posts/uncertain-post-3",
        title="Reflections on Market Dynamics",
        content="Short opinion on modern digital ecosystem tendencies.",
        author="Legal Analyst",
        raw_metadata={"author_type": "person"},
    )
    # 4. Institutional website entry (should be ignored by evaluate_linkedin_triage)
    e_web = Entry(
        id=uuid.uuid4(),
        source_id=src_web.id,
        url="https://cnmc.es/resolucion-1",
        title="CNMC Resolución Sancionadora",
        content="Resolución en el sector de distribución farmacéutica.",
        author="CNMC",
    )

    db_session.add_all([e_rel, e_not_rel, e_unc, e_web])
    db_session.flush()

    # Add analyses
    a_rel = EntryAnalysis(
        entry_id=e_rel.id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        status="completed",
        relevance_status="relevant",
        relevance_score=94,
        confidence=0.95,
        pipeline_version="v6",
        entry_content_hash="h1",
        matrix_snapshot_hash="m1",
        summary="Substantive analysis of CAT cartel damages action.",
    )
    a_not_rel = EntryAnalysis(
        entry_id=e_not_rel.id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        status="completed",
        relevance_status="not_relevant",
        relevance_score=15,
        confidence=0.90,
        pipeline_version="v6",
        entry_content_hash="h2",
        matrix_snapshot_hash="m1",
        summary="Social event with no antitrust or legal relevance.",
    )
    a_unc = EntryAnalysis(
        entry_id=e_unc.id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        status="completed",
        relevance_status="uncertain",
        relevance_score=55,
        confidence=0.70,
        pipeline_version="v6",
        entry_content_hash="h3",
        matrix_snapshot_hash="m1",
        summary="Generic opinion piece without concrete case citation.",
    )
    a_web = EntryAnalysis(
        entry_id=e_web.id,
        matrix_id=matrix.id,
        matrix_snapshot={"topics": []},
        status="completed",
        relevance_status="relevant",
        relevance_score=98,
        confidence=0.99,
        pipeline_version="v6",
        entry_content_hash="h4",
        matrix_snapshot_hash="m1",
        summary="Official sanction resolution.",
    )
    db_session.add_all([a_rel, a_not_rel, a_unc, a_web])
    db_session.commit()

    report = evaluate_linkedin_triage(db=db_session)

    # Web entry must not be counted in LinkedIn stats
    assert report["total_linkedin_entries"] == 3
    assert report["total_analyzed"] == 3

    dist = report["distribution"]
    assert dist["relevant"]["count"] == 1
    assert dist["relevant"]["pct"] == pytest.approx(33.33, rel=1e-2)
    assert dist["not_relevant"]["count"] == 1
    assert dist["uncertain"]["count"] == 1

    sm = report["score_metrics"]
    assert sm["min_score"] == 15
    assert sm["max_score"] == 94
    assert sm["avg_score"] == pytest.approx((94 + 15 + 55) / 3, rel=1e-2)
    assert sm["buckets"]["0-19"] == 1
    assert sm["buckets"]["40-59"] == 1
    assert sm["buckets"]["90-100"] == 1

    # Quality audit checks
    qa = report["quality_audit"]
    assert qa["potential_false_positives_count"] == 0
    assert "verdict" in report
