---
plan_id: 04-memoria-rag-kbs
title: Memoria, RAG y Bases de Conocimiento
status: in_progress
blocking_plan: [02-ejecucion-agentes]
started_at: 2026-05-25
completed_at: null
estimated_duration_calendar: 4-5 semanas
estimated_effort_person_days: 80-100
estimated_cost_human_eur: 32.000 € – 40.000 €
estimated_cost_ai_eur: 200 € – 350 €
created_by: system_architect
spec_sections_referenced: [10, 11]
docs_language: es
---

# Plan 04 — Memoria, RAG y Bases de Conocimiento

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `04-memoria-rag-kbs`                      |
| **Estado**                         | `pending_approval`                        |
| **Bloqueado por**                  | `02-ejecucion-agentes`                    |
| **Tiempo estimado (calendario)**   | 4-5 semanas                               |
| **Tiempo estimado (persona-días)** | 80-100                                    |
| **Previsión de coste — humano**    | 32.000 € – 40.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 200 € – 350 €                             |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/04-memoria-rag-kbs`                 |
| **Secciones del .docx**            | [10, 11]                                  |

---

## Descripción Detallada

### Resumen Ejecutivo

Implementar memoria con scopes (privada/equipo/proyecto/organización), RAG con múltiples KBs por proyecto, ingestión con Docling de 65+ formatos, búsqueda híbrida BM25 + vector + RRF + reranking, citas verificables.

### Contexto

Sin memoria, cada ejecución empieza desde cero. Sin RAG, los agentes son genéricos. Esta fase los conecta a la experiencia acumulada y a la documentación del proyecto.

### Alcance

**Entra en este plan**:

- Modelo MemoryEntry con scopes (private/team_shared/project_shared/global).
- Memoria episódica vs semántica.
- Memorizer service: indexación post-tarea automática.
- Tools memory_recall y memory_store funcionales (sustituir placeholders de Fase 2).
- Modelo KnowledgeBase, Document, Chunk con embeddings pgvector.
- Ingestión con Docling (vía docling-serve): PDFs, Office, Markdown, HTML, audio (Whisper), etc.
- Chunking estructural respetando jerarquía del documento.
- Búsqueda híbrida: BM25 + vector + RRF + reranker.
- Tool document_convert funcional (placeholder de Fase 2 sustituido).
- MCP server docling-mcp expuesto para procesamiento in-flight con promote_to_kb.
- UI de gestión de KBs por proyecto: upload, indexación con barra de progreso, listado de documentos.
- UI de visualización de citas con bounding boxes en PDFs.

**Queda fuera (otras fases)**:

- Marketplace de KBs compartidas (Fase 9).
- Visor cross-proyecto de docs (Fase 7).
- Re-indexación masiva con cambio de modelo de embeddings (operación administrativa documentada, no UI hasta Fase 12).

### Decisiones Clave

- pgvector con índice HNSW (no IVFFlat): mejor recall a costa de ligera latencia inicial.
- Docling vs LlamaParse vs Unstructured: Docling por ser open source, multi-formato, soportar audio, y tener servidor HTTP y MCP nativos.
- Reranker: cohere-rerank-v3 o bge-reranker-v2-m3 (local). Configurable por tenant.
- Modelo de embeddings: nomic-embed-text-v1.5 (local) por defecto, OpenAI text-embedding-3-small opcional con coste.
- Memorias se reescriben con embeddings nuevos si se cambia el modelo (no se pierden, se reindexan).

### Riesgos Identificados

| Riesgo                                                              | Probabilidad | Impacto | Mitigación                                                                     |
| ------------------------------------------------------------------- | ------------ | ------- | ------------------------------------------------------------------------------ |
| Docling es nuevo y puede tener inestabilidades en formatos exóticos | Media        | Medio   | Fallback a Unstructured.io para tipos no soportados. Tests con corpus variado. |
| Embeddings de baja calidad afectan a todo el sistema                | Media        | Alto    | Eval continuo de calidad de recuperación con dataset golden.                   |
| Coste de embeddings explota con tenants que ingieren TBs            | Baja         | Alto    | Quotas por tenant. Por defecto modelo local sin coste por token.               |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Memoria

#### `task_04_01` — Modelo MemoryEntry con scope, type (episódica/semántica), embedding, metadata

- [x] **Título**: Modelo MemoryEntry con scope, type (episódica/semántica), embedding, metadata
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_04_01_a
    description: "Modelo MemoryEntry con scope, type (episódica/semántica), embedding, metadata"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_memory_models.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_02` — Migración con índice HNSW de pgvector

- [x] **Título**: Migración con índice HNSW de pgvector
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_04_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_02_a
    description: "Migración con índice HNSW de pgvector"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_memory_migration.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_03` — Memorizer service: job post-tarea que destila la ejecución en MemoryEntry según política

- [ ] **Título**: Memorizer service: job post-tarea que destila la ejecución en MemoryEntry según política
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_04_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_03_a
    description: "Memorizer service: job post-tarea que destila la ejecución en MemoryEntry según política"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_memorizer.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_04` — Tool memory_recall: búsqueda híbrida BM25 + vector + RRF en memorias accesibles al agente

- [ ] **Título**: Tool memory_recall: búsqueda híbrida BM25 + vector + RRF en memorias accesibles al agente
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_04_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_04_a
    description: "Tool memory_recall: búsqueda híbrida BM25 + vector + RRF en memorias accesibles al agente"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_memory_recall.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_05` — Tool memory_store: persistencia manual de aprendizajes desde el chat

- [ ] **Título**: Tool memory_store: persistencia manual de aprendizajes desde el chat
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_04_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_05_a
    description: "Tool memory_store: persistencia manual de aprendizajes desde el chat"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_memory_store.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_06` — UI 'Memoria del Equipo' para visualizar, editar y eliminar entradas (con permisos)

- [ ] **Título**: UI 'Memoria del Equipo' para visualizar, editar y eliminar entradas (con permisos)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_04_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_06_a
    description: "UI 'Memoria del Equipo' para visualizar, editar y eliminar entradas (con permisos)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/memory-ui.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Knowledge Bases

#### `task_04_07` — Modelos KnowledgeBase, Document, Chunk con relación M:N a Project

- [ ] **Título**: Modelos KnowledgeBase, Document, Chunk con relación M:N a Project
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_04_07_a
    description: "Modelos KnowledgeBase, Document, Chunk con relación M:N a Project"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_kb_models.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_08` — Migración con índice HNSW para chunks

- [ ] **Título**: Migración con índice HNSW para chunks
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_04_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_08_a
    description: "Migración con índice HNSW para chunks"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_kb_migration.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_09` — Endpoints CRUD de KB + upload de documentos a MinIO

- [ ] **Título**: Endpoints CRUD de KB + upload de documentos a MinIO
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_04_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_09_a
    description: "Endpoints CRUD de KB + upload de documentos a MinIO"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_kb_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Ingestión con Docling

#### `task_04_10` — Despliegue de docling-serve como contenedor del stack

- [ ] **Título**: Despliegue de docling-serve como contenedor del stack
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_04_10_a
    description: "Despliegue de docling-serve como contenedor del stack"
    check_type: automated
    runtime: generic-shell
    command: "curl -f http://docling-serve:5001/health"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_11` — Worker de ingestión que llama a docling-serve y produce chunks estructurales

- [ ] **Título**: Worker de ingestión que llama a docling-serve y produce chunks estructurales
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_04_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_11_a
    description: "Worker de ingestión que llama a docling-serve y produce chunks estructurales"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_docling_ingestion.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_12` — Soporte para audio (transcripción con Whisper integrada en Docling)

- [ ] **Título**: Soporte para audio (transcripción con Whisper integrada en Docling)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_04_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_12_a
    description: "Soporte para audio (transcripción con Whisper integrada en Docling)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_audio_ingestion.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_13` — Antivirus ClamAV antes de ingestión

- [ ] **Título**: Antivirus ClamAV antes de ingestión
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: security
- **Dependencias**: `task_04_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_13_a
    description: "Antivirus ClamAV antes de ingestión"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_clamav_scan.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_14` — Embeddings con modelo configurable (default nomic-embed-text-v1.5 vía Ollama o transformers locales)

- [ ] **Título**: Embeddings con modelo configurable (default nomic-embed-text-v1.5 vía Ollama o transformers locales)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_04_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_14_a
    description: "Embeddings con modelo configurable (default nomic-embed-text-v1.5 vía Ollama o transformers locales)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_embeddings.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_15` — Progreso de indexación expuesto vía WebSocket

- [ ] **Título**: Progreso de indexación expuesto vía WebSocket
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + frontend-dev
- **Dependencias**: `task_04_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_15_a
    description: "Progreso de indexación expuesto vía WebSocket"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/ingestion-progress.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Búsqueda Híbrida y Reranking

#### `task_04_16` — Búsqueda BM25 con pg_trgm sobre texto de chunks

- [ ] **Título**: Búsqueda BM25 con pg_trgm sobre texto de chunks
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_04_16_a
    description: "Búsqueda BM25 con pg_trgm sobre texto de chunks"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_bm25_search.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_17` — Búsqueda vectorial con pgvector + HNSW

- [ ] **Título**: Búsqueda vectorial con pgvector + HNSW
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_04_16`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_17_a
    description: "Búsqueda vectorial con pgvector + HNSW"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_vector_search.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_18` — RRF (Reciprocal Rank Fusion) para combinar resultados

- [ ] **Título**: RRF (Reciprocal Rank Fusion) para combinar resultados
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_04_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_18_a
    description: "RRF (Reciprocal Rank Fusion) para combinar resultados"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_rrf.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_19` — Reranker bge-reranker-v2-m3 local (vía ONNX o transformers)

- [ ] **Título**: Reranker bge-reranker-v2-m3 local (vía ONNX o transformers)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_04_18`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_19_a
    description: "Reranker bge-reranker-v2-m3 local (vía ONNX o transformers)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_reranker.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_20` — Tool rag_search disponible para agentes

- [ ] **Título**: Tool rag_search disponible para agentes
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_04_19`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_20_a
    description: "Tool rag_search disponible para agentes"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_rag_search_tool.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase E — Docling MCP y Document Convert

#### `task_04_21` — Despliegue de docling-mcp como contenedor del stack

- [ ] **Título**: Despliegue de docling-mcp como contenedor del stack
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_04_21_a
    description: "Despliegue de docling-mcp como contenedor del stack"
    check_type: automated
    runtime: generic-shell
    command: "curl -f http://docling-mcp:3000/health"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_22` — Tool document_convert (que era placeholder) ahora invoca docling-mcp

- [ ] **Título**: Tool document_convert (que era placeholder) ahora invoca docling-mcp
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_04_21`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_22_a
    description: "Tool document_convert (que era placeholder) ahora invoca docling-mcp"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_document_convert.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_23` — Función promote_to_kb: convertir un documento procesado in-flight en entry persistente de la KB

- [ ] **Título**: Función promote_to_kb: convertir un documento procesado in-flight en entry persistente de la KB
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_04_22`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_23_a
    description: "Función promote_to_kb: convertir un documento procesado in-flight en entry persistente de la KB"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_promote_to_kb.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase F — UI y Cierre

#### `task_04_24` — UI 'Knowledge Bases' del proyecto con upload, listado, progreso, eliminar

- [ ] **Título**: UI 'Knowledge Bases' del proyecto con upload, listado, progreso, eliminar
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_04_24_a
    description: "UI 'Knowledge Bases' del proyecto con upload, listado, progreso, eliminar"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/kb-ui.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_25` — Visualización de citas con bounding boxes en PDFs (PDF.js + overlay)

- [ ] **Título**: Visualización de citas con bounding boxes en PDFs (PDF.js + overlay)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_04_24`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_25_a
    description: "Visualización de citas con bounding boxes en PDFs (PDF.js + overlay)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/citation-bboxes.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_04_26` — Documentación: guías de ingestión, ADRs sobre Docling y embeddings, changelog

- [ ] **Título**: Documentación: guías de ingestión, ADRs sobre Docling y embeddings, changelog
- **Tiempo estimado**: 8 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_04_25`
- **Tests automáticos**:
  ```yaml
  - id: auto_04_26_a
    description: "Documentación: guías de ingestión, ADRs sobre Docling y embeddings, changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/04-memoria-rag-kbs.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_04_01
  description: "Memoria mejora la calidad de tareas repetidas"
  hint: "Ejecutar la misma tarea (escribir endpoint REST estándar) dos veces con tiempo entre medias"
  checklist:
    - "La segunda ejecución cita aprendizajes de la primera vía memory_recall"
    - "El estilo de la segunda implementación es coherente con la primera"
    - "El tiempo de ejecución de la segunda es menor (gracias al contexto previo)"

- id: human_04_02
  description: "RAG funciona con corpus realista"
  hint: "Subir 10 documentos de dominio (mezcla de PDFs, .docx, .md, una grabación de reunión)"
  checklist:
    - "Docling procesa todos los formatos correctamente"
    - "Audio se transcribe e indexa"
    - "Búsqueda por término del dominio devuelve fragmentos relevantes"
    - "Búsqueda semántica encuentra conceptos sin coincidencia léxica exacta"
    - "Las citas en respuestas del agente apuntan a fragmento + página + bounding box"

- id: human_04_03
  description: "docling-mcp permite flujo conversacional"
  hint: "Usuario pega un PDF en el chat"
  checklist:
    - "El agente lo procesa con document_convert sin requerir indexación previa"
    - "El contenido relevante aparece en el chat con citas"
    - "Si el usuario dice 'añade esto a la KB del proyecto', el sistema invoca promote_to_kb y persiste"

- id: human_04_04
  description: "Scopes de memoria son respetados"
  hint: "Crear memoria privada de un agente, otra del equipo, otra del proyecto, otra global"
  checklist:
    - "Otro agente del mismo equipo NO ve la memoria privada del primero"
    - "Un agente de OTRO equipo del MISMO proyecto sí ve memorias project_shared"
    - "Memorias global son accesibles a agentes de todos los proyectos del tenant"
    - "Memorias de Tenant A NO son accesibles desde Tenant B"

- id: human_04_05
  description: "Reindexación con cambio de modelo de embeddings"
  hint: "Cambiar el modelo de embeddings desde el panel admin"
  checklist:
    - "El sistema detecta el cambio y propone reindexación"
    - "La reindexación es asíncrona con progreso visible"
    - "Las consultas durante la reindexación devuelven resultados (con el modelo antiguo) sin error"
    - "Tras completar, las consultas usan el modelo nuevo"
```

---

## Criterios de Cierre del Plan

El plan se cierra como `completed` cuando se cumplen TODOS estos criterios:

1. ✅ Todas las tareas están en estado `done`.
2. ✅ Todos los tests automáticos de las tareas están en `pass`.
3. ✅ Todos los `human_*` están marcados como `pass` por el revisor humano.
4. ✅ CI verde en `main`.
5. ✅ Generada entrada en `/docs/07-changelog/{plan_id}.md`.
6. ✅ PR del plan abierto y mergeado a `main`.

## Próximo Plan

Tras cerrar este plan, el siguiente es **Plan 05** (`05-mcp-tools-avanzadas.md`).
