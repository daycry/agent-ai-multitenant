---
adr: "0099"
title: Visor de diffs de código y flujo de resolución de conflictos
status: proposed
date: 2026-07-03
deciders: operador (pendiente)
phase: auditoria-plataforma-2026-07-03
related: ["0072", "0085", "0087", "0095", "0098"]
docs_language: es
---

# ADR 0099 — Visor de diffs de código y flujo de resolución de conflictos

## Contexto

Hallazgo **P7** de la auditoría de plataforma 2026-07-03 (`docs/roadmap/auditoria-plataforma-2026-07-03.md`,
recogido en `docs/roadmap/cadena-pr-plan.md` fila P7), verificado adversarialmente contra HEAD `3d22337`:
**no existe visor de diffs del CÓDIGO ni flujo de resolución de conflictos**, y un rebase conflictivo se
traga como un fallo genérico invisible.

Dos carencias distintas convergen en P7:

### 1) El único visor de diffs es el de documentación, y solo `.md`

`apps/api-server/src/api_server/docs_viewer/service.py` ya tiene toda la maquinaria de un diff estructurado
(`diff_doc`, `service.py:626-694`: `git diff <base>..<head> -- docs/<relpath>`, clasificación de líneas
`added`/`removed`/`context`/`hunk` en `DocDiffLine`), expuesta read-only y **tenant-scoped** en
`GET /projects/{project_id}/docs/diff` (`routers/docs_viewer.py:312-375`, gate
`require_tenant_member` + `_require_visible_project` → 404 por RLS). El frontend ya renderiza esas líneas
(`apps/admin-panel/app/admin/docs/doc-diff-renderer.tsx`, `doc-diff-view.tsx`).

Pero está deliberadamente **acotado a markdown**: `_safe_relpath` rechaza cualquier sufijo distinto de `.md`
(`service.py:339`, `if posix.suffix.lower() != MARKDOWN_SUFFIX: raise DocNotFoundError`), y además lee de un
checkout **`docs-mirror`** (`project_repo_root`, `service.py:284-298`), **no** del bare real donde vive la
rama del plan. Es decir: hoy un humano **no puede ver qué código cambió una tarea o un plan** desde la UI;
solo su documentación `.md`.

El árbol git real del plan vive en otro sitio: `BareRepoLayout` (`workers/git_repos.py:73-102`) resuelve el
bare en `{data_root}/projects/{tenant_slug}/{project_slug}/repos/{repo}.git` y los worktrees por tarea en
`worktrees/{task_id}`. La rama del plan (`plan/{id8}-{slug}`) y sus commits con trailers `Plan-Id`/`Task-Id`/
`Execution-Id` (`plan_git.py:105-168`) viven ahí, **no** en `docs-mirror`.

### 2) Un rebase conflictivo acaba en `commit_failed` genérico, invisible y sin resolución

Varias tareas hermanas del mismo plan comparten UNA rama y empujan a ella (`push_review_to_bare`,
`plan_git.py:239-290`). Cuando dos tocan las mismas líneas, el rebase de reconciliación falla y se
**re-lanza como `GitCommandError`** con mensaje "another task changed the same lines"
(`plan_git.py:281-287`, tras `rebase --abort`). Ese error sube por `_commit_and_push_worktree`, que captura
`Exception` y devuelve `True` (`execution.py:1016-1021`) → `_mark_commit_failed` sella
`execution.abort_code = "commit_failed"` y añade una nota al `output` (`execution.py:1024-1046`), **sin**
poner `status = needs_human_review`.

El problema: `commit_failed` **no** está en `_REVIEW_ESCALATION_ABORT_CODES`
(`plans.py:892-897`: solo `review_inconclusive`, `max_review_retries_exhausted`, `agent_reported_failure`) y
el run tampoco queda en `needs_human_review` (`_ESCALATED_EXECUTION_STATUS`). Como el panel de escaladas
(`plans.py:961-979`) solo muestra `awaiting_human_approval` o `blocked` + (estado escalado **o** abort_code
de la lista), **la tarea queda `blocked` e INVISIBLE**, sin acciones humanas, con el trabajo de la tarea
perdedora atrapado en su worktree y sin ninguna vía de resolución en la UI.

**Matiz importante (dos conflictos distintos):**

- **Conflicto intra-plan** (tareas hermanas contra la rama del plan, en el bare de la plataforma, dentro del
  worker): es el que dispara P7. El commit perdedor **nunca llega a la rama**, así que **no aparece en
  ningún PR** — el proveedor no puede resolverlo porque aún no está en la rama.
- **Conflicto rama-vs-base** (la rama del plan contra `main` al abrir/mergear el PR): eso ocurre **en el
  proveedor** (GitHub/GitLab/Azure, ADR 0072 Fase 2) y su UI ya lo resuelve.

Este ADR decide **si y cómo** introducir el visor de diffs de código y el flujo de conflictos. El plan
`cadena-pr-plan.md` (T8, Fase D) implementa **solo el mínimo**: distinguir el conflicto de rebase con un
`abort_code='rebase_conflict'` que SÍ entra en `_REVIEW_ESCALATION_ABORT_CODES` para que deje de ser
invisible. El **visor** y la **resolución** son un feature nuevo que necesita decisión de producto — este
ADR.

### Restricciones rectoras

- **Principio 2 (aislamiento):** el **worker es dueño del git**; el sandbox solo escribe ficheros en
  `/workspace` y el worker hace `add/commit/push/rebase` (ADR 0085 dec. 4). El api-server **no muta** el
  repo. Sí hay **precedente de git read-only en el api-server**: `docs_viewer` y `kb_sync` importan
  perezosamente `_run_git` (`service.py:655`) para un `git diff` sin shell (argv explícito,
  `safe.bareRepository=all`, `git_repos.py:114-155`). Un visor read-only encaja; un editor que **escriba** la
  resolución NO puede vivir en el api-server ni en el sandbox — tendría que pasar por el worker.
- **Tenant-scoped:** el diff debe leerse siempre bajo `{data_root}/projects/{tenant_slug}/{project_slug}/…`
  y solo tras que RLS confirme que el proyecto es visible para el tenant del caller (mismo patrón
  `require_tenant_member` + `_require_visible_project` → 404 de `docs_viewer`).
- **Fuente única de identidad git:** `cadena-pr-plan` T1 introduce `plan_git_identity(plan, project) →
(bare_repo_name, plan_branch)`. El visor de código debe resolver el bare **por esa misma función**, no por
  `docs-mirror` ni por una cuarta derivación, o reintroduce la deriva (causa raíz A de la auditoría).

## Decisión propuesta (pendiente de aprobación)

**Opción A: visor read-only de diffs de código de la rama del plan + escalado del conflicto a humano con
contexto.** Concretamente:

1. **Servicio de diff de código read-only**, hermano del de docs (nuevo `api_server/code_diff/service.py` o
   generalización de `docs_viewer`), que **reutiliza** `_safe_git_ref` (`service.py:358-384`), `_run_git` y
   la clasificación `DocDiffLine`, pero:
   - **sin** el candado `.md` (`service.py:339`): acepta cualquier ruta del repo, manteniendo la seguridad de
     traversal (rechaza `..`, absolutos, letras de unidad, NUL — la lógica de `_safe_relpath` menos el sufijo);
   - lee del **bare real** que devuelve `plan_git_identity` (no de `docs-mirror`);
   - soporta dos vistas: **diff de plan** (`base..plan_branch`, "qué cambia este plan") y **diff de tarea**
     (`parent..task_sha` localizando el commit por el trailer `Task-Id`);
   - **acotado**: tope de bytes por fichero + truncado, paginado por fichero, y el `timeout=120` ya presente
     en `_run_git` (evita saturar contexto con un scaffold entero — mismo riesgo señalado en ADR 0095).
2. **Endpoint(s) GET tenant-scoped**, p.ej. `GET /projects/{project_id}/plans/{plan_id}/diff` y
   `GET /projects/{project_id}/code/diff?base=&head=&path=`, con el gate `require_tenant_member` +
   `_require_visible_project` (cross-tenant → 404). **Solo GET**: no hay superficie de mutación (principio 2
   intacto).
3. **Escalado del conflicto con contexto** (encima del mínimo de `cadena-pr-plan` T8): cuando
   `rebase_conflict` escala la tarea, el run persiste el **contexto del conflicto** (lista de ficheros en
   conflicto + `plan_branch` + sha de la rama + sha del worktree de la tarea) y la entrada del panel de
   escaladas (`plans.py:900-979`) enlaza al visor de código para ver **ambos lados** del conflicto (diff de
   la rama y diff del worktree perdedor). El humano resuelve **fuera de banda** (re-lanza la tarea, ajusta el
   plan, o resuelve en el host) — **sin** editor in-app en esta iteración.
4. **Frontend read-only**: reutiliza `doc-diff-renderer.tsx` (ya clasifica added/removed/context/hunk) para
   el código, abierto desde el Kanban de tareas del plan y desde el panel de escaladas.

**No entra (aplazado explícitamente):** editor de merge 3-vías in-app con `git rebase --continue` desde la
UI (opción B) → futuro ADR si la tasa de conflictos intra-plan lo justifica. **Delegado por diseño:** los
conflictos **rama-vs-base** se resuelven en el **PR del proveedor** (opción C, ya cubierto por ADR 0072
Fase 2), no en la plataforma.

## Opciones evaluadas

| Opción                                                        | Pros                                                                                                                                                                                                                                                                                                                                                                                                                                 | Contras                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A — Visor read-only + escalado con contexto (RECOMENDADA)** | Alinea con principio 2 sin fisuras (solo GET, worker sigue siendo único mutador). Reutiliza infra probada (`diff_doc`, `_safe_git_ref`, `DocDiffLine`, `doc-diff-renderer.tsx`) → coste bajo. Cubre AMBOS tipos de conflicto en su rol correcto (intra-plan visible + escalado; rama-vs-base al PR). Superficie de seguridad conocida (traversal + ref-injection ya resueltas). Tenant-scoped por el mismo patrón RLS→404 existente. | No resuelve el conflicto intra-plan **en la app**: el humano resuelve fuera de banda (re-lanzar tarea / host). Requiere leer del bare real (no `docs-mirror`) → depende de `cadena-pr-plan` T1 (identidad git canónica). Diffs grandes hay que acotarlos (bytes/paginado).                                                                                                                                                                                                                                                              |
| **B — Resolución in-app (editor 3-vías)**                     | Cierra el ciclo sin salir de la plataforma: el humano resuelve el conflicto intra-plan y el worker hace `rebase --continue` + push. UX superior para equipos sin acceso al host.                                                                                                                                                                                                                                                     | **Superficie de mutación nueva** con contenido humano escrito en el repo: NO puede vivir en api-server ni sandbox (principio 2) → obliga a un **tipo de task worker nuevo** (recibir resolución → aplicar → `rebase --continue` → push) + estado de UI complejo (marcadores de conflicto, abort, reintento). Riesgo de corromper la rama del plan si la resolución es inconsistente. Alto esfuerzo para una tasa de conflictos hoy **desconocida** (0 planes `completed`, la cadena nunca se ha disparado). Sobre-ingeniería prematura. |
| **C — Delegar todo al PR del proveedor + solo escalar**       | Coste casi nulo (el proveedor ya trae editor de conflictos maduro). Consistente con ADR 0072 (GitHub/GitLab/Azure). Cero superficie de mutación propia.                                                                                                                                                                                                                                                                              | **No aplica al conflicto intra-plan**: el commit perdedor **nunca llega a la rama** (`plan_git.py:281-287`), así que **no hay nada que resolver en el PR** — el trabajo queda atrapado en el worktree, invisible. Deja P7 (su mitad de mayor impacto) sin resolver. Depende de que el proyecto tenga remoto/PR (los locales quedan sin nada). No da visibilidad del código dentro de la plataforma (sigue sin visor).                                                                                                                   |
| **D — Status quo (solo `cadena-pr-plan` T8, sin visor)**      | Mínimo esfuerzo: `rebase_conflict` deja de ser invisible en el panel. Nada nuevo que mantener.                                                                                                                                                                                                                                                                                                                                       | El humano ve que **hay** un conflicto pero **no puede ver el código** (ni el diff ni los lados del conflicto) desde la UI → diagnóstico a ciegas, resolución 100% en el host. No añade el visor de código que P7 pide como carencia principal. Deja la plataforma sin la superficie de "ver qué cambió un plan/tarea", útil mucho más allá de los conflictos (review humano, auditoría).                                                                                                                                                |

**Por qué A y no B/C/D:** B es la única que resuelve el conflicto intra-plan _dentro_ de la app, pero paga
una superficie de mutación que choca con el principio 2 y un coste alto para una frecuencia de conflictos que
hoy es **cero observada** — es optimizar sin datos. C resuelve el conflicto equivocado (rama-vs-base, que ya
está cubierto por el PR) y no toca el intra-plan. D no entrega el visor, que es la carencia central de P7. A
entrega el 80% del valor (visibilidad del código + conflicto escalado con contexto) al 20% del coste,
respeta principio 2 por construcción (read-only), y **deja la puerta abierta** a B como follow-up medido por
la tasa real de `rebase_conflict` una vez la cadena de PR esté viva.

## Consecuencias

**Si se acepta (Opción A):**

- **Nuevo servicio + endpoint(s) read-only de diff de código**, tenant-scoped, que leen del **bare real** vía
  `plan_git_identity` (dependencia blanda de `cadena-pr-plan` T1: si T1 no está, el visor puede resolver el
  bare por `BareRepoLayout` con `project.slug`, pero conviene reconciliar para no crear una 4.ª derivación).
- **Reutilización** de `_safe_git_ref` + `_run_git` + `DocDiffLine` + `doc-diff-renderer.tsx`; el único
  cambio de contrato es levantar el candado `.md` **solo** en el nuevo servicio de código (el de docs
  mantiene su contrato markdown intacto). Recomendado: módulo `code_diff` hermano, no relajar `docs_viewer`.
- **Contexto de conflicto persistido** en el run/tarea (ficheros en conflicto, `plan_branch`, shas) y
  enlazado desde el panel de escaladas; requiere que `cadena-pr-plan` T8 ya haya cambiado `commit_failed` →
  `rebase_conflict` (dependencia dura para que la entrada sea visible).
- **Principio 2 preservado:** endpoints solo-GET; el worker sigue siendo el único que muta el repo. Igual que
  ADR 0095 desciega al reviewer con un **worktree read-only**, aquí el humano ve el código read-only sin
  ganar capacidad de escritura sobre la rama.
- **Frontend:** una vista de diff de código (reutiliza el renderer existente) abierta desde el Kanban del
  plan y desde el panel de escaladas.

**Lo implementa:** un plan de roadmap propio (candidato, p.ej. `visor-diffs-codigo`), **fuera** de
`cadena-pr-plan` (que solo cubre T8, el escalado mínimo). No es un cableado rápido: toca api-server (servicio

- router), admin-panel (vista) y el payload de escalada.

**Migraciones / riesgos:**

- Mayormente **aditivo** (endpoints read-only + FE + campos de contexto en el run, en JSONB si se evita
  migración). Persistir el contexto de conflicto puede ir en `execution.output`/`inputs` (JSONB) sin migración
  de columna, como se hace ya con `is_free_task` (`plans.py:857`).
- **Riesgo — diffs grandes:** un scaffold CI4 entero satura contexto/pantalla (mismo riesgo que ADR 0095
  documenta para el reviewer). Mitigación: tope de bytes por fichero + truncado + paginado por fichero +
  `timeout=120` de `_run_git`.
- **Riesgo — acoplamiento api-server ↔ workers:** el api-server importa perezosamente `workers.git_repos`
  (ya ocurre en `docs_viewer`/`kb_sync`). Aceptable y precedentado; la alternativa (duplicar `_run_git`)
  sería peor.
- **Riesgo — exposición de código:** levantar el candado `.md` expone el contenido de ficheros arbitrarios
  del repo en el diff. Es el objetivo (ver el código del plan) y está **tenant-scoped** (un miembro ve el
  código de SU proyecto). Se mantienen intactas las defensas de traversal y de ref-injection.

## Criterio de aceptación

1. **Visor de código funcional y tenant-scoped:** el diff de una rama de plan vs su base renderiza en la UI
   para un fichero **no-`.md`** (p.ej. `.php`); un caller de otro tenant sobre el mismo `project_id` recibe
   **404** (RLS), nunca el contenido.
2. **Lee del bare correcto:** el diff proviene del **mismo** bare que resuelve `plan_git_identity`
   (`cadena-pr-plan` T1), no de `docs-mirror`; un test lo fija (guard de que no reaparece la ruta
   `docs-mirror` en el camino de código).
3. **Conflicto intra-plan visible y con contexto:** un push conflictivo produce `abort_code='rebase_conflict'`
   (T8) **y** la entrada del panel de escaladas enlaza a un diff que muestra los ficheros en conflicto con
   **ambos lados** (rama del plan y worktree perdedor). Ya no aparece como `commit_failed` genérico
   silenciado ni como `blocked` invisible.
4. **Sin superficie de mutación:** los endpoints del visor son solo-GET; no existe ningún camino por el que el
   api-server o el sandbox escriban en el repo (principio 2). Un intento de mutar devuelve 405/ausente.
5. **Seguridad:** refs option-like / con whitespace / NUL y rutas con `..`/absolutas se rechazan (reutiliza
   `_safe_git_ref` + validador de rutas); el diff está acotado por bytes y por `timeout`.
6. **Delimitación clara:** el editor 3-vías in-app (opción B) **no** se implementa aquí y queda como candidato
   de ADR futuro condicionado a la tasa real de `rebase_conflict`; los conflictos rama-vs-base se resuelven en
   el PR del proveedor (ADR 0072), documentado como tal.
