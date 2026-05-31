"""Celery application + the notification queue topology (task_10_02).

The api-server / orchestrator enqueue a notification send onto these
queues; notification-dispatcher worker processes consume them. Mirrors
``apps/workers/src/workers/celery_app.py`` (the 7-queue topology) but with
the two dedicated notification lanes, whose names come from
:class:`~notification_dispatcher.config.Settings` (never hardcoded):

  notifications.default    ordinary sends — the common lane.
  notifications.priority   time-sensitive sends (escalations, budget
                           alerts) drained by a separate worker so an
                           ordinary backlog never delays a priority alert.

Routing: ``notification_dispatcher.tasks`` registers ``send_notification``;
the enqueuer picks a lane at apply time via ``apply_async(queue=...)`` and
unrouted tasks fall through to the default queue.
"""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from notification_dispatcher.config import Settings, get_settings


def build_celery_app(settings: Settings | None = None) -> Celery:
    """Construct the Celery app. ``settings`` is injectable for tests."""
    cfg = settings or get_settings()

    # Queue names are operator-tunable (config.py) — read them once here so
    # both the task_queues and the default-queue agree.
    queue_names: tuple[str, ...] = (cfg.default_queue, cfg.priority_queue)

    app = Celery("agentic-notification-dispatcher")
    app.conf.update(
        broker_url=cfg.broker_url,
        result_backend=cfg.result_backend,
        # The two notification lanes.
        task_queues=tuple(Queue(name) for name in queue_names),
        task_default_queue=cfg.default_queue,
        # Task module a real worker imports on boot so the task is
        # registered (imported lazily — no circular import at config time).
        imports=("notification_dispatcher.tasks",),
        # Ack only after completion so a worker crash re-queues the send
        # rather than silently dropping it.
        task_acks_late=True,
        # One send at a time per worker process — a slow channel API
        # shouldn't stall the queue behind it via prefetch.
        worker_prefetch_multiplier=1,
        task_track_started=True,
        # Redis broker can drop the connection; retry on boot rather than
        # crash if Redis isn't up yet.
        broker_connection_retry_on_startup=True,
        # Serialise as JSON (no pickle — never trust the broker payload).
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
    )
    return app


# Module-level app for the Celery CLI
# (`celery -A notification_dispatcher.celery_app`).
app = build_celery_app()
