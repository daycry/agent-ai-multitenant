---
name: fix-code-diff-500-delegar-worker
description: 'Fix del visor de "diff de código" que daba 500 SIEMPRE — la api-server no monta el volumen agent-data; git/data ops deben delegarse al worker'
metadata:
  node_type: memory
  type: project
  originSessionId: ed356da1-3ffb-49dc-a846-642abace2f05
  modified: 2026-07-24T20:05:57.659Z
---

Bug (2026-07-24): el apartado «Diff de código de la rama» en la ficha de plan (`GET /projects/{pid}/plans/{planId}/code-diff`, componente `plan-code-diff-section.tsx`) devolvía **500 SIEMPRE**.

**Causa raíz**: el endpoint calculaba el diff ejecutando git EN EL PROCESO de la api-server (`api_server.code_diff.plan_code_diff` vía `run_in_threadpool`). Pero **la api-server NO monta el volumen `agentic-platform-agent-data`** y su `data_root` es el default `/data/agent-platform` (inexistente en su contenedor) → `subprocess.run(cwd=<bare inexistente>)` lanza `FileNotFoundError` (OSError, cwd ausente), que el `except GitCommandError` NO capturaba → 500 no controlado.

**GOTCHA reutilizable**: solo el **worker** ve los bares (`WORKERS_DATA_ROOT=/var/lib/docker/volumes/agentic-platform-agent-data/_data`, mount identidad DooD) y corre como `app` (owner de los bares). La api-server NO tiene ese volumen. **Cualquier operación git/FS sobre `/data` debe delegarse al worker** (celery), nunca ejecutarse en la api-server. `docker exec` como root sobre un bare da «dubious ownership»; el proceso celery del worker corre como `app` (setpriv) y no.

**Fix (commit `a0cb208a`, rama plan/runs-visor-trabajo, EMPUJADO):**

- Nueva task `workers.compute_plan_code_diff` (`apps/workers/src/workers/tasks/code_diff_task.py`, registrada en `tasks/__init__.py`) que calcula `plan_code_diff` con el `data_root` real del worker y devuelve el diff serializado `{ok, plan_branch, default_branch, files, lines, ...}` o `{ok: False, error}`.
- `celery_client.compute_plan_code_diff_and_wait(...)` — patrón síncrono send_task+`.get(timeout)` como `run_stack_command_and_wait`, cola `default`; broker caído/timeout → `{ok: False}`.
- Endpoint `get_plan_code_diff` delega a ese helper y relaya; `ok:False` → 404 neutro.
- Endurecido `plan_code_diff`: `_safe_git_ref` entra al `try` y el `except` captura `(GitCommandError, OSError, ValueError)` → `PlanCodeDiffError` (bare no materializado o ref mala → 404, nunca 500).

**Verificado e2e 2026-07-24**: `compute_plan_code_diff_and_wait` desde la api-server (plan CI4 019f8e47) → `ok:True, default=master, 107 ficheros, 9476 líneas`. Desplegado: rebuild `api-server:manuals` + `workers:ci` (sobre esa base) + recreate (4/4 healthy). TDD: `tests/unit/test_plan_code_diff.py::test_missing_bare_is_a_clean_error_not_500` (regresión). No tocado el orquestador (no interviene). Relacionado: [[data-root-en-volumen-durable]], [[stack-exec-feature]].
