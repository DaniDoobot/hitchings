"""Script de inspección y auditoría de calidad post-batch de LinkedIn.

Uso en el contenedor de producción:
    python -m scripts.inspect_linkedin_batch
"""

from sqlalchemy import select, desc
from app.db.session import SessionLocal
from app.models.entry import Entry
from app.models.analysis import AnalysisPromptVersion
from app.providers.ai.gemini_api import GeminiApiProvider


def inspect_batch():
    db = SessionLocal()
    try:
        query = (
            select(Entry)
            .where(
                (Entry.content_type == "social_post")
                | (Entry.url.ilike("%linkedin.com%"))
            )
            .order_by(desc(Entry.captured_at))
            .limit(10)
        )
        entries = db.execute(query).scalars().all()

        print("=" * 70)
        print("AUDITORIA DE CALIDAD Y PROMPTS LINKEDIN BATCH")
        print("=" * 70)
        print(f"Total de entries encontradas: {len(entries)}\n")

        if not entries:
            print("No se encontraron publicaciones de LinkedIn en la base de datos.")
            return

        for idx, entry in enumerate(entries, 1):
            meta = entry.raw_metadata or {}
            author_type = meta.get("author_type", "desconocido")
            provider = meta.get("retrieval_provider", "desconocido")
            prov_status = meta.get("provenance_status", "desconocido")
            ident_status = meta.get("identity_status", "desconocido")

            print(f"--- Publicacion #{idx} ---")
            print(f"ID Entry:          {entry.id}")
            print(f"Autor:             {entry.author} ({author_type})")
            print(f"Entidad rastreada: {meta.get('tracked_entity_name', 'N/A')}")
            print(f"URL Canonica:      {entry.canonical_url or entry.url}")
            print(f"Fecha publicacion: {entry.published_at}")
            print(f"Capturado el:      {entry.captured_at}")
            print(f"Proveedor tecnico: {provider} (almacenado solo en metadata)")
            print(f"Status Provenance: {prov_status} | Identity: {ident_status}")
            print(f"External ID:       {entry.external_id}")
            print(f"is_linkedin:       {entry.is_linkedin}")
            print(f"source_origin_cat: {entry.source_origin_category}")

            content = (entry.content or "").strip()
            print(f"Longitud texto:    {len(content)} caracteres")
            snippet = content[:250].replace("\n", " ") if content else "(sin texto)"
            print(f"Extracto:          {snippet}...")

            keywords_juridicas = [
                "antitrust", "competencia", "cartel", "danos", "damages",
                "litigacion", "litigation", "cnmc", "comision", "commission",
                "tribunal", "sentencia", "judgment", "infraccion", "multa",
                "reclamacion", "claim", "colectiva", "collective", "dma",
                "digital markets act", "abuso", "dominio", "sancion",
            ]
            keywords_corporativas = [
                "congratulations", "felicidades", "enhorabuena", "happy to announce",
                "welcome", "bienvenido", "hiring", "contratando", "anniversary",
                "aniversario", "ranking", "best lawyers", "chambers",
            ]
            content_lower = content.lower()
            encontradas_jur = [k for k in keywords_juridicas if k in content_lower]
            encontradas_corp = [k for k in keywords_corporativas if k in content_lower]

            print("Evaluacion tematica preliminar:")
            if encontradas_jur:
                print(f"  [+] Indicios juridicos / competencia detectados: {', '.join(encontradas_jur[:5])}")
            else:
                print("  [-] Sin terminos clave evidentes de competencia o litigacion en texto.")
            if encontradas_corp:
                print(f"  [!] Alerta contenido corporativo / social: {', '.join(encontradas_corp[:5])}")

            print()

        print("=" * 70)
        print("SIMULACION DE ENTRADA A PROMPT GEMINI (TRIAGE)")
        print("=" * 70)
        first_entry = entries[0]
        dummy_prompt_version = AnalysisPromptVersion(
            id=first_entry.id,
            prompt_type="triage",
            version=1,
            code="test_triage",
            name="Test Triage",
            active=True,
            system_prompt="Eres un analista juridico experto en Derecho de la Competencia.",
            user_template="",
            input_schema={},
            response_schema={},
        )
        dummy_snapshot = {
            "name": "Matriz Hitchings v1",
            "relevance_instructions": "Relevancia estricta sobre danos y carteles.",
            "exclusion_instructions": "Excluir nombramientos comerciales.",
            "topics": [{"code": "CARTEL_DAMAGES", "name": "Danos por Carteles"}],
        }
        gemini = GeminiApiProvider()
        sys_text, user_text, in_chars = gemini._build_triage_prompt(
            prompt_version=dummy_prompt_version,
            entry=first_entry,
            snapshot=dummy_snapshot,
        )

        doc_section = user_text[user_text.find("[DOCUMENTO A ANALIZAR]"):]
        print(doc_section)
        print("=" * 70)
        print("Auditoria completada.")

    finally:
        db.close()


if __name__ == "__main__":
    inspect_batch()
