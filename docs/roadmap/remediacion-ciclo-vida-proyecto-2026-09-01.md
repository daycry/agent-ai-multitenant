---
plan_id: remediacion-ciclo-vida-proyecto-2026-09-01
title: Remediación del ciclo de vida de un proyecto — workers, agentes, memorias y runtimes
status: in_progress
blocking_plan: []
started_at: 2026-09-02
completed_at: null
estimated_duration_calendar: 5-6 semanas
estimated_effort_person_days: 34
created_by: claude-fable-5-1-audit-2026-09-01
docs_language: es
priority: P0
source_audit: auditoria-ciclo-vida-proyecto-2026-09-01
---

# Plan de remediación — Ciclo de vida de un proyecto (2026-09-01)

## Cabecera

| Campo             | Valor                                                                                     |
| ----------------- | ----------------------------------------------------------------------------------------- |
| **ID del Plan**   | `remediacion-ciclo-vida-proyecto-2026-09-01`                                              |
| **Prioridad**     | P0 (olas 0-1) · P1 (olas 2-3) · P2 (ola 4)                                                |
| **Bloqueado por** | Ninguno. Asume mergeada la rama `fix/auditoria-git-dependencias-2026-09-01`               |
| **Rama sugerida** | `plan/remediacion-ciclo-vida-2026-09`                                                     |
| **Método**        | TDD estricto: test en rojo reproduciendo el hallazgo, arreglo, verde. Un commit por tarea |
| **Origen**        | [auditoria-ciclo-vida-proyecto-2026-09-01](./auditoria-ciclo-vida-proyecto-2026-09-01.md) |

## Resumen

81 hallazgos, de los que los 25 críticos y altos se verificaron adversarialmente contra el
código (dos con reproducción ejecutable). El patrón dominante ya no es «cableado del último
tramo» como en la auditoría del 2026-07-25: es **atribución y contrato**. Varias piezas leen «la
última ejecución de la tarea» sin saber si es del implementador o del reviewer, de esta
reclamación o de la anterior; el runtime aparca con una política que el worker evalúa distinta;
la fase de tests se cerró con un test que no ejercita Celery; el compose de producción niega
el `exec` que el diseño exige.

Las olas van por **daño ÷ coste**. La ola 0 son cuatro roturas deterministas en producción y
cinco defectos de un día cada uno que dejan al reviewer juzgando lo que no es. La ola 1 cierra
las carreras del ciclo de vida. Las olas 2 y 3 son seguridad del sandbox y calidad de lo que
el modelo ve. La ola 4 es operación.

## Criterios de cierre del plan

1. Todos los checkboxes marcados `[x]` con su test automático en verde.
2. Suites `unit` + `integration` + runtime sin regresiones respecto al baseline.
3. Tests humanos `human_cv_01..04` (§Tests humanos) validados por el operador.
4. Entrada en `docs/07-changelog/remediacion-ciclo-vida-proyecto.md`.
5. Los ADR 0060, 0071, 0072, 0102, 0129, 0149 y 0163 actualizados en el mismo commit que
   los cambios que los contradicen (regla de precedencia de `CLAUDE.md`).

---

## Ola 0 — Roto en producción hoy (P0 · ~5 d)

Cuatro roturas deterministas y cinco defectos de atribución. Ninguna exige diseño nuevo.

### `task_cv_00` — La fase de tests post-run vuelve a correr (A-02)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: `dispatch_test_runtime_and_wait` hace `AsyncResult.get()` dentro de un task
      prefork y Celery lo prohíbe (`RuntimeError: Never call result.get() within a task!`,
      reproducido en el venv). Todo `done` con criterios automáticos acaba en
      `test_phase_dispatch_failed` y el reviewer nunca ve tests reales. Arreglo mínimo:
      `allow_join_result()` + lane `test` con concurrencia propia (hoy `default,ingestion,test,review`
      en un solo worker con `--concurrency=2`: la espera síncrona se autoinanicia). Arreglo
      correcto: no esperar dentro de `run_execution`; encadenar (`link`/`chain`) y que el
      consumidor del `test_run_completed` despache el review.
      **Test**: unit con `celery._state._set_task_join_will_block(True)` alrededor de la
      función (hoy devuelve `dispatch_failed`); integración con worker `prefork` real.
      **Coste**: 1 d (parche) / 2 d (reestructura).

### `task_cv_01` — La política de aprobación que evalúa el worker es la efectiva (A-01)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: `request_approval_if_needed` evalúa `project.human_approval_policy` cruda y su
      docstring lo declara «no es de este ADR»; el runtime aparcó con el preset efectivo. Pasar
      `prepared.approval_policy` como `policy=` y, si el runtime aparcó y el worker decide que
      no hacía falta, tratarlo como F12 (`failed(approval_policy_mismatch)` + transición), nunca
      dejar `awaiting_human_approval` sin `ApprovalRequest`.
      **Test**: integración con `human_approval_policy=None`, runner fake que emite
      `awaiting_human_approval` con `approval={category:"http_post"}` → fila en
      `approval_requests` y tarea `awaiting_human_approval`.
      **Coste**: 0,5 d.

### `task_cv_02` — El compose del instalador permite `exec` en el socket-proxy (B-01)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: `compose_generator.py` fija `EXEC=0` y `test_compose_generator.py` lo protege;
      `docker-compose.manuals.yml` lleva `EXEC=1` con el comentario «con EXEC=0 todo exec_run
      daba 403». Todos los checks, `pre_install`, `stack_exec` y healthchecks de sidecars pasan
      por `exec_run`. Poner `EXEC=1`, invertir el aserto, corregir el ADR 0060 §Parte A y añadir
      un test de coherencia entre el generador y `manuals.yml` para esa ACL.
      **Test**: el de coherencia; en un stack generado, `exec_run('true')` sobre un contenedor
      efímero devuelve 0.
      **Coste**: 0,25 d.

### `task_cv_03` — Los agentes core dejan de pinear `provider="anthropic"` (F-01)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: 11 agentes de `builtin_agents.py` (y `qa_e2e_automator.py`) pinean un provider
      que no existe en `LLMProviderKind`; las copias adoptadas copian `model_config` verbatim y
      el refresco de arranque lo re-afirma. Quitar el pin (heredar como CI4, ADR 0055), test
      `every BuiltinAgent.model_provider in LLM_PROVIDER_KINDS`, y migración de datos con el
      patrón 0145 (sólo copias con `forked_from → global_builtin` y `provider='anthropic'`,
      respaldo + downgrade).
      **Test**: el unitario; integración de la migración sobre copias sembradas.
      **Coste**: 1 d.

### `task_cv_04` — Los sidecars ADR 0129 pueden arrancar (B-02)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: `build_aux_run_kwargs` lanza postgres/redis/mysql/mariadb con `cap_drop ALL` sin
      `cap_add` ni `user`, la combinación que `gotchas/docker-cap-drop-all-breaks-official-images.md`
      documenta como crash-loop. Añadir `cap_add=[CHOWN, DAC_OVERRIDE, FOWNER, SETGID, SETUID]`
      (el mismo `x-infra-caps` del compose) y `remove(force=True, v=True)`.
      **Test**: integración marcada `docker` que levante `postgres:16-alpine` por digest con los
      kwargs reales y espere `_wait_healthy` en verde.
      **Coste**: 0,5 d.

### `task_cv_05` — El perfil AppArmor deja ejecutar los toolchains bajo `/opt` y `/ms-playwright` (B-03)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: `docker/apparmor/agent-runtime.profile` sólo da `ix` a `/usr`, `/bin`, `/sbin`,
      `/lib`, `/lib64`, `/workspace` y `/home/agent`; java-maven, java-gradle, node-playwright y
      browser-runtime ejecutan desde `/opt/**` y `/ms-playwright/**`. Añadir las dos reglas y un
      test que recorra `PATH`/`JAVA_HOME` de cada Dockerfile contra el perfil.
      **Test**: el del perfil; en CI Linux, `java -version` bajo `--security-opt apparmor=agent-runtime`.
      **Coste**: 0,5 d.

### `task_cv_06` — El reviewer no recibe su propio veredicto como salida del implementador (C-03)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: `prior_rows` en `dispatch.py` selecciona las últimas ejecuciones de la tarea sin
      filtrar por agente (el filtro sí existe para `<commands-run>`); lo mismo en
      `_read_predecessor_briefs` y `_read_prior_failure`. Aplicar el predicado
      `agent_id != reviewer_agent_id` en los tres sitios (o una columna `kind` en `executions`).
      **Test**: `test_in_review_dispatch` con `reviewer_runs_after=3`:
      `implementer_output == prior_output`.
      **Coste**: 0,5 d.

### `task_cv_07` — El diff de re-review sólo contiene los commits de la tarea (C-02)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: `compute_task_review_diff` hace `git diff {oldest}^..{newest}` y arrastra los
      commits hermanos intercalados (reproducido: `otro.py` aparece en el diff de `T1`). Diff por
      commit (`diff-tree -p` de cada sha con el trailer) o limitado a los paths de esos shas.
      **Test**: en `tests/unit/test_review_diff.py`, A1 → B(otra tarea) → A2 ⇒ `otro.py` fuera.
      **Coste**: 0,5 d.

### `task_cv_08` — Un fallo del vigía de cancelación no pierde el run ni deja la credencial (A-03)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: el bucle de `_watch_for_cancel` no captura excepciones y el `finally` lo
      re-lanza al `await watcher` justo cuando el contenedor terminó bien; `staged_credentials.cleanup()`
      no se alcanza. `try/except Exception` + `continue` en el bucle; `cleanup()` en su propio
      `finally` antes de esperar al vigía.
      **Test**: unit con `sessionmaker` que lanza en la segunda consulta y runner fake que
      devuelve `done` → ejecución `done` y `cleanup()` llamado.
      **Coste**: 0,25 d.

---

## Ola 1 — Integridad del ciclo de vida (P0 · ~6 d)

### `task_cv_10` — El reconciler no transiciona con una ejecución anterior a la reclamación (C-01)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: `_reconcile_stuck_tasks` toma la última `Execution` por `created_at` sin
      compararla con `task.started_at`; con la cola llena flipa a `in_review` una tarea recién
      re-despachada usando el run del reviewer, y el implementador nunca corre. Si
      `latest.created_at < task.started_at`, tratar como reclamación huérfana (rama a2).
      **Test**: integración: `started_at=now-6min`, execution `done` de `now-10min` → no transiciona.
      **Coste**: 0,5 d.

### `task_cv_11` — Un `commit_failed`/`rebase_conflict` bloquea la tarea (A-06, C-04)

- [x] _(hecho 2026-09-02, tests en verde; la guarda del PR sólo cuenta las `done` cuyo último run acabó en `commit_failed`/`rebase_conflict`, para no parar planes con tareas de diseño que nunca tuvieron commit)_ **Título**: la transición a `in_review` se persiste antes del commit; si el commit o el
      rebase fallan sólo se marca la ejecución, el reviewer revisa un commit local y puede
      aprobar a `done` trabajo que no está en la rama del plan (y el siguiente `sync_to_head`
      lo borra). Si `_commit_and_push_worktree` devuelve `abort_code`, `in_review → blocked`
      en la misma transacción, `commit_failed` en `_REVIEW_ESCALATION_ABORT_CODES`, y guarda en
      `open_plan_pr` que cuente trailers `Task-Id` frente a tareas `done`.
      **Test**: integración con `commit_task` que lanza → tarea `blocked` y visible en el panel.
      **Coste**: 1 d.

### `task_cv_12` — Recuperación de un run cuyo worker murió con el contenedor vivo (A-04)

- [x] _(hecho 2026-09-02, tests en verde; `list_managed_execution_ids` conserva `all=True`: el contenedor `exited` se trata ahora explícitamente antes de la lógica de huérfanos, y la promesa del ADR 0149 queda corregida en el propio ADR)_ **Título**: `list_managed_execution_ids` usa `all=True` (un contenedor `exited` cuenta como
      vivo), la re-entrega de Celery se descarta por el run-lock antes del supersede, y la fila
      queda 7 h en `running` con el resultado del contenedor perdido. En el sweeper, para filas
      `running` con contenedor `exited/dead`: leer `container.logs()` → `_scan_logs_for_terminal`
      y finalizar con el resultado real (+ commit del worktree si `done`); si no hay terminal,
      sellar ya. Corregir la promesa del ADR 0149.
      **Test**: integración con runner fake cuyo contenedor está `exited` y logs con
      `execution.finished` → fila finalizada con ese resultado en la primera pasada.
      **Coste**: 1,5 d.

### `task_cv_13` — Una reclamación viaja con identidad (A-05)

- [x] _(hecho 2026-09-02, tests en verde; migración 0148 `tasks.claim_id` nullable sin backfill, comprobación en el worker ANTES del supersede, mensaje sin `claim_id` aceptado por compatibilidad: desplegar worker antes que orquestador)_ **Título**: V-1 revierte reclamaciones de más de 30 min aunque el mensaje siga en cola
      (`prefetch=1`, `concurrency=2`, runs de hasta 2 h: ocurre con tres tareas `ready`), y un
      duplicado viejo puede ganar al redespacho con feedback. `claim_id` que el dispatch escribe
      en la tarea y viaja en `ExecutionRequest`; `_prepare_run` descarta (`skipped/stale_claim`)
      si no coincide. Alternativa barata mientras tanto: el reconciler no revierte si `LLEN default > 0`.
      **Test**: dos mensajes para la misma tarea con `claim_id` distinto → sólo corre el vigente.
      **Coste**: 1,5 d.

### `task_cv_14` — El cierre de plan se reconcilia y avisa (G-01, G-02)

- [x] _(hecho 2026-09-02, tests en verde)_ **Título**: el cierre cuelga de un `send_task` cuyo `False` se ignora y ningún beat lo
      reencola (el «reconciler» del docstring de `plan_docs.py` no existe); un segundo cierre pisa
      `pr_url` con el 422 «already exists». Pasada del reconciler «`completed` sin `pr_url` ni
      `pr_error` > 10 min → reencolar»; `keep_existing_url=True` en el camino principal y, ante
      422/409 «already exists», recuperar la URL del PR abierto por `head`; evento `plan_pr_failed`.
      **Test**: plan `completed` sin `pr_*` → `send_task("workers.open_plan_pr")`; opener que
      lanza 422 sobre plan con `pr_url` → `pr_url` intacto.
      **Coste**: 1 d.

### `task_cv_15` — Errores del daemon al lanzar y runs de review que aparcan (A-07, A-08)

- [x] _(hecho 2026-09-02, tests en verde; `container_launched_at` se sigue sellando ANTES del `_start`: con el fallo capturado la fila ya no queda `running`, y el sello previo es lo que acota la ventana de aprovisionamiento del sweeper M1)_ **Título**: un `APIError` de `docker.from_env()`/`_start` sale sin capturar (fila `running`,
      DLQ, «worker loss» a los 5 min); un run de REVIEW que aparca en aprobación queda no
      terminal para siempre y consume `retry_count`. Capturar el lanzamiento →
      `failed(container_launch_failed)` + transición; sellar `container_launched_at` tras `_start`;
      para `request.review` inyectar una política que no aparque (workspace ya read-only).
      **Test**: runner fake que lanza `APIError` → `failed`; review con `awaiting` → sellada.
      **Coste**: 0,5 d.

---

## Ola 2 — Seguridad del sandbox (P1 · ~8 d)

### `task_cv_20` — El spec y el token interno no viven en el env del proceso del agente (D-01)

- [x] _(hecho 2026-09-02, tests en verde; paso 1: env mínimo explícito en `shell_exec` (allowlist PATH/HOME/locale/proxy); paso 2: spec y token en `/run/secrets` (`AGENT_TASK_SPEC_FILE`, `AGENTIC_INTERNAL_TOKEN_FILE`, mismo staging que la credencial del modelo) y el boot los retira de `os.environ` aunque un worker antiguo los mande en línea; desplegar la imagen del runtime antes que el worker)_ **Título**: `shell_exec` hereda el env completo (`subprocess.run` sin `env=`) y
      `python_function` comparte uid con el runtime: `AGENT_TASK_SPEC` (headers de MCP,
      `approved_actions`, código de python_function) y `AGENTIC_INTERNAL_TOKEN` (que autoriza
      `mcp-oauth-token`) son legibles por el modelo o por una inyección. Paso 1 (S): `env`
      mínimo explícito en `shell_exec` y el runner python. Paso 2 (M): spec+token por fichero en
      `/run/secrets` (el patrón ya existe para la credencial del modelo) y `__main__` los retira
      de `os.environ` tras leerlos; `mcp_servers[].headers/env` al mismo mount.
      **Test**: `ShellExecTool({"python"})` con `python -c "import os,sys;sys.exit('AGENTIC_INTERNAL_TOKEN' in os.environ)"` → 0.
      **Coste**: 2 d.

### `task_cv_21` — Tope anunciado por observación y en `steps_log` (D-02)

- [x] _(hecho 2026-09-02, tests en verde; `_MAX_OBSERVATION_CHARS = 24_000` aplicado en `act()` al mismo valor que va al step (`truncated`, `bytes_total`) y a la observación, y `read_file` con `offset`/`limit` en caracteres y `total_chars`)_ **Título**: una lectura de 900 KB entra dos veces al prompt (1,8 M chars, ~450k tokens en un
      turno) y entera al `steps_log`; el presupuesto de tokens se evalúa al turno siguiente.
      `_MAX_OBSERVATION_CHARS` (~24k) aplicado en `act()` con marcador explícito al estilo de
      `list_files` («showing the first N of M chars; use offset/limit»), `offset`/`limit` reales
      en `read_file`, y en `steps_log` `result` recortado + `bytes_total`.
      **Test**: `ScriptedModelClient` que lee 900 KB → `len(json.dumps(last_observation)) < 30_000`
      y step con `truncated: true`.
      **Coste**: 1,5 d.

### `task_cv_22` — El hook `pre_llm` escanea lo que de verdad se manda (D-03)

- [x] _(hecho 2026-09-02, tests en verde; el hook escanea el mensaje real de `_decide_messages` (sistema + usuario) con cabeza y cola ante el tope de 50k, así que una tarea sin contexto también se escanea una vez)_ **Título**: el hook lee `entry["content"]` y las observaciones son
      `{"role":"observation","tool":…,"output":{…}}`: cero eventos con la forma real, uno con la
      forma sintética del test que cerró B-05. Escanear `_decide_messages(state)[1].content` (cabeza
      y cola ante el cap de 50k).
      **Test**: cambiar el fixture de `test_llm_guardrail_hooks.py` a la forma real → pasa.
      **Coste**: 0,5 d.

### `task_cv_23` — Toda tool MCP pasa por el gate de aprobación (D-04)

- [x] _(hecho 2026-09-02, tests en verde; fallback `external_http_post` para nombres con namespace que ningún spec catalogó; un spec listado SIN categoría sigue siendo el opt-out explícito del operador (`UNGATED_TOOL`))_ **Título**: `register_mcp_server` registra todas las tools que lista el servidor y
      `ApprovalGate.review` devuelve `None` para cualquier nombre sin categoría, también bajo
      «Cliente Externo». Fallback `external_http_post` para nombres con namespace no mapeados (el
      criterio de `spec_approval_category` para `mcp_tool`) y/o registrar sólo lo catalogado.
      **Test**: `test_boot_approval_mcp_gate` sin `tool_specs` y política customer-external → step
      `awaiting_human_approval`.
      **Coste**: 0,5 d.

### `task_cv_24` — La dep-cache se acota por tenant y deja de ser 0777 (B-04)

- [x] _(hecho 2026-09-02, tests en verde; clave `{tenant_slug}/{prefix}-{hash}` en `cache_path_for`/`ensure_entry`/`mount_for`/`invalidate`, 0755 + chown 1000 en vez de 0777, purga del layout plano anterior en `purge_expired`, y el endpoint de invalidación acotado al tenant que llama)_ **Título**: `cache_path_for` devuelve `{prefix}-{lock_hash}` sin tenant, montado RW en el
      contenedor no confiable y con `chmod 0777`: dos tenants con el mismo lockfile comparten el
      directorio donde maven/bundler/composer no verifican contenido. Clave
      `{tenant_slug}/{prefix}-{hash}` en `cache_path_for`/`invalidate`/`purge_expired`; `chown 1000`
      en vez de 0777; migración de layout con purga.
      **Test**: `cache_path_for` contiene el tenant; dos tenants → dos directorios.
      **Coste**: 1 d.

### `task_cv_25` — Un bridge efímero por ejecución en vez de una L2 compartida (B-07)

- [x] _(hecho 2026-09-02, tests en verde; `AgentContainerRunner` crea un bridge `internal` por ejecución (`agent-run-<exec>-<hex>`, etiqueta `com.agentic-platform.run-bridge`), conecta con alias el egress-proxy, el api-server y los MCP internos del proyecto (`ContainerSpec.peers`), lo desmonta al terminar y el sweeper poda los huérfanos; `WORKERS_AGENT_NETWORK_PER_EXECUTION=false` devuelve la red compartida; ADR 0012 con adenda; el test de «dos sandboxes no se ven» es de Docker real y queda para el stack de pruebas)_ **Título**: todos los sandboxes de todos los tenants, previews, api-server y workers
      comparten `agentic-agents` con ICC (`isolation.py` promete lo contrario). Bridge interno por
      ejecución (patrón de `test_runtime.py:_create_bridge`) con `network.connect` del egress-proxy
      y del api-server. Actualizar ADR 0012.
      **Test**: dos sandboxes → `curl` entre ellos falla.
      **Coste**: 1,5 d.

### `task_cv_26` — El preview de review no monta el worktree en RW (B-06)

- [x] _(hecho 2026-09-02, tests en verde; `repository_config.preview.writable_paths` (tmpfs) y `preview.workspace_rw` (opt-in), documentado en `docs/03-guides/app-review-images.md`)_ **Título**: `review_runtime_task.py` lanza la app del tenant 48 h con el worktree del plan en
      RW. `workspace_read_only=True` por defecto con `tmpfs` para rutas de escritura declaradas
      (opt-in `preview_workspace_rw`).
      **Test**: `Mounts[/workspace].RW == false`.
      **Coste**: 0,5 d.

### `task_cv_27` — Las memorias recuperadas van valladas y un hit bloqueado se descarta (E-03)

- [x] _(hecho 2026-09-02, tests en verde; valla compartida en `agent_runtime/untrusted.py`, bloque propio «RECALLED MEMORY AND KNOWLEDGE» y descarte de hits bloqueados en `recall`)_ **Título**: las memorias entran al prompt como JSON bajo «Context so far» sin
      `_fence_untrusted`, y el guardrail del `recall` es sólo LOG. Bloque propio vallado para
      `role in {memory, knowledge}` y descarte de hits con `action == "block"`.
      **Test**: memoria con `UNTRUSTED_DATA>>>` dentro → neutralizada y dentro de la valla; hit
      bloqueado → fuera del contexto.
      **Coste**: 0,5 d.

---

## Ola 3 — Lo que el modelo ve: memoria y agentes (P1 · ~6 d)

### `task_cv_30` — La escalera de lectura sigue al ADR 0071 (E-01)

- [x] _(hecho 2026-09-02, tests en verde; un agente de IA lee `team_shared` + `project_shared` + `global` sea cual sea su scope de escritura (los punteros los pone el endpoint); `private` conserva sus filas delante y un scope no canónico sigue leyendo sólo `global`)_ **Título**: `_default_readable_scopes` usa el orden viejo (`team_shared` más estrecho que
      `project_shared`) sobre el scope crudo del agente: un agente `project_shared` nunca lee
      `team_shared` y uno `global` sólo lee `global`. Leer «todo scope compartido con puntero»
      (`global` + `project_shared` del proyecto + `team_shared` del equipo).
      **Test**: `"team_shared" in _default_readable_scopes("project_shared")`.
      **Coste**: 0,5 d.

### `task_cv_31` — `memory_store` aplica la política de equipo y el enrutado (E-02)

- [x] _(hecho 2026-09-02, tests en verde; `memory_store` calcula el scope con `_store_scope_for` (política del equipo > agente > default de plataforma, y enrutado por tipo), y un `scope` explícito distinto del enrutado sigue dando 403)_ **Título**: la tool interna persiste con `agent.memory_scope` crudo; el memorizer usa
      `resolve_effective_memory_scope` + `route_scope_for_type`. Unificar.
      **Test**: agente `global` + `episodic` → `project_shared`.
      **Coste**: 0,5 d.

### `task_cv_32` — El destilador lee los steps reales y las memorias son sticky (E-04, E-05)

- [x] _(hecho 2026-09-02, tests en verde; el destilador lee `summary` + `result.error` + `result.output[:200]` (y el fixture del test usa ya la forma real de un step); las memorias van fuera de la ventana de pasos, valladas y acotadas a 3 x 500 chars)_ **Título**: `distillation.py` lee `note/output/content` que los steps no tienen (el fixture
      del test miente) y las memorias salen de la ventana de 8 items a los pocos turnos. Leer
      `summary` + `result.error` + `result.output[:200]`; renderizar `role=memory` como bloque
      sticky acotado (3 × 500).
      **Test**: step con `result.error="ImportError psycopg"` → «ImportError» llega al LLM;
      `_decide_messages` con 9 observaciones conserva la memoria.
      **Coste**: 1 d.

### `task_cv_33` — La guía de ejecución se genera en el dispatch y `merge` absorbe capacidades (F-03)

- [x] _(hecho 2026-09-02, tests en verde; `resolve_agent_persona(agent, tool_slugs=)` retira la guía horneada y añade la de las tools efectivas (`strip_execution_guidance` / `with_execution_guidance`), el dispatch la pide con `agent_tool_names`, y `POST /agents/{fork_id}/merge` acepta `capabilities: ["tools","skills"]`; los seeds siguen horneando la guía para la UI, el dispatch la sustituye)_ **Título**: la guía se hornea en `agents.system_prompt` al sembrar, las copias la heredan
      congelada y las migraciones cambian tools sin tocar texto. `resolve_agent_persona` recibe
      las tools efectivas y añade `execution_guidance`; `POST /agents/{fork_id}/merge` acepta
      `capabilities: ["tools","skills"]`.
      **Test**: copia con tools distintas a su built-in → guía coherente con sus tools.
      **Coste**: 1,5 d.

### `task_cv_34` — Comandos base de sólo lectura para todos los proveedores (F-02)

- [x] _(hecho 2026-09-02, tests en verde; los proveedores finos reciben el subconjunto de sólo lectura de la base del SDK (`ls cat grep find head tail wc`), nunca la mitad que escribe)_ **Título**: la guía promete `grep/ls/cat` por `shell_exec` y sólo `claude_sdk` recibe la
      base: en Ollama/Copilot/Azure cada `ls` es «command not allowed». Unir un subconjunto de
      sólo lectura (`ls cat grep find head tail wc`) para todos los kinds, o hacer la guía
      consciente del kind.
      **Test**: spec `ollama` + `shell_exec("ls")` → permitido.
      **Coste**: 0,5 d.

### `task_cv_35` — Textos contradictorios y `docs_language` (F-04, F-05, F-06)

- [x] _(hecho 2026-09-02, tests en verde; las cuatro cadenas reescritas y vigiladas por `test_the_prompts_no_longer_promise_git_or_mv_inside_the_sandbox`; `resolve_agent_persona(agent, language=)` elige el prompt EN y el dispatch lo pide desde `repository_config.docs_language` porque no existe columna `docs_language` en `projects`; derivar `shell-exec` del rol (`ci4-tech-writer`) queda para `task_cv_33`)_ **Título**: `_CI4_STACK_HYGIENE` («donde ya está el repo»), `SHELL_ONLY_*` («git log/diff»),
      la descripción de `shell-exec` («mv») y `_DECIDE_SYSTEM` («a git worktree») contradicen el
      ADR 0163 y la retirada de `mv`; el prompt EN no llega nunca al modelo. Reescribir las cuatro
      cadenas, aserción en `test_builtin_prompt_tool_coherence`, y pasar `project.docs_language`
      al dispatch. Derivar `shell-exec` del rol (`ci4-tech-writer`).
      **Test**: `grep` de las cadenas → 0; `resolve_agent_persona(agent, language="en")`.
      **Coste**: 1 d.

### `task_cv_36` — El refresco de arranque cubre plantillas y avisa del corpus rancio (F-07, F-08)

- [x] _(hecho 2026-09-02, tests en verde; el refresco de arranque aplica también políticas, plantillas de proyecto (built-in y CI4) y plantillas de agente humano, y avisa con WARNING del corpus de KB rancio (`warn_stale_catalog_corpus`); `RuntimeTemplate.toolchains` + `foreign_commands` cruzan las allowlists, y `webapp`, `legacy-migration` y `devops-bootstrap` dejan de prometer comandos que su runtime no trae)_ **Título**: `startup.py` no refresca `project_templates`, `human_agent_templates`,
      `approval_policies` ni el corpus de skills (la skill CI4 reescrita el 2026-09-01 no llega a
      ninguna instalación sin CLI). Añadir los upserts; WARNING con `corpus_hash` desactualizado;
      test que cruce `allowed_commands` de cada plantilla con los binarios de su runtime
      (`devops-bootstrap` promete `docker/terraform/ansible` sobre `python-pytest`).
      **Test**: `test_builtin_capabilities_no_se_quedan_rancias` extendido; el cruce falla hoy.
      **Coste**: 1 d.

---

## Ola 4 — Operación (P2 · ~7 d)

### `task_cv_40` — Presupuestos que se aplican antes del gasto (D-05, D-06, D-07)

- [x] **Título**: el wall-clock sólo se mira al inicio de `plan` (una llamada puede rebasarlo 45
      min y el worker mata antes); `max_cost_usd` es 0 en tres de cuatro proveedores; un fallo del
      motor de guardrails deja un proyecto con reglas `block` corriendo sin ninguna. Pasar el
      restante como `timeout` a `_run_with_retry`/`stack_exec`; estimar coste con precios del
      catálogo cuando el proveedor devuelva 0; abortar (`guardrails_unavailable`) si el spec trae
      `block` y el pipeline no arranca.
      _Cerrada el 2026-09-02_: `run_agent` ata el restante del wall-clock al cliente del
      proveedor (`bind_deadline`) y cada llamada viaja con ese `timeout` (suelo 5 s); el worker
      adjunta `model.prices` (USD/1M tokens, del catálogo `model_prices`) y el tracker estima el
      coste de las llamadas que llegan a 0 (`cost_estimated_calls` en el envelope), con lo que
      `max_cost_usd` tripa en los cuatro proveedores; con reglas `block` y sin motor el run
      aborta `guardrails_unavailable` antes de la primera llamada (ADR 0102, addendum). Tests:
      `test_budgets_before_spend.py`, `test_provider_retries.py`, `test_budget_envelope_step.py`,
      `test_el_spec_lleva_los_precios_del_catalogo.py`.
      **Coste**: 1,5 d.

### `task_cv_41` — Escaladas visibles y despacho de review que respeta pausa y proyecto (C-05, C-06, C-07)

- [x] **Título**: las tres escaladas del bucle AI-reviewer no emiten notificación ni salen en el
      panel; `_on_task_in_review` esquiva `budget_pause_block` y `status == active`; el reviewer
      puede acabar siendo el implementador cuando no hay preset. Emitir `task_blocked`, panel con
      `blocked` + `review_comment.escalated`, y excluir `reviewer_agent_id` del pool.
      **Coste**: 1 d.
      _Cerrada el 2026-09-02_: `_notify_execution_outcome` recibe el estado final de la tarea y
      emite `task_blocked` (razón = `abort_code` o `escalated`) cuando queda bloqueada;
      `_on_task_in_review` aplica las mismas guardas que el despacho de implementación
      (proyecto `active`, sin `budget_pause_block`); `_pick` retira `reviewer_agent_id` del
      pool; el panel de la tarea muestra la escalada del último `review_comment`
      (`task-review-criteria.tsx`). Tests: `test_execution_outcome_notify.py`,
      `test_assignment_policies.py`, `test_in_review_dispatch.py`, `task-review-criteria.test.tsx`.

### `task_cv_42` — Beat con instancia única y poda que respeta ejecuciones vivas (G-05, G-06, G-04)

- [x] **Título**: ninguna tarea beat tiene `expires` ni lock (`acks_late` las reentrega: dos backups
      con dos quiesces); la poda hace `unlock` + `remove --force` confiando sólo en el mtime del
      directorio (dependencia no fijada por ningún test: hoy la refresca el ocultado del `.git`);
      el worktree `plan-docs-*` nunca se retira. `expires` + `SET NX` en las tareas con efectos en
      disco; `executions.status='running'` → `keep`; no soltar un lock cuyo `execution_id` esté
      `running`; `_remove_worktree` en el `finally` de `write_plan_docs_to_branch`. Actualizar ADR 0163 §5.
      _Cerrada el 2026-09-02_: las seis tareas con efecto en disco (poda de worktrees, dep-cache,
      housekeeping de git, purga de soft-borrados, backup, sweeper) llevan `expires` en su entrada
      del beat y el cerrojo `SET NX EX` de `workers.maintenance.singleton` (sin Redis corren igual
      y lo registran); la política de poda lee `executions.status` y un worktree con run `running`
      es `keep` aunque el plan esté cerrado (con ello no se suelta su lock); `write_plan_docs_to_branch`
      retira el worktree de docs en un `finally` (`WorktreeManager.remove`). ADR 0163 con addendum
      del 2026-09-02. Tests: `test_el_mantenimiento_respeta_las_ejecuciones_vivas.py`,
      `test_plan_closure_docs.py::test_the_docs_worktree_is_removed_after_the_push`.
      **Coste**: 1,5 d.

### `task_cv_43` — Timeouts de git remoto, DLQ con lector y quiesce que no deja facturando (G-07, A-09, G-08)

- [x] **Título**: `_run_git` fija 120 s también para push/fetch de remoto (un repo grande nunca
      cierra); `dlq:executions` no tiene lector; el quiesce nocturno mata el worker y deja al
      agent-runtime facturando hasta 6-7 h. `timeout` parametrizable (`WORKERS_GIT_REMOTE_TIMEOUT_S`);
      métrica `agentic_dlq_depth` + endpoint System Admin; señal `worker_shutting_down` que mata
      los contenedores del worker y sella las filas (`failed:quiesced`).
      **Coste**: 1,5 d.
      _Cerrada el 2026-09-02_: `WORKERS_GIT_REMOTE_TIMEOUT_S` (300 s por defecto) acota
      fetch/push/pull/ls-remote/clone en `_run_git`, lo local sigue a 120 s y el vencimiento es un
      `GitCommandError` (`test_el_git_remoto_tiene_timeout.py`); `dlq:executions` entra en
      `agentic_dlq_depth` y `GET /admin/dead-letters` (System Admin) enseña profundidad y últimas
      entradas de cada DLQ; cada contenedor lleva la etiqueta `com.agentic-platform.worker` y al
      recibir `worker_shutting_down` el worker mata SUS agent-runtimes y sella sus filas
      (`failed`, `abort_code=quiesced`, `workers.quiesce`). Tests:
      `test_el_quiesce_no_deja_facturando.py`, `test_el_mantenimiento_respeta_las_ejecuciones_vivas.py`.

### `task_cv_44` — Imágenes del tenant por digest y con allowlist (B-05, B-09)

- [x] **Título**: `runtime_services.py` acepta cualquier `host/repo:tag` y `version` deshace el pin
      por digest de los sidecars; `agent-runtime:v1` y `browser-runtime:v1` no se publican ni
      fijan por digest (ADR 0148 sólo cubrió las 14 plantillas). Exigir `@sha256:` o allowlist
      de registries; mapa versión→digest; añadir las dos imágenes al pipeline de release.
      **Coste**: 1,5 d.
      _Cerrada el 2026-09-02_: una `image:`/`runtime_image` del tenant lleva `@sha256:` o viene
      de `WORKERS_TENANT_IMAGE_REGISTRY_ALLOWLIST` (por segmentos enteros; vacía = sólo digest);
      `version` resuelve contra `pinned_versions()` del catálogo y una versión no pineada se
      rechaza nombrando las que hay; `agent-runtime` y `browser-runtime` se construyen y escanean
      en `release-images.yml` (job `runtimes`), entran en `PLATFORM_APPS` y el compose del
      instalador se las pasa al worker por `WORKERS_*_RUNTIME_IMAGE`. ADR 0148 con addendum.
      Tests: `test_runtime_services.py`, `test_platform_images_wiring.py`.

### `task_cv_45` — Restos de menor riesgo

- [x] **Título**: `timeout -k` en el wrapper de `exec_run` (B-08); validar `worktree_host_path`
      bajo `data_root` en los consumidores (B-10); `args` capados en la observación (D-08); techo
      de `ask_human` por task (D-09); lote read-only que anuncia el elemento expulsado (D-10);
      `plan_retro` por la persistencia común e idempotente por tag (E-06, G-12); memorizer humano
      con causa y racha (E-07); log/métrica del embedding fallido (E-10); detector de secretos
      antes de persistir memorias (E-11); `restore_reconcile` sin CRITICAL falso (G-09); evento
      `git_credential_failed` (G-10); watchdog que re-resuelve contenedores (G-11); retirar
      `direct_to_default_allowed`/`plan_validation_mode` o cablearlos (G-03).
      **Coste**: 2 d (sumados).
      _Cerrada el 2026-09-02_ en dos tandas. Operación: `timeout -k 10` (B-08);
      `workers.host_paths.ensure_under_data_root` en review/test-runtime/run_cycle (B-10);
      `agentic_recall_embedding_failures_total` (E-10); `approved` sin rama es WARNING (G-09); el
      watchdog re-resuelve por etiqueta tras un recreate (G-11); `plan_retro` por
      `persist_memory_candidates` e idempotente por tag en BD, 30 días de ventana (E-06, G-12).
      Modelo y memoria: `args` capados en la observación y en la línea condensada (D-08);
      `ASK_HUMAN_MAX_PER_TASK=5` → `ask_human_remaining` en el spec y noop visible al agotarse
      (D-09); `review(tool, args)` por elemento del lote y `batch_dropped` en la observación
      (D-10); memorizer humano por `_select_distiller` con causa y racha (E-07);
      `sanitize_memory_content` antes de embeber/persistir (E-11); evento `git_credential_failed`
      throttled desde el auto-PR y la sonda de fetch (G-10); `direct_to_default_allowed` retirada
      —`plan_validation_mode=auto_approve` sí estaba cableado— (G-03; ADR 0072 addendum). Tests:
      `test_los_restos_operativos_de_menor_riesgo.py`, `test_los_restos_de_memoria_y_git.py`,
      runtime `test_restos_del_modelo.py`.

---

## Tests humanos

| ID            | Qué valida                                                                                             |
| ------------- | ------------------------------------------------------------------------------------------------------ |
| `human_cv_01` | Una tarea con criterios automáticos muestra al reviewer un `<test-report>` real, no de infraestructura |
| `human_cv_02` | Un proyecto creado por API sin política, con una acción `http_post`, aparece en la bandeja humana      |
| `human_cv_03` | Adoptar `full-stack-web` y lanzar un plan: ninguna ejecución aborta `model_unresolved`                 |
| `human_cv_04` | Rechazar dos veces una tarea con tareas hermanas activas: el reviewer ve sólo el diff de la tarea      |

## Riesgos del plan

- `task_cv_13` (`claim_id`) toca el contrato orchestrator↔worker: desplegar worker antes que
  orchestrator y aceptar mensajes sin `claim_id` durante una versión.
- `task_cv_20` cambia cómo el runtime recibe el spec: la imagen `agent-runtime` debe
  reconstruirse y desplegarse antes que el worker.
- `task_cv_25` multiplica redes efímeras: el reaper ya las limpia, pero conviene medir el coste
  en hosts con muchas ejecuciones concurrentes.
