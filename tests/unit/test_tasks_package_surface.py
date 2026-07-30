"""Characterization of the ``workers.tasks`` surface (refactor tasks-split).

Same contract pin as ``test_maintenance_package_surface``: the module became a
package of focused submodules and this fixes the TWO things the split must
preserve — the registered Celery task NAMES (the wire contract the
orchestrator/api-server enqueue by string) and the façade's importable
surface (tests + ``workers.execution._run_task_tests`` import from here).
"""

from __future__ import annotations

_TASK_NAMES = (
    "workers.run_agent_container",
    "workers.run_execution",
    "workers.run_test_runtime",
    "workers.run_stack_command",
    "workers.compose_review_runtime",
)

_FACADE_SYMBOLS = (
    "run_agent_container",
    "run_execution",
    "run_test_runtime",
    "run_stack_command",
    "compose_review_runtime",
    "tenant_cap_exceeded",
    "_stack_command_allowed",
    "_finalize_soft_timeout",
    "_run_execution",
    "_run_test_runtime",  # workers.execution._run_task_tests lo importa lazy
    "_run_stack_command",
)


def test_all_wire_task_names_stay_registered() -> None:
    import workers.tasks  # noqa: F401  (import-time @app.task registration)
    from workers.celery_app import app

    missing = [name for name in _TASK_NAMES if name not in app.tasks]
    assert missing == []


def test_facade_exports_survive_the_split() -> None:
    import workers.tasks as m

    missing = [symbol for symbol in _FACADE_SYMBOLS if not hasattr(m, symbol)]
    assert missing == []
