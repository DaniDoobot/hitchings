# Contrato de API del Observatorio HITCHINGS (Product / Client-Facing API)

Este documento define el contrato de integración HTTP que consumirá el portal web frontend de HITCHINGS.
Todos los endpoints están bajo el namespace `/api/v1/observatory`.

> [!NOTE]
> Esta API expone **únicamente** la visión consolidada de producto: entradas, análisis vigentes, temas canónicos, fuentes y evidencias verificadas. Los modelos internos de depuración, histórico de versiones de pipeline (`v2..v6`), prompts, hashes y llamadas de coste técnico no están expuestos.

---

## 1. Mapeo Pantalla Frontend → Endpoints

| Pantalla Frontend | Endpoint(s) que Consume | Propósito |
|---|---|---|
| **Dashboard / Inicio** | `GET /api/v1/observatory/dashboard` | Alimentar KPIs, gráficas de tendencia (7d/30d), top temas, top fuentes y 5 publicaciones relevantes recientes. |
| **Observatorio / Explorador** | `GET /api/v1/observatory/entries`<br>`GET /api/v1/observatory/sources`<br>`GET /api/v1/observatory/topics` | Listado paginado con búsqueda textual, ordenación y filtros combinados. Los catálogos de fuentes y temas alimentan los selectores de filtro. |
| **Ficha de Detalle** | `GET /api/v1/observatory/entries/{entry_id}` | Vista completa de la publicación, resumen jurídico, puntos clave y citas textuales de respaldo (*evidence*). |

---

## 2. Endpoints y Especificación Técnica

### 2.1. `GET /api/v1/observatory/dashboard`
Retorna métricas clave consolidadas derivadas exclusivamente de análisis vigentes.

#### Respuesta `200 OK`
```json
{
  "total_publications": 80,
  "relevant_count": 32,
  "uncertain_count": 10,
  "not_relevant_count": 38,
  "publications_last_7_days": 23,
  "publications_last_30_days": 40,
  "relevant_last_30_days": 11,
  "top_topics": [
    { "code": "competition_litigation", "name": "Litigios de competencia", "count": 21 },
    { "code": "competition_policy", "name": "Política de competencia", "count": 20 },
    { "code": "collective_actions", "name": "Acciones colectivas", "count": 12 }
  ],
  "top_sources": [
    {
      "source_id": "97e685f0-6101-4433-8a03-9bb6fa6443c5",
      "name": "Competition Appeal Tribunal - Judgments",
      "publication_count": 20,
      "relevant_count": 19
    },
    {
      "source_id": "01b2a969-9524-4f05-b045-8f6fc814f85e",
      "name": "CNMC - Noticias",
      "publication_count": 20,
      "relevant_count": 6
    }
  ],
  "latest_relevant_entries": [
    {
      "entry_id": "ada5d125-a861-4ff8-bcf6-2b2607afd834",
      "title": "[2026] EWCA Civ 993 | Dr Liza Lovdahl Gormsen v Meta Platforms, Inc.",
      "source": {
        "id": "97e685f0-6101-4433-8a03-9bb6fa6443c5",
        "name": "Competition Appeal Tribunal - Judgments"
      },
      "published_at": "2026-08-21T14:00:00Z",
      "score": 95,
      "summary": "The Court of Appeal considered user damages in competition law claims...",
      "canonical_topics": [
        { "code": "damages_actions", "name": "Reclamaciones de daños" }
      ]
    }
  ]
}
```

---

### 2.2. `GET /api/v1/observatory/entries`
Listado paginado de publicaciones con análisis vigente.

#### Parámetros de Consulta (Query Params)
| Parámetro | Tipo | Por defecto | Validación / Descripción |
|---|---|---|---|
| `limit` | `integer` | `20` | `1` a `100`. Número de elementos por página. |
| `offset` | `integer` | `0` | $\ge 0$. Desplazamiento de elementos para paginación. |
| `sort_by` | `string` | `"published_at"` | Enum: `"published_at"` \| `"relevance_score"`. |
| `sort_order` | `string` | `"desc"` | Enum: `"asc"` \| `"desc"`. |
| `q` | `string` | `null` | Búsqueda textual case-insensitive en `title`, `excerpt`, `summary` y `key_points`. |
| `date_from` | `date` | `null` | Formato ISO `YYYY-MM-DD`. Fecha mínima de publicación (inclusive). |
| `date_to` | `date` | `null` | Formato ISO `YYYY-MM-DD`. Fecha máxima de publicación (inclusive). |
| `source_id` | `UUID` | `null` | Filtrar por un ID de fuente específico. |
| `source_ids` | `list[UUID]` | `null` | Filtrar por múltiples IDs de fuente (`?source_ids=<uuid1>&source_ids=<uuid2>`). |
| `relevance_status` | `string` | `null` | Enum: `"relevant"` \| `"uncertain"` \| `"not_relevant"`. |
| `min_relevance_score` | `integer` | `null` | `0` a `100`. Puntuación de relevancia mínima. |
| `topic_code` | `string` | `null` | Código del tema. **Expande automáticamente a todos sus subtemas**. |

#### Ejemplos de Filtro
- Solo publicaciones relevantes:
  `?relevance_status=relevant`
- Relevancia alta ($\ge 70$ puntos):
  `?min_relevance_score=70`
- Filtrar por tema padre (incluye subtemas como `damages_actions`):
  `?topic_code=private_enforcement`
- Filtrar por fuente:
  `?source_id=97e685f0-6101-4433-8a03-9bb6fa6443c5`
- Búsqueda por término:
  `?q=cartel`
- Rango de fechas:
  `?date_from=2026-09-01&date_to=2026-09-30`
- Ordenar por puntuación descendente:
  `?sort_by=relevance_score&sort_order=desc`
- Paginación:
  `?limit=20&offset=0`

#### Respuesta `200 OK`
```json
{
  "items": [
    {
      "entry_id": "27c1a107-ebfe-40d0-ba9e-d7d399c3c565",
      "title": "Case C-60/25 [Livronsa] | Judgment",
      "source": {
        "id": "e6f47df4-3d9a-412e-9d2a-89a03fc5b931",
        "name": "Court of Justice of the European Union - Case Law"
      },
      "published_at": "2026-09-03T02:00:00Z",
      "url": "https://curia.europa.eu/juris/document/document.jsf?text=&docid=289000",
      "content_type": "judgment",
      "relevance": {
        "status": "relevant",
        "score": 95,
        "confidence": 1.0
      },
      "summary": "Sentencia del Tribunal de Justicia en el asunto Livronsa...",
      "canonical_topics": [
        {
          "code": "preliminary_rulings",
          "name": "Cuestiones prejudiciales"
        }
      ],
      "canonical_primary_topic": {
        "code": "preliminary_rulings",
        "name": "Cuestiones prejudiciales"
      },
      "key_points": [
        "Interpretación del artículo 102 TFUE.",
        "Obligaciones de suministro de medicamentos."
      ]
    }
  ],
  "total": 80,
  "limit": 20,
  "offset": 0
}
```

---

### 2.3. `GET /api/v1/observatory/entries/{entry_id}`
Ficha de detalle de una publicación con evidencias verificadas.

#### Respuestas
- `200 OK`: Entidad encontrada y con análisis vigente.
- `404 Not Found`: Si la entrada no existe o carece de análisis vigente.

#### Respuesta `200 OK`
```json
{
  "entry_id": "27c1a107-ebfe-40d0-ba9e-d7d399c3c565",
  "title": "Case C-60/25 [Livronsa] | Judgment",
  "source": {
    "id": "e6f47df4-3d9a-412e-9d2a-89a03fc5b931",
    "name": "Court of Justice of the European Union - Case Law"
  },
  "published_at": "2026-09-03T02:00:00Z",
  "url": "https://curia.europa.eu/juris/document/document.jsf?text=&docid=289000",
  "content_type": "judgment",
  "relevance": {
    "status": "relevant",
    "score": 95,
    "confidence": 1.0
  },
  "summary": "Sentencia del Tribunal de Justicia sobre abuso de posición dominante...",
  "canonical_topics": [
    { "code": "abuse_dominance", "name": "Abuso de posición dominante" }
  ],
  "canonical_primary_topic": {
    "code": "abuse_dominance", "name": "Abuso de posición dominante"
  },
  "key_points": [
    "Determinación del mercado relevante de distribución farmacéutica.",
    "Doctrina de las instalaciones esenciales aplicada a licencias."
  ],
  "evidence": {
    "source": "deep",
    "summary_quotes": [
      {
        "source_field": "content",
        "quote": "Article 102 TFEU must be interpreted as precluding a dominant undertaking..."
      }
    ],
    "key_points": [
      {
        "point": "Determinación del mercado relevante de distribución farmacéutica.",
        "quotes": [
          {
            "source_field": "content",
            "quote": "The relevant market comprises all products regarded as interchangeable..."
          }
        ]
      }
    ]
  }
}
```

---

### 2.4. `GET /api/v1/observatory/sources`
Catálogo de fuentes para poblar desplegables de filtro.

#### Respuesta `200 OK`
```json
[
  {
    "id": "01b2a969-9524-4f05-b045-8f6fc814f85e",
    "name": "CNMC - Noticias",
    "type": "website",
    "url": "https://www.cnmc.es/prensa",
    "entry_count": 20,
    "latest_published_at": "2026-09-01T08:08:02+02:00"
  },
  {
    "id": "97e685f0-6101-4433-8a03-9bb6fa6443c5",
    "name": "Competition Appeal Tribunal - Judgments",
    "type": "website",
    "url": "https://www.catribunal.org.uk/judgments",
    "entry_count": 20,
    "latest_published_at": "2026-08-21T14:00:00+02:00"
  }
]
```

---

### 2.5. `GET /api/v1/observatory/topics`
Taxonomía jerárquica de temas para selectores en árbol.

#### Respuesta `200 OK`
```json
[
  {
    "code": "private_enforcement",
    "name": "Aplicación privada",
    "parent_code": null,
    "description": "Litigios ante tribunales civiles ordinarios",
    "children": [
      {
        "code": "damages_actions",
        "name": "Acciones de daños y perjuicios",
        "parent_code": "private_enforcement",
        "description": "Reclamaciones de indemnización por cárteles",
        "children": []
      },
      {
        "code": "collective_actions",
        "name": "Acciones colectivas y representativas",
        "parent_code": "private_enforcement",
        "description": "Opt-in y opt-out en competencia",
        "children": []
      }
    ]
  }
]
```

---

## 3. Manejo de Errores

| Código HTTP | Motivo | Formato de Respuesta |
|---|---|---|
| `404 Not Found` | Recurso no encontrado (ej. `entry_id` inexistente o sin análisis vigente). | `{"detail": "Observatory entry '<id>' not found or has no current analysis"}` |
| `422 Unprocessable Entity` | Parámetros inválidos (ej. `limit=101`, `min_relevance_score=150`, `sort_by=invalido`, `date_from=not-a-date`). | Formato estándar de validación FastAPI con detalle de campos infractores. |

---

## 4. Política CORS
El backend permite solicitudes Cross-Origin desde el navegador siempre que el origen esté declarado en `CORS_ALLOWED_ORIGINS` (ej. `http://localhost:5173` para Vite).
- Las solicitudes preflight `OPTIONS` son respondidas automáticamente con `200 OK` y cabeceras `Access-Control-Allow-*`.
- Se permite el envío de credenciales (`Access-Control-Allow-Credentials: true`).
- Orígenes no configurados son rechazados sin cabecera de autorización.