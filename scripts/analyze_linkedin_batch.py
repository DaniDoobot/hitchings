"""Script de validación y ejecución de análisis Gemini sobre Entries de LinkedIn.

Reutiliza estrictamente la arquitectura oficial del Observatorio HITCHINGS:
- AnalysisPipelineService (v6 triage -> deep analysis)
- GeminiAPIProvider
- AnalysisPromptVersion activos (observatory_triage, observatory_deep_analysis)
- Modelos EntryAnalysis, AnalysisCall, EntryAnalysisTopic

Uso:
    # Modo seguro previo (sin llamadas LLM ni escrituras)
    python -m scripts.analyze_linkedin_batch --dry-run

    # Análisis real controlado con límite y entidad
    python -m scripts.analyze_linkedin_batch --entity Hausfeld --limit 2

    # Análisis real de todo el lote pendiente
    python -m scripts.analyze_linkedin_batch --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.analysis import AnalysisCall, AnalysisPromptVersion, EntryAnalysis
from app.models.entry import Entry
from app.models.tracking import TrackingMatrix
from app.providers.ai.base import BaseAIProvider
from app.providers.ai.gemini_api import GeminiAPIProvider
from app.services.analysis_pipeline_service import AnalysisPipelineService

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def get_unanalyzed_linkedin_entries(
    db: Session,
    entity_name: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[Entry]:
    """Recupera Entries con origen LinkedIn que no tienen un EntryAnalysis completado.

    Garantiza aislamiento estricto: solo selecciona publicaciones de LinkedIn
    (source_origin_category == 'linkedin'), excluyendo fuentes institucionales o web.
    """
    stmt = (
        select(Entry)
        .options(
            joinedload(Entry.source),
            joinedload(Entry.analyses),
        )
        .where(
            (Entry.content_type == "social_post")
            | (Entry.url.ilike("%linkedin.com%"))
        )
        .order_by(Entry.published_at.desc().nullslast(), Entry.created_at.desc())
    )
    all_entries = db.execute(stmt).unique().scalars().all()

    candidates: list[Entry] = []
    for entry in all_entries:
        # Validación de categoría de origen
        if entry.source_origin_category != "linkedin":
            continue

        # Filtrar si ya cuenta con análisis completado
        has_completed_analysis = any(
            a.status == "completed" for a in (entry.analyses or [])
        )
        if has_completed_analysis:
            continue

        # Filtro opcional por entidad
        if entity_name:
            target = entity_name.strip().lower()
            meta = entry.raw_metadata or {}
            tracked_name = str(meta.get("tracked_entity_name") or "").strip().lower()
            author = str(entry.author or "").strip().lower()
            title = str(entry.title or "").strip().lower()

            if target != tracked_name and target != author and target not in title:
                continue

        candidates.append(entry)
        if limit is not None and len(candidates) >= limit:
            break

    return candidates


async def run_linkedin_analysis_batch(
    db: Session,
    provider: Optional[BaseAIProvider] = None,
    entity_name: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    """Ejecuta el pipeline de análisis v6 sobre las Entries de LinkedIn no analizadas."""
    cfg = settings or get_settings()

    # 1. Resolver prompts activos
    triage_prompt = (
        db.query(AnalysisPromptVersion)
        .filter(
            AnalysisPromptVersion.code == "observatory_triage",
            AnalysisPromptVersion.active.is_(True),
        )
        .order_by(AnalysisPromptVersion.version.desc())
        .first()
    )
    if not triage_prompt:
        raise RuntimeError("No se encontró una versión activa de 'observatory_triage' en la base de datos.")

    deep_prompt = (
        db.query(AnalysisPromptVersion)
        .filter(
            AnalysisPromptVersion.code == "observatory_deep_analysis",
            AnalysisPromptVersion.active.is_(True),
        )
        .order_by(AnalysisPromptVersion.version.desc())
        .first()
    )
    if not deep_prompt:
        raise RuntimeError("No se encontró una versión activa de 'observatory_deep_analysis' en la base de datos.")

    # 2. Resolver TrackingMatrix activa
    matrix = db.query(TrackingMatrix).filter(TrackingMatrix.status == "active").first()
    if not matrix:
        raise RuntimeError("No se encontró una TrackingMatrix activa en la base de datos.")

    # 3. Localizar candidatos no analizados
    candidates = get_unanalyzed_linkedin_entries(db, entity_name=entity_name, limit=limit)

    report: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "matrix_code": matrix.code,
        "triage_prompt": f"{triage_prompt.code}:v{triage_prompt.version}",
        "deep_prompt": f"{deep_prompt.code}:v{deep_prompt.version}",
        "candidates_found": len(candidates),
        "analyzed_count": 0,
        "relevant_count": 0,
        "uncertain_count": 0,
        "not_relevant_count": 0,
        "deep_executed_count": 0,
        "failed_count": 0,
        "entries": [],
    }

    print("\n" + "=" * 70)
    print("HITCHINGS OBSERVATORIO — ANÁLISIS BATCH DE LINKEDIN (GEMINI)")
    print("=" * 70)
    print(f"Modo                      : {'DRY-RUN (Simulación segura)' if dry_run else 'REAL (Gemini AI)'}")
    print(f"Tracking Matrix           : {matrix.code} ({matrix.name})")
    print(f"Prompt Triage Activo      : {triage_prompt.code}:v{triage_prompt.version}")
    print(f"Prompt Deep Activo        : {deep_prompt.code}:v{deep_prompt.version}")
    print(f"Filtro Entidad            : {entity_name or '(todas las entidades verificadas)'}")
    print(f"Límite                    : {limit or '(sin límite)'}")
    print(f"Entries LinkedIn a procesar: {len(candidates)}")
    print("=" * 70)

    if not candidates:
        print("\nNo se encontraron nuevas Entries de LinkedIn pendientes de análisis.")
        return report

    # 4. MODO DRY-RUN: Previsualizar sin llamadas ni escrituras
    if dry_run:
        print("\n[DRY-RUN] Entradas candidatas identificadas para análisis:")
        for idx, entry in enumerate(candidates, 1):
            meta = entry.raw_metadata or {}
            ent_name = meta.get("tracked_entity_name") or entry.author or "N/A"
            pub_date = entry.published_at.strftime("%Y-%m-%d") if entry.published_at else "N/A"
            print(f"\n  {idx}. Entry ID: {entry.id}")
            print(f"     Entidad : {ent_name}")
            print(f"     Autor   : {entry.author or 'N/A'}")
            print(f"     Fecha   : {pub_date}")
            print(f"     Título  : {entry.title or '(sin título)'}")
            print(f"     URL     : {entry.canonical_url or entry.url}")
            print(f"     Acción  : [PLANIFICADO] Triage v6 -> Deep v6 (si es relevant)")

            report["entries"].append({
                "entry_id": str(entry.id),
                "entity": ent_name,
                "author": entry.author,
                "published_at": pub_date,
                "title": entry.title,
                "status": "planned",
            })
        return report

    # 5. MODO REAL: Inicializar proveedor y pipeline
    ai_provider = provider
    if ai_provider is None:
        if not cfg.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY no está configurada. No se pueden realizar llamadas reales a Gemini."
            )
        ai_provider = GeminiAPIProvider(settings=cfg)

    pipeline = AnalysisPipelineService(provider=ai_provider)

    for idx, entry in enumerate(candidates, 1):
        meta = entry.raw_metadata or {}
        ent_name = meta.get("tracked_entity_name") or entry.author or "N/A"
        pub_date = entry.published_at.strftime("%Y-%m-%d") if entry.published_at else "N/A"

        print(f"\n--- Procesando Entry #{idx} [{entry.id}] ---")
        print(f"Entidad : {ent_name} | Autor: {entry.author or 'N/A'}")
        print(f"Fecha   : {pub_date}")
        print(f"Título  : {entry.title or '(sin título)'}")

        try:
            analysis = await pipeline.run_pipeline(
                entry_id=entry.id,
                matrix_id=matrix.id,
                triage_prompt_id=triage_prompt.id,
                deep_prompt_id=deep_prompt.id,
                db=db,
                pipeline_version="v6",
                extra_call_metadata={
                    "run_type": "linkedin_batch_analysis",
                    "orchestrator": "analyze_linkedin_batch",
                },
            )
            report["analyzed_count"] += 1

            # Clasificar y extraer auditoría
            calls = (
                db.query(AnalysisCall)
                .filter(AnalysisCall.entry_analysis_id == analysis.id)
                .order_by(AnalysisCall.created_at.asc())
                .all()
            )
            t_call = next((c for c in calls if c.stage == "triage"), None)
            d_call = next((c for c in calls if c.stage == "deep_analysis"), None)

            deep_executed = d_call is not None and d_call.status == "completed"
            if deep_executed:
                report["deep_executed_count"] += 1

            if analysis.relevance_status == "relevant":
                report["relevant_count"] += 1
            elif analysis.relevance_status == "uncertain":
                report["uncertain_count"] += 1
            elif analysis.relevance_status == "not_relevant":
                report["not_relevant_count"] += 1

            # Determinar categoría / tópico principal
            primary_topic_name = "N/A"
            if analysis.topics:
                for t_rel in analysis.topics:
                    if t_rel.is_primary and t_rel.topic:
                        primary_topic_name = t_rel.topic.name
                        break
                if primary_topic_name == "N/A" and analysis.topics[0].topic:
                    primary_topic_name = analysis.topics[0].topic.name

            # Determinar resultado de Deep Analysis
            deep_resultado: str
            if deep_executed:
                kp_count = len(analysis.key_points or [])
                summary_snip = (analysis.summary or "").strip().replace("\n", " ")
                if len(summary_snip) > 120:
                    summary_snip = summary_snip[:120] + "..."
                deep_resultado = f"Completado exitosamente ({kp_count} puntos clave). Resumen: {summary_snip}"
            else:
                meta_call = (t_call.call_metadata or {}) if t_call else {}
                skip_reason = meta_call.get("deep_skipped_reason")
                if skip_reason:
                    deep_resultado = f"No ejecutado ({skip_reason})"
                else:
                    deep_resultado = f"No ejecutado (relevancia: {analysis.relevance_status or 'desconocida'})"

            entry_report = {
                "entry_id": str(entry.id),
                "entity": ent_name,
                "author": entry.author,
                "published_at": pub_date,
                "title": entry.title,
                "triage": {
                    "relevance": analysis.relevance_status,
                    "score": analysis.relevance_score,
                    "category": primary_topic_name,
                    "reasoning": analysis.reason,
                },
                "deep_analysis": {
                    "executed": "sí" if deep_executed else "no",
                    "result": deep_resultado,
                },
            }
            report["entries"].append(entry_report)

            # Imprimir resultado por Entry según formato requerido
            print("Triage:")
            print(f"- relevance          : {analysis.relevance_status} (score: {analysis.relevance_score}/100)")
            print(f"- category           : {primary_topic_name}")
            clean_reason = (analysis.reason or "").strip().replace("\n", " ")
            print(f"- reasoning resumido : {clean_reason[:200]}...")

            print("Deep analysis:")
            print(f"- ejecutado          : {'sí' if deep_executed else 'no'}")
            print(f"- resultado          : {deep_resultado}")

        except Exception as exc:
            logger.error("Error al analizar Entry %s: %s", entry.id, exc, exc_info=True)
            report["failed_count"] += 1
            print(f"ERROR: Falló el análisis de la Entry: {exc}")

    print("\n" + "=" * 70)
    print("RESUMEN DE EJECUCIÓN BATCH LINKEDIN")
    print("=" * 70)
    print(f"Total Entries analizadas : {report['analyzed_count']}")
    print(f"  - Relevantes           : {report['relevant_count']}")
    print(f"  - Inciertas            : {report['uncertain_count']}")
    print(f"  - No relevantes        : {report['not_relevant_count']}")
    print(f"Deep Analysis ejecutados : {report['deep_executed_count']}")
    print(f"Fallos                   : {report['failed_count']}")
    print("=" * 70)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HITCHINGS — Análisis Gemini Real sobre Entries de LinkedIn (Bloque 9C/9D)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Límite máximo de Entries a procesar.",
    )
    parser.add_argument(
        "--entity",
        type=str,
        default=None,
        help="Filtrar por entidad específica (e.g. 'Hausfeld').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Modo seguro: inspecciona y lista entradas sin invocar Gemini ni persistir.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        asyncio.run(
            run_linkedin_analysis_batch(
                db=db,
                entity_name=args.entity,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
