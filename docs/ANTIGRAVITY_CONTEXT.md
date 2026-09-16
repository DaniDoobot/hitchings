# CONTEXTO PARA CONTINUAR DESARROLLO

Este repositorio pertenece al Observatorio HITCHINGS.

Estado:
LinkedIn está cerrado como fuente operativa.

No volver a investigar:
- Bright Data API
- discovery_new
- snapshots
- payloads
- provenance

Ya está resuelto.

Antes de modificar arquitectura:
Revisar:
- Source model
- WeeklyRefreshService
- AnalysisPipelineService
- IncrementalAnalysisService

Mantener siempre:
- fail closed
- separación de orígenes
- no exponer proveedores técnicos en UI
- no duplicar servicios
