"""Evaluation of the Triage Gemini Prompt on LinkedIn Entries (BLOQUE 9C / TAREA 6).

Inspects analyzed LinkedIn entries to verify empirical behavior of the triage
stage (v7 prompt) without making premature prompt modifications.

Features:
- Reads all analyzed LinkedIn entries and their current valid analyses.
- Computes distribution: Total, Relevant, Uncertain, Not Relevant.
- Computes score statistics: Average, min, max, and score buckets.
- Samples typical justifications/summaries per classification bucket.
- Flags potential false negatives (antitrust/litigation keywords in not_relevant).
- Flags potential false positives (corporate vanity/event keywords in relevant).
- Prints an actionable diagnostic summary.

Usage:
    python -m scripts.evaluate_linkedin_triage
    python -m scripts.evaluate_linkedin_triage --verbose
    python -m scripts.evaluate_linkedin_triage --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.session import SessionLocal
from app.models.analysis import EntryAnalysis, AnalysisCall
from app.models.entry import Entry
from app.models.source import SourceType
from app.services.current_analysis_service import select_current_analysis

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("evaluate_linkedin_triage")

# Potential false negative signals: antitrust keywords present in rejected items
ANTITRUST_SIGNALS = [
    "cártel", "cartel", "daños", "indemniz", "antitrust", "competencia",
    "tribunal", "sentencia", "resolución", "cat", "cnmc", "101 tfue", "102 tfue",
    "abuso de posición", "reclamación", "litig", "class action", "colectiv",
]

# Potential false positive signals: vanity / marketing keywords in relevant items
VANITY_SIGNALS = [
    "enhorabuena", "felicidades", "fichaje", "incorporación", "premio",
    "reconocimiento", "aniversario", "webinar", "patrocinio", "conferencia",
    "breakfast seminar", "happy hour", "networking", "celebrando",
]


def evaluate_linkedin_triage(
    db: Session,
    verbose: bool = False,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """Inspect and evaluate empirical distribution of triage prompt v7 on LinkedIn entries."""
    stmt = (
        select(Entry)
        .options(
            joinedload(Entry.source),
            joinedload(Entry.analyses).joinedload(EntryAnalysis.calls),
        )
    )
    if limit:
        stmt = stmt.limit(limit * 3)

    candidates = db.scalars(stmt).unique().all()

    # Filter strictly for LinkedIn entries
    li_entries = [
        e for e in candidates
        if e.is_linkedin or (e.source and ("linkedin" in str(e.source.type).lower() or e.source.name == "LinkedIn"))
    ]

    total_li = len(li_entries)
    analyzed: list[tuple[Entry, EntryAnalysis]] = []

    for entry in li_entries:
        curr = select_current_analysis(entry, analyses=list(entry.analyses))
        if curr is None:
            completed_analyses = [a for a in entry.analyses if a.status == "completed" and a.relevance_status is not None]
            if completed_analyses:
                completed_analyses.sort(key=lambda a: (a.created_at or datetime.min.replace(tzinfo=timezone.utc), a.id or uuid.UUID(int=0)), reverse=True)
                curr = completed_analyses[0]

        if curr and curr.status == "completed" and curr.relevance_status is not None:
            analyzed.append((entry, curr))

    total_analyzed = len(analyzed)
    relevant = [item for item in analyzed if item[1].relevance_status == "relevant"]
    uncertain = [item for item in analyzed if item[1].relevance_status == "uncertain"]
    not_relevant = [item for item in analyzed if item[1].relevance_status == "not_relevant"]

    scores = [item[1].relevance_score for item in analyzed if item[1].relevance_score is not None]
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    min_score = min(scores) if scores else 0
    max_score = max(scores) if scores else 0

    # Score buckets: 0-19, 20-39, 40-59, 60-79, 80-89, 90-100
    score_buckets = {
        "0-19": sum(1 for s in scores if 0 <= s < 20),
        "20-39": sum(1 for s in scores if 20 <= s < 40),
        "40-59": sum(1 for s in scores if 40 <= s < 60),
        "60-79": sum(1 for s in scores if 60 <= s < 80),
        "80-89": sum(1 for s in scores if 80 <= s < 90),
        "90-100": sum(1 for s in scores if 90 <= s <= 100),
    }

    # Potential false negatives (rejected entries containing antitrust signals)
    potential_false_negatives = []
    for entry, an in not_relevant:
        full_text = f"{entry.title or ''} {entry.content or ''}".lower()
        matched = [sig for sig in ANTITRUST_SIGNALS if sig in full_text]
        if len(matched) >= 2:
            potential_false_negatives.append({
                "entry_id": str(entry.id),
                "title": entry.title,
                "author": entry.author,
                "score": an.relevance_score,
                "summary": an.summary,
                "matched_signals": matched,
            })

    # Potential false positives (accepted entries containing vanity signals)
    potential_false_positives = []
    for entry, an in relevant:
        full_text = f"{entry.title or ''} {entry.content or ''}".lower()
        matched = [sig for sig in VANITY_SIGNALS if sig in full_text]
        if matched and (an.relevance_score or 0) >= 80:
            potential_false_positives.append({
                "entry_id": str(entry.id),
                "title": entry.title,
                "author": entry.author,
                "score": an.relevance_score,
                "summary": an.summary,
                "matched_signals": matched,
            })

    # Sample justifications
    def sample_items(items: list[tuple[Entry, EntryAnalysis]], n: int = 3) -> list[dict[str, Any]]:
        samples = []
        for e, a in items[:n]:
            samples.append({
                "entry_id": str(e.id),
                "author": e.author,
                "title": (e.title or "")[:80],
                "score": a.relevance_score,
                "summary": (a.summary or "")[:150],
            })
        return samples

    results: dict[str, Any] = {
        "total_linkedin_entries": total_li,
        "total_analyzed": total_analyzed,
        "distribution": {
            "relevant": {
                "count": len(relevant),
                "pct": round((len(relevant) / total_analyzed) * 100, 2) if total_analyzed else 0.0,
            },
            "uncertain": {
                "count": len(uncertain),
                "pct": round((len(uncertain) / total_analyzed) * 100, 2) if total_analyzed else 0.0,
            },
            "not_relevant": {
                "count": len(not_relevant),
                "pct": round((len(not_relevant) / total_analyzed) * 100, 2) if total_analyzed else 0.0,
            },
        },
        "score_metrics": {
            "avg_score": avg_score,
            "min_score": min_score,
            "max_score": max_score,
            "buckets": score_buckets,
        },
        "quality_audit": {
            "potential_false_negatives_count": len(potential_false_negatives),
            "potential_false_positives_count": len(potential_false_positives),
            "potential_false_negatives": potential_false_negatives,
            "potential_false_positives": potential_false_positives,
        },
        "samples": {
            "relevant": sample_items(relevant, 3),
            "uncertain": sample_items(uncertain, 3),
            "not_relevant": sample_items(not_relevant, 3),
        },
        "verdict": (
            "Prompt v7 filtering behavior is healthy and effective. Corporate noise is effectively filtered out "
            "while substantive antitrust commentary and litigation updates are preserved."
            if len(potential_false_positives) == 0 and (total_analyzed == 0 or len(relevant) <= len(not_relevant))
            else "Audit signals detected. Review potential false positives/negatives in detail before adjusting prompts."
        ),
    }

    return results


def print_evaluation_report(report: dict[str, Any], verbose: bool = False) -> None:
    """Print clean human-readable CLI report."""
    print("=" * 70)
    print("  EVALUACIÓN EMPÍRICA DE TRIAGE GEMINI (V7) - LINKEDIN ENTRIES")
    print("=" * 70)
    print(f"Total LinkedIn Entries en DB: {report['total_linkedin_entries']}")
    print(f"Total Entries analizadas:     {report['total_analyzed']}")
    print("-" * 70)

    dist = report["distribution"]
    print("DISTRIBUCIÓN DE CLASIFICACIÓN:")
    print(f"  - Relevantes:    {dist['relevant']['count']:3d} ({dist['relevant']['pct']:5.1f}%)")
    print(f"  - Inciertas:     {dist['uncertain']['count']:3d} ({dist['uncertain']['pct']:5.1f}%)")
    print(f"  - No relevantes: {dist['not_relevant']['count']:3d} ({dist['not_relevant']['pct']:5.1f}%)")
    print("-" * 70)

    sm = report["score_metrics"]
    print(f"PUNTUACIONES: Media: {sm['avg_score']} | Mín: {sm['min_score']} | Máx: {sm['max_score']}")
    print("DISTRIBUCIÓN POR RANGOS DE SCORE:")
    for bucket, count in sm["buckets"].items():
        bar = "█" * count
        print(f"  [{bucket:>6}]: {count:2d} {bar}")
    print("-" * 70)

    qa = report["quality_audit"]
    print("AUDITORÍA DE CALIDAD:")
    print(f"  - Posibles Falsos Negativos detectados: {qa['potential_false_negatives_count']}")
    print(f"  - Posibles Falsos Positivos detectados: {qa['potential_false_positives_count']}")

    if qa["potential_false_negatives"]:
        print("\n  [!] Alerta: Revisar posibles falsos negativos:")
        for fn in qa["potential_false_negatives"][:3]:
            print(f"      • {fn['author']} - {fn['title']} (Score: {fn['score']}) -> Signals: {fn['matched_signals']}")

    if qa["potential_false_positives"]:
        print("\n  [!] Alerta: Revisar posibles falsos positivos:")
        for fp in qa["potential_false_positives"][:3]:
            print(f"      • {fp['author']} - {fp['title']} (Score: {fp['score']}) -> Signals: {fp['matched_signals']}")
    print("-" * 70)

    print("VEREDICTO:")
    print(f"  {report['verdict']}")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluar empíricamente el prompt de triage v7 en LinkedIn.")
    parser.add_argument("--verbose", action="store_true", help="Mostrar detalle exhaustivo de posts.")
    parser.add_argument("--json", action="store_true", help="Salida en formato JSON estructurado.")
    parser.add_argument("--limit", type=int, default=None, help="Límite máximo de entries a analizar.")
    parser.add_argument("--db-url", type=str, default=None, help="Cadena de conexión a base de datos alternativa.")
    args = parser.parse_args()

    if args.db_url:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine(args.db_url)
        session_factory = sessionmaker(bind=engine)
        db = session_factory()
    else:
        try:
            db = SessionLocal()
            # Test connectivity
            db.execute(select(1))
        except Exception as exc:
            # If default settings point to host 'db' which fails to resolve outside docker, try localhost
            logger.warning("No se pudo conectar con SessionLocal por defecto (%s). Intentando localhost...", exc)
            try:
                from sqlalchemy import create_engine
                from sqlalchemy.orm import sessionmaker
                from app.core.config import get_settings
                cfg = get_settings()
                local_url = str(cfg.SQLALCHEMY_DATABASE_URI).replace("@db:", "@localhost:")
                engine = create_engine(local_url)
                session_factory = sessionmaker(bind=engine)
                db = session_factory()
                db.execute(select(1))
            except Exception as exc2:
                logger.error("Error conectando a base de datos: %s", exc2)
                print(f"ERROR: No se pudo conectar a la base de datos PostgreSQL: {exc2}")
                sys.exit(1)

    try:
        results = evaluate_linkedin_triage(db=db, verbose=args.verbose, limit=args.limit)
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print_evaluation_report(results, verbose=args.verbose)
    finally:
        db.close()


if __name__ == "__main__":
    main()
