# HITCHINGS — Current Project Status

Fecha:
2026-10-13

HEAD:
b3e3a57 — fix: improve PDF text reconstruction

## Bloque 11B — Fondo Documental
ESTADO: COMPLETADO Y OPERATIVO EN PRODUCCIÓN.

- **Separación Conceptual y de Dominio**:
  - Distinción nítida entre la vista ejecutiva del Observatorio (`/api/v1/observatory/entries` & `/observatorio`, que exige análisis completado vigente) y el Fondo Documental (`/api/v1/documents` & `/documentos`, que permite consultar tanto entradas analizadas como expedientes y capturas originales en archivo).
- **Backend & API (`/api/v1/documents`)**:
  - `GET /api/v1/documents`: Listado paginado con filtros (`q`, `source_id`, `has_analysis`, `origin_category`, `date_from`, `date_to`, `sort_by`, `sort_order`).
  - `GET /api/v1/documents/{document_id}`: Detalle de documento. Devuelve HTTP 200 con `has_analysis=False` y cuerpo documental si no tiene análisis, o con dictamen completo, temas canónicos y citas de evidencia si está analizado. Devuelve HTTP 404 únicamente si el documento no existe.
  - Anti-N+1: `selectinload` optimizado para `Source`, `TrackedEntity`, `analyses`, `topics` y `calls`.
  - 0 llamadas externas / 0 Gemini en tiempo de consulta: puramente lectura optimizada de PostgreSQL.
- **Frontend Observatorio**:
  - Integración en navegación principal (`AppLayout.tsx`) mediante la ruta `/documentos`.
  - Página de catálogo `DocumentsPage.tsx` con búsqueda reactiva, filtro por estado de análisis ("Todos", "Con análisis IA", "Sin análisis"), filtros de procedencia (institucional, LinkedIn, Google News, manual) y paginación.
  - Vista de expediente `DocumentDetailPage.tsx` con doble columna: cuerpo documental íntegro original a la izquierda y dictamen jurídico IA estructurado con citas literales a la derecha.
- **Integración con Fuentes y Producción Validada**:
  - Conexión con todas las fuentes existentes (>1102 documentos en producción).
  - Cobertura de entradas institucionales, LinkedIn y Google News.
  - Commits relevantes: `fcecf0a` (implementación inicial 11B), `0ee5f5c` (resolución de build TS), `3eb1479` (registro de rutas API).

## Bloque 11C — Ingesta Manual de Documentos
ESTADO: COMPLETADO Y OPERATIVO EN PRODUCCIÓN.

Módulo de incorporación manual de resoluciones, demandas y enlaces externos al Fondo Documental con análisis IA desacoplado, deduplicación institucional y calidad de lectura legal.

- **Fase 1: Infraestructura y Persistencia**:
  - Dependencia `python-multipart` integrada en `requirements.txt`.
  - Límite de carga en Nginx `client_max_body_size 25M` en `nginx.conf`.
  - Tipo de fuente canónico `SourceType.MANUAL_UPLOAD = "manual_upload"`.
  - Migración Alembic `0008_seed_manual_upload_source.py` y script `scripts/seed_source_manual_upload.py`.
  - Fuente canónica creada en producción: UUID `00000000-0000-0000-0000-000000000001`, code `manual_upload`, nombre `Carga manual de documentos`.
  - Commit: `d44cea0`.
- **Fase 2: Backend Core Service**:
  - Servicio `ManualDocumentIngestionService` en `app/services/manual_document_ingestion_service.py` (1048 líneas).
  - Ingesta desde URL externa con extracción automática de título/contenido mediante trafilatura/BeautifulSoup.
  - Ingesta desde archivo con soporte para PDF, TXT y MD hasta 25 MB (`MAX_FILE_BYTES = 25 * 1024 * 1024`).
  - Validación de extensiones y magic bytes (detección y rechazo de PDFs corruptos o vacíos).
  - Protección SSRF estricta contra IPs loopback, rangos privados RFC 1918 y metadata cloud (`169.254.169.254`).
  - Deduplicación jerárquica fail-closed: detección por URL canónica normalizada y por hash de contenido SHA-256 (`content_hash`).
  - Ejecución desacoplada de análisis IA (`_execute_background_analysis` vía `BackgroundTasks` de FastAPI) gobernada por parámetro `analyze_with_ai`.
  - Commit: `c63e43e`.
- **Fase 3: Endpoints API REST**:
  - `POST /api/v1/documents/url`: Endpoint para ingesta manual por URL.
  - `POST /api/v1/documents/upload`: Endpoint para carga manual por archivo multipart/form-data.
  - Autenticación obligatoria mediante sesión `get_current_user`.
  - Respuestas HTTP: `201 Created` con payload del documento o `409 Conflict` con esquema `DocumentDuplicateResponse` (`duplicate_reason`, `existing_document_id`, `existing_document_title`, `existing_document_url`).
  - Commit: `dadf7ac`.
- **Fase 4: Frontend UI (Modal y Gestión de Duplicados)**:
  - Botón `"Añadir documento"` visible en cabecera de `DocumentsPage.tsx`.
  - Componente modal interactivo `AddDocumentModal.tsx` (728 líneas):
    - Tab `"Desde URL"`: validación de protocolo HTTP/HTTPS y metadatos opcionales.
    - Tab `"Desde archivo"`: zona drag & drop, validación inmediata de extensión y tamaño.
    - Selector conmutador `"Analizar con IA"` (activado por defecto).
    - Metadatos opcionales desplegables: título personalizado, autor y fecha de emisión.
    - Modal secundario ante HTTP 409 Conflict con enlace directo al documento existente para evitar duplicidades.
    - Redirección automática tras éxito a `/documentos/:id`.
  - Commit: `798c076`.
- **Fase 5: Calidad, Limpieza y Reconstrucción de Texto PDF**:
  - Sanitización de títulos: eliminación de extensiones `.pdf`, `.txt`, `.md` y de hashes SHA-256 o UUIDs en los títulos mostrados.
  - Reconstrucción geométrica de texto PDF por coordenadas (`_reconstruct_page_text_geometrically`): extracción ordenada vertical y horizontalmente mediante `pypdf`, preservando párrafos reales y evitando fragmentaciones por columnas.
  - Parser y formateador jurídico en `documentFormatter.tsx` (`healHistoricalContent`): unión tipográfica de palabras partidas por guion a final de línea y corrección de drop caps descolgados.
  - Commits: `937c360` (limpieza de títulos y párrafos) y `b3e3a57` (reconstrucción geométrica PDF).

- **Validación en Producción Dokploy**:
  - Desplegado y verificado en Dokploy con build TypeScript exitoso.
  - Migración 0008 aplicada en PostgreSQL de producción.
  - Pruebas reales de ingesta por archivo PDF, ingesta por URL, respuesta 409 ante duplicados y visualización de expedientes con formato jurídico.
  - Commit final desplegado: `b3e3a57` (*fix: improve PDF text reconstruction*).

## LinkedIn Source: Operational Readiness & Production Integration (Bloques 9C, 9C.1, 9C.2, 9E)

ESTADO: OPERATIVO Y VALIDADO EN PRODUCCIÓN
- **Pipeline Operativo Completo**: Discovery asíncrono con Bright Data (`gd_lyy3tktm25m4avu764`) -> Fallback a Apify (`harvestapi/linkedin-profile-posts`) -> Recuperación canónica de perfiles ante `dead_page` o 0 publicaciones -> Normalización de subdominios regionales y paths localizados (`/en`) -> Validación de procedencia fail-closed -> Deduplicación por `activity_id` / canonical URL -> Creación de Entry -> Análisis Gemini automático (Triage v7 -> Deep Analysis v7 condicional).
- **Validación Real de Thomas Funke en Producción**:
  - Entidad: `Thomas Funke` (ID: `b5bd6bab-8849-465d-84f3-8bad7ac3bb23`).
  - URL canónica verificada en BD de producción y sincronizada en código: `https://www.linkedin.com/in/dr-thomas-g-funke-96297346`.
  - Bright Data extrajo publicaciones con URL localizada `https://de.linkedin.com/in/dr-thomas-g-funke-96297346/en?trk=...`.
  - Normalizador reconoció la equivalencia canónica exacta.
  - Resultado en producción: 5/5 posts descubiertos, 5/5 Entries creadas, 0 duplicados, 0 provenance rejected, 0 errores de proveedor.
- **Modos de Ejecución**:
  1. *Automático (WeeklyRefresh / Daily Refresh)*:
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

## Estado Observatorio

ADLC:
CLOSED
49/49 entries completed
0 pending
0 failed-only
0 new candidates

Fuentes:
17/17 integradas (incluidas FTC y DOJ Antitrust Division).

## Scheduler Diario y Semanal (Bloque 10 / 11C)

OPERATIVO Y DESPLEGADO EN PRODUCCIÓN
- Daemon: `scheduler` en `docker-compose.prod.yml` (`python -m app.scheduler`).
- Cadencia configurada en producción: `SCHEDULER_CADENCE=daily`.
- Ejecución diaria: 06:00 `Europe/Madrid`.
- Cobertura: 17/17 fuentes activas procesadas con aislamiento estricto de fallos.
- Lookback diario: 3 días fijados por defecto (`SCHEDULER_DAILY_LOOKBACK_DAYS=3`).
- Inmutabilidad: `Source.config` no se modifica.
- Concurrencia segura: `RefreshAdvisoryLock` mediante advisory lock de PostgreSQL.
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

## LinkedIn Discovery: Estado de Entidades y Procedencia (Bloque 9E)

ESTADO DE VERIFICACIÓN DE ENTIDADES
- Proveedores: Bright Data primario, Apify fallback.
- Gating de autoría fail-closed: posts con autores no verificables o URLs incoherentes son descartados sin persistir.
- Gestión de proveedor técnico: escrituras registran exclusivamente `raw_metadata["retrieval_provider"]`.
- Entidades Piloto Verificadas en Código y Producción (11):
  - Organizaciones (4): Hausfeld, ESKARIAM, CNMC, European Commission.
  - Personas (7): Miguel Sousa Ferro, Pinar Akman, Damien Geradin, Thomas Höppner, Joost Fanoy, Stefan Tuinenga, Thomas Funke (`https://www.linkedin.com/in/dr-thomas-g-funke-96297346`).
- Entidades Personales Pendientes de Verificación (16):
  - No tienen URL asignada o carecen de enlace público comprobado sin login; permanecen expresamente sin URL para evitar falsos positivos o homónimos:
    Pablo Ibáñez Colomo, Francisco Marcos, Assimakis Komninos, Julia Suderow, Fernando Díez Estella, Antonio Robles Martín-Laborda, Alba Ribera Martínez, Pierre Bichet, Christian Bergqvist, Emilija Berzanskaite, Jaime Concheiro, Adoni Llosa, Eduardo Pastor, Pedro Suárez, Javier Pérez, James Hain-Cole, Lena Hornkohl.
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

1. Supervisión del primer ciclo refresh completo en producción Dokploy con telemetría operacional activa.
2. Revisión del dashboard de monitorización y consolidación de métricas de calidad y telemetría por fuente.

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
