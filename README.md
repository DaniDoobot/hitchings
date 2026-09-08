# HITCHINGS - Backend Observatorio

## 1. Qué es HITCHINGS

**HITCHINGS** es una plataforma orientada a la monitorización, extracción, normalización y análisis inteligente de fuentes de información y novedades (noticias, portales regulatorios e institucionales, blogs especializados y redes como LinkedIn).

En el futuro, HITCHINGS integrará dos grandes capacidades:
1. **Observatorio automático de novedades:** monitorización continua, clasificación temática, síntesis y scoring mediante IA.
2. **Herramienta de análisis documental:** ingesta de documentos, audios y textos, prompts especializados y generación de informes.

---

## 2. Alcance Actual: BLOQUES 0, 1, 2, 3, 4, 5, 6 y 7A

El proyecto cuenta con:
- **BLOQUE 0:** Base estructural, persistencia (PostgreSQL + SQLAlchemy 2.0 síncrono con psycopg v3, Alembic), configuración y contratos de proveedores.
- **BLOQUE 1:** Gestión configurable de fuentes, matriz de seguimiento v0.1 (`TrackingMatrix`, `TrackingTopic`, `TrackedEntity`, `TrackedEntityTopic`).
- **BLOQUE 2:** Primera fuente real end-to-end conectada a Internet (**CNMC - Prensa / Noticias** vía website HTML oficial con `CNMCNewsExtractor`), ingesta normalizada, deduplicación básica en base de datos y endpoints de consulta de entradas.
- **BLOQUE 3:** Trazabilidad, histórico y robustez de ingestas (`IngestionRun`, estados `success`/`partial`/`failed`, cálculo dinámico de frescura y endpoints de observabilidad técnica).
- **BLOQUE 4:** Segunda fuente real institucional: **European Commission / DG Competition** (vía RSS oficial de Competition Policy con enriquecimiento de texto íntegro vía Press Corner API y node fallback con `EuropeanCommissionExtractor`).
- **BLOQUE 5:** Tercera fuente real institucional y nuevo tipo de contenido: **Competition Appeal Tribunal (CAT)** / Resoluciones Judiciales (vía website HTML oficial con `CompetitionAppealTribunalExtractor`, extracción de Neutral Citations, multi-casos, PDFs originales enlazados y resúmenes oficiales normalizados como `judicial_decision`).
- **BLOQUE 6:** Cuarta fuente real institucional y jurisprudencia comunitaria: **Tribunal de Justicia de la Unión Europea (TJUE / CURIA)** / Sentencias y Conclusiones (vía InfoCuria, identificador canónico ECLI, deduplicación exacta, texto íntegro y limpio).
- **BLOQUE 7A:** Arquitectura y Persistencia del Análisis con IA: cimientos de modelado, inmutabilidad (`Entry` vs `EntryAnalysis`), versionado estricto de prompts (`AnalysisPromptVersion`), snapshots canónicos con hash SHA-256 de matrices y contenidos, asignación normalizada de temas con un único tema primario (`EntryAnalysisTopic`), auditoría técnica granular (`AnalysisCall`: tokens, latencia, costes USD), proveedor mock determinista (`MockAIProvider`) y política de seguridad de cero llamadas externas no autorizadas (`ANALYSIS_PROVIDER="disabled"`).


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

## 18. Funcionalidades Deliberadamente Pendientes

Para respetar la delimitación estricta de fases, en este Bloque 7E **NO** se han implementado:
1. Análisis de las 59 Entries restantes sin procesar.
2. Re-análisis del caso Livronsa (su fallo por paráfrasis detectada permanece como baseline de auditoría).
3. Creación de prompts v4.
4. Scheduler en segundo plano (Celery, APScheduler, cron).
5. Interfaz gráfica o frontend.

---

## 19. Roadmap

- [x] **Bloque 0:** Arquitectura base, persistencia, contratos y Docker.
- [x] **Bloque 1:** Catálogo y gestión de fuentes, matriz de seguimiento v0.1.
- [x] **Bloque 2:** Primera fuente real end-to-end: CNMC (Website HTML oficial, deduplicación e ingesta).
- [x] **Bloque 3:** Trazabilidad, histórico y robustez de ingestas (`IngestionRun`, frescura y observabilidad).
- [x] **Bloque 4:** Ampliación de fuentes libres e institucionales: Comisión Europea / DG Competition.
- [x] **Bloque 5:** Resoluciones Judiciales: Competition Appeal Tribunal (CAT) / Judgments.
- [x] **Bloque 6:** Jurisprudencia de la UE: Tribunal de Justicia de la Unión Europea (TJUE / CURIA) / Judgments and Opinions.
- [x] **Bloque 7A:** Arquitectura y Persistencia del Análisis con IA (Modelos, snapshots, auditoría de llamadas, versionado de prompts y mock provider determinista).
- [x] **Bloque 7B:** Gemini Developer API Baseline: pipeline triage + deep analysis, benchmark controlado de 20 entries.
- [x] **Bloque 7C:** Auditoría de Grounding, taxonomía y consistencia del benchmark.
- [x] **Bloque 7D:** Source Sufficiency y enriquecimiento selectivo de CAT vía PDFs oficiales.
- [x] **Bloque 7E:** Evidence-grounded output, compuerta de suficiencia en pipeline, prompts v3 y validación controlada de 8 entries.
- [x] **Bloque 7E.1:** Integridad de auditoría, prompt immutability y grounding del input real.
- [x] **Bloque 7E.2:** Aislamiento estricto de tests, guarda fail-closed y ledger hygiene. *(Cerrado)*
- [ ] **Bloque 8:** Automatización / programación (scheduler).
- [ ] **Bloque 9:** LinkedIn y fuentes complejas mediante proveedor externo.
- [ ] **Bloque 10:** Interfaz web.
- [ ] **Futuro:** Módulo de análisis documental.


