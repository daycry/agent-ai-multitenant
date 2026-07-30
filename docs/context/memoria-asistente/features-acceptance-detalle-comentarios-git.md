---
name: features-acceptance-detalle-comentarios-git
description: 4 features (acceptance_criteria del planner + detalle de tarea UI + comentarios→prompt + git limpio en sandbox) IMPLEMENTADAS+DESPLEGADAS en dev
metadata:
  node_type: memory
  type: project
  originSessionId: 9b6ffa32-bda3-49a0-a5ed-708c0fca5208
---

2026-06-30, rama `plan/runs-visor-trabajo` (sin push/PR — merge = decisión del operador). 4 features
entregadas, suite verde (2060 unit+agent-runtime, 15 integración), y DESPLEGADAS en dev
(api-server:ci+:manuals, workers:ci, orchestrator:manuals, agent-runtime:v1, admin-panel:manuals, todo
WITH_CLAUDE=1; 5 servicios recreados y `healthy`).

- **A — planner genera `acceptance_criteria`** (convergencia): `planning_llm.py` `pm_plan_draft` pide 2-5
  criterios DESCRIPTIVOS y verificables por tarea (NO comandos — decisión del operador) + parser
  `_clean_acceptance_criteria` (trim, aplana `{description}`, cap 8/300). **Verificado e2e contra
  claude_sdk/opus en el proyecto demo CI4 (`019f1384-311d…` "Api CI"): 6/6 tareas con criterios,
  incluida "Auditar dependencias" (la que siempre bloqueaba).** Antes TODAS las tareas nacían con
  `acceptance_criteria=[]` → over-verificación → `repetitive_loop_detected` → blocked.
- **B — detalle de tarea en Kanban**: `components/tasks/task-detail-sheet.tsx` (info+criterios+deps +
  runs `listRuns({task_id})` + comentarios). Cableado en los dos Kanban (board + projects/[id]/tasks).
  `to_task_response` ya exponía `plan_id`+`inputs` (spec id en `inputs[plan_task_spec_id]`). QA visual
  pendiente del operador.
- **C — comentarios → prompt** (rail único, hace NO-INERTES los `PlanComment` de tarea Y de plan):
  `dispatch._read_relevant_comments` (target_kind task con target_ref=spec id, o plan) →
  `request["task_comments"]` → `ExecutionRequest.task_comments` → `spec` → agent-runtime
  `build_comments_preamble` → preámbulo. Reusa `PlanComment` (sin tabla nueva). Ver
  [[provider-resolution-two-paths]].
- **D — git limpio en sandbox**: quitado `git` de `_SDK_BASE_SHELL_COMMANDS` (daba exit 128 críptico:
  gitdir→bare no montado) + nota en `_DECIDE_SYSTEM` ("la plataforma persiste tus cambios; no ejecutes
  git"). El agente nunca comitea (lo hace el worker; principio 2).

**Addendum 2026-06-30 (commit `706ffa0`, misma rama, DESPLEGADO):** editor de `acceptance_criteria`
en el `TaskDetailSheet` (antes solo-lectura). Lógica pura en `apps/admin-panel/lib/acceptance-criteria.ts`
(`criterionText` + `cleanCriteria`: recorta/descarta vacíos/capa 8·300/preserva criterios dict como
`{...original, description}`); UI lista de filas (Input + ×, Añadir, Guardar/Cancelar) → `PUT
/projects/{pid}/tasks/{tid}` (ya aceptaba `acceptance_criteria`; re-sync idempotente no pisa). Solo
frontend, sin backend/migración. Tests: 10 vitest + 2 e2e Playwright (mockear al **origen API
:8001**, no `**/projects/...`, que también intercepta la navegación `:3000/admin/projects/...`).

Follow-ups FUERA de alcance (no empezar sin confirmar): `commit_failed` robusto (escalar a humano; la
carrera non-ff ya está en `ef6b42f`); checks automatizados de criterios por rol QA; comentarios de plan
en el prompt de PLANNING (este los inyecta solo en EJECUCIÓN). Ver [[estado-trabajo-en-curso]],
[[auditoria-runs-remediacion]], [[runs-no-convergen-causas-estructurales]].

**Addendum 2026-07-01 (misma rama `plan/runs-visor-trabajo`, sin commit/push aún — merge = decisión del
operador): GENERACIÓN de `acceptance_criteria` por IA + backfill.** El planner ya genera criterios para
planes NUEVOS (Feature A), pero las tareas LEGADO nacían con `[]`. Entregado:

- **Servicio** `apps/api-server/src/api_server/chat/criteria_llm.py`: `build_criteria_messages` +
  `generate_task_acceptance_criteria` (reutiliza `planning_llm._clean_acceptance_criteria`, cap 8/300;
  parser robusto `_first_json_value` con `json.JSONDecoder().raw_decode` — tolera prosa/fences/llaves al
  final, arregla el greedy `{.*}`). NO persiste.
- **Endpoint** `POST /projects/{pid}/tasks/{tid}/generate-acceptance-criteria` (`routers/tasks.py`) →
  `GeneratedAcceptanceCriteria`. Resuelve provider con `resolve_chat_model_config` + `_resolve_chat_provider`
  (chat model, herencia ADR 0065), **409** si no hay provider, **404 cross-tenant**, `aclose` en finally,
  **no escribe** (guarda el `PUT` existente tras confirmación).
- **UI** (`components/tasks/task-detail-sheet.tsx`): botón "Generar/Regenerar con IA" en `CriteriaSection`
  → vacía = precarga el editor; con criterios = **modal comparativo** (`CriteriaCompareDialog`) actuales
  vs propuestos → Aceptar (funnel al editor + Guardar explícito) / Cancelar (sin PUT). NUNCA machaca sin
  confirmación (decisión del operador).
- **Fix compartido** `components/ui/dialog.tsx`: pila `openDialogStack` — solo el diálogo SUPERIOR
  responde a `Escape` (antes Escape cerraba el modal anidado Y el sheet padre).
- **Backfill** `scripts/dev/backfill_acceptance_criteria.py` (sesión admin BYPASSRLS, reutiliza el
  servicio, try/except por-tarea para no abortar el lote). **Ejecutado en "Api CI" (`019f1384-311d`,
  tenant c5e446e7): 14/14 tareas con criterios (media 6.4), verificado en BD.** Correr DESDE PowerShell
  (`docker cp`/`docker exec` con `/tmp/...` — MSYS mangla la ruta en Git Bash, misma familia que el
  gotcha `/api`).
- Tests: 9 vitest-equiv unit (`test_criteria_llm.py`) + 3 integración (`test_generate_acceptance_criteria.py`)
  - 5 e2e Playwright (`task-criteria-generate.spec.ts`, incl. Escape). Review adversarial (workflow, 3
    hallazgos low) → los 3 corregidos. DESPLEGADO en dev (api-server:manuals + admin-panel:manuals
    recreados, healthy).
- **UI "Depende de" → título, no UUID** (`task-detail-sheet.tsx` `DependsOnSection`): resuelve los
  UUIDs de `depends_on` contra `GET /projects/{pid}/tasks` (query `["project-task-titles",pid]`, solo si
  hay deps) → muestra el título; fallback a id corto. Solo frontend, e2e `task-deps-titles.spec.ts`,
  DESPLEGADO. **Validado en vivo**: el backfill funciona — la tarea "Definir contrato de respuesta JSON"
  (019f1399-34e2) convergió a `done` en 4+2 iteraciones usando los criterios generados (antes abortaba a
  50 iter `max_iterations_exceeded`).

**Fix infra stack_exec: dep-cache no escribible (2026-07-01, DESPLEGADO).** La tarea "Auditar
dependencias" (019f1399-34f5) bucleaba (`repetitive_loop_detected`×3) porque `composer audit` emitía
_"Cannot create cache directory /root/.composer/cache… not writable"_ en cada llamada y el agente oscilaba
con `--no-cache`. Causa: `shared_test_runtimes.dep_cache.ensure_entry` crea el dir como **root** (worker)
pero el runtime-template corre **no-root** (`isolation.AGENT_UID_GID=1000:1000`) → bind-mount root-owned no
escribible. Fix: `os.chmod(host_path, 0o777)` en `ensure_entry` (re-chmod idempotente, sana dirs viejos).
TDD (test_dep_cache_persist.py, spy a `os.chmod`). Deploy: `shared_test_runtimes` vive en `api-server:ci`
→ rebuild `api-server:ci` + `workers:ci` (FROM base) + recrear workers. **Secundarios del mismo run
(pendientes):** `git status` vía stack_exec da exit -1 (bare-repo no montado; el agente no debería usar
git); ejecución zombie 019f1d60 quedó `running` (no pude limpiarla, clasificador de permisos bloqueó la
UPDATE directa). Recuerda: `docker cp`/`docker exec` con paths `/tmp/...` DESDE PowerShell (MSYS los
mangla en Git Bash).

**GAP del generador de criterios (2026-07-01, diagnóstico multi-agente):** `criteria_llm.build_criteria_messages`
es **ciego al contexto de hermanos** — solo ve título+descripción de LA tarea + nombre/descr del proyecto,
NO el contrato ni los criterios de tareas hermanas. Efecto: en el proyecto CI4, la tarea "Implementar
controladores" (019f1399-34e9) recibió un crit #3 "formato ResponseTrait" que CONTRADICE el contrato
`{message,meta}` (§2.2) al que remiten sus crit #1/#2 → self-review autoritativo (ADR 0087) rechaza en
bucle → `repetitive_loop_detected` (ADR 0089-D4) → `needs_human_review`/blocked. Auditados los 84 criterios
(workflow): 19 reescrituras verificadas aplicadas (1 blocking ResponseTrait + 5 high + 9 medium + 4 low;
patrones: format-contradiction, over-strict-runtime "el build falla si…"→"el YAML declara el paso X sin
continue-on-error", wording subjetivo→objetivo). Backup en scratchpad/criteria_backup.json.
**VALIDADO end-to-end (2026-07-01 15:xx): tras corregir los criterios, relancé 34e9 moviéndola a `ready`
vía la API (mint JWT del tenant_admin demo dentro del contenedor api-server + `PUT /projects/{pid}/tasks/{tid}`
`{"status":"ready"}` → el orquestador despacha; el trigger de dispatch es status=`ready`) y CONVERGIÓ:
implementador `done` (10 iter, {message,meta}) + reviewer `done`/approve (5 iter) → tarea `done`, ~$0.31,
sin bucle.** Relanzar una tarea `blocked` = moverla a `ready` (deps done) por la API/Kanban. **Follow-ups
sistémicos (no hechos): (1)** alimentar al generador con contexto de plan/hermanos (o el contrato) + un
chequeo de coherencia; **(2)** el pipeline debería escalar "self-review rechaza por la MISMA razón" con un
abort_code legible en vez del genérico `repetitive_loop_detected`. Ver [[runs-no-convergen-causas-estructurales]],
[[reviewer-ciego-convergencia-fix]], [[refactor-self-review-autoritativo]].

**FIX #45 (2026-07-01, DESPLEGADO) — hecho el follow-up (1):** `criteria_llm` gana
`format_sibling_context(siblings)` + param `sibling_context` en `build_criteria_messages`/
`generate_task_acceptance_criteria` (regla de COHERENCIA: no contradigas a las hermanas; si remites a "el
contrato acordado", respétalo). Cableado en endpoint (`routers/tasks._plan_sibling_context`, fetch
`Task.plan_id == self AND id != self`) + backfill (`_sibling_context`). TDD 13 unit + 1 integración
(threading). Deploy: rebuild `api-server:manuals` + recrear (suite unit 1906 verde).

**FIX #46 (2026-07-01, DESPLEGADO) — hecho el follow-up (2):** `abort_code` legible cuando el bucle salta
DENTRO de un ciclo de self-review. `graph._loop_trip_outcome(review_retries, last_review_feedback, tool)`:
si `review_retries>0` → nuevo `SafeguardCode.SELF_REVIEW_STALEMATE` (`"self_review_stalemate"`) + pone el
feedback del revisor en el `output` de escalado (en vez del opaco `repetitive_loop_detected`); fuera de
review sigue igual. Usado en el trip de `plan()`. TDD 3 unit (`test_loop_trip_stalemate.py`); suite
agent-runtime 170 verde. Deploy = rebuild imagen **efímera** `agent-runtime:v1` (WITH_CLAUDE=1); sin
contenedor que recrear (se lanza fresca por ejecución). **Todos los sistémicos del hilo cerrados.**
