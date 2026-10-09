# HITCHINGS NEXT STEPS

## Estado actual

Fecha:
2026-10-09

Últimos bloques completados:
1. **Bloque 9C / 9E — LinkedIn Source Operational & Verified Production Readiness**:
   - Bright Data primario (`gd_lyy3tktm25m4avu764`) con snapshots asíncronos.
   - Apify fallback (`harvestapi/linkedin-profile-posts`) ante timeouts, errores o resultados vacíos.
   - Normalización de paths lingüísticos (`/en`) y subdominios regionales.
   - Validación de procedencia estricta fail-closed.
   - Entidad Thomas Funke probada y validada en producción (5/5 posts creados, 0 errores, 0 rechazos de provenance) con URL canónica `https://www.linkedin.com/in/dr-thomas-g-funke-96297346`.
2. **Bloque 10 — Scheduler Diario**:
   - `SCHEDULER_CADENCE=daily` a las 06:00 `Europe/Madrid`.
   - Lookback diario de 3 días.
   - Aislamiento de fallos y análisis incremental automático.
   - Desplegado y operando en Dokploy.

---

## Tests verificados

Backend:
`pytest tests/`
Resultado:
- 84 passed en tests específicos de LinkedIn (`tests/test_linkedin_discovery.py`).
- 721+ passed en la suite total de la aplicación.

Frontend:
`npm test`
Resultado:
48 passed

---

## Arquitectura actual

### Fuentes:
```
Source
 |
 +-- institucional
 |
 +-- linkedin
 |
 +-- expert_analysis
```

### Flujo LinkedIn:
```
Bright Data
    ↓
LinkedInIngestionService
    ↓
Entry
    ↓
IncrementalAnalysisService
    ↓
Gemini Triage
    ↓
Gemini Deep Analysis
```

---

## Variables importantes

- `LINKEDIN_DISCOVERY_ENABLED`: Flag global `.env` (mantenido en `false` por defecto para fail-closed).
- `BRIGHTDATA_API_TOKEN`: Token API de Bright Data para trigger y snapshots.
- `LINKEDIN_MAX_CONCURRENT_JOBS`: Límite de concurrencia de jobs simultáneos (default: 3).

### Aclaración operativa:
La activación operativa debe hacerse mediante:
- `Source.active`
- `Source.config`

No crear nuevas tablas de configuración.

---

## Próximos pasos recomendados

NEXT:

1. Ejecutar refresh completo real en entorno producción.
2. Revisar dashboard de métricas LinkedIn.
3. Analizar muestra mayor de publicaciones LinkedIn.
4. Ajustar prompt v7 únicamente si aparecen falsos positivos/falsos negativos.
5. Valorar nuevas fuentes:
   - LinkedIn personas
   - newsletters
   - blogs especializados
   - Google News
