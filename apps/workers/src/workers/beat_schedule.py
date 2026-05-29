"""Celery beat schedule (Plan 06.5 Fase D — task_06_5_13).

Wires the four maintenance tasks of `workers.maintenance` to Celery
beat with the cadences mandated by Plan 06.5:

    idle_sweep_pools         every 30 seconds
    expire_review_runtimes   every 5 minutes
    purge_dep_cache          daily at 03:00 UTC
    prune_worktrees          daily at 03:30 UTC

Activated by `apps/workers/__main__.py` (or `celery -A workers.celery_app
beat`) — `build_celery_app` reads this schedule when running with the
``beat`` role. The constants are exported so tests can introspect them.
"""

from __future__ import annotations

from celery.schedules import crontab, schedule

# Each schedule entry is the standard Celery shape:
# `{task: <name>, schedule: <celery.schedules.*>, options: {queue: <name>}}`.
#
# We pin queues explicitly:
#   - idle_sweep_pools     → default  (no infra side-effects beyond logging)
#   - expire_review_runtimes → review  (touches review_sessions rows)
#   - purge_dep_cache      → test    (touches the dep-cache directory the
#                                      test queue manages)
#   - prune_worktrees      → default (cross-cutting filesystem walk)
BEAT_SCHEDULE: dict[str, dict[str, object]] = {
    "idle-sweep-pools-every-30s": {
        "task": "workers.idle_sweep_pools",
        "schedule": schedule(run_every=30.0),
        "options": {"queue": "default"},
    },
    "expire-review-runtimes-every-5m": {
        "task": "workers.expire_review_runtimes",
        "schedule": schedule(run_every=300.0),
        "options": {"queue": "review"},
    },
    "purge-dep-cache-daily": {
        "task": "workers.purge_dep_cache",
        "schedule": crontab(hour="3", minute="0"),
        "options": {"queue": "test"},
    },
    "prune-worktrees-daily": {
        "task": "workers.prune_worktrees",
        "schedule": crontab(hour="3", minute="30"),
        "options": {"queue": "default"},
    },
    # Plan 06.11 — safety net: re-enqueue documents stuck in `pending`
    # (a missed enqueue, a worker crash mid-flight, or an upload while
    # the broker was down). Cheap query; runs every 2 minutes.
    "sweep-pending-documents-every-2m": {
        "task": "workers.sweep_pending_documents",
        "schedule": schedule(run_every=120.0),
        "options": {"queue": "ingestion"},
    },
}
