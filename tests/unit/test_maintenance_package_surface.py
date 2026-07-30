"""Characterization of the ``workers.maintenance`` surface (refactor P1).

The maintenance junk-drawer (10 beat tasks in one 1.6k-line module) becomes a
package of focused submodules. This pins the TWO contracts the split must
preserve, BEFORE and AFTER:

1. Every beat task keeps its registered Celery name — the string is the wire
   contract ``beat_schedule.py`` fires by name (moving a ``@app.task`` without
   its module being imported at boot would silently unregister it).
2. The package façade keeps exporting every symbol external consumers import
   (other workers modules, integration tests, demo scripts).
"""

from __future__ import annotations

# The beat tasks the module registers (mirrors beat_schedule.py).
_BEAT_TASK_NAMES = (
    "workers.idle_sweep_pools",
    "workers.reap_orphans",
    "workers.expire_review_runtimes",
    "workers.purge_dep_cache",
    "workers.prune_worktrees",
    "workers.backfill_memory_embeddings",
    "workers.promote_ready_plans",
    "workers.sweep_stale_executions",
    "workers.refresh_budgets",
    "workers.sample_queue_metrics",
    "workers.reconcile_pipeline_state",
)

# Symbols imported from ``workers.maintenance`` elsewhere in the repo (grep'd):
# production code, integration tests and unit tests. The façade must keep them.
_FACADE_SYMBOLS = (
    # review-runtime expiry
    "plan_status_after_expiry",
    "_expire_review_runtimes",
    # memory back-fill
    "_backfill_memory_embeddings_async",
    "EmbedderFactory",
    # DAG promotion safety net
    "_promote_ready_plans_async",
    # zombie sweeper
    "_sweep_stale_executions_async",
    # budgets sweep
    "_refresh_budgets_async",
    # queue metrics sampler
    "_sample_queue_metrics_async",
    # convergence reconciler (passes + pure helpers + core)
    "_reconcile_pipeline_state_async",
    "_reconcile_stuck_tasks",
    "_reconcile_orphan_reviews",
    "_reconcile_complete_plans",
    "_reconcile_unpushed_worktrees",
    "_backfill_worktree_to_bare",
    "_autostart_review_runtime",
    "_stuck_task_needs_reconcile",
    "_orphan_review_needs_reannounce",
    "_orphan_review_should_escalate",
    "_RECONCILE_STUCK_TASK_MIN_AGE",
    "_RECONCILE_REVIEW_MIN_AGE",
    "_RECONCILE_REVIEW_MAX_STUCK",
)


def test_all_maintenance_beat_tasks_stay_registered() -> None:
    """Importing the package registers every beat task under its wire name."""
    import workers.maintenance  # noqa: F401  (import-time @app.task registration)
    from workers.celery_app import app

    missing = [name for name in _BEAT_TASK_NAMES if name not in app.tasks]
    assert missing == []


def test_facade_exports_survive_the_split() -> None:
    """Every externally-imported symbol stays importable from the façade."""
    import workers.maintenance as m

    missing = [symbol for symbol in _FACADE_SYMBOLS if not hasattr(m, symbol)]
    assert missing == []
