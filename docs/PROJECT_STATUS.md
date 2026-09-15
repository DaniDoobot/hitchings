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
FTC es la fuente 16/17 en curso.
Después queda:
DOJ Antitrust Division.

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

IMPLEMENTED
- Extractor nativo: `app/providers/extractors/doj_antitrust.py`
- RSS feed: `https://www.justice.gov/news/rss?field_component=376&type=press_release`
- Seeder idempotente: `scripts/seed_source_doj_antitrust.py`
- Scope guard fail-closed: exclusión automática de causas USAO/no-antitrust.
- Identidad canónica: `doj_atr:node:{node_id}` o `doj_atr:{year}:{month}:{slug}`
- Previews locales validados (strict read-only, 0 Gemini, 0 DB mutations):
  - 14d: 1 new candidate (1 FULL, 1 eligible)
  - 30d: 4 new candidates (4 FULL, 4 eligible)
  - 90d: 13 new candidates (12 FULL, 1 PARTIAL, 12 eligible, 1 USAO excluido)
- Tests: 9 passed (`tests/test_doj_antitrust_extractor.py`)

## Próximo paso exacto

1. En producción (Dokploy):
   - Redeploy SOLO Compose con el commit final (NO tocar PostgreSQL).
   - Ejecutar seed idempotente:
     `python -m scripts.seed_source_doj_antitrust`
   - Ejecutar previews read-only en backend producción:
     `python -m scripts.preview_source_discovery --source doj --lookback-days 14`
     `python -m scripts.preview_source_discovery --source doj --lookback-days 30`
     `python -m scripts.preview_source_discovery --source doj --lookback-days 90`
2. Revisar resultados de los 3 previews.
3. Primera sonda controlada de 1 candidato (`--lookback-days 14 --max-new-entries 1`).

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
