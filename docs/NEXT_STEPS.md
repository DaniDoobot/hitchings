# HITCHINGS NEXT STEPS

## Estado actual

Fecha:
2026-10-14

HEAD:
f3fbfe5 — feat: complete observatory tracking priorities

### Últimos bloques y prioridades completados:

1. **Prioridades del Observatorio: Taxonomía y Blindaje de Fuentes**:
   - Matriz `HITCHINGS-v0.1` ampliada de 18 a 20 tópicos:
     - `unfair_competition`: "Competencia desleal" (prioridad 75, parent `competition_law_general`).
     - `ai_intellectual_property`: "IA y Propiedad Intelectual" (prioridad 70, parent `competition_law_general`).
   - Incorporación de referencias normativas europeas clave: Arts. 101 y 102 TFUE, Directiva de Daños 2014/104, Reglamento DMA (UE) 2022/1925, Directiva (UE) 2020/1828 (Acciones de Representación y Tutela Colectiva).
   - Sincronización idempotente (`upsert`) en `scripts/seed_tracking_v01.py` (520 líneas).
   - **Ejecutado y verificado en Dokploy producción**: 20/20 tópicos confirmados en PostgreSQL de producción (`hitchings-news`).
   - Blindaje de fuente manual: excluida en `WeeklyRefreshService` y guardia fail-closed en `IngestionService`.
   - Commit: `f3fbfe5`.

2. **Bloque 12 — Exportación Documental y Dossiers Temáticos Word (.docx)**:
   - Fase 1: Servicio backend `DocumentExportService` para maquetación corporativa en Microsoft Word (.docx).
   - Fase 2: Endpoint HTTP `GET /api/v1/documents/{document_id}/export`.
   - Fase 3: Integración frontend "Exportar Word" en `DocumentDetailPage.tsx`.
   - Fase 4A: Motor backend `generate_dossier_docx()` para dossiers de 1 a 30 documentos (100% en memoria, sin persistencia ni llamadas a IA).
   - Fase 4B: Endpoint HTTP `POST /api/v1/documents/dossier/export` con orden determinista y validación estricta de documentos.
   - Fase 4C: Selección múltiple en catálogo `DocumentsPage.tsx`, barra de acciones flotante, modal y descarga directa en navegador.
   - Commits: `a5b2596`, `7ab5d7b`, `331e2ba`, `8935405`.

3. **Bloque 11C — Ingesta Manual de Documentos**:
   - Ingesta manual desde URL y desde archivo multipart (PDF, TXT, MD hasta 25 MB).
   - Mitigación SSRF, deduplicación jerárquica por URL y SHA-256 (HTTP 409 Conflict).
   - Fuente canónica `SourceType.MANUAL_UPLOAD` (migración 0008 en producción).
   - Modal interactivo con drag & drop, conmutador de análisis IA y gestión de duplicados.
   - Reconstrucción geométrica de párrafos PDF por coordenadas y reparación tipográfica.
   - Desplegado y verificado en producción Dokploy.

4. **Bloque 11B — Módulo Documental e Informes**:
   - Consulta documental integral `/api/v1/documents` y `/documentos`.
   - Consulta transparente de entradas analizadas y sin análisis previo.
   - Detalle documental con doble perspectiva (texto original vs análisis IA y evidencia literal).
   - Anti-N+1, 0 llamadas a IA en tiempo de consulta.
   - Desplegado y validado en producción Dokploy.

5. **Bloque 9C / 9E — LinkedIn Source Operational & Verified Production Readiness**:
   - Bright Data primario (`gd_lyy3tktm25m4avu764`) con snapshots asíncronos.
   - Apify fallback (`harvestapi/linkedin-profile-posts`) ante timeouts, errores o resultados vacíos.
   - Normalización de paths lingüísticos (`/en`) y subdominios regionales.
   - Validación de procedencia estricta fail-closed.
   - Entidad Thomas Funke probada y validada en producción (5/5 posts creados, 0 errores, 0 rechazos de provenance) con URL canónica `https://www.linkedin.com/in/dr-thomas-g-funke-96297346`.

6. **Bloque 10 — Scheduler Periódico**:
   - Daemon `scheduler` en `docker-compose.prod.yml` (`python -m app.scheduler`).
   - Cadencia semanal configurada en producción: lunes a las 06:00 `Europe/Madrid`.
   - Lookback semanal de 8 días (`WEEKLY_REFRESH_LOOKBACK_DAYS=8`).
   - Aislamiento de fallos y análisis incremental automático.
   - Desplegado y operando en Dokploy.

---

## Tests verificados

Backend:
`.venv\Scripts\python.exe -m pytest -q`
Resultado:
- **892 passed** en la suite completa de backend (0 failed, 30.97s).
- Cobertura de seed, query planner, weekly refresh, taxonomía, análisis incremental y exportación Word.

Frontend:
`npm test -- --run`
Resultado:
- **130 passed** en Vitest (8 suites passed, incluyendo catálogo, selección múltiple, dossiers y fondo documental).
- `npm run build` (`tsc && vite build`): 0 errores TypeScript, build limpio en 4.21s.

---

## Estado Actual de Desarrollo

**NO HAY DESARROLLO PENDIENTE BLOQUEANTE.**
Todos los componentes funcionales comprometidos están implementados, probados y desplegados en `origin/main` y en el entorno Dokploy.

---

## Próximo hito operativo real: Ciclo Automático del Scheduler

- **Frecuencia:** Semanal (`weekly`)
- **Día:** Lunes
- **Hora:** 06:00
- **Timezone:** `Europe/Madrid`
- **Próxima ejecución programada:** **Lunes, 19 de octubre de 2026 a las 06:00:00 CET**.
- **Ventana de retrospectiva:** 8 días (`lookback_days: 8`).

> [!IMPORTANT]
> Este será el **primer ciclo automático posterior a la actualización de la matriz de tracking**.
> No debe forzarse ni ejecutarse manualmente con antelación para permitir evaluar la ejecución desatendida del cron productivo.

---

## Verificaciones pendientes tras el ciclo del lunes

Tras la finalización del ciclo del 19/10/2026, realizar las siguientes comprobaciones de solo lectura:

1. **Estado del Scheduler:** Confirmar en logs que finaliza con `status="completed"` y 0 errores no controlados.
2. **Google News Discovery:** Confirmar que ejecutó las nuevas queries:
   - `"competencia desleal sentencia mercantil"`
   - `"inteligencia artificial propiedad intelectual demanda"`
3. **Clasificación en Nuevos Tópicos:** Comprobar si se han catalogado entradas bajo `unfair_competition` o `ai_intellectual_property`.
4. **Snapshot de Taxonomía:** Verificar que los análisis IA generados en el ciclo utilizan un `matrix_snapshot` con exactamente 20 tópicos.
5. **Exclusión de Fuente Manual:** Confirmar que `Aportación Manual / Fondo Documental` fue excluida del rastreo (`manual_source_excluded`) y no generó ningún error de extractor.
6. **Integridad de Fuentes Existentes:** Verificar que las 18 fuentes institucionales y blogs continúan operando sin regresiones.

---

## Comandos READ-ONLY para la verificación post-ciclo en Dokploy

Ejecutar en el terminal del contenedor `backend` o en el host VPS:

### 1. Logs del Scheduler
```bash
# Revisar resumen de finalización del ciclo:
docker logs <scheduler_container> | grep -E "Completed weekly refresh|Starting weekly run"

# Verificar exclusión limpia de la fuente manual:
docker logs <scheduler_container> | grep "manual_source_excluded"

# Verificar ejecución de las nuevas consultas Google News:
docker logs <scheduler_container> | grep -E "competencia desleal|propiedad intelectual"
```

### 2. Consultas SQL (Solo Lectura) en el contenedor backend
```bash
python -c "
from app.db.session import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    # 1. Comprobar que los nuevos análisis tienen 20 tópicos en el snapshot:
    res = db.execute(text('''
        SELECT jsonb_array_length(matrix_snapshot->'topics') as topics_in_snapshot, count(*)
        FROM entry_analyses
        GROUP BY 1
        ORDER BY count(*) DESC;
    ''')).fetchall()
    print('Distribución de tópicos en snapshots:', res)

    # 2. Comprobar clasificaciones en los nuevos tópicos:
    classif = db.execute(text('''
        SELECT t.code, t.name, count(eat.id) as entradas_clasificadas
        FROM tracking_topics t
        LEFT JOIN entry_analysis_topics eat ON eat.topic_id = t.id
        WHERE t.code IN ('unfair_competition', 'ai_intellectual_property')
        GROUP BY t.code, t.name;
    ''')).fetchall()
    print('Clasificaciones en nuevos tópicos:', classif)
finally:
    db.close()
"
```
