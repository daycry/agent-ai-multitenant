"""Celery tasks the workers execute (task_02_06, task_02_31 — refactor 2026-07-08).

Package façade: cada task Celery vive en su submódulo enfocado; importar el
paquete (Celery lo hace en el boot vía ``imports=("workers.tasks", ...)``)
importa cada submódulo y dispara los registros ``@app.task``:

  * :mod:`~workers.tasks.run_cycle`           — ``workers.run_agent_container``,
    ``workers.run_execution`` (+ run-lock A6, soft-timeout, DLQ)
  * :mod:`~workers.tasks.test_runtime_task`   — ``workers.run_test_runtime``
  * :mod:`~workers.tasks.stack_exec_task`     — ``workers.run_stack_command`` (ADR 0093)
  * :mod:`~workers.tasks.review_runtime_task` — ``workers.compose_review_runtime``

Los re-exports de abajo son la superficie pública del paquete (tests +
``workers.execution._run_task_tests`` importan de ``workers.tasks``). NOTA
para tests: ``monkeypatch`` sobre el SUBMÓDULO que resuelve el nombre (p.ej.
``workers.tasks.run_cycle``), no sobre esta façade.
"""

from __future__ import annotations

from workers.tasks.code_diff_task import compute_plan_code_diff
from workers.tasks.review_runtime_task import (
    _compose_review_runtime,
    _count_active_review_sessions,
    _notify_review_ready,
    _resolve_review_worktree_host_path,
    _spawn_review_runtime,
    compose_review_runtime,
    tenant_cap_exceeded,
)
from workers.tasks.run_cycle import (
    _DEAD_LETTER_STREAM,
    _finalize_soft_timeout,
    _push_execution_dead_letter,
    _record_execution_dead_letter,
    _run_execution,
    run_agent_container,
    run_execution,
)
from workers.tasks.stack_exec_task import (
    _STACK_EXEC_DEFAULT_TIMEOUT_S,
    _resolve_stack_dep_cache,
    _run_stack_command,
    _stack_command_allowed,
    run_stack_command,
)
from workers.tasks.test_runtime_task import (
    _launch_test_runtime_plans,
    _run_test_runtime,
    dispatch_test_runtime_and_wait,
    run_test_runtime,
    test_phase_wait_budget_s,
)

__all__ = [
    # Internos con consumidores externos (tests / workers.execution).
    "_DEAD_LETTER_STREAM",
    "_STACK_EXEC_DEFAULT_TIMEOUT_S",
    "_compose_review_runtime",
    "_count_active_review_sessions",
    "_finalize_soft_timeout",
    "_launch_test_runtime_plans",
    "_notify_review_ready",
    "_push_execution_dead_letter",
    "_record_execution_dead_letter",
    "_resolve_review_worktree_host_path",
    "_resolve_stack_dep_cache",
    "_run_execution",
    "_run_stack_command",
    "_run_test_runtime",
    "_spawn_review_runtime",
    "_stack_command_allowed",
    "compose_review_runtime",
    "compute_plan_code_diff",
    "dispatch_test_runtime_and_wait",
    "run_agent_container",
    "run_execution",
    "run_stack_command",
    "run_test_runtime",
    "tenant_cap_exceeded",
    "test_phase_wait_budget_s",
]
