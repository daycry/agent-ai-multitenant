---
plan_id: remediacion-auditoria-dirigida-2026-07-16
title: Remediación de auditoría dirigida — tools por proveedor, notificaciones visibles, contabilidad, monitorización
status: in_progress
blocking_plan: []
started_at: 2026-07-16
completed_at: null
estimated_duration_calendar: 1-2 semanas
estimated_effort_person_days: 9
created_by: claude-fable-5-audit-2026-07-16
docs_language: es
priority: P0
source_audit: auditoria-dirigida-2026-07-16
---

# Plan de remediación — Auditoría dirigida 2026-07-16

## Cabecera

| Campo             | Valor                                                                                                                         |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| **ID del Plan**   | `remediacion-auditoria-dirigida-2026-07-16`                                                                                   |
| **Prioridad**     | P0 (fases A-C) · P1/P2 (fases D-F)                                                                                            |
| **Bloqueado por** | Ninguno; no solapa con `remediacion-auditoria-integral-2026-07-14` (pending_approval) ni con la serie prod                    |
| **Rama**          | `plan/runs-visor-trabajo` (continuidad del trabajo en curso)                                                                  |
| **Mandato**       | Orden directa del operador 2026-07-16: «crea el docs/roadmap e implementalo» — implementación autónoma TDD + commits atómicos |

## Resumen

Implementa el delta confirmado por
[`auditoria-dirigida-2026-07-16`](./auditoria-dirigida-2026-07-16.md)
(hallazgos AUD16-01…27). No duplica planes existentes: los hallazgos con dueño
(AUD14-01…08 → plan 07-14; exporters/Loki/trazas → prod-08; junctions RLS →
prod-14; ADR 0108 → decisión del operador) quedan fuera. Los ítems que
requieren credenciales o decisión humana están en «Gated / operador».

## Alcance

**Entra**: envelope OpenAI de las tools de finalización + fidelidad de schema
por proveedor; notificaciones visibles para humanos (inbox de plataforma +
contenido persistido); precio de catálogo del modelo real y herencia de modelo
en el destilador de memorias; robustez de runs (effects honestos, aborts de
infra, trazabilidad de redispatch); huecos de monitorización de este host y
del sampler; pulido P2 accionable por código.

**Queda fuera**: hardening del overlay de monitoring y el resto de AUD14
(plan 07-14), despliegue/configuración de canales externos y neonize
(operador), smoke e2e con credenciales copilot/azure reales (operador),
Loki/OTLP/exporters (prod-08), decisión ADR 0108.

## Tareas

### Fase A — Camino HTTP de tools por proveedor (AUD16-01, -03, -04, -05, -06)

#### `task_aud16_a1` — Envelope OpenAI de `submit_result`/`submit_verdict` + tests de wire-format

- [ ] **Título**: Las tools de finalización viajan con el envelope `{"type":"function","function":…}` a los 3 kinds HTTP
- **Descripción**: `_SUBMIT_RESULT_TOOL` y `_SUBMIT_VERDICT_TOOL`
  (`docker/agent-runtimes/agent-runtime/agent_runtime/providers.py:141-201`)
  son dicts planos; envolverlos como ya hace `_SUBMIT_PROGRESS_TOOL` y
  verificar que claude_sdk (`_unwrap_tool_schemas`) sigue tolerándolos.
  Actualizar el test rojo desde el 27-06
  (`tests/integration/test_model_clients.py::test_azure_decide_targets_apim_url_with_subscription_key`)
  y añadir asserts de wire-format (todas las entradas de `body["tools"]`
  llevan envelope) para decide+review en ollama, copilot y azure_foundry.
- **Tiempo**: 0,5 d · **Complejidad**: m · **Hallazgo**: AUD16-01 (crítico)
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_a1
    runtime: python-pytest
    command: "pytest tests/integration/test_model_clients.py -v"
  ```

#### `task_aud16_a2` — Retirar `search_code` de los system prompts del runtime

- [ ] **Título**: Los prompts solo anuncian tools que existen
- **Descripción**: `providers.py:99` (`_DECIDE_SYSTEM`) y `:126-128`
  (`_REVIEW_RUN_SYSTEM`) nombran `search_code`, no cableada (7/7 llamadas
  fallidas en 14 días). Retirarla de ambos prompts y añadir test que cruce las
  tools nombradas en los prompts contra el catálogo runtime-wired.
- **Tiempo**: 0,25 d · **Complejidad**: s · **Hallazgo**: AUD16-04
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_a2
    runtime: python-pytest
    command: "pytest docker/agent-runtimes/agent-runtime/tests -k 'prompt or system' -v"
  ```

#### `task_aud16_a3` — claude_sdk recibe el JSON Schema completo de cada tool

- [ ] **Título**: Sin degradar `required`/`enum`/descriptions/anidados
- **Descripción**: `_json_schema_to_tool_schema`
  (`packages/shared-llm/src/shared_llm/providers/claude_agent.py:745-764`)
  mapea a `{campo: tipo}`; pasar el JSON Schema crudo al `@tool` del SDK
  (lo admite como dict) conservando la tolerancia a schemas envueltos/planos.
  Test: una tool con enum+required+objeto anidado llega íntegra al SDK.
- **Tiempo**: 0,5 d · **Complejidad**: m · **Hallazgo**: AUD16-05
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_a3
    runtime: python-pytest
    command: "pytest tests/unit -k 'claude_agent and (schema or tool)' -v"
  ```

#### `task_aud16_a4` — Streaming OpenAI-compat no pierde `tool_calls` en silencio

- [ ] **Título**: `parse_sse_delta` acumula deltas de tool_calls (o falla explícito)
- **Descripción**: `_openai_compat.py:126-146` solo extrae `delta.content`.
  Acumular los deltas de `tool_calls` y exponerlos al final del stream;
  documentar la semántica en `base.py`. Hoy sin uso con tools (solo
  FINISH_NUDGE del asistente), pero es una pérdida silenciosa latente.
- **Tiempo**: 0,5 d · **Complejidad**: m · **Hallazgo**: AUD16-06
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_a4
    runtime: python-pytest
    command: "pytest tests/unit -k 'openai_compat or sse' -v"
  ```

### Fase B — Notificaciones visibles para humanos (AUD16-10, -11)

#### `task_aud16_b1` — `notification_logs` persiste subject/body para in_app

- [ ] **Título**: Una notificación in-app dice QUÉ pasó, no solo que pasó algo
- **Descripción**: Migración reversible con columnas `subject`/`body`
  (truncadas, p. ej. 200/2000 chars) en `notification_logs`; el dispatcher
  (`apps/notification-dispatcher/.../tasks.py:450-460`) deja de descartar el
  render y lo persiste para `channel_type=in_app`; `_log_to_response`
  (`routers/notifications.py:578-590`) lo expone; el inbox del admin-panel lo
  muestra.
- **Tiempo**: 0,75 d · **Complejidad**: m · **Hallazgo**: AUD16-11
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_b1_a
    runtime: python-pytest
    command: "pytest tests/unit -k 'notification and (body or subject or log)' -v && pytest tests/migrations -v"
  - id: auto_aud16_b1_b
    runtime: node-vitest
    command: "npm --prefix apps/admin-panel run test -- inbox"
  ```

#### `task_aud16_b2` — Inbox de plataforma: el System Admin ve los envíos `tenant_id=NULL`

- [ ] **Título**: Las notifs platform-scoped dejan de ser invisibles
- **Descripción**: `GET /notifications/logs`
  (`routers/notifications.py:615-683`) excluye tenant NULL por diseño;
  añadir el camino de plataforma (endpoint admin o inclusión condicionada a
  `is_system_admin`), con mark-read funcional (`notification_log_reads`) y
  sin abrir los envíos de plataforma a tenant admins. El inbox del
  admin-panel (`app/admin/notifications/inbox/page.tsx`) los lista.
- **Tiempo**: 1 d · **Complejidad**: l · **Hallazgo**: AUD16-10
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_b2_a
    runtime: python-pytest
    command: "pytest tests/integration -k 'notification' -v"
  - id: auto_aud16_b2_b
    runtime: python-pytest
    command: "pytest tests/integration -k 'cross_tenant and notification' -v"
  ```

### Fase C — Contabilidad y memoria (AUD16-15, -14, -17, -18)

#### `task_aud16_c1` — `claude-opus-4-8` (y familia) en `model_prices`: coste facturable deja de estar ciego

- [ ] **Título**: `price_snapshot` resuelve el modelo real en uso
- **Descripción**: 128/128 executions con `price_snapshot_cost_usd` NULL
  («no current price in catalog»). Añadir el precio del modelo al catálogo
  (seed/migración con fuente y fecha) y/o matching de alias en el snapshot;
  test de que un run con `claude-opus-4-8` produce snapshot `available:true`
  y coste > 0. Añadir aviso operativo si el snapshot corre N días sin precio.
- **Tiempo**: 0,75 d · **Complejidad**: m · **Hallazgo**: AUD16-15
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_c1
    runtime: python-pytest
    command: "pytest tests/unit -k 'price_snapshot or model_price' -v"
  ```

#### `task_aud16_c2` — El destilador de memorias usa el modelo REAL del agente (herencia ADR 0065/0082)

- [ ] **Título**: `_build_agent_llm` resuelve plataforma→proyecto→agente en vez de devolver None
- **Descripción**: El camino primario de F2.1 lee `agent.model_config` crudo
  y con modelos heredados devuelve None en silencio → 100% de memorias
  destiladas por el fallback `llama3.2:1b` (~21% ruido). Usar el mismo
  resolver por provider_id/kind que el dispatch; loguear (y contar en
  métrica/log estructurado) cuándo se cae al fallback.
- **Tiempo**: 0,75 d · **Complejidad**: m · **Hallazgo**: AUD16-14
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_c2
    runtime: python-pytest
    command: "pytest tests/unit -k 'memoriz and (llm or distill or model)' -v"
  ```

#### `task_aud16_c3` — Default real de estados memorizables incluye fracasos

- [ ] **Título**: P1-1(a) efectivo en el camino del worker
- **Descripción**: Sin fila en `platform_settings`,
  `get_memorizable_statuses()` devuelve `{'done'}` y el default nuevo de
  `policy.py` solo lo ven los tests. Alinear
  `DEFAULT_MEMORY_MEMORIZABLE_STATUSES` con el default de policy.py y añadir
  test que ejercite el camino real del worker.
- **Tiempo**: 0,25 d · **Complejidad**: s · **Hallazgo**: AUD16-17
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_c3
    runtime: python-pytest
    command: "pytest tests/unit -k 'memorizable' -v"
  ```

#### `task_aud16_c4` — Dedup de contenido en `recall()` + consolidación de duplicados preexistentes

- [ ] **Título**: Un slot del recall no se gasta dos veces en la misma lección
- **Descripción**: Dedup por contenido normalizado en la fusión RRF de
  `recall()` (cinturón); tarea de mantenimiento idempotente que consolida
  (soft-delete conservando la más antigua) los duplicados exactos
  preexistentes (5 filas idénticas del 07-07). La ejecución contra la BD viva
  se hace en el despliegue de este plan y se verifica con SQL antes/después.
- **Tiempo**: 0,5 d · **Complejidad**: m · **Hallazgo**: AUD16-18
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_c4
    runtime: python-pytest
    command: "pytest tests/unit -k 'recall and dedup or consolidat' -v"
  ```

### Fase D — Robustez de runs (AUD16-02, -20, -21, -22, -23)

#### `task_aud16_d1` — Tools de orquestación honestas: efectos drenados o error explícito, nunca `ok=true` falso

- [ ] **Título**: Fin del éxito falso de kanban_update/task_comment/agent_invoke/notify_user
- **Descripción**: Los effects van a un `OrchestrationSink` que nadie drena
  (`orchestration_tools.py:29-41`; el worker solo procesa
  step/finished/error en `workers/execution.py:1113-1130`). Drenar en el
  worker los efectos seguros (`task_comment` → comentario persistido;
  `notify_user` → evento de notificación) y, para los no cableados aún
  (`kanban_update`, `agent_invoke`), devolver al modelo un error honesto y
  retirarlos del anuncio (`SYSTEM_TOOL_NAMES`,
  `agent_tool_schemas.py:201-213`) hasta que exista su consumidor.
- **Tiempo**: 1 d · **Complejidad**: l · **Hallazgo**: AUD16-02
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_d1
    runtime: python-pytest
    command: "pytest tests/unit -k 'orchestration' -v && pytest tests/integration -k 'orchestration or task_comment' -v"
  ```

#### `task_aud16_d2` — Fallos de transporte repetidos de `stack_exec` abortan el run

- [ ] **Título**: `stack_exec_unavailable` en vez de quemar 50 iteraciones
- **Descripción**: 8 fallos 5xx idénticos de infraestructura no cortaron el
  run 019f21be-e5c0 (las guardas por novedad no aplican: stack_exec es
  producing-tool, `tool_classification.py:20-27`). Contador de fallos de
  transporte consecutivos (5xx/timeout, no errores del toolchain del
  usuario) → abort con `abort_code=stack_exec_unavailable` y escalado.
- **Tiempo**: 0,5 d · **Complejidad**: m · **Hallazgo**: AUD16-20
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_d2
    runtime: python-pytest
    command: "pytest tests/unit -k 'stack_exec and (unavailable or transport or safeguard)' -v"
  ```

#### `task_aud16_d3` — Todo redispatch/reapertura deja `task_audit_events`; reaper/supersede dejan skip_reason

- [ ] **Título**: La cronología de una task es reconstruible desde BD
- **Descripción**: Los relanzamientos (sweeper, reconciler,
  promote_ready_plans) y las reaperturas de task done no generan
  task_audit_events; 8 runs finalizados por reaper/supersede quedaron sin
  `memorize_skip_reason`. Emitir evento con actor+motivo en todos esos
  caminos y sellar skip_reason en los finalizadores administrativos.
- **Tiempo**: 0,75 d · **Complejidad**: m · **Hallazgo**: AUD16-21
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_d3
    runtime: python-pytest
    command: "pytest tests/unit -k 'audit_event and (sweep or reconcil or redispatch or reopen)' -v"
  ```

#### `task_aud16_d4` — El `what_to_fix` del reviewer se acota a acciones ejecutables por el agente

- [ ] **Título**: El reviewer no puede exigir commit/push ni acciones worker-side
- **Descripción**: Histórico: un reviewer pidió reintentar el commit
  (imposible en sandbox) y la task bucleó. Añadir al prompt del reviewer la
  restricción explícita (ficheros del worktree, stack_exec) y un test del
  prompt; opcional: post-proceso que descarte instrucciones imposibles
  conocidas (commit/push/deploy).
- **Tiempo**: 0,25 d · **Complejidad**: s · **Hallazgo**: AUD16-22
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_d4
    runtime: python-pytest
    command: "pytest docker/agent-runtimes/agent-runtime/tests -k 'review and prompt' -v"
  ```

#### `task_aud16_d5` — Chequeo proactivo de la credencial claude_sdk

- [ ] **Título**: La caducidad del OAuth se detecta ANTES de quemar runs
- **Descripción**: `provider_error` ×17 (07-02→07-08) por oauth caducado y
  cuota. Extender el diagnóstico del provider (camino ADR 0082) con un check
  de sesión barato y exponer el estado en el panel admin de providers;
  emitir evento de notificación `provider_credential_invalid` al fallar.
- **Tiempo**: 0,75 d · **Complejidad**: m · **Hallazgo**: AUD16-23
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_d5
    runtime: python-pytest
    command: "pytest tests/unit -k 'claude and (diagnos or credential)' -v"
  ```

### Fase E — Monitorización de este host (AUD16-07, -08, -09, -19)

#### `task_aud16_e1` — cAdvisor ve los contenedores en Docker Desktop (o el fallo deja de ser silencioso)

- [ ] **Título**: Paneles per-container y `ContainerOOMKilled` vivos, o alerta de cadvisor degradado
- **Descripción**: cAdvisor v0.49.1 no resuelve la capa RW con el containerd
  snapshotter (0 contenedores visibles, healthcheck verde). Probar bump de
  imagen/flags; si no hay fix viable en Docker Desktop, añadir regla
  `CadvisorDegraded` (`count(container_last_seen) <= 1`) para que el fallo
  sea visible, y documentar la gotcha en `docs/03-guides/gotchas/`.
- **Tiempo**: 0,75 d · **Complejidad**: m · **Hallazgo**: AUD16-07
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_e1
    runtime: python-pytest
    command: "pytest tests/docs tests/security -k 'monitoring or compose' -v"
  ```

#### `task_aud16_e2` — El sampler de métricas es distinguible de «no hay datos»

- [ ] **Título**: Familias siempre emitidas + heartbeat del sampler + regla de staleness
- **Descripción**: `queue_metrics.py:76,83` omite familias enteras con dict
  vacío. Emitir siempre HELP/TYPE con series a 0, añadir
  `agentic_sampler_last_run_timestamp_seconds` y regla Prometheus de
  staleness (>5 min sin heartbeat → alerta).
- **Tiempo**: 0,5 d · **Complejidad**: s · **Hallazgo**: AUD16-09
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_e2
    runtime: python-pytest
    command: "pytest tests/unit -k 'queue_metrics or sampler' -v"
  ```

#### `task_aud16_e3` — El backup sin copia offsite se ve y alerta

- [ ] **Título**: Métrica `agentic_backup_offsite_*` + alerta si no hay upload N días
- **Descripción**: `uploaded=[]` en todos los bundles. Emitir métrica de
  uploads (count + timestamp del último éxito) desde el task de backup y
  regla de alerta; documentar en el runbook cómo configurar el destino
  offsite (la configuración real es del operador).
- **Tiempo**: 0,5 d · **Complejidad**: s · **Hallazgo**: AUD16-19
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_e3
    runtime: python-pytest
    command: "pytest tests/unit -k 'backup and (offsite or metric)' -v"
  ```

#### `task_aud16_e4` — Menores de monitorización

- [ ] **Título**: Healthcheck de node-exporter, egress del update-checker de Grafana, runbook de disco en Windows-dev
- **Descripción**: Añadir healthcheck a node-exporter (único del overlay sin
  él); cerrar el check de updates de plugins de Grafana
  (`GF_SECURITY_DISABLE_GRAVATAR`/`GF_PLUGINS_*` según corresponda);
  documentar en runbook que en Windows-dev la vigilancia de disco del host es
  inexistente (AUD16-08).
- **Tiempo**: 0,25 d · **Complejidad**: s · **Hallazgos**: AUD16-08 + menores B
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_e4
    runtime: python-pytest
    command: "docker compose -f docker/docker-compose.yml -f docker/docker-compose.monitoring.yml config --quiet && pytest tests/docs -v"
  ```

### Fase F — Pulido P2 (colaterales accionables)

#### `task_aud16_f1` — Cola `notifications.priority` con exchange/routing propios

- [ ] **Título**: `Queue(name, Exchange(name), routing_key=name)` antes de separar workers por lane
- **Tiempo**: 0,25 d · **Complejidad**: s · **Hallazgo**: menor C (H7)
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_f1
    runtime: python-pytest
    command: "pytest tests/unit -k 'notification and (queue or celery_app or routing)' -v"
  ```

#### `task_aud16_f2` — Bandeja de escaladas sin runs de tasks ya done

- [ ] **Título**: 19 `needs_human_review` huérfanos dejan de inflar la bandeja
- **Descripción**: Filtrar (o marcar resueltos) los runs needs_human_review
  cuya task ya está done, en el endpoint del panel de escaladas.
- **Tiempo**: 0,25 d · **Complejidad**: s · **Hallazgo**: AUD16-25
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_f2
    runtime: python-pytest
    command: "pytest tests/unit -k 'escalation or needs_human_review' -v"
  ```

#### `task_aud16_f3` — Runtime-template sin fricción: HOME/.composer escribible

- [ ] **Título**: composer deja de ruidear en cada invocación
- **Descripción**: Ajustar la imagen del runtime-template (HOME escribible o
  COMPOSER_HOME a tmp) — el bloqueo de bash en stack_exec es política, se
  documenta y no se cambia.
- **Tiempo**: 0,25 d · **Complejidad**: s · **Hallazgo**: AUD16-26
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_f3
    runtime: python-pytest
    command: "pytest tests/unit -k 'runtime_template or stack_exec' -v"
  ```

#### `task_aud16_f4` — Timeouts del córtex tipados: nunca un 500 crudo al usuario

- [ ] **Título**: `httpx.ReadTimeout` → respuesta de error controlada del asistente
- **Descripción**: `routers/cortex.py:310` deja escapar el ReadTimeout de
  ollama → `api.unhandled_exception`. Capturar errores de transporte del
  provider en el router/servicio y devolver error tipado (retryable) al
  cliente.
- **Tiempo**: 0,25 d · **Complejidad**: s · **Hallazgo**: colateral A (H-7)
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_f4
    runtime: python-pytest
    command: "pytest tests/unit -k 'cortex and (timeout or provider_error)' -v"
  ```

#### `task_aud16_f5` — `NOTIFY_EVENTS_REDIS_URL` coherente entre dev y el installer de prod

- [ ] **Título**: El DLQ de notificaciones se lee de la misma DB que se escribe
- **Descripción**: `compose_generator.py:872` genera DB 3 en prod vs DB 0 en
  dev. Verificar qué DB usan productor y consumidor del stream
  `dlq:notifications` y alinear (con test del generator).
- **Tiempo**: 0,25 d · **Complejidad**: s · **Hallazgo**: menor C (H10)
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_f5
    runtime: python-pytest
    command: "pytest tests/unit -k 'compose_generator' -v"
  ```

#### `task_aud16_f6` — `write_audit_log` en el login

- [ ] **Título**: El docstring deja de mentir: login con rastro en audit_log
- **Descripción**: `auth/audit.py:21` afirma «Called from login» pero ningún
  call site existe en auth. Añadir auditoría de login success/failure
  (sin credenciales en el payload). Coordinado con prod-09 (no se adelanta
  el hardening general).
- **Tiempo**: 0,25 d · **Complejidad**: s · **Hallazgo**: AUD16-16 (parcial)
- **Tests automáticos**:
  ```yaml
  - id: auto_aud16_f6
    runtime: python-pytest
    command: "pytest tests/unit -k 'audit_log and login' -v"
  ```

## Gated / operador (no implementable en autónomo)

| Ítem                                                       | Qué falta                                      | Hallazgo |
| ---------------------------------------------------------- | ---------------------------------------------- | -------- |
| Activar neonize                                            | `--profile neonize`, emparejar QR, crear canal | AUD16-12 |
| Canal externo del operador (telegram/email) + preferencias | credenciales/destino                           | AUD16-13 |
| Smoke e2e copilot/azure                                    | credenciales reales                            | AUD16-03 |
| Destino offsite de backups                                 | bucket/credenciales                            | AUD16-19 |
| Plan demo «MVP Hello World PHP» varado                     | cancelar o reactivar                           | AUD16-24 |
| Decisión A/B/C ADR 0108 (canal de veredicto)               | decisión humana (desde 07-10)                  | —        |
| E2E guardrails + política de aprobación reales             | decidir política a activar (prod-03/ADR 0102)  | AUD16-16 |

## Tests humanos del plan

```yaml
- id: human_aud16_01
  description: "Camino HTTP de tools operativo"
  checklist:
    - "Un run con provider ollama completa con submit_result estructurado (no red de prosa)"
    - "El inbox del System Admin muestra las notifs de plataforma con su contenido"
    - "Un run nuevo muestra price_snapshot available:true con coste de catálogo"

- id: human_aud16_02
  description: "Memorias con modelo real"
  checklist:
    - "Las memorias nuevas llevan distill_model del provider real del agente (no llama3.2:1b)"
    - "El recall no presenta dos memorias idénticas"

- id: human_aud16_03
  description: "Monitorización honesta en este host"
  checklist:
    - "cadvisor ve >1 contenedor O la alerta CadvisorDegraded está firing"
    - "Parar el sampler dispara la alerta de staleness"
```

## Criterios de cierre

1. Checkboxes `[x]` solo tras test automático en verde (más suites globales:
   ruff, mypy strict, unit con ratchet, tests/docs).
2. Imágenes reconstruidas y desplegadas en dev según la receta de cada
   servicio (agent-runtime con WITH_CLAUDE=1 y contexto raíz; workers sobre
   base api-server:manuals).
3. Verificación en vivo de lo observable (inbox, métricas, snapshot).
4. Entrada en `docs/07-changelog/remediacion-auditoria-dirigida-2026-07-16.md`.
5. Los ítems gated quedan documentados, no bloquean el cierre del resto.
