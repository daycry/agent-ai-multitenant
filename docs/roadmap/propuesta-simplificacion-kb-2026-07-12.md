# Propuesta: simplificar el sistema de Knowledge Bases sin perder funcionalidad

- **Fecha**: 2026-07-12
- **Origen**: pregunta del operador («el sistema de KB quizá es algo complejo… ¿se puede simplificar sin perder funcionalidad?»)
- **Estado**: propuesta — pendiente de que el operador elija qué aplicar

## Diagnóstico: por qué se PERCIBE complejo

El motor (ingesta docling → chunks → BM25+vector+RRF) es sólido y no es la fuente de la fricción. La complejidad percibida está en la **superficie de gestión**, y es real:

1. **Nada es visible sin grant explícito.** Creas una KB, subes documentos… y ningún proyecto la ve hasta que vas a otra pantalla y la grantéas (`kb_projects`). Hasta las KBs **built-in** de la plataforma requieren grant explícito. Es el papercut nº 1.
2. **Dos superficies de grants distintas** (KB↔proyecto y KB↔agente/rol, `agent_knowledge_bases`) que se gestionan en sitios distintos (pantalla de KBs vs ficha del agente) y se suman en el retrieval — difícil razonar quién ve qué.
3. **Decisiones avanzadas en primer plano**: elección de modelo de embeddings por KB (que además es inmutable en cuanto hay chunks), categorías, flags — todo al crear, cuando el 95% de los casos quiere el default.
4. **Estados de documento crípticos**: `pending`, `pending_scan`, `indexed`, `indexed_empty`, `failed` — jerga del pipeline expuesta tal cual.
5. **No hay camino corto**: para «que mi proyecto sepa de X» hay que: crear KB → subir docs → esperar ingesta → grant al proyecto (→ opcional grant a agentes). Cuatro pantallas.

## Propuesta (conserva TODA la funcionalidad; solo cambia defaults y UI)

### Q1 — Auto-grant al crear desde contexto _(el mayor alivio, backend pequeño)_

Si creas la KB **desde un proyecto** (o subes un documento desde él), se grantéa a ese proyecto automáticamente. El grant explícito sigue existiendo para compartirla con más proyectos. _(Cambio: `create_kb` acepta `project_id` opcional + la UI del proyecto ofrece «Añadir conocimiento».)_

### Q2 — «Añadir conocimiento» como flujo primario _(UI)_

Un único botón en la ficha del proyecto: arrastra ficheros → se crea (lazy) una KB implícita «Documentos de {proyecto}» ya granteada → ingesta. La gestión avanzada (KBs compartidas, categorías) queda como está para quien la necesite.

### Q3 — Built-ins activables con un clic _(UI + endpoint fino)_

En la ficha del proyecto, las KBs built-in aparecen como catálogo con toggle «Activar» (crea/borra el grant). Hoy hay que descubrirlas y grantearlas desde la pantalla global.

### Q4 — Una sola vista «¿Quién puede leer esta KB?» _(UI)_

En el detalle de la KB, un panel único que muestre y edite AMBOS tipos de grant (proyectos y agentes/roles), con los grants de agente plegados bajo «Avanzado». Elimina la caza por dos pantallas.

### Q5 — Embeddings y categorías a «Avanzado» _(UI)_

El alta de KB pide solo nombre (+ descripción). Modelo de embeddings (default `nomic-embed-text`) y categoría van en un desplegable «Avanzado». Dado que el modelo es inmutable con chunks, mostrarlo en primer plano solo invita a decisiones que luego no se pueden cambiar.

### Q6 — Estados humanos _(UI)_

Mapear estados a lenguaje de persona: `pending/pending_scan` → «Procesando…» (con matiz «esperando antivirus» en tooltip), `indexed` → «Listo», `indexed_empty` → «Sin contenido aprovechable», `failed` → «Error» (+ motivo). El estado técnico queda en el tooltip/detalle.

### Qué NO tocar

- El modelo de datos (kb_projects / agent_knowledge_bases): correcto y ya cubre todos los casos; el problema era la superficie.
- El aislamiento por grant explícito como PRIMITIVA (los defaults Q1/Q3 crean grants normales — auditables y revocables; nada pasa a ser «visible mágicamente»).
- El pipeline de ingesta (docling/antivirus/embeddings) — intacto.

## Ya mejorado en esta tanda (reduce fricción de fondo)

- Búsqueda unificada `es_unaccent` + índice acorde (P0-4) — «no encuentra lo que subí» era en parte esto.
- Auto-inyección de KB en runs (P0-2) y `rag_search` como capacidad de sistema (P0-3) — configurar KBs ahora **se nota** en los agentes sin pasos extra.
- Backfill de embeddings de chunks (P1-11b) — un apagón de Ollama ya no degrada documentos para siempre.
- El asistente busca en las KBs (`search_knowledge`, A4) — otra vía de retorno de la misma inversión.

## Esfuerzo estimado

Q1: S (backend+UI pequeños). Q2: M (UI). Q3: S. Q4: M (UI). Q5: S. Q6: S. Total ≈ 2-3 días de trabajo enfocado, sin migraciones.

**Recomendación**: aprobar Q1+Q3+Q5+Q6 (baratos, quitan el 80% de la fricción) y decidir Q2/Q4 tras probarlos.
