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

- Pipeline Operativo LinkedIn Discovery (Transición completada desde Sonda):
  - Flujo asíncrono Bright Data validado en producción: `POST /datasets/v3/trigger` -> polling `GET /datasets/v3/progress/{snapshot_id}` -> descarga `GET /datasets/v3/snapshot/{snapshot_id}?format=json` (snapshot real `sd_mu34g4n42ptmtxyidh` validado con 1 post, external_id `urn:li:activity:7503008038128119808`).
  - Soporte completo y verificado para organizaciones (`/company/`, `discover_by=company_url`) y personas (`/in/`, `discover_by=profile_url`, `only_authored_posts: true`, `author_type=person`).
  - Límites seguros configurables en entorno (`LINKEDIN_DISCOVERY_MAX_ENTITIES=1`, `LINKEDIN_DISCOVERY_MAX_POSTS_PER_ENTITY=1`).
  - Seguimiento de costes con `BRIGHTDATA_LINKEDIN_POST_COST_PER_RECORD` (ej. 0.0025) y fallback automático a `BRIGHTDATA_COST_PER_RECORD_USD`.
  - Desacoplamiento de Hausfeld: `LinkedInDiscoveryPlanner` selecciona deterministamente entidades activas con prioridad institucional (`institution`: 90, `organization`: 80, `person`: 70, resto: 50).
  - Procedencia estricta fail-closed: exclusión automática de entidades sin URL y posts con autor mismatch.
  - CLI `scripts/ingest_linkedin.py` adaptado para ejecución batch estándar (`--confirm-real-calls`, `--max-entities`, `--max-posts`, `--entity`), con generación estructurada de `LINKEDIN DISCOVERY REPORT` con resumen, consumo de proveedor y desglose `Por entidad:`.
  - Flag `--probe` conservado para pruebas quirúrgicas controladas de Hausfeld.
  - Scheduler globalmente inactivo (`LINKEDIN_DISCOVERY_ENABLED=false` por defecto).
  - Cobertura de tests: 44/44 en `tests/test_linkedin_discovery.py`, 47/47 en Vitest frontend.

## Próximo paso exacto

1. Ejecución de prueba batch controlada en entorno de pruebas/producción cuando se autorice:
   `python -m scripts.ingest_linkedin --confirm-real-calls --max-entities 1 --max-posts 1`

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
