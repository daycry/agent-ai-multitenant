"""Celery application + the 7-queue topology (task_02_02).

The orchestrator enqueues work onto these queues; worker processes
(task_02_06) consume them. Each queue is a separate Celery worker
deployment so heavy / GPU / privileged work is isolated from the
default lane and can scale independently.

Queues (spec §12, Plan 02 Fase A):

  default      ordinary agent tasks — the common lane.
  heavy        long / memory-hungry agent runs.
  gpu          tasks needing a GPU host (optional deployment).
  ingestion    document ingestion pipelines (Docling — Plan 04).
  test         test-runtime execution (placeholder until Plan 06).
  review       review-runtime execution (placeholder until Plan 06).
  privileged   tasks touching secrets / infra — drained by a worker
               with a tighter security profile.

Routing: `workers.tasks` registers the tasks (task_02_06); a task
picks its queue at apply time via `apply_async(queue=...)`, and
unrouted tasks fall through to `default`.
"""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from workers.config import Settings, get_settings

# Canonical queue names. Order is informational only.
QUEUE_NAMES: tuple[str, ...] = (
    "default",
    "heavy",
    "gpu",
    "ingestion",
    "test",
    "review",
    "privileged",
)

DEFAULT_QUEUE = "default"


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
            "workers.maintenance",
            "workers.ingestion",
            "workers.price_sync",
            "workers.backup_task",
            "workers.restore_task",
            "workers.credential_rotation_task",
        ),
        # Agent runs are long; ack only after completion so a worker
        # crash re-queues the job instead of losing it.
        task_acks_late=True,
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
