import sys
sys.path.insert(0, ".")
import json
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.entry import Entry
from app.models.analysis import EntryAnalysis, AnalysisCall, AnalysisPromptVersion, EntryAnalysisTopic
from app.models.source import Source

def inspect():
    db = SessionLocal()
    try:
        entries = db.execute(
            select(Entry).where(
                Entry.external_id.in_(["26-DCC-180", "26-DCC-181"])
            )
        ).scalars().all()

        print(f"Found {len(entries)} entries matching 26-DCC-180/181:")
        for e in entries:
            print("=" * 80)
            print(f"Entry ID: {e.id}")
            print(f"External ID: {e.external_id}")
            print(f"Title: {e.title}")
            print(f"URL: {e.url}")
            print(f"Published At: {e.published_at}")
            print(f"Source ID: {e.source_id}")
            
            # Sufficiency
            suff = e.extra_metadata.get("source_sufficiency") if e.extra_metadata else None
            print(f"Source Sufficiency in extra_metadata: {suff}")

            # Analyses
            analyses = db.execute(
                select(EntryAnalysis).where(EntryAnalysis.entry_id == e.id).order_by(EntryAnalysis.created_at.desc())
            ).scalars().all()
            print(f"Total analyses: {len(analyses)}")

            for a in analyses:
                print(f"  Analysis ID: {a.id}")
                print(f"  Pipeline Version: {a.pipeline_version}")
                print(f"  Status: {a.status}")
                print(f"  Relevance Status: {a.relevance_status}")
                print(f"  Relevance Score: {a.relevance_score}")
                print(f"  Confidence: {a.confidence}")
                print(f"  Reason: {a.reason}")
                print(f"  Summary: {a.summary}")

                # Topics
                topics = db.execute(
                    select(EntryAnalysisTopic).where(EntryAnalysisTopic.analysis_id == a.id)
                ).scalars().all()
                print(f"  Topics count: {len(topics)}")
                for t in topics:
                    print(f"    Topic: {t.topic.code if t.topic else t.topic_id} (primary={t.is_primary}, conf={t.confidence}, rationale={t.rationale})")

                # Calls
                calls = db.execute(
                    select(AnalysisCall).where(AnalysisCall.entry_analysis_id == a.id).order_by(AnalysisCall.created_at)
                ).scalars().all()
                print(f"  Calls count: {len(calls)}")
                for c in calls:
                    prompt = db.execute(
                        select(AnalysisPromptVersion).where(AnalysisPromptVersion.id == c.prompt_version_id)
                    ).scalar_one_or_none()
                    p_info = f"{prompt.code} v{prompt.version} (stage={prompt.stage}, schema={prompt.response_schema_version})" if prompt else "Unknown"
                    print(f"    Call ID: {c.id}")
                    print(f"    Stage: {c.stage}")
                    print(f"    Status: {c.status}")
                    print(f"    Provider/Model: {c.provider} / {c.model}")
                    print(f"    Prompt: {p_info}")
                    print(f"    Call Metadata: {json.dumps(c.call_metadata)}")
                    if c.raw_response:
                        # Print relevant parts of raw response
                        raw_data = c.raw_response if isinstance(c.raw_response, dict) else {}
                        print(f"    Raw Response Keys: {list(raw_data.keys())}")
                        if "parsed_json" in raw_data:
                            print(f"    Parsed JSON: {json.dumps(raw_data['parsed_json'], indent=2)}")
                        elif "response_payload" in raw_data:
                            print(f"    Payload: {json.dumps(raw_data['response_payload'], indent=2)}")
                        else:
                            print(f"    Raw excerpt: {str(c.raw_response)[:300]}")
    finally:
        db.close()

if __name__ == "__main__":
    inspect()
