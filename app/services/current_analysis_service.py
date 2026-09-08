"""Service for determining and selecting the active/current analysis of an Entry.

Rules:
1. An EntryAnalysis is eligible to be 'current' if and only if:
   a. status == 'completed'
   b. entry_content_hash == compute_analysis_input_hash(entry)
      (i.e. it was performed against the current exact textual content of the Entry).
2. Failed analyses are never 'current' (they remain in DB for audit and debugging).
3. Stale analyses (where content was subsequently updated, e.g. CAT PDF enrichment) are not 'current'.
4. Among eligible candidates:
   a. Most recent pipeline_version (parsed numerically: v4 > v3 > v2 > v1).
   b. Tie-breaker: created_at DESC (or started_at DESC).
"""

from __future__ import annotations

import re
import uuid
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models.analysis import EntryAnalysis
from app.models.entry import Entry
from app.services.analysis_service import compute_analysis_input_hash


def parse_pipeline_version_num(version_str: Optional[str]) -> int:
    """Parse numeric version from string: 'v4' -> 4, 'v3' -> 3, 'v2.1' -> 2, etc."""
    if not version_str:
        return 0
    m = re.search(r"\d+", str(version_str))
    return int(m.group()) if m else 0


def is_analysis_stale(analysis: EntryAnalysis, entry: Entry) -> bool:
    """Check whether a completed analysis was performed on an older content version."""
    current_hash = compute_analysis_input_hash(entry)
    return analysis.entry_content_hash != current_hash


def select_current_analysis(
    entry: Entry,
    analyses: Optional[Sequence[EntryAnalysis]] = None,
    db: Optional[Session] = None,
) -> Optional[EntryAnalysis]:
    """Select the definitive current production analysis for an entry, if one exists.

    Returns None if:
    - Entry has never been analysed
    - All existing analyses failed
    - All existing completed analyses are stale
    """
    if analyses is None:
        if db is None:
            raise ValueError("Either analyses or db must be provided to select_current_analysis")
        analyses = db.query(EntryAnalysis).filter(EntryAnalysis.entry_id == entry.id).all()

    current_hash = compute_analysis_input_hash(entry)

    # Filter strictly to completed analyses matching current content
    valid_candidates = [
        ea for ea in analyses
        if ea.status == "completed" and ea.entry_content_hash == current_hash
    ]

    if not valid_candidates:
        return None

    # Sort candidates by pipeline_version DESC (numeric), created_at DESC
    def sort_key(ea: EntryAnalysis) -> tuple[int, Any]:
        v_num = parse_pipeline_version_num(ea.pipeline_version)
        ts = ea.created_at or ea.started_at
        return (v_num, ts)

    valid_candidates.sort(key=sort_key, reverse=True)
    return valid_candidates[0]


def get_entry_analysis_state(
    entry: Entry,
    analyses: Optional[Sequence[EntryAnalysis]] = None,
    db: Optional[Session] = None,
) -> dict[str, Any]:
    """Provide a comprehensive diagnostic state of an entry's analyses."""
    if analyses is None:
        if db is None:
            raise ValueError("Either analyses or db must be provided")
        analyses = db.query(EntryAnalysis).filter(EntryAnalysis.entry_id == entry.id).all()

    current_hash = compute_analysis_input_hash(entry)
    current = select_current_analysis(entry, analyses)

    completed = [ea for ea in analyses if ea.status == "completed"]
    failed = [ea for ea in analyses if ea.status == "failed"]
    stale = [ea for ea in completed if ea.entry_content_hash != current_hash]

    has_v4_completed = any(
        ea.pipeline_version == "v4" and ea.status == "completed" and ea.entry_content_hash == current_hash
        for ea in analyses
    )

    return {
        "entry_id": entry.id,
        "current_analysis": current,
        "has_current": current is not None,
        "current_version": current.pipeline_version if current else None,
        "current_score": current.relevance_score if current else None,
        "current_status": current.relevance_status if current else None,
        "total_analyses": len(analyses),
        "completed_count": len(completed),
        "failed_count": len(failed),
        "stale_count": len(stale),
        "has_v4_completed": has_v4_completed,
        "needs_v4_baseline": not has_v4_completed,
    }
