"""HITCHINGS Observatory — Operational Health Diagnostics CLI.

Diagnoses the health of all monitored sources, pipeline conversion
(Captured -> Entries -> Triage -> Deep Analysis), and financial costs.
Can evaluate against local DB or remote API endpoints.

Usage:
    python -m scripts.diagnose_observatory_health
    python -m scripts.diagnose_observatory_health --verbose
    python -m scripts.diagnose_observatory_health --api-base http://127.0.0.1:8000
"""

import argparse
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.source import Source, SourceType
from app.models.entry import Entry
from app.models.analysis import EntryAnalysis, AnalysisCall
from app.models.ingestion_run import IngestionRun


@dataclass
class SourceHealthRecord:
    source_id: str
    source_name: str
    source_type: str
    is_linkedin: bool
    enabled: bool
    status: str  # healthy, degraded, disabled, unknown
    last_execution: Optional[datetime] = None
    last_success: Optional[datetime] = None
    entries_created: int = 0
    errors: int = 0


@dataclass
class PipelineMetricsRecord:
    posts_captured: int = 0
    entries_created: int = 0
    total_analyzed: int = 0
    relevant_count: int = 0
    uncertain_count: int = 0
    not_relevant_count: int = 0
    deep_analysis_count: int = 0
    failed_analysis_count: int = 0
    timed_out_snapshots: int = 0
    anomalies: List[str] = field(default_factory=list)


@dataclass
class CostMetricsRecord:
    gemini_estimated_cost_usd: float = 0.0
    provider_estimated_cost_usd: float = 0.0
    total_estimated_cost_usd: float = 0.0
    has_gemini_cost_data: bool = False
    has_provider_cost_data: bool = False


@dataclass
class TimingMetricsRecord:
    last_execution: Optional[datetime] = None
    last_success: Optional[datetime] = None
    avg_analysis_time_ms: Optional[float] = None


@dataclass
class ObservatoryHealthReport:
    sources: List[SourceHealthRecord] = field(default_factory=list)
    pipeline: PipelineMetricsRecord = field(default_factory=PipelineMetricsRecord)
    cost: CostMetricsRecord = field(default_factory=CostMetricsRecord)
    timing: TimingMetricsRecord = field(default_factory=TimingMetricsRecord)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def gather_health_from_db(db: Session) -> ObservatoryHealthReport:
    """Collect operational health and quality metrics directly from database."""
    report = ObservatoryHealthReport()

    sources = db.execute(select(Source).order_by(Source.name.asc())).scalars().all()

    # 1. Source status
    for s in sources:
        is_li = s.type == SourceType.LINKEDIN or "linkedin" in str(s.type).lower()

        latest_run = (
            db.execute(
                select(IngestionRun)
                .where(IngestionRun.source_id == s.id)
                .order_by(IngestionRun.started_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )

        enabled = bool(s.active)
        if s.config and "enabled" in s.config:
            enabled = enabled and bool(s.config["enabled"])

        if not enabled:
            source_status = "disabled"
        elif latest_run and latest_run.status == "failed":
            source_status = "degraded"
        else:
            source_status = "healthy"

        created_cnt = latest_run.created_count if latest_run else 0
        error_cnt = latest_run.failed_count if latest_run else 0
        last_exec = s.last_run_at or (latest_run.started_at if latest_run else None)
        last_succ = s.last_success_at or (
            latest_run.finished_at if latest_run and latest_run.status == "success" else None
        )

        stype_val = s.type.value if hasattr(s.type, "value") else str(s.type)

        rec = SourceHealthRecord(
            source_id=str(s.id),
            source_name=s.name,
            source_type=stype_val,
            is_linkedin=is_li,
            enabled=enabled,
            status=source_status,
            last_execution=last_exec,
            last_success=last_succ,
            entries_created=created_cnt,
            errors=error_cnt,
        )
        report.sources.append(rec)

        # Track latest overall execution
        if last_exec:
            if not report.timing.last_execution or last_exec > report.timing.last_execution:
                report.timing.last_execution = last_exec
        if last_succ:
            if not report.timing.last_success or last_succ > report.timing.last_success:
                report.timing.last_success = last_succ

    # 2. Pipeline metrics aggregated
    # 2.1 Captured & Entries
    all_runs = db.execute(select(IngestionRun)).scalars().all()
    total_captured = 0
    total_timed_out_snapshots = 0
    for r in all_runs:
        total_captured += r.fetched_count or 0
        meta = r.run_metadata or {}
        if "timed_out_snapshots" in meta:
            total_timed_out_snapshots += int(meta["timed_out_snapshots"] or 0)
        if "estimated_provider_cost" in meta and meta["estimated_provider_cost"]:
            cost_val = float(meta["estimated_provider_cost"])
            report.cost.provider_estimated_cost_usd += cost_val
            report.cost.has_provider_cost_data = True

    total_entries = db.execute(select(func.count(Entry.id))).scalar() or 0
    if total_captured == 0 and total_entries > 0:
        total_captured = total_entries

    report.pipeline.posts_captured = total_captured
    report.pipeline.entries_created = total_entries
    report.pipeline.timed_out_snapshots = total_timed_out_snapshots

    # 2.2 Analyses
    analyses = db.execute(select(EntryAnalysis)).scalars().all()
    completed_analyses = [a for a in analyses if a.status == "completed"]
    failed_analyses = [a for a in analyses if a.status == "failed"]

    report.pipeline.total_analyzed = len(completed_analyses)
    report.pipeline.failed_analysis_count = len(failed_analyses)

    for a in completed_analyses:
        if a.relevance_status == "relevant":
            report.pipeline.relevant_count += 1
        elif a.relevance_status == "uncertain":
            report.pipeline.uncertain_count += 1
        elif a.relevance_status == "not_relevant":
            report.pipeline.not_relevant_count += 1

    # 2.3 Deep calls & Latency & Gemini Cost
    completed_analysis_ids = [a.id for a in completed_analyses]
    if completed_analysis_ids:
        calls = (
            db.execute(
                select(AnalysisCall).where(AnalysisCall.entry_analysis_id.in_(completed_analysis_ids))
            )
            .scalars()
            .all()
        )
        deep_entry_ids = set()
        total_latency_ms = 0
        call_count_with_latency = 0

        for c in calls:
            if c.latency_ms is not None:
                total_latency_ms += c.latency_ms
                call_count_with_latency += 1
            if c.estimated_cost_usd is not None:
                report.cost.gemini_estimated_cost_usd += float(c.estimated_cost_usd)
                report.cost.has_gemini_cost_data = True
            if c.stage == "deep_analysis" and c.status == "completed":
                deep_entry_ids.add(c.entry_analysis_id)

        report.pipeline.deep_analysis_count = len(deep_entry_ids)
        if call_count_with_latency > 0:
            report.timing.avg_analysis_time_ms = round(total_latency_ms / call_count_with_latency, 1)

    report.cost.total_estimated_cost_usd = round(
        report.cost.gemini_estimated_cost_usd + report.cost.provider_estimated_cost_usd, 4
    )

    # 3. Anomaly detection (Phase 3)
    detect_anomalies(report)

    return report


def detect_anomalies(report: ObservatoryHealthReport) -> None:
    """Audit metrics for logical inconsistencies or telemetry corruption."""
    anomalies = []

    # 1. Pipeline inversion: entries > captured (unless zero captured with legacy data)
    if report.pipeline.posts_captured > 0 and report.pipeline.entries_created > report.pipeline.posts_captured:
        anomalies.append(
            f"Pipeline anomaly: Entries created ({report.pipeline.entries_created}) exceeds items captured ({report.pipeline.posts_captured})."
        )

    # 2. Negative numbers
    if report.pipeline.posts_captured < 0 or report.pipeline.entries_created < 0:
        anomalies.append("Integrity error: Negative item counts detected in ingestion telemetry.")

    # 3. Deep Analysis > Relevant
    if report.pipeline.deep_analysis_count > report.pipeline.relevant_count:
        anomalies.append(
            f"Analysis anomaly: Deep Analysis count ({report.pipeline.deep_analysis_count}) exceeds Relevant count ({report.pipeline.relevant_count})."
        )

    # 4. Deep Analysis > Total Analyzed
    if report.pipeline.deep_analysis_count > report.pipeline.total_analyzed:
        anomalies.append(
            f"Analysis anomaly: Deep Analysis count ({report.pipeline.deep_analysis_count}) exceeds Total Analyzed ({report.pipeline.total_analyzed})."
        )

    # 5. Negative costs
    if (
        report.cost.gemini_estimated_cost_usd < 0
        or report.cost.provider_estimated_cost_usd < 0
        or report.cost.total_estimated_cost_usd < 0
    ):
        anomalies.append("Financial anomaly: Negative estimated costs recorded.")

    # 6. Source-specific inconsistencies
    for s in report.sources:
        if s.is_linkedin:
            if s.entries_created > 0 and not s.last_execution:
                anomalies.append(
                    f"LinkedIn source '{s.source_name}' has created entries ({s.entries_created}) but no execution run timestamp recorded."
                )

    report.pipeline.anomalies = anomalies


def format_health_report(report: ObservatoryHealthReport) -> str:
    """Format operational health diagnostic report into structured text."""
    lines = []
    lines.append("=" * 60)
    lines.append("HITCHINGS OBSERVATORY — OPERATIONAL HEALTH")
    lines.append("=" * 60)

    # Sources
    lines.append("\nSources")
    lines.append("-" * 60)
    lines.append(f"{'Source':<35} {'Status':<12} {'Enabled':<8}")
    lines.append("-" * 60)
    for s in report.sources:
        enabled_str = "YES" if s.enabled else "NO"
        name = "LinkedIn" if s.is_linkedin else s.source_name
        if len(name) > 34:
            name = name[:31] + "..."
        lines.append(f"{name:<35} {s.status:<12} {enabled_str:<8}")

    # Pipeline
    lines.append("\nPipeline")
    lines.append("-" * 60)
    lines.append(f"{'Items captured':<30} : {report.pipeline.posts_captured}")
    lines.append(f"{'Entries created':<30} : {report.pipeline.entries_created}")
    lines.append(f"{'Triage analyzed':<30} : {report.pipeline.total_analyzed}")
    lines.append(f"{'  Relevant':<30} : {report.pipeline.relevant_count}")
    lines.append(f"{'  Uncertain':<30} : {report.pipeline.uncertain_count}")
    lines.append(f"{'  Not relevant':<30} : {report.pipeline.not_relevant_count}")
    lines.append(f"{'Deep Analysis':<30} : {report.pipeline.deep_analysis_count}")
    lines.append(f"{'Failed':<30} : {report.pipeline.failed_analysis_count}")
    lines.append(f"{'Timed out':<30} : {report.pipeline.timed_out_snapshots}")

    # Cost
    lines.append("\nCost")
    lines.append("-" * 60)
    if report.cost.has_gemini_cost_data:
        gemini_str = f"${report.cost.gemini_estimated_cost_usd:.4f}"
    else:
        gemini_str = "N/A — insufficient telemetry"

    if report.cost.has_provider_cost_data:
        provider_str = f"${report.cost.provider_estimated_cost_usd:.4f}"
    else:
        provider_str = "N/A — insufficient telemetry"

    if report.cost.has_gemini_cost_data or report.cost.has_provider_cost_data:
        total_str = f"${report.cost.total_estimated_cost_usd:.4f}"
    else:
        total_str = "N/A — insufficient telemetry"

    lines.append(f"{'Gemini estimated cost':<30} : {gemini_str}")
    lines.append(f"{'Provider estimated cost':<30} : {provider_str}")
    lines.append(f"{'Total estimated cost':<30} : {total_str}")

    # Timing
    lines.append("\nTiming")
    lines.append("-" * 60)
    last_exec_str = (
        report.timing.last_execution.strftime("%Y-%m-%d %H:%M:%S UTC")
        if report.timing.last_execution
        else "N/A — no executions recorded"
    )
    last_succ_str = (
        report.timing.last_success.strftime("%Y-%m-%d %H:%M:%S UTC")
        if report.timing.last_success
        else "N/A — no successful executions"
    )
    avg_time_str = (
        f"{report.timing.avg_analysis_time_ms:.1f} ms"
        if report.timing.avg_analysis_time_ms is not None
        else "N/A — insufficient telemetry"
    )

    lines.append(f"{'Last execution':<30} : {last_exec_str}")
    lines.append(f"{'Last success':<30} : {last_succ_str}")
    lines.append(f"{'Average analysis time':<30} : {avg_time_str}")

    # Anomalies
    if report.pipeline.anomalies:
        lines.append("\nAnomalies & Warnings")
        lines.append("-" * 60)
        for a in report.pipeline.anomalies:
            lines.append(f" [!] {a}")

    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="HITCHINGS Observatory — Operational Health Diagnostics")
    parser.add_argument("--verbose", action="store_true", help="Include granular details in stdout")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        report = gather_health_from_db(db)
        print(format_health_report(report))
        if report.pipeline.anomalies:
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
