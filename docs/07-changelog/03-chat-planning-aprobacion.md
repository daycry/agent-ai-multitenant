---
plan_id: 03-chat-planning-aprobacion
title: Chat, Planning Multi-Agente y Aprobación
started_at: 2026-05-24
completed_at: 2026-05-25
status: completed
tasks_done: 31
tasks_total: 31
tasks_pending_local: []
docs_language: es
---

> **Estado:** plan **`completed`** (cerrado el 2026-05-25). Las 31
> tareas (`task_03_01`..`task_03_31`) en `done` con sus tests
> automáticos en verde. Los cinco tests humanos (`human_03_01`..
> `human_03_05`) quedan pendientes de validar por el revisor humano —
> se ejecutarán sobre la rama `plan/03-chat-planning-aprobacion` antes
> de mergear a `main`. Plan 03 cierra el ciclo "conversación →
> propuesta → plan canónico → aprobación → tarjetas en el Kanban":
> un humano puede ya hablar con el equipo de agentes en modo Planning,
> obtener un plan estructurado con coste humano + IA, aprobarlo con
> doble firma sobre umbral y materializarlo al Kanban con scopes y con
> idempotencia.

# Changelog — Plan 03 · Chat, Planning Multi-Agente y Aprobación

Tercera fase del Plan de Implementación. El Plan 01 modeló el dominio,
el Plan 02 lo puso a ejecutar. El Plan 03 da la **entrada del usuario
al sistema**: el chat multi-agente que produce planes, su revisión y
su materialización al Kanban que Plan 02 ya sabe orquestar.

## Resultado

Al cierre del plan, la plataforma permite:

1. **Conversar con el equipo.** Endpoints REST + WebSocket sobre la
   tabla `conversations`/`messages` (Fase A). Selector persistente
   de modo (Planning, Discusión, Ejecución, custom por tenant) con
   system prompts y conjuntos de tools distintos por modo (Fase B).
   La compresión jerárquica resume mensajes antiguos sin perder
   contexto.
2. **Planificar en grupo.** Sub-grafo LangGraph en modo Planning
   donde el PM actúa de portavoz y los specialists (Arquitecto,
   Backend, QA, …) intervienen cuando aportan valor (Fase C). El
   contexto incorpora chat actual + estado Kanban + planes previos +
   memoria + KBs. El equipo produce borradores estructurados con
   tablas y listas en el chat, y el humano puede dirigirse a un
   agente concreto con `@-mentions`.
3. **Generar y persistir el plan.** Botón **Generar Plan** contextual
   (aparece cuando el equipo cierra propuesta), que persiste el plan
   completo con plantilla canónica (cabecera + descripción + fases +
   tareas con sus tests automáticos). El persist hace **validación
   DAG** (detección de ciclos por DFS iterativo) y la máquina de
   estados del plan cubre los once estados del ciclo de vida
   (`draft`, `pending_approval`, `pending_second_approval`,
   `approved`, `in_progress`, `blocked`, `pending_human_validation`,
   `completed`, `cancelled`, `rejected`, `archived`) — Fase D.
4. **Revisar el plan.** Pestaña **Planes** del proyecto con listado,
   filtros y badges (Fase E). Vista de detalle densa que renderiza
   sobre el JSONB `plan.specification` la cabecera, el resumen, las
   estimaciones, el desglose de coste, las fases, el **grafo DAG de
   dependencias**, la **vista Gantt con línea crítica** (Critical
   Path Method en JS) y los comentarios in-line por plan/fase/tarea.
5. **Calcular el coste y aprobar.** Cálculo de **coste humano** (
   tarifa única por tenant × horas estimadas por tarea, default
   50 €/h) y **coste IA** (catálogo de precios cerrado + rango por
   complejidad). El endpoint
   `GET /plans/{id}/cost-breakdown?model=&hourly_rate=` permite
   simulaciones what-if. El flujo de **aprobación con doble firma
   opcional** sobre umbral configurable (`POST /plans/{id}/approve`)
   garantiza que el segundo firmante sea un usuario distinto al
   primero (`SameSignerError`). La pestaña admin
   `/admin/settings/hourly-rate` deja al tenant_admin configurar su
   tarifa horaria — Fase F.
6. **Sincronizar al Kanban.** Botón **Sincronizar al Kanban** con tres
   scopes (Plan completo / Por fase / Selección custom) que materializa
   `plan.specification.tasks` como filas de la tabla `tasks` con sus
   `task_dependencies`. La idempotencia se garantiza marcando cada
   tarea con su `inputs.plan_task_spec_id`: una segunda llamada
   reporta las ya existentes como `skipped_task_ids` sin duplicar
   nada. Una vez en el Kanban, el `PUT /projects/{p}/tasks/{t}`
   devuelve **422** si un humano intenta mover una tarjeta a
   `in_progress` / `awaiting_human_approval` / `in_review` mientras
   alguna dependencia upstream no esté `done` — Fase G.

## Fases

| Fase                                   | Tareas | Entregable                                                                                                                                                    |
| -------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A — Modelo de Conversación             | 01–04  | Tablas `conversations` + `messages` con RLS, endpoints REST + WebSocket, compresión jerárquica con sub-agente de resumen                                      |
| B — Modos de Chat                      | 05–08  | Selector persistente, system prompts y tools por modo, cambio de modo sin pérdida de contexto, modos custom por tenant                                        |
| C — Multi-Agente en Modo Planning      | 09–12  | Sub-grafo LangGraph (PM portavoz + specialists), construcción de contexto enriquecido, borradores estructurados en chat, `@-mentions`                         |
| D — Generación y Persistencia del Plan | 13–16  | Botón Generar Plan, persistencia con plantilla canónica, validación DAG iterativa, máquina de estados de 11 estados                                           |
| E — Pestaña Planes y Vista de Detalle  | 17–21  | Listado con filtros, vista de detalle, grafo DAG visual (react-flow), Gantt con CPM, comentarios in-line                                                      |
| F — Cálculo de Coste y Aprobación      | 22–26  | Coste humano (tarifa única), coste IA (catálogo cerrado + rango), desglose tabular, flujo de aprobación con doble firma, panel de tarifa horaria del tenant   |
| G — Sincronización al Kanban           | 27–31  | Endpoint `sync-to-kanban` con tres scopes, materialización con `task_dependencies`, idempotencia por `plan_task_spec_id`, DAG enforcement runtime (422), docs |

## Decisiones de arquitectura (ADRs)

- **ADR 0021** — Capa `shared-llm` con catálogo cerrado de proveedores
  (Claude Agent SDK + GitHub Copilot + Azure AI Foundry vía APIM +
  Ollama) y retirada de LiteLLM.
- **ADR 0022** — Sincronización Plan → Kanban con scopes
  (total / fase / selección) e idempotencia por `plan_task_spec_id`.
  Cubre también la decisión de implementar el DAG enforcement en la
  capa de aplicación (router) frente a la opción de trigger en base
  de datos.

## Migraciones de base de datos

- `0014` — promoción del soft-FK `plans.conversation_id` a FK real una
  vez existe la tabla `conversations`.
- `0015` — tablas `conversations` y `messages` con sus políticas RLS
  por tenant.
- `0016` — `plans.status` ampliada a 32 caracteres para encajar el
  ciclo de vida largo (`pending_human_validation`, etc.).
- `0017` — tabla `plan_comments` para los comentarios in-line.
- `0018` — `plans.first_approved_by` + `first_approved_at` para el
  trail de la doble firma.
- `0019` — `organizations.hourly_rate` + `hourly_rate_currency` para
  la tarifa horaria por tenant.

Todas reversibles, verificadas por test.

## Métricas de cierre

| Métrica                           | Valor   |
| --------------------------------- | ------- |
| Tareas totales / done             | 31 / 31 |
| Tests pytest nuevos (Plan 03)     | 81      |
| Tests Playwright nuevos (Plan 03) | 28      |
| Migraciones de base de datos      | 6       |
| ADRs aceptados durante el plan    | 2       |
| Tests humanos                     | 5       |

## Tests humanos del plan

Los cinco tests humanos quedan pendientes de validar antes del merge:

- `human_03_01` — la conversación de planning produce un plan utilizable.
- `human_03_02` — cambios de modo de chat sin pérdida de contexto.
- `human_03_03` — el detalle del plan es revisable (cabecera + DAG +
  Gantt + coste + comentarios).
- `human_03_04` — la sincronización al Kanban respeta el DAG (incluido
  el 422 al promover con dependencias pendientes).
- `human_03_05` — la doble firma sobre umbral funciona y rechaza al
  mismo firmante.

## Próximo plan

Tras cerrar este plan, el siguiente es **Plan 04** (`04-memoria-rag-kbs.md`):
memoria del agente, RAG y bases de conocimiento — la pieza que
permitirá enriquecer el contexto de planning con los planes y
ejecuciones anteriores ya producidos por Plan 02 y Plan 03.
