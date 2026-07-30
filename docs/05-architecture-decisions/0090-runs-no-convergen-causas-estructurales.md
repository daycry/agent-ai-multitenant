---
adr: "0090"
title: Causas estructurales de no-convergencia de runs — anti-cuelgue del worker, guard de elegibilidad y capacidad de borrado
status: accepted
date: 2026-06-28
deciders: operador, System Architect (claude-opus)
phase: remediacion-ciclo-vida-ejecucion
related: ["0085", "0087", "0088", "0089"]
docs_language: es
---

# ADR 0090 — Causas estructurales de no-convergencia de runs

## Contexto

Tras la convergencia del loop (ADR 0089), la monitorización **en vivo** de runs reales (sonnet y
opus en paralelo) probó que la no-convergencia **no era calidad del modelo**: el self-review
funciona, el feedback llega y el escalado preserva el trabajo. Quedaban **tres causas estructurales**
independientes, ajenas al modelo:

1. **Un runtime que muere al arrancar colgaba el worker ENTERO.** El contenedor agent-runtime moría
   en el arranque y, por `--rm`, desaparecía; el worker quedaba en un bucle de polling
   `GET /containers/<id>/json → 404` (cada ~250 ms) → ForkPoolWorker bloqueado, run clavado en
   `running`, y el kanban→`blocked` sin efecto (el reconciler no podía correr). CUALQUIER crash de
   runtime colgaba el worker.
2. **Una tarea `blocked`/`cancelled` recibía runtime igualmente.** `conduct_execution` validaba
   task↔tenant pero **NO** `task.status` antes de crear la `running` execution y lanzar el
   contenedor. Una re-entrega de Celery (`acks_late`, p.ej. tras reiniciar el worker) **re-lanzaba el
   runtime aunque el operador hubiera movido la tarea a `blocked`** → "docker fantasma".
3. **El agente no tenía CERO capacidad de borrar ficheros.** El worktree es por-tarea y persiste
   entre ejecuciones (ADR 0085); un run heredaba duplicados en conflicto de intentos previos. El
   agente intentaba reconciliar con `rm` (→ `command not allowed`, allowlist vacío) y con
   `apply_patch` a `/dev/null` (→ `unknown tool`). Solo podía sobrescribir, no eliminar → no podía
   resolver la duplicación heredada (confirmado con opus, más capaz, atascado en el MISMO muro).

## Decisiones

### R1 — Un runtime desaparecido es TERMINAL, no se sondea hasta el budget

`AgentContainerRunner._await_exit` trata `docker.errors.NotFound` (404 del daemon) como salida
**terminal inmediata** (no un timeout de wall-clock): el contenedor se esfumó/crasheó. `_capture`
tolera el 404 devolviendo un `ContainerResult(exit_code=-1, logs="")` para que el run finalice
`failed` ("exited with no result") en vez de propagar la excepción y matar el hilo del worker. Así
**cualquier** crash de runtime libera el worker en vez de colgarlo. (`apps/workers/.../container.py`)

### R5 — Guard de elegibilidad ANTES de crear la execution / lanzar el contenedor

`conduct_execution` arranca con un guard: si `task.status` no está en el estado lanzable
(`in_progress` para implementer, `in_review` para reviewer) → `ack` + return no-op
(`status="skipped"`, `abort_code="ineligible_task_status"`), **sin** crear execution ni lanzar
contenedor. Cierra el bucle `restart → re-deliver → re-launch` y el "docker fantasma" sobre tareas
`blocked`/`cancelled`. Espeja el guard que ya existía en los `_apply_*` pero **antes** de lanzar.
(`apps/workers/.../execution.py`)

### R6 — Capacidad de borrado segura (`delete_file`), sin abrir `rm` por shell

Nuevo builtin `delete_file` acotado al `/workspace` (path-jailed, sin `..`, rechaza directorios),
reusando el path-jail de las demás file-tools — NO se abre `rm` por shell. Cableado en las cuatro
fuentes de verdad (catálogo `builtin_tools.py`, `builtin_families.py`, `tool_names.py`
`RUNTIME_WIRED_TOOL_NAMES`, grants del equipo CI4) y concedido al agente implementer. Permite
reconciliar el deliverable eliminando ficheros stale/duplicados de intentos previos.
(`docker/agent-runtimes/.../file_tools.py`, `builtin_families.py`, `shared-domain/.../tool_names.py`,
`apps/api-server/.../seeds/`)

## Invariantes preservadas

- **Aislamiento del contenedor (CLAUDE.md principio 2) intacto**: `delete_file` está path-jailed al
  workspace; NO se habilita `rm` ni shell ampliado por este ADR.
- **`apply_patch`** sigue sin estar cableado: se **retira** de los grants/anuncios en vez de fingir
  que existe (se reemplazó por `delete_file` en el equipo CI4, net-zero).
- La **terminación** del run sigue garantizada por `max_iterations`/`wall_clock`; R1 solo evita el
  cuelgue ante un runtime desaparecido.

## Validación

Demo en vivo (opus sobre la tarea JWT, run `019f127f`): R5 saltó una re-entrega de Celery sobre
tarea `blocked` (`ineligible_task_skipped` → `skipped`, sin docker fantasma); el run finalizó limpio
en 744 s sin colgar el worker (R1); el escalado preservó y commiteó el deliverable
(`worktree_committed escalated=True`, ADR 0087/0089). El demo además expuso un **read-churn tras
self-review fallido** no cubierto por estos fixes → se aborda como addendum D4 de ADR 0089.

## Trazabilidad

Monitorización y plan en `~/.claude/plans`; memoria de sesión
`runs-no-convergen-causas-estructurales`. Implementación commiteada en `plan/runs-visor-trabajo`
(R1/R5/R6 + tests unit/integration).
