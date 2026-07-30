---
title: "Tarea SIEMPRE bloqueada: commit_failed por push non-fast-forward a la rama del plan"
area: git
encountered: 2026-06-30
stack: git · plan_git · execution · ADR 0085
---

## Síntoma

Una tarea (p.ej. "Auditar dependencias y fijar versiones") acaba **siempre `blocked`**.
En los logs del worker:

```
workers.worktree_commit_failed
  git push <bare> HEAD:refs/heads/plan/<id>-<slug> failed (rc=1):
  ! [rejected]  HEAD -> plan/...  (non-fast-forward)
  error: failed to push some refs ... Note about fast-forwards ... use 'git pull'
```

El agente además intenta `stack_exec("git …")` y recibe `command not allowed: git`.

## Causa raíz (fallo compuesto)

1. **El disparador — race de push.** Todas las tareas de un mismo plan comitean a la
   **misma rama** `plan/{id}-{slug}` (ADR 0085). Cada worktree se creó desde el HEAD del
   plan en su momento; cuando una tarea hermana empuja primero, la rama avanza y el push de
   esta tarea queda **non-fast-forward → rechazado**. `_commit_and_push_worktree` lo marca
   como `commit_failed` (el entregable no llega a la rama).
2. **La cascada.** El reviewer ve que no hay entregable persistido → **rechaza**
   ("reintentar el commit/push…"). Pero el commit lo hace el **worker** (el sandbox no tiene
   credenciales git, principio 2) y `git` está **denegado** en `stack_exec`. El agente no
   puede hacer lo que el reviewer le pide → re-corre su toolchain en bucle →
   `repetitive_loop_detected` → escala → **`blocked`**. Reproducible: esa tarea casi siempre
   pierde la carrera del push.

## Fix

`PlanGitWorkflow.push_review_to_bare` (`apps/workers/src/workers/plan_git.py`) **reconcilia**
en vez de un push plano: ante un rechazo non-fast-forward, hace
`git fetch <bare> <branch>` + `git rebase FETCH_HEAD` (replay del commit de la tarea sobre el
tip actual) y reintenta — concurrencia optimista, varios reintentos. Un **conflicto real** de
rebase (dos tareas tocan las mismas líneas) NO es una carrera: se re-lanza como
`GitCommandError` para que escale a resolución (no se traga el conflicto).

## Cómo verificar

```bash
# tests/integration/test_push_worktree_to_bare.py:
#   test_push_reconciles_concurrent_sibling_commit  -> B rebasa sobre A y empuja (sin non-ff)
#   test_push_raises_on_real_rebase_conflict         -> conflicto real -> GitCommandError
pytest tests/integration/test_push_worktree_to_bare.py -q
```

## Relacionado

- `ADR 0085` — un bare por proyecto, worktrees por tarea, rama por plan.
- Follow-up (no incluido aquí): el reviewer no debería rechazar por un `commit_failed` de
  infra (no es culpa del agente) ni decirle "reintenta el commit" (lo hace el worker); y el
  agente no debería intentar `git` por `stack_exec`.
