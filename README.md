# HITCHINGS - Backend Observatorio

## 1. Qué es HITCHINGS

**HITCHINGS** es una plataforma orientada a la monitorización, extracción, normalización y análisis inteligente de fuentes de información y novedades (noticias, portales regulatorios e institucionales, blogs especializados y redes como LinkedIn).

En el futuro, HITCHINGS integrará dos grandes capacidades:
1. **Observatorio automático de novedades:** monitorización continua, clasificación temática, síntesis y scoring mediante IA.
2. **Herramienta de análisis documental:** ingesta de documentos, audios y textos, prompts especializados y generación de informes.

---

## 2. Alcance Actual: BLOQUES 0 a 8A (8A Cerrado)

El proyecto cuenta con:
- **BLOQUE 0:** Base estructural, persistencia (PostgreSQL + SQLAlchemy 2.0 síncrono con psycopg v3, Alembic), configuración y contratos de proveedores.
- **BLOQUE 1:** Gestión configurable de fuentes, matriz de seguimiento v0.1 (`TrackingMatrix`, `TrackingTopic`, `TrackedEntity`, `TrackedEntityTopic`).
- **BLOQUE 2:** Primera fuente real end-to-end conectada a Internet (**CNMC - Prensa / Noticias** vía website HTML oficial con `CNMCNewsExtractor`), ingesta normalizada, deduplicación básica en base de datos y endpoints de consulta de entradas.
- **BLOQUE 3:** Trazabilidad, histórico y robustez de ingestas (`IngestionRun`, estados `success`/`partial`/`failed`, cálculo dinámico de frescura y endpoints de observabilidad técnica).
- **BLOQUE 4:** Segunda fuente real institucional: **European Commission / DG Competition** (vía RSS oficial de Competition Policy con enriquecimiento de texto íntegro vía Press Corner API y node fallback con `EuropeanCommissionExtractor`).
- **BLOQUE 5:** Tercera fuente real institucional y nuevo tipo de contenido: **Competition Appeal Tribunal (CAT)** / Resoluciones Judiciales (vía website HTML oficial con `CompetitionAppealTribunalExtractor`, extracción de Neutral Citations, multi-casos, PDFs originales enlazados y resúmenes oficiales normalizados como `judicial_decision`).
- **BLOQUE 6:** Cuarta fuente real institucional y jurisprudencia comunitaria: **Tribunal de Justicia de la Unión Europea (TJUE / CURIA)** / Sentencias y Conclusiones (vía InfoCuria, identificador canónico ECLI, deduplicación exacta, texto íntegro y limpio).
- **BLOQUES 7A a 7H.3:** Motor Analítico y Grounding Completo (Cerrado). 100% de cobertura (80/80 entradas con análisis vigente, validación verbatim de citas sin falsos positivos, prompts v6 y control estricto de costes).
- **BLOQUE 8A:** API de Consumo del Observatorio para el Portal Cliente (`/api/v1/observatory`): frontera estricta entre API técnica y de producto, semántica de `select_current_analysis`, topics canónicos, evidencia limpia y consultas optimizadas anti-N+1.


### Principio Arquitectónico Fundamental: Separación de Responsabilidades
El diseño desacopla estrictamente tres dimensiones:
1. **QUÉ SE VIGILA (`TrackedEntity`)**: La persona, organización, institución o publicación de interés (ej. *Pinar Akman*, *Comisión Nacional de los Mercados y la Competencia*, *Hausfeld*). No presupone una URL fija ni un canal técnico específico.
2. **DÓNDE SE OBTIENE (`Source`)**: El canal técnico concreto de adquisición (web personal, perfil LinkedIn, RSS, API institucional). Una misma entidad puede tener asociadas múltiples fuentes técnicas o ninguna hasta que se disponga de su URL definitiva.
3. **CÓMO SE INTERPRETA (`TrackingTopic` & `TrackingMatrix`)**: La taxonomía temática, subtemas, queries de descubrimiento, señales/keywords orientativas e instrucciones de relevancia/exclusión.

> [!NOTE]
> En este bloque **NO** se implementan modelos de IA, no hay scheduler automático en segundo plano, no hay frontend ni autenticación. La ingesta se ejecuta de forma manual y controlada vía endpoint HTTP o scripts.

---

## 3. Matriz de Seguimiento y Versionado (`TrackingMatrix`)

Una **`TrackingMatrix`** define un marco temporal y metodológico de monitorización:
- **Versionado:** Permite evolucionar la matriz (ej. `HITCHINGS-v0.1`, `HITCHINGS-v1.0`) sin alterar el código de los scrapers ni la base de datos.
- **Estados:** `draft` (borrador), `active` (matriz activa actual) y `archived` (histórica/archivada).
- **Regla de Activación:** Existe como máximo una matriz en estado `active`. Al activar una nueva matriz mediante `POST /api/v1/tracking/matrices/{id}/activate`, cualquier matriz previamente activa pasa automáticamente a `archived`.
- **Instrucciones Centralizadas:** Contiene las directrices textuales de relevancia y exclusión que consumirá la capa de IA en fases posteriores.

### Carácter de la Matriz `HITCHINGS-v0.1`:
- **Entidades iniciales:** Proceden directamente de la documentación facilitada por el cliente (conservando sus nombres literales).
- **Áreas temáticas principales:** `competition_law_general` y `private_enforcement` proceden expresamente de la documentación del cliente (`provisional = false`).
- **Subtemas:** Son una propuesta técnica interna (`provisional = true`) para estructurar la clasificación y podrán ser sustituidos cuando el cliente proporcione su taxonomía definitiva.

---

## 4. Arquitectura del Repositorio

```text
hitchings/
├── app/
│   ├── api/                  # Enrutamiento y endpoints HTTP
│   │   ├── router.py         # Router central (/health y /api/v1)
│   │   └── v1/endpoints/
│   │       ├── health.py     # GET /health, GET /health/db
│   │       ├── sources.py    # CRUD y disparo de ingesta (/api/v1/sources)
│   │       ├── entries.py    # Consulta de entradas capturadas (/api/v1/entries)
│   │       └── tracking.py   # Matrices, topics, entities y associations (/api/v1/tracking)
│   ├── core/                 # Configuración central (pydantic-settings) y logging
│   ├── db/                   # Engine SQLAlchemy síncrono (psycopg v3) y sesiones
│   ├── models/               # Modelos SQLAlchemy: Source, Entry, IngestionRun, Tracking*
│   ├── providers/            # Contratos e implementaciones (NativeProvider, Bright Data, Apify)
│   │   └── extractors/       # Extractores especializados (CNMC, European Commission, CAT)
│   ├── schemas/              # Esquemas Pydantic para validación y serialización
│   ├── services/             # Servicios de dominio: IngestionService con deduplicación y observabilidad
│   └── main.py               # Punto de entrada FastAPI y lifespan
├── migrations/               # Scripts de migración Alembic
│   └── versions/
│       ├── 0001_initial_schema.py        # sources, entries, provider_usage
│       ├── 0002_tracking_configuration.py # matrices, topics, entities, entity_topics
│       └── 0003_ingestion_runs.py         # ingestion_runs, campos de frescura
├── scripts/
│   ├── seed_tracking_v01.py                   # Seed de la Matriz v0.1 y entidades del cliente
│   ├── seed_source_cnmc.py                    # Seed de la fuente real CNMC asociada a su entidad
│   ├── seed_source_european_commission.py     # Seed de la fuente real Comisión Europea
│   ├── seed_source_competition_appeal_tribunal.py # Seed de la fuente real CAT (Judgments)
│   └── seed_source_curia.py                   # Seed de la fuente real TJUE / CURIA (Case Law)
├── tests/                    # Tests unitarios, de API, modelos, seeds y fuentes reales (68 tests)
├── .env.example              # Plantilla de variables de entorno seguras
├── .gitignore                # Exclusiones de Git (.env, .venv, caches)
├── Dockerfile                # Imagen Docker multi-plataforma (Python 3.12-slim)
├── docker-compose.yml        # Orquestación de servicios: api y db (PostgreSQL 16)
├── requirements.txt          # Dependencias fijadas del proyecto
├── alembic.ini               # Configuración del motor de migraciones
└── README.md                 # Documentación técnica
```

---

## 5. Configuración de Variables de Entorno (`.env`)

Copia la plantilla `.env.example` a un archivo `.env`:

```bash
cp .env.example .env
```

### Puertos y Conexión PostgreSQL:
- **Desarrollo Local en Windows (PostgreSQL en Host):**  
  `DATABASE_URL=postgresql+psycopg://hitchings:hitchings@localhost:5432/hitchings`
- **Dentro de Docker Compose:**  
  `DATABASE_URL=postgresql+psycopg://hitchings:hitchings@db:5432/hitchings`
- **Desde el Host Windows (conectando a PostgreSQL en Docker):**  
  `DATABASE_URL=postgresql+psycopg://hitchings:hitchings@localhost:5433/hitchings`

### Política Free-First y Límites:
- `BRIGHTDATA_ENABLED=false` (desactivado por defecto)
- `BRIGHTDATA_MONTHLY_LIMIT=5000`
- `BRIGHTDATA_SOFT_LIMIT=4500`
- `BRIGHTDATA_ALLOW_PAID_USAGE=false`
- `APIFY_ENABLED=false`

---

## 6. Cómo Levantarlo en Local

### Opción A: Entorno Virtual Local Windows (Recomendado para Desarrollo)
```powershell
# Activar entorno virtual
.\.venv\Scripts\Activate.ps1

# Iniciar servidor Uvicorn
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Opción B: Docker Compose (Para despliegue futuro en VPS / Dokploy)
```bash
docker compose up -d --build
```

---

## 7. Cómo Ejecutar Migraciones

Las migraciones de Alembic se aplican con:

```powershell
# En entorno local Windows:
.\.venv\Scripts\alembic.exe upgrade head

# En contenedor Docker:
docker compose exec api alembic upgrade head
```

---

## 8. Seeds Idempotentes

### 1. Matriz de Seguimiento v0.1
Puebla la base de datos con la matriz `HITCHINGS-v0.1`, los 18 topics/subtopics y las 38 entidades proporcionadas por el cliente:
```powershell
python -m scripts.seed_tracking_v01
```

### 2. Fuente Real CNMC
Registra la fuente técnica oficial de la CNMC (`type="website"`, `url="https://www.cnmc.es/prensa/noticias"`) vinculándola a la entidad vigilada existente "Comisión Nacional de los Mercados y la Competencia":
```powershell
python -m scripts.seed_source_cnmc
```

### 3. Fuente Real Comisión Europea / DG Competition
Registra la fuente técnica oficial de Competition Policy de la Comisión Europea (`type="rss"`, `url="https://competition-policy.ec.europa.eu/node/38/rss_en"`) vinculándola a la entidad vigilada existente "European Commission" (`002dde9c-af40-443c-80f4-c9eca5b9f57f`):
```powershell
python -m scripts.seed_source_european_commission
```

Todos los scripts son completamente **idempotentes**.

---

## 9. Ingesta Real y Deduplicación (BLOQUE 2)

### Circuito End-to-End
```text
INTERNET (https://www.cnmc.es/prensa/noticias)
   ↓
NativeProvider (httpx async + CNMCNewsExtractor con BeautifulSoup4)
   ↓
Paginación Drupal (?page=0, ?page=1...) hasta initial_fetch_limit (20)
   ↓
Enriquecimiento asíncrono concurrente de cuerpo de noticia (.page-nw-article-body)
   ↓
RawEntryData (título, url canónica, published_at ISO, sector, texto limpio, extracto)
   ↓
IngestionService (deduplicación jerárquica en PostgreSQL + freshness check)
   ↓
Entry persistida (status='raw', content_hash SHA-256)
```

### Decisión Técnica Clave: HTML vs RSS y Validación de Frescura
Durante el desarrollo del Bloque 2 se validó inicialmente el endpoint RSS oficial (`https://www.cnmc.es/feed/prensa/noticias`). Aunque el endpoint respondía técnicamente con HTTP 200 y XML válido, se detectó una discrepancia temporal y funcional respecto a la página web en vivo:
- La página web oficial publica las noticias de forma inmediata, incluye la categorización por **sector** (Competencia, Telecomunicaciones, Energía, Promoción de Competencia) y el cuerpo editorial íntegro.
- Por tanto, se adoptó la página web oficial como fuente primaria mediante `CNMCNewsExtractor`.
- **Soporte RSS preservado:** El método `NativeProvider._fetch_rss()` permanece intacto en el código para dar cobertura a otras fuentes institucionales y medios donde el RSS sea el canal idóneo.
- **Validación de Frescura:** Se incorporó el cálculo de `latest_published_at` y `oldest_published_at` en el ciclo de ingesta para constatar documentalmente que los datos capturados están actualizados, demostrando que la disponibilidad técnica (HTTP 200) no es suficiente sin verificación de frescura del contenido.

### Jerarquía de Deduplicación:
1. `source_id + external_id` (URL canónica única).
2. `source_id + url / canonical_url` (URL de la noticia sin parámetros de tracking).
3. `source_id + content_hash` (hash SHA-256 de título normalizado + texto o resumen).

Si una entrada ya existe, se contabiliza como duplicada y se omiten inserciones repetidas, asegurando idempotencia en ejecuciones sucesivas.

---

---

## 10. Trazabilidad, Histórico y Observabilidad de Ingestas (BLOQUE 3)

### El Modelo `IngestionRun` (`ingestion_runs`)
Cada ejecución de ingesta genera un registro histórico inmutable con sus métricas y estado:
- `id`: Identificador único UUID de la ejecución.
- `source_id`: Clave foránea a la fuente técnica (`ON DELETE CASCADE`).
- `started_at` / `finished_at`: Marcas de tiempo UTC.
- `status`: Estado del ciclo de captura (`running`, `success`, `partial`, `failed`).
- `fetched_count`, `created_count`, `duplicate_count`, `failed_count`: Métricas de elementos procesados.
- `latest_published_at` / `oldest_published_at`: Rango de fechas de las publicaciones capturadas.
- `error_type` / `error_message`: Diagnóstico de fallos sin almacenar volcados de pila en la base de datos.
- `duration_ms`: Duración calculada en milisegundos.

### Diferencia entre `Source.last_run_at` e Histórico:
- `Source.last_run_at`: Muestra únicamente la marca temporal del **último intento de inicio** de ingesta.
- `Source.last_success_at`: Se actualiza **estrictamente** cuando una ejecución concluye en estado `success`. En ejecuciones `failed` o `partial`, `last_success_at` conserva intacto su valor anterior.
- `IngestionRun`: Proporciona la auditoría completa y detallada de todas las ejecuciones históricas.

### Estados de Ejecución:
1. `running`: Ejecución en curso.
2. `success`: El proceso finalizó completamente sin errores en la obtención ni en los elementos individuales.
3. `partial`: El proceso finalizó pero uno o varios elementos individuales fallaron durante la persistencia o normalización.
4. `failed`: Error global o estructural (caída de red, HTTP 5xx, excepción fatal del extractor).

### Control Dinámico de Frescura (`freshness`):
Distingue claramente dos escenarios operativos distintos:
- **Ejecución fallida (`status: failed`):** El scraper o la conexión con la fuente técnica tienen un problema técnico.
- **Fuente sin contenido reciente (`status: success`, `freshness: stale`):** El scraper funciona con éxito técnico, pero el organismo o portal no ha publicado novedades dentro del umbral esperado.

Configurable por fuente en `source.config` mediante `freshness_warning_hours` (ej. 168 horas = 7 días):
- `fresh`: `now - latest_published_at <= freshness_warning_hours`.
- `stale`: El tiempo transcurrido supera el umbral configurado.
- `unknown`: No se dispone de fecha de publicación en ejecuciones exitosas previas.

---

## 11. Segunda Fuente Real: European Commission / DG Competition (BLOQUE 4)

### Circuito y Arquitectura Reutilizable:
```text
European Commission / DG Competition (https://competition-policy.ec.europa.eu/node/38/rss_en)
        ↓
Source (type='rss', provider='native', category='institutional', tracked_entity_id='European Commission')
        ↓
NativeProvider dispatch -> EuropeanCommissionExtractor
        ↓
Parsing RSS XML (Drupal node/38 feed) con soporte de initial_fetch_limit (20)
        ↓
Extracción de metadata semántica (categories: antitrust, mergers, state aid, cartels, foreign subsidies)
        ↓
Enriquecimiento concurrente de texto íntegro (asyncio.Semaphore):
   ├── Caso A: URL Press Corner directa -> API JSON oficial (https://ec.europa.eu/commission/presscorner/api/documents?reference={REF}&language={LANG})
   ├── Caso B: URL de nodo Drupal -> Extracción de enlace Press Corner o parseo HTML de <main>/<article>
   └── Fallback graceful -> Descripción del feed RSS
        ↓
IngestionService (reutilizado de forma transparente)
        ↓
Deduplicación jerárquica (SHA-256 de contenido + URL canónica)
        ↓
Entry en PostgreSQL (content_type='institutional_news', metadata completa)
        ↓
IngestionRun persistido + actualización de freshness (status='fresh', warning_hours=168)
```

### Principales Logros del Bloque 4:
1. **Reutilización Total:** Se reutilizó al 100% el motor `IngestionService`, el modelo `Entry`, la auditoría `IngestionRun` y el sistema de alertas de `freshness` sin modificar el esquema de base de datos ni crear tablas redundantes.
2. **Extracción Multicanal:** Se conecta el RSS agregador de novedades de la Dirección General de Competencia con la API REST oficial de documentos de la Comisión Europea (*Press Corner*), obteniendo notas de prensa íntegras (3.500 – 7.200 caracteres) con formato limpio y extractos descriptivos.
3. **Idempotencia y Deduplicación:** Ejecuciones sucesivas respetan las entradas existentes, registrando exactamente 0 duplicados y marcando los elementos existentes como no modificados.

---

## 12. Tercera Fuente Real: Competition Appeal Tribunal (CAT) / Judgments (BLOQUE 5)

### Circuito y Arquitectura:
```text
Competition Appeal Tribunal (https://www.catribunal.org.uk/judgments)
        ↓
Source (name='Competition Appeal Tribunal - Judgments', type='website', provider='native', category='institutional', tracked_entity_id='Competition Appeal Tribunal')
        ↓
NativeProvider dispatch -> CompetitionAppealTribunalExtractor
        ↓
Parsing HTML de listado (/judgments) con soporte de initial_fetch_limit (20) y paginación (?page=0, 1, ...)
        ├── Extracción de casos asociados (case_numbers, case_names, case_urls)
        ├── Extracción de Neutral Citation oficial ([2026] CAT 71, [2026] EWCA Civ 993)
        ├── Extracción de tipo de resolución y enlace al PDF original de la sentencia (judgment_pdf_url)
        └── Detección de landing de resumen oficial (/judgments/{slug})
        ↓
Enriquecimiento concurrente de resumen oficial (asyncio.Semaphore):
        ├── Con landing de resumen: extracción de texto íntegro en <div class="above-related"> y excerpt de 1.er párrafo
        └── Sin landing (ej. EWCA Civ): content=None, excerpt=None, url al caso con ancla de citación
        ↓
IngestionService (reutilizado de forma transparente)
        ↓
Deduplicación jerárquica (Neutral Citation como external_id + URL canónica)
        ↓
Entry en PostgreSQL:
        ├── content_type = 'judicial_decision'
        ├── language = 'en'
        ├── title = '[2026] CAT 71 | Walter Hugh Merricks CBE v Mastercard Incorporated - Ruling (Trial 2 Costs)'
        ├── published_at = fecha oficial de emisión del tribunal
        └── raw_metadata = {case_numbers, case_names, case_urls, neutral_citation, judgment_pdf_url, has_summary}
        ↓
IngestionRun persistido + actualización de freshness (status='stale' en receso judicial, warning_hours=336 / 14 días)
```

### Logros y Decisiones de Diseño del Bloque 5:
1. **Nuevo Tipo de Contenido (`content_type='judicial_decision'`):** HITCHINGS valida la captura de resoluciones judiciales formales, diferenciándolas de notas de prensa o noticias institucionales.
2. **Neutral Citations Oficiales:** Se capturan y normalizan las citaciones canónicas del sistema judicial británico (`[2026] CAT 71`, `[2026] EWCA Civ 872`), utilizándolas como `external_id` único y en el título.
3. **Mapeo de Casos Complejos:** Sentencias con múltiples casos agrupados (ej. litigios paraguas de comisiones de intercambio) conservan la lista completa de números y nombres de caso en `raw_metadata`.
4. **Resúmenes Oficiales vs. Resoluciones Sin Resumen:** Cuando el tribunal publica un resumen oficial (`/judgments/{slug}`), se extrae el texto íntegro y el primer párrafo como extracto. En resoluciones de tribunales superiores (Court of Appeal) sin resumen en CAT, se almacena el enlace al PDF oficial y la página del caso correspondiente sin generar errores ni campos espurios.
5. **Cero Migraciones Innecesarias:** Se respetó el modelo existente `Entry`, asegurando que `external_id` permanezca acotado ($\le 255$ caracteres) mediante la Neutral Citation oficial.

---

## 13. Cuarta Fuente Real: Tribunal de Justicia de la UE / CURIA (BLOQUE 6)

### Circuito y Arquitectura:
```text
InfoCuria Elastic REST API (https://infocuriaws.curia.europa.eu/elastic-connector/search)
        ↓
Source (name='Court of Justice of the European Union - Case Law', type='website', provider='native', category='institutional', tracked_entity_id='El Tribunal de Justicia de la Unión Europea')
        ↓
NativeProvider dispatch -> CuriaCaseLawExtractor
        ↓
Búsqueda oficial InfoCuria (tabName='jurisprudence', sort='DOC_DATE desc', initial_fetch_limit=20)
        ├── Captura de resoluciones individuales: Sentencias (Arrêt), Autos (Ordonnance), Conclusiones AG (Conclusions)
        ├── Identificador canónico judicial: ECLI (e.g. ECLI:EU:C:2026:702, ECLI:EU:C:2026:685) -> external_id
        ├── Metadata rica: número de caso (C-380/25), tribunal (Court of Justice / General Court), CELEX, fechas oficiales
        └── Detección de nombre usual oficial / ficticio RGPD (e.g. [Livronsa], [Lertimene], [Grixta])
        ↓
Descarga concurrente del documento íntegro oficial (InfoCuria Blob Storage):
        ├── https://infocuriaws.curia.europa.eu/blob/download-file-html/{jur}/{year}/{proc}/{file}
        ├── Preferencia de idioma canónico: EN -> FR -> primer idioma oficial disponible
        ├── Conversión oficial HTML a texto limpio: preservación semántica de párrafos, encabezados, partes y numeración sin tags HTML
        └── Principio de procedencia: si el blob no estuviera disponible, content=None y excerpt=None sin generar texto ficticio
        ↓
IngestionService (reutilizado al 100% de forma transparente)
        ↓
Deduplicación canónica por ECLI + SHA-256
        ↓
Entry en PostgreSQL:
        ├── content_type = 'eu_case_law'
        ├── language = 'en' / 'fr'
        ├── title = 'Case C-60/25 [Livronsa] | Judgment'
        ├── published_at = fecha oficial de lectura / pronunciamiento
        ├── content = Texto íntegro y limpio de la resolución judicial oficial (sin tags HTML)
        └── raw_metadata = {ecli, case_number, document_type, celex, court, infocuria_url, content_source='infocuria_html', content_format='text/plain', full_text_available=True}
        ↓
IngestionRun persistido + actualización de freshness (status='fresh', warning_hours=336 / 14 días)
```

### Logros y Decisiones de Diseño del Bloque 6:
1. **Regla de Oro: 1 Resolución Judicial = 1 Entry:** No se capturan entregas de vídeo agregadas ni índices masivos, sino cada sentencia individual, auto o conclusiones del Abogado General como un registro independiente con su propio texto completo.
2. **Identificador Canónico ECLI:** Se normaliza el European Case Law Identifier (`ECLI:EU:C:...` / `ECLI:EU:T:...`) como `external_id` único, garantizando deduplicación exacta y trazabilidad a escala comunitaria.
3. **Extracción Directa InfoCuria:** Conexión con los endpoints oficiales del nuevo sistema InfoCuria de la Unión Europea para búsqueda estructurada y descarga de contenido.
4. **Normalización a Texto Limpio y Procedencia Estricta:** `Entry.content` almacena texto limpio conforme al estándar del observatorio; si el blob no está disponible, no se fabrican cuerpos documentales ficticios.
5. **Respeto a la Matriz y Entidad Existente:** Se vincula con la entidad `"El Tribunal de Justicia de la Unión Europea"` (ID `46c36f17-fca8-49ae-ad5c-080d5492b400`), sin crear entidades duplicadas.
6. **Captura Exhaustiva y Neutral:** Se capturan las decisiones más recientes sin filtrar por materia en la capa de ingesta, dejando la clasificación temática para la futura capa de IA.

---

## 14. Bloque 7A: Arquitectura y Persistencia del Análisis con IA

El **Bloque 7A** establece los cimientos de modelado, persistencia, versionado y auditoría para el análisis asistido por inteligencia artificial de las publicaciones capturadas en HITCHINGS, bajo estrictas garantías de reproducibilidad, trazabilidad y control de costes.

```text
                                  ┌─────────────────────────────┐
                                  │   TrackingMatrix (v0.1)     │
                                  └──────────────┬──────────────┘
                                                 │ Snapshot JSONB + SHA-256
                                                 ▼
┌─────────────────────────┐           ┌─────────────────────────────┐
│      Entry (Original)   │──────────►│      EntryAnalysis          │
│  (Inmutable en BD)      │  (1..N)   │  - relevance_score (0-100)  │
└─────────────────────────┘           │  - relevance_status         │
                                      │  - summary / key_points     │
                                      └──────┬───────────────┬──────┘
                                             │               │
                      ┌──────────────────────┘               └──────────────────────┐
                      ▼                                                             ▼
┌──────────────────────────────────────────┐                  ┌──────────────────────────────────────────┐
│      EntryAnalysisTopic (Normalizado)    │                  │       AnalysisCall (Auditoría Técnica)   │
│  - topic_id (FK tracking_topics)         │                  │  - prompt_version_id (FK versioned)      │
│  - confidence (0.0 - 1.0)                │                  │  - stage ('triage', 'deep_analysis')     │
│  - is_primary (Exactamente 1 primario)   │                  │  - provider / model                      │
│  - rationale                             │                  │  - input_tokens / output_tokens          │
└──────────────────────────────────────────┘                  │  - estimated_cost_usd / latency_ms       │
                                                              │  - status ('success', 'error')           │
                                                              └──────────────────────────────────────────┘
```

### Principios Fundamentales del Análisis con IA:
1. **Inmutabilidad de la Entrada (`Entry` vs `EntryAnalysis`):** La tabla `entries` preserva exactamente la captura original del medio fuente. Toda interpretación analítica se almacena en `entry_analyses`. Una misma entrada puede disponer de 0..N análisis históricos o comparativos entre versiones de matriz o prompts.
2. **Snapshots Canónicos y Hashing Determinista:** Cada análisis guarda una copia estricta en JSONB de la `TrackingMatrix` en el momento de la evaluación (`matrix_snapshot`), calculando su hash SHA-256 (`matrix_snapshot_hash`). Asimismo, se calcula el hash del contenido textual analizado (`entry_content_hash`). Si la matriz cambia en el futuro, el histórico analítico permanece auditable y reproducible.
3. **Control de Versiones de Prompts (`AnalysisPromptVersion`):** Los prompts no se almacenan como cadenas arbitrarias en el código, sino en base de datos con código, número de versión entero y etapa (`triage`, `deep_analysis`). La restricción única `UNIQUE(code, version)` previene colisiones.
4. **Clasificación Temática Normalizada (`EntryAnalysisTopic`):** Los temas sugeridos por el análisis se validan contra los temas activos de la matriz. Se garantiza como regla de negocio que exista **como máximo 1 tema primario** (`is_primary=True`).
5. **Auditoría Exhaustiva de Costes y Rendimiento (`AnalysisCall`):** Cada invocación individual a un modelo registra tokens de entrada y salida, latencia en milisegundos, coste estimado en USD (precisión `NUMERIC(12,6)`), payload de respuesta en bruto, y metadatos técnicos. Si la llamada falla, la auditoría persiste con estado `error`, tipo de excepción y mensaje para diagnóstico.
6. **Política de Seguridad "Disabled-by-Default":** Por defecto, `ANALYSIS_PROVIDER="disabled"`, bloqueando cualquier intento de invocar modelos externos reales sin autorización explícita de configuración y sin generar costes no planificados. Para pruebas unitarias locales y tests automatizados, se implementa `MockAIProvider` determinista.

---

## 15. Cómo Ejecutar Tests

La suite completa (**91 tests**, 0 fallos) valida configuración, endpoints, modelos, contratos de providers, seeds, extractores especializados (CNMC, Comisión Europea, CAT y TJUE/CURIA), parsing RSS y HTML Drupal, deduplicación, ciclo de vida de `IngestionRun`, frescura, snapshots, modelos relacionales de IA, mock provider determinista, cuotas mensuales y endpoints de análisis:

```powershell
.venv\Scripts\pytest.exe -v
```

---

## 16. Endpoints Disponibles

### Salud del Sistema
- `GET /health`: Estado del proceso FastAPI (`{"status": "ok", "service": "hitchings"}`).
- `GET /health/db`: Conectividad real con PostgreSQL (`{"status": "ok", "database": "connected"}`).

### Fuentes Técnicas (`/api/v1/sources`)
- `GET /api/v1/sources`: Listado con paginación (`limit`, `offset`) y filtros (`active`, `tracked_entity_id`).
- `GET /api/v1/sources/{id}`: Detalle de una fuente.
- `POST /api/v1/sources`: Crear fuente técnica (permite asociar `tracked_entity_id`).
- `PATCH /api/v1/sources/{id}`: Actualización parcial.
- `DELETE /api/v1/sources/{id}`: Eliminación.
- `GET /api/v1/sources/{id}/status`: **Observabilidad técnica y frescura** en tiempo real.
- `POST /api/v1/sources/{id}/ingest`: Disparo manual de ingesta con retorno de `ingestion_run_id`.

### Histórico de Ejecuciones (`/api/v1/ingestion-runs`)
- `GET /api/v1/ingestion-runs`: Listado cronológico inverso con filtros (`source_id`, `status`, `limit`, `offset`).
- `GET /api/v1/ingestion-runs/{id}`: Detalle completo de una ejecución histórica.

### Entradas Capturadas (`/api/v1/entries`)
- `GET /api/v1/entries`: Listado de entradas persistidas con paginación (`limit`, `offset`) y filtros (`source_id`).
- `GET /api/v1/entries/{id}`: Detalle completo de una entrada capturada (`title`, `url`, `published_at`, `content`, `content_hash`, `raw_metadata`, etc.).
- `GET /api/v1/entries/{entry_id}/analyses`: Historial completo de análisis analíticos (0..N) generados para esta publicación.

### Análisis con IA (`/api/v1/entry-analyses` y `/api/v1/analysis-*`)
- `GET /api/v1/entry-analyses`: Listado paginado y filtrable de análisis (`status`, `relevance_status`, `entry_id`, `limit`, `offset`).
- `GET /api/v1/entry-analyses/{id}`: Detalle completo de un análisis incluyendo `matrix_snapshot`, topics asignados normalizados y registro de llamadas auditadas (`calls`).
- `GET /api/v1/analysis-usage`: Resumen acumulado de llamadas exitosas/fallidas, recuento de tokens de entrada/salida y coste total estimado en USD agrupado por proveedor y etapa.
- `GET /api/v1/analysis-prompts`: Listado de versiones de prompts de análisis disponibles (`observatory_triage`, `observatory_deep_analysis`).

### Matrices de Seguimiento (`/api/v1/tracking/matrices`)
- `GET /api/v1/tracking/matrices`: Listar matrices.
- `GET /api/v1/tracking/matrices/{id}`: Detalle de una matriz.
- `POST /api/v1/tracking/matrices`: Crear nueva matriz (estado `draft` por defecto).
- `PATCH /api/v1/tracking/matrices/{id}`: Modificar matriz.
- `POST /api/v1/tracking/matrices/{id}/activate`: Activar matriz (archiva automáticamente matrices activas previas).

### Temas y Subtemas (`/api/v1/tracking/topics`)
- `GET /api/v1/tracking/topics`: Listar temas con filtros (`matrix_id`, `parent_id`, `active`).
- `GET /api/v1/tracking/topics/{id}`: Detalle de un tema.
- `POST /api/v1/tracking/topics`: Crear tema o subtema jerárquico.
- `PATCH /api/v1/tracking/topics/{id}`: Modificar tema.
- `DELETE /api/v1/tracking/topics/{id}`: Eliminar tema (en cascada a subtemas).

### Entidades Vigiladas (`/api/v1/tracking/entities`)
- `GET /api/v1/tracking/entities`: Listado paginado (`limit`, `offset`, `entity_type`, `active`).
- `GET /api/v1/tracking/entities/{id}`: Detalle de entidad.
- `POST /api/v1/tracking/entities`: Registrar entidad (persona, organización, institución, publicación).
- `PATCH /api/v1/tracking/entities/{id}`: Modificar entidad.
- `DELETE /api/v1/tracking/entities/{id}`: Eliminar entidad.

### Asociación Entidad ↔ Tema
- `POST /api/v1/tracking/entities/{entity_id}/topics/{topic_id}`: Asociar tema a entidad (`is_primary`, `priority`, `notes`).
- `DELETE /api/v1/tracking/entities/{entity_id}/topics/{topic_id}`: Desvincular tema de entidad.
- `GET /api/v1/tracking/entities/{entity_id}/topics`: Consultar temas asociados a una entidad.

---

## 15. Bloque 7B: Gemini Developer API — Primer Pipeline Real de IA

El **Bloque 7B** integra la **Gemini Developer API** como primer proveedor real de análisis en HITCHINGS,
construye el pipeline de dos etapas (triage → deep analysis) y establece la infraestructura de benchmark controlado.

### Decisión Arquitectónica Definitiva

```
HITCHINGS (VPS / Dokploy)
      ↓
Gemini Developer API (Google AI Studio)
      ↓
GEMINI_API_KEY (Variable de entorno secreta)
```

- **Proveedor:** Google Gemini Developer API.
- **Autenticación:** API Key (`GEMINI_API_KEY`) inyectada como variable de entorno secreta en Dokploy/VPS.
- **NO Vertex AI:** Se descarta deliberadamente Vertex AI, Application Default Credentials (ADC), IAM de Google Cloud y proyectos de GCP para la ejecución del aplicativo.
- **Despliegue futuro:** GitHub → Dokploy → VPS → Gemini Developer API.
- **Módulo 2 (futuro):** Seguirá la misma arquitectura de Gemini Developer API con API Key.

### Modelo y Pricing

| Parámetro | Valor |
|---|---|
| Modelo | `gemini-3.8-flash` |
| SDK | `google-genai >= 1.16.0` (inicializado con `api_key=GEMINI_API_KEY`) |
| Structured Output | Sí (`TriageAnalysisResult` / `DeepAnalysisResult`) |
| Thinking / Reasoning | Nativo (`thinking_level="low"` para triage, `"medium"` para deep) |
| Pricing Input (oficial) | **$0.75 / 1M tokens** |
| Pricing Output (oficial) | **$3.75 / 1M tokens** (incluye tokens de razonamiento) |
| Context Window | 1.000.000 tokens |

### Configuración (`.env`)

```bash
# Provider (disabled por defecto — fail-closed)
ANALYSIS_PROVIDER=disabled

# Gemini Developer API
GEMINI_API_KEY=tu_api_key_aqui
GEMINI_MODEL=gemini-3.8-flash

# Thinking levels por etapa
ANALYSIS_TRIAGE_THINKING_LEVEL=low
ANALYSIS_DEEP_THINKING_LEVEL=medium

# Límites de caracteres de entrada (salvaguarda anti-truncación)
ANALYSIS_TRIAGE_MAX_INPUT_CHARS=250000
ANALYSIS_DEEP_MAX_INPUT_CHARS=250000

# Budget benchmark (USD)
ANALYSIS_BENCHMARK_MAX_USD=1.00

# Pricing configurable
GEMINI_INPUT_USD_PER_MILLION_TOKENS=0.75
GEMINI_OUTPUT_USD_PER_MILLION_TOKENS=3.75
```

### Pipeline de Dos Etapas

```
Entry
  ↓
TRIAGE (thinking_level=low, max_output=512)
  → relevance_score (0-100)
  → relevance_status: not_relevant / uncertain / relevant
  → topic_codes + primary_topic_code
  → reason (en castellano)
  ↓
Si not_relevant / uncertain → EntryAnalysis completed (sin deep)
Si relevant:
  ↓
DEEP ANALYSIS (thinking_level=medium, max_output=2048)
  → summary (150-300 palabras en castellano)
  → key_points (3-6 puntos en castellano)
  ↓
EntryAnalysis completed
```

### Semántica de Fallos

| Escenario | EntryAnalysis | Triage Call | Deep Call |
|---|---|---|---|
| Triage falla | `failed` | `failed` | no ejecutada |
| Deep falla (triage OK) | `failed` | `completed` | `failed` (datos de triage preservados) |
| Topic inventado / no matriz | `failed` | `failed` | no ejecutada |

### Benchmark Controlado

El benchmark se ejecuta bajo doble seguro:
1. `ANALYSIS_PROVIDER=gemini_api`
2. Flag explícito `--confirm-real-calls`

```bash
# Dry-run (100% no facturable — muestra preflight y selección determinista):
python -m scripts.run_analysis_benchmark

# Ejecución real (requiere GEMINI_API_KEY):
python -m scripts.run_analysis_benchmark --confirm-real-calls [--max-usd 1.00]
```

**Muestra determinista:** 5 Entries más recientes por Source (20 en total).
**Hard stop presupuestario:** Si el coste acumulado alcanza o supera `--max-usd` antes de procesar una Entry, el benchmark se detiene limpiamente.

---

## 16. Source Sufficiency y Enriquecimiento Selectivo de Fuentes (Bloque 7D)

Para garantizar un análisis riguroso y fundamentado (*grounded*), HITCHINGS incorpora un mecanismo determinista de evaluación de suficiencia documental (`SourceSufficiencyService`) previo al procesamiento por LLM.

### Niveles de Suficiencia (`SourceSufficiencyLevel`):
1. **`full` (Suficiencia Completa):** El documento contiene el texto íntegro oficial de la resolución o noticia:
   - Sentencias y conclusiones completas de CURIA extraídas de InfoCuria.
   - Notas de prensa y comunicados editoriales completos de la CNMC y de la Comisión Europea.
   - Texto digital íntegro de resoluciones judiciales del CAT extraído de su PDF oficial.
2. **`partial` (Suficiencia Parcial / Sumario Oficial Sustantivo):** Contiene un sumario procesal oficial redactado por el emisor que incluye hechos, partes, fechas preclusivas o pronunciamientos jurídicos concretos (ej. resúmenes procesales del CAT de 150 a 7.000 caracteres sobre conferencias de gestión procesal, órdenes paraguas o certificaciones de acciones de clase).
3. **`insufficient` (Insuficiente):** El documento carece de cuerpo de texto (`content` vacío) o contiene exclusivamente un texto procesal genérico de una línea (ej. *"Ruling of the Tribunal on costs."* o *"Judgment of the Tribunal."*) sin hechos, fechas ni fundamentación sustantiva.

### Estrategia de Enriquecimiento Selectivo de CAT:
Para la fuente judicial del Competition Appeal Tribunal (CAT), se aplica una política escalonada de adquisición de contenido:
```
1. Sumario HTML oficial sustantivo (Prioridad 1)
       ↓ (si es 'insufficient' o vacío)
2. Descarga del PDF oficial enlazado en raw_metadata['judgment_pdf_url']
       ↓
3. Extracción de capa de texto digital nativa (vía pypdf)
       ↓
4. NO OCR (se rechaza el procesamiento si no existe capa de texto digital)
```

### Justificación de la Estrategia:
- **Reducción de alucinaciones y no-grounding:** Evita que el modelo intente sintetizar un fallo o asignar responsabilidades a partir de titulares vacíos o avisos procesales genéricos de 30 caracteres.
- **Máxima fidelidad documental:** Todo hecho, cuantía, fecha o criterio reflejado en el análisis procede directamente del texto oficial suministrado.
- **Preservación estricta de procedencia y trazabilidad (Bloques 7D.1 y 7E):** 
  - `Entry.content_hash`: Es la identidad canónica de deduplicación de ingesta (`SHA256(clean_url | clean_title | clean_excerpt)`). Permanece **invariable** ante enriquecimientos de contenido (PDF), garantizando que las futuras ingestas reconozcan la entrada como ya existente sin crear duplicados.
  - `EntryAnalysis.entry_content_hash`: Es el hash criptográfico de la versión textual de la entrada (`SHA256(clean_title | clean_content)` en el momento del análisis). Permanece **inmutable** en el histórico. No debe confundirse con un hash de todo el request exacto enviado a Gemini (el cual incluye además prompt del sistema, snapshot de la matriz, fuente, fecha, URL, configuración, etc.). La reproducibilidad integral del análisis depende conjuntamente de `entry_content_hash`, `matrix_snapshot_hash`, `prompt_version_id`, `provider`, `model`, `pipeline_version` y `call_metadata`.
  - **Detección de análisis obsoletos (*stale*):** Se evalúa dinámicamente comparando el hash del contenido actual de la entrada con el hash registrado en el análisis histórico: `compute_analysis_input_hash(entry) != analysis.entry_content_hash`. Si difieren (como en las 6 entradas de CAT enriquecidas con PDF), el sistema identifica de forma determinista que existe un contenido más rico disponible para re-análisis.

---

## 17. Pipeline de Grounding Estricto v3 y Políticas de Auditoría e Inmutabilidad (Bloques 7E y 7E.1)

### 1. Grounding Estricto y Verificación Determinista de Citas (`GroundingValidator`)
El pipeline v3 (`observatory_triage:v3` y `observatory_deep_analysis:v3`) incorpora esquemas Pydantic con citas textuales obligatorias (`GroundingEvidence`). Cada evidencia extraída por el LLM se somete a una verificación determinista estricta en código:
- **Cero tolerancia algorítmica:** No se emplean modelos, embeddings ni coincidencias difusas.
- **Normalización simétrica de texto:** Unicode NFKC, comillas, guiones tipográficos, saltos de línea y normalización de guiones de corte de línea procedentes de PDFs (`(\w)\s*-\s*(\w)` $\to$ `\1-\2`).
- **Política de fallos:** Cualquier discrepancia o alteración sutil (ej. sustituir *"with the result that"* por *"It follows that"*) dispara un `AnalysisGroundingError`, marcando el `AnalysisCall` y el `EntryAnalysis` con `status="failed"`.

### 2. Contrato de Disponibilidad de Campos en el Input Real (`get_allowed_evidence_fields`)
Una evidencia solo es jurídicamente válida si el campo de origen (`source_field`) fue **efectivamente suministrado al modelo** en el prompt de esa ejecución:
- Si `Entry.content` está presente, `_build_content_section()` envía exclusivamente el contenido íntegro; el extracto (`excerpt`) **no** se envía en el prompt. En consecuencia, cualquier cita con `source_field="excerpt"` es rechazada automáticamente por el validador, impidiendo que el LLM justifique decisiones con campos no contenidos en su ventana de contexto.
- Si `Entry.content` está ausente o vacío pero existe `Entry.excerpt`, el extracto sí forma parte del input y `source_field="excerpt"` es admitido.
- `title` siempre está presente en el input. `raw_metadata` no constituye una fuente admitida de citas de evidencia sustantiva.

### 3. Política Infranqueable de No-Eliminación de Llamadas de IA
Toda llamada real facturable a un proveedor de IA constituye un hecho histórico e inmutable de auditoría:
- **Prohibición de borrado:** Ninguna llamada real a un LLM puede eliminarse de la base de datos PostgreSQL (`AnalysisCall`, `EntryAnalysis`, `EntryAnalysisTopic`), independientemente de que se trate de un smoke test, un benchmark, una prueba de validación, una llamada fallida o un reintento.
- **Trazabilidad:** Las llamadas pueden clasificarse mediante metadatos (`call_metadata`: `validation_run`, `benchmark`, `smoke_test`, `obsolete`, `superseded`), pero su registro físico permanece inalterable.
- **Terminología y Contabilidad de Costes (Cost Accounting):**
  - **Database Recorded Estimated Cost:** Suma actual de `AnalysisCall.estimated_cost_usd` en PostgreSQL ($0.268197 tras Bloque 7E). Todos los valores de coste almacenados internamente son **estimaciones** computadas a partir de `usage_metadata` (tokens de entrada, salida y razonamiento) multiplicados por las tarifas configuradas.
  - **Reconstructed Historical Estimated Cost:** Coste estimado acumulado de todas las llamadas API externas reales realizadas en el proyecto ($0.484013), incluyendo las corridas de desarrollo depuradas previamente.
  - **Fuente Definitiva:** La facturación consolidada de Google Cloud / Google AI Studio es la única fuente jurídicamente vinculante y definitiva para conciliación contable externa.

### 4. Inmutabilidad Absoluta de Versiones de Prompts (`AnalysisPromptVersion`)
- Toda versión de prompt registrada en PostgreSQL es estrictamente inmutable en todos sus campos materiales: `system_prompt`, `user_prompt_template`, `response_schema_version`, `stage` y `config` (`max_output_tokens`, `thinking_level`, `temperature`, esquemas de structured output).
- `scripts/seed_analysis_prompts.py` evalúa la identidad material de cada versión existente; si detecta cualquier discrepancia, aborta con `PromptVersionImmutabilityError` en lugar de realizar una actualización silenciosa en base de datos.
- Las versiones `v1`, `v2` y `v3` están permanentemente congeladas. Cualquier modificación futura (instrucciones, tokens, esquemas) requiere una nueva versión (`v4`).

### 5. Aislamiento Estricto de Tests y Protección Fail-Closed (Bloque 7E.2)
- **Aislamiento de base de datos:** La suite de pruebas automatizadas (`pytest`) opera de manera 100% aislada sobre una base de datos SQLite en memoria (`sqlite:///:memory:`). Las pruebas nunca escriben ni modifican las tablas de PostgreSQL de desarrollo o producción.
- **Guarda Fail-Closed (`verify_test_db_url_is_safe`):** Un fixture con ámbito de sesión inspecciona la URL de conexión antes de ejecutar cualquier prueba; si detecta una URL que no contenga `:memory:` o `test`, aborta la suite de pruebas inmediatamente antes de emitir cualquier comando DDL/DML, sin exponer contraseñas ni credenciales.
- **Diferenciación ontológica:** Los artefactos sintéticos creados en tests (mocks, llamadas simuladas) no constituyen llamadas reales ni computan en el ledger de auditoría ni en las estimaciones de costes.

### 6. Registro de Incidencia de Desarrollo (Bloque 7E)
Durante el desarrollo inicial del Bloque 7E, antes de formalizar la regla de congelación absoluta, se ejecutó una corrida de validación inicial con `observatory_deep_analysis:v3` configurado con `max_output_tokens: 2048`. Al detectarse que el razonamiento del modelo (`thinking` en nivel `medium`) consumía parte de ese presupuesto y truncaba la salida en documentos extensos, se ajustó la configuración en la base de datos a `max_output_tokens: 4096` antes de relanzar la validación final. Dicha mutación en desarrollo queda documentada como antecedente técnico que motivó el endurecimiento definitivo de la regla de inmutabilidad y la política de no-borrado de auditoría.

---

---

## 18. Pipeline de Robustez de Evidencia v4 y Validación de Livronsa (Bloque 7F)

### 1. Protocolo Extract-First y Preferencia de Cláusulas Cortas
Tras evidenciarse en el Bloque 7E que el modelo podía incurrir en paráfrasis sutiles de una sola palabra al intentar citar oraciones compuestas extensas (caso *Livronsa* en v3), el Bloque 7F implementó los prompts v4 (`observatory_triage:v4` y `observatory_deep_analysis:v4`) bajo dos principios rectores:
1. **Protocolo Extract-First (Citar primero, analizar después):** El LLM recibe instrucciones terminantes de localizar y extraer la cita literal exacta del texto fuente antes de redactar la justificación de triage, el resumen o los puntos clave.
2. **Preferencia por Cláusulas Cortas (5 a 25 palabras):** Instrucción específica para seleccionar proposiciones o incisos continuos y precisos (orientativamente entre 20 y 180 caracteres), evitando unir fragmentos discontinuos, párrafos completos o cadenas de subordinadas que multiplican el riesgo de desajuste.
3. **Cero Relajación del `GroundingValidator`:** El validador determinista de citas no sufrió ninguna relajación ni tolerancia difusa; mantuvo su verificación estricta carácter a carácter.

### 2. Resultados de la Validación Controlada en *Livronsa* (C-60/25)
La validación se ejecutó exclusivamente sobre la entrada de *Livronsa* (`27c1a107-ebfe-40d0-ba9e-d7d399c3c565`, 29.077 caracteres) mediante el script `scripts/run_v4_livronsa_validation.py`:

| Dimensión | Livronsa v2 | Livronsa v3 | Livronsa v4 (Bloque 7F) |
| :--- | :--- | :--- | :--- |
| **Pipeline Version** | `v2` | `v3` | `v4` |
| **Estado del Análisis** | `completed` | `failed` | **`completed`** |
| **Score de Relevancia** | 95/100 (`relevant`) | 95/100 (`relevant`) | **95/100 (`relevant`)** |
| **Confianza** | 0.95 | 0.95 | **0.98** |
| **Verificación de Citas** | N/A (sin citas) | Falló en cita 1 de deep | **100.0% (10/10 citas verificadas)** |
| **Citas Triage** | 0 | 2 | **2 citas** (140 y 205 caracteres) |
| **Citas Summary** | 0 | 3 | **3 citas** (105, 196 y 205 caracteres) |
| **Citas Key Points** | 0 | 4 | **5 citas** (todas verificadas) |
| **Media caracteres / cita** | N/A | 321.4 chars | **205.2 chars** (min=105, max=286) |
| **Media palabras / cita** | N/A | 48.2 words | **31.9 words** (min=18, max=49) |
| **Llamadas API** | 2 | 2 | **2 (1 triage + 1 deep)** |
| **Coste Incremental** | $0.019777 | $0.018243 | **$0.023601** (presupuesto máx: $0.05) |

### 3. Preservación del Histórico y Contabilidad
- La entrada histórica fallida de *Livronsa* v3 y el análisis v2 permanecen intactos en la base de datos PostgreSQL con propósitos de auditoría e investigación retrospectiva.
- Total de prompts registrados en PostgreSQL: **8** (v1: 2, v2: 2, v3: 2, v4: 2), todos inmutables.
- Total de llamadas registradas en PostgreSQL: **46** (45 completadas, 1 fallida v3).

---

## 19. Compatibilidad de Resultados, Topics Canónicos y Plan de Baseline v4 (Bloque 7G)

### 1. Contrato Invariable de `EntryAnalysis.key_points`
Se establece de forma terminante y retrocompatible el contrato de datos para los puntos clave del análisis:
- **`EntryAnalysis.summary`:** Texto final narrativo resumido (150–300 palabras).
- **`EntryAnalysis.key_points`:** Lista estricta de cadenas de texto (`list[str]`). No duplica la estructura de citas.
- **Evidencia estructurada (`grounding_evidence`):** Se preserva íntegramente en el Structured Output de auditoría dentro de `AnalysisCall.raw_response["result"]["key_points"]` (con sus campos `point` y `evidence`).
- **Retrocompatibilidad de API:** Las respuestas Pydantic (`EntryAnalysisResponse` y `EntryAnalysisDetailResponse`) incorporan un validador que normaliza de forma transparente cualquier representación histórica en formato `list[dict]` a `list[str]`, garantizando un contrato público estable en `GET /api/v1/entry-analyses/{id}`.

### 2. Visión Canónica de Temas (`TopicCanonicalizationService`)
La auditoría reveló que en 20 de los 30 análisis históricos (66,7%), el modelo asignó concurrentemente categorías generales (padres) y específicas (hijos), tales como `private_enforcement` junto con `damages_actions`.
- **Principio de Auditoría:** `EntryAnalysisTopic` se mantiene inmutable para conservar exactamente la clasificación original emitida por el LLM.
- **Regla de Canonicidad:** Cuando una categoría específica (descendiente) está seleccionada, la categoría general (ancestro) se elimina de `canonical_topics` por subsunción lógica. Si solo se seleccionó la categoría general sin descendientes, se preserva íntegramente. Soporta profundidad arbitraria ($Padre \to Hijo \to Nieto$).
- **Resolución de Tema Principal Canónico (`canonical_primary`):** Si el tema primario original era un ancestro eliminado, se resuelve deterministamente entre sus descendientes seleccionados mediante: (1) mayor `confidence`; (2) menor `priority` / `display_order`; (3) `topic.code ASC`.
- **Expansión de Filtros (`expand_topic_filter`):** Las consultas y filtros por tema padre se expanden automáticamente para incluir todos sus descendientes, garantizando que búsquedas de nivel superior encuentren todas las entradas etiquetadas a nivel hoja.

### 3. Algoritmo de Selección de Análisis Vigente (`CurrentAnalysisService`)
Un análisis se considera vigente (*current production analysis*) para una entrada si:
1. `status == "completed"`.
2. `entry_content_hash == compute_analysis_input_hash(entry)` (corresponde a la versión textual actual).
3. Entre múltiples candidatos válidos, se selecciona la versión de pipeline numéricamente más reciente (`v4 > v3 > v2 > v1`), desempatando por fecha de creación descendente (`created_at DESC`).
4. Los análisis fallidos (*failed*) o desfasados por enriquecimiento posterior (*stale*) se preservan en el histórico pero nunca se seleccionan como vigentes.

### 4. Planificador de Baseline Homogéneo v4 (`scripts/plan_v4_backfill.py`)
Se implementó un planificador en modo estrictamente **dry-run** (sin llamadas a Gemini) que diagnostica el universo de las 80 entradas:
- **Con análisis v4 vigente:** 1 entrada (*Livronsa*).
- **Pendientes de baseline v4:** 79 entradas (59 nunca analizadas + 20 analizadas previamente sin v4).
- **Volumen textual pendiente:** 1.871.834 caracteres (65 texto completo, 14 resúmenes oficiales, 0 insuficientes).
- **Estimación dual de costes y límite de seguridad:** Véase Bloque 7G.1.

---

## 20. Auditoría de Pricing, Estimación Dual y Diagnóstico de IDs (Bloque 7G.1)

### 1. Auditoría de Tarifas de Gemini y Corrección de Divergencia
- **Tarifas reales configuradas:** Se verificó que `Settings` en `app.core.config` y el cálculo de costes en `GeminiAPIProvider` han utilizado en todo momento las tarifas oficiales de **$0.75 / 1M tokens de entrada** y **$3.75 / 1M tokens de salida** (que rigen hasta el 2026-12-31, duplicándose a $1.50 / $7.50 a partir del 2027-01-01).
- **Auditoría del ledger de llamadas:** Se recalcularon las 46 llamadas históricas persistidas en PostgreSQL con dichas tarifas, verificando que el total registrado de **$0.291798** coincide exactamente con la fórmula configurada (discrepancia máxima de $0.0000005 por llamada atribuible a redondeo flotante). La mención previa a $0.15/$0.60 fue únicamente una errata narrativa en un texto descriptivo del planner sin impacto en la contabilidad.
- **Fuente única de verdad:** `scripts/plan_v4_backfill.py` se actualizó para obtener las tarifas dinámicamente de `get_settings()` sin ningún valor quemado (*hardcoded*).

### 2. Modelo Dual de Estimación para las 79 Entradas Pendientes
El planificador incorpora dos metodologías complementarias:
1. **Método A (Coste Histórico Observado por Fuente):**
   - LOW (35% relevancia): $0.6657 (~107 llamadas)
   - EXPECTED (48% relevancia): $0.7525 (~117 llamadas)
   - HIGH (65% relevancia): $1.0392 (~130 llamadas)
2. **Método B (Modelo Fino por Conteo Textual y Tokens):**
   - LOW (35% relevancia, 1500 out tokens): $0.7932
   - EXPECTED (48% relevancia, 2000 out tokens): $0.9949
   - HIGH (65% relevancia, 2800 out tokens): $1.3603
- **Explicación de la divergencia (+32% en EXPECTED):** El benchmark histórico de 20 entradas incluía resoluciones del CAT breves (~200 caracteres de resumen). Sin embargo, entre las 59 entradas pendientes no analizadas figuran 5 sentencias del CAT EWCA enriquecidas con texto íntegro (55.000 a 84.000 caracteres, ~15k-22k tokens cada una). El Método B modela directamente esta carga textual real, por lo que constituye la previsión más precisa.
- **Proyección a tarifas 2027 ($1.50 / $7.50):** LOW: $1.5863, EXPECTED: $1.9899, HIGH: $2.7206.
- **Límite de seguridad duro (Fail-Closed Budget):** Se fija un techo de seguridad de **$2.0000** para autorizaciones futuras de ejecución del backfill.

### 3. Aclaración Inequívoca de Identificadores (IDs)
Se auditó y diferenció la semántica de identificadores para evitar confusiones de diagnóstico:
- **`entry_id: c9e2a041-b66e-4e67-9d1b-090b6bfc6943`:** Noticia de la Comisión Europea ("Commission adopts EU Guidelines on exclusionary abuses of dominance"). Su análisis histórico de smoke test v2 es `analysis_id: 5ea42a63-adae-404b-b2e7-77aad0f4f043` (completed, score 95).
- **`entry_id: c119efb6-ecde-48c6-9a04-490becb6f176`:** Resolución CAT 67 (*GLOBAL-365 plc & Another v PayPoint plc & Others*). Sus análisis históricos reales son `analysis_id: 255f9570-e3a9-4656-9da0-f2ad0d0fd1c4` (v2, stale por tener 32 chars) y `analysis_id: 3a16fccc-d1f9-4ab6-9a96-1885bb326c87` (v3, current con 15.522 chars).

---

## 21. Auditoría de Identidad de Inventario y Preflight de Baseline v4 (Bloque 7G.2)

### 1. Auditoría de Identidad en Base de Datos
- **Aclaración definitiva de `c119efb6`:** Se comprobó directamente en PostgreSQL que la entrada `c119efb6-ecde-48c6-9a04-490becb6f176` corresponde inequívocamente a `[2026] CAT 67 | GLOBAL-365 plc & Another v PayPoint plc & Others - Ruling (Costs)` (Case No: `1597/5/7/23`, 15.522 caracteres enriquecidos desde el PDF oficial). La mención previa en texto narrativo a *Dr. Rachael Kent v Apple* (CAT 57 / `ebc678c6`) fue un lapsus narrativo del asistente sin ninguna corrupción en la base de datos PostgreSQL.
- **Auditor de inventario read-only (`scripts/audit_entry_inventory.py`):** Script de verificación exhaustiva que audita las 80 entradas del repositorio sin realizar escrituras ni llamadas a la IA:
  - Comprueba la coherencia de URLs, títulos, fechas, hashes canónicos y suficiencia.
  - Verifica la presencia de identificadores judiciales (citas neutrales, números de caso y nombres de partes) en el texto de las resoluciones de CAT y TJUE/CURIA (normalizando guiones no separables `\u2011`).
  - Control de colisiones y duplicados: 0 colisiones de URL, 0 colisiones de hash de deduplicación, 0 colisiones de título por fuente.
- **Resultado global de preflight:**
  - `IDENTITY_OK`: 80 / 80 (CNMC: 20, CAT: 20, CURIA: 20, EC: 20).
  - `IDENTITY_WARNING`: 0 / 80.
  - `IDENTITY_ERROR`: 0 / 80.
  - Preflight aprobado para autorización futura del backfill v4.

---

---

## 22. Runner Resumible y Backfill Real V4 (Bloque 7H)

### 1. Arquitectura del Runner de Backfill (`scripts/run_v4_backfill.py`)
- **Ejecución estrictamente secuencial y determinista:** Orden de fuentes prioritario: (1) CNMC, (2) European Commission, (3) Competition Appeal Tribunal, (4) CURIA. Ordenación por `published_at DESC nullslast` y `entry.id ASC`.
- **Preflight exhaustivo de 5 fases:**
  1. Auditoría completa de identidad e inventario (`IDENTITY_OK`: 80/80, 0 colisiones).
  2. Comprobación de cero análisis v4 pendientes o en ejecución.
  3. Verificación de configuración de proveedor (`gemini_api`), modelo (`gemini-3.8-flash`), API Key y tarifas oficiales vigentes ($0.75 / $3.75 por 1M tokens).
  4. Verificación de versiones activas de prompts (`observatory_triage:v4`, `observatory_deep_analysis:v4`) y matriz de seguimiento activa (`HITCHINGS-v0.1`).
  5. Verificación de integridad y concordancia numérica de la base de datos PostgreSQL.
- **Guardas fail-closed de presupuesto y parada:**
  - Reserva conservadora pre-call antes de Triage y antes de Deep (piso de $0.0400).
  - Presupuesto duro autorizado: $2.0000; aviso de umbral al 75%: $1.5000.
  - Reglas de parada sistémica: parada inmediata ante fallo de autenticación/API key, ante 2 fallos consecutivos de grounding, ante 3 fallos totales de grounding, o ante 2 fallos consecutivos de parseo estructurado.
  - Manejador de señal `SIGINT` (Ctrl+C) con interrupción limpia entre etapas o llamadas.
  - Soporte para reanudación idempotente mediante `--resume-run-id <UUID>`.

### 2. Resultados de la Ejecución Real del Backfill V4
- **Run ID:** `2c73e471-acc3-40c7-9fc3-77fa1e7d0df1`.
- **Duración total:** 595.6 segundos (~9.93 minutos).
- **Entries procesadas:** 79 de 79 intentadas.
- **Análisis v4 completados:** 76 en este run (+ 1 previo de *Livronsa* = 77 vigentes en el repositorio).
- **Análisis v4 fallidos:** 3 (registrados permanentemente en PostgreSQL; fail-closed sin auto-reintentos):
  - `ada5d125` (*Dr Liza Lovdahl Gormsen v Meta*): `AnalysisGroundingError` en Deep Analysis (paráfrasis no literal en una de las 14 citas; detectado y bloqueado por el validador estricto).
  - `4db3fa9a` (*Elisabetta Sciallis v Fender*): `NoParsedResponse` en Deep Analysis (truncamiento por `max_output_tokens: 4096` consumido por tokens de razonamiento).
  - `14e036d2` (*Mr David Alexander de Horne Rowntree v PRS*): `NoParsedResponse` en Deep Analysis (truncamiento por `max_output_tokens: 4096` consumido por tokens de razonamiento).
- **Llamadas API realizadas:** 110 (79 Triage + 31 Deep Analysis; 107 completadas, 3 fallidas).
- **Consumo de tokens del run:**
  - Input tokens: 739.576 (Triage: 612.265, Deep: 127.311)
  - Visible output tokens: 49.865 (Triage: 22.649, Deep: 27.216)
  - Thought tokens: 45.884 (Triage: 0, Deep: 45.884)
  - Billable output tokens: 95.749 (Triage: 22.649, Deep: 73.100)
- **Métricas de Evidencia y Grounding (Citas literales):**
  - Total de citas extraídas: 391
  - Total de citas validadas por `GroundingValidator`: 391 (100.0% de tasa de verificación).
  - Longitud media: 128.5 caracteres / 19.8 palabras.
  - Citas > 180 caracteres: 72 (18.4%).
  - Citas > 25 palabras: 98 (25.1%).
  - Longitud máxima de cita: 394 caracteres.
- **Contabilidad y Precisión Presupuestaria (Terminología de costes estimados):**
  - Run estimated cost: **$0.913752 USD** (Triage: $0.544139, Deep: $0.369613; frente a previsión EXPECTED de $0.9949 del token-model: 91.8% de precisión).
  - Presupuesto remanente: $1.086248 USD (consumido el 45.7% del límite de $2.0000; nunca se alcanzó el aviso del 75%).
  - DB recorded estimated cost: **$1.205550 USD** (anterior: $0.291798).
  - Reconstructed historical estimated cost: **$1.421366 USD** (anterior: $0.507614).
- **Subsunción Canónica de Taxonomía:**
  - De los 76 análisis completados, 38 presentaban redundancia padre-hijo bruta en los tópicos asignados por el LLM (50.0%).
  - La capa canónica (`canonicalize_analysis_topics`) eliminó el 100% de las redundancias: **0 redundancias canónicas** subsistentes.
- **Distribución Final de Análisis Vigentes (`select_current_analysis` sobre 80 Entries):**
  - `Current v4`: 77 / 80 (96.25%).
  - `Current v3`: 0 / 80.
  - `Current v2`: 0 / 80.
  - `Sin análisis vigente`: 3 / 80 (las 3 fallidas de CAT que nunca antes habían sido analizadas).

---

## 23. Post-Backfill Integrity y Failure Forensics (Bloque 7H.1)

### 1. Auditoría Forense de Fallos
- **UUID real de Livronsa:** `27c1a107-ebfe-40d0-ba9e-d7d399c3c565` (100% íntegra en PostgreSQL con análisis v4 completed). La errata en el texto del informe 7H (`...-1ba7-47ec...`) fue un lapsus narrativo en prosa sin correlato en base de datos.
- **Integridad del Set del Run:** Comprobado inequívocamente: `selected set == attempted set: True`. 79 Entry IDs únicos procesados, 0 duplicados, Livronsa correctamente excluida por tener ya v4 vigente.
- **Forensics Gormsen (`ada5d125`):** 13 de 14 citas validadas al 100%. Una cita de pleito colectivo combinó dos cláusulas del documento en una frase continua de 208 caracteres. Ni el modelo ni el texto contenían `...` ni `…`; la omisión de wildcard literal preserva el rigor estricto del grounding. Clasificación: `RETRY_V4_REASONABLE`.
- **Forensics CAT 56 (`4db3fa9a`) y EWCA 814 (`14e036d2`):** Causa raíz compartida: **MAX_TOKENS / Truncamiento**. El razonamiento interno bajo `thinking_level='medium'` consumió 3.199 y 3.933 tokens respectivamente, dejando presupuesto insuficiente para cerrar el JSON dentro del límite de `max_output_tokens: 4096`. Clasificación: `V4_RETRY_UNLIKELY` sin ajuste previo de configuración (e.g. `max_output_tokens: 8192` o `thinking_level: 'low'`).

### 2. Corrección del Budget Guard
- **Corrección de defecto de reserva fija:** Se eliminó el piso plano de $0.0400 USD.
- **Cálculo dinámico por etapa y entrada:** Funciones `max_estimated_triage_call_cost` y `max_estimated_deep_call_cost` que calculan cotas superiores basadas en la longitud exacta de la entrada, factor de seguridad de tokenización (2.0 chars/token + 20% margen), `max_output_tokens` del prompt y tarifas configuradas.
- **Planner de reintentos read-only:** Creado `scripts/plan_v4_failed_retries.py` para consultar y clasificar entradas fallidas sin realizar llamadas a Gemini ni escrituras.

---

## 24. V5 Capacity Hotfix y Reparación Controlada (Bloque 7H.2)

### 1. Diagnóstico y Hotfix V5
- **Prompts v5 introducidos:**
  - `observatory_triage:v5`: Materialmente idéntico a v4 (`thinking_level='low'`, `max_output_tokens=1024`).
  - `observatory_deep_analysis:v5`: Materialmente idéntico a v4 pero con **ampliación de capacidad a `max_output_tokens=8192`** (`thinking_level='medium'`).
- **GroundingValidator inalterado:** Cero relajación de reglas de grounding. Búsqueda verbatim continua estricta preservada sin comodines ni elipsis.
- **Whitelist estricta de 3 entradas:** `ada5d125` (Gormsen / Meta), `4db3fa9a` (Sciallis / Fender CAT 56), `14e036d2` (Rowntree / PRS EWCA Civ 814).
- **Presupuesto hard:** $0.25 USD con reserva dinámica conservadora (`calculate_conservative_reservation`).

### 2. Resultados de la Ejecución Real (Run ID: `341cf679-6b87-48e3-9be7-8bbaaf66b56f`)
- **Llamadas API realizadas:** 6 (3 Triage + 3 Deep Analysis).
- **Resolución de fallos por capacidad (MAX_TOKENS):**
  - `4db3fa9a` (CAT 56): **Completada con éxito** (score 95, 13 citas 100% verificadas, 3.192 tokens out, coste $0.018180).
  - `14e036d2` (EWCA Civ 814): **Completada con éxito** (score 95, 11 citas 100% verificadas, 3.747 tokens out, coste $0.040540).
  - *El incremento a 8k tokens resolvió definitivamente el truncamiento por razonamiento en deep analysis.*
- **Entrada Gormsen (`ada5d125`):** Falló en validación de grounding debido a un salto de página con encabezado judicial interpuesto en medio de una frase larga (`AnalysisGroundingError`). Preservada como fallida según el protocolo fail-closed.
- **Métricas de Evidencia (Entradas completadas v5):**
  - Citas totales extraídas y validadas: 24 / 24 (100.0% de verificación estricta).
  - Longitud media: 179.9 caracteres / 29.4 palabras.
- **Coste del Run de Reparación:** **$0.100588 USD** (muy inferior al límite de $0.2500).
- **Estado Actual del Observatorio:**
  - DB recorded estimated cost: **$1.306138 USD**
  - Reconstructed historical estimated cost: **$1.521954 USD**
  - Cobertura de análisis vigentes (`select_current_analysis` sobre 80 Entries):
    - `Current v5`: 2
    - `Current v4`: 77
    - `Total con análisis de producción vigente`: **79 / 80 (98.75%)**
    - `Sin análisis vigente`: 1 (Gormsen)

> [!NOTE]
> **Aclaración sobre el coste histórico reconstruido:** La diferencia fija de $0.215816 USD entre el coste registrado en BD y el coste histórico estimado reconstruido procede de llamadas de desarrollo eliminadas durante la validación de prompts v3 en el **Bloque 7E** (no en 7C/7D).

---

## 25. V6 Contiguous-Evidence Hotfix y Reparación Final de Gormsen (Bloque 7H.3)

### 1. Diagnóstico y Principio Técnico
- **Causa raíz en Gormsen v5:** La resolución judicial de la Court of Appeal (*Dr Liza Lovdahl Gormsen v Meta*, `ada5d125`) contiene en el carácter 83.582 un salto de página físico con encabezado judicial interpuesto (`Judgment Approved by the court for handing down. Meta Platforms Inc & Ors v Gormsen`). En v5, el modelo extrajo una proposición jurídica omitiendo dicha cabecera intermedia, generando una cita discontinua de 235 caracteres que el `GroundingValidator` rechazó con `AnalysisGroundingError`.
- **Principio:** No relajar el `GroundingValidator`, no admitir comodines ni elipsis, y no alterar `Entry.content`. Toda cita debe ser un span literal continuo presente en el texto fuente suministrado.
- **Prompts v6 introducidos:**
  - `observatory_triage:v6`: Materialmente idéntico a v5 salvo versión 6.
  - `observatory_deep_analysis:v6`: Mantiene capacidad de 8k tokens (`max_output_tokens=8192`, `thinking_level='medium'`) e incorpora la **Regla 7 genérica de span continuo y exclusión de artefactos de salto de página**:
    - Si una frase útil atraviesa un salto de página o encabezado físico intercalado, el modelo debe extraer una cita más corta íntegramente ANTES del artefacto, íntegramente DESPUÉS, o seleccionar otro span continuo independiente.
    - Regla 100% genérica sin sobreajuste (no menciona a Gormsen, Meta, Devenish ni textos de cabecera específicos).

### 2. Resultados de la Ejecución Real (Run ID: `541e0b5b-0da1-4458-9ccb-92963410b464`)
- **Llamadas realizadas:** 2 (1 Triage v6 + 1 Deep Analysis v6).
- **Resultado Gormsen v6:** **`completed`**
  - Relevance score: `95 / 100` (`relevant`).
  - Cita sobre Devenish: El modelo aplicó la Regla 7 y extrajo la cita continua situada íntegramente antes del artefacto: `'It is strongly arguable that Devenish is not authority for the proposition that user damages do not apply in competition law cases.'` (131 caracteres, 100% exacta).
  - Tasa de verificación de evidencias: **12 / 12 citas verificadas al 100%** (3 triage, 4 summary, 5 key points). Cero citas discontinuas.
  - Finish reason: `STOP` (sin truncamiento).
  - Consumo: 21.545 in / 400 out en Triage; 20.583 in / 4.552 out en Deep (0 thought, 4.552 visible).
  - Coste del run: **$0.050166 USD** (Triage: $0.017659, Deep: $0.032507; muy inferior al límite de $0.12 USD).

### 3. Cierre del Baseline del Observatorio (80 / 80 Entries)
Con la reparación de Gormsen, el observatorio alcanza **cobertura total**:
- `select_current_analysis` sobre las 80 entradas:
  - **`Current v6`:** 1 (Gormsen v Meta)
  - **`Current v5`:** 2 (Sciallis v Fender CAT 56, Rowntree v PRS EWCA Civ 814)
  - **`Current v4`:** 77
  - **`Current v3 / v2`:** 0
  - **`Sin análisis vigente`:** **0** (100% cubierto).
- **Recomendación para futuras ingestas:** El pipeline recomendado para analizar nuevas entradas a partir de este punto es **v6**, ya que combina el protocolo extract-first (v4), la capacidad ampliada de 8.192 tokens (v5) y la protección contra artefactos de paginación (v6).
- **Contabilidad final de costes:**
  - DB recorded estimated cost: **$1.356304 USD**
  - Reconstructed historical estimated cost: **$1.572120 USD**

---

---

## 26. BLOQUE 8A — API de Consumo del Observatorio para el Portal Cliente

En este bloque se ha construido la capa API orientada al producto final que consumirá el portal frontend del cliente, bajo el namespace `/api/v1/observatory`.

### 1. Frontera Estricta entre API Técnica y API de Producto
El cliente final no consume directamente tablas internas de auditoría ni modelos de debug:
- **Oculto al cliente:** `pipeline_version`, `prompt_version`, `entry_content_hash`, `matrix_snapshot`, `matrix_snapshot_hash`, `raw_response`, llamadas individuales de `AnalysisCall`, costes en USD, tokens, modelos de IA y detalles de validación de grounding (`GroundingValidator`, `verified_pct`, etc.).
- **Expuesto al cliente:** `Entry` + `Current Analysis` + `Canonical Topics` + `Source` + `Evidence` limpia estructurada.

### 2. Semántica de Análisis Vigente (`select_current_analysis`)
- Todos los endpoints del observatorio consumen exclusivamente el análisis vigente resuelto mediante `select_current_analysis()`.
- Se descartan análisis fallidos, análisis obsoletos (*stale*) cuyo hash de contenido no coincida, y si existen múltiples versiones válidas, se selecciona la versión más reciente del pipeline (v6 > v5 > v4).
- Las entradas sin análisis vigente quedan excluidas del listado de producto (y devuelven HTTP 404 en la vista de detalle).

### 3. Endpoints Implementados

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/v1/observatory/entries` | Listado paginado de publicaciones con análisis vigente, soporte de filtros avanzados y ordenación. |
| `GET` | `/api/v1/observatory/entries/{entry_id}` | Detalle completo de una publicación con evidencias textuales limpias (verbatim) derivadas del análisis deep (o triage fallback). |
| `GET` | `/api/v1/observatory/sources` | Catálogo de fuentes disponibles para filtros, con recuento de publicaciones y fecha de última publicación. |
| `GET` | `/api/v1/observatory/topics` | Taxonomía jerárquica activa de temas (padres y subtemas) de la matriz de seguimiento vigente. |
| `GET` | `/api/v1/observatory/dashboard` | Indicadores clave (KPIs), tendencias de los últimos 7 y 30 días, distribución de relevancia, top temas canónicos, top fuentes y 5 publicaciones relevantes más recientes. |

### 4. Filtros y Búsqueda
- **Paginación:** `limit` (default 20, máx 100), `offset` (default 0). Validación estricta con HTTP 422.
- **Ordenación:** `sort_by` (`published_at`, `relevance_score`) y `sort_order` (`asc`, `desc`). Whitelist estricta.
- **Búsqueda textual (`q`):** Búsqueda *case-insensitive* completa (OR) a través de `title`, `excerpt`, `summary` y `key_points`.
- **Rango temporal:** `date_from` y `date_to` (formato ISO `YYYY-MM-DD`, inclusivo), con normalización determinista a UTC tanto para datetimes con zona horaria como *naive*.
- **Fuentes:** Filtro por `source_id` único o lista `source_ids`.
- **Relevancia:** Filtro por `relevance_status` (`relevant`, `uncertain`, `not_relevant`) y `min_relevance_score` (0 a 100).
- **Expansión jerárquica de temas (`topic_code`):** Al filtrar por un tema padre (ej. `private_enforcement`), el observatorio expande automáticamente la consulta mediante `expand_topic_code_filter()` para incluir todas las publicaciones etiquetadas con sus subtemas específicos (ej. `damages_actions`, `collective_actions`).

### 5. Taxonomía Canónica en Respuestas
- Para evitar redundancia conceptual, las respuestas eliminan categorías padre cuando un subtema específico de la misma rama ha sido asignado.
- Si solo existe la categoría padre sin descendientes seleccionados, se preserva.
- El contrato de temas cliente se simplifica a `{"code": "...", "name": "..."}`.

### 6. Optimización Anti-N+1
- Las consultas de listado y dashboard utilizan `selectinload` por lotes para `Entry.source`, `Entry.analyses`, `EntryAnalysis.topics` y `EntryAnalysisTopic.topic`.
- Se evita expresamente la carga de `AnalysisCall` en las consultas de listado y dashboard, reservándose exclusivamente para la consulta puntual de detalle (`/entries/{entry_id}`).
- La resolución del análisis vigente y los filtros se efectúan en memoria sobre las entidades precargadas, garantizando semántica exacta en el cómputo del `total` paginado.

---

## 27. BLOQUE 8B.0 — Preparación del Backend para el Portal Web

En este bloque se ha completado la preparación técnica del backend FastAPI para permitir el consumo directo de la API del Observatorio desde navegadores web:

### 1. Configuración de CORS
- Se añadió [`AppCORSMiddleware`](file:///C:/Users/danim/Proyectos/hitchings/app/main.py) sobre FastAPI, controlado dinámicamente mediante la configuración `CORS_ALLOWED_ORIGINS`.
- Soporta múltiples orígenes declarados en variables de entorno (separados por comas o arrays JSON).
- Por defecto, la lista de orígenes permitidos está vacía (`[]`), garantizando que no se abra ningún origen comodín (*wildcard*) no autorizado.
- Se prohíbe explícitamente el uso de comodines (`"*"`) junto con credenciales (`allow_credentials=True`), cumpliendo con los estándares de seguridad web.
- Las peticiones preflight HTTP `OPTIONS` devuelven cabeceras completas `Access-Control-Allow-*` con código `200 OK` para orígenes autorizados, y `400 Bad Request` para orígenes no permitidos.

### 2. Documentación del Contrato API para Frontend
- Se creó la guía de integración compacta [`docs/observatory_api_contract.md`](file:///C:/Users/danim/Proyectos/hitchings/docs/observatory_api_contract.md) orientada exclusivamente al desarrollo del portal cliente:
  - Mapeo exacto entre pantallas frontend (Dashboard, Observatorio, Ficha de Detalle) y endpoints del backend.
  - Ejemplos de uso de los filtros combinados (`relevance_status`, `min_relevance_score`, `topic_code` jerárquico, `q`, `date_from/date_to`, `sort_by/sort_order`).
  - Formato JSON de respuestas y códigos de error estandarizados (`404 Not Found`, `422 Unprocessable Entity`).

---

## 28. BLOQUE 8B.1 — Portal Cliente MVP: Estructura, Diseño y Conexión API

En este bloque se ha construido el portal frontend cliente para el Observatorio de Competencia, diseñado específicamente para profesionales jurídicos:

### 1. Arquitectura y Stack Tecnológico
- **SPA Moderna:** React 18 + TypeScript + Vite + Tailwind CSS + Lucide Icons.
- **Enrutamiento:** React Router v6 con 3 rutas principales:
  - `/` — **Cuadro de Mando:** KPIs del fondo documental (total, relevantes, en revisión, descartadas, actividad 7d/30d), materias jurídicas activas con barras de distribución, fuentes monitorizadas y últimas 5 resoluciones relevantes.
  - `/observatorio` — **Explorador Jurisprudencial:** Listado completo con búsqueda textual (`q`), filtros combinados (calificación de relevancia, puntuación mínima, organismo emisor, materia jurídica con expansión en árbol, rango de fechas), ordenación bidireccional y paginación.
  - `/observatorio/:entryId` — **Ficha Jurídica:** Resumen ejecutivo de alto contraste, puntos clave estructurados y bloque de **Evidencias Textuales Verificadas** (citas literales de la resolución oficial).
- **Diseño Visual:** Estética sobria, institucional y legible, con paleta navy/slate y acentos dorados legales (`legal-gold`).

### 2. Capa de Datos: Reglas Fail-Closed y Protección en Producción
- **Servicio Unificado:** [`src/services/observatoryApi.ts`](file:///C:/Users/danim/Proyectos/hitchings/src/services/observatoryApi.ts) desacopla los componentes de la procedencia de los datos.
- **Fail-Closed Estricto:**
  - Los datos mock se activan **única y exclusivamente** si `VITE_USE_MOCK_DATA=true` de forma explícita.
  - Si `VITE_USE_MOCK_DATA=false` (o no está definido) y `VITE_API_BASE_URL` no está configurada, el cliente **NO** hace fallback silencioso a mock: lanza inmediatamente un error explícito de configuración (`VITE_API_BASE_URL is required when mock data is disabled`), provocando la presentación del `ErrorState` en la interfaz.
- **Protección Antidespliegue de Mock:**
  - Si la aplicación se compila en modo producción (`import.meta.env.PROD === true`) y se intenta forzar mock (`VITE_USE_MOCK_DATA=true`), el servicio lanza un error de seguridad impidiendo la publicación accidental de datos ficticios.
  > [!CAUTION]
  > **Nunca desplegar en producción con mock activado.** Los entornos productivos deben conectarse obligatoriamente a la API real del backend.

### 3. Desarrollo Local Real (Frontend ↔ Backend)
Para ejecutar la plataforma completa en local con datos reales:

**1. Backend (FastAPI):**
```bash
# Configurar CORS para el origen de Vite y arrancar uvicorn
$env:CORS_ALLOWED_ORIGINS="http://localhost:5173"
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**2. Frontend (React / Vite):**
```bash
# Configurar variables de entorno y arrancar Vite
$env:VITE_API_BASE_URL="http://127.0.0.1:8000"
$env:VITE_USE_MOCK_DATA="false"
bun run dev
```

**3. Suite de Tests Frontend (Vitest + React Testing Library):**
```bash
bun run test
```

---

## 29. BLOQUE 8B.2 — Hardening del Frontend y Conexión Real Frontend ↔ Backend

En este bloque se ha ejecutado el hardening de la integración end-to-end entre el frontend y el backend:
1. **Mock Fail-Closed:** Eliminado cualquier fallback silencioso a datos mock.
2. **Protección de Producción:** Bloqueo terminante de datos mock en builds de producción.
3. **Eliminación de Badges Técnicos:** Retirados indicadores técnicos ("Mock Dataset", "API Conectada") de la interfaz de usuario para preservar la sobriedad institucional.
4. **Normalización Terminológica:**
   - Evidencias: adaptadas de "texto oficial" a "documento fuente" para contemplar la diversidad de futuras fuentes (Google News, LinkedIn, prensa jurídica).
   - Enlaces externos: renombrados de "Fuente oficial" a "Publicación original".
   - Estados de relevancia: unificados uniformemente como *Relevante*, *En revisión* y *No relevante* en todas las pantallas.
5. **Hero y Fuentes Dinámicas:** Eliminadas menciones estáticas hardcoded (CAT, TJUE, CNMC, DG Comp) para asegurar escalabilidad ante la incorporación de nuevas fuentes.
6. **Tests Frontend Automatizados:** 14 tests unitarios implementados con Vitest y React Testing Library cubriendo contratos de API, parámetros de query, reglas fail-closed, estados de carga/error/vacío y ausencia de jerga interna de auditoría.
7. **Smoke Test Real End-to-End:** Validación contra la base de datos real confirmando 80 publicaciones, 32 relevantes, 10 en revisión, 38 no relevantes, 22 en *private_enforcement*, paginación por offsets y detalle íntegro verificado en Livronsa y Gormsen.

---

## 30. Funcionalidades Deliberadamente Pendientes

Para respetar la delimitación estricta de fases, en este Bloque 8B.2 **NO** se han implementado:
1. Autenticación de usuarios, login o gestión de sesiones (fase posterior).
2. Modificación de datos analíticos, históricos ni prompts.
3. Scheduler automático en segundo plano.
4. Nuevos endpoints ni alteraciones del contrato API existente.

---

## 31. BLOQUE 8C.1 — Autenticación Privada del Portal Cliente

### Objetivo

Convertir el portal del Observatorio en una aplicación privada: solo usuarios autorizados provistos por el administrador pueden acceder al Dashboard, Observatorio y Detalle.

### Modelo de Seguridad

| Elemento | Decisión |
|---|---|
| Sesiones | Server-side (`auth_sessions` en PostgreSQL). No JWT, no localStorage. |
| Cookie | `HttpOnly=True`, `SameSite=Lax`, `Secure=False` en dev (configurable). |
| Hash de contraseña | Argon2id (`argon2-cffi`), tiempo de hashing ~100ms. |
| Token de sesión | 32 bytes aleatorios vía `secrets.token_bytes`. Se almacena en BD como SHA-256. |
| TTL | Configurable `AUTH_SESSION_TTL_HOURS` (defecto: 24h). |
| Rate limiter | Ventana deslizante en memoria: 5 intentos fallidos / min por IP. |
| Timing-safe | `dummy_verify_password()` se ejecuta siempre aunque el usuario no exista, para mitigar enumeración de usuarios por tiempo de respuesta. |
| Signup | Ninguno. Usuarios solo mediante CLI administrativo. |

### Nuevos Modelos de Base de Datos

```text
users:
  id (UUID PK), email (unique), display_name,
  password_hash (Argon2id), is_active, created_at, last_seen_at

auth_sessions:
  id (UUID PK), user_id (FK users), token_hash (SHA-256),
  created_at, expires_at, revoked_at, last_seen_at
```

Migración: `0005_users_and_auth_sessions`.

### Endpoints de Autenticación

| Método | Ruta | Descripción | Auth |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | Inicio de sesión. Devuelve `UserPublic` y establece cookie HttpOnly. | Público |
| `GET` | `/api/v1/auth/me` | Devuelve el usuario autenticado actual. | Sesión válida |
| `POST` | `/api/v1/auth/logout` | Revoca la sesión en BD y elimina la cookie del navegador. | Sesión válida |

**Endpoints protegidos:** Todos los endpoints bajo `/api/v1/observatory/*` requieren sesión válida. Si no hay cookie o la sesión está expirada o revocada, devuelven `HTTP 401`.

**Endpoints públicos:** `/health` y `/health/db` permanecen siempre accesibles sin autenticación.

### Gestión Administrativa de Usuarios (CLI Local)

```powershell
# Crear nuevo usuario interactivamente (pide contraseña de forma segura via getpass):
python -m scripts.create_user --email usuario@ejemplo.com --display-name "Nombre Visible"

# Restablecer contraseña y revocar todas las sesiones activas del usuario (via getpass):
python -m scripts.reset_user_password --email usuario@ejemplo.com
```

Ambos scripts son completamente **idempotentes**, operan localmente contra PostgreSQL, exigen entrada interactiva oculta mediante `getpass` y nunca exponen contraseñas en terminal, logs ni argumentos CLI.

### Consideraciones de Despliegue en Producción

#### 1. Arquitectura Same-Site Recomendada
Para producción, el frontend y la API deben desplegarse bajo el **mismo site**, preferiblemente:
- Frontend: `https://portal.example.com`
- API proxied: `https://portal.example.com/api/...`
- O como mínimo, subdominios del mismo registable domain con `AUTH_COOKIE_DOMAIN=.example.com`.

> [!WARNING]
> Evitar desplegar frontend y API en sitios completamente distintos (cross-site), ya que las cookies con directiva `SameSite=Lax` no se transmiten en peticiones asíncronas (`fetch`) cross-site, lo que rompería la autenticación en el cliente.

#### 2. Pre-Production Security Blocker: Endpoints Técnicos
> [!CAUTION]
> **PRE-PRODUCTION SECURITY BLOCKER:**  
> Los endpoints técnicos del backend preexistentes:
> - `/api/v1/entry-analyses/*`
> - `/api/v1/tracking/*`
> - `/api/v1/sources/*`
> - Endpoints de ingesta y administración técnica
> 
> **NO** deben quedar expuestos públicamente en producción. Antes del despliegue final deben protegerse mediante autenticación interna/VPN o segregarse en un gateway administrativo independiente.

#### 3. Especificación y Limitaciones del Rate Limiter
- **Implementación:** Ventana deslizante en memoria (*in-memory sliding window*) basada en timestamps `time.time()`.
- **Scope:** Compuesto por IP del cliente + email normalizado (`{client_ip}:{email}`).
- **Ventana y límite:** Máximo 5 intentos fallidos consecutivos en una ventana móvil de 60 segundos (`HTTP 429 Too Many Requests`). Al autenticarse con éxito, el contador se resetea.
- **Limitaciones (Best-Effort):** Al ser estrictamente local al proceso Python, los reinicios del servidor uvicorn limpian la ventana y los workers multiproceso no comparten estado. Para producción distribuida con alta concurrencia queda pendiente hardening con almacenamiento centralizado (ej. Redis / token bucket) o a nivel de reverse proxy.

### Variables de Entorno Adicionales

```bash
# Autenticación (8C.1)
AUTH_COOKIE_NAME=hitchings_session
AUTH_SESSION_TTL_HOURS=24
AUTH_COOKIE_SECURE=false         # true en producción HTTPS
AUTH_COOKIE_DOMAIN=              # vacío en dev; fijar a dominio de prod
```

### Protecciones de Frontend

- **`ProtectedRoute`:** Wrapper de React Router que verifica el estado de autenticación (`checking` → espera, `unauthenticated` → `/login`, `authenticated` → renderiza ruta).
- **`AuthContext`:** Proveedor global con `login`, `logout` y escucha del evento `hitchings:unauthorized` emitido por `observatoryApi` ante cualquier `HTTP 401` en las rutas protegidas.
- **`LoginPage`:** Vista institucional sobria con campo de email y contraseña, retroalimentación de error en caso de credenciales inválidas y redirección automática a la ruta de origen tras el login.
- **`AppLayout`:** Muestra el nombre visible y email del usuario autenticado más el botón de cierre de sesión.
- **`credentials: 'include'`:** Configurado en todos los `fetch` de `observatoryApi` y `authApi` para que el navegador adjunte la cookie de sesión en cada petición.

### Verificación E2E (11/11 Checks)

La verificación fue ejecutada con Puppeteer (Chrome headless) contra backend real + PostgreSQL real:

| # | Check | Resultado |
|---|---|---|
| 1 | Acceso no autenticado a `/` redirige a `/login` | ✅ |
| 2 | Credenciales incorrectas → mensaje de error visible | ✅ |
| 3 | Credenciales válidas → login y redirección al Dashboard | ✅ |
| 4 | Dashboard muestra métricas reales (80 publicaciones) | ✅ |
| 5 | `document.cookie` vacío — cookie no accesible desde JS (HttpOnly) | ✅ |
| 6 | Cookie CDP: `httpOnly=true`, `sameSite=Lax` | ✅ |
| 7 | Observatorio carga con 20 publicaciones paginadas reales | ✅ |
| 8 | Detalle de entrada se renderiza con contenido real de BD | ✅ |
| 9 | Logout → sesión revocada en BD, redirige a `/login` | ✅ |
| 10 | Acceso post-logout a `/` redirige a `/login` | ✅ |
| 11 | Todos los checks superados simultáneamente | ✅ |

### Estado Final tras Bloque 8C.1

| Métrica | Valor |
|---|---|
| Backend tests | 247 pasando |
| Frontend tests | 32 pasando (5 suites) |
| Build | OK |
| Migración Alembic | `0005_users_and_auth_sessions (head)` |
| Sources | 4 |
| Entries | 80 |
| EntryAnalysis | 113 |
| AnalysisCalls | 164 |
| PromptVersions | 12 |
| Coste acumulado | $1.356304 |
| Users | 1 (QA local) |
| AuthSessions | 5 (0 activas, 5 revocadas) |

---

---

## 32. BLOQUE 9A & 9A.1 — Google News como Fuente de Descubrimiento y Hardening Semántico

### Objetivo y Principio Arquitectónico
Incorporar **Google News** como canal complementario de descubrimiento (*discovery source*) para identificar noticias, resoluciones y novedades de Derecho de la Competencia publicadas en medios externos generalistas y especializados.

Google News **NO** sustituye a las fuentes oficiales (CNMC, Comisión Europea, CAT, CURIA) ni actúa como el publisher de las noticias. Se modela dentro de la arquitectura existente como:
- **`Source` canónica:** `type=SourceType.GOOGLE_NEWS` (`"google_news"`), `category="news_aggregator"`, `provider="native"`.
- **Semántica de Autor vs Publisher (`Entry.author = None`):** Los medios de prensa, despachos o portales son personas jurídicas o publicaciones (`publisher`), no autores individuales del texto. `Entry.author` se fija estrictamente en `None` a menos que exista un autor humano explícito; los datos del medio se preservan en `raw_metadata["publisher"]`, `raw_metadata["publisher_url"]` y `raw_metadata["publisher_domain"]`.

### Semántica de URLs y Consent Wall
- **`google_news_url`:** Enlace de redirección propio del feed RSS de Google News (`news.google.com/rss/articles/...`). En la UE/UK, este enlace está sujeto al *consent wall* de Google. **No se intenta romper el consent wall** mediante automatizaciones frágiles ni scraping invasivo.
- **`publisher_url`:** Corresponde al dominio raíz o página principal del medio (atributo `url` del elemento `<source>` del RSS de Google News, e.g. `https://www.ft.com`), **NO a la URL final del artículo**. Se almacena claramente documentado en `raw_metadata["publisher_url"]` y su forma normalizada en `raw_metadata["publisher_domain"]` (e.g. `ft.com`, `cnmc.es`).

### Planificador 100% DB-Driven (`GoogleNewsQueryPlanner`)
El planificador es completamente dinámico y no contiene listas blancas (*whitelists*) ni nombres de entidades hardcodeados en el código Python:
1. **Entidades Dinámicas (`TrackedEntity`):** Carga todas las entidades activas de tipo `institution` (prioridad 90) y `organization` (prioridad 75) y genera consultas genéricas multilingües (`"{display_name}" competencia` para español y `"{display_name}" competition` para inglés), vinculándolas a sus códigos de temas asociados.
2. **Consultas Temáticas de Base de Datos (`TrackingTopic.discovery_queries`):** Las cadenas de búsqueda temática provienen 100% de la configuración de la matriz de seguimiento en base de datos, sin términos de búsqueda fijados en el código.
3. **Ordenación Determinista y Límites:** Ordena por `(priority DESC, query_text ASC)` y acota a un máximo de consultas determinista (`max_queries`, por defecto 20).

### Deduplicación Multicapa y Discovery Fingerprint
El sistema implementa una estrategia de deduplicación conservadora para evitar colisiones cruzadas y re-ingestas:
1. **Deduplicación Intra-Run:** Rastreo estricto de URLs canónicas, GUIDs del feed RSS y huellas calculadas en el lote.
2. **Límite de `content_hash`:** El hash de ingestión clásico (`SHA256(url|title|excerpt)`) depende de la URL; dado que Google News genera URLs dinámicas en base64 diferentes de las URLs oficiales, el hash de contenido por sí solo no detecta duplicados entre distintas fuentes.
3. **Huella de Descubrimiento (`compute_discovery_fingerprint`):**
   $$\text{fingerprint} = \text{SHA256}(\text{publisher\_domain} + ":" + \text{normalize\_title}(\text{title}))$$
   - `normalize_title`: Unicode NFKC, minúsculas, colapso de espacios y eliminación determinista de puntuación superficial. Sin stemming ni matching difuso agresivo.
   - **Mismo medio + mismo título (o variación superficial):** Duplicado.
   - **Distintos medios + mismo título (e.g. Reuters vs FT):** NO son duplicados (no hay sobre-deduplicación).
   - **Mismo medio + títulos materialmente distintos:** NO son duplicados.
4. **Deduplicación Cruzada con Fuentes Oficiales:** Si el `publisher_domain` coincide con el dominio de una fuente oficial registrada (e.g. `cnmc.es`, `catribunal.org.uk`, `curia.europa.eu`, `ec.europa.eu`) y existe una entrada previa con el mismo título normalizado, la publicación se descarta como duplicada.

### Contenido y Suficiencia Documental (`SourceSufficiencyService`)
- Las entradas descubiertas vía Google News se registran con `content=None` y el extracto en `excerpt`.
- Conforme a los umbrales de suficiencia documental de HITCHINGS, `SourceSufficiencyService.assess()` evalúa estas entradas como **`insufficient`** (10/10), garantizando que nunca se envíen al motor analítico de Gemini sin disponer del texto completo.

### Trazabilidad y Métricas de Ingestión (`IngestionRun`)
Cada ejecución registra en `run_metadata`:
- `queries_planned`: total de consultas planificadas.
- `queries_executed`: consultas efectivamente lanzadas antes de alcanzar límites.
- `stopped_by_cap`: booleano que certifica si la ingesta se detuvo anticipadamente al alcanzar el tope de nuevas entradas (`max_new_entries`).
- `max_new_entries_cap`: límite configurado para la ejecución.

### CLI de Ingesta (`scripts/ingest_google_news.py`)

```powershell
# Modo DRY-RUN (por defecto: 0 llamadas de red, 0 escrituras en BD):
python -m scripts.ingest_google_news

# Ejecución real controlada (requiere flag explícito y GOOGLE_NEWS_ENABLED=true):
python -m scripts.ingest_google_news --confirm-real-calls --max-queries 5 --max-items-per-query 5 --max-new-entries 15
```

---

---

## 33. Ingesta de Webs y Fuentes de Referencia Directas (BLOQUE 9B)

### 1. Principio y Arquitectura
El Bloque 9B separa categóricamente las fuentes de **Descubrimiento** (Google News en 9A) de las fuentes de **Ingesta Directa** (web oficial de despachos, publicaciones especializadas y blogs doctrinales). Una `Source` directa obtiene la publicación directamente desde el editor original:
- URL canónica real del publisher.
- Titular íntegro.
- Fecha verificada conforme a jerarquía estructurada.
- Cuerpo editorial completo sin maquetación ajena (menús, sidebars, banners, comentarios o widgets sociales).
- Atribución estricta de autor personal (sin atribuir el publisher como autor).

### 2. Inventario y Selección de Pilotos
Se auditaron las 38 entidades activas de la matriz HITCHINGS-v0.1 y los publishers detectados en Google News. Se seleccionaron **3 fuentes piloto** altamente representativas y de acceso público sin paywall:
1. **Kluwer Competition Law Blog (`kluwer_competition`)**:
   - Tipo: `blog` / `expert_analysis` (Inglés).
   - Relación doctrinal: Artículos de fondo de autores de la matriz como Christian Bergqvist y Alba Ribera Martínez.
   - Extracción: RSS discovery (`legalblogs.wolterskluwer.com/competition-blog/rss.xml`) + HTML editorial (`div.cg3-main-article-section-cstm`).
2. **Chillin'Competition (`chillin_competition`)**:
   - Tipo: `blog` / `expert_analysis` (Inglés).
   - Relación con la matriz: Vinculado inequívocamente a la `TrackedEntity` **Pablo Ibáñez Colomo** (catedrático de Derecho de la Competencia).
   - Extracción: RSS discovery (`chillingcompetition.com/feed/`) + HTML editorial (`div.post`).
3. **Almacén de Derecho - Competencia (`almacen_derecho`)**:
   - Tipo: `blog` / `expert_analysis` (Español).
   - Relación con la matriz: Vinculado inequívocamente a la `TrackedEntity` **Francisco Marcos** (especialista líder en reclamaciones de daños por cárteles en España).
   - Extracción: RSS discovery (`almacendederecho.org/category/competencia/feed`) + HTML editorial (`div.entry-content`).

### 3. Jerarquía de Fechas y Atribución de Autor
- **Fechas (Sección 18)**: Precedencia estricta: `JSON-LD datePublished` > `<meta property="article:published_time">` / `<meta name="date">` > `<time datetime="...">` > fallback del feed de descubrimiento (`published_at_source: listing_feed`).
- **Autores (Sección 17)**: Sólo se persiste `Entry.author` si existe una persona física identificada. Los nombres del medio o publisher se descartan expresamente.

### 4. Normalización de URLs, Deduplicación y Trazabilidad Cruzada con Google News
- **Normalización**: Se eliminan parámetros de rastreo (`utm_*`, `fbclid`, etc.) y se verifica que `rel=canonical` pertenezca al mismo dominio/sitio que la publicación.
- **Deduplicación**: Detección intra-ejecución por URL y deduplicación histórica por URL canónica y huella `compute_ingestion_dedupe_hash`.
- **Cruce con Google News (Sección 24)**: Si una entrada directa coincide por dominio mismo-sitio y titular normalizado con un descubrimiento previo de Google News:
  - En la nueva entrada directa: `raw_metadata["discovered_via_google_news"] = True`, `raw_metadata["google_news_discovery_entry_id"] = UUID`.
  - En la entrada de Google News: `raw_metadata["direct_entry_id"] = UUID`.
  - La entrada directa pasa a ser la fuente de verdad definitiva.

### 5. Suficiencia Documental (`SourceSufficiencyService`)
A diferencia de los resúmenes superficiales de Google News (`insufficient`), los artículos de fuentes directas recuperan el texto íntegro (a menudo entre 2.000 y 60.000 caracteres), clasificándose con nivel **`full`**, listas para el posterior análisis del motor v6 sin depender de scrapes intermedios.

### 6. Ejecución y CLI (`scripts/ingest_direct_sources.py`)
```powershell
# Modo DRY-RUN (por defecto: 0 llamadas de red, 0 escrituras en BD):
python -m scripts.ingest_direct_sources

# Registro / Seeding idempotente de las fuentes piloto:
python -m scripts.ingest_direct_sources --seed-sources

# Ejecución real controlada (requiere flag explícito):
python -m scripts.ingest_direct_sources --confirm-real-calls --limit 5

# Filtrar por fuente o adaptador específico:
python -m scripts.ingest_direct_sources --source chillin_competition --confirm-real-calls
```

### 7. Hardening Semántico de Sources Directas (BLOQUE 9B.1)

#### TRACKED ENTITY ≠ SOURCE ≠ ENTRY AUTHOR

Para evitar confusiones en la atribución de autoría y propiedad del canal, se establece una separación estricta:

1. **`Source` (Publicación o Canal Técnico)**:
   - Puede ser una **publicación colectiva**, blog doctrinal multiautor, medio o revista especializada (ej. *Kluwer Competition Law Blog*, *Chillin'Competition*, *Almacén de Derecho*).
   - `Source.tracked_entity_id`: **SÓLO** debe asignarse cuando la fuente pertenece inequívoca y exclusivamente a una única entidad vigilada (ej. website institucional de la CNMC $\to$ CNMC; página oficial de la DG COMP $\to$ European Commission; blog personal exclusivo de un experto).
   - Para fuentes y publicaciones de referencia colectivas (*Reference Sources*), `Source.tracked_entity_id = None`. Una *Reference Source* **no necesita ni debe** pertenecer a un `TrackedEntity`.
2. **`Entry.author` (Autor Concreto del Artículo)**:
   - Registra el autor personal humano de cada artículo individual (ej. *Jesús Alfaro* o *Francisco Marcos* en *Almacén de Derecho*; *Pablo Ibáñez Colomo* o *Alfonso Lamadrid* en *Chillin'Competition*).
   - Si el autor coincide con una entidad vigilada activa de la matriz, el servicio de ingesta anota en los metadatos de la entrada (`raw_metadata["tracked_author_entity_id"]` y `raw_metadata["tracked_author_entity_name"]`) el contexto del autor rastreado, sin alterar jamás la propiedad global de la fuente (`Source.tracked_entity_id`).
3. **Despacho Explícito de Adaptadores en el Registro (`DirectWebAdapterRegistry`)**:
   - Se eliminan deducciones heurísticas o por coincidencia parcial de nombres/URLs.
   - La resolución del adaptador falla de forma cerrada (*fail-closed* con `DirectWebUnknownAdapterError`) a menos que la fuente tenga configurado explícitamente `source.config["adapter"]` o `source.config["adapter_code"]`. Renombrar una fuente o cambiar su URL no altera el despacho del adaptador.

---


---

## 34. Descubrimiento en LinkedIn mediante Proveedor Externo (BLOQUE 9C)

### 1. Principio y Decisión Arquitectónica
El Bloque 9C incorpora **LinkedIn** como canal de descubrimiento (*discovery source*) para seguir publicaciones públicas de interés sobre Derecho de la Competencia realizadas por firmas, expertos, organizaciones e instituciones.

Se aplican principios de seguridad e integridad categóricos:
- **PROVEEDOR PRINCIPAL**: **Bright Data** (Web Scraper API / Dataset API).
- **PROVEEDOR FALLBACK**: **Apify** (Actor execution API).
- **NO SCRAPING PROPIO DE LINKEDIN**: Queda terminantemente prohibido el uso de Playwright, Selenium, cookies de sesión personales, credenciales de usuario o evasión de protecciones anti-bot contra LinkedIn. Toda la interacción técnica externa se delega en proveedores especializados.
- **DISCOVERY SOURCE (NO SUFICIENCIA INMEDIATA)**: Los posts de LinkedIn son señales de descubrimiento temprano. **NO se envían automáticamente a Gemini** en 9C (0 Gemini calls).

### 2. Documentación Oficial y APIs Consultadas
- **Bright Data**:
  - `https://docs.brightdata.com/api-reference/scrapers/social-media-apis/linkedin-posts-discover-by-profile-url`
  - `https://docs.brightdata.com/api-reference/scrapers/social-media-apis/linkedin-posts-discover-by-company-url`
  - Método: `POST https://api.brightdata.com/datasets/v3/scrape?dataset_id=gd_lyy3tktm25m4avu764` con `Authorization: Bearer <token>`.
- **Apify**:
  - `https://docs.apify.com/api/v2`
  - Método: `POST https://api.apify.com/v2/acts/{actorId}/run-sync-get-dataset-items` con `Authorization: Bearer <token>`.

### 3. Verificación Estricta de Identidad y Metadatos
Para evitar falsos positivos por homónimos, el sistema **nunca inventa URLs** ni asume patrones como `linkedin.com/in/nombre-apellido`.
- Las URLs oficiales verificadas se persisten en `TrackedEntity.metadata_`:
  `{"linkedin_url": "https://www.linkedin.com/company/...", "linkedin_entity_type": "organization|person"}`.
- Subconjunto piloto verificado: **Hausfeld**, **ESKARIAM**, **CNMC**, **European Commission**.
- Entidades personales o sin enlace oficial verificado son ignoradas deterministamente por el planificador hasta que se valide su URL oficial.

### 4. Planificador Determinista (`LinkedInDiscoveryPlanner`)
- Filtra únicamente entidades activas con `metadata["linkedin_url"]` verificado.
- Deduplica URLs de entrada.
- Ordena deterministamente: Instituciones (90) > Organizaciones (80) > Personas (70), desempate por nombre alfabético.
- Aplica límite de entidades (`LINKEDIN_MAX_ENTITIES_PER_RUN`, por defecto 10).

### 5. Política de Fallback Fail-Closed
- **Fallos de Autenticación (401/403)**: Fallan de forma cerrada (*fail-closed* con `LinkedInAuthError`). **NUNCA** disparan fallback a Apify para no consumir otro proveedor por un error de credenciales.
- **Fallos Recuperables (Timeout, 5xx, 429)**: Si Apify está configurado con token válido, se dispara el fallback y se anota `fallback_used = True` y `fallback_reason`.
- **Aislamiento de Errores**: El fallo en una entidad no detiene el procesamiento de las restantes.

### 6. Modelado de Source y Entry
- **Source Canónica**: `name="LinkedIn"`, `type=SourceType.LINKEDIN` (`"linkedin"`), `provider="external"`, `tracked_entity_id=None`.
- **Título Técnico**: `LinkedIn — {author_name} — {YYYY-MM-DD}` (documentado como título técnico determinista).
- **Atribución de Autor**: `Entry.author` registra el autor real devuelto por el post. El contexto de la entidad vigilada se preserva en `raw_metadata["tracked_entity_id"]` y `raw_metadata["tracked_entity_name"]`.

### 7. Deduplicación Cruzada entre Proveedores (Cross-Dedupe)
El proveedor **no forma parte de la identidad** del post. Si Bright Data descubre un post con URL $X$ y posteriormente Apify devuelve la misma URL $X$, el sistema lo reconoce de forma unívoca como duplicado, manteniendo una única `Entry` en base de datos.

### 8. Privacidad y Minimización de Datos
Conforme a los principios de minimización de datos:
- Solo se almacenan textos públicos, autores y métricas de engagement agregadas (likes, comments, reposts).
- Se descartan datos de contacto personales, emails, teléfonos o volcados masivos del proveedor.
- El texto del post se acota a `LINKEDIN_MAX_POST_CHARS` (50.000 caracteres).

### 9. Control de Costes y Trazabilidad (`ProviderUsage`)
- Cada ejecución registra un `IngestionRun` con métricas operativas completas.
- El uso comercial se contabiliza en la tabla `provider_usage` por período mensual (`YYYY-MM`) para cada proveedor (`brightdata` y `apify`).
- Si no hay una tarifa unitaria configurada expresamente en variables de entorno, `estimated_provider_cost` permanece en `None` (nunca se inventa un coste de \$0 para proveedores comerciales).

### 10. CLI de Descubrimiento (`scripts/ingest_linkedin.py`)
```powershell
# Modo DRY-RUN (por defecto: 0 llamadas de red, 0 escrituras en BD):
python -m scripts.ingest_linkedin

# Registro idempotente de la Source canónica y metadatos verificados:
python -m scripts.ingest_linkedin --seed-source --seed-verified-metadata

# Ejecución real controlada (requiere flags y tokens configurados):
python -m scripts.ingest_linkedin --confirm-real-calls

# Smoke individual para una entidad específica:
python -m scripts.ingest_linkedin --entity "Hausfeld" --confirm-real-calls
```

---

## 35. Roadmap

- [x] **Bloque 0:** Arquitectura base, persistencia, contratos y Docker.
- [x] **Bloque 1:** Catálogo y gestión de fuentes, matriz de seguimiento v0.1.
- [x] **Bloque 2:** Primera fuente real end-to-end: CNMC (Website HTML oficial, deduplicación e ingesta).
- [x] **Bloque 3:** Trazabilidad, histórico y robustez de ingestas (`IngestionRun`, frescura y observabilidad).
- [x] **Bloque 4:** Ampliación de fuentes libres e institucionales: Comisión Europea / DG Competition.
- [x] **Bloque 5:** Resoluciones Judiciales: Competition Appeal Tribunal (CAT) / Judgments.
- [x] **Bloque 6:** Jurisprudencia de la UE: Tribunal de Justicia de la Unión Europea (TJUE / CURIA) / Sentencias y Conclusiones.
- [x] **Bloques 7A a 7H.3:** Motor Analítico de IA y Grounding (Cerrado). 100% de cobertura (80/80 entradas con análisis vigente, validación verbatim de citas, prompts v6 y control estricto de costes).
- [x] **Bloque 8A:** API de Consumo del Observatorio para el Portal Cliente (`/api/v1/observatory`). *(Cerrado)*
- [x] **Bloque 8B.0:** Preparación del Backend para el Portal Web (CORS, Contrato API y Smoke Test HTTP). *(Cerrado)*
- [x] **Bloque 8B.1:** Portal Frontend Cliente MVP: Estructura, Diseño y Conexión API. *(Cerrado)*
- [x] **Bloque 8B.2:** Hardening del Frontend y Conexión Real Frontend ↔ Backend. *(Cerrado)*
- [x] **Bloque 8C.1:** Autenticación Privada del Portal Cliente (sesiones server-side, cookies HttpOnly, CLI de provisión, protección de rutas). *(Cerrado)*
- [x] **Bloque 8C.1A:** Security Cleanup y Auditoría de Credenciales (rotación QA, reset CLI interactivo, política de longitud). *(Cerrado)*
- [x] **Bloque 9A:** Google News como Fuente de Descubrimiento (feed RSS público, query planner determinista, límites y dedupe). *(Cerrado)*
- [x] **Bloque 9A.1:** Hardening de Google News: Semántica, Dedupe y Trazabilidad (author=None, discovery fingerprint, cross-source dedupe, planner DB-driven). *(Cerrado)*
- [x] **Bloque 9A.2:** Verificación Final de Matching de Dominios (jerarquías multinivel, spoofing defense). *(Cerrado)*
- [x] **Bloque 9B:** Webs y Fuentes de Referencia Directas (adaptadores específicos, registry, extracción limpia de cuerpo, cruce con Google News). *(Cerrado)*
- [x] **Bloque 9B.1:** Hardening Semántico de Sources Directas (separación de propiedad de fuente y autores de artículos). *(Cerrado)*
- [x] **Bloque 9C:** LinkedIn Discovery mediante Proveedor Externo (Bright Data primary, Apify fallback, planeador, cross-dedupe, control de costes). *(Cerrado)*
- [ ] **Bloque 9D:** Pipeline continuo de ingesta / scheduler.
- [ ] **Futuro:** Módulo de análisis documental.




