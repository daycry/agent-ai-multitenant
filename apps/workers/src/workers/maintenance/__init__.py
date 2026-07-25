"""Periodic maintenance beat tasks (Plan 06.5 Fase D + prod-06 + audit C3).

Package façade: each beat task lives in its own focused submodule; importing
the package (Celery's ``imports=("workers.maintenance", ...)`` does it at boot)
imports every submodule so all ``@app.task`` registrations fire. The submodules:

  * :mod:`~workers.maintenance.cleanup`           — ``workers.idle_sweep_pools`` (30s),
    ``workers.purge_dep_cache`` (daily 03:00), ``workers.prune_worktrees`` (daily 03:30)
  * :mod:`~workers.maintenance.review_runtimes`   — ``workers.expire_review_runtimes`` (5 min)
  * :mod:`~workers.maintenance.memory_backfill`   — ``workers.backfill_memory_embeddings``
  * :mod:`~workers.maintenance.dag_promotion_beat`— ``workers.promote_ready_plans`` (30s)
  * :mod:`~workers.maintenance.stale_sweeper`     — ``workers.sweep_stale_executions`` (5 min)
  * :mod:`~workers.maintenance.budget_sweep`      — ``workers.refresh_budgets`` (5 min)
  * :mod:`~workers.maintenance.queue_sampler`     — ``workers.sample_queue_metrics`` (30s)
  * :mod:`~workers.maintenance.reconciler`        — ``workers.reconcile_pipeline_state`` (90s)
  * :mod:`~workers.maintenance.worktree_backfill` — pass (d) of the reconciler (M4)

These are best-effort jobs — a single failure must not crash beat itself. Each
task catches its own exceptions and logs them; the beat scheduler keeps firing
on its cadence regardless.

The re-exports below are the package's public surface (external consumers +
tests import from ``workers.maintenance``). NOTE for tests: ``monkeypatch`` a
symbol on the SUBMODULE that looks it up (e.g. ``workers.maintenance.reconciler``),
not on this façade — rebinding the façade attribute doesn't affect the
submodule's own global.
"""

from __future__ import annotations

from workers.maintenance.budget_sweep import _refresh_budgets_async, refresh_budgets
from workers.maintenance.chunk_backfill import (
    _backfill_chunk_embeddings_async,
    backfill_chunk_embeddings,
)
from workers.maintenance.cleanup import idle_sweep_pools, prune_worktrees, purge_dep_cache
from workers.maintenance.dag_promotion_beat import _promote_ready_plans_async, promote_ready_plans
from workers.maintenance.memory_backfill import (
    EmbedderFactory,
    _backfill_memory_embeddings_async,
    _default_embedder_factory,
    backfill_memory_embeddings,
)
from workers.maintenance.orphan_reaper import _reap_orphans_async, reap_orphans
from workers.maintenance.queue_sampler import _sample_queue_metrics_async, sample_queue_metrics
from workers.maintenance.reconciler import (
    _RECONCILE_REVIEW_MAX_STUCK,
    _RECONCILE_REVIEW_MIN_AGE,
    _RECONCILE_STUCK_TASK_MIN_AGE,
    _autostart_review_runtime,
    _orphan_claim_needs_revert,
    _orphan_review_needs_reannounce,
    _orphan_review_should_escalate,
    _reconcile_complete_plans,
    _reconcile_orphan_reviews,
    _reconcile_pipeline_state_async,
    _reconcile_stuck_tasks,
    _stuck_task_needs_reconcile,
    reconcile_pipeline_state,
)
from workers.maintenance.review_runtimes import (
    _expire_review_runtimes,
    expire_review_runtimes,
    plan_status_after_expiry,
)
from workers.maintenance.stale_sweeper import (
    _remove_exited_terminal_containers,
    _sweep_stale_executions_async,
    sweep_stale_executions,
)
from workers.maintenance.worktree_backfill import (
    _backfill_worktree_to_bare,
    _reconcile_unpushed_worktrees,
)

__all__ = [
    "EmbedderFactory",
    "reap_orphans",
    "_reap_orphans_async",
    "backfill_memory_embeddings",
    "expire_review_runtimes",
    "idle_sweep_pools",
    "plan_status_after_expiry",
    "promote_ready_plans",
    "prune_worktrees",
    "purge_dep_cache",
    "reconcile_pipeline_state",
    "refresh_budgets",
    "sample_queue_metrics",
    "sweep_stale_executions",
    # Test-visible internals (grep'd external consumers).
    "_RECONCILE_REVIEW_MAX_STUCK",
    "_RECONCILE_REVIEW_MIN_AGE",
    "_RECONCILE_STUCK_TASK_MIN_AGE",
    "_autostart_review_runtime",
    "_backfill_chunk_embeddings_async",
    "_backfill_memory_embeddings_async",
    "backfill_chunk_embeddings",
    "_backfill_worktree_to_bare",
    "_default_embedder_factory",
    "_expire_review_runtimes",
    "_orphan_claim_needs_revert",
    "_orphan_review_needs_reannounce",
    "_orphan_review_should_escalate",
    "_promote_ready_plans_async",
    "_reconcile_complete_plans",
    "_reconcile_orphan_reviews",
    "_reconcile_pipeline_state_async",
    "_reconcile_stuck_tasks",
    "_reconcile_unpushed_worktrees",
    "_refresh_budgets_async",
    "_remove_exited_terminal_containers",
    "_sample_queue_metrics_async",
    "_stuck_task_needs_reconcile",
    "_sweep_stale_executions_async",
]
