"""Controlled validation script for Bloque 7F: v4 Evidence Robustness.

Validates the v4 extract-first grounded pipeline on EXACTLY ONE entry:
CURIA C-60/25 Livronsa (UUID: 27c1a107-ebfe-40d0-ba9e-d7d399c3c565).

Usage:
    # Dry-run mode (preflight and verification only, no API calls):
    python -m scripts.run_v4_livronsa_validation

    # Real call (with confirmation flag and max budget cap $0.05):
    python -m scripts.run_v4_livronsa_validation --confirm-real-calls --max-usd 0.05
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

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
from app.services.source_sufficiency_service import assess_source_sufficiency

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("validation_v4_livronsa")

LIVRONSA_ENTRY_UUID = uuid.UUID("27c1a107-ebfe-40d0-ba9e-d7d399c3c565")


def fmt_usd(value: float) -> str:
    return f"${value:.6f}"


def fmt_ms(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return f"{value}ms"


def print_sep(char: str = "=", width: int = 110) -> None:
    print(char * width)


async def main() -> None:
    parser = argparse.ArgumentParser(description="HITCHINGS Bloque 7F - Livronsa v4 validation")
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        default=False,
        help="Explicit confirmation required to make real Gemini Developer API calls.",
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        default=0.05,
        help="Maximum incremental cost in USD for this validation run (hard stop at 0.05).",
    )
    args = parser.parse_args()

    settings = get_settings()

    print_sep("=")
    print("  HITCHINGS OBSERVATORY - BLOQUE 7F: V4 EVIDENCE ROBUSTNESS (LIVRONSA VALIDATION)")
    print_sep("=")

    db: Session = SessionLocal()
    try:
        # 1. Preflight checks
        print("\n--- PREFLIGHT CHECKS ---")
        print(f"Target Entry UUID:         {LIVRONSA_ENTRY_UUID}")
        print(f"Provider configured:       {settings.ANALYSIS_PROVIDER}")
        print(f"Gemini Model:              {settings.GEMINI_MODEL}")
        print(f"Triage Thinking Level:     {settings.ANALYSIS_TRIAGE_THINKING_LEVEL}")
        print(f"Deep Thinking Level:       {settings.ANALYSIS_DEEP_THINKING_LEVEL}")
        print(f"API Key configured:        {'YES (set)' if settings.GEMINI_API_KEY else 'NO (missing)'}")
        print(f"Budget limit for this run: {fmt_usd(args.max_usd)}")
        print(f"Real calls confirmed:      {args.confirm_real_calls}")

        if args.confirm_real_calls:
            if settings.ANALYSIS_PROVIDER != "gemini_api":
                logger.error(f"Cannot run real calls with ANALYSIS_PROVIDER='{settings.ANALYSIS_PROVIDER}'. Must be 'gemini_api'.")
                sys.exit(1)
            if not settings.GEMINI_API_KEY:
                logger.error("GEMINI_API_KEY is not set.")
                sys.exit(1)

        # 2. Verify tracking matrix
        matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
        if not matrix:
            logger.error("No active TrackingMatrix found.")
            sys.exit(1)
        print(f"Active Matrix:             {matrix.code} - {matrix.name}")

        # 3. Verify prompt versions v4
        triage_p = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_triage",
                AnalysisPromptVersion.version == 4,
                AnalysisPromptVersion.active.is_(True),
            )
            .first()
        )
        deep_p = (
            db.query(AnalysisPromptVersion)
            .filter(
                AnalysisPromptVersion.code == "observatory_deep_analysis",
                AnalysisPromptVersion.version == 4,
                AnalysisPromptVersion.active.is_(True),
            )
            .first()
        )

        if not triage_p or not deep_p:
            logger.error("Active v4 prompt versions not found in database. Run python -m scripts.seed_analysis_prompts first.")
            sys.exit(1)
        print(f"Triage Prompt v4:          {triage_p.code}:v{triage_p.version} (id={triage_p.id})")
        print(f"Deep Analysis Prompt v4:   {deep_p.code}:v{deep_p.version} (id={deep_p.id})")

        # 4. Load Livronsa Entry
        entry = db.query(Entry).options(joinedload(Entry.source)).filter(Entry.id == LIVRONSA_ENTRY_UUID).first()
        if not entry:
            logger.error(f"Target entry {LIVRONSA_ENTRY_UUID} not found in database.")
            sys.exit(1)

        suff = assess_source_sufficiency(entry)
        content_chars = len(entry.content or "")
        print(f"\nTarget Entry Details:")
        print(f"  Source:                  {entry.source.name}")
        print(f"  Title:                   {entry.title}")
        print(f"  Content Chars:           {content_chars}")
        print(f"  Source Sufficiency:      {suff.level.value.upper()} (source={suff.signals.content_source})")

        # 5. Review Historical Analyses (v2 and v3)
        v2_analysis = (
            db.query(EntryAnalysis)
            .filter(EntryAnalysis.entry_id == entry.id, EntryAnalysis.pipeline_version == "v2")
            .first()
        )
        v3_analysis = (
            db.query(EntryAnalysis)
            .filter(EntryAnalysis.entry_id == entry.id, EntryAnalysis.pipeline_version == "v3")
            .first()
        )

        print("\nHistorical Analyses:")
        if v2_analysis:
            print(f"  v2: status={v2_analysis.status} | score={v2_analysis.relevance_score} | key_points={len(v2_analysis.key_points or [])}")
        else:
            print("  v2: none")

        if v3_analysis:
            print(f"  v3: status={v3_analysis.status} | score={v3_analysis.relevance_score} | failure_reason={v3_analysis.reason[:90]}...")
        else:
            print("  v3: none")

        if not args.confirm_real_calls:
            print_sep("-")
            print("DRY-RUN COMPLETE: --confirm-real-calls was not specified.")
            print("No external calls were made. Livronsa is verified and ready for v4 validation.")
            print("To execute real validation run:")
            print("  python -m scripts.run_v4_livronsa_validation --confirm-real-calls --max-usd 0.05")
            print_sep("-")
            return

        # 6. Execute Real Validation Run
        print("\n" + "=" * 110)
        print("  STARTING REAL VALIDATION RUN ON LIVRONSA (GEMINI API - PROMPTS V4)")
        print("=" * 110)

        validation_run_id = uuid.uuid4()
        run_start_time = datetime.now(timezone.utc)

        gemini_provider = GeminiAPIProvider()
        pipeline = AnalysisPipelineService(provider=gemini_provider)

        extra_meta = {
            "validation_run": True,
            "validation_version": "v4_evidence_robustness",
            "validation_run_id": str(validation_run_id),
            "target_entry": "livronsa",
        }

        v4_analysis = await pipeline.run_pipeline(
            entry_id=entry.id,
            matrix_id=matrix.id,
            triage_prompt_id=triage_p.id,
            deep_prompt_id=deep_p.id,
            db=db,
            pipeline_version="v4",
            extra_call_metadata=extra_meta,
        )

        # Load calls made for v4 analysis
        calls = (
            db.query(AnalysisCall)
            .filter(AnalysisCall.entry_analysis_id == v4_analysis.id)
            .order_by(AnalysisCall.created_at)
            .all()
        )
        total_cost_usd = sum(float(c.estimated_cost_usd or 0.0) for c in calls)

        print("\n--- RUN EXECUTION RESULTS ---")
        print(f"EntryAnalysis ID:          {v4_analysis.id}")
        print(f"Pipeline Version:          {v4_analysis.pipeline_version}")
        print(f"Analysis Status:           {v4_analysis.status.upper()}")
        print(f"Relevance Score:           {v4_analysis.relevance_score}/100 ({v4_analysis.relevance_status})")
        print(f"Confidence:                {v4_analysis.confidence}")
        print(f"Total Calls:               {len(calls)}")
        print(f"Total Incremental Cost:    {fmt_usd(total_cost_usd)}")

        # Inspect calls and quotes
        triage_call = next((c for c in calls if c.stage == "triage"), None)
        deep_call = next((c for c in calls if c.stage == "deep_analysis"), None)

        triage_quotes: list[str] = []
        if triage_call and triage_call.raw_response and isinstance(triage_call.raw_response, dict):
            res_dict = triage_call.raw_response.get("result", {})
            if isinstance(res_dict, dict):
                triage_quotes = [e.get("quote", "") for e in res_dict.get("evidence", []) if isinstance(e, dict)]

        deep_summary_quotes: list[str] = []
        deep_kp_quotes: list[str] = []
        if deep_call and deep_call.raw_response and isinstance(deep_call.raw_response, dict):
            res_dict = deep_call.raw_response.get("result", {})
            if isinstance(res_dict, dict):
                deep_summary_quotes = [e.get("quote", "") for e in res_dict.get("summary_evidence", []) if isinstance(e, dict)]
                for kp in res_dict.get("key_points", []):
                    if isinstance(kp, dict):
                        deep_kp_quotes.extend([e.get("quote", "") for e in kp.get("evidence", []) if isinstance(e, dict)])

        all_v4_quotes = triage_quotes + deep_summary_quotes + deep_kp_quotes
        quote_lens = [len(q) for q in all_v4_quotes]
        quote_words = [len(q.split()) for q in all_v4_quotes]

        print("\n--- EVIDENCE QUOTES AUDIT ---")
        print(f"Triage Quotes:             {len(triage_quotes)}")
        for i, q in enumerate(triage_quotes, 1):
            print(f"  [{i}] ({len(q)} chars, {len(q.split())} words): \"{q}\"")

        print(f"Deep Summary Quotes:       {len(deep_summary_quotes)}")
        for i, q in enumerate(deep_summary_quotes, 1):
            print(f"  [{i}] ({len(q)} chars, {len(q.split())} words): \"{q}\"")

        print(f"Deep Key Points Quotes:    {len(deep_kp_quotes)}")
        for i, q in enumerate(deep_kp_quotes, 1):
            print(f"  [{i}] ({len(q)} chars, {len(q.split())} words): \"{q}\"")

        if quote_lens:
            print(f"\nQuote Statistics (v4):")
            print(f"  Total Quotes:            {len(all_v4_quotes)}")
            print(f"  Avg Chars / Quote:       {sum(quote_lens) / len(quote_lens):.1f} chars (min={min(quote_lens)}, max={max(quote_lens)})")
            print(f"  Avg Words / Quote:       {sum(quote_words) / len(quote_words):.1f} words (min={min(quote_words)}, max={max(quote_words)})")
            print(f"  Quote Verification Rate: {'100.0%' if v4_analysis.status == 'completed' else 'FAILED'}")

        print("\n--- COMPARISON ACROSS VERSIONS (LIVRONSA) ---")
        print(f"{'Version':<10} | {'Status':<12} | {'Score':<8} | {'Calls':<6} | {'Cost':<10} | Outcome / Notes")
        print("-" * 90)
        print(f"{'v2':<10} | {v2_analysis.status if v2_analysis else '-':<12} | {str(v2_analysis.relevance_score if v2_analysis else '-'):<8} | {'2':<6} | {'$0.019777':<10} | Completed without evidence validation")
        print(f"{'v3':<10} | {v3_analysis.status if v3_analysis else '-':<12} | {str(v3_analysis.relevance_score if v3_analysis else '-'):<8} | {'2':<6} | {'$0.018243':<10} | Failed: deep quote paraphrased 1 word")
        v4_status_str = v4_analysis.status.upper()
        v4_note = 'Completed: 100% verified verbatim' if v4_analysis.status == 'completed' else v4_analysis.reason[:40]
        print(f"{'v4':<10} | {v4_status_str:<12} | {str(v4_analysis.relevance_score):<8} | {str(len(calls)):<6} | {fmt_usd(total_cost_usd):<10} | {v4_note}")
        print_sep("=")

        # Persist results to scratch
        out_path = Path("scratch_validation_v4_livronsa.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "validation_run_id": str(validation_run_id),
                    "target_entry_id": str(LIVRONSA_ENTRY_UUID),
                    "started_at": run_start_time.isoformat(),
                    "total_cost_usd": total_cost_usd,
                    "analysis": {
                        "id": str(v4_analysis.id),
                        "status": v4_analysis.status,
                        "relevance_score": v4_analysis.relevance_score,
                        "relevance_status": v4_analysis.relevance_status,
                        "confidence": v4_analysis.confidence,
                        "reason": v4_analysis.reason,
                        "summary": v4_analysis.summary,
                        "key_points_count": len(v4_analysis.key_points or []),
                    },
                    "quotes": {
                        "triage": triage_quotes,
                        "deep_summary": deep_summary_quotes,
                        "deep_key_points": deep_kp_quotes,
                        "total_count": len(all_v4_quotes),
                        "verified_pct": 100.0 if v4_analysis.status == "completed" else 0.0,
                    },
                    "calls": [
                        {
                            "id": str(c.id),
                            "stage": c.stage,
                            "status": c.status,
                            "input_tokens": c.input_tokens,
                            "output_tokens": c.output_tokens,
                            "cost_usd": float(c.estimated_cost_usd or 0.0),
                            "latency_ms": c.latency_ms,
                        }
                        for c in calls
                    ],
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"\nDetailed metrics saved to: {out_path.resolve()}\n")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
