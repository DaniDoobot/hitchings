"""CLI script for Google News discovery feed ingestion (Bloque 9A).

Usage:
    # Dry-run (default: 0 external calls, 0 DB writes):
    python -m scripts.ingest_google_news

    # Real run (requires explicit confirmation flag):
    python -m scripts.ingest_google_news --confirm-real-calls [--max-queries 5] [--max-items-per-query 5] [--max-new-entries 20]
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.providers.base import ProviderDisabledError
from app.services.google_news_ingestion_service import GoogleNewsIngestionService
from app.services.google_news_query_planner import GoogleNewsQueryPlanner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingesta de descubrimiento de noticias mediante Google News (Bloque 9A)."
    )
    parser.add_argument(
        "--confirm-real-calls",
        action="store_true",
        default=False,
        help="Flag explícito requerido para realizar peticiones HTTP reales y persistir en BD. "
             "Por defecto es DRY-RUN (0 llamadas externas, 0 escrituras).",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=None,
        help="Límite de consultas a ejecutar en este run (por defecto toma GOOGLE_NEWS_MAX_QUERIES_PER_RUN).",
    )
    parser.add_argument(
        "--max-items-per-query",
        type=int,
        default=None,
        help="Límite de items a procesar por cada consulta (por defecto toma GOOGLE_NEWS_MAX_ITEMS_PER_QUERY).",
    )
    parser.add_argument(
        "--max-new-entries",
        type=int,
        default=None,
        help="Límite máximo de nuevas Entries a insertar en este run (por defecto toma GOOGLE_NEWS_MAX_NEW_ENTRIES_PER_RUN).",
    )
    parser.add_argument(
        "--languages",
        type=str,
        default=None,
        help="Idiomas separados por coma (e.g. 'es,en'). Por defecto usa settings.",
    )

    args = parser.parse_args()
    settings = get_settings()

    db = SessionLocal()
    try:
        service = GoogleNewsIngestionService()

        # Parse languages if specified
        languages = (
            [l.strip().lower() for l in args.languages.split(",") if l.strip()]
            if args.languages
            else settings.GOOGLE_NEWS_LANGUAGES
        )

        planner = GoogleNewsQueryPlanner(
            languages=languages,
            region=settings.GOOGLE_NEWS_REGION,
            max_queries=args.max_queries or settings.GOOGLE_NEWS_MAX_QUERIES_PER_RUN,
        )

        planned_queries = planner.plan_queries(db)

        print("\n=======================================================")
        print("  HITCHINGS — Google News Discovery Ingestion (Bloque 9A)")
        print("=======================================================")
        print(f"Modo: {'REAL (Llamadas de red y escritura en BD)' if args.confirm_real_calls else 'DRY RUN (0 llamadas, 0 escrituras)'}")
        print(f"Idiomas configurados:       {languages}")
        print(f"Región configurada:        {settings.GOOGLE_NEWS_REGION}")
        print(f"Límite max_queries:         {args.max_queries or settings.GOOGLE_NEWS_MAX_QUERIES_PER_RUN}")
        print(f"Límite max_items_per_query: {args.max_items_per_query or settings.GOOGLE_NEWS_MAX_ITEMS_PER_QUERY}")
        print(f"Límite max_new_entries:     {args.max_new_entries or settings.GOOGLE_NEWS_MAX_NEW_ENTRIES_PER_RUN}")
        print(f"Consultas planificadas:     {len(planned_queries)}")
        print("-------------------------------------------------------")
        print("Muestra de consultas planificadas (primeras 10):")
        for q in planned_queries[:10]:
            ent = f" [{q.entity_name}]" if q.entity_name else ""
            print(f"  [{q.query_id}] (P={q.priority}, {q.language}/{q.region}): {q.query_text}{ent}")
        print("-------------------------------------------------------")

        if not args.confirm_real_calls:
            print("\n[DRY RUN FINALIZADO] No se realizaron llamadas HTTP ni escrituras en base de datos.")
            print("Para ejecutar llamadas reales y persistir noticias, añada: --confirm-real-calls\n")
            return 0

        # Execute real ingestion
        report = service.execute_ingestion(
            db=db,
            planner=planner,
            max_queries=args.max_queries,
            max_items_per_query=args.max_items_per_query,
            max_new_entries=args.max_new_entries,
            confirm_real_calls=True,
        )

        print("\n=== REPORTE DE EJECUCIÓN REAL ===")
        print(f"  Run ID:              {report.run_id}")
        print(f"  Source ID:           {report.source_id}")
        print(f"  Estado:              {report.status}")
        print(f"  Queries planificadas:{report.queries_planned}")
        print(f"  Queries ejecutadas:  {report.queries_executed}")
        print(f"  Queries fallidas:    {report.failed_queries}")
        print(f"  Items observados:    {report.items_seen}")
        print(f"  Nuevas Entries:      {report.entries_created}")
        print(f"  Duplicados omitidos: {report.duplicates_count}")
        print(f"  Publishers únicos:   {len(report.publishers_found)}")
        print(f"  Rango de fechas:     {report.oldest_published_at} -> {report.latest_published_at}")
        print(f"  Llamadas Gemini:     0 (Bloque sin IA)")
        print(f"  Coste estimado IA:   $0.00")

        if report.sample_created_entries:
            print("\nMuestra de nuevas entradas creadas (hasta 10):")
            for idx, entry in enumerate(report.sample_created_entries, 1):
                print(f"  {idx}. [{entry.get('publisher') or 'Desconocido'}] {entry.get('title')}")
                print(f"     Fecha: {entry.get('published_at')} | Query: {entry.get('discovery_query')}")
                print(f"     URL:   {entry.get('url')}")

        print("\nEjecución finalizada con éxito.\n")
        return 0

    except ProviderDisabledError as pde:
        print(f"\n[ERROR - PROVEEDOR DESACTIVADO] {pde}", file=sys.stderr)
        return 1
    except Exception as exc:
        logger.exception("Error durante la ejecución de Google News ingestion: %s", exc)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
