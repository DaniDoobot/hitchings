# HITCHINGS — Current Project Status

Fecha:
2026-09-16

## LinkedIn Source: Operational Readiness & Production Integration (Bloque 9C/9D)

ESTADO: OPERATIVO EN PRODUCCIÓN
- **Pipeline Operativo Completo**: Discovery asíncrono con Bright Data -> Normalización y validación de procedencia fail-closed -> Deduplicación por `activity_id` / canonical URL -> Creación de Entry -> Análisis Gemini automático (Triage v7 -> Deep Analysis v7 condicional).
- **Modos de Ejecución**:
  1. *Automático (WeeklyRefresh)*:
     - Integrado en `WeeklyRefreshService`.
     - Configuración dinámica en base de datos: `Source.config` y `Source.active` gobiernan `enabled`, `max_entities`, `max_posts` y `max_concurrent_jobs` sin necesidad de reiniciar el servicio o redeployar `.env`.
     - Límite de concurrencia: `LINKEDIN_MAX_CONCURRENT_JOBS=3` (controlado por semáforo `asyncio.Semaphore`).
     - Análisis incremental automático integrado: tras la ingesta de LinkedIn en el refresh, las nuevas entradas se incorporan al plan incremental de análisis Gemini.
  2. *Manual (CLI y Scripts)*:
     - Ingesta controlada: `python -m scripts.ingest_linkedin --confirm-real-calls --max-entities=N --max-posts=M`
     - Análisis en lote: `python -m scripts.analyze_linkedin_batch --limit=N --entity=Nombre`
- **APIs de Monitorización y Calidad**:
  - `GET /api/v1/sources/status`: Resumen de salud operacional por fuente (estado 'healthy'/'degraded'/'failed'/'disabled', fecha y duración del último run, `entries_created`, `duplicates_skipped`, `errors`, `timeout_snapshots`).
  - `GET /api/v1/sources/metrics`: Métricas de calidad y rendimiento consolidadas (`posts_captured`, `entries_created`, `total_analyzed`, `relevant_count`, `relevant_pct`, `deep_analysis_count`, `deep_analysis_pct`, `avg_analysis_time_ms`, `estimated_gemini_cost_usd`, `estimated_provider_cost_usd`).
- **Frontend Observatorio (UI)**:
  - Filtro por origen en barra lateral de escritorio y cajón móvil: `Todas`, `Institucional`, `LinkedIn`, `Expert Analysis`.
  - Badge `"Fuente LinkedIn"` visible en las tarjetas de publicación y cabecera de detalle para publicaciones originadas en LinkedIn.
  - Estricto aislamiento institucional: cero exposición de términos técnicos de proveedor (`brightdata`, `apify`) en la interfaz de usuario.
- **Evaluación del Prompt de Triage v7 (`scripts/evaluate_linkedin_triage.py`)**:
  - Comportamiento validado: alta especificidad filtrando ruido corporativo (publicidad, felicitaciones, eventos, webinars) y preservando publicaciones con fondo jurídico sustantivo (sentencias de tribunales, cárteles, litigios de daños, DMA/competencia).
  - Sin necesidad de modificaciones en el prompt `observatory_triage:v7`.
- **Próximos Pasos Naturales**:
  - Monitorización continua de métricas de coste y ratio de relevancia vía `/api/v1/sources/metrics`.
  - Incorporación progresiva de perfiles personales de la lista de expertos conforme se verifiquen sus URLs canónicas en abierto.
  - Ajuste fino de concurrencia según SLA del proveedor de scraping.

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

- Pipeline Operativo LinkedIn Discovery & Validación Multiorigen (Fases 1-4):
  - Flujo asíncrono Bright Data validado en producción: `POST /datasets/v3/trigger` -> polling `GET /datasets/v3/progress/{snapshot_id}` -> descarga `GET /datasets/v3/snapshot/{snapshot_id}?format=json`.
  - Soporte completo para organizaciones (`/company/`) y personas (`/in/`, `only_authored_posts: true`, `author_type=person`).
  - Fase 1 (Batch CLI & Override manual seguro): CLI `scripts/ingest_linkedin.py` permite `--confirm-real-calls` con `LINKEDIN_DISCOVERY_ENABLED=false` mediante `allow_manual=True`. Métrica `provenance_rejected` agregada y reportada por entidad y en resumen.
  - Fase 2 (Diferenciación de Origen en Modelo, API y LLM):
    - Modelo `Entry`: propiedades `@property is_linkedin -> bool` y `@property source_origin_category -> str` ('institutional', 'linkedin', 'expert_analysis', 'other').
    - Esquemas API (`ObservatorySourceRef`, `ObservatoryEntryListItem`, `ObservatoryEntryDetail`): expuestos `type`, `category`, `is_linkedin` y `source_origin_category`.
    - Prompts Gemini (`_build_triage_prompt` y `_build_deep_prompt`): inyección explícita de `Origen: LinkedIn (organization/person)` / `Institucional` y `Autor` en los bloques `[DOCUMENTO]`.
  - Fase 3 (Punto de integración en Scheduler): `WeeklyRefreshService` preserva `LINKEDIN_DISCOVERY_ENABLED=false` estricto y mapea automáticamente nuevas publicaciones a `detail.new_entry_ids` para análisis incremental cuando sea activado.
  - Fase 4 (UI Enriquecida del Observatorio): `ObservatoryPage` y `EntryDetailPage` presentan claramente el origen LinkedIn, autor, iconos diferenciados (`Building2` / `User`) y badge `Organización` / `Persona`, manteniendo compatibilidad con tests y sin exponer proveedores técnicos (`brightdata`, `apify`).
  - Cobertura de tests: 47/47 en `tests/test_linkedin_discovery.py`, 12/12 en `tests/test_weekly_refresh.py`, 47/47 en Vitest frontend.

## Diagnóstico Operacional del Observatorio (Operational Health Diagnostics)

IMPLEMENTADO & AUDITADO
- **Herramienta CLI de Telemetría**: `scripts/diagnose_observatory_health.py` (`python -m scripts.diagnose_observatory_health`).
- **Métricas Inspeccionadas**:
  - Estado y habilitación por fuente (`healthy`, `degraded`, `disabled`, `YES`/`NO`).
  - Conversión del pipeline: `Items captured` -> `Entries created` -> `Triage analyzed` (`Relevant`, `Uncertain`, `Not relevant`) -> `Deep Analysis` -> `Failed` -> `Timed out snapshots`.
  - Costes agregados: Gemini estimated cost, Provider estimated cost (Bright Data / Apify) con fallback explícito a `N/A — insufficient telemetry` cuando no hay datos.
  - Tiempos de ejecución: Última ejecución global, último éxito y latencia promedio de análisis LLM.
  - Auditoría de integridad y detección automática de anomalías (inversiones de pipeline, ratios incoherentes, números negativos).
- **Cobertura de Pruebas**: `tests/test_observatory_health_cli.py` (4/4 tests específicos pasados; 660+ tests totales en suite backend).

## Próximo paso exacto

1. Ejecución manual del diagnóstico de salud en producción Dokploy:
   `docker exec -it <backend_container> python -m scripts.diagnose_observatory_health`
2. Revisar los resultados de las 17 fuentes institucionales y LinkedIn antes de autorizar el primer ciclo semanal completo.

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
