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

- [x] **Título**: Extender el mapa tool→categoría a todas las tools
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

- [x] **Título**: Si `projects.human_approval_policy` es NULL
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
  - ✅ **Cerrada (2026-08-02)**: el operador firmó el **ADR 0153, opción (D)**, y las dos mitades están puestas. (a) venía del ADR 0104. (b) es la clave `unlisted_category`, implementada en los **DOS espejos** —`api_server/db/approval_repo.py` y `agent_runtime/approval.py`, que no se importan entre sí— con el orden de resolución: decisión listada → clave explícita → derivación del `preset` (`auto` en sandbox/development, `human_required` en production/customer-external) → **fail-closed** si no hay nada legible. La solicitud que nace del camino nuevo lleva `gate_reason` con el motivo en castellano, la clave se ve y se edita en `/admin/approval-policy` y la API rechaza (422) un valor ilegible o colado dentro de `categories`. Evidencia: `tests/unit/test_unlisted_approval_category.py` (57 casos; una tabla que pasa por los dos espejos y los compara uno a uno) y `apps/admin-panel/app/admin/approval-policy/page.test.tsx` (5). Referencia de operador: `docs/04-reference/validacion-humana.md` §«Qué pasa con una categoría que la política NO lista». La siembra de los 4 presets y el relleno de las políticas existentes son los carriles hermanos del mismo ADR.
  - ⏳ **Lo que decía cuando estaba abierta (2026-08-01), que sigue explicando POR QUÉ:** La mitad (a) estaba cerrada desde el **ADR 0104** (`accepted`): `_resolve_effective_approval_policy` hereda el preset `development` y `tests/unit/test_default_approval_policy.py` lo cubre. La mitad (b), la clave `unlisted_category`, **no la decido yo**: cambia cuántas acciones para la plataforma y en qué dirección falla, y las dos direcciones tienen coste operativo real. Queda escrita en el **[ADR 0153](../05-architecture-decisions/0153-categoria-no-listada-en-la-politica-de-aprobacion.md)** (`proposed`), con cuatro opciones, su coste y una recomendación argumentada.
    **Lo que el ADR aporta y no se sabía**: el argumento con el que el ADR 0104 descartó esta clave —«todos los presets construyen sus `decisions` sobre `_all(CATEGORIES, …)`»— es cierto para `seeds/builtin_approval_policies.py` y **falso para lo que acaba en `projects.human_approval_policy`**. Los proyectos nacidos de plantilla copian `_POLICY_DEV_SKELETON` (`seeds/builtin_project_templates.py:62-70`), que lista CUATRO claves —y una de ellas, `external_http`, ni siquiera es canónica, así que no gatea nada—. Medido: **10 de las 13 categorías quedan en `auto` por omisión** en las siete plantillas que usan el esqueleto, incluidas `external_http_post`, `data_export_pii` y `user_management`; y las dos plantillas que la UI presenta como «Producción» heredan los mismos diez huecos. El ADR 0135 difirió esta decisión «al ADR de política de fallo del motor», que es el 0102 D5 — y ese ADR trata el fallo de un CHECK del motor, no el vocabulario de la política de aprobación. La decisión llevaba apuntada en un sitio que no la contenía.
- **Tiempo**: 1 día · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_approval_default_policy.py -v"
  ```

### Fase B — Ciclo de vida de aprobaciones

#### `task_prod03_04` — Resolución atómica (fin de la carrera check-then-act)

- [x] **Título**: Reescribir `resolve_approval`
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

- [x] **Título**: Crear task Celery `workers.expire_stale_approvals` que
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
  - ✅ **Hecho (2026-08-01)**: la task, la constante `APPROVAL_EXPIRY_BEAT_ENTRY`, la entrada de beat cada 15 min en `default` y el registro en `celery_app(imports=…)` ya estaban; lo que faltaba era el arnés. **`tests/integration/test_approval_expiry_job.py` 7/7 + `tests/unit/test_approval_expiry_beat.py` 5/5 en verde.** La causa del rojo tenía DOS capas y solo la primera estaba diagnosticada: (1) el helper escribía el platform setting sin invalidar la caché Redis de prod-13, así que el `approval_expiry_enabled=False` del kill-switch sobrevivía al `TRUNCATE` del test siguiente; (2) **y la invalidación que se añadió para arreglarlo fallaba en silencio**, porque `api_server.auth.deps.get_redis` es `lru_cache` y su pool queda atado al event loop del test anterior — la primera operación Redis de cada test revienta con `RuntimeError: Event loop is closed` e `invalidate_platform_setting_cache` se la traga por ser best-effort. Fix: fixture autouse con `reset_redis_cache()` + purga explícita en `_set_setting` y antes del `TRUNCATE`. Rojo verificado quitando el `reset_redis_cache()` (3 rojos) y restaurándolo (7 verdes).
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

- [x] **Título**: Implementar la opción (a) de la decisión clave 2: al
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
  - ✅ **Hecho (2026-07-31)** con el [ADR 0135](../05-architecture-decisions/0135-que-autoriza-una-aprobacion-humana.md) ya `accepted`: **G1+S1+T1+N3** — aprobar autoriza esa acción exacta, en esa task, una vez. La huella canónica vive en `shared_domain/approval_action.py` (una sola implementación para los dos extremos: `to_canonical` + `json.dumps(sort_keys, UTF-8)` + SHA-256, **sin** normalización con pérdida); `read_approved_actions` (approval_repo) la emite con predicado `tenant_id` explícito y tope `APPROVED_ACTIONS_MAX`; `_build_runtime_env` la serializa como `approved_actions`; `ApprovalGate.review(tool, args)` la canjea, **una vez por run**. **Dos desvíos del enunciado de arriba, ambos deliberados**: (1) el **fallback por `(tool, categoría)` con TTL corto queda RECHAZADO** por el operador —convertía la ruta laxa en la ruta normal—, sustituido por N3 (re-preguntar enseñando el delta, `action.prior_approvals` + bloque en la UI de aprobaciones); (2) **no hay TTL temporal** porque la vigencia elegida fue T1 (un canje), no T2. Además `resolve_approval` gasta un reintento por aprobación y escala a `blocked` con evento `approval_retry_capped` al llegar a `max_retries`: el bucle deja de ser infinito. `tests/integration/test_approval_no_repark_loop.py` (13) + `tests/unit/test_approval_action_fingerprint.py` (18) + `tests/unit/test_approval_gate_authorized_actions.py` (14) + `docker/agent-runtimes/agent-runtime/tests/test_boot_approved_actions.py` (4) + `tests/unit/test_agent_spec_approved_actions.py` (3), todos en verde. **Queda como deuda con nombre**: la persistencia del canje (`consumed_at`) NO se implementó — tal cual la propone el ADR produce un livelock cuadrático, porque al re-ejecutarse la task DESDE CERO el agente vuelve a proponer las acciones ya consumidas; el canje es por run.
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

- [x] **Título**: Nueva migración Alembic (reversible) con tabla
      `guardrail_configs` (scope `platform|tenant|project`, `tenant_id`
      nullable solo para scope platform, `project_id` nullable, config JSONB
      validada contra `PipelineConfig`, versión, timestamps) con RLS por
      `tenant_id` como el resto de tablas (Principio nº1). Hoy las únicas
      migraciones guardrail son `20260530_0052_guardrail_events` y
      `20260530_0053_guardrail_alert_rules` — no existe persistencia de
      configs. Cubre guardrails-4 (persistencia).
  - ✅ **Hecho (2026-08-01)**: migración **0132** (`20260801_0132_guardrail_configs.py`) con `guardrail_configs` — scope cerrado por CHECK, un segundo CHECK que exige a cada scope SUS columnas (un `platform` con `tenant_id` es una contradicción y la rechaza la BD), tres índices únicos parciales (una fila de plataforma, una por tenant, una por proyecto) y modelo ORM en `api_server/db/guardrail_config.py`. **RLS con una asimetría deliberada**: `USING (tenant_id IS NULL OR tenant_id = app.tenant_id)` deja LEER el baseline de plataforma desde cualquier tenant —es la capa que todos heredan, no contiene dato de nadie, y hoy vive en `platform_settings`, una tabla directamente sin RLS— mientras que el `WITH CHECK` **no** tiene la rama NULL, así que desde una sesión de tenant no se puede crear ni tocar la fila de plataforma. Lo verifica PostgreSQL, no la capa de aplicación. `tests/integration/test_guardrail_configs_table.py` **13/13** (forma, unicidad, catálogo, dos tests cross-tenant, el fail-closed sin `app.tenant_id`, y el **round-trip de reversibilidad ejecutado de verdad**: downgrade → la tabla no existe → upgrade → vuelve con RLS). `test_rls_invariant.py` 7/7 y `test_migrations.py` 11/11 siguen verdes con la tabla nueva dentro.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_07_a
    runtime: python-pytest
    command: "pytest tests/integration/test_guardrail_configs_table.py -v"
  ```
  Incluye test cross-tenant (RLS) y test de reversibilidad de la migración.

#### `task_prod03_08` — Baseline de plataforma locked + CRUD strict

- [x] **Título**: Seed del baseline de plataforma con `pii`,
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
  - ✅ **Hecho (2026-08-01)**: `seeds/guardrail_baseline.py` siembra los tres `locked` (`platform_prompt_injection` en `post_tool` —es el hook que cierra la inyección INDIRECTA, porque ahí es donde reentra lo que devuelve una tool—, `platform_secret_leakage` y `platform_pii` en `post_llm`), cableado como paso `guardrail_baseline` del seed CLI y **idempotente sin pisar**: si el operador subió los tres a `block`, un re-arranque no se lo revierte. Router `routers/guardrail_configs.py` con GET de config efectiva **+ el recibo de procedencia** (qué capa ganó cada check y cuáles están bloqueados, que es lo que permite a la UI explicar por qué algo no se puede tocar), PUT/DELETE de las capas tenant y proyecto bajo `require_tenant_admin`, y `resolve_config(..., strict=True)` en cada escritura → **422 con nombre** (`{"error": "locked_guardrail_override", hook, key, layer, message}`). Servicio `get_effective_guardrail_config` cacheado en Redis con invalidación por alcance (tocar la plataforma purga todo; un tenant, sus proyectos; un proyecto, solo él). **Tres decisiones que conviene leer antes de tocar esto**: (1) los tres van en **`warn`, no en `block`** — es la mitigación nº1 de riesgos de este mismo plan, y el candado protege la EXISTENCIA del check, no su dureza; (2) al ser `locked`, su `on_error` efectivo SÍ es `block` (task_prod03_09), o sea que un hallazgo avisa pero un check ROTO bloquea; (3) la resolución mira primero la tabla nueva y cae a las columnas viejas (`platform_settings.guardrails_config`, `projects.guardrails_config`) si esa capa no tiene fila — mientras nadie escriba, la plataforma se comporta igual que antes, que es la dirección segura. Tests: `test_guardrail_configs_crud.py` **7/7** y `test_guardrail_locked_override_422.py` **5/5**, los dos por la ruta HTTP; el segundo cubre las TRES formas de saltarse un candado (sobrescribir, degradar la acción y `remove: true`, que es la que más fácil se cuela) más la guarda de la guarda (un override permitido SÍ persiste). Rojo verificado poniendo `strict=False`: 4 rojos.
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

- [x] **Título**: Tras aprobar el ADR de la decisión clave 1: añadir
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
  - ✅ **Hecho (2026-08-01)**. El ADR de la decisión clave 1 **existe y está `accepted`**: es el [ADR 0102 D5](../05-architecture-decisions/0102-cableado-motor-guardrails-runtime.md), que eligió la opción (c) — `on_error` por check con «default `block` (fail-closed) para los guardrails `locked` de plataforma». No hacía falta escribir uno nuevo; hacía falta implementar lo que decía. Entregado: `GuardrailSpec.on_error` pasa de `str = "warn"` a `str | None = None` (guardar la AUSENCIA es lo que permite derivar el default) + `effective_on_error` = `block` si `locked`, `warn` si no, y lo escrito por el operador gana siempre; `to_dict` solo serializa el `on_error` explícito, porque `locked` ya viaja y el receptor recalcula el mismo default (ADR 0102 D3). Y `available: False` deja de pasar en silencio: `_is_unavailable` lo trata con la MISMA política que un crash, porque son el mismo caso —el check no emitió veredicto—. **`tests/unit/test_pipeline_on_error_policy.py` (12) + `tests/unit/test_guardrails_engine.py` (13) en verde**; los 54 tests unit con `-k guardrail` y los 24 del runtime, también. **Un cambio de comportamiento con nombre**: un check que revienta bajo `warn` ahora sale `triggered=True` con acción `warn` en vez de `triggered=False`. Es lo que pide el enunciado de esta tarea al pie de la letra («convirtiendo la excepción en un `GuardrailOutcome` **triggered** con esa acción») y el criterio de aceptación del propio ADR 0102 («uno no-locked produce warn»). No cambia el enforcement —`decision.allowed` sigue True con `warn`—, pero sí la visibilidad: `record_pipeline_decision` solo persiste los outcomes disparados, así que hasta hoy un check roto no dejaba rastro en ninguna parte. Fail-open sí; invisible no. `tests/unit/test_guardrails_engine.py::test_check_crash_fail_open_by_default` se actualizó para fijar la conducta nueva.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_09_a
    runtime: python-pytest
    command: "pytest tests/unit/test_pipeline_on_error_policy.py -v"
  ```

#### `task_prod03_10` — Seam async y límites de tamaño de input

- [x] **Título**: Ejecutar `pipeline.run` vía `asyncio.to_thread` (o
      `anyio.to_thread.run_sync`) en los hosts async
      (`apps/api-server/src/api_server/guardrails/planning.py:273` y `:341`,
      y los nuevos hosts de la Fase D), y añadir un límite de tamaño de
      input configurable por check (truncado + flag en el payload) para
      textos largos — el detector genérico de `secret_leakage`
      (`checks/secret_leakage.py:128-136`) es lineal-cuadrático en el peor
      caso. Cubre guardrails-10; coordinación: prod-13 trata el event loop en
      global, este seam se cierra aquí para no cablear (Fase D) un motor
      bloqueante.
  - ✅ **Hecho (2026-08-01)**: `_run_off_loop` (`asyncio.to_thread`) en los dos hosts de `api_server/guardrails/planning.py` — el motor es puro, así que sacarlo a un hilo es seguro por construcción. Y el tope de input, que en el runtime ya existía (D6) pero en el api-server no: `_bounded_input` recorta a `MAX_SCANNED_CHARS = 50_000` —el mismo número que `_HOOK_INPUT_MAX` del runtime, para que el mismo texto se trate igual a los dos lados— y **anota `truncated: True` en el metadata del contexto**, que viaja al evento persistido: un escaneo parcial presentado como completo es peor que no escanear, porque su «no se encontró nada» se lee como una garantía que no se dio. `tests/unit/test_pipeline_async_seam.py` (6) en verde, comparando `threading.get_ident()` dentro y fuera del hook.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_10_a
    runtime: python-pytest
    command: "pytest tests/unit/test_pipeline_async_seam.py -v"
  ```

### Fase D — Cableado del motor en los flujos reales

#### `task_prod03_11` — El dispatch resuelve y transporta la config efectiva

- [x] **Título**: En el dispatch del worker
      (`apps/workers/src/workers/execution.py` — hoy CERO referencias a
      guardrails), resolver la config efectiva por (tenant, proyecto) vía el
      servicio de `task_prod03_08` e incluirla serializada en el task spec
      que recibe el runtime (mismo canal que `approval_policy`,
      `__main__.py:248`), con límite de tamaño y versión para invalidación.
      Cubre guardrails-1 (transporte). Depende de `task_prod03_08`.
  - ✅ **Hecho (2026-08-01)**: la premisa «hoy CERO referencias a guardrails» era falsa desde el ADR 0102 D3; lo que faltaba de verdad eran las dos cosas del final del enunciado, las dos dependientes de la tabla de `task_prod03_07`. Ahora `_resolve_effective_guardrails` **delega en `get_effective_guardrail_config`**, que fusiona las TRES capas, y el resultado lleva `version` (`p<v>.t<v>.j<v>`, con `-` para la capa ausente) **hermana** de `guardrails`, no dentro: `parse_config` solo mira la clave `guardrails`, así que el runtime la ignora sin enterarse y el worker puede comparar sin re-derivar. Se eligió el contador de escrituras y no un hash del contenido a propósito: cambia en cada escritura aunque el JSON quede igual, y dice QUÉ capa se movió. `tests/integration/test_dispatch_guardrail_config.py` **4/4**, contra PostgreSQL con las tres capas escritas — incluido el caso que el CRUD no puede cubrir: una fila de tenant que **se saltó el 422** (un seed, una restauración, un `psql`) intentando `remove: true` sobre el check locked, y el candado sigue ganando en la resolución no-estricta del dispatch. **Coste anotado**: los dos tests unitarios que fusionaban capas en `tests/unit/test_guardrails_transport.py` se sustituyeron —monkeypatcheaban la capa de plataforma, así que por construcción no podían ver ni la tabla ni la capa tenant—; la cobertura de fusión se mudó al fichero de integración y en el unitario queda lo que sí es unitario: el hilo por `_agent_spec` y el contrato best-effort (un fallo resolviendo devuelve `None`, nunca tumba un run).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_11_a
    runtime: python-pytest
    command: "pytest tests/integration/test_dispatch_guardrail_config.py -v"
  ```

#### `task_prod03_12` — Pipeline en los 4 hooks del bucle del agente

> **Estado (2026-07-31, contabilidad del roadmap)**: la nota anterior («PARCIAL — solo `post_tool`»,
> 2026-07-06) ha caducado. Los CUATRO hooks están cableados en `graph.py`: `pre_tool`/`post_tool` por
> el ADR 0102 D2 (`_screened_tool_call`) y `pre_llm`/`post_llm` por `task_wf_50`; el
> `GuardrailPipeline` se instancia desde la config del spec en `guardrails.build_pipeline`. Verificado
> ejecutando los cuatro ficheros de test del runtime (24/24 en verde, ver detalle abajo). Lo único que
> queda del bloque es papel: el disclaimer de `tools.py:15-17` es ya falso y no se ha retirado (el de
> `chat/modes.py:62-64` sigue siendo cierto porque el chat de planning lo cablea `task_prod03_14`).

- [x] **Título**: Instanciar `GuardrailPipeline` desde la config del spec en
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

- [x] **Título**: El runtime (sandboxed, sin DB) acumula las decisiones del
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

- [x] **Título**: Invocar `run_planning_chat_guardrails`
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
  - ✅ **Hecho (2026-08-01)**: `api_server/guardrails/route_gates.py` con los dos gates, y sus llamantes en las rutas reales: `gate_planning_turn` en `routers/conversations.py::post_message` (`pre_llm`, **antes** de persistir el mensaje y de programar la respuesta del equipo — bloquear después de haber llamado al LLM no bloquea nada) y `gate_plan_generation` en `routers/plans.py::create_plan`. `tests/integration/test_planning_guardrails_route.py` **7/7**, todo por HTTP. Rojo verificado desactivando el gate del chat: 2 rojos.
    **Dos decisiones de alcance, las dos medidas y no adivinadas:**
    1. **El gate de «Generar Plan» corre SOLO sobre el borrador del chat**, no sobre un `specification` inline. Cablearlo en los dos sitios rompió **14 tests de flujos legítimos** (`test_plan_approval.py`, `test_plan_comments.py`) que crean planes con tareas y `summary` vacío: el `PLAN_DRAFT_SCHEMA` exige `summary` no vacío, y aplicarlo al contrato público de la API convertía el gate en un bloqueo de la creación de planes. El gate existe para que un borrador mal formado del EQUIPO no se materialice. Los 14 vuelven a estar en verde, y hay un test nuevo que fija la exclusión para que nadie la «arregle» sin querer.
    2. **El evento del turno BLOQUEADO se persiste en su propia transacción.** `run_planning_chat_guardrails` escribe sus eventos en la sesión de la request, y un `block` termina en `HTTPException` → la dependencia hace rollback → se perdería justo el evento del único turno que la plataforma llegó a DETENER, que es el que el dashboard necesita enseñar. Está cubierto por su test.
       Queda **sin hacer** lo que la tarea pedía de papel: sincerar `task_11_22` en `docs/roadmap/11-guardrails-precios.md` — ese fichero es de otro carril de esta ola y no se toca desde aquí (el criterio de cierre 4 sigue abierto).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_14_a
    runtime: python-pytest
    command: "pytest tests/e2e/test_planning_guardrails_route.py -v"
  ```

### Fase E — Verificación integral y documentación

#### `task_prod03_15` — E2E integral: «Cliente Externo» detiene la primera tool sensible

- [x] **Título**: Test e2e que ejecuta una tarea real con el preset
      `customer-external`: la primera tool sensible (`http_post` o
      `shell_exec`) se aparca con `awaiting_human_approval`, el rechazo
      aborta, la aprobación continúa SIN re-aparcar la misma acción, y una
      solicitud no atendida expira por el job. Es el test de regresión del
      titular de la auditoría («ni siquiera Cliente Externo detiene una sola
      tool»). Depende de Fases A, B y D completas.
  - ✅ **Hecho (2026-08-01)**: `tests/integration/test_customer_external_preset_gates.py` **7/7**, con los cuatro tramos del enunciado —se aparca, el rechazo deja la acción sin autorizar y aborta la ejecución, aprobar deja pasar la MISMA acción **exactamente una vez** (T1 del ADR 0135) y una acción distinta de la misma categoría se vuelve a aparcar, y lo no atendido caduca a `timed_out` + ejecución `aborted` + tarea `blocked`. Tres cosas deliberadas: (1) el preset se **lee de `BUILTIN_POLICIES`**, no se copia en el test — una copia seguiría verde mientras el preset real se relaja; (2) hay un **control** con `sandbox` sobre la misma llamada, porque sin él un gate roto en «siempre aparca» pasaría el test y no gatearía nada; (3) hay un test del **vocabulario** (las 13 canónicas), que es donde estuvo el agujero g6 y lo que nadie miró. La solicitud se crea por `request_approval_if_needed`, el camino real del worker, no con un INSERT. Ubicación: en `tests/integration/` y no en `tests/e2e/` como nombra el plan, por lo mismo que el fichero de `task_prod03_14` — `tests/e2e/` exige runner Docker y CI no lo corre; un test que no se ejecuta no vigila.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod03_15_a
    runtime: python-pytest
    command: "pytest tests/e2e/test_customer_external_preset_gates.py -v"
  ```

#### `task_prod03_16` — Documentación y ADRs

- [x] **Título**: `docs/04-reference/guardrails.md` (capas, candados,
      `on_error`, hooks cableados, tabla tool→categoría) y
      `docs/04-reference/validacion-humana.md` (13 categorías, presets,
      ciclo aprobación/expiración/`approved_actions`);
      `docs/06-runbooks/aprobaciones-atascadas.md`; ADR nuevo de política de
      fallo (decisión 1+3) y extensión del ADR 0020 (decisión 2) con estado
      según apruebe el humano; actualizar el estado «falta el job» del ADR
      0016 (`0016-motor-validacion-humana.md:98`).
  - ✅ **Hecho (2026-08-01)**, con una excepción anotada abajo. Entregado: **`docs/04-reference/validacion-humana.md`** (nuevo — 13 categorías, mapa tool→categoría con el porqué de las dos decisiones que no hay que deshacer, tools MCP y las dos reglas del merge, 4 presets, ciclo de vida, qué autoriza exactamente una aprobación, resolución atómica y caducidad); **`docs/06-runbooks/aprobaciones-atascadas.md`** (nuevo — las cuatro causas en orden de descarte, con las consultas, la trampa del beat sin importar y la de la caché Redis de 30 s que hace que un `UPDATE` a pelo del setting no surta efecto); y `docs/04-reference/guardrails.md` ampliado con las capas persistidas, el baseline sembrado, el CRUD, `on_error`, el seam async y **dónde se invocan de verdad** los gates. `docs/07-changelog/prod-03-guardrails-validacion-humana.md` NO se escribe aquí: es el criterio de cierre 5 y depende de tareas que siguen abiertas. El **ADR 0016** queda sinceramiento: su «falta el job periódico» llevaba dos meses siendo falso desde hoy y está tachado con la referencia. Los ADR de las decisiones 1 y 2 **ya existían** y están `accepted` (0102 D5 y 0135) — no hacía falta escribirlos, hacía falta implementarlos. El de la decisión 3 se escribe nuevo y en `proposed`: **[ADR 0153](../05-architecture-decisions/0153-categoria-no-listada-en-la-politica-de-aprobacion.md)**. `tests/docs/` 286/286.
    **Excepción**: la tabla tool→categoría se documenta en `validacion-humana.md`, no dentro de `guardrails.md` como pide el enunciado. Son dos cosas distintas —el motor de guardrails y la validación humana— y meter el mapa de aprobación en la referencia del motor invita a confundirlas, que es precisamente lo que ya pasó con el vocabulario en el hallazgo g6.
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
