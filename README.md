# HITCHINGS - Backend Observatorio

## 1. Qué es HITCHINGS

**HITCHINGS** es una plataforma orientada a la monitorización, extracción, normalización y análisis inteligente de fuentes de información y novedades (noticias, portales regulatorios e institucionales, blogs especializados y redes como LinkedIn).

En el futuro, HITCHINGS integrará dos grandes capacidades:
1. **Observatorio automático de novedades:** monitorización continua, clasificación temática, síntesis y scoring mediante IA.
2. **Herramienta de análisis documental:** ingesta de documentos, audios y textos, prompts especializados y generación de informes.

---

## 2. Alcance Actual: BLOQUES 0, 1, 2, 3 y 4

El proyecto cuenta con:
- **BLOQUE 0:** Base estructural, persistencia (PostgreSQL + SQLAlchemy 2.0 síncrono con psycopg v3, Alembic), configuración y contratos de proveedores.
- **BLOQUE 1:** Gestión configurable de fuentes, matriz de seguimiento v0.1 (`TrackingMatrix`, `TrackingTopic`, `TrackedEntity`, `TrackedEntityTopic`).
- **BLOQUE 2:** Primera fuente real end-to-end conectada a Internet (**CNMC - Prensa / Noticias** vía website HTML oficial con `CNMCNewsExtractor`), ingesta normalizada, deduplicación básica en base de datos y endpoints de consulta de entradas.
- **BLOQUE 3:** Trazabilidad, histórico y robustez de ingestas (`IngestionRun`, estados `success`/`partial`/`failed`, cálculo dinámico de frescura y endpoints de observabilidad técnica).
- **BLOQUE 4:** Segunda fuente real institucional: **European Commission / DG Competition** (vía RSS oficial de Competition Policy con enriquecimiento de texto íntegro vía Press Corner API y node fallback con `EuropeanCommissionExtractor`).

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
│   │   └── extractors/       # Extractores especializados (CNMCNewsExtractor, EuropeanCommissionExtractor)
│   ├── schemas/              # Esquemas Pydantic para validación y serialización
│   ├── services/             # Servicios de dominio: IngestionService con deduplicación y observabilidad
│   └── main.py               # Punto de entrada FastAPI y lifespan
├── migrations/               # Scripts de migración Alembic
│   └── versions/
│       ├── 0001_initial_schema.py        # sources, entries, provider_usage
│       ├── 0002_tracking_configuration.py # matrices, topics, entities, entity_topics
│       └── 0003_ingestion_runs.py         # ingestion_runs, campos de frescura
├── scripts/
│   ├── seed_tracking_v01.py               # Seed de la Matriz v0.1 y entidades del cliente
│   ├── seed_source_cnmc.py                # Seed de la fuente real CNMC asociada a su entidad
│   └── seed_source_european_commission.py # Seed de la fuente real Comisión Europea
├── tests/                    # Tests unitarios, de API, modelos, seeds, CNMC y Comisión Europea (48 tests)
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

## 12. Cómo Ejecutar Tests

La suite completa (48 tests) valida configuración, endpoints, modelos, contratos de providers, seeds, extractores especializados (CNMC y Comisión Europea), parsing RSS, deduplicación, ciclo de vida de `IngestionRun`, fallos, parciales y cálculo dinámico de frescura:

```powershell
pytest -v
```

---

## 13. Endpoints Disponibles

### Salud del Sistema
- `GET /health`: Estado del proceso FastAPI (`{"status": "ok", "service": "hitchings"}`).
- `GET /health/db`: Conectividad real con PostgreSQL (`{"status": "ok", "database": "connected"}`).

### Fuentes Técnicas (`/api/v1/sources`)
- `GET /api/v1/sources`: Listado con paginación (`limit`, `offset`) y filtros (`active`, `tracked_entity_id`).
- `GET /api/v1/sources/{id}`: Detalle de una fuente.
- `POST /api/v1/sources`: Crear fuente técnica (permite asociar `tracked_entity_id`).
- `PATCH /api/v1/sources/{id}`: Actualización parcial.
- `DELETE /api/v1/sources/{id}`: Eliminación.
- `GET /api/v1/sources/{id}/status`: **Observabilidad técnica y frescura** en tiempo real:
  ```json
  {
    "source_id": "162474fa-1d13-4e24-b8a0-659863040157",
    "name": "CNMC - Noticias",
    "active": true,
    "last_run_at": "2026-09-07T12:10:13.471078+02:00",
    "last_success_at": "2026-09-07T12:10:15.847684+02:00",
    "last_run_status": "success",
    "latest_published_at": "2026-09-01T08:08:02+02:00",
    "freshness": {
      "status": "fresh",
      "warning_hours": 168,
      "hours_since_latest": 148.0
    },
    "last_run": {
      "id": "feace384-88ea-4ded-b6e2-642816800114",
      "started_at": "2026-09-07T12:10:13.471078+02:00",
      "finished_at": "2026-09-07T12:10:15.847684+02:00",
      "status": "success",
      "fetched": 20,
      "created": 0,
      "duplicates": 20,
      "failed": 0,
      "duration_ms": 2376
    }
  }
  ```
- `POST /api/v1/sources/{id}/ingest`: Disparo manual de ingesta con retorno de `ingestion_run_id`:
  ```json
  {
    "ingestion_run_id": "feace384-88ea-4ded-b6e2-642816800114",
    "source_id": "162474fa-1d13-4e24-b8a0-659863040157",
    "status": "success",
    "fetched": 20,
    "created": 0,
    "duplicates": 20,
    "failed": 0,
    "started_at": "2026-09-07T10:10:13.471078Z",
    "finished_at": "2026-09-07T10:10:15.847684Z",
    "duration_ms": 2376,
    "latest_published_at": "2026-09-01T06:08:02Z",
    "oldest_published_at": "2026-07-27T09:39:58Z"
  }
  ```

### Histórico de Ejecuciones (`/api/v1/ingestion-runs`)
- `GET /api/v1/ingestion-runs`: Listado cronológico inverso con filtros (`source_id`, `status`, `limit`, `offset`).
- `GET /api/v1/ingestion-runs/{id}`: Detalle completo de una ejecución histórica.

### Entradas Capturadas (`/api/v1/entries`)
- `GET /api/v1/entries`: Listado de entradas persistidas con paginación (`limit`, `offset`) y filtros (`source_id`, `status`).
- `GET /api/v1/entries/{id}`: Detalle completo de una entrada capturada (`title`, `url`, `published_at`, `raw_text`, `content_hash`, `raw_metadata`, etc.).

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

## 14. Funcionalidades Deliberadamente Pendientes

Para respetar la delimitación estricta de fases, en este Bloque 4 **NO** se han implementado:
1. Retries automáticos, backoff exponencial o circuit breakers.
2. Scheduler en segundo plano (Celery, APScheduler, cron).
3. Ingesta, clasificación, resúmenes o scoring con IA.
4. Nuevas fuentes técnicas o providers de pago (Bright Data, Apify).
5. Autenticación, JWT o control de acceso.
6. Interfaz gráfica o frontend.
7. Módulo de análisis documental (PDF, audio, exportación).

---

## 15. Roadmap

- [x] **Bloque 0:** Arquitectura base, persistencia, contratos y Docker.
- [x] **Bloque 1:** Catálogo y gestión de fuentes, matriz de seguimiento v0.1.
- [x] **Bloque 2:** Primera fuente real end-to-end: CNMC (Website HTML oficial, deduplicación e ingesta).
- [x] **Bloque 3:** Trazabilidad, histórico y robustez de ingestas (`IngestionRun`, frescura y observabilidad).
- [x] **Bloque 4:** Ampliación de fuentes libres e institucionales: Comisión Europea / DG Competition. *(Completado)*
- [ ] **Bloque 5:** Tratamiento y enriquecimiento con IA.
- [ ] **Bloque 6:** Automatización / programación (scheduler).
- [ ] **Bloque 7:** LinkedIn y fuentes complejas mediante proveedor externo.
- [ ] **Bloque 8:** Interfaz web.
- [ ] **Futuro:** Módulo de análisis documental.
