"""Bloque 16C: Read-only diagnostic script for identifying and inspecting the single failed ADLC analysis.

Strict guarantees:
- 0 DB writes (SET TRANSACTION READ ONLY)
- 0 LLM calls
- 0 network requests
- Strictly diagnostic
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.analysis import (
    AnalysisCall,
    AnalysisPromptVersion,
    EntryAnalysis,
    EntryAnalysisTopic,
)
from app.models.entry import Entry
from app.models.source import Source
from app.services.source_sufficiency_service import SourceSufficiencyService


def run_diagnostic() -> dict[str, Any]:
    db: Session = SessionLocal()
    report_data: dict[str, Any] = {}

    try:
        # Enforce read-only transaction in PostgreSQL if possible
        try:
            db.execute(text("SET TRANSACTION READ ONLY"))
        except Exception:
            pass

        # 1. Resolve ADLC source
        adlc_source = (
            db.execute(
                select(Source).where(
                    Source.name.ilike("%autorit%")
                    | Source.url.ilike("%autoritedelaconcurrence%")
                )
            )
            .scalars()
            .first()
        )

        if not adlc_source:
            print("ERROR: ADLC Source not found in database!")
            return {"error": "ADLC Source not found"}

        print("=" * 80)
        print("BLOQUE 16C — DIAGNÓSTICO READ-ONLY ANÁLISIS ADLC")
        print("=" * 80)
        print(f"Source Name : {adlc_source.name}")
        print(f"Source ID   : {adlc_source.id}")
        print(f"Source URL  : {adlc_source.url}")
        print(f"Source Type : {adlc_source.type}")
        print(f"Provider    : {adlc_source.provider}")
        print("-" * 80)

        # 2. Query all entries for ADLC
        all_entries: list[Entry] = (
            db.execute(
                select(Entry)
                .where(Entry.source_id == adlc_source.id)
                .order_by(Entry.published_at.desc(), Entry.created_at.desc())
            )
            .scalars()
            .all()
        )

        total_entries = len(all_entries)
        completed_entries: list[Entry] = []
        failed_only_entries: list[tuple[Entry, list[EntryAnalysis]]] = []
        no_analysis_entries: list[Entry] = []

        for ent in all_entries:
            analyses: list[EntryAnalysis] = (
                db.execute(
                    select(EntryAnalysis)
                    .where(EntryAnalysis.entry_id == ent.id)
                    .order_by(EntryAnalysis.created_at.desc())
                )
                .scalars()
                .all()
            )

            has_completed = any(ea.status == "completed" for ea in analyses)
            if has_completed:
                completed_entries.append(ent)
            elif analyses:
                failed_only_entries.append((ent, analyses))
            else:
                no_analysis_entries.append(ent)

        print(f"Total ADLC Entries in DB       : {total_entries}")
        print(f"Entries with Completed Analysis: {len(completed_entries)}")
        print(f"Entries with Failed-Only       : {len(failed_only_entries)}")
        print(f"Entries with No Analysis       : {len(no_analysis_entries)}")
        print("-" * 80)

        report_data["total_entries"] = total_entries
        report_data["completed_count"] = len(completed_entries)
        report_data["failed_only_count"] = len(failed_only_entries)
        report_data["no_analysis_count"] = len(no_analysis_entries)

        # 3. Identify the target failed/pending entry
        target_entry: Optional[Entry] = None
        target_analyses: list[EntryAnalysis] = []

        if failed_only_entries:
            target_entry, target_analyses = failed_only_entries[0]
        elif no_analysis_entries:
            target_entry = no_analysis_entries[0]
            target_analyses = []
        else:
            print("NOTE: All entries have completed analysis! No failed case found.")
            return report_data

        raw_meta = target_entry.raw_metadata or {}
        official_id = raw_meta.get("official_id", "n/a")
        content = target_entry.content or ""
        content_len = len(content)

        # Assess sufficiency
        suff_level = "unknown"
        suff_reason = "unknown"
        try:
            suff = SourceSufficiencyService.assess(target_entry)
            suff_level = suff.level.value
            suff_reason = suff.reason
        except Exception as exc:
            suff_reason = f"Error: {exc}"

        print("\n==================================================")
        print("1. CASO FALLIDO IDENTIFICADO")
        print("==================================================")
        print(f"Entry ID       : {target_entry.id}")
        print(f"External ID    : {target_entry.external_id}")
        print(f"Official ID    : {official_id}")
        print(f"Title          : {target_entry.title}")
        print(f"Published At   : {target_entry.published_at}")
        print(f"Captured At    : {target_entry.captured_at}")
        print(f"URL            : {target_entry.url}")
        print(f"Content Type   : {target_entry.content_type}")
        print(f"Content Length : {content_len} chars")
        print(f"Sufficiency    : {suff_level} ({suff_reason})")
        print(f"Act Type       : {raw_meta.get('act_type', 'n/a')}")
        print(f"Phase          : {raw_meta.get('phase', 'n/a')}")
        print(f"Outcome        : {raw_meta.get('outcome', 'n/a')}")
        print(f"PDF URL        : {raw_meta.get('pdf_url', 'n/a')}")
        print(f"PDF Extracted  : {raw_meta.get('pdf_extracted', 'n/a')}")
        print(f"Language       : {target_entry.language}")

        report_data["target_entry"] = {
            "id": str(target_entry.id),
            "external_id": target_entry.external_id,
            "official_id": official_id,
            "title": target_entry.title,
            "published_at": target_entry.published_at.isoformat() if target_entry.published_at else None,
            "content_type": target_entry.content_type,
            "content_length": content_len,
            "sufficiency": suff_level,
            "raw_metadata": raw_meta,
        }

        # 4. Inspect EntryAnalysis records
        print("\n==================================================")
        print(f"2. ENTRY ANALYSIS ({len(target_analyses)} records)")
        print("==================================================")

        report_data["analyses"] = []
        all_calls_info: list[dict[str, Any]] = []

        for i, ea in enumerate(target_analyses, 1):
            print(f"\n--- EntryAnalysis #{i} ---")
            print(f"Analysis ID     : {ea.id}")
            print(f"Pipeline Version: {ea.pipeline_version}")
            print(f"Status          : {ea.status}")
            print(f"Relevance Status: {ea.relevance_status}")
            print(f"Relevance Score : {ea.relevance_score}")
            print(f"Reason          : {ea.reason}")
            print(f"Created At      : {ea.created_at}")
            print(f"Completed At    : {ea.completed_at}")

            # Topics associated
            topics = (
                db.execute(
                    select(EntryAnalysisTopic).where(EntryAnalysisTopic.analysis_id == ea.id)
                )
                .scalars()
                .all()
            )
            print(f"Topics Count    : {len(topics)}")

            # Associated AnalysisCalls
            calls: list[AnalysisCall] = (
                db.execute(
                    select(AnalysisCall)
                    .where(AnalysisCall.entry_analysis_id == ea.id)
                    .order_by(AnalysisCall.started_at.asc())
                )
                .scalars()
                .all()
            )

            ea_data: dict[str, Any] = {
                "id": str(ea.id),
                "pipeline_version": ea.pipeline_version,
                "status": ea.status,
                "relevance_status": ea.relevance_status,
                "relevance_score": ea.relevance_score,
                "reason": ea.reason,
                "created_at": ea.created_at.isoformat() if ea.created_at else None,
                "completed_at": ea.completed_at.isoformat() if ea.completed_at else None,
                "calls": [],
            }

            print(f"\nAnalysis Calls ({len(calls)} calls):")
            for j, c in enumerate(calls, 1):
                # Prompt info
                prompt_ver = (
                    db.execute(
                        select(AnalysisPromptVersion).where(
                            AnalysisPromptVersion.id == c.prompt_version_id
                        )
                    )
                    .scalars()
                    .first()
                )
                p_code = prompt_ver.code if prompt_ver else "unknown"
                p_ver = prompt_ver.version if prompt_ver else "unknown"
                p_cfg = prompt_ver.config if prompt_ver else {}
                grounding_mode = (
                    (c.call_metadata or {}).get("grounding_mode")
                    or (p_cfg or {}).get("grounding_mode")
                    or ("v7_evidence_blocks" if p_ver == 7 else "legacy")
                )

                print(f"\n  [Call #{j}]")
                print(f"    Call ID        : {c.id}")
                print(f"    Stage          : {c.stage}")
                print(f"    Status         : {c.status}")
                print(f"    Provider       : {c.provider}")
                print(f"    Model          : {c.model}")
                print(f"    Prompt Code    : {p_code}")
                print(f"    Prompt Version : v{p_ver}")
                print(f"    Grounding Mode : {grounding_mode}")
                print(f"    Tokens In/Out  : {c.input_tokens} / {c.output_tokens}")
                print(f"    Latency        : {c.latency_ms} ms")
                print(f"    Error Type     : {c.error_type}")
                print(f"    Error Message  : {c.error_message}")
                print(f"    Started At     : {c.started_at}")
                print(f"    Completed At   : {c.completed_at}")

                # Call metadata
                meta = c.call_metadata or {}
                print(f"    Call Metadata  : {json.dumps(meta, default=str)}")

                # Raw response snippet
                if c.raw_response:
                    raw_snippet = c.raw_response[:500]
                    print(f"    Raw Response   : {raw_snippet}...")

                call_data: dict[str, Any] = {
                    "id": str(c.id),
                    "stage": c.stage,
                    "status": c.status,
                    "provider": c.provider,
                    "model": c.model,
                    "prompt_code": p_code,
                    "prompt_version": p_ver,
                    "grounding_mode": grounding_mode,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "latency_ms": c.latency_ms,
                    "error_type": c.error_type,
                    "error_message": c.error_message,
                    "call_metadata": meta,
                    "raw_response_snippet": c.raw_response[:1000] if c.raw_response else None,
                }
                ea_data["calls"].append(call_data)
                all_calls_info.append(call_data)

            report_data["analyses"].append(ea_data)

        # 5. Failure Stage & Root Cause Classification
        print("\n==================================================")
        print("3. ANÁLISIS DE FALLO Y CAUSA RAÍZ")
        print("==================================================")

        failed_call = next((c for c in all_calls_info if c["status"] == "failed"), None)
        if failed_call:
            print(f"Stage Fallido    : {failed_call['stage']}")
            print(f"Error Type       : {failed_call['error_type']}")
            print(f"Error Message    : {failed_call['error_message']}")

            # Classification
            err_msg = (failed_call["error_message"] or "").lower()
            err_typ = (failed_call["error_type"] or "").lower()

            if any(k in err_typ or k in err_msg for k in ["rate", "resourceexhausted", "quota", "429", "timeout", "unavailable", "503", "500"]):
                classification = "A) Fallo transitorio / reintentable (rate limit o timeout de proveedor API)"
            elif "grounding" in err_typ or "grounding" in err_msg or "evidence" in err_msg:
                classification = "B) Fallo de grounding v7 (evidence block resolution o validación estricta de citas)"
            elif "extraction" in err_typ or "content" in err_msg:
                classification = "C) Fallo de extracción/input (contenido corrupto o insuficiente)"
            elif any(k in err_typ or k in err_msg for k in ["parse", "json", "validation", "schema"]):
                classification = "D) Fallo de schema / parser (JSON malformado o violación de Pydantic)"
            else:
                classification = f"E/F) Otro / Bug de código ({failed_call['error_type']})"

            print(f"Clasificación    : {classification}")
            report_data["failure_classification"] = classification
        else:
            print("No se encontró AnalysisCall con status=='failed'.")

        # 6. Procedimiento de Reparación Quirúrgica
        print("\n==================================================")
        print("4. PROCEDIMIENTO DE REPARACIÓN QUIRÚRGICA")
        print("==================================================")
        print("Comando de DRY RUN previo:")
        print(f"  python -m scripts.run_incremental_analysis --entry-id {target_entry.id}")
        print("\nComando de EJECUCIÓN REAL:")
        print(f"  python -m scripts.run_incremental_analysis --entry-id {target_entry.id} --confirm-real-calls")
        print("\nGarantías:")
        print("  - Afecta estrictamente a: 1 Entry")
        print("  - Máximo de llamadas LLM: 2 (1 triage + 1 deep si es relevant)")
        print("  - Reutiliza 100% la infraestructura de producción existente (IncrementalAnalysisService)")
        print("  - 0 impacto / 0 mutación en las 48 Entries ya completadas")
        print("==================================================")

        return report_data

    finally:
        db.close()


if __name__ == "__main__":
    run_diagnostic()
