---
plan_id: prod-03-guardrails-validacion-humana
title: Guardrails cableados y validación humana operativa
status: pending_approval
blocking_plan: null
started_at: null
completed_at: null
estimated_duration_calendar: 4 semanas
estimated_effort_person_days: 18.5
estimated_cost_human_eur: 8.325 € – 11.100 €
estimated_cost_ai_eur: 80 € – 150 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P0
---

# Plan prod-03 — Guardrails cableados y validación humana operativa

## Cabecera

| Campo                              | Valor                                                                                   |
| ---------------------------------- | --------------------------------------------------------------------------------------- |
| **ID del Plan**                    | `prod-03-guardrails-validacion-humana`                                                  |
| **Prioridad**                      | P0                                                                                      |
| **Bloqueado por**                  | — (null)                                                                                |
| **Tiempo estimado (calendario)**   | 4 semanas                                                                               |
| **Tiempo estimado (persona-días)** | 18,5                                                                                    |
| **Rama git sugerida**              | `plan/prod-03-guardrails-validacion-humana`                                             |
| **Origen**                         | Auditoría integral de producción 2026-06-10 (dimensión guardrails, calificación _poor_) |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

## Resumen

La auditoría confirma que los Principios Rectores nº10 (guardrails declarativos
por capas en 4 hooks) y nº11 (validación humana configurable, 13 categorías,
4 plantillas) están **hoy incumplidos en producción** aunque el código que los
implementaría exista y pase tests:

1. **El motor de guardrails no se invoca en ningún flujo real** (guardrails-1):
   `GuardrailPipeline` solo se instancia en `api_server/guardrails/planning.py`
   y sus funciones host no tienen ningún caller fuera de tests. En
   `apps/workers` no hay ni una referencia a `guardrail`; el bucle del agente
   (`graph.py` plan→act→observe) llama al LLM y a las tools sin pasar por el
   motor. Las salidas de RAG/HTTP/MCP reentran **crudas** al contexto del
   modelo: el check `prompt_injection` existe y está testeado pero nunca corre
   (inyección indirecta sin defensa).
2. **La validación humana está rota de facto** (guardrails-2/3): las 4
   plantillas seed deciden sobre 13 categorías canónicas, pero el gate del
   runtime (`DEFAULT_TOOL_CATEGORIES`, `approval.py:25-31`) emite 4 categorías
   distintas — intersección **vacía** — y la categoría no listada es `auto`
   (fail-open). Ni siquiera el preset «Cliente Externo» detiene una sola tool.
   Además ~14 de las 19 tools wired y todas las MCP jamás se gatean, y un
   proyecto sin política no instancia el gate.
3. **El ciclo de vida de aprobaciones está incompleto** (guardrails-5/6/7):
   las solicitudes pendientes nunca caducan (no existe el job de
   `expire_stale_requests`), la resolución tiene una carrera check-then-act y
   aprobar no autoriza la acción (bucle aprobar→backlog→re-aparcar, ADR 0020).
4. **El merge plataforma→tenant→proyecto no existe en producción**
   (guardrails-4): `resolve_config` solo se llama desde tests; no hay tabla,
   seed ni baseline de plataforma con candados.
5. **La política de fallo del motor está indefinida** (guardrails-8) y el
   motor síncrono bloquearía el event loop al cablearse (guardrails-10).
6. El roadmap del Plan 11 marca `task_11_22` como cableado sin que ninguna
   ruta lo invoque (guardrails-9).

Este plan **cablea de verdad** el motor (4 hooks) en el ciclo del agente y en
el chat de planning, **repara** la validación humana (vocabulario unificado,
cobertura completa, default de plataforma, expiración, resolución atómica,
fin del bucle de re-aparcamiento) y **persiste** la config en capas con
candados, definiendo de paso la política fail-open/fail-closed del motor.

## Alcance

**Entra**:

- Fuente única de las 13 categorías de acciones sensibles en
  `packages/shared-domain` + remapeo de `DEFAULT_TOOL_CATEGORIES` + test de
  contrato en CI.
- Cobertura del gate de aprobación para todas las tools runtime-wired y las
  tools MCP/custom (campo `category` en el ToolSpec); default de plataforma
  para proyectos sin política; política configurable para categoría no listada.
- Resolución atómica de aprobaciones (UPDATE condicional → 409), job beat de
  expiración a 24h (configurable) y pase de la decisión aprobada a la
  siguiente ejecución (`approved_actions` en el task spec).
- Persistencia `guardrail_configs` (scope platform/tenant/project, RLS), seed
  del baseline de plataforma con `pii`/`secret_leakage`/`prompt_injection` en
  `locked: true`, CRUD con `strict=True` (422 al relajar un candado).
- Política de fallo por check (`on_error: block|warn`, default fail-closed
  para los locked), aislamiento de excepciones en `pipeline.run`, tratamiento
  de `available: False`.
- Cableado del pipeline en los 4 hooks del bucle del agente (incl.
  `prompt_injection` sobre salidas de RAG/HTTP/MCP), transporte de la config
  efectiva y de los eventos vía task spec/result (el runtime es sandboxed,
  sin DB), persistencia de eventos vía `record_pipeline_decision`.
- Cableado real de `run_planning_chat_guardrails` y `gate_generate_plan` en
  sus rutas; sinceramiento de `task_11_22` en el roadmap del Plan 11.
- Seam async (`asyncio.to_thread`) y límites de tamaño de input por check.
- Documentación de referencia, guía y runbook.

**Queda fuera** (con coordinación anotada):

- Resumption con contexto (Opción B del ADR 0020) — la implementa
  **prod-06-ciclo-vida-ejecucion**; aquí entra la mitigación mínima viable
  (`approved_actions`).
- Cadena de notificación del timeout de aprobaciones (el job emite el evento;
  el enrutado de alertas es de **prod-08-observabilidad-alertas**).
- Optimización general del event loop del api-server — **prod-13** la trata en
  global; aquí solo el seam async del pipeline (guardrails-10 se cierra aquí).
- Sinceramiento sistemático del resto del roadmap — **prod-15-gobernanza**;
  aquí solo `task_11_22` porque es parte del wiring (guardrails-9).
- UI de gestión de configs de guardrails por tenant/proyecto (follow-up; este
  plan entrega API + seeds).

## Decisiones clave

1. **Política de fallo del motor (ADR nuevo propuesto, siguiente número
   libre)** — Opciones: (a) fail-closed global; (b) fail-open global;
   (c) **recomendada**: `on_error: block|warn` declarativo por check, con
   default `block` (fail-closed) para los guardrails `locked` de plataforma y
   `warn` para el resto; `available: False` (p. ej. `content_safety` sin
   clasificador) se trata con la misma política. La decisión de producto la
   aprueba un humano vía ADR antes de la Fase C.
2. **Fin del bucle aprobar→re-aparcar (extensión del ADR 0020)** — Opciones:
   (a) **recomendada como mínimo viable**: lista `approved_actions`
   (tool + hash normalizado de args + categoría + TTL) en el task spec que el
   `ApprovalGate` consulta antes de aparcar; (b) resumption con contexto
   (Opción B del ADR 0020) — más correcta pero solapa con prod-06. Se propone
   (a) aquí y (b) en prod-06; el ADR 0020 se actualiza con la decisión.
3. **Categoría no listada en la política** — hoy `auto` (fail-open). Se
   propone una clave `unlisted_category: auto|human_required` en la política,
   seedeada a `human_required` en los presets `production` y
   `customer-external`, y `auto` en `sandbox`/`development`. Cerrado vía el
   mismo ADR de la decisión 1 (es la misma filosofía fail-open/fail-closed).
4. **Fuente única de categorías** — `packages/shared-domain` (el runtime ya
   importa `shared_domain.tool_names`, así que es importable desde el sandbox
   sin acceso a DB). El seed del api-server y el gate del runtime importan de
   ahí; un test de contrato lo pinea.

## Tareas

### Fase A — Validación humana reparada: vocabulario, cobertura y default

#### `task_prod03_01` — Fuente única de las 13 categorías + remapeo del gate

> **Estado (2026-07-06, auditoría de roadmap)**: implementado y desplegado por el commit `13c4ad0`
> ("fix(security): cierra el fail-open del gate de validación humana (g6, P0)"), que dice
> explícitamente "absorbe la Fase A no-gated de prod-03". Verificado:
> `packages/shared-domain/src/shared_domain/approval_categories.py` existe con el remapeo exacto
> (`shell_exec→code_changes`, `http_get→external_http_get`, `http_post→external_http_post`,
> `write_file→code_changes`) en `docker/agent-runtimes/agent-runtime/agent_runtime/approval.py:33-40`.

- [x] **Título**: Mover `CATEGORIES` a `packages/shared-domain`
      (p. ej. `shared_domain/approval_categories.py`), importarlas desde
      `apps/api-server/src/api_server/seeds/builtin_approval_policies.py:31`,
      y remapear `DEFAULT_TOOL_CATEGORIES`
      (`docker/agent-runtimes/agent-runtime/agent_runtime/approval.py:25-31`)
      a las categorías canónicas: `shell_exec`→`code_changes` (o nueva
      categoría `code_execution` añadida al vocabulario canónico vía el seed),
      `write_file`→`code_changes`, `http_get`→`external_http_get`,
      `http_post`→`external_http_post`, `agent_invoke`→ decisión en code
      review (propuesta: mantener `agent_delegation` añadiéndola al
      vocabulario canónico). Test de contrato CI: todo valor de
      `DEFAULT_TOOL_CATEGORIES` ∈ `CATEGORIES`. Cubre guardrails-2.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_01_a
    runtime: python-pytest
    command: "pytest tests/unit/test_approval_categories_contract.py -v"
  - id: auto_prod03_01_b
    runtime: python-pytest
    command: "pytest tests/integration/test_approval_gate_presets.py -v"
  ```
  El test `_b` verifica end-to-end de datos: con el preset
  `customer-external`, `ApprovalGate.review("http_post")` devuelve categoría
  (la tool se aparca); con `sandbox`, devuelve `None`.

#### `task_prod03_02` — Cobertura del gate: todas las tools wired + MCP/custom

- [ ] **Título**: Extender el mapa tool→categoría a todas las tools
      runtime-wired (`packages/shared-domain/src/shared_domain/tool_names.py:106-136`):
      `send_notification`→`external_communication`, `run_pytest`/`run_lint`/
      `run_build`→`code_changes` (o `code_execution`), `promote_to_kb` y
      `memory_store`→categoría de escritura persistente acordada,
      `kanban_update`→categoría de gestión. Añadir campo `category` opcional
      al ToolSpec de tools MCP/custom que el worker forwardee al task spec
      (`apps/workers/src/workers/execution.py`), de modo que
      `<server>.<tool>` sea gateable. Tabla tool→categoría documentada y
      pineada por test. Cubre guardrails-3 (cobertura). Depende de
      `task_prod03_01`.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_02_a
    runtime: python-pytest
    command: "pytest tests/unit/test_tool_category_coverage.py -v"
  - id: auto_prod03_02_b
    runtime: python-pytest
    command: "pytest tests/integration/test_mcp_tool_gating.py -v"
  ```

#### `task_prod03_03` — Default de plataforma y política para categoría no listada

- [ ] **Título**: Si `projects.human_approval_policy` es NULL
      (`apps/api-server/src/api_server/db/domain.py:750`), el dispatch del
      worker resuelve y pasa el preset por defecto de plataforma (propuesta:
      `development`, configurable como platform setting) en lugar de no
      instanciar el gate (`__main__.py:304`). Implementar
      `unlisted_category: auto|human_required` en la política (decisión
      clave 3): `requires_human` (`approval.py:34-46` y
      `apps/api-server/src/api_server/db/approval_repo.py:54`) usa ese valor
      en vez del `"auto"` hardcodeado; actualizar los 4 seeds. Cubre
      guardrails-3 (fail-open sin política) y el fail-open de guardrails-2.
      Depende de `task_prod03_01`.
- **Tiempo**: 1 día · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_approval_default_policy.py -v"
  ```

### Fase B — Ciclo de vida de aprobaciones

#### `task_prod03_04` — Resolución atómica (fin de la carrera check-then-act)

- [ ] **Título**: Reescribir `resolve_approval`
      (`apps/api-server/src/api_server/db/approval_repo.py:98-147`) como
      `UPDATE approval_requests SET status=... WHERE id=:id AND
status='pending'` (o `SELECT ... FOR UPDATE` + re-check en la misma
      transacción); 0 filas afectadas → 409 en
      `POST /approvals/{id}/resolve`
      (`apps/api-server/src/api_server/routers/approvals.py:60-69`). Las
      transiciones de Execution/Task solo se aplican si la fila ganó la
      transición. El mismo guard se reutiliza en la expiración
      (`task_prod03_05`) para la carrera aprobar-vs-timeout. Cubre
      guardrails-6.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_04_a
    runtime: python-pytest
    command: "pytest tests/integration/test_approval_resolve_race.py -v"
  ```
  Incluye un test de concurrencia: dos resoluciones simultáneas (approve vs
  reject) → exactamente una 200 y una 409, estado final consistente.

#### `task_prod03_05` — Job beat de expiración de aprobaciones

- [ ] **Título**: Crear task Celery `workers.expire_stale_approvals` que
      invoque `expire_stale_requests`
      (`apps/api-server/src/api_server/db/approval_repo.py:160-197` — lógica
      a exponer vía servicio compartido o réplica en workers, según el patrón
      de los demás jobs) por tenant, y añadir la entrada al
      `BEAT_SCHEDULE` (`apps/workers/src/workers/beat_schedule.py:37-77`)
      cada 15 min en cola `default`, con timeout configurable como platform
      setting (default 24 h) y constante `APPROVAL_EXPIRY_BEAT_ENTRY`. Emitir
      evento de timeout para el dispatcher de notificaciones (el enrutado de
      alertas queda en prod-08). Cubre guardrails-5. Depende de
      `task_prod03_04`.
- **Tiempo**: 1 día · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_05_a
    runtime: python-pytest
    command: "pytest tests/integration/test_approval_expiry_job.py -v"
  - id: auto_prod03_05_b
    runtime: python-pytest
    command: "pytest tests/unit/test_beat_schedule.py -k approval -v"
  ```

#### `task_prod03_06` — Aprobar autoriza la acción (fin del bucle ADR 0020)

- [ ] **Título**: Implementar la opción (a) de la decisión clave 2: al
      aprobar, `resolve_approval`
      (`apps/api-server/src/api_server/db/approval_repo.py:141-147`) registra
      la acción autorizada (tool + hash normalizado de args + categoría +
      TTL); el dispatch del worker la incluye como `approved_actions` en el
      task spec (`docker/agent-runtimes/agent-runtime/agent_runtime/__main__.py:248`
      hoy solo lee `approval_policy`); `ApprovalGate.review`
      (`approval.py:60-72`) consulta la lista antes de aparcar. La
      normalización del hash y el fallback (si los args regenerados no
      matchean, gatear por tool+categoría con TTL corto) se fijan en la
      extensión del ADR 0020, que esta tarea redacta y somete a aprobación
      humana ANTES de implementar. Cubre guardrails-7. Depende de
      `task_prod03_04`; coordinar con prod-06 (Opción B futura).
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_06_a
    runtime: python-pytest
    command: "pytest tests/integration/test_approval_no_repark_loop.py -v"
  ```
  Verifica: aparcar → aprobar → re-ejecutar → la MISMA acción ya no se
  aparca; una acción distinta de la misma categoría sí se aparca.

### Fase C — Config en capas persistida y política de fallo del motor

#### `task_prod03_07` — Tabla `guardrail_configs` + migración + RLS

- [ ] **Título**: Nueva migración Alembic (reversible) con tabla
      `guardrail_configs` (scope `platform|tenant|project`, `tenant_id`
      nullable solo para scope platform, `project_id` nullable, config JSONB
      validada contra `PipelineConfig`, versión, timestamps) con RLS por
      `tenant_id` como el resto de tablas (Principio nº1). Hoy las únicas
      migraciones guardrail son `20260530_0052_guardrail_events` y
      `20260530_0053_guardrail_alert_rules` — no existe persistencia de
      configs. Cubre guardrails-4 (persistencia).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_07_a
    runtime: python-pytest
    command: "pytest tests/integration/test_guardrail_configs_table.py -v"
  ```
  Incluye test cross-tenant (RLS) y test de reversibilidad de la migración.

#### `task_prod03_08` — Baseline de plataforma locked + CRUD strict

- [ ] **Título**: Seed del baseline de plataforma con `pii`,
      `secret_leakage` y `prompt_injection` en `locked: true` (como promete
      `packages/shared-guardrails/src/shared_guardrails/layers.py:8-9`),
      endpoints CRUD tenant/proyecto que resuelven con
      `resolve_config(..., strict=True)` — un intento de relajar o eliminar
      una regla bloqueada falla con 422 (hoy `LockedFieldOverrideError`
      existe pero nadie la usa fuera de tests, `layers.py:49-60`). Servicio
      `get_effective_guardrail_config(tenant_id, project_id)` cacheado que
      consumirán el dispatch (`task_prod03_11`) y el chat
      (`task_prod03_14`). Cubre guardrails-4 (baseline + candados). Depende
      de `task_prod03_07`.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_08_a
    runtime: python-pytest
    command: "pytest tests/integration/test_guardrail_configs_crud.py -v"
  - id: auto_prod03_08_b
    runtime: python-pytest
    command: "pytest tests/integration/test_guardrail_locked_override_422.py -v"
  ```

#### `task_prod03_09` — Política de fallo por check (`on_error`)

- [ ] **Título**: Tras aprobar el ADR de la decisión clave 1: añadir
      `on_error: block|warn` a la config declarativa
      (`shared_guardrails/config.py`), envolver cada check en try/except en
      `GuardrailPipeline.run`
      (`packages/shared-guardrails/src/shared_guardrails/pipeline.py:98-99`)
      convirtiendo la excepción en un `GuardrailOutcome` triggered con esa
      acción (default: `block` si el guardrail es locked, `warn` si no), y
      tratar `available: False` de `content_safety`
      (`packages/shared-guardrails/src/shared_guardrails/checks/content_safety.py:394-404`)
      con la misma política en vez de pasar silenciosamente
      (`types.py:181` solo mira `action`). Cubre guardrails-8.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_09_a
    runtime: python-pytest
    command: "pytest tests/unit/test_pipeline_on_error_policy.py -v"
  ```

#### `task_prod03_10` — Seam async y límites de tamaño de input

- [ ] **Título**: Ejecutar `pipeline.run` vía `asyncio.to_thread` (o
      `anyio.to_thread.run_sync`) en los hosts async
      (`apps/api-server/src/api_server/guardrails/planning.py:273` y `:341`,
      y los nuevos hosts de la Fase D), y añadir un límite de tamaño de
      input configurable por check (truncado + flag en el payload) para
      textos largos — el detector genérico de `secret_leakage`
      (`checks/secret_leakage.py:128-136`) es lineal-cuadrático en el peor
      caso. Cubre guardrails-10; coordinación: prod-13 trata el event loop en
      global, este seam se cierra aquí para no cablear (Fase D) un motor
      bloqueante.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_10_a
    runtime: python-pytest
    command: "pytest tests/unit/test_pipeline_async_seam.py -v"
  ```

### Fase D — Cableado del motor en los flujos reales

#### `task_prod03_11` — El dispatch resuelve y transporta la config efectiva

- [ ] **Título**: En el dispatch del worker
      (`apps/workers/src/workers/execution.py` — hoy CERO referencias a
      guardrails), resolver la config efectiva por (tenant, proyecto) vía el
      servicio de `task_prod03_08` e incluirla serializada en el task spec
      que recibe el runtime (mismo canal que `approval_policy`,
      `__main__.py:248`), con límite de tamaño y versión para invalidación.
      Cubre guardrails-1 (transporte). Depende de `task_prod03_08`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_11_a
    runtime: python-pytest
    command: "pytest tests/integration/test_dispatch_guardrail_config.py -v"
  ```

#### `task_prod03_12` — Pipeline en los 4 hooks del bucle del agente

> **Estado (2026-07-06, auditoría de roadmap)**: PARCIAL — solo el hook `post_tool` está cableado
> (`docker/agent-runtimes/agent-runtime/agent_runtime/graph.py:958`, ADR 0102 "g1 slice mínimo",
> commits `a905612`/`60d1c87`). Los otros 3 hooks (`pre_llm`, `post_llm`, `pre_tool`) y el
> `GuardrailPipeline` instanciado desde config NO existen todavía — no marcar `[x]`.

- [ ] **Título**: Instanciar `GuardrailPipeline` desde la config del spec en
      el runtime y ejecutarlo en los 4 puntos de
      `docker/agent-runtimes/agent-runtime/agent_runtime/graph.py`:
      `pre_llm` antes de `model.invoke` (nodo plan), `post_llm` sobre la
      respuesta, `pre_tool` antes de `self.deps.tools.call` (nodo act,
      `graph.py:259-264`) y `post_tool` sobre `result` ANTES de que la
      observación reentre al contexto (`observe`) — esto cierra la inyección
      indirecta: `prompt_injection`/`pii`/`secret_leakage` corren sobre las
      salidas de RAG/HTTP/MCP. Acciones: `block` aborta o aparca el paso
      según hook; `warn` registra y continúa. Retirar los disclaimers «not
      the full layered guardrail engine» (`tools.py:15-17`,
      `apps/api-server/src/api_server/chat/modes.py:62-64`) cuando dejen de
      ser ciertos. Cubre guardrails-1 (núcleo). Depende de
      `task_prod03_09`, `task_prod03_10`, `task_prod03_11`.
- **Tiempo**: 2,5 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_12_a
    runtime: python-pytest
    command: "pytest tests/integration/test_agent_loop_guardrail_hooks.py -v"
  - id: auto_prod03_12_b
    runtime: python-pytest
    command: "pytest tests/integration/test_indirect_prompt_injection.py -v"
  ```
  `_b`: una tool stub devuelve un payload con instrucción inyectada → el
  hook `post_tool` lo marca/bloquea y el evento queda registrado.

#### `task_prod03_13` — Persistencia de eventos de guardrails desde el worker

- [ ] **Título**: El runtime (sandboxed, sin DB) acumula las decisiones del
      pipeline en el result envelope; el worker las persiste al recoger el
      resultado vía `record_pipeline_decision` (tabla `guardrail_events`,
      migración 0052, hoy sin datos de producción), tenant-scoped y
      enmascaradas como ya hace el host de planning. Con esto el dashboard y
      las alert rules (migración 0053) dejan de estar vacíos. Cubre
      guardrails-1 (observabilidad del motor). Depende de `task_prod03_12`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_13_a
    runtime: python-pytest
    command: "pytest tests/integration/test_guardrail_events_from_worker.py -v"
  ```

#### `task_prod03_14` — Cablear el chat de planning y «Generar Plan» por la RUTA

- [ ] **Título**: Invocar `run_planning_chat_guardrails`
      (`apps/api-server/src/api_server/guardrails/planning.py:242`) desde el
      router/grafo real del chat de planning
      (`routers/conversations.py` no importa nada de `api_server.guardrails`)
      y `gate_generate_plan` (`planning.py:313`) desde el endpoint de
      generación de plan (`routers/plans.py`), usando la config efectiva de
      `task_prod03_08`. Test e2e que pasa por la RUTA HTTP, no por la
      función. Reabrir/sincerar `task_11_22` en
      `docs/roadmap/11-guardrails-precios.md:497-499` con nota de que el
      cableado real lo entrega este plan (coordinar con prod-15, que audita
      el roadmap completo). Cubre guardrails-9 y parte de guardrails-1.
      Depende de `task_prod03_08`, `task_prod03_10`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_14_a
    runtime: python-pytest
    command: "pytest tests/e2e/test_planning_guardrails_route.py -v"
  ```

### Fase E — Verificación integral y documentación

#### `task_prod03_15` — E2E integral: «Cliente Externo» detiene la primera tool sensible

- [ ] **Título**: Test e2e que ejecuta una tarea real con el preset
      `customer-external`: la primera tool sensible (`http_post` o
      `shell_exec`) se aparca con `awaiting_human_approval`, el rechazo
      aborta, la aprobación continúa SIN re-aparcar la misma acción, y una
      solicitud no atendida expira por el job. Es el test de regresión del
      titular de la auditoría («ni siquiera Cliente Externo detiene una sola
      tool»). Depende de Fases A, B y D completas.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_15_a
    runtime: python-pytest
    command: "pytest tests/e2e/test_customer_external_preset_gates.py -v"
  ```

#### `task_prod03_16` — Documentación y ADRs

- [ ] **Título**: `docs/04-reference/guardrails.md` (capas, candados,
      `on_error`, hooks cableados, tabla tool→categoría) y
      `docs/04-reference/validacion-humana.md` (13 categorías, presets,
      ciclo aprobación/expiración/`approved_actions`);
      `docs/06-runbooks/aprobaciones-atascadas.md`; ADR nuevo de política de
      fallo (decisión 1+3) y extensión del ADR 0020 (decisión 2) con estado
      según apruebe el humano; actualizar el estado «falta el job» del ADR
      0016 (`0016-motor-validacion-humana.md:98`).
- **Tiempo**: 1 día · **Complejidad**: s
- **Tests automáticos**: revisión humana de docs (sin test automático; el
  linter de Markdown de CI debe pasar).

## Hallazgos de auditoría cubiertos

| fid           | Severidad | Tarea(s) que lo cierran                                        |
| ------------- | --------- | -------------------------------------------------------------- |
| guardrails-1  | critical  | task_prod03_11, task_prod03_12, task_prod03_13, task_prod03_14 |
| guardrails-2  | critical  | task_prod03_01, task_prod03_03 (fail-open), task_prod03_15     |
| guardrails-3  | high      | task_prod03_02, task_prod03_03                                 |
| guardrails-4  | high      | task_prod03_07, task_prod03_08                                 |
| guardrails-5  | medium    | task_prod03_05                                                 |
| guardrails-6  | medium    | task_prod03_04                                                 |
| guardrails-7  | medium    | task_prod03_06 (mínimo viable; Opción B en prod-06)            |
| guardrails-8  | medium    | task_prod03_09                                                 |
| guardrails-9  | medium    | task_prod03_14                                                 |
| guardrails-10 | low       | task_prod03_10 (coordinación con prod-13)                      |

## Riesgos

1. **Falsos positivos al cablear post_tool/post_llm**: `prompt_injection` y
   `pii` sobre salidas reales de RAG/HTTP pueden bloquear ejecuciones
   legítimas. Mitigación: arrancar los checks no-locked en `warn`, observar
   `guardrail_events` una semana y subir a `block` con datos.
2. **Latencia del bucle del agente**: 4 hooks × regex/entropía por turno.
   Mitigación: límites de tamaño (task_prod03_10), seam async y presupuesto
   de latencia medido en el e2e (task_prod03_15).
3. **Migración de datos de políticas existentes**: proyectos con
   `human_approval_policy` ya copiada usan el vocabulario antiguo de 4
   categorías; el remapeo (task_prod03_01) necesita migración de datos o
   aliases de compatibilidad, o esas filas quedarían fail-open de nuevo.
4. **Hash de args frágil (ADR 0020 opción a)**: el modelo puede regenerar
   args ligeramente distintos y la acción aprobada no matchear → bucle de
   nuevo. Mitigación: normalización + fallback por (tool, categoría) con TTL
   corto, decidido en el ADR antes de implementar.
5. **Tamaño del task spec**: transportar config efectiva + approved_actions +
   eventos por el envelope puede chocar con límites de payload del broker.
   Mitigación: versión + límite de tamaño y poda de payloads en eventos.
6. **Carrera aprobar-vs-timeout**: el job de expiración (nuevo) y la
   resolución humana compiten; sin el UPDATE condicional de task_prod03_04
   primero, el job introduciría una segunda carrera. El orden de la Fase B es
   obligatorio.

## Tests humanos del Plan

```yaml
- id: human_prod03_01
  description: "El preset Cliente Externo detiene de verdad las tools sensibles"
  hint: "Proyecto con preset customer-external y una tarea que haga http_post"
  checklist:
    - "Crear proyecto con plantilla 'Cliente Externo' y lanzar una tarea que use http_post"
    - "La ejecución se detiene en awaiting_human_approval ANTES de ejecutar la tool"
    - "La solicitud aparece en la bandeja de aprobaciones con tool, args y categoría canónica"
    - "Rechazar → la ejecución termina rechazada y la tool NUNCA se ejecutó"
    - "Repetir y aprobar → la ejecución continúa y NO vuelve a aparcar la misma acción"

- id: human_prod03_02
  description: "Las aprobaciones caducan y la resolución es atómica"
  checklist:
    - "Dejar una solicitud sin atender con timeout de prueba (p. ej. 2 min) → pasa a timed_out, la ejecución a aborted y la tarea a blocked"
    - "Con dos sesiones de revisor, aprobar y rechazar la misma solicitud a la vez → una recibe 200 y la otra 409"
    - "El estado final es consistente con la resolución que ganó"

- id: human_prod03_03
  description: "Guardrails activos en chat de planning y en el bucle del agente"
  checklist:
    - "Pegar un texto con una API key falsa en el chat de planning → el motor bloquea/enmascara según la política y el evento aparece en el dashboard de guardrails"
    - "Tool HTTP que devuelve una página con instrucción inyectada ('ignore previous instructions…') → el hook post_tool la marca/bloquea y queda registrada"
    - "El dashboard de guardrails muestra eventos reales de una ejecución de agente (no solo de tests)"

- id: human_prod03_04
  description: "Capas con candado operativas"
  checklist:
    - "Como tenant_admin, intentar desactivar el guardrail pii (locked de plataforma) en un proyecto → la API responde 422 con mensaje claro"
    - "Un override permitido (p. ej. añadir un dominio a allowed_domains en capa proyecto) sí persiste y se refleja en la config efectiva"
    - "Un proyecto SIN política de aprobación usa el default de plataforma (no queda sin gate)"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. El ADR de política de fallo y la extensión del ADR 0020 aprobados por un
   humano ANTES de mergear las fases C/B correspondientes.
3. Los 4 tests humanos del plan validados.
4. `task_11_22` del Plan 11 sincerada en `docs/roadmap/11-guardrails-precios.md`.
5. Entrada de changelog en
   `docs/07-changelog/prod-03-guardrails-validacion-humana.md`.
6. PR del plan mergeado a `master`.

## Próximo Plan

**prod-04-backup-dr-restaurable** [P0] — Backup/DR restaurable de verdad:
bug de tar, restore ejecutable, clave offsite, RPO/RTO y drill. Coordinación
pendiente de este plan hacia: **prod-06** (Opción B del ADR 0020),
**prod-08** (alertas de timeout de aprobaciones), **prod-13** (event loop
global) y **prod-15** (sinceramiento sistemático del roadmap).
