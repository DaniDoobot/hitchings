"""Read-only diagnostic script for ADLC production entries 26-DCC-180 and 26-DCC-181.

Strictly:
  0 writes
  0 flushes
  0 commits
  0 Gemini calls

Identifies entries by canonical external_id (adlc:act:26-dcc-NNN) with fallback
to raw_metadata["official_id"] for robustness. Never relies on titles.
"""

import sys
sys.path.insert(0, ".")
import json
from sqlalchemy import select, or_
from app.db.session import SessionLocal
from app.models.entry import Entry
from app.models.analysis import EntryAnalysis, AnalysisCall, AnalysisPromptVersion, EntryAnalysisTopic
from app.models.source import Source
from app.services.source_sufficiency_service import SourceSufficiencyService


# Canonical external_ids: adlc:act:<official_id_lower_no_spaces>
CANONICAL_EXTERNAL_IDS = [
    "adlc:act:26-dcc-180",
    "adlc:act:26-dcc-181",
]

# Fallback: raw_metadata["official_id"] values (case-insensitive match)
OFFICIAL_ID_FALLBACKS = [
    "26-DCC-180",
    "26-DCC-181",
]


def inspect():
    db = SessionLocal()
    try:
        # Primary lookup: by canonical external_id
        entries = db.execute(
            select(Entry).where(
                Entry.external_id.in_(CANONICAL_EXTERNAL_IDS)
            )
        ).scalars().all()

        # Fallback: by raw_metadata["official_id"] — strictly read-only
        if len(entries) < len(CANONICAL_EXTERNAL_IDS):
            found_ids = {e.external_id for e in entries}
            fallback_entries = db.execute(
                select(Entry).where(
                    Entry.raw_metadata["official_id"].astext.in_(OFFICIAL_ID_FALLBACKS)
                )
            ).scalars().all()
            for fe in fallback_entries:
                if fe.external_id not in found_ids:
                    entries = list(entries) + [fe]
                    found_ids.add(fe.external_id)

        print(f"Found {len(entries)} entries for ADLC 26-DCC-180/181:")

        for e in entries:
            print("=" * 80)
            print(f"Entry ID:     {e.id}")
            print(f"External ID:  {e.external_id}")
            print(f"Title:        {e.title}")
            print(f"URL:          {e.url}")
            print(f"Published At: {e.published_at}")
            print(f"Captured At:  {e.captured_at}")
            print(f"Language:     {e.language}")
            print(f"Content Type: {e.content_type}")

            # Source
            source = db.execute(
                select(Source).where(Source.id == e.source_id)
            ).scalar_one_or_none()
            print(f"Source:       {source.name if source else 'NOT FOUND'} (id={e.source_id})")

            # Sufficiency — computed from entry content (no DB write)
            try:
                suff = SourceSufficiencyService.assess(e)
                print(f"Sufficiency:  {suff.level.value} (chars={suff.signals.content_chars}, full_text={suff.signals.full_text_available})")
                print(f"Suff Reason:  {suff.reason}")
            except Exception as exc:
                print(f"Sufficiency:  ERROR: {exc}")

            # raw_metadata relevant fields
            raw_meta = e.raw_metadata or {}
            print(f"Official ID:  {raw_meta.get('official_id', 'n/a')}")
            print(f"Act Type:     {raw_meta.get('act_type', 'n/a')}")
            print(f"Phase:        {raw_meta.get('phase', 'n/a')}")
            print(f"Outcome:      {raw_meta.get('outcome', 'n/a')}")
            print(f"PDF URL:      {raw_meta.get('pdf_url', 'n/a')}")
            print(f"PDF Extracted:{raw_meta.get('pdf_extracted', 'n/a')}")

            # Analyses
            analyses = db.execute(
                select(EntryAnalysis)
                .where(EntryAnalysis.entry_id == e.id)
                .order_by(EntryAnalysis.created_at.desc())
            ).scalars().all()
            print(f"\nTotal analyses: {len(analyses)}")

            for a in analyses:
                print(f"\n  ── Analysis ID: {a.id}")
                print(f"  Pipeline Version:  {a.pipeline_version}")
                print(f"  Status:            {a.status}")
                print(f"  Relevance Status:  {a.relevance_status}")
                print(f"  Relevance Score:   {a.relevance_score}")
                print(f"  Confidence:        {a.confidence}")
                print(f"  Reason:            {a.reason}")
                print(f"  Summary:           {str(a.summary)[:200] if a.summary else 'None'}")
                print(f"  Created At:        {a.created_at}")

                # Topics
                topics = db.execute(
                    select(EntryAnalysisTopic).where(EntryAnalysisTopic.analysis_id == a.id)
                ).scalars().all()
                print(f"  Topics count: {len(topics)}")
                for t in topics:
                    topic_code = t.topic.code if t.topic else str(t.topic_id)
                    print(f"    Topic: {topic_code} (primary={t.is_primary}, conf={t.confidence})")
                    if t.rationale:
                        print(f"           rationale: {str(t.rationale)[:150]}")

                # Calls
                calls = db.execute(
                    select(AnalysisCall)
                    .where(AnalysisCall.entry_analysis_id == a.id)
                    .order_by(AnalysisCall.created_at)
                ).scalars().all()
                print(f"  Calls count: {len(calls)}")
                for c in calls:
                    prompt = db.execute(
                        select(AnalysisPromptVersion).where(AnalysisPromptVersion.id == c.prompt_version_id)
                    ).scalar_one_or_none()
                    if prompt:
                        p_info = (
                            f"{prompt.code} v{prompt.version} "
                            f"(stage={prompt.stage}, schema={prompt.response_schema_version})"
                        )
                        p_cfg = prompt.config or {}
                    else:
                        p_info = "Unknown"
                        p_cfg = {}

                    call_meta = c.call_metadata or {}
                    grounding_mode = p_cfg.get("grounding_mode") or call_meta.get("grounding_mode", "n/a")
                    input_tokens = call_meta.get("input_tokens", call_meta.get("prompt_tokens", "n/a"))
                    output_tokens = call_meta.get("output_tokens", call_meta.get("completion_tokens", "n/a"))

                    print(f"\n    ── Call ID: {c.id}")
                    print(f"    Stage:          {c.stage}")
                    print(f"    Status:         {c.status}")
                    print(f"    Provider/Model: {c.provider} / {c.model}")
                    print(f"    Prompt:         {p_info}")
                    print(f"    Grounding Mode: {grounding_mode}")
                    print(f"    Tokens in/out:  {input_tokens} / {output_tokens}")
                    print(f"    Call Metadata:  {json.dumps(call_meta, default=str)}")

                    if c.raw_response:
                        raw_data = c.raw_response if isinstance(c.raw_response, dict) else {}
                        print(f"    Raw Response Keys: {list(raw_data.keys())}")
                        if "parsed_json" in raw_data:
                            parsed = raw_data["parsed_json"]
                            print(f"    Parsed JSON (relevant keys):")
                            for key in ["relevance_status", "relevance_score", "reasoning",
                                        "topics", "confidence", "deep_analysis_recommended"]:
                                if key in parsed:
                                    val = parsed[key]
                                    val_str = json.dumps(val) if not isinstance(val, str) else val
                                    print(f"      {key}: {val_str[:300]}")
                        elif "response_payload" in raw_data:
                            print(f"    Payload excerpt: {json.dumps(raw_data['response_payload'])[:500]}")
                        else:
                            print(f"    Raw excerpt: {str(c.raw_response)[:400]}")

        print("\n" + "=" * 80)
        print("DIAGNOSTIC COMPLETE — 0 DB writes, 0 commits, 0 Gemini calls.")

    finally:
        db.close()


if __name__ == "__main__":
    inspect()
