---
adr_id: "0085"
title: "Worktree en la ejecución: slugs estables, repo por proyecto y quién commitea"
status: accepted
date: 2026-06-26
decided_at: 2026-06-26
authors: [claude-opus]
plan_referenced: prod-18-worktree-en-ejecucion
docs_language: es
related: ["0072", "0063"]
supersedes: []
---

# ADR 0085 — Worktree en la ejecución del agente: decisiones de modelo

> **Estado: `accepted`** — decisiones de la Fase A de `prod-18-worktree-en-ejecucion`,
> aprobadas por el operador (2026-06-26, "adelante con la recomendación"). El cableado
> del worktree en el sandbox (Fase B+, sensible a seguridad — principio 2) las consume.

## Contexto

Las bibliotecas de git-worktrees (`workers.git_repos`, `workers.plan_git`) existen y
están testeadas (Plan 06 `completed`), pero `conduct_execution` no las usa: el agente
corre en `/workspace` tmpfs efímero y nada persiste. `BareRepoLayout` exige **slugs
estables** para los paths (`{data_root}/projects/{tenant_slug}/{project_slug}/...`,
"nunca UUIDs") y `make_plan_branch_name` un slug del plan; pero `projects`/`plans` **no
tienen columna `slug`** hoy, y **no hay relación Task→repo**. ADR 0072 dejó esto como
pendiente explícito ("pipeline de ejecución-git, plan propio").

## Decisiones

1. **Slugs estables persistidos**: nuevas columnas `projects.slug` y `plans.slug`
   (String, indexadas por tenant). Se generan UNA vez al crear (slugify kebab del
   nombre/título + sufijo corto del id para unicidad) y **no cambian** aunque el
   nombre cambie — así el worktree/rama de una entidad no se huerfaniza al renombrar.
   Backfill de filas existentes en la migración (SQL `regexp_replace`). Helper
   `api_server.slug.slugify` (kebab `[a-z0-9-]`), distinto de `normalize_tool_name`
   (que usa `_` y preserva el punto para el namespacing MCP).
2. **`repo_name` por proyecto (MVP un repo)**: hoy no hay multi-repo. Convención
   `repo_name = project.slug` (un bare repo por proyecto). Se deja el hook para una FK
   `Task.repo_name` / `Project.primary_repo` el día que haya multi-repo; no se añade
   FK ahora (YAGNI).
3. **Granularidad: worktree por tarea** (`worktrees/{task_id}`, HEAD detached
   compartiendo la rama del plan), consolidado a la rama del plan vía
   `push_review_to_bare`. Es lo que las bibliotecas ya asumen y testean.
4. **Commitea el WORKER, no el sandbox**: el agent-runtime solo escribe ficheros en
   `/workspace` (`write_file`, confinado); el worker lee el worktree y hace
   `git add/commit` con los trailers obligatorios `Plan-Id`/`Task-Id`/`Execution-Id`.
   El sandbox NO tiene credenciales git ni acceso al bare (principio 2).
5. **Cadencia de push**: reusar `PlanGitPolicies.branch_push_mode`
   (`incremental`/`final_only`), ya tipado en `worker_config`. `push_review_to_bare`
   (worktree→bare) siempre tras el commit; `push_branch_to_remote` según el modo.
6. **Bind RW para el implementador**: el worktree del agente implementador se monta
   read-write (escribe código); el del review-runtime (ADR 0063, fuera de prod-18) iría
   read-only. Path absoluto del host idéntico host/daemon (DooD; bind `{data_root}:{data_root}`,
   nunca volumen nombrado).

## Consecuencias

- **Fase A (esta)**: migración reversible `0099` (añade `projects.slug` + `plans.slug`,
  backfill), modelos, helper `slugify`, población en los create-paths de project/plan.
- **Fase B+**: `conduct_execution` resuelve el worktree (slugs de project/plan + task_id)
  y fija `ContainerSpec.workspace_host_path`; el worker commitea + empuja; se encadena el
  test-runtime. Sensible a seguridad (montaje en el sandbox) — se trata con cuidado.
- Un proyecto/plan sin slug (no debería tras el backfill) cae al comportamiento actual
  (tmpfs) sin romper — degradación segura.
