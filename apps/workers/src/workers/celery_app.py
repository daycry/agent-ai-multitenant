"""Celery application + the queue topology (task_02_02).

The orchestrator enqueues work onto these queues; worker processes
(task_02_06) consume them. Each queue is a separate Celery worker
deployment so privileged / runtime work is isolated from the default
lane and can scale independently.

Queues (spec §12, Plan 02 Fase A; trimmed by ADR 0083 / prod-06 colas_02):

  default      ordinary agent runs — the common lane.
  ingestion    document ingestion pipelines (Docling — Plan 04).
  test         test-runtime execution.
  review       review-runtime execution.
  privileged   tasks touching secrets / infra — drained by a worker
               with a tighter security profile.

The ``heavy`` and ``gpu`` lanes were REMOVED (ADR 0083, Option B): they were
declared but had no producer (the dispatcher always uses ``dispatch_queue`` =
``default``) and no dedicated worker to drain them on a single host — dead
queues that promised an isolation the deployment never delivered. Reintroducing
a lane the day a GPU host or a heavy worker exists is a config change + an ADR,
not a migration. See `docs/06-runbooks/06-capacity-management.md`.

Routing: `workers.tasks` registers the tasks (task_02_06); a task
picks its queue at apply time via `apply_async(queue=...)`, and
unrouted tasks fall through to `default`.
"""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from workers.config import Settings, get_settings

# Canonical queue names. Order is informational only. ``heavy``/``gpu`` removed
# by ADR 0083 (prod-06 colas_02): dead lanes with no producer/consumer on a
# single host.
QUEUE_NAMES: tuple[str, ...] = (
    "default",
    "ingestion",
    "test",
    "review",
    "privileged",
)

DEFAULT_QUEUE = "default"

# prod-06 task_prod06_zombi_03 (decision 2): the Redis broker's per-message
# visibility window. If a task runs LONGER than this, Redis assumes the worker
# died and REDELIVERS the message — duplicating a still-live run. So this MUST
# stay strictly above the execution hard time limit. We pin it at 7h and cap the
# operator-tunable `execution_hard_time_limit_s` at 6h in the platform-settings
# registry (its `max_value`); `tests/unit/test_hard_limit_registry_validation.py`
# enforces `hard_limit.max_value < EXECUTION_VISIBILITY_TIMEOUT_S` as a cross-check.
EXECUTION_VISIBILITY_TIMEOUT_S = 25200


def build_celery_app(settings: Settings | None = None) -> Celery:
    """Construct the Celery app. `settings` is injectable for tests."""
    cfg = settings or get_settings()

    # Imported here (not at module top) to avoid Celery importing the
    # maintenance task module before its own celery_app singleton is
    # ready — `maintenance.py` registers tasks against `app`, so we
    # need the schedule attached AFTER `app` exists. `build_beat_schedule`
    # folds in the CONFIGURABLE price-sync cadence (Plan 11 task_11_18).
    from workers.beat_schedule import build_beat_schedule

    app = Celery("agentic-workers")
    app.conf.update(
        broker_url=cfg.broker_url,
        result_backend=cfg.result_backend,
        # The 7 queues.
        task_queues=tuple(Queue(name) for name in QUEUE_NAMES),
        task_default_queue=DEFAULT_QUEUE,
        # Task modules a real worker imports on boot so the tasks are
        # registered (imported lazily — no circular import at config time).
        imports=(
            "workers.tasks",
            "workers.memorizer",
            "workers.cortex_affect",
            "workers.cortex_reflection",
            "workers.cortex_curiosity",
            "workers.cortex_maintenance",
            "workers.cortex_platform",
            "workers.cortex_initiative",
            "workers.browse_task",
            "workers.maintenance",
            "workers.ingestion",
            "workers.price_sync",
            "workers.backup_task",
            "workers.restore_task",
            "workers.credential_rotation_task",
            "workers.fx_fetcher",
            "workers.git_remote_sweep",
            "workers.human_escalation",
            "workers.repo_clone",
            "workers.plan_pr",
        ),
        # Agent runs are long; ack only after completion so a worker
        # crash re-queues the job instead of losing it.
        task_acks_late=True,
        # prod-06 task_prod06_zombi_02 (decision 1): when a worker is LOST
        # mid-task (OOM/SIGKILL/hard-limit), reject + requeue the in-flight
        # message so the run is retried instead of silently dropped. The
        # `supersede_running_executions` guard (execution_repo) absorbs the
        # duplicate so the redelivery never spawns a second container. Defence
        # in depth with the stale-execution sweeper (zombi_01).
        task_reject_on_worker_lost=True,
        # One job at a time per worker process — agent runs are not
        # cheap, prefetching would stall the queue behind a slow job.
        worker_prefetch_multiplier=1,
        task_track_started=True,
        # NOTE: the run_execution backstop time limit (workers-orchestrator-10)
        # is NOT hardcoded here. It is an operator-tunable platform setting
        # (`execution_soft/hard_time_limit_s`) applied per-task by the
        # orchestrator at enqueue time, so a UI change takes effect for new
        # runs without restarting the workers (Plan 06.14 task_06_14_04).
        # Redis broker can drop the connection; retry on boot rather
        # than crash if Redis isn't up yet.
        broker_connection_retry_on_startup=True,
        # prod-06 task_prod06_zombi_03 (decision 2): pin the Redis broker
        # visibility timeout ABOVE the execution hard limit (capped at 6h in the
        # registry) so a long-but-legitimate run is never redelivered as a
        # duplicate while still alive.
        broker_transport_options={"visibility_timeout": EXECUTION_VISIBILITY_TIMEOUT_S},
        # Serialise as JSON (no pickle — never trust the broker payload).
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Plan 06.5 Fase D — periodic maintenance + Plan 11 task_11_18
        # scheduled (configurable) price-catalog sync.
        beat_schedule=build_beat_schedule(cfg),
    )
    return app


# Module-level app for the Celery CLI (`celery -A workers.celery_app`).
app = build_celery_app()
