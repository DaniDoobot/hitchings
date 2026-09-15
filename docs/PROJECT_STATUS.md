# HITCHINGS — Current Project Status

Fecha:
2026-09-15

## Estado Observatorio

ADLC:
CLOSED
49/49 entries completed
0 pending
0 failed-only
0 new candidates

Fuentes:
17/17 integradas (incluidas FTC y DOJ Antitrust Division).

## Scheduler Semanal (Weekly Refresh)

HARDENED & AUDITED
- Daemon: `scheduler` en `docker-compose.prod.yml` (`python -m app.scheduler`).
- Cadencia: Lunes 06:00 `Europe/Madrid`.
- Cobertura: 17/17 fuentes activas procesadas con aislamiento estricto de fallos.
- Lookback: 8 días fijados por defecto y propagados a nivel de ejecución (`ingestion_service.ingest_source` y `direct_web_service.execute_ingestion`).
- Inmutabilidad: `Source.config` no se modifica.
- Gating de suficiencia: FULL analizada; PARTIAL e INSUFFICIENT omitidas de análisis automático.

## FTC

CLOSED
- 16 entries en 90d
- 14 completed
- 1 PARTIAL no analizada
- 1 INSUFFICIENT no analizada
- 0 pending
- 0 failed-only
- 0 new candidates

Source ID producción:
e9671e46-94a5-49eb-ba0a-db96d35ffc24

## DOJ Antitrust Division (Source 17/17)

CLOSED & IMPLEMENTED
- Extractor nativo: `app/providers/extractors/doj_antitrust.py`
- RSS feed: `https://www.justice.gov/news/rss?field_component=376&type=press_release`
- Seeder idempotente: `scripts/seed_source_doj_antitrust.py`
- Scope guard fail-closed: exclusión automática de causas USAO/no-antitrust.
- Identidad canónica: `doj_atr:node:{node_id}` o `doj_atr:{year}:{month}:{slug}`

## LinkedIn Discovery Hardening (Bloque 9C / Provenance Closure)

HARDENED & DEDUPLICATED (INACTIVE / ZERO CALLS)
- Discovery status: `LINKEDIN_DISCOVERY_ENABLED=false` (sin llamadas reales ni consumo de créditos).
- Semántica de Identidad vs Procedencia:
  - `identity_status`: `"activity_id"` (ID numérico extraído canónicamente) o `"canonical_url_fallback"` (hash determinista sha256 sobre URL canónica).
  - `provenance_status`: `"verified"` o `"unverified"`. No se marca `provenance_status="verified"` únicamente por tener `activity_id`.
  - Requisitos de `provenance_status="verified"`:
    1. `TrackedEntity` inequívoca en base de datos.
    2. `author_name` fiable (no vacío, no genérico/placeholder).
    3. `author_profile_url` coherente con la URL configurada para esa entidad en `TrackedEntity.metadata_["linkedin_url"]` (normalizando subdominios regionales, query params y trailing slashes).
  - Gating de autoría fail-closed: posts con autores no verificables o URLs incoherentes son descartados sin persistir.
- Gestión de proveedor técnico (Legacy Provider):
  - Nuevas escrituras: persisten ÚNICAMENTE `raw_metadata["retrieval_provider"]` (Bright Data / Apify). Se prohíbe escribir la clave duplicada `raw_metadata["provider"]`.
  - Compatibilidad de lectura: `LinkedInIngestionService.get_retrieval_provider(entry)` resuelve con fallback retrocompatible `meta.get("retrieval_provider") or meta.get("provider")`.
- Perfiles de Entidades Piloto Verificados (5):
  - Organizaciones (4): Hausfeld, ESKARIAM, CNMC, European Commission.
  - Personas (1): Miguel Sousa Ferro (`https://www.linkedin.com/in/miguel-sousa-ferro-b7551666`).
- Perfiles Personales Pendientes (23):
  - Por homónimos, falta de URL indexada en abierto o ausencia de enlace directo verificado sin login:
    Pablo Ibáñez Colomo, Francisco Marcos, Pinar Akman, Damien Geradin, Thomas Höppner, Assimakis Komninos, Julia Suderow, Fernando Díez Estella, Antonio Robles Martín-Laborda, Alba Ribera Martínez, Pierre Bichet, Christian Bergqvist, Emilija Berzanskaite, Jaime Concheiro, Adoni Llosa, Eduardo Pastor, Pedro Suárez, Javier Pérez, Joost Fanoy, Stefan Tuinenga, Thomas Funke, James Hain-Cole, Lena Hornkohl.
- Deduplicación cross-provider: orden estricto `external_id -> canonical_url -> fallback`.
- UI portal (`ObservatoryPage` y `EntryDetailPage`):
  - Cabecera: `LinkedIn · {author_name}` acompañado de icono contextual (`Building2` para organización, `User` para persona).
  - Enlace seguro directo al post original en LinkedIn.
  - Ni Bright Data ni Apify se muestran en ningún lugar de la interfaz.
- Validación Local Mock de Ciclo Completo (Hausfeld):
  - Validado de extremo a extremo sin llamadas HTTP externas: TrackedEntity -> Planner -> Mock Provider -> Normalizer (`external_id=urn:li:activity:{id}`) -> Provenance (`identity_status=activity_id`, `provenance_status=verified`) -> Metadata (sin legacy `provider`, solo `retrieval_provider`) -> Entry DB -> Dedupe cruzado contra simulación Apify -> API & UI contract (`LinkedIn · Hausfeld`).
  - Pruebas automatizadas: 25/25 en pytest backend, 47/47 en vitest frontend.

- Sonda Controlada Bright Data (Hausfeld) preparada:
  - Modo dry-run seguro: `python -m scripts.ingest_linkedin --probe` (0 llamadas, 0 escrituras, validación de las 7 guardas).
  - Modo ejecución real: `python -m scripts.ingest_linkedin --probe --confirm-real-calls` (llamada única a Bright Data, max_entities=1, max_posts=1, sin fallback Apify).
  - Guardas de seguridad pre-ejecución:
    1. `BRIGHTDATA_API_TOKEN` presente en entorno (verificación sin mostrar el valor).
    2. `TrackingMatrix` activa en base de datos.
    3. `TrackedEntity` inequívoca para Hausfeld.
    4. URL configurada exactamente `https://www.linkedin.com/company/hausfeld`.
    5. Límites estrictos: `max_entities=1`, `max_posts=1` (consumo rígidamente acotado).
    6. Fallback Apify desactivado (`disable_fallback=True`).
    7. Procedencia fail-closed activa.
    8. Deduplicación activa.
  - Reporte post-ejecución desglosado: HTTP/provider status, registros devueltos, author_name, author_profile_url, linkedin_post_url, activity/post ID, published_at, identity_status, provenance_status, retrieval_provider, acción (CREATED / DUPLICATE), consumo/coste.
  - Pruebas automatizadas: 31/31 en pytest backend, 47/47 en vitest frontend.

## Próximo paso exacto

1. Ejecutar sonda real de Bright Data para Hausfeld cuando el usuario configure `BRIGHTDATA_API_TOKEN` en el entorno y proporcione confirmación explícita:
   `python -m scripts.ingest_linkedin --probe --confirm-real-calls`

## Invariantes

Gemini:
Developer API / API key
model = gemini-3.8-flash
NO Vertex.

Database:
PostgreSQL Dokploy separado.
NO reiniciar/redeployar Database innecesariamente.
Redeploy SOLO Compose.

Backfills:
lookback explícito execution-scoped.
NO mutar Source.config.

Grounding:
prompts v7
evidence_blocks_v1
pipeline orchestrator v6.
