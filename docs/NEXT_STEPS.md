# HITCHINGS NEXT STEPS

## Estado actual

Fecha:
2026-09-16

Último bloque completado:
LinkedIn Source Operational Production Ready

Incluye:
- Bright Data discovery
- snapshots async
- provenance
- deduplicación
- análisis Gemini
- Weekly Refresh
- configuración dinámica Source.config
- API sources status
- API sources metrics
- filtros origen Observatorio

---

## Último commit estable

Hash commit actual:
`fb8d78f`

Mensaje commit:
`feat(observatorio): operational linkedin source, dynamic config, status/metrics APIs, and origin filters`

---

## Tests verificados

Backend:
`pytest tests/`
Resultado:
642 passed

Frontend:
`bun run test`
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
