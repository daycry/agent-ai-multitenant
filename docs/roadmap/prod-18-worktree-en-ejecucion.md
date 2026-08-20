---
plan_id: prod-18-worktree-en-ejecucion
title: Worktree en la ejecución del agente — código persistente, commit y test-runtime real
status: pending_human_validation
blocking_plan: null
started_at: 2026-06-26
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 14-18
estimated_cost_human_eur: 6.500 € – 9.000 €
estimated_cost_ai_eur: 40 € – 90 €
created_by: prod-17-test_01-defer-2026-06
spec_sections_referenced: [12.4, 12.5, 14]
docs_language: es
priority: P2
---

# Plan prod-18 — Worktree en la ejecución del agente

## Cabecera

| Campo                            | Valor                                                                    |
| -------------------------------- | ------------------------------------------------------------------------ |
| **ID del Plan**                  | `prod-18-worktree-en-ejecucion`                                          |
| **Prioridad**                    | P2                                                                       |
| **Bloqueado por**                | — (las bibliotecas son de Plan 06, `completed`)                          |
| **Tiempo estimado (calendario)** | 3-4 semanas                                                              |
| **Rama git sugerida**            | `plan/prod-18-worktree-en-ejecucion`                                     |
| **ADRs relacionados**            | `0072` (git por proyecto), `0063` B2 (worktree del plan), `0084`/prod-17 |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

> **Estado (2026-07-06, auditoría de roadmap)**: `status` corregido de `in_progress` (congelado
> desde 2026-06-26) a `pending_human_validation`. PR #57 fusionado a `master` (`e78ed2b`); 4/5
> checkboxes hechos, el único pendiente (`task_prod18_e2e_01`) requiere runner Docker real. Trabajo
> colateral posterior que avanza este mismo subsistema sin actualizar este doc: `bdea0af`
> (2026-07-03, durabilidad de worktrees/data-root) y ADR 0095 D1 (montaje read-only del worktree
> para el reviewer, ver prod-17).

## Resumen

El Plan 06 (`completed`) construyó y **testeó** todas las bibliotecas de git-worktrees:
`git_repos` (`BareRepoLayout`, `BareRepoManager.ensure_repo`, `WorktreeManager.add`/
`sync_to_head`/`prune_idle`), `plan_git` (`commit_task` con trailers, `push_review_to_bare`,
`push_branch_to_remote`, `apply_push_policy`, `open_plan_pr`) e `isolation.
build_hardened_run_kwargs(workspace_host_path=...)` que **ya** bind-montea un worktree del
host en `/workspace`. Pero **el runtime de ejecución productivo no las usa**: `conduct_execution`
(`execution.py:694`) construye el `ContainerSpec` **sin** `workspace_host_path`, así que el
agente implementador corre en un **`/workspace` tmpfs efímero** — todo lo que escribe se
**pierde** al destruir el contenedor, nada se commitea, las ramas de plan no llevan diffs y
el test-runtime no tiene **qué** testear (`config.py:100-104`: "real worktree mounts arrive
in Plan 06" — quedó sin cablear en el camino live). La cadena completa solo está ensamblada
en `orchestrator/plan_runner.py` (runner **síncrono en memoria** para los 12 tests humanos,
con un `file_writer` **stub** en vez del agente real). Hallazgo `[critical]` de la auditoría
(`auditoria-zonas-2026-06.md:82`) y pendiente explícito de **ADR 0072** ("pipeline de
ejecución-git… es un plan de roadmap propio").

Este plan **cablea** esas bibliotecas en la ejecución real: provisiona un worktree por
tarea, lo bind-montea en el sandbox del agente (DooD, principio 2), commitea el output con
los trailers obligatorios, lo empuja al bare, y **encadena el test-runtime** sobre el mismo
worktree. Es la pieza pivote que **desbloquea en cascada**: prod-17 `test_01`/`e2e` (el AI
reviewer recibe TestReports reales), ADR 0063 B2 (worktree del plan para el review-runtime)
y el auto-PR de ADR 0072 (de "infra lista" a "PRs con contenido real").

## Alcance

**Entra**:

- **Modelo de datos**: resolución `repo_name` por tarea (hoy **no hay FK Task→repo**) +
  columnas `slug` en `projects`/`plans` (ADR 0072 las pide para nombres de rama/worktree
  estables). Migración Alembic reversible.
- **Provisión del worktree en `conduct_execution`**: `ensure_repo` + `WorktreeManager.add(
task_id, branch=make_plan_branch_name(plan))` + `sync_to_head` antes de lanzar el agente;
  fijar `ContainerSpec.workspace_host_path` al path del worktree (bind RW, DooD-seguro).
- **Commit + push tras el run** (lo hace el **worker**, no el sandbox — principio 2): al
  terminar `done`, `commit_task` con trailers `Plan-Id`/`Task-Id`/`Execution-Id` +
  `push_review_to_bare`; honrar `branch_push_mode` (`PlanGitPolicies`, ya tipado).
- **Encadenar el test-runtime**: propagar `worktree_host_path` a `run_test_runtime` →
  persiste `TestReport`/`test_run_completed` (cierra **prod-17 `task_prod17_test_01`**; el
  consumidor `test_02` ya está hecho).
- **Hardening del bind**: path absoluto del host idéntico en worker y daemon (gotcha DooD),
  bind `{data_root}:{data_root}` (nunca volumen nombrado), cap-drop ALL / sin socket /
  red restringida intactos. Documentar el gotcha DooD dedicado (no existe hoy).

**Queda fuera**:

- **B1 — procedencia de `main_image` del review-runtime de preview humano** (ADR 0063 Parte B):
  decisión independiente (imagen genérica vs build por plan vs operador-configurable). Este
  plan resuelve B2 (materializar el worktree) pero NO B1.
- El **AI reviewer** (prod-17): NO se toca; este plan solo le da el TestReport real que
  consume. No confundir el AI reviewer (ejecución de agente) con el review-runtime de preview
  humano (contenedor que sirve la app, ADR 0063).
- **Pool elástica con reuso de worktree** (`runtime_pool._cleanup_between_steps` no-op +
  `AgentContainerRunner.reset_slot` ausente): optimización de rendimiento (mantener el proceso
  Python caliente entre roles). Este plan usa el modelo "un contenedor por tarea" (Plan 02) ya
  productivo; el reuso es un plan de rendimiento posterior.

## Decisiones clave (a cerrar en `task_prod18_design_01` — ADR)

1. **Granularidad del worktree**: **uno por tarea** (modelo actual `worktrees/{task_id}`,
   HEAD detached compartiendo la rama del plan) + consolidación a la rama del plan vía
   `push_review_to_bare`. Alternativa: worktree de plan compartido (más colisiones entre
   tareas concurrentes). **Recomendado: por tarea** (lo que las bibliotecas ya asumen y testean).
2. **Quién commitea**: el **worker** (lee el worktree que el agente escribió y hace
   `git add/commit` con trailers), NO el sandbox. El agent-runtime no tiene credenciales git
   ni acceso al bare (principio 2). El sandbox solo escribe ficheros en `/workspace`.
3. **Cadencia de push**: reusar `PlanGitPolicies.branch_push_mode` (`incremental` = push por
   tarea aceptada vs `final_only` = al cerrar el plan). `push_review_to_bare` (worktree→bare)
   siempre tras el commit; `push_branch_to_remote` según el modo.
4. **`repo_name` por tarea**: un proyecto puede tener varios bare repos. **Recomendado para el
   MVP**: un repo por proyecto (convención `repo_name = project.slug` o el único repo del
   proyecto) hasta que exista multi-repo; documentar la convención y dejar el hook para una FK
   `Task.repo_name`/`Project.primary_repo` futura.
5. **Columnas `slug`**: añadir `projects.slug` y `plans.slug` (migración reversible) para
   `BareRepoLayout` (paths estables, nunca UUIDs) y `make_plan_branch_name`. Backfill desde el
   nombre al migrar.
6. **Bind RW vs RO**: el worktree del **agente implementador** se monta **RW** (escribe
   código); el del **review-runtime** (ADR 0063, fuera) iría RO. Mantener el perfil endurecido.
7. **Prerequisito LLM (no es decisión de arquitectura)**: con modelos pequeños (p.ej.
   `llama3.2:1b`) el agente a veces solo "responde" el código sin escribir el fichero; el
   pipeline luce de verdad con un modelo capaz (Claude SDK). El plan asume que "hay algo que
   commitear" depende de que el agente escriba ficheros reales (`write_file` ya existe en el
   sandbox, confinado a `/workspace`).

## Tareas

### Fase A — Modelo de datos + diseño

#### `task_prod18_design_01` — ADR de worktree-en-ejecución + columnas slug

- [x] **Título**: Redactar ADR con las decisiones 1-6 (granularidad, quién commitea, cadencia,
      `repo_name` por tarea, columnas slug, bind RW/RO). Migración Alembic reversible:
      `projects.slug` + `plans.slug` (con backfill) y, si se aprueba, el hook de `repo_name`.
- **Tiempo**: 2 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod18_design_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_slug_columns_migration.py -v"
  ```

### Fase B — Provisión del worktree en la ejecución

#### `task_prod18_provision_01` — Montar el worktree en `conduct_execution`

- [x] **Título**: En `conduct_execution` (execution.py:694), antes de lanzar el agente:
      resolver `BareRepoLayout` (slugs del Project ya cargado) → `BareRepoManager.ensure_repo` →
      `WorktreeManager.add(task_id, branch=make_plan_branch_name(plan))` → `sync_to_head`;
      pasar `workspace_host_path=str(wt_path)` al `ContainerSpec`. Path absoluto del host
      (DooD). Una tarea sin plan/repo (caso degenerado) cae a tmpfs como hoy (sin romper).
- **Tiempo**: 3 días · **Complejidad**: l
- **Depende de**: task_prod18_design_01
- **Tests automáticos**:
  ```yaml
  - id: auto_prod18_provision_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_conduct_execution_worktree.py -v"
  ```

### Fase C — Commit + push del output del agente

#### `task_prod18_commit_01` — Commit con trailers + push al bare tras el run

- [x] **Título**: Tras `finalize_execution` con `done`, si el worktree tiene cambios:
      `commit_task(wt_path, trailers=CommitTrailers(plan_id, task_id, execution_id, "agent"))` + `push_review_to_bare(wt_path)`; `push_branch_to_remote` según `branch_push_mode`.
      Árbol limpio (`commit_task` lanza "worktree is clean") → la tarea no produjo cambio (no
      es error). Lo hace el worker (el sandbox no tiene credenciales).
- **Tiempo**: 2,5 días · **Complejidad**: m
- **Depende de**: task_prod18_provision_01
- ✅ **Comando corregido (2026-08-20)**: `tests/integration/test_execution_commits_to_worktree.py`
  nunca existió; los dos tests que esta casilla pedía están en
  `tests/integration/test_conduct_execution_worktree.py`, el mismo fichero que declara
  `task_prod18_provision_01` (de ahí el `-k`: cada orden verifica su fase). Cubren las **dos**
  ramas del enunciado con git de verdad en `tmp_path`: el fichero que escribe el agente llega
  al bare con los tres trailers (`Plan-Id`, `Task-Id`, `Execution-Id`), y el **árbol limpio no
  es un error** —`commit_task` lanza «worktree is clean», se traga, no se empuja nada y la
  rama se queda en el commit semilla vacío. Verde 2/2 (7/7 el fichero entero).
- **Tests automáticos**:
  ```yaml
  - id: auto_prod18_commit_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_conduct_execution_worktree.py -v -k commit_and_push"
  ```

### Fase D — Encadenar el test-runtime (cierra prod-17 test_01)

#### `task_prod18_test_01` — Disparar `run_test_runtime` sobre el worktree

- [x] **Título**: Tras el commit del implementador (Fase C), si la tarea tiene
      `acceptance_criteria` automáticos, disparar `run_test_runtime` con el mismo
      `worktree_host_path` (reusa `group_tasks_by_runtime`); persiste `test_run_completed`.
      Esto cierra **prod-17 `task_prod17_test_01`** — el consumidor (`_build_review_request` →
      `<test-report>`) ya está hecho, así que el AI reviewer recibe el TestReport real.
      Decidir el orden tests↔in_review (los tests deben estar antes de la review).
- **Tiempo**: 2,5 días · **Complejidad**: m
- **Depende de**: task_prod18_commit_01
- ✅ **Comando corregido (2026-08-20)**: `tests/integration/test_test_runtime_wiring.py` nunca
  existió, ni aquí ni en prod-17 `task_prod17_test_01`, que declaraba **el mismo camino
  inexistente**. Los tests están en `tests/integration/test_conduct_execution_worktree.py`
  (`-k run_task_tests`): la petición al test-runtime lleva el `worktree_host_path` del
  implementador y **filtra** los criterios de aceptación —sólo los automáticos; sin ninguno no
  se despacha nada—. El segundo comando cubre dónde corre eso hoy: `task_wf_22` movió la fase
  de la cola `default` a la cola `test` con espera acotada, así que lo que aquel test fijaba
  (qué request se construye) sigue valiendo, pero el seam cambió. Las dos casillas apuntan
  ahora al mismo par de órdenes, que es lo correcto: es un cableado, no dos. Verde 2/2 + 11/11.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod18_test_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_conduct_execution_worktree.py -v -k run_task_tests"
  - id: auto_prod18_test_01_b
    runtime: python-pytest
    command: "pytest tests/unit/test_test_phase_queue.py -v"
  ```

### Fase E — e2e, hardening y cierre

#### `task_prod18_e2e_01` — e2e implementador→worktree→commit→tests + gotcha DooD

- [ ] **Título** ⏸️ **REQUIERE RUNNER DOCKER (no acreditable localmente)**: Test e2e (Docker
      real, skip-guarded como el e2e de instalación): un agente escribe en el worktree → el
      worker commitea con trailers → push al bare → el test-runtime monta el mismo worktree y
      persiste el TestReport. Documentar el **gotcha DooD del bind** en `docs/03-guides/gotchas/`.
      Verificar el bind `{data_root}:{data_root}` en el compose generado (no volumen nombrado). > Parcial: **gotcha DooD escrito** (`worktree-bind-dood-empty-vs-named-volume.md`) + > **esqueleto e2e skip-guarded** (`tests/e2e/test_worktree_execution.py`). El run GREEN > real necesita un runner Linux+Docker + modelo capaz (como prod-01 task_20: el skip NO > lo acredita). Las piezas (1)-(4) están cubiertas en integración (sin Docker).
- **Tiempo**: 2 días · **Complejidad**: m
- **Depende de**: task_prod18_test_01
- **Tests automáticos**:
  ```yaml
  - id: auto_prod18_e2e_01_a
    runtime: python-pytest
    command: "E2E_INSTALL=1 pytest tests/e2e/test_worktree_execution.py -v"
  ```

## Coordinación con otros planes

- **Plan 06** (`completed`): dueño de las bibliotecas (`git_repos`/`plan_git`); este plan las
  CABLEA en la ejecución (no las re-implementa).
- **prod-17** (`in_progress`): este plan desbloquea `task_prod17_test_01` (TestReport real) y
  por cascada `task_prod17_e2e_01`. El consumidor del `<test-report>` (`test_02`) ya está hecho.
- **ADR 0063 B2**: materializar worktrees en disco para la ejecución resuelve la maquinaria que
  B2 necesita (worktree del plan); B1 (`main_image`) sigue independiente.
- **ADR 0072**: el auto-PR ya disparado pasa a tener contenido real cuando los agentes commitean.
  Dependencia: `git`/`openssh-client` en imágenes (ADR 0072 dice hechas), credenciales Vault.

## Criterios de cierre

1. Todos los checkboxes `[x]` con su test automático en verde.
2. Un agente real escribe código que persiste en el worktree, se commitea con trailers y se
   empuja al bare; el test-runtime corre sobre ese worktree (e2e Docker verde en runner Linux).
3. prod-17 `task_prod17_test_01` desbloqueado y marcado.
4. Tests humanos del plan validados.
5. Entrada en `docs/07-changelog/prod-18-worktree-en-ejecucion.md` + gotcha DooD documentado.
6. PR del plan mergeado a master.
