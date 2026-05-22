---
plan_id: 02-ejecucion-agentes
title: Ejecución de Agentes
started_at: 2026-05-22
completed_at: null
status: pending_human_validation
tasks_done: 28
tasks_total: 28
tasks_pending_local: []
tests_automated_passing: 194
human_validations_passing: 0
docs_language: es
---

> **Estado:** todas las tareas (`task_02_01`..`task_02_28`) están en
> `done` con sus tests automáticos en verde — 181 tests pytest + 13
> tests Playwright. El plan queda en `pending_human_validation`:
> esperando los cinco tests humanos (`human_02_01`..`human_02_05`) y el
> merge del PR a `main`. Sobre el dominio estático del Plan 01, el Plan
> 02 da vida al sistema: orchestrator, workers, contenedores aislados,
> el agent loop LangGraph, las tools builtin, la captura de ejecuciones,
> la UI en tiempo real y la validación humana.

# Changelog — Plan 02 · Ejecución de Agentes

Fase **2** del Plan de Implementación. El Plan 01 dejó el dominio
modelado pero estático; el Plan 02 lo pone a ejecutar: agentes reales
corriendo un bucle de razonamiento dentro de contenedores aislados,
observable en tiempo real, con validación humana configurable.

## Resultado

Al cierre del plan, la plataforma puede:

1. **Orquestar** — el servicio Orchestrator escucha el bus de eventos
   (Redis Streams, `events:tasks`), Celery declara sus 7 colas
   (default, heavy, gpu, ingestion, test, review, privileged) y cuatro
   políticas de asignación (skill_match por similitud coseno,
   load_balanced, round_robin, manual) eligen el agente. El trigger
   `fn_compute_task_ready` promueve una tarea a `ready` en cuanto sus
   dependencias DAG llegan a `done`.
2. **Aislar** — el worker lanza la imagen `agent-runtime:v1` con
   cap-drop ALL, sistema de ficheros read-only, red dedicada interna,
   seccomp default-deny y sin socket Docker; las credenciales entran
   por `/run/secrets`, nunca por entorno.
3. **Razonar** — el agent loop es un grafo LangGraph de ocho nodos
   (`perceive → recall → plan → act → observe → reflect → finalize →
self_review`). Cada ejecución se captura en la tabla `executions`
   con su `steps_log` JSONB — node / model_call / tool_call /
   memory_read, con tokens y coste — bajo salvaguardas (max_iterations,
   max_tokens, max_cost, max_wall_clock, max_tool_calls,
   max_review_retries) y detección de loops repetitivos.
4. **Actuar** — tools builtin funcionales: `shell_exec` (allowlist de
   comandos), `file_read/write/list` (acotadas a /workspace),
   `http_request` (allowlist de dominios, timeout, tope de body),
   `kanban_update/task_comment/notify_user/agent_invoke` (emiten
   efectos), y placeholders `memory_*`/`document_convert` que devuelven
   501 hasta Plan 04.
5. **Observar en vivo** — WebSockets `/ws/executions/{id}` y
   `/ws/kanban/{project_id}` sobre Redis Streams; UI Timeline de
   Ejecución (jerárquica, expansible, con coste y tiempo por paso) y
   Kanban dual reactivo en tiempo real.
6. **Pedir permiso** — el motor de `human_approval_policy` intercepta
   las acciones de categorías sensibles, aparca la ejecución en
   `awaiting_human_approval`, persiste la solicitud, la notifica
   in-app y ofrece la UI de aprobar/rechazar con motivo. Una solicitud
   sin respuesta expira tras 24 h (configurable).

## Fases

| Fase                           | Tareas | Entregable                                                                                         |
| ------------------------------ | ------ | -------------------------------------------------------------------------------------------------- |
| A — Orchestrator y Celery      | 01–04  | Servicio Orchestrator, 7 colas, políticas de asignación, `fn_compute_task_ready`                   |
| B — Worker y agent-runtime     | 05–09  | Imagen `agent-runtime:v1`, worker con docker SDK, aislamiento estricto, secrets, sin socket Docker |
| C — Agent Loop LangGraph       | 10–14  | Grafo de 8 nodos, `executions`/`steps_log`, captura, salvaguardas, detección de loops              |
| D — Tools Builtin              | 15–19  | shell*exec, file*\*, http_request, tools de orquestación, placeholders 501                         |
| E — UI y Tiempo Real           | 20–23  | WebSockets, Timeline de Ejecución, Kanban reactivo                                                 |
| F — Validación Humana y Cierre | 24–28  | Motor de aprobación, notificación in-app, UI de aprobación, timeout 24 h, docs                     |

## Decisiones de arquitectura (ADRs)

- **ADR 0011** — Bus de eventos de dominio sobre Redis Streams.
- **ADR 0012** — Aislamiento de contenedores agent-runtime.
- **ADR 0013** — Agent loop con LangGraph y captura de ejecuciones.
- **ADR 0014** — Tools builtin del agente: allowlists y efectos.
- **ADR 0015** — UI en tiempo real: WebSocket sobre Redis Streams.
- **ADR 0016** — Motor de validación humana.

## Migraciones de base de datos

- `0009` — `fn_compute_task_ready` (trigger DAG).
- `0010` — tabla `executions` con `steps_log` JSONB.
- `0011` — tabla `platform_settings` (ajustes globales de plataforma).
- `0012` — tabla `approval_requests`; `executions.status` ampliada a 32
  caracteres para `awaiting_human_approval`.

Todas reversibles, verificadas por test.

## Tests

181 tests **pytest** (unit + integration) y 13 tests **Playwright**
E2E, todos en verde. Los tests E2E son autocontenidos: mockean REST y
WebSocket e inyectan el token, sin depender del api-server.

El bucle agéntico se prueba de forma **determinista y offline** con un
`ScriptedModelClient` — ningún test llama a un LLM real.

## Pendiente para cerrar el plan

1. Tests humanos `human_02_01`..`human_02_05` validados por un revisor:
   ejecución end-to-end, aislamiento real del contenedor, salvaguardas,
   pausa por validación humana y tiempo real.
2. CI en verde sobre la rama del plan.
3. PR del plan mergeado a `main`.

Tras esos tres pasos el plan pasa a `completed`. El siguiente es el
**Plan 03** (`03-chat-planning-aprobacion.md`).

## Deuda y notas

- El nodo `recall` y los steps `memory_read` son placeholders hasta el
  Plan 04 (memoria + RAG).
- El cliente LLM real (LiteLLM) se enchufa detrás del protocolo
  `ModelClient` en una tarea posterior; Fase C lo ejercita con el
  cliente scriptado.
- La aplicación de los efectos de las tools de orquestación por el
  worker, y la integración del motor de aprobación con el agent loop
  en ejecución, son trabajo de wiring de fases posteriores.
- El modelo simple "un contenedor por tarea" evoluciona a pool elástico
  por plan en el Plan 06 (con los git worktrees).
