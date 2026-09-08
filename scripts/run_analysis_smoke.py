"""Controlled smoke test runner for a single Entry in HITCHINGS.

Executes triage -> deep analysis pipeline on exactly one Entry using Gemini Developer API.

SAFETY:
  - Requires --entry-id <UUID>
  - Requires --confirm-real-call to actually invoke Gemini Developer API.
  - Fail-closed: dry-run by default without flag.
  - Budget safety: hard limit --max-usd (default 0.10 USD).
  - Precheck: refuses to run if Entry already has an EntryAnalysis.
  - Never logs or exposes API keys.

Usage:
    # Dry-run:
    python -m scripts.run_analysis_smoke --entry-id <UUID>

    # Real call:
    python -m scripts.run_analysis_smoke --entry-id <UUID> --confirm-real-call [--max-usd 0.10]
"""

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path
from typing import Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.analysis import AnalysisCall, AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.tracking import TrackingMatrix
from app.providers.ai.gemini_api import GeminiAPIProvider
from app.services.analysis_pipeline_service import AnalysisPipelineService
from app.services.analysis_service import compute_content_hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("smoke_test")


def fmt_usd(val: float) -> str:
    return f"${val:.6f}"


def print_separator(char: str = "-", width: int = 80) -> None:
    print(char * width)


def resolve_prompts(db: Session) -> tuple[AnalysisPromptVersion, AnalysisPromptVersion]:
    """Resolve active v2 triage and deep analysis prompts."""
    triage = db.execute(
        select(AnalysisPromptVersion).where(
            AnalysisPromptVersion.code == "observatory_triage",
            AnalysisPromptVersion.version == 2,
            AnalysisPromptVersion.active == True,  # noqa: E712
        )
    ).scalar_one_or_none()

    deep = db.execute(
        select(AnalysisPromptVersion).where(
            AnalysisPromptVersion.code == "observatory_deep_analysis",
            AnalysisPromptVersion.version == 2,
            AnalysisPromptVersion.active == True,  # noqa: E712
        )
    ).scalar_one_or_none()

    if not triage or not deep:
        print("\nERROR: v2 prompts not found in database. Run: python -m scripts.seed_analysis_prompts")
        sys.exit(1)

    return triage, deep


def resolve_matrix(db: Session) -> TrackingMatrix:
    """Resolve active TrackingMatrix."""
    matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    if not matrix:
        print("\nERROR: No active TrackingMatrix found.")
        sys.exit(1)
    return matrix


async def run_smoke(
    entry_uuid: uuid.UUID,
    real_call: bool,
    max_usd: float,
    db: Session,
) -> None:
    settings = get_settings()

    print_separator("=")
    print("  HITCHINGS - AI ANALYSIS SMOKE TEST PRECHECK")
    print_separator("=")
    print(f"  Provider:               {settings.ANALYSIS_PROVIDER}")
    print(f"  Model:                  {settings.GEMINI_MODEL}")
    has_key = bool(settings.GEMINI_API_KEY.strip()) if settings.GEMINI_API_KEY else False
    print(f"  API Key Configured:     {'YES' if has_key else 'NO'}")
    print(f"  Thinking TRIAGE:        {settings.ANALYSIS_TRIAGE_THINKING_LEVEL}")
    print(f"  Thinking DEEP:          {settings.ANALYSIS_DEEP_THINKING_LEVEL}")
    print(f"  Max USD Budget:         {fmt_usd(max_usd)}")
    print(f"  Mode:                   {'REAL CALL [BILLABLE]' if real_call else 'DRY-RUN (fail-closed)'}")
    print_separator()

    # Precheck provider & key if real call
    if real_call:
        if settings.ANALYSIS_PROVIDER.lower() != "gemini_api":
            print(
                f"\nERROR: --confirm-real-call requires ANALYSIS_PROVIDER=gemini_api, "
                f"found '{settings.ANALYSIS_PROVIDER}'."
            )
            sys.exit(1)
        if not has_key:
            print("\nERROR: GEMINI_API_KEY is not configured. Cannot perform real call.")
            sys.exit(1)

    # Resolve Entry
    entry = db.query(Entry).options(joinedload(Entry.source)).filter(Entry.id == entry_uuid).first()
    if not entry:
        print(f"\nERROR: Entry '{entry_uuid}' not found in database.")
        sys.exit(1)

    content_hash = compute_content_hash(entry)
    content_len = len(entry.content or "")

    print(f"  Entry ID:               {entry.id}")
    print(f"  Source:                 {entry.source.name if entry.source else 'Unknown'}")
    print(f"  Title:                  {entry.title}")
    print(f"  Published At:           {entry.published_at}")
    print(f"  Content Type:           {entry.content_type}")
    print(f"  Content Length:         {content_len:,} chars")
    print(f"  Content Hash:           {content_hash}")
    print_separator()

    # Precheck prior analyses
    prior_analyses = db.query(EntryAnalysis).filter(EntryAnalysis.entry_id == entry.id).all()
    if prior_analyses:
        print(f"\nERROR: Entry '{entry.id}' already has {len(prior_analyses)} EntryAnalysis record(s).")
        print("Smoke test aborted to prevent duplicate processing.")
        sys.exit(1)

    print("  Prior Analyses:         0 (clean)")
    print_separator("=")

    if not real_call:
        print("\n  DRY-RUN COMPLETE: Prechecks passed.")
        print("  To execute the single real call against Gemini Developer API:")
        print(f"    python -m scripts.run_analysis_smoke --entry-id {entry.id} --confirm-real-call")
        return

    # Resolve prompts & matrix
    triage_prompt, deep_prompt = resolve_prompts(db)
    matrix = resolve_matrix(db)

    print(f"\n  Triage Prompt:          {triage_prompt.code}:v{triage_prompt.version} [{triage_prompt.id}]")
    print(f"  Deep Prompt:            {deep_prompt.code}:v{deep_prompt.version} [{deep_prompt.id}]")
    print(f"  Matrix:                 {matrix.code} [{matrix.id}]")
    print("\n  Executing single Entry pipeline...")
    print_separator("-")

    provider = GeminiAPIProvider(settings=settings)
    pipeline = AnalysisPipelineService(provider=provider)

    extra_meta = {
        "smoke_test": True,
        "runner": "run_analysis_smoke",
    }

    analysis = await pipeline.run_pipeline(
        entry_id=entry.id,
        matrix_id=matrix.id,
        triage_prompt_id=triage_prompt.id,
        deep_prompt_id=deep_prompt.id,
        db=db,
        pipeline_version="v2",
        extra_call_metadata=extra_meta,
    )

    db.refresh(analysis)
    calls = sorted(analysis.calls, key=lambda c: c.created_at)
    triage_call = next((c for c in calls if c.stage == "triage"), None)
    deep_call = next((c for c in calls if c.stage == "deep_analysis"), None)

    # -----------------------------------------------------------------------
    # Report TRIAGE
    # -----------------------------------------------------------------------
    print("\n  [1/2] TRIAGE RESULT:")
    print_separator()
    print(f"  EntryAnalysis ID:       {analysis.id}")
    print(f"  Status:                 {analysis.status}")
    print(f"  Relevance Score:        {analysis.relevance_score}")
    print(f"  Relevance Status:       {analysis.relevance_status}")
    print(f"  Confidence:             {analysis.confidence}")
    topics = [t.topic.code for t in analysis.topics if t.topic]
    primary_topic = next((t.topic.code for t in analysis.topics if t.is_primary and t.topic), None)
    print(f"  Topic Codes:            {topics}")
    print(f"  Primary Topic:          {primary_topic}")
    print(f"  Reason:                 {analysis.reason}")

    if triage_call:
        t_in = triage_call.input_tokens or 0
        t_out = triage_call.output_tokens or 0
        t_meta = triage_call.call_metadata or {}
        t_txt = t_meta.get("output_text_tokens", "-")
        t_thk = t_meta.get("output_thought_tokens", "-")
        t_cost = float(triage_call.estimated_cost_usd or 0)
        t_lat = triage_call.latency_ms
        t_level = t_meta.get("thinking_level", "-")

        print(f"  Input Tokens:           {t_in}")
        print(f"  Output Text Tokens:     {t_txt}")
        print(f"  Output Thought Tokens:  {t_thk}")
        print(f"  Output Billable Tokens: {t_out}")
        print(f"  Estimated Cost:         {fmt_usd(t_cost)}")
        print(f"  Latency:                {t_lat}ms")
        print(f"  Thinking Level:         {t_level}")
        print(f"  Call ID:                {triage_call.id}")
    print_separator()

    # -----------------------------------------------------------------------
    # Report DEEP (if executed)
    # -----------------------------------------------------------------------
    if deep_call:
        print("\n  [2/2] DEEP ANALYSIS RESULT:")
        print_separator()
        print(f"  Summary:\n  {analysis.summary}")
        print("\n  Key Points:")
        for kp in (analysis.key_points or []):
            print(f"    * {kp}")

        d_in = deep_call.input_tokens or 0
        d_out = deep_call.output_tokens or 0
        d_meta = deep_call.call_metadata or {}
        d_txt = d_meta.get("output_text_tokens", "-")
        d_thk = d_meta.get("output_thought_tokens", "-")
        d_cost = float(deep_call.estimated_cost_usd or 0)
        d_lat = deep_call.latency_ms
        d_level = d_meta.get("thinking_level", "-")

        print(f"\n  Input Tokens:           {d_in}")
        print(f"  Output Text Tokens:     {d_txt}")
        print(f"  Output Thought Tokens:  {d_thk}")
        print(f"  Output Billable Tokens: {d_out}")
        print(f"  Estimated Cost:         {fmt_usd(d_cost)}")
        print(f"  Latency:                {d_lat}ms")
        print(f"  Thinking Level:         {d_level}")
        print(f"  Call ID:                {deep_call.id}")
        print_separator()
    else:
        print(f"\n  DEEP ANALYSIS: Skipped (relevance_status={analysis.relevance_status})")

    # -----------------------------------------------------------------------
    # Total Cost & Budget check
    # -----------------------------------------------------------------------
    total_cost = sum(float(c.estimated_cost_usd or 0) for c in calls)
    print(f"\n  TOTAL ESTIMATED COST:   {fmt_usd(total_cost)}")
    print(f"  BUDGET LIMIT:           {fmt_usd(max_usd)}")
    if total_cost > max_usd:
        print(f"  WARNING: Total cost {fmt_usd(total_cost)} exceeded max budget {fmt_usd(max_usd)}!")
    else:
        print("  BUDGET CHECK:           OK (within limit)")

    print_separator("=")
    print("  SMOKE TEST COMPLETED SUCCESSFULLY")
    print_separator("=")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HITCHINGS - AI Analysis Single Entry Smoke Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--entry-id",
        type=uuid.UUID,
        required=True,
        help="UUID of the Entry to analyze.",
    )
    parser.add_argument(
        "--confirm-real-call",
        action="store_true",
        default=False,
        help="Actually invoke Gemini Developer API. Dry-run if omitted.",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=0.10,
        help="Maximum USD budget for this single run (default 0.10).",
    )
    args = parser.parse_args()

    db: Session = SessionLocal()
    try:
        asyncio.run(run_smoke(
            entry_uuid=args.entry_id,
            real_call=args.confirm_real_call,
            max_usd=args.max_usd,
            db=db,
        ))
    finally:
        db.close()


if __name__ == "__main__":
    main()
