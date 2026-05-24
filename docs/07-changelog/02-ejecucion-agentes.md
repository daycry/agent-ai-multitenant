---
plan_id: 02-ejecucion-agentes
title: Ejecución de Agentes
started_at: 2026-05-22
completed_at: 2026-05-24
status: completed
tasks_done: 35
tasks_total: 35
tasks_pending_local: []
tests_automated_passing: 471
human_validations_passing: 5
docs_language: es
---

> **Estado:** plan **`completed`** (cerrado el 2026-05-24). Las 35
> tareas (`task_02_01`..`task_02_35`) en `done` con sus tests
> automáticos en verde — 471 tests pytest + 13 tests Playwright. Los
> cinco tests humanos (`human_02_01`..`human_02_05`) validados por el
> revisor. `task_02_35` cierra el último cabo suelto de ADR 0019: el
> `egress-proxy` allowlisted que permite al sandbox alcanzar a los
> proveedores LLM sin abrir su red a internet. Sobre el dominio estático del Plan 01, el Plan
> 02 da vida al sistema: orchestrator, workers, contenedores aislados,
> el agent loop LangGraph, las tools builtin, la captura de ejecuciones,
> la UI en tiempo real, la validación humana — y, con la **Fase G**, el
> cableado que hace que un agente ejecute una tarea de principio a fin.

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
7. **Ejecutar de principio a fin** — la **Fase G** cablea todo lo
   anterior en un pipeline vivo: un evento de tarea llega al
   orchestrator, que elige agente y encola el worker; el worker conduce
   la ejecución (lanza el contenedor, streamea sus steps al stream
   Redis por-ejecución, persiste la fila `Execution`); el agent-runtime
   corre el agent loop de verdad; las salvaguardas y el motor de
   aprobación operan sobre ese run. Tres `ModelClient` reales —gateway
   LiteLLM, Claude Agent SDK, GitHub Copilot— quedan enchufados detrás
   del protocolo, además del `ScriptedModelClient` determinista.
8. **Salir a internet de forma controlada** — `task_02_35` (ADR 0019)
   añade el servicio `egress-proxy` (tinyproxy con `FilterDefaultDeny`)
   en el docker-compose. El sandbox sigue en su red `internal`, pero
   alcanza al proxy a través de ella; el proxy filtra contra una
   allowlist de hosts (Anthropic, Copilot, gateway LiteLLM). Sin esta
   pieza los tres `ModelClient` reales eran código que no podía hablar
   con su proveedor desde dentro del contenedor.

## Fases

| Fase                           | Tareas | Entregable                                                                                                                                                                                                     |
| ------------------------------ | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A — Orchestrator y Celery      | 01–04  | Servicio Orchestrator, 7 colas, políticas de asignación, `fn_compute_task_ready`                                                                                                                               |
| B — Worker y agent-runtime     | 05–09  | Imagen `agent-runtime:v1`, worker con docker SDK, aislamiento estricto, secrets, sin socket Docker                                                                                                             |
| C — Agent Loop LangGraph       | 10–14  | Grafo de 8 nodos, `executions`/`steps_log`, captura, salvaguardas, detección de loops                                                                                                                          |
| D — Tools Builtin              | 15–19  | shell*exec, file*\*, http_request, tools de orquestación, placeholders 501                                                                                                                                     |
| E — UI y Tiempo Real           | 20–23  | WebSockets, Timeline de Ejecución, Kanban reactivo                                                                                                                                                             |
| F — Validación Humana y Cierre | 24–28  | Motor de aprobación, notificación in-app, UI de aprobación, timeout 24 h, docs                                                                                                                                 |
| G — Integración End-to-End     | 29–35  | Entrypoint que corre el loop, worker que conduce el run, dispatch del orchestrator, ModelClients reales, aprobación/salvaguardas en vivo, smoke test e2e, egress controlado vía proxy allowlisted (task_02_35) |

## Decisiones de arquitectura (ADRs)

- **ADR 0011** — Bus de eventos de dominio sobre Redis Streams.
- **ADR 0012** — Aislamiento de contenedores agent-runtime.
- **ADR 0013** — Agent loop con LangGraph y captura de ejecuciones.
- **ADR 0014** — Tools builtin del agente: allowlists y efectos.
- **ADR 0015** — UI en tiempo real: WebSocket sobre Redis Streams.
- **ADR 0016** — Motor de validación humana.
- **ADR 0017** — Fase de integración end-to-end del Plan 02 (Fase G).
- **ADR 0018** — El Claude Agent SDK como `ModelClient` de un turno.
- **ADR 0019** — Egress de red del sandbox agent-runtime (Opción 1:
  egress controlado).
- **ADR 0020** — Ciclo de vida `awaiting_human_approval` en la
  `Task` (Opción A: agente libre + vuelta a backlog al aprobar /
  blocked al rechazar). Refinamiento de ADR 0016 nacido al ejecutar
  `human_02_04` y observar que la tarea aparcada no aparecía en el
  board.

## Migraciones de base de datos

- `0009` — `fn_compute_task_ready` (trigger DAG).
- `0010` — tabla `executions` con `steps_log` JSONB.
- `0011` — tabla `platform_settings` (ajustes globales de plataforma).
- `0012` — tabla `approval_requests`; `executions.status` ampliada a 32
  caracteres para `awaiting_human_approval`.
- `0013` — `tasks.status` ampliada a 32 caracteres por el mismo motivo
  (ADR 0020).

Todas reversibles, verificadas por test.

## Tests

467 tests **pytest** (unit + integration) y 13 tests **Playwright**
E2E, todos en verde. Los tests E2E son autocontenidos: mockean REST y
WebSocket e inyectan el token, sin depender del api-server.

El bucle agéntico se prueba de forma **determinista y offline** con un
`ScriptedModelClient` — ningún test automático llama a un LLM real. Los
tres `ModelClient` reales (LiteLLM, Claude Agent SDK, Copilot) se
ejercitan con transports mockeados (`httpx.MockTransport`, un `query`
inyectado), sin credenciales. El smoke test end-to-end (`task_02_34`)
recorre el pipeline completo —evento → orchestrator → worker →
contenedor → loop → BD— con el modelo scriptado.

## Pendiente para cerrar el plan

1. Tests humanos `human_02_01`..`human_02_05` validados por un revisor:
   ejecución end-to-end, aislamiento real del contenedor, salvaguardas,
   pausa por validación humana y tiempo real. `human_02_01` requiere
   además que el operador configure **uno** de los tres caminos de
   proveedor LLM (API key de LiteLLM, suscripción Claude Pro/Max, o el
   OAuth Device Flow de Copilot).
2. CI en verde sobre la rama del plan.
3. PR del plan mergeado a `main`.

Tras esos tres pasos el plan pasa a `completed`. El siguiente es el
**Plan 03** (`03-chat-planning-aprobacion.md`).

## Deuda y notas

- El nodo `recall` y los steps `memory_read` son placeholders hasta el
  Plan 04 (memoria + RAG).
- El Claude Agent SDK se integra como `ModelClient` de un turno (ADR
  0018): el loop LangGraph sigue conduciendo las iteraciones. El
  paquete `claude-agent-sdk` es un extra opcional de la imagen
  `agent-runtime`; empaquetarlo —junto con el CLI de Node— es un paso
  de despliegue del operador, sólo necesario si se elige
  `provider=claude`.
- El Device Flow de GitHub Copilot (obtención interactiva del token
  OAuth) no tiene aún UI en el admin-panel; `CopilotAuth` cubre el
  intercambio a JWT y su caché. La pantalla de "Sign in with GitHub"
  es trabajo de una fase posterior.
- La aprobación sobre el run en vivo aparca la ejecución en
  `awaiting_human_approval` antes de ejecutar la acción sensible; la
  reanudación tras la decisión humana (re-ejecución continuada) llega
  con el pool elástico del Plan 06.
- El modelo simple "un contenedor por tarea" evoluciona a pool elástico
  por plan en el Plan 06 (con los git worktrees).
