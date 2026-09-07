"""Idempotent seed script for HITCHINGS Tracking Matrix v0.1.

Seeds:
1. Matrix: HITCHINGS-v0.1 (active)
2. Main topics: competition_law_general, private_enforcement (provisional=False)
3. Subtopics: provisional=True
4. Tracked entities from client documentation
5. Entity <-> Topic initial associations

Execution:
    python -m scripts.seed_tracking_v01
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.tracking import TrackingMatrix, TrackingTopic, TrackedEntity, TrackedEntityTopic

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger(__name__)


def seed() -> dict[str, int]:
    """Execute idempotent seeding of Tracking Matrix v0.1."""
    stats = {
        "matrices_created": 0,
        "matrices_existing": 0,
        "topics_created": 0,
        "topics_existing": 0,
        "entities_created": 0,
        "entities_existing": 0,
        "associations_created": 0,
        "associations_existing": 0,
    }

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # ----------------------------------------------------------------------
        # 1. TRACKING MATRIX v0.1
        # ----------------------------------------------------------------------
        matrix_code = "HITCHINGS-v0.1"
        matrix = db.execute(
            select(TrackingMatrix).where(TrackingMatrix.code == matrix_code)
        ).scalar_one_or_none()

        relevance_instructions = (
            "Considerar potencialmente relevante contenido relacionado con el Derecho de la "
            "competencia, su aplicación pública o privada, jurisprudencia, regulación, "
            "litigación, acciones de daños, actuaciones de autoridades de competencia, "
            "competencia digital y materias relacionadas con los temas activos de la matriz."
        )

        exclusion_instructions = (
            "Como criterio provisional, considerar normalmente no relevante:\n"
            "- contenido personal;\n"
            "- felicitaciones;\n"
            "- anuncios corporativos sin contenido jurídico sustantivo;\n"
            "- marketing puramente comercial;\n"
            "- ofertas de empleo;\n"
            "- publicaciones completamente ajenas al Derecho de la competencia;\n"
            "- duplicados;\n"
            "- republicaciones sin aportación sustantiva nueva."
        )

        if not matrix:
            matrix = TrackingMatrix(
                code=matrix_code,
                name="Matriz HITCHINGS v0.1",
                description=(
                    "Primera matriz provisional construida a partir de la documentación inicial "
                    "proporcionada por el cliente. Carácter provisional sujeto a sustitución futura."
                ),
                status="active",
                relevance_instructions=relevance_instructions,
                exclusion_instructions=exclusion_instructions,
                config={"version": "0.1", "provisional": True, "source": "client_initial_documentation"},
                activated_at=now,
            )
            db.add(matrix)
            db.flush()
            stats["matrices_created"] += 1
            logger.info("Created matrix '%s' (id=%s)", matrix.code, matrix.id)
        else:
            stats["matrices_existing"] += 1
            logger.info("Matrix '%s' already exists (id=%s)", matrix.code, matrix.id)

        # ----------------------------------------------------------------------
        # 2. TOPICS DEFINITION (Main and Subtopics)
        # ----------------------------------------------------------------------
        # Main topics (provisional=False)
        main_topics_data = [
            {
                "code": "competition_law_general",
                "name": "Derecho de la competencia – General",
                "description": "Área principal: Derecho de la competencia sustantivo y regulatorio general (procedente de documentación del cliente).",
                "priority": 100,
                "provisional": False,
                "keywords": ["competition law", "EU competition law", "antitrust", "derecho de la competencia"],
                "discovery_queries": ["EU competition law developments", "antitrust updates", "derecho de la competencia novedades"],
            },
            {
                "code": "private_enforcement",
                "name": "Aplicación privada",
                "description": "Área principal: Aplicación privada del derecho de la competencia y litigación de daños (procedente de documentación del cliente).",
                "priority": 100,
                "provisional": False,
                "keywords": ["private enforcement", "competition damages", "antitrust damages", "aplicación privada", "daños competencia"],
                "discovery_queries": ["competition damages litigation", "antitrust private enforcement", "acciones de daños competencia"],
            },
        ]

        # Subtopics (provisional=True)
        subtopics_data = [
            # Under competition_law_general
            {
                "parent_code": "competition_law_general",
                "code": "antitrust_general",
                "name": "Competencia / antitrust general",
                "description": "Subtema provisional: Principios generales de antitrust y regulación del mercado.",
                "priority": 50,
                "keywords": ["antitrust", "competition rules", "market competition", "conducta anticompetitiva"],
                "discovery_queries": ["general antitrust enforcement", "competition compliance"],
            },
            {
                "parent_code": "competition_law_general",
                "code": "cartels_agreements",
                "name": "Cárteles y acuerdos anticompetitivos",
                "description": "Subtema provisional: Detección y sanción de cárteles y acuerdos colusorios.",
                "priority": 60,
                "keywords": ["cartel", "cartel damages", "anticompetitive agreements", "cártel", "acuerdos anticompetitivos"],
                "discovery_queries": ["cartel sanction decisions", "leniency competition", "acuerdos colusorios"],
            },
            {
                "parent_code": "competition_law_general",
                "code": "abuse_dominance",
                "name": "Abuso de posición dominante",
                "description": "Subtema provisional: Conductas unilaterales y aplicación del Artículo 102 TFUE.",
                "priority": 60,
                "keywords": ["abuse of dominance", "dominant position", "Article 102 TFEU", "abuso de posición dominante"],
                "discovery_queries": ["Article 102 TFEU investigation", "abuse of dominant position ruling"],
            },
            {
                "parent_code": "competition_law_general",
                "code": "merger_control",
                "name": "Control de concentraciones",
                "description": "Subtema provisional: Revisión y autorización de operaciones de concentración económica.",
                "priority": 50,
                "keywords": ["merger control", "mergers", "concentrations", "control de concentraciones"],
                "discovery_queries": ["EU merger clearance", "merger prohibition decision"],
            },
            {
                "parent_code": "competition_law_general",
                "code": "digital_competition_dma",
                "name": "Competencia digital / Digital Markets Act",
                "description": "Subtema provisional: Mercados digitales, plataformas gatekeeper y normativa DMA.",
                "priority": 70,
                "keywords": ["Digital Markets Act", "DMA", "gatekeepers", "mercados digitales", "digital platforms antitrust"],
                "discovery_queries": ["Digital Markets Act compliance", "gatekeeper designation DMA"],
            },
            {
                "parent_code": "competition_law_general",
                "code": "competition_policy",
                "name": "Política y regulación de competencia",
                "description": "Subtema provisional: Directrices, orientaciones de política y reformas legislativas.",
                "priority": 40,
                "keywords": ["competition policy", "regulatory guidelines", "política de competencia"],
                "discovery_queries": ["European Commission competition policy", "guidelines antitrust"],
            },
            {
                "parent_code": "competition_law_general",
                "code": "competition_case_law",
                "name": "Jurisprudencia de competencia",
                "description": "Subtema provisional: Sentencias y doctrina de tribunales de competencia (TJUE, TG, etc.).",
                "priority": 60,
                "keywords": ["competition case law", "CJEU competition", "General Court judgment", "jurisprudencia competencia"],
                "discovery_queries": ["CJEU antitrust judgment", "General Court competition appeal"],
            },
            # Under private_enforcement
            {
                "parent_code": "private_enforcement",
                "code": "private_enforcement_general",
                "name": "Aplicación privada general",
                "description": "Subtema provisional: Novedades generales en aplicación civil de normas de competencia.",
                "priority": 50,
                "keywords": ["private enforcement", "antitrust litigation", "aplicación privada"],
                "discovery_queries": ["private enforcement EU directive", "competition litigation developments"],
            },
            {
                "parent_code": "private_enforcement",
                "code": "damages_actions",
                "name": "Acciones de daños",
                "description": "Subtema provisional: Reclamaciones de indemnización de daños y perjuicios por ilícitos anticompetitivos.",
                "priority": 70,
                "keywords": ["damages actions", "competition damages claims", "reclamaciones de daños"],
                "discovery_queries": ["antitrust damages claim", "reclamación daños cártel"],
            },
            {
                "parent_code": "private_enforcement",
                "code": "collective_actions",
                "name": "Acciones colectivas",
                "description": "Subtema provisional: Mecanismos de tutela colectiva y class actions en competencia.",
                "priority": 65,
                "keywords": ["collective actions", "collective redress", "class actions", "acciones colectivas"],
                "discovery_queries": ["opt-out collective action antitrust", "collective redress competition"],
            },
            {
                "parent_code": "private_enforcement",
                "code": "competition_litigation",
                "name": "Litigación de competencia",
                "description": "Subtema provisional: Práctica procesal y contenciosa en los tribunales ordinarios.",
                "priority": 60,
                "keywords": ["competition litigation", "antitrust litigation", "litigación competencia"],
                "discovery_queries": ["antitrust court proceedings", "competition trial hearings"],
            },
            {
                "parent_code": "private_enforcement",
                "code": "litigation_funding",
                "name": "Financiación de litigios",
                "description": "Subtema provisional: Fondos de litigación, acuerdos de honorarios y third-party funding.",
                "priority": 55,
                "keywords": ["litigation funding", "third party funding", "financiación litigios", "litigation investment"],
                "discovery_queries": ["third party litigation funding antitrust", "litigation finance competition"],
            },
            {
                "parent_code": "private_enforcement",
                "code": "evidence_disclosure",
                "name": "Prueba / acceso a documentación / disclosure",
                "description": "Subtema provisional: Solicitudes de exhibición de pruebas, secreto profesional y acceso a expedientes.",
                "priority": 60,
                "keywords": ["disclosure", "access to evidence", "evidence competition damages", "exhibición de documentos"],
                "discovery_queries": ["disclosure order antitrust", "access to evidence directive competition"],
            },
            {
                "parent_code": "private_enforcement",
                "code": "damages_quantification",
                "name": "Cuantificación de daños",
                "description": "Subtema provisional: Informes periciales, sobreprecio (overcharge), repercusión (pass-on) y métodos econométricos.",
                "priority": 65,
                "keywords": ["damages quantification", "overcharge", "pass-on", "economic evidence", "cuantificación pericial"],
                "discovery_queries": ["overcharge quantification antitrust", "pass-on defense damages"],
            },
            {
                "parent_code": "private_enforcement",
                "code": "jurisdiction_procedure",
                "name": "Jurisdicción y cuestiones procesales",
                "description": "Subtema provisional: Competencia territorial, plazos de prescripción y ley aplicable.",
                "priority": 55,
                "keywords": ["jurisdiction competition damages", "limitation period", "applicable law", "procedural rules", "prescripción"],
                "discovery_queries": ["limitation period competition damages", "jurisdiction antitrust torts"],
            },
            {
                "parent_code": "private_enforcement",
                "code": "private_enforcement_case_law",
                "name": "Jurisprudencia sobre aplicación privada",
                "description": "Subtema provisional: Pronunciamientos judiciales clave en materia de indemnizaciones (e.g. Cártel de Camiones).",
                "priority": 70,
                "keywords": ["private enforcement case law", "trucks cartel judgment", "preliminary ruling damages"],
                "discovery_queries": ["Supreme Court cartel damages ruling", "CJEU private enforcement ruling"],
            },
        ]

        topics_by_code: dict[str, TrackingTopic] = {}

        # First pass: seed main topics
        for item in main_topics_data:
            code = item["code"]
            existing_topic = db.execute(
                select(TrackingTopic).where(
                    TrackingTopic.matrix_id == matrix.id,
                    TrackingTopic.code == code,
                )
            ).scalar_one_or_none()

            if not existing_topic:
                topic = TrackingTopic(
                    matrix_id=matrix.id,
                    parent_id=None,
                    code=code,
                    name=item["name"],
                    description=item["description"],
                    priority=item["priority"],
                    keywords=item["keywords"],
                    discovery_queries=item["discovery_queries"],
                    active=True,
                    provisional=item["provisional"],
                )
                db.add(topic)
                db.flush()
                topics_by_code[code] = topic
                stats["topics_created"] += 1
            else:
                topics_by_code[code] = existing_topic
                stats["topics_existing"] += 1

        # Second pass: seed subtopics linked to parent
        for item in subtopics_data:
            code = item["code"]
            parent = topics_by_code[item["parent_code"]]
            existing_subtopic = db.execute(
                select(TrackingTopic).where(
                    TrackingTopic.matrix_id == matrix.id,
                    TrackingTopic.code == code,
                )
            ).scalar_one_or_none()

            if not existing_subtopic:
                subtopic = TrackingTopic(
                    matrix_id=matrix.id,
                    parent_id=parent.id,
                    code=code,
                    name=item["name"],
                    description=item["description"],
                    priority=item["priority"],
                    keywords=item["keywords"],
                    discovery_queries=item["discovery_queries"],
                    active=True,
                    provisional=True,
                )
                db.add(subtopic)
                db.flush()
                topics_by_code[code] = subtopic
                stats["topics_created"] += 1
            else:
                topics_by_code[code] = existing_subtopic
                stats["topics_existing"] += 1

        # ----------------------------------------------------------------------
        # 3. TRACKED ENTITIES FROM CLIENT DOCUMENTATION
        # ----------------------------------------------------------------------
        entities_data = [
            # GRUPO: Derecho de la competencia – General (linked to competition_law_general)
            {"display_name": "Pinar Akman", "entity_type": "person", "topic_code": "competition_law_general", "section": "Derecho de la competencia – General"},
            {"display_name": "Assimakis Komninos", "entity_type": "person", "topic_code": "competition_law_general", "section": "Derecho de la competencia – General"},
            {"display_name": "Pablo Ibáñez Colomo", "entity_type": "person", "topic_code": "competition_law_general", "section": "Derecho de la competencia – General"},
            {"display_name": "Antonio Robles Martín-Laborda", "entity_type": "person", "topic_code": "competition_law_general", "section": "Derecho de la competencia – General"},
            {"display_name": "Alba Ribera Martínez", "entity_type": "person", "topic_code": "competition_law_general", "section": "Derecho de la competencia – General"},
            {"display_name": "Pierre Bichet", "entity_type": "person", "topic_code": "competition_law_general", "section": "Derecho de la competencia – General"},
            {"display_name": "Christian Bergqvist", "entity_type": "person", "topic_code": "competition_law_general", "section": "Derecho de la competencia – General"},
            {"display_name": "Emilija Berzanskaite", "entity_type": "person", "topic_code": "competition_law_general", "section": "Derecho de la competencia – General"},
            {"display_name": "Damien Geradin", "entity_type": "person", "topic_code": "competition_law_general", "section": "Derecho de la competencia – General"},

            # GRUPO: Aplicación privada (linked to private_enforcement)
            {"display_name": "Francisco Marcos", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Jaime Concheiro", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Hausfeld", "entity_type": "organization", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "ESKARIAM", "entity_type": "organization", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Thomas Hoppner, Geradin", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Geradin Partners Monthly EU litigation briefing", "entity_type": "publication", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Adoni Llosa", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Redi Abogados", "entity_type": "organization", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Eduardo Pastor", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Julia Suderow", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "ALI Antitrust Litigation Investment", "entity_type": "organization", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Fernando Diez Estella", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Pedro Suarez, Regula", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Javier Perez, Regula", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Joost Fanoy", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Stefan Tuinenga", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Thomas Funke", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "James Hain-Cole", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Miguel Sousa Ferro", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},
            {"display_name": "Lena Hornkohl", "entity_type": "person", "topic_code": "private_enforcement", "section": "Aplicación privada"},

            # FUENTES INSTITUCIONALES (linked to competition_law_general)
            {"display_name": "Competition Appeal Tribunal", "entity_type": "institution", "topic_code": "competition_law_general", "section": "Fuentes institucionales"},
            {"display_name": "Comisión Nacional de los Mercados y la Competencia", "entity_type": "institution", "topic_code": "competition_law_general", "section": "Fuentes institucionales"},
            {"display_name": "European Commission", "entity_type": "institution", "topic_code": "competition_law_general", "section": "Fuentes institucionales"},
            {"display_name": "European Commission / Digital Markets Act", "entity_type": "institution", "topic_code": "competition_law_general", "section": "Fuentes institucionales"},
            {"display_name": "El Tribunal de Justicia de la Unión Europea", "entity_type": "institution", "topic_code": "competition_law_general", "section": "Fuentes institucionales"},

            # BLOGS / PUBLICACIONES (linked to competition_law_general)
            {"display_name": "Global Competition Review", "entity_type": "publication", "topic_code": "competition_law_general", "section": "Blogs / Publicaciones"},
            {"display_name": "Concurrences", "entity_type": "publication", "topic_code": "competition_law_general", "section": "Blogs / Publicaciones"},
            {"display_name": "OECD Competition Law and Policy", "entity_type": "publication", "topic_code": "competition_law_general", "section": "Blogs / Publicaciones"},
            {"display_name": "EU Competition Policy", "entity_type": "publication", "topic_code": "competition_law_general", "section": "Blogs / Publicaciones"},
        ]

        for item in entities_data:
            name = item["display_name"]
            existing_entity = db.execute(
                select(TrackedEntity).where(TrackedEntity.display_name == name)
            ).scalar_one_or_none()

            if not existing_entity:
                entity = TrackedEntity(
                    display_name=name,
                    entity_type=item["entity_type"],
                    active=True,
                    metadata_={
                        "origin": "client_document",
                        "client_section": item["section"],
                        "provisional": False,
                    },
                )
                db.add(entity)
                db.flush()
                stats["entities_created"] += 1
            else:
                entity = existing_entity
                stats["entities_existing"] += 1

            # Associate with topic
            topic = topics_by_code[item["topic_code"]]
            existing_assoc = db.execute(
                select(TrackedEntityTopic).where(
                    TrackedEntityTopic.tracked_entity_id == entity.id,
                    TrackedEntityTopic.tracking_topic_id == topic.id,
                )
            ).scalar_one_or_none()

            if not existing_assoc:
                assoc = TrackedEntityTopic(
                    tracked_entity_id=entity.id,
                    tracking_topic_id=topic.id,
                    is_primary=True,
                    notes=f"Asignación inicial v0.1 por sección '{item['section']}' de documentación de cliente.",
                )
                db.add(assoc)
                stats["associations_created"] += 1
            else:
                stats["associations_existing"] += 1

        db.commit()
        logger.info("Seeding completed successfully: %s", stats)
        return stats

    except Exception as exc:
        db.rollback()
        logger.error("Seeding failed: %s", exc, exc_info=True)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    result = seed()
    print("\n--- RESUMEN DE EJECUCIÓN SEED v0.1 ---")
    for k, v in result.items():
        print(f"  {k}: {v}")
