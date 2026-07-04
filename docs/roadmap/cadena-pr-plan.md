---
plan_id: cadena-pr-plan
title: Cadena auto-PR del cierre de plan — identidad git de fuente única, push incremental y persistencia del PR
status: pending_approval
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 3-4 días
estimated_effort_person_days: 3
estimated_cost_human_eur: 1.200 € – 1.800 €
estimated_cost_ai_eur: 20 € – 40 €
created_by: auditoría-plataforma-2026-07-03
spec_sections_referenced: []
docs_language: es
---

# Plan cadena-pr-plan — que el «PR automático al cerrar el plan» realmente funcione contra un remoto real

> **Origen:** auditoría de plataforma 2026-07-03 (`docs/roadmap/auditoria-plataforma-2026-07-03.md`),
> causa raíz **A (identidad git sin fuente única)** + **G (cierre de plan incompleto)**. Ocho hallazgos
> (P1-P8) verificados adversarialmente en Opus 4.8. El principio rector 5 de CLAUDE.md («Plan = unidad de
> cambio… al completar el plan se abre un PR automático», ADR 0072/0085 `accepted`) **no funciona hoy con
> un remoto real**: la rama del PR nunca coincide con la de los commits, el bare del PR no es el de la
> ejecución, y en el modo por defecto la rama jamás llega al remoto.
>
> **Severidad efectiva: P1 latente.** La BD tiene **0 planes `completed`** (1 solo plan, `in_progress`), así
> que la cadena auto-PR **nunca se ha disparado** todavía; no hay daño observado ni pérdida de datos (los
> commits persisten en el bare local durable). Pero el **único proyecto con git configurado** («Api CI» →
> `github.com/daycry/test-mailchimp-agent-ai`, provider github, auth pat) tocaría el bug **el instante en que
> su plan pase a `completed`**. Hay que arreglarlo antes del primer cierre real, no con urgencia de incidente.

## Cabecera

| Campo           | Valor                                                      |
| --------------- | ---------------------------------------------------------- |
| **ID del Plan** | `cadena-pr-plan`                                           |
| **Rama git**    | `plan/runs-visor-trabajo` (rama en curso)                  |
| **Causa raíz**  | A (identidad git sin fuente única) + G (cierre incompleto) |
| **Depende de**  | — (independiente; toca `apps/workers` + `apps/api-server`) |

## Problema (con evidencia verificada)

Cada fila es un hallazgo verificado adversarialmente (lente «refutar») contra HEAD `3d22337`. Veredicto
completo en la sección 7 del informe de auditoría.

| Id     | Veredicto         | Defecto                                                                                                                                                                                                                                                                                                                                                          | Evidencia (file:line en HEAD)                                                                                                   |
| ------ | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **P1** | confirmado        | La rama del auto-PR **NUNCA** coincide con la de los commits. El enqueue antepone `"Plan: "` al título (`review.py:484`) y el worker deriva la rama de `_slugify(title)` (`plan_pr.py:65`) → prefijo `plan-` extra; la ejecución usa `plan.slug` (sin prefijo, ascii-fold, cap 60). Triple divergencia.                                                          | `review.py:484`, `plan_pr.py:65`, `repo_clone.py:28`, `slug.py:22`, `execution.py:884/989/1200`, `plans.py:209`                 |
| **P2** | confirmado        | El **bare repo diverge** entre ejecución y PR/clone. Ejecución escribe en `.../{project.slug}/repos/{project.slug}.git` (sin `origin`); clone/PR usan `.../{_slugify(project.name)}/repos/{basename(remote_url)}.git` (con `origin`). Para todo proyecto con remoto cuyo basename ≠ `project.slug` son **dos bares físicos distintos**.                          | `execution.py:883/886/1003`, `plan_pr.py:61/99`, `repo_clone.py:33-36/71/103-104`                                               |
| **P3** | confirmado        | En modo `incremental` (**el default**) la rama **nunca se empuja al remoto**: `_commit_and_push_worktree` solo hace `push_review_to_bare` (local); `open_plan_pr` solo fuerza push si `branch_push_mode=='final_only'`. Contradice ADR 0085 decisión 5.                                                                                                          | `execution.py:1002/960`, `plan_git.py:300/338`, `plan_pr.py:37`, `plan_runner.py:240` (solo demo)                               |
| **P4** | confirmado        | `apply_push_policy` (el único código que materializa el merge directo con `git update-ref`) **no tiene caller de producción** (solo tests). La opción de UI «Merge directo a la rama base» (`direct_to_default_allowed`) se persiste pero se comporta **idéntica** a «Abrir PR».                                                                                 | `plan_git.py:378/397-409`, `plan_pr.py:107`, `git-config-section.tsx:254`                                                       |
| **P5** | matizado          | No hay re-sync **automático** del remoto: `fetch_remote` solo se llama al (re)guardar la config git (`projects.py:365`); su docstring promete un beat periódico y un webhook receiver que **no existen**. _Matiz:_ re-guardar la config SÍ re-sincroniza (no es «nunca»); falta el automático/periódico/por-webhook y el botón «Sincronizar» dedicado.           | `git_repos.py:275-278` (docstring), `repo_clone.py:105`, `beat_schedule.py` (sin entrada git), `webhooks/actions.py:9-20`       |
| **P6** | confirmado        | La **URL/rama del PR no se persiste ni se muestra**: `plans` no tiene columna `pr_url`/`branch`; el resultado de `open_plan_pr` solo se loguea y la task es fire-and-forget (`send_task` sin `.get()`). Única traza = logs del worker.                                                                                                                           | `\d plans` (sin pr_url), `plan_git.py:363-374`, `plan_pr.py:110-122`, `celery_client.py:178-192`, admin-panel (0 refs a pr_url) |
| **P7** | confirmado        | **Sin visor de diffs de código ni flujo de conflictos**: el único diff viewer es `docs_viewer` (solo `.md`); un rebase conflictivo se relanza como `GitCommandError` y acaba en `abort_code='commit_failed'` genérico, excluido del panel de escaladas, sin resolución en UI.                                                                                    | `docs_viewer/service.py:339`, `plan_git.py:281-287`, `execution.py:1016/1040`, `plans.py:892` (escalation codes)                |
| **P8** | matizado (inerte) | `_commit_and_push_worktree` usa `PlanGitPolicies()` hardcoded (`execution.py:1005`). _Matiz:_ **sin impacto funcional** — el único método invocado (`push_review_to_bare`, worktree→bare local) **no lee** `self._policies` en ninguna línea; las políticas solo gobiernan el paso bare→remoto/PR, que sí las lee en `plan_pr.py`. Es higiene de código, no bug. | `execution.py:1002-1006`, `plan_git.py:239-290` (no referencia `_policies`), `plan_pr.py:62`                                    |

**Diagnóstico de raíz (A):** hay **tres derivaciones independientes** de la identidad git de un plan — la rama
(`plan/{id8}-{slug}`) y el bare repo — calculadas en tres sitios con reglas distintas (`execution.py` usa
`plan.slug`/`project.slug` persistidos; `plan_pr.py`/`repo_clone.py` usan `_slugify(title)`/`_slugify(name)`/
`basename(url)`). Sin una **fuente única**, la ejecución y el auto-PR operan sobre ramas y bares distintos. La
deriva la introdujo prod-18/ADR 0085 (que fijó la ejecución a `plan.slug`/`project.slug`) sin reconciliar el
camino heredado de ADR 0072 (`plan_pr.py`/`repo_clone.py`).

## Alcance

**Entra:** reconciliación de la identidad git (rama + bare) en una fuente única, cableado del push incremental
al remoto según política, persistencia y superficie del PR, decisión sobre el «merge directo», y re-sync
automático del remoto. Todo con test automático; e2e de cierre contra un remoto fake.

**Queda fuera (GATED → ADR):**

- **Visor de diffs de código + flujo de resolución de conflictos completo** (P7 en su forma plena) → **ADR
  candidato 0099**. Este plan solo cubre el mínimo: distinguir el `abort_code` de conflicto de rebase y
  escalarlo a humano (hoy se traga como `commit_failed` genérico). El visor de diffs y la UI de resolución
  son un feature nuevo que necesita decisión de producto.
- **Política de timing/merge del ciclo de PR** (¿merge directo real?, ¿re-sync por webhook con verificación de
  firma?) → **ADR candidato 0098**. Este plan implementa la mecánica; las decisiones de política van al ADR.

## Decisiones clave

- **Fuente única de identidad git**: una función canónica `plan_git_identity(plan, project)` (o equivalente)
  que devuelva `(bare_repo_name, plan_branch)` y sea la **única** llamada por ejecución, clone y auto-PR.
  Guard-test estático que falle el CI si aparece `_slugify(` o `make_plan_branch_name(` fuera de esa función.
- **`plan.slug` y `project.slug` son la verdad** (ya persistidos, ADR 0085 dec.1): el auto-PR pasa a usarlos;
  se elimina la derivación desde el título en `plan_pr.py`.
- **Un bare repo por proyecto** (ADR 0085 dec.2): el nombre del bare se deriva de `project.slug` en TODOS los
  caminos; el `origin` se configura en ese mismo bare (hoy la ejecución lo crea sin `origin`).
- **Push incremental cableado** (ADR 0085 dec.5): `_commit_and_push_worktree` invoca `push_branch_to_remote`
  según `branch_push_mode` del proyecto; `final_only` sigue difiriendo al cierre.
- **PR persistido**: nuevas columnas `Plan.pr_url` + `Plan.pr_branch`; la task de auto-PR las escribe (deja de
  ser fire-and-forget para el resultado).
- **P8 (inerte)**: se pasa la política real por consistencia, sin cambio de comportamiento (no es prioritario).

## Tareas

### Fase A — Fuente única de identidad git (P1 + P2)

- [x] **T1 — Función canónica de identidad git** (implementado en `workers/plan_git.py`; test
      `tests/unit/test_plan_git_identity.py`, 4 casos verde): `plan_git_identity(plan_id, plan_slug,
project_slug) → PlanGitIdentity(project_slug, plan_branch)`, derivando SIEMPRE de `project.slug` (bare) y
      `plan.slug` (rama, vía `make_plan_branch_name`). El test pinea que coincide con la derivación de ejecución
      y diverge de la antigua del auto-PR (`_slugify("Plan: "+title)` / `basename(url)`), incl. no-ASCII/>60.
- [ ] **T2 — Cablear los tres call-sites a T1**: `execution.py` (provision/commit), `plan_pr.py` (auto-PR) y
      `repo_clone.py` (clone) llaman a `plan_git_identity`; se elimina `_slugify(title)` de `plan_pr.py:65` y la
      derivación por `basename(url)` del bare. El bare de ejecución se crea **con `remote_url`** (hoy `ensure_repo`
      sin origin, `execution.py:886`). **Test:** grep-guard de CI que falla si `_slugify(`/`_repo_name_from_url(`
      reaparecen en el cálculo de bare/rama fuera de T1; e2e: commit de tarea + auto-PR apuntan al mismo bare.

### Fase B — Push al remoto y persistencia del PR (P3 + P6)

- [ ] **T3 — Push incremental cableado**: `_commit_and_push_worktree` invoca `push_branch_to_remote` según
      `branch_push_mode` (incremental → push por tarea aceptada; final_only → difiere). Best-effort con log
      estructurado; sin romper proyectos locales (sin `remote_url` → no-op silencioso). **Test:** con remoto
      fake + `incremental`, tras aceptar una tarea la rama existe en `origin`; con `final_only` no, hasta el
      cierre.
- [ ] **T4 — Persistir el PR**: migración Alembic reversible `Plan.pr_url TEXT NULL` + `Plan.pr_branch VARCHAR
NULL`; `_open_plan_pr_async` escribe ambos (y `pr_error` si falla, dejando de tragarse el fallo silencioso
      de `plan_pr.py:138-140`). Exponer en el schema de `GET /plans/{id}` y en la ficha de plan del admin-panel.
      **Test:** cierre de plan con remoto fake → `plan.pr_url` poblado y visible por API; con opener que
      erroriza → `plan.pr_error` poblado, `pr_url` NULL.

### Fase C — Políticas y re-sync (P4 + P5 + P8)

- [ ] **T5 — Decidir el «merge directo» (P4)**: o bien **cablear** `apply_push_policy` en `open_plan_pr` cuando
      `push_policy=='direct_to_default_allowed'` (fast-forward del default branch tras abrir/mergear), o bien
      **retirar** la opción de la UI (`git-config-section.tsx:254`) para no ofrecer un comportamiento que no
      existe. Recomendación: retirar en este plan y remitir el merge-directo real al **ADR 0098** (decisión de
      producto). **Test:** seleccionar la política elegida produce el comportamiento anunciado (o la opción no
      se ofrece); test de que `apply_push_policy` no es código muerto (tiene caller o no existe).
- [ ] **T6 — Re-sync del remoto (P5)**: como mínimo un endpoint/botón **«Sincronizar»** dedicado
      (`POST /projects/{id}/git/sync` → `enqueue fetch_remote`) y **corregir el docstring** de `fetch_remote`
      (hoy promete beat+webhook inexistentes). El beat periódico y el webhook receiver con verificación de firma
      → **ADR 0098** (gated). **Test:** el endpoint encola el fetch y actualiza el bare; el docstring ya no
      miente.
- [ ] **T7 — P8 higiene (opcional, bajo)**: `_commit_and_push_worktree` recibe las políticas reales del
      proyecto por consistencia con `plan_pr.py`. Sin cambio de comportamiento (documentado). **Test:** el valor
      pasado es el de `worker_config.git_policies`; `push_review_to_bare` sigue siendo policy-agnóstico.

### Fase D — Conflictos (P7, mínimo)

- [ ] **T8 — `abort_code` de conflicto distinto + escalado**: `_commit_and_push_worktree` distingue el
      `GitCommandError` de conflicto de rebase (`plan_git.py:284-287`) de otros fallos git y sella un
      `abort_code='rebase_conflict'` que SÍ entra en `_REVIEW_ESCALATION_ABORT_CODES` (`plans.py:892`) → aparece
      en el panel de escaladas con contexto. El visor de diffs + resolución en UI → **ADR 0099**. **Test:** un
      push conflictivo produce `abort_code='rebase_conflict'` y la tarea aparece escalada (no como `commit_failed`
      genérico silenciado).

### Fase E — Verificación e2e

- [ ] **T9 — e2e de cierre**: cerrar un plan de un proyecto con **remoto fake** (git daemon local o bare
      `file://`) y verificar: (a) la rama del PR **contiene** los commits de las tareas (`PR.head` == rama con
      commits), (b) `plan.pr_url` poblado, (c) con `incremental` la rama existe en el remoto antes del cierre.
      **Test:** integración e2e reproducible en CI sin red externa.

## Criterios de cierre

1. Checkboxes en `[x]` con test automático en verde.
2. e2e (T9): un plan cerrado abre un PR **contra la rama que contiene sus commits**, en el bare correcto, con
   `pr_url` persistido y visible en la ficha de plan.
3. Guard-test estático de identidad git activo en CI (T1/T2).
4. `apply_push_policy` con caller o retirado (sin código muerto que la UI ofrezca — T5).
5. Docstring de `fetch_remote` veraz + botón «Sincronizar» funcional (T6).
6. ADR 0098 (política push/PR/re-sync) y ADR 0099 (visor diffs + conflictos) listados como candidatos en el
   informe de auditoría; **no se redactan aquí** (esperan decisión del operador).
