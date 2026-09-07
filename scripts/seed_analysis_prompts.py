"""Idempotent seed script to initialize or update AI analysis prompt versions.

Creates or updates:
1. observatory_triage v1 (stage: triage)
2. observatory_deep_analysis v1 (stage: deep_analysis)

Execution:
    python -m scripts.seed_analysis_prompts
"""

import logging
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.analysis import AnalysisPromptVersion

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger(__name__)

PROMPT_DEFINITIONS = [
    {
        "code": "observatory_triage",
        "version": 1,
        "stage": "triage",
        "name": "Observatorio Triage v1",
        "description": "Fase 1: Triage rápido de relevancia regulatoria, competencia y telecomunicaciones.",
        "system_prompt": (
            "Eres un analista experto en derecho de la competencia, regulación sectorial y mercados digitales "
            "para el observatorio legal HITCHINGS.\n"
            "Tu objetivo en esta fase de TRIAGE es evaluar si una publicación capturada es relevante para el observatorio "
            "conforme a la matriz de seguimiento proporcionada.\n"
            "Debes evaluar objetivamente la relevancia asignando una puntuación de 0 a 100, clasificar los temas aplicables "
            "y proporcionar una justificación concisa."
        ),
        "user_prompt_template": (
            "Evalúa la siguiente publicación según los criterios y temas de la matriz de seguimiento:\n\n"
            "[MATRIZ DE SEGUIMIENTO]\n"
            "Nombre: {matrix_name}\n"
            "Instrucciones de relevancia: {relevance_instructions}\n"
            "Instrucciones de exclusión: {exclusion_instructions}\n"
            "Temas activos disponibles:\n{active_topics}\n\n"
            "[PUBLICACIÓN]\n"
            "Título: {entry_title}\n"
            "Fuente: {source_name}\n"
            "URL: {entry_url}\n"
            "Contenido:\n{entry_content}\n\n"
            "Devuelve tu análisis en formato JSON estricto con:\n"
            "- relevance_score (entero 0-100)\n"
            "- confidence (float 0.0-1.0)\n"
            "- topics (lista de objetos con topic_code, confidence, is_primary, rationale)\n"
            "- summary (resumen ejecutivo de 2-3 frases)\n"
            "- key_points (lista de puntos clave)\n"
            "- reason (justificación de la relevancia o descarte)"
        ),
        "response_schema_version": "v1",
        "config": {"temperature": 0.1, "max_tokens": 1024},
        "active": True,
    },
    {
        "code": "observatory_deep_analysis",
        "version": 1,
        "stage": "deep_analysis",
        "name": "Observatorio Análisis en Profundidad v1",
        "description": "Fase 2: Análisis jurídico y regulatorio detallado para publicaciones confirmadas como relevantes.",
        "system_prompt": (
            "Eres un analista jurídico senior y especialista regulatorio en derecho de la competencia de la UE y España "
            "para HITCHINGS.\n"
            "Tu objetivo es realizar un análisis exhaustivo y estructurado de una resolución, sentencia o noticia regulatoria "
            "de alto impacto.\n"
            "Debes desglosar antecedentes, fundamentos jurídicos, implicaciones doctrinales o de mercado y clasificar "
            "rigurosamente los temas principales y secundarios."
        ),
        "user_prompt_template": (
            "Realiza un análisis exhaustivo de la siguiente resolución/publicación relevante:\n\n"
            "[MATRIZ DE SEGUIMIENTO]\n"
            "Nombre: {matrix_name}\n"
            "Temas activos:\n{active_topics}\n\n"
            "[PUBLICACIÓN]\n"
            "Título: {entry_title}\n"
            "Fuente: {source_name}\n"
            "Fecha: {published_at}\n"
            "URL: {entry_url}\n"
            "Texto completo:\n{entry_content}\n\n"
            "Estructura tu respuesta JSON según el esquema v1 con resumen analítico, puntos de impacto, doctrina aplicable "
            "y topics normalizados."
        ),
        "response_schema_version": "v1",
        "config": {"temperature": 0.2, "max_tokens": 2048},
        "active": True,
    },
]


def seed_analysis_prompts(db: Optional[Session] = None) -> list[AnalysisPromptVersion]:
    """Idempotently seed or update the default analysis prompt versions."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    seeded = []
    try:
        for prompt_data in PROMPT_DEFINITIONS:
            code = prompt_data["code"]
            version = prompt_data["version"]

            existing = db.execute(
                select(AnalysisPromptVersion).where(
                    AnalysisPromptVersion.code == code,
                    AnalysisPromptVersion.version == version,
                )
            ).scalar_one_or_none()

            if existing:
                # Check immutability: verify whether material content is identical
                content_identical = (
                    existing.stage == prompt_data["stage"]
                    and existing.system_prompt == prompt_data["system_prompt"]
                    and existing.user_prompt_template == prompt_data["user_prompt_template"]
                    and existing.response_schema_version == prompt_data["response_schema_version"]
                    and (existing.config or {}) == (prompt_data["config"] or {})
                )
                if content_identical:
                    logger.info("Prompt version '%s:v%d' already exists and is unchanged (id=%s)", code, version, existing.id)
                    seeded.append(existing)
                else:
                    err_msg = (
                        f"Immutability conflict for prompt version '{code}:v{version}' (id={existing.id}). "
                        f"The existing prompt definition differs from the seed specification. "
                        f"Prompt versions are strictly immutable; create version {version + 1} (e.g. '{code}:v{version + 1}') "
                        f"instead of modifying an existing version."
                    )
                    logger.error(err_msg)
                    raise ValueError(err_msg)
            else:
                logger.info("Creating new prompt version '%s:v%d'", code, version)
                new_prompt = AnalysisPromptVersion(
                    code=code,
                    version=version,
                    stage=prompt_data["stage"],
                    name=prompt_data["name"],
                    description=prompt_data["description"],
                    system_prompt=prompt_data["system_prompt"],
                    user_prompt_template=prompt_data["user_prompt_template"],
                    response_schema_version=prompt_data["response_schema_version"],
                    config=prompt_data["config"],
                    active=prompt_data["active"],
                )
                db.add(new_prompt)
                seeded.append(new_prompt)

        db.commit()
        for p in seeded:
            db.refresh(p)
            logger.info("Prompt version ready: %s:v%d (stage=%s, active=%s)", p.code, p.version, p.stage, p.active)

        return seeded
    except Exception as exc:
        if close_db:
            db.rollback()
        logger.exception("Error during prompt version seeding: %s", exc)
        raise
    finally:
        if close_db:
            db.close()


if __name__ == "__main__":
    seed_analysis_prompts()
