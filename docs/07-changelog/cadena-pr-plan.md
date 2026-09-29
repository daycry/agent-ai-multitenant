---
plan_id: cadena-pr-plan
title: Cadena auto-PR del cierre de plan — identidad git de fuente única, push incremental y persistencia del PR
started_at: 2026-07-04
completed_at: null
status: pending_human_validation
tasks_done: 9
tasks_total: 9
docs_language: es
---

> **Estado: entregado, pendiente de validación humana.** Las 9 casillas están
> marcadas desde el 2026-07-18 y el plan seguía etiquetado `pending_approval`
> («no empezado»). Esta entrada se escribe el 2026-09-29, en la consolidación
> del roadmap, tras volver a ejecutar sus tests declarados.

# Changelog — cadena-pr-plan · Auto-PR del cierre de plan

Sale de la [auditoría de plataforma del 2026-07-03](../roadmap/auditoria-plataforma-2026-07-03.md).
Su hallazgo de raíz: la identidad git de un plan —su rama y su bare repo— se
calculaba en **tres sitios con tres reglas distintas**. La ejecución escribía en
`plan.slug` y `project.slug`; el auto-PR y el clone derivaban la rama del título
(«Plan: …», con prefijo extra) y el bare del basename de la URL remota. Resultado:
el PR se abría contra una rama que no contenía los commits del plan.

## Qué cambió

- **Fuente única de identidad (P1, P2).** `plan_git_identity` en
  `workers/plan_git.py` es la única derivación de `(bare, rama)`. Ejecución,
  clone y auto-PR la llaman; los helpers antiguos de `repo_clone.py`
  (`_slugify`, `_repo_name_from_url`) se eliminaron para que no puedan volver.
- **Push incremental (P3).** Con `branch_push_mode=incremental`, el default,
  cada tarea empuja su commit al remoto tras escribirlo en el bare. Es
  best-effort: un fallo de push no rompe la tarea. `final_only` sigue difiriendo
  al cierre.
- **PR persistido (P6).** Migración `0102_plan_pr_url`: `plans.pr_url`,
  `pr_branch` y `pr_error`. El resultado del auto-PR deja de existir sólo en los
  logs del worker y viaja en `GET /plans`.
- **«Merge directo» retirado de la UI (P4).** Se comportaba igual que «Abrir PR»
  porque `apply_push_policy` no tenía caller productivo. El valor sigue siendo
  válido en el enum por compatibilidad. El merge directo real quedó en el ADR 0098.
- **Re-sync manual del remoto (P5).** `POST /projects/{id}/git/sync` encola un
  `git fetch --prune` idempotente. El docstring de `fetch_remote` dejó de
  prometer un beat y un webhook que no existían.
- **Conflicto de rebase distinguible (P7 mínimo).** `abort_code=rebase_conflict`
  en vez del genérico `commit_failed`, y entra en el panel de escaladas.

## Lo que NO entra

- **Visor de diffs y resolución de conflictos en UI.** Decisión de producto,
  llevada al ADR 0099.
- **Re-sync periódico o por webhook firmado.** Llevado al ADR 0098.
- **Botón «Sincronizar» en la ficha del proyecto.** El endpoint existe; el botón
  quedó anotado como pendiente menor en `task T6` del plan.

## Verificación (2026-09-29)

Los cinco ficheros de test que declara el plan, ejecutados contra el Postgres de
desarrollo:

```text
tests/unit/test_plan_git_identity.py
tests/unit/test_commit_abort_code.py
tests/integration/test_incremental_remote_push.py
tests/integration/test_project_git_sync.py
tests/integration/test_plan_close_e2e.py
15 passed in 128.44s
```

El e2e de cierre (`test_plan_close_e2e.py`) usa un remoto `file://`, así que no
prueba un proveedor git real. Eso es el test humano del plan.
