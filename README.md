# HITCHINGS - Backend Observatorio

## 1. Qué es HITCHINGS

**HITCHINGS** es una plataforma orientada a la monitorización, extracción, normalización y análisis inteligente de fuentes de información y novedades (noticias, portales regulatorios e institucionales, blogs especializados y redes como LinkedIn).

En el futuro, HITCHINGS integrará dos grandes capacidades:
1. **Observatorio automático de novedades:** monitorización continua, clasificación temática, síntesis y scoring mediante IA.
2. **Herramienta de análisis documental:** ingesta de documentos, audios y textos, prompts especializados y generación de inf## 2. Alcance Actual: BLOQUES 0, 1 y 2

El proyecto cuenta con:
- **BLOQUE 0:** Base estructural, persistencia (PostgreSQL + SQLAlchemy 2.0 síncrono con psycopg v3, Alembic), configuración y contratos de proveedores.
- **BLOQUE 1:** Gestión configurable de fuentes, matriz de seguimiento v0.1 (`TrackingMatrix`, `TrackingTopic`, `TrackedEntity`, `EntityTopicAssociation`).
- **BLOQUE 2:** Primera fuente real end-to-end conectada a Internet (**CNMC - Prensa / Noticias** vía RSS oficial), ingesta normalizada, deduplicación básica en base de datos y endpoints de consulta de entradas.

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
│   ├── models/               # Modelos SQLAlchemy: Source, Entry, ProviderUsage, Tracking*
│   ├── providers/            # Contratos e implementaciones (NativeProvider RSS, Bright Data, Apify)
│   ├── schemas/              # Esquemas Pydantic para validación y serialización
│   ├── services/             # Servicios de dominio: IngestionService con deduplicación
│   └── main.py               # Punto de entrada FastAPI y lifespan
├── migrations/               # Scripts de migración Alembic
│   └── versions/
│       ├── 0001_initial_schema.py        # sources, entries, provider_usage
│       └── 0002_tracking_configuration.py # matrices, topics, entities, entity_topics
├── scripts/
│   ├── seed_tracking_v01.py  # Seed de la Matriz v0.1 y entidades del cliente
│   └── seed_source_cnmc.py   # Seed de la fuente técnica real CNMC asociada a su entidad
├── tests/                    # Tests unitarios, de API, modelos, seeds e ingesta
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

Ambos scripts son completamente **idempotentes**.

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

## 10. Cómo Ejecutar Tests

La suite completa (31 tests) valida configuración, endpoints, modelos, contratos de providers, seeds, extractor HTML de CNMC, parsing RSS y deduplicación:

```powershell
pytest -v
```

---

## 11. Endpoints Disponibles

### Salud del Sistema
- `GET /health`: Estado del proceso FastAPI (`{"status": "ok", "service": "hitchings"}`).
- `GET /health/db`: Conectividad real con PostgreSQL (`{"status": "ok", "database": "connected"}`).

### Fuentes Técnicas (`/api/v1/sources`)
- `GET /api/v1/sources`: Listado con paginación (`limit`, `offset`) y filtros (`active`, `tracked_entity_id`).
- `GET /api/v1/sources/{id}`: Detalle de una fuente.
- `POST /api/v1/sources`: Crear fuente técnica (permite asociar `tracked_entity_id`).
- `PATCH /api/v1/sources/{id}`: Actualización parcial.
- `DELETE /api/v1/sources/{id}`: Eliminación.
- `POST /api/v1/sources/{id}/ingest`: **Disparo manual de ingesta** para la fuente técnica indicada. Retorna métricas de la captura y control de frescura:
  ```json
  {
    "source_id": "162474fa-1d13-4e24-b8a0-659863040157",
    "fetched": 20,
    "created": 20,
    "duplicates": 0,
    "failed": 0,
    "started_at": "2026-09-07T09:34:08.143208Z",
    "finished_at": "2026-09-07T09:34:09.755682Z",
    "latest_published_at": "2026-09-01T06:08:02Z",
    "oldest_published_at": "2026-07-27T09:39:58Z"
  }
  ```

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

## 12. Funcionalidades Deliberadamente Pendientes

Para respetar la delimitación estricta de fases, en este Bloque 2 **NO** se han implementado:
1. Scraping complejo o descargas web con renderizado dinámico (Playwright, navegadores headless).
2. Llamadas a APIs de terceros de pago (Bright Data, Apify).
3. Planificador o scheduler recurrente automático en segundo plano.
4. Ingesta, clasificación, resúmenes o scoring con IA.
5. Modelo `EntryAnalysis` (se creará en el bloque de IA).
6. Autenticación, JWT o control de acceso.
7. Interfaz gráfica o frontend.
8. Módulo de análisis documental (PDF, audio, exportación).

---

## 13. Roadmap

- [x] **Bloque 0:** Arquitectura base, persistencia, contratos y Docker.
- [x] **Bloque 1:** Catálogo y gestión de fuentes, matriz de seguimiento v0.1.
- [x] **Bloque 2:** Primera fuente real end-to-end: CNMC (RSS, deduplicación e ingesta). *(Completado)*
- [ ] **Bloque 3:** Normalización avanzada y ampliación de fuentes libres.
- [ ] **Bloque 4:** Tratamiento y enriquecimiento con IA.
- [ ] **Bloque 5:** Automatización / programación (scheduler).
- [ ] **Bloque 6:** LinkedIn y fuentes complejas mediante proveedor externo.
- [ ] **Bloque 7:** Interfaz web.
- [ ] **Futuro:** Módulo de análisis documental.
