# HITCHINGS - Backend Observatorio

## 1. Qué es HITCHINGS

**HITCHINGS** es una plataforma orientada a la monitorización, extracción, normalización y análisis inteligente de fuentes de información y novedades (noticias, portales regulatorios e institucionales, blogs especializados y redes como LinkedIn).

En el futuro, HITCHINGS integrará dos grandes capacidades:
1. **Observatorio automático de novedades:** monitorización continua, clasificación temática, síntesis y scoring mediante IA.
2. **Herramienta de análisis documental:** ingesta de documentos, audios y textos, prompts especializados y generación de informes.

---

## 2. Alcance Actual: BLOQUE 0 & BLOQUE 1

El proyecto cuenta con la base estructural (**BLOQUE 0**) y la gestión configurable de fuentes y matriz de seguimiento v0.1 (**BLOQUE 1**).

### Principio Arquitectónico Fundamental: Separación de Responsabilidades
El diseño desacopla estrictamente tres dimensiones:
1. **QUÉ SE VIGILA (`TrackedEntity`)**: La persona, organización, institución o publicación de interés (ej. *Pinar Akman*, *CNMC*, *Hausfeld*). No presupone una URL fija ni un canal técnico específico.
2. **DÓNDE SE OBTIENE (`Source`)**: El canal técnico concreto de adquisición (web personal, perfil LinkedIn, RSS, API institucional). Una misma entidad puede tener asociadas múltiples fuentes técnicas o ninguna hasta que se disponga de su URL definitiva.
3. **CÓMO SE INTERPRETA (`TrackingTopic` & `TrackingMatrix`)**: La taxonomía temática, subtemas, queries de descubrimiento, señales/keywords orientativas e instrucciones de relevancia/exclusión.

> [!NOTE]
> En este bloque **NO** se realiza scraping real, no hay llamadas HTTP a webs o APIs externas, no hay modelos de IA, no hay frontend ni autenticación.

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
│   │       ├── sources.py    # CRUD de fuentes técnicas (/api/v1/sources)
│   │       └── tracking.py   # Matrices, topics, entities y associations (/api/v1/tracking)
│   ├── core/                 # Configuración central (pydantic-settings) y logging
│   ├── db/                   # Engine SQLAlchemy síncrono (psycopg v3) y sesiones
│   ├── models/               # Modelos SQLAlchemy: Source, Entry, ProviderUsage, Tracking*
│   ├── providers/            # Contratos e interfaces (BaseSourceProvider, Native, Bright Data, Apify)
│   ├── schemas/              # Esquemas Pydantic para validación y serialización
│   └── main.py               # Punto de entrada FastAPI y lifespan
├── migrations/               # Scripts de migración Alembic
│   └── versions/
│       ├── 0001_initial_schema.py        # sources, entries, provider_usage
│       └── 0002_tracking_configuration.py # matrices, topics, entities, entity_topics
├── scripts/
│   └── seed_tracking_v01.py  # Seed idempotente de la Matriz v0.1 y entidades del cliente
├── tests/                    # Tests unitarios, de API, modelos y de seed idempotente
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
- **Dentro de Docker Compose:**  
  `DATABASE_URL=postgresql+psycopg://hitchings:hitchings@db:5432/hitchings`
- **Desde el Host Windows (conectando a PostgreSQL en Docker):**  
  `DATABASE_URL=postgresql+psycopg://hitchings:hitchings@localhost:5433/hitchings`
  *(Se utiliza el puerto 5433 para evitar colisiones si existe PostgreSQL local en el puerto 5432)*

### Política Free-First y Límites:
- `BRIGHTDATA_ENABLED=false` (desactivado por defecto)
- `BRIGHTDATA_MONTHLY_LIMIT=5000`
- `BRIGHTDATA_SOFT_LIMIT=4500`
- `BRIGHTDATA_ALLOW_PAID_USAGE=false`
- `APIFY_ENABLED=false`

---

## 6. Cómo Levantarlo en Local (Docker Compose)

Para construir la imagen y levantar los servicios:

```bash
docker compose up -d --build
```

Comprobar el estado:
```bash
docker compose ps
```

Ver logs del backend:
```bash
docker compose logs -f api
```

---

## 7. Cómo Ejecutar Migraciones

Las migraciones de Alembic se aplican con:

```bash
# Aplicar todas las migraciones (0001 y 0002)
docker compose exec api alembic upgrade head

# Revertir la última migración
docker compose exec api alembic downgrade -1
```

O en local (con el entorno virtual activo):
```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

---

## 8. Cómo Ejecutar el Seed Idempotente (Matriz v0.1)

El script de seed puebla la base de datos con la matriz `HITCHINGS-v0.1`, los 18 topics/subtopics y las 38 entidades proporcionadas por el cliente con sus asignaciones iniciales. Es completamente **idempotente** y puede ejecutarse repetidamente sin duplicar datos:

```bash
# En contenedor Docker:
docker compose exec api python -m scripts.seed_tracking_v01

# En entorno local Windows:
python -m scripts.seed_tracking_v01
```

---

## 9. Cómo Ejecutar Tests

La suite de pruebas valida configuración, endpoints, modelos, contratos de providers y la idempotencia del seed:

```bash
# En contenedor Docker:
docker compose exec api pytest -v

# En entorno local Windows:
pytest -v
```

---

## 10. Endpoints Disponibles

### Salud del Sistema
- `GET /health`: Estado del proceso FastAPI (`{"status": "ok", "service": "hitchings"}`).
- `GET /health/db`: Conectividad real con PostgreSQL (`{"status": "ok", "database": "connected"}`).

### Fuentes Técnicas (`/api/v1/sources`)
- `GET /api/v1/sources`: Listado con paginación (`limit`, `offset`) y filtros (`active`, `tracked_entity_id`).
- `GET /api/v1/sources/{id}`: Detalle de una fuente.
- `POST /api/v1/sources`: Crear fuente técnica (permite asociar `tracked_entity_id`).
- `PATCH /api/v1/sources/{id}`: Actualización parcial.
- `DELETE /api/v1/sources/{id}`: Eliminación.

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

## 11. Cómo Cambiar la Matriz sin Modificar Código

Toda la definición de seguimiento reside en base de datos:
1. Para actualizar criterios o temas, se crea una nueva versión de matriz (ej. `HITCHINGS-v0.2`) vía API o script.
2. Se definen sus temas y subtemas correspondientes.
3. Se activa mediante `POST /api/v1/tracking/matrices/{id}/activate`.
4. Los futuros motores de extracción y clasificación consumen dinámicamente la matriz que tenga `status == 'active'`, sin necesidad de recompilar ni desplegar nuevo código.

---

## 12. Funcionalidades Deliberadamente Pendientes

Para respetar la delimitación estricta de fases, en este Bloque 1 **NO** se han implementado:
1. Scraping o descargas web reales (Playwright, BeautifulSoup, requests externas).
2. Llamadas a APIs de terceros (Bright Data, Apify, Google News, LinkedIn).
3. Motor de deduplicación o hash de contenido en ingesta.
4. Planificador o scheduler recurrente de tareas.
5. Ingesta, clasificación, resúmenes o scoring con IA.
6. Modelo `EntryAnalysis` (se creará en el bloque de IA).
7. Autenticación, JWT o control de acceso.
8. Interfaz gráfica o frontend.
9. Módulo de análisis documental (PDF, audio, exportación).

---

## 13. Roadmap

- [x] **Bloque 0:** Arquitectura base, persistencia, contratos y Docker.
- [x] **Bloque 1:** Catálogo y gestión de fuentes, matriz de seguimiento v0.1. *(Completado)*
- [ ] **Bloque 2:** Primera fuente real end-to-end.
- [ ] **Bloque 3:** Normalización y deduplicación.
- [ ] **Bloque 4:** Ampliación de fuentes.
- [ ] **Bloque 5:** Tratamiento con IA.
- [ ] **Bloque 6:** Automatización / programación (scheduler).
- [ ] **Bloque 7:** LinkedIn mediante proveedor externo.
- [ ] **Bloque 8:** Interfaz web.
- [ ] **Futuro:** Módulo de análisis documental.
