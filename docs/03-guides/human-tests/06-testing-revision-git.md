# Plan 06 — tests humanos

Esta guía cubre los **12 tests humanos** del Plan 06 (Testing
heterogéneo, revisión y ciclo Git del plan). Cada `human_06_NN`
está agrupado dentro de uno de los **4 demos automatizados** que
seedean datos en un sandbox local, ejecutan el flujo contra git
real y reportan `[ OK ]` / `[FAIL]` por cada checkbox.

> **Estado del plan**: `pending_human_validation`. Las 50 tareas
> están `[x]` con ~250 tests automáticos en verde. Estos 12 tests
> humanos son el último paso antes de pasar a `completed`.

## TL;DR

```powershell
.\scripts\dev\run-human-tests-06.ps1
```

El launcher:

1. Limpia el sandbox previo (`scripts/.demo_06/`) y lo re-crea.
2. Ejecuta `setup_demo_06.py` — seedea dos bare repos "remotos"
   (`backend.git` + `frontend.git`) que simulan GitHub sin red.
3. Corre los 4 demos en orden — cada uno imprime sus `[ OK ]` /
   `[FAIL]` por checkbox.
4. Termina con un resumen `PASSED/FAILED` por demo.

**Esperado en consola**:

```
[ OK ] demo_setup  (exit 0)
[ OK ] demo_a  (exit 0)
[ OK ] demo_b  (exit 0)
[ OK ] demo_c  (exit 0)
[ OK ] demo_d  (exit 0)

Todos los demos PASSED. Puedes marcar los 12 human_06_* como pass.
```

## Pre-requisitos

| Requisito                                                                                  | Por qué                                                                       |
| ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| `git --version` ≥ 2.30                                                                     | `git worktree --detach` + `--initial-branch`, usado por los demos.            |
| `.venv` con `apps/api-server[dev]`, `apps/workers[dev]`, `apps/orchestrator[dev]` editable | El `PlanRunner` importa de los tres apps.                                     |
| `packages/shared-test-runtimes[dev]` editable                                              | El catálogo + dep-cache + parsers + test_report viven aquí.                   |
| Windows: NTFS con permisos para `chmod`                                                    | El sandbox elimina pack files de git que quedan read-only por defecto.        |
| **NO** requiere Docker daemon                                                              | Los aux services / DinD proxy quedan stubeados; el spin-up real es Plan 06.5. |
| **NO** requiere el stack docker compose                                                    | Los demos son in-process: usan `InMemoryTaskStore` + git en disco.            |

> El launcher detecta `.venv\Scripts\python.exe` automáticamente. Si
> no existe, falla con exit 2 indicando que actives el venv primero.

## Opciones del launcher

| Modo                            | Comando                                                                 |
| ------------------------------- | ----------------------------------------------------------------------- |
| Todos los demos                 | `.\scripts\dev\run-human-tests-06.ps1`                                  |
| Solo setup (re-siembra sandbox) | `.\.venv\Scripts\python.exe scripts\demos\setup_demo_06.py`             |
| Solo un demo concreto           | `.\.venv\Scripts\python.exe scripts\demos\demo_human_06_<a/b/c/d>_*.py` |

No hay flags `-Only` / `-SkipSetup` como en Plan 05 — el launcher es
intencionalmente lineal porque los demos son rápidos (~45s total).

## Qué siembra `setup_demo_06.py`

```
scripts/.demo_06/
├── remote/                          # "GitHub" simulado
│   ├── backend.git/                 # bare seedeado, branch main + 1 commit
│   └── frontend.git/                # bare seedeado, branch main + 1 commit
└── data/                            # data_root del PlanRunner
    └── (vacío hasta que un demo lo poblé)
scripts/demos/.demo_state_06.json          # paths + slugs que los demos leen
```

Los demos siguientes construyen `projects/<tenant>/<project>/repos/` bajo
`data/` con sus propios bare repos (que apuntan via `origin` a los del
sandbox `remote/`).

> Re-ejecutar el launcher es **destructivo** para el sandbox: borra
> `.demo_06/` y `.demo_state_06.json`. Esos paths están gitignored.

---

## Mapa rápido test humano → demo

| Test humano   | Demo     | Cubierto                                                                         |
| ------------- | -------- | -------------------------------------------------------------------------------- |
| `human_06_01` | `demo_a` | end-to-end pipeline con commits + push + transición a `pending_human_validation` |
| `human_06_02` | `demo_b` | dep-cache hash determinista + invalidación por cambio de lock                    |
| `human_06_03` | `demo_b` | aux services per-task isolation (paths/aliases distintos)                        |
| `human_06_04` | `demo_d` | review-runtime spawn + URL firmada HMAC + rerun flag                             |
| `human_06_05` | `demo_b` | múltiples repos por plan, cada uno con su rama                                   |
| `human_06_06` | `demo_a` | `git fsck --full` verde tras todas las operaciones                               |
| `human_06_07` | `demo_c` | pool min/max/eviction + role-switch in-place                                     |
| `human_06_08` | `demo_c` | matriz `branch_push_mode × plan_validation_mode`                                 |
| `human_06_09` | `demo_a` | conflicto entre tareas paralelas (parcial — full en 06.5)                        |
| `human_06_10` | `demo_d` | escalado a `awaiting_human` tras 3 rechazos + 4 acciones                         |
| `human_06_11` | `demo_d` | checkbox fail → tarea plan-scoped en backlog                                     |
| `human_06_12` | `demo_d` | audit trail cronológico append-only                                              |

---

## `human_06_01` — Ciclo end-to-end con repo Git

**Cubierto por**: `demo_a`.

**Qué prueba**:

1. `make_plan_branch_name()` genera `plan/{id_short}-{slug}`.
2. El worktree del primer task crea la rama en el bare.
3. Cada commit lleva los trailers `Plan-Id` / `Task-Id` /
   `Execution-Id` / `Generated-By`.
4. `push_review_to_bare` mueve el commit del worktree al bare.
5. `push_branch_to_remote` (incremental) lo manda al remote.
6. Antes de cada task posterior, `sync_to_head` hace `fetch + reset
--hard FETCH_HEAD` para traer commits de siblings.
7. `transition_to_pending_human_validation` dispara cuando todos
   los tasks están `done`.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\setup_demo_06.py
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_a_endtoend.py
```

**Output esperado** (resumido):

```
[ OK ] bare_repo:backend
[ OK ] task:<id> — title='add login endpoint'
[ OK ] task:<id> — title='add login service'
[ OK ] task:<id> — title='add login view'
[ OK ] execute:<id> — sha=<8 hex> role=implementador
[ OK ] execute:<id> — sha=<8 hex> role=implementador
[ OK ] execute:<id> — sha=<8 hex> role=implementador
[ OK ] plan:transition_to_review — ok
[ OK ] git fsck --full passes
  3/3 tareas done
[ OK ] plan:shutdown
demo human_06_a PASSED
```

**Verificación visual adicional** (no automatizada, opcional):

```powershell
cd .\scripts\.demo_06\data\projects\demo-tenant\demo-project\repos\backend.git
git log --format='%H %s%n%(trailers:key=Plan-Id)%(trailers:key=Task-Id)' refs/heads/plan/* | Select-Object -First 30
```

Debes ver los trailers `Plan-Id:` y `Task-Id:` en cada commit.

```powershell
cd .\scripts\.demo_06\remote\backend.git
git branch -a
# Debe listar 'plan/11111111-end-to-end'
```

**Checklist pass/fail** (los 12 items del roadmap):

- [ ] Al sincronizar, se crea rama `plan/{id}-{slug}` en el repo
- [ ] Cada tarea hace su commit con trailers correctos
- [ ] Los tests `phpunit` se ejecutan en `test-runtime php-phpunit` _(Plan 06.5 — el spin-up real)_
- [ ] TestReport canónico se entrega al agente revisor con failures parseados _(Plan 06.5)_
- [ ] Tras aprobación de revisión automática de cada tarea, el commit se pushea al bare repo local
- [ ] Con `branch_push_mode=incremental`, cada tarea aprobada se pushea también al remoto
- [ ] Antes de arrancar cada tarea posterior, el sistema hace `fetch + reset --hard` al HEAD vigente
- [ ] Al completar todas las tareas, plan pasa a `pending_human_validation`
- [ ] review-runtime se levanta con stack servido + DB efímera _(Plan 06.5)_
- [ ] Humano accede vía URL temporal y prueba la app _(Plan 06.5)_
- [ ] Humano marca tests humanos del plan como pass
- [ ] Plan pasa a `completed`, sistema abre PR contra main _(Plan 06.5)_

**Pitfalls conocidos**:

- **"destination path 'backend.seed' already exists"** durante setup
  → Una corrida previa dejó residuos read-only en `.demo_06/`. El
  `_force_rmtree()` del setup ya lo maneja, pero si insiste:
  `Remove-Item -Recurse -Force scripts/.demo_06` manual.
- **"git clone failed (rc=128)"** sin más mensaje → falta heredar
  `os.environ` en el subprocess (git necesita `SYSTEMROOT` /
  `USERPROFILE` en Windows). El demo ya lo hace; si lo replicas en
  un script tuyo, copia la receta de `_git()` en `scripts/demos/setup_demo_06.py`.

---

## `human_06_02` — Caché de dependencias

**Cubierto por**: `demo_b`.

**Qué prueba**:

1. `compute_lock_hash(workspace, "python-pytest")` es determinista
   sobre el mismo contenido y SHA-256 (64 chars hex).
2. Cambiar `requirements.txt` produce un hash distinto.
3. `DepCacheManager.ensure_entry()` crea `pip-{hash}/` idempotente.
4. `invalidate(template, lock_hash=…)` borra solo ese entry.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_b_cache_aux.py
```

**Output esperado**:

```
human_06_02 - dep-cache (hash + invalidacion por lock)
[ OK ] hash deterministico - 8cecb8b4...
[ OK ] cache dir creado - pip-8cecb8b4...
[ OK ] lock changed -> hash diferente - 8cecb8b4 -> 88b8248c
[ OK ] invalidate quita un entry - pip-8cecb8b4...
```

**Checklist pass/fail**:

- [ ] La primera vez, `npm ci / composer install / pip install` tarda lo normal _(Plan 06.5 — instalación real)_
- [ ] La segunda vez, el dep-cache se monta y los tests arrancan en segundos _(Plan 06.5)_
- [ ] Cambiar el lock file invalida el caché y reinstala — **cubierto por el demo**

**Pitfalls**:

- En Windows, `write_text("hello\n")` puede convertirse en `hello\r\n`
  según el `newline` del open. Los tests + el demo usan `write_bytes`
  cuando piden un hash exacto.

---

## `human_06_03` — Aislamiento de servicios auxiliares

**Cubierto por**: `demo_b`.

**Qué prueba**:

1. Dos `TestRuntimeSpec` con el mismo template apuntan a worktrees
   distintos (`/data/wt/task-a` vs `/data/wt/task-b`).
2. `DEFAULT_POSTGRES.resolved_alias()` es `postgres-test` (alias
   estable dentro del bridge).
3. `DEFAULT_REDIS.healthcheck_cmd` es `("redis-cli", "ping")`.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_b_cache_aux.py
# bloque "human_06_03 - aux services isolation per task"
```

**Output esperado**:

```
[ OK ] task-a y task-b reciben paths distintos
[ OK ] postgres-test alias estable dentro del bridge
[ OK ] redis healthcheck es redis-cli ping
```

**Checklist pass/fail**:

- [ ] Cada tarea recibe su propio `postgres-test` efímero _(spin-up real Plan 06.5)_
- [ ] Una tarea no ve datos de la otra _(spin-up real Plan 06.5)_
- [ ] Al terminar cada tarea, los servicios se destruyen sin dejar rastro _(spin-up real Plan 06.5)_

> **Nota**: el demo valida que el **spec** del aux service es
> correcto. El spin-up real de containers postgres-test/redis-test
> está cubierto por `tests/integration/test_aux_services.py` (con
> docker mockeado) y se ejecutará contra Docker real en Plan 06.5.

---

## `human_06_04` — Validación humana del plan funciona

**Cubierto por**: `demo_d`.

**Qué prueba**:

1. `ReviewRuntimeManager.create()` invoca el spawn factory y devuelve
   una `ReviewSession` con `expires_at ~ now + 48h`.
2. `sign_review_url()` produce una URL firmada con HMAC-SHA256.
3. `verify_review_url()` rechaza una firma alterada (`sig + "X"`).
4. `queue_rerun()` setea el flag `rerun_requested = True`.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_d_review_audit.py
# bloque "human_06_04 - review-runtime URL firmada"
```

**Output esperado**:

```
[ OK ] review-runtime spawned - <session_id>
[ OK ] spawn factory invocado 1 vez
[ OK ] expira_at ~48h en el futuro
[ OK ] URL firmada round-trip OK
[ OK ] URL con firma alterada rechazada
[ OK ] queue_rerun set the flag
```

**Verificación visual adicional**:

Arrancar el admin-panel y navegar a `/admin/review/<session_id>`
(el `session_id` aparece en el output del demo). La página renderiza
los 4 paneles (terminal / logs / botón rerun / checklist con los
items `human_06_01` y `human_06_02` cargados).

```powershell
cd apps\admin-panel
npm run dev
# Abre http://localhost:3000/admin/review/<session_id>
```

> El backend HTTP que sirve `/api/review/{id}` es Plan 06.5. Hoy el
> demo valida el contrato Python (spawn + URL + rerun flag); la UI
> renderiza pero las llamadas a `/api/review/*` quedan en estado
> de error visible (esperado).

**Checklist pass/fail**:

- [ ] review-runtime se levanta y URL accesible al revisor _(parcial — spawn validado; HTTP real Plan 06.5)_
- [ ] Terminal web permite hacer comandos dentro del contenedor _(Plan 06.5)_
- [ ] Re-ejecutar tests funciona desde el botón _(flag validado en demo; ejecución real Plan 06.5)_
- [ ] Tras checklist completo, verdict approved abre PR _(Plan 06.5)_
- [ ] Si rejected, contenedor sigue 4h y plan vuelve a `in_progress` _(Plan 06.5)_

---

## `human_06_05` — Múltiples repos por plan

**Cubierto por**: `demo_b`.

**Qué prueba**:

1. `ensure_repo("backend")` + `ensure_repo("frontend")` crean dos
   bare repos en la jerarquía del mismo proyecto.
2. Una task que toca `backend` y otra que toca `frontend` aterrizan
   cada una en su bare correspondiente.
3. Cada bare termina con su rama `plan/{id_short}-{slug}` creada.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_b_cache_aux.py
# bloque "human_06_05 - multiples repos por plan"
```

**Output esperado**:

```
[ OK ] bare_repo:backend  — path=...repos/backend.git
[ OK ] bare_repo:frontend — path=...repos/frontend.git
[ OK ] execute:<id> — sha=<...> role=implementador  (touch backend)
[ OK ] execute:<id> — sha=<...> role=implementador  (touch frontend)
[ OK ] backend bare tiene rama plan/
[ OK ] frontend bare tiene rama plan/
```

**Checklist pass/fail**:

- [ ] Se crea rama `plan/…` en AMBOS repos
- [ ] Los commits van al repo correcto según la tarea
- [ ] Al completar, se abren DOS PRs (uno por repo) _(Plan 06.5)_
- [ ] El plan no pasa a `completed` hasta que ambos PRs están en estado válido _(Plan 06.5)_

---

## `human_06_06` — Git worktrees no corrompen el bare repo

**Cubierto por**: `demo_a`.

**Qué prueba**: tras ejecutar 3 tareas con sus worktrees + commits +
pushes, `git fsck --full` sobre el bare reporta cero errores.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_a_endtoend.py
# bloque "human_06_06 - bare repo integrity"
```

**Output esperado**:

```
[ OK ] git fsck --full passes
```

**Verificación manual del git fsck** (si quieres verlo con tus propios ojos):

```powershell
cd .\scripts\.demo_06\data\projects\demo-tenant\demo-project\repos\backend.git
git fsck --full --strict
# Debe imprimir solo "Checking object directories" y salir 0
```

**Checklist pass/fail**:

- [ ] Los worktrees se crean y destruyen sin errores
- [ ] `git fsck` en cada bare repo al final pasa sin warnings
- [ ] Ningún worktree huérfano queda tras los 30 días _(test integration `test_worktree_cleanup.py` cubre el TTL; el demo no espera 30 días reales — usa `now=` override)_

---

## `human_06_07` — Pool elástico de runtime por plan

**Cubierto por**: `demo_c`.

**Qué prueba**:

1. `pool.start()` arranca exactamente `min` containers.
2. 3 acquires paralelos hacen crecer el pool a `max=3`.
3. El mismo container sirve a 4 roles distintos
   (implementador → reviewer → memorizer → technical_writer) sin
   destrucción/respawn.
4. `sweep_idle(now=future)` elimina containers por encima de `min`.
5. Métricas (`size`, `busy`, `idle`, `evictions_total`,
   `role_executions_total`) reflejan la realidad.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_c_pool_policies.py
# bloque "human_06_07 - pool elastico por plan"
```

**Output esperado**:

```
[ OK ] pool arranca con min=1 container
[ OK ] pool crece a max=3 con 3 acquires paralelos
[ OK ] los 3 slots estan busy
[ OK ] post-release todos idle
[ OK ] mismo container sirve 4 roles distintos sin reinicio - {'c-2'}
[ OK ] sweep_idle elimina 2 (min=1 sobrevive) - 2
[ OK ] pool vuelve a tamano min=1
[ OK ] evictions_total counter en 2
[ OK ] role_executions_total acumula >=7 invocaciones - {...}
```

**Checklist pass/fail**:

- [ ] Al iniciar el plan el pool arranca con `min` contenedores (default 1)
- [ ] Al ejecutarse pasos paralelos, el pool crece hasta `max`
- [ ] Cuando un paso termina y empieza la revisión, se reutiliza el mismo contenedor
- [ ] Tras periodos de inactividad superiores a `idle_ttl_seconds`, contenedores por encima de `min` se destruyen
- [ ] Las métricas `runtime_pool_*` exportadas a Prometheus reflejan el comportamiento _(export Prometheus Plan 06.5)_
- [ ] Al cerrar el plan, todos los contenedores del pool se destruyen limpiamente

---

## `human_06_08` — Matriz `branch_push_mode × plan_validation_mode`

**Cubierto por**: `demo_c` + tests integration `test_git_policies_matrix.py`.

**Qué prueba**: las 4 combinaciones de los dos ejes construyen
`PlanGitPolicies` válidos sin error. Las 12 combinaciones totales
(con `push_policy`) están testeadas en
`tests/integration/test_git_policies_matrix.py` con un parametrize
por matriz.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_c_pool_policies.py
# bloque "human_06_08 - matriz 4 combinaciones"
```

**Output esperado**:

```
[ OK ] incremental + human_required - rama visible en remoto desde 1a tarea, humano valida
[ OK ] incremental + auto_approve - rama en vivo, plan se cierra sin paso humano
[ OK ] final_only + human_required - rama no aparece hasta cierre, humano valida
[ OK ] final_only + auto_approve - rama aparece de golpe, sin paso humano
```

**Checklist pass/fail**:

- [ ] `incremental + human_required` (default): rama visible en remoto desde la 1ª tarea, humano valida al final
- [ ] `incremental + auto_approve`: rama visible en vivo, plan se cierra y abre PR sin paso humano
- [ ] `final_only + human_required`: rama no aparece en remoto hasta el cierre, humano valida
- [ ] `final_only + auto_approve`: rama aparece de golpe en remoto al cierre, sin paso humano
- [ ] `push_policy` aplica correctamente en cada caso _(parcial — los 3 outcomes validados en `test_push_policy.py`; merge real Plan 06.5)_

---

## `human_06_09` — Conflicto entre tareas paralelas (parcial)

**Cubierto por**: `demo_a` (skip explícito) + `test_worktree_sync.py` (parcial).

**Qué prueba en Plan 06**: `sync_to_head` (fetch + reset --hard +
clean -fdx) limpia los cambios uncommitted de un worktree antes de
la siguiente task. Test integration:
`tests/integration/test_worktree_sync.py`.

**Qué queda para Plan 06.5**: el flujo C1 ("agente recibe feedback
de conflicto y reintenta") y C2 ("plan a `blocked`") requieren el
orchestrator productivo que cablea el reviewer agente con la branch
remota y maneja `git push --force-with-lease` failures.

**Cómo ejecutarlo** (solo la parte que el demo cubre):

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_a_endtoend.py
# bloque "human_06_09 - conflicto entre tareas paralelas"
# imprime "Skipped en este demo - el conflicto real ... requiere DB-backed orchestrator"
```

**Checklist pass/fail**:

- [ ] La primera tarea pushea limpio al bare repo _(cubierto por `demo_a`)_
- [ ] La segunda tarea recibe conflicto al pushear _(Plan 06.5)_
- [ ] El sistema aplica la política configurada (C1 vs C2) _(Plan 06.5)_
- [ ] Si C1: el agente recibe el feedback de conflicto estructurado y reintenta _(Plan 06.5)_

---

## `human_06_10` — Escalado a humano tras `max_review_retries`

**Cubierto por**: `demo_d`.

**Qué prueba**:

1. Tras cada rechazo de `reject_review()`, la task vuelve a
   `backlog` con un audit event `review_comment` adjunto.
2. `retry_count` se incrementa en cada rechazo.
3. Al 3º rechazo, `escalate_if_exhausted()` transiciona a
   `awaiting_human` e invoca `notifier.notify_escalation()`.
4. Las 4 acciones humanas (`approve_manual`, `reassign_with_guidance`,
   `block_with_reason`, `cancel`) transicionan la task al estado
   correcto.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_d_review_audit.py
# bloque "human_06_10 - escalado a humano"
```

**Output esperado**:

```
rejection #1 -> retry_count=1
rejection #2 -> retry_count=2
rejection #3 -> retry_count=3
[ OK ] transicion a awaiting_human
[ OK ] notifier invocado 1 vez
[ OK ] action 'approve_manual' -> 'done'
[ OK ] action 'reassign_with_guidance' -> 'backlog'
[ OK ] action 'block_with_reason' -> 'blocked'
[ OK ] action 'cancel' -> 'cancelled'
```

**Verificación visual adicional**: en el admin-panel, una vez Plan
06.5 cablee `/api/plans/{id}/escalated-tasks`, la página
`/admin/plans/<plan_id>/escalated` mostrará los 3 intentos con sus
outputs y los 4 botones de acción.

**Checklist pass/fail**:

- [ ] Tras cada rechazo del revisor automático, la tarea vuelve a `backlog` con comentario estructurado adjunto
- [ ] El `retry_count` se incrementa correctamente en cada rechazo
- [ ] Tras `max_review_retries` (3), la tarea transiciona a `awaiting_human`
- [ ] Llega notificación al humano por el asistente personal con histórico completo _(Plan 10 — asistente personal; en Plan 06 solo se valida que el notifier hook se invoca)_
- [ ] El panel de Tareas Escaladas en la UI muestra los 3 intentos _(UI existe; endpoint backend Plan 06.5)_
- [ ] Las cuatro acciones humanas funcionan
- [ ] Cada acción queda en `audit_log` con timestamp, usuario, justificación

---

## `human_06_11` — Checkbox fail genera tarea nueva

**Cubierto por**: `demo_d`.

**Qué prueba**:

1. `create_task_from_checkbox()` crea una `TaskRecord` con
   `plan_id` correcto, `parent_checkbox_id` set, `status='backlog'`,
   `is_free_task=False`.
2. El título de la nueva task es el texto del checkbox; la
   descripción es el comentario del humano.
3. `create_free_task()` produce el mismo shape pero con
   `parent_checkbox_id=None` y `is_free_task=True`.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_d_review_audit.py
# bloque "human_06_11 - checkbox fail genera tarea plan-scoped"
```

**Output esperado**:

```
[ OK ] tarea nueva con plan_id correcto
[ OK ] titulo viene del checkbox
[ OK ] parent_checkbox_id correcto
[ OK ] tarea nueva en backlog
[ OK ] free task con is_free_task=True
[ OK ] free task sin parent_checkbox_id
```

**Checklist pass/fail**:

- [ ] Por cada checkbox marcado `fail` se crea automáticamente una tarea nueva en el plan
- [ ] El título de la tarea es el texto del checkbox; la descripción es el comentario humano
- [ ] Las tareas nuevas son plan-scoped (`plan_id` correcto) y aparecen en el Kanban filtrado del plan
- [ ] El plan vuelve a `in_progress` y las tareas nuevas a `backlog` _(la transición la orquesta `plan_progress.transition_to_pending_human_validation`; el toggle backend de pending→in_progress es Plan 06.5)_
- [ ] El coste estimado del plan se ajusta y la diferencia es visible al humano _(UI Plan 06.5)_
- [ ] Tras completar las tareas nuevas, el plan vuelve a `pending_human_validation` y se puede revalidar _(Plan 06.5)_
- [ ] El botón "Añadir tarea libre al plan" permite crear tareas no asociadas a checkboxes y también son plan-scoped

---

## `human_06_12` — Registro auditable completo de la tarea

**Cubierto por**: `demo_d`.

**Qué prueba**:

1. Una task con 3 rechazos + acciones humanas tiene history con
   eventos `review_comment` + `transition` + `creation` +
   `human_action`.
2. Los eventos están ordenados cronológicamente.
3. El history tiene ≥ 6 entries para una task con 3 rejections.

**Cómo ejecutarlo**:

```powershell
.\.venv\Scripts\python.exe scripts\demos\demo_human_06_d_review_audit.py
# bloque "human_06_12 - audit trail completo"
```

**Output esperado**:

```
[ OK ] history tiene review_comment events
[ OK ] history tiene transition events
[ OK ] history tiene >=6 entries (3 rejs + 3 trans) - 7
[ OK ] history ordenado cronologicamente
```

**Checklist pass/fail**:

- [ ] La vista de detalle muestra una línea de tiempo con todas las executions, reviews y comentarios en orden cronológico
- [ ] Cada entrada es expandible y muestra el detalle completo (`steps_log`, `tool_calls`, `feedback_text`...) _(UI Plan 06.5)_
- [ ] El histórico incluye las transiciones de estado con el actor que las causó
- [ ] El outcome final (commit hash si aplica, `manual_approval=true` si aplica) está visible en la cabecera de la tarea
- [ ] Exportar la tarea como bundle JSON produce un fichero con todo el registro auditable _(Plan 06.5)_
- [ ] La API `GET /api/v1/tasks/{id}/history` devuelve la misma información que ve la UI _(Plan 06.5 — el método `lc.history()` existe y devuelve los events; el endpoint REST que lo sirve queda fuera de Plan 06)_

---

## Cómo marcar el plan como `completed`

Cuando los 4 demos hayan salido `PASSED`:

1. Editar [`docs/roadmap/06-testing-revision-git.md`](../../roadmap/06-testing-revision-git.md)
   y cambiar el frontmatter:

   ```yaml
   status: completed
   completed_at: <fecha del día>
   ```

2. Verificar que la entrada de changelog
   [`docs/07-changelog/06-testing-revision-git.md`](../../07-changelog/06-testing-revision-git.md)
   existe (la sembré con el plan).
3. Push de la rama + abrir PR contra `master`.
4. Tras merge, el siguiente plan desbloqueable es **Plan 06.5
   (production wiring)** o, si se decide saltarlo, **Plan 07
   (documentación y visor)** — ambos son válidos según el árbol de
   dependencias.

## Qué entrega Plan 06 y qué deja para Plan 06.5

**Plan 06 entrega los módulos individuales con sus tests pasando** —
en total ~250 tests automáticos verdes. Los demos arriba prueban
que los módulos se integran entre sí (un `plan_runner` síncrono los
orquesta en secuencia y ejecuta git real).

**Plan 06.5 (production wiring)** cubrirá:

- Celery tasks que invocan estos módulos asincrónicamente.
- Migrations Alembic para `review_sessions` + `task_audit_events`.
- Endpoints REST: `GET /api/v1/tasks/{id}/history`, `POST
/api/plans/{id}/free-task`, `POST /api/tasks/{id}/human-action`,
  `POST /api/review/{id}/rerun`, `WS /ws/review/{id}/logs`.
- Beat scheduler para `purge_dep_cache`, `prune_worktrees`,
  `expire_review_runtimes`, `idle_sweep_pools`.
- Integración con el agente reviewer LangGraph (el reviewer recibe
  `reviewer_input_block(reports)` como parte de su prompt).
- Spin-up real de containers en los demos (postgres-test /
  redis-test / review-runtime con docker compose), tras lo cual los
  tests humanos `06_03`, `06_04`, `06_09` se ejecutan contra
  infraestructura real.

## Troubleshooting

| Síntoma                                             | Causa                                                                 | Workaround                                                           |
| --------------------------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `.venv\Scripts\python.exe no encontrado`            | Falta el venv local                                                   | `.\scripts\dev\bootstrap.ps1`                                        |
| `ModuleNotFoundError: api_server.plan_progress`     | Editable install no actualizado                                       | `pip install -e apps/api-server[dev]`                                |
| `ModuleNotFoundError: workers.git_repos`            | Idem                                                                  | `pip install -e apps/workers[dev]`                                   |
| `ModuleNotFoundError: orchestrator.plan_runner`     | Plan 06 introdujo el módulo; falta `pip install -e apps/orchestrator` | `pip install -e apps/orchestrator[dev]`                              |
| `git clone failed (rc=128)` durante setup           | Sandbox residual con read-only flags                                  | `Remove-Item -Recurse -Force scripts/.demo_06` y re-ejecutar         |
| `git: 'worktree' is not a git command`              | `git --version < 2.5`                                                 | Actualizar git a 2.30+                                               |
| Demo `b` falla con `commit_task: worktree is clean` | Re-ejecutaste demo `b` sin re-correr setup                            | `.\.venv\Scripts\python.exe scripts\demos\setup_demo_06.py` primero  |
| Caracteres `→` mal renderizados en consola          | cp1252 en cmd.exe                                                     | Usa PowerShell 7+; los scripts hacen `reconfigure(encoding='utf-8')` |

## Trampas conocidas del toolchain (transversales)

Si encuentras un error que no es específico de Plan 06, consulta:

- [`docs/03-guides/gotchas/powershell-utf8-em-dash-and-native-stderr.md`](../gotchas/powershell-utf8-em-dash-and-native-stderr.md)
  — em-dashes / cp1252 / `NativeCommandError`.
- [`docs/03-guides/gotchas/`](../gotchas/) — el resto de gotchas
  conocidos del stack (asyncpg, mypy, OTEL, …).
