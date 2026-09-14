# HITCHINGS — Current Project Status

Fecha:
2026-09-14

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

Bloque 17A:
INTEGRATE

Bloque 17B:
implementado.

Commit base FTC:
1e3af6d

Source producción:
Federal Trade Commission - Bureau of Competition

Source ID:
e9671e46-94a5-49eb-ba0a-db96d35ffc24

FTC source ya está seeded en producción.

IMPORTANTE:
NO volver a ejecutar seed salvo que sea necesario/idempotente.

No existen todavía Entries FTC creadas mediante backfill.

No se ha ejecutado Gemini para FTC.

## Incidencia encontrada

Los previews de 14/30/90 días fallaron inicialmente con:

SET TRANSACTION ISOLATION LEVEL must be called before any query

Causa:
La Session ejecutaba operaciones previas o autobegin implícito antes de configurar
el aislamiento en PostgreSQL, lo que provocaba que el motor rechazara
`SET TRANSACTION ISOLATION LEVEL`.

Commit que lo corrige:
`eda208e` (fix: initialize read-only preview transaction safely)

La solución configura de forma idiomática `execution_options`
(`isolation_level="REPEATABLE READ"`, `postgresql_readonly=True`) a nivel de Engine
y Connection, haciendo que la transacción nazca directamente como `REPEATABLE READ, READ ONLY`
sin emitir sentencias SQL frágiles.

## Próximo paso exacto

1. En el ordenador de oficina:
   git fetch
   git pull origin main

2. Recrear entorno local si hace falta.
   NO versionar .env ni secretos.

3. Desplegar SOLO HITCHINGS Compose con el HEAD final.
   NO tocar/reiniciar PostgreSQL.

4. Ejecutar en backend producción:

```bash
python -m scripts.preview_source_discovery \
  --source ftc \
  --lookback-days 14

python -m scripts.preview_source_discovery \
  --source ftc \
  --lookback-days 30

python -m scripts.preview_source_discovery \
  --source ftc \
  --lookback-days 90
```

5. Revisar resultados ANTES de cualquier backfill.

6. Elegir una ventana cuyo new_candidates sea exactamente 1
   para la primera sonda real FTC.

7. NO ejecutar backfill FTC hasta revisar esos previews.

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
