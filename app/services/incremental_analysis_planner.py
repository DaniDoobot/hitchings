"""Incremental Analysis Planner for HITCHINGS (Bloque 9D).

Identifies entries pending v6 AI analysis based on source sufficiency,
content existence, and current-analysis state (detecting both unanalyzed
and stale/updated content).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from app.models.entry import Entry
from app.models.analysis import EntryAnalysis
from app.services.analysis_service import compute_analysis_input_hash
from app.services.current_analysis_service import select_current_analysis
from app.services.source_sufficiency_service import (
    SourceSufficiencyLevel,
    assess_source_sufficiency,
)

logger = logging.getLogger(__name__)


class AnalysisCandidate(BaseModel):
    """Represents an entry evaluated by the incremental analysis planner."""

    entry_id: uuid.UUID
    source_id: Optional[uuid.UUID] = None
    source_name: str = "Unknown"
    title: str = ""
    published_at: Optional[datetime] = None
    sufficiency: str  # "full", "partial", "insufficient"
    content_chars: int = 0
    analysis_input_hash: str
    has_current_analysis: bool = False
    reason: str  # "eligible", "already_current", "insufficient", "partial", "missing_content", "stale_reanalysis", "unsupported"
    estimated_stage_plan: str  # "triage_then_deep_if_relevant", "skip_already_current", "skip_insufficient", "skip_partial", "skip_missing_content"


class IncrementalAnalysisPlan(BaseModel):
    """Result of an incremental analysis planning execution."""

    total_entries_inspected: int = 0
    eligible_count: int = 0
    already_current_count: int = 0
    insufficient_count: int = 0
    partial_count: int = 0
    missing_content_count: int = 0
    stale_count: int = 0
    candidates: list[AnalysisCandidate] = Field(default_factory=list)  # Only eligible candidates
    all_evaluated: list[AnalysisCandidate] = Field(default_factory=list)


class IncrementalAnalysisPlanner:
    """Service to evaluate and select entries eligible for incremental AI analysis."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def evaluate_entry(self, entry: Entry) -> AnalysisCandidate:
        """Evaluate a single entry against source sufficiency and current analysis rules."""
        source_id = entry.source_id
        source_name = entry.source.name if entry.source else "Unknown"
        title = entry.title or ""
        published_at = entry.published_at
        content = (entry.content or "").strip()
        content_chars = len(content)
        input_hash = compute_analysis_input_hash(entry)

        # 1. First check: Does this entry already have a valid current analysis matching its exact content?
        current_ea = select_current_analysis(entry, analyses=entry.analyses, db=self.db)
        suff_res = assess_source_sufficiency(entry)
        suff_level = suff_res.level.value

        if current_ea is not None and current_ea.entry_content_hash == input_hash:
            return AnalysisCandidate(
                entry_id=entry.id,
                source_id=source_id,
                source_name=source_name,
                title=title,
                published_at=published_at,
                sufficiency=suff_level,
                content_chars=content_chars,
                analysis_input_hash=input_hash,
                has_current_analysis=True,
                reason="already_current",
                estimated_stage_plan="skip_already_current",
            )

        # 2. Entry is not currently analyzed (or its content was updated). Check content existence.
        if content_chars == 0:
            return AnalysisCandidate(
                entry_id=entry.id,
                source_id=source_id,
                source_name=source_name,
                title=title,
                published_at=published_at,
                sufficiency=SourceSufficiencyLevel.INSUFFICIENT.value,
                content_chars=0,
                analysis_input_hash=input_hash,
                has_current_analysis=False,
                reason="insufficient",
                estimated_stage_plan="skip_insufficient",
            )

        # 3. Source Sufficiency Gate: Only FULL is eligible for automatic analysis in Bloque 9D
        if suff_level == SourceSufficiencyLevel.INSUFFICIENT.value:
            return AnalysisCandidate(
                entry_id=entry.id,
                source_id=source_id,
                source_name=source_name,
                title=title,
                published_at=published_at,
                sufficiency=suff_level,
                content_chars=content_chars,
                analysis_input_hash=input_hash,
                has_current_analysis=False,
                reason="insufficient",
                estimated_stage_plan="skip_insufficient",
            )

        if suff_level == SourceSufficiencyLevel.PARTIAL.value:
            return AnalysisCandidate(
                entry_id=entry.id,
                source_id=source_id,
                source_name=source_name,
                title=title,
                published_at=published_at,
                sufficiency=suff_level,
                content_chars=content_chars,
                analysis_input_hash=input_hash,
                has_current_analysis=False,
                reason="partial",
                estimated_stage_plan="skip_partial",
            )

        # 4. Sufficiency is FULL: determine whether it is an initial eligible analysis or a stale reanalysis
        is_stale = False
        if current_ea is not None and current_ea.entry_content_hash != input_hash:
            is_stale = True
        else:
            analyses = entry.analyses
            if analyses is None:
                analyses = (
                    self.db.query(EntryAnalysis)
                    .filter(EntryAnalysis.entry_id == entry.id)
                    .all()
                )
            is_stale = any(
                ea.status == "completed" and ea.entry_content_hash != input_hash
                for ea in analyses
            )

        reason = "stale_reanalysis" if is_stale else "eligible"

        return AnalysisCandidate(
            entry_id=entry.id,
            source_id=source_id,
            source_name=source_name,
            title=title,
            published_at=published_at,
            sufficiency=suff_level,
            content_chars=content_chars,
            analysis_input_hash=input_hash,
            has_current_analysis=False,
            reason=reason,
            estimated_stage_plan="triage_then_deep_if_relevant",
        )

    def plan(
        self,
        entry_id: Optional[uuid.UUID] = None,
        source_id: Optional[uuid.UUID] = None,
        limit: Optional[int] = None,
    ) -> IncrementalAnalysisPlan:
        """Scan entries and produce a deterministic incremental analysis plan.

        Entries are ordered deterministically: published_at ASC (nulls last), then entry_id ASC.
        """
        query = self.db.query(Entry).options(
            joinedload(Entry.source),
            joinedload(Entry.analyses),
        )

        if entry_id is not None:
            query = query.filter(Entry.id == entry_id)

        if source_id is not None:
            query = query.filter(Entry.source_id == source_id)

        # Deterministic backlog ordering: oldest published first, then UUID
        query = query.order_by(Entry.published_at.asc().nulls_last(), Entry.id.asc())
        entries: Sequence[Entry] = query.all()

        evaluated: list[AnalysisCandidate] = []
        eligible: list[AnalysisCandidate] = []

        already_current_cnt = 0
        insufficient_cnt = 0
        partial_cnt = 0
        missing_content_cnt = 0
        stale_cnt = 0

        for entry in entries:
            cand = self.evaluate_entry(entry)
            evaluated.append(cand)

            if cand.reason == "already_current":
                already_current_cnt += 1
            elif cand.reason == "insufficient":
                insufficient_cnt += 1
            elif cand.reason == "partial":
                partial_cnt += 1
            elif cand.reason == "missing_content":
                missing_content_cnt += 1
            elif cand.reason == "stale_reanalysis":
                stale_cnt += 1
                eligible.append(cand)
            elif cand.reason == "eligible":
                eligible.append(cand)

        total_eligible = len(eligible)

        # Apply limit to eligible candidates
        if limit is not None and limit >= 0:
            eligible = eligible[:limit]

        return IncrementalAnalysisPlan(
            total_entries_inspected=len(evaluated),
            eligible_count=total_eligible,
            already_current_count=already_current_cnt,
            insufficient_count=insufficient_cnt,
            partial_count=partial_cnt,
            missing_content_count=missing_content_cnt,
            stale_count=stale_cnt,
            candidates=eligible,
            all_evaluated=evaluated,
        )
