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

## LinkedIn Discovery Hardening (Bloque 9C)

HARDENED & DEDUPLICATED (INACTIVE / ZERO CALLS)
- Discovery status: `LINKEDIN_DISCOVERY_ENABLED=false` (sin llamadas reales ni consumo de créditos).
- Normalizador canónico (`app/providers/linkedin/normalizer.py`):
  - Extracción de Activity ID numérico de longitud arbitraria (`urn:li:activity:...`, `/feed/update/...`, `/posts/...-activity-...`).
  - Fallback determinista `linkedin:post:{sha256}` con trazado `provenance_status="fallback"`.
  - Normalización de `canonical_url` (stripping de querystrings/tracking, fragmentos y trailing slashes).
- Gating de autoría fail-closed: posts con autores vacíos o genéricos ("LinkedIn Author", "Unknown") o sin entidad trackeada son descartados sin persistir.
- Desacoplamiento estricto de procedencia:
  - Autor editorial = Persona u organización (`author_name`).
  - Proveedor técnico = Bright Data / Apify (`retrieval_provider`, nunca visible al usuario).
  - Origen / Source = LinkedIn.
- Deduplicación cross-provider: orden estricto `external_id -> canonical_url -> fallback`.
- UI portal (`ObservatoryPage` y `EntryDetailPage`):
  - Cabecera: `LinkedIn · {author_name}` acompañado de icono contextual (`Building2` para organización, `User` para persona).
  - Enlace seguro directo al post original en LinkedIn.
  - Ni Bright Data ni Apify se muestran en ningún lugar de la interfaz.

## Próximo paso exacto

1. Prueba controlada de LinkedIn Discovery en staging o local con mock/fixture validado antes de activar credenciales reales.

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
