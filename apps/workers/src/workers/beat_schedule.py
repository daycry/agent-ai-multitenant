"""Celery beat schedule (Plan 06.5 Fase D — task_06_5_13).

Wires the four maintenance tasks of `workers.maintenance` to Celery
beat with the cadences mandated by Plan 06.5:

    idle_sweep_pools         every 30 seconds
    expire_review_runtimes   every 5 minutes
    purge_dep_cache          daily at 03:00 UTC
    prune_worktrees          daily at 03:30 UTC

Plan 11 task_11_18 adds a CONFIGURABLE scheduled price-catalog sync
(`workers.sync_model_prices`); its cadence comes from
`Settings.price_sync_cron` (default daily 04:00 UTC) rather than a
hardcoded magic schedule, and its live enable/disable is the
`price_sync_enabled` platform setting a System Admin owns.

Activated by `apps/workers/__main__.py` (or `celery -A workers.celery_app
beat`) — `build_celery_app` reads this schedule when running with the
``beat`` role. The constants are exported so tests can introspect them.
"""

from __future__ import annotations

from celery.schedules import crontab, schedule

from workers.config import Settings, get_settings

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

# Plan 11 task_11_18: the scheduled price-catalog sync entry name. Kept as a
# constant so tests + ops can reference it without hardcoding the string.
PRICE_SYNC_BEAT_ENTRY = "sync-model-prices"

# Plan 12 task_12_01/12_04: the scheduled daily-backup entry name. Same
# constant-not-hardcoded-string discipline as the price-sync entry.
BACKUP_BEAT_ENTRY = "run-daily-backup"


def _parse_cron(expr: str) -> crontab:
    """Parse a 5-field cron string (minute hour dom month dow) to a crontab.

    Falls back to daily 04:00 UTC on a malformed expression so a typo in the
    operator's ``WORKERS_PRICE_SYNC_CRON`` never crashes beat boot.
    """
    parts = expr.split()
    if len(parts) != 5:
        return crontab(minute="0", hour="4")
    minute, hour, dom, month, dow = parts
    return crontab(
        minute=minute,
        hour=hour,
        day_of_month=dom,
        month_of_year=month,
        day_of_week=dow,
    )


def build_beat_schedule(settings: Settings | None = None) -> dict[str, dict[str, object]]:
    """The full beat schedule, including the CONFIGURABLE price-sync entry.

    `build_celery_app` calls this so the scheduled price-catalog sync
    (task_11_18) picks up its cadence from ``Settings.price_sync_cron`` rather
    than a hardcoded schedule. The static maintenance entries are unchanged.
    The price-sync run is pinned to the `default` queue (a cheap HTTP fetch +
    a handful of catalog writes — no infra side-effects).
    """
    cfg = settings or get_settings()
    sched: dict[str, dict[str, object]] = dict(BEAT_SCHEDULE)
    sched[PRICE_SYNC_BEAT_ENTRY] = {
        "task": "workers.sync_model_prices",
        "schedule": _parse_cron(cfg.price_sync_cron),
        "options": {"queue": "default"},
    }
    # Plan 12 task_12_01/12_04: daily full backup on a CONFIGURABLE cadence
    # (WORKERS_BACKUP_CRON, default 03:00). Pinned to the `privileged` queue —
    # it touches infra (the DB dump + the data volumes), the lane drained by a
    # worker with host-level access. Its live enable/disable is the
    # `backup_enabled` platform setting a System Admin owns.
    sched[BACKUP_BEAT_ENTRY] = {
        "task": "workers.run_daily_backup",
        "schedule": _parse_cron(cfg.backup_cron),
        "options": {"queue": "privileged"},
    }
    return sched
