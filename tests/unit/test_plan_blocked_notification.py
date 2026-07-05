"""c3/T7 (audit 2026-07-03): escalating a plan to `blocked` notifies the operator.

When the orchestrator escalates a plan `in_progress -> blocked` (its only remaining
open tasks are blocked), it enqueues a `plan_blocked` domain event so the dispatcher
fans it out to the operator's channels. This pins the enqueue mechanics of the new
`_send_plan_blocked_notification` (recipient resolution + template render live in the
notification-dispatcher, covered by its registry/template tests).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace


class _RecordingCelery:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def send_task(self, name, *, args=None, queue=None, **_kw):
        self.calls.append((name, args, queue))


def test_plan_blocked_notification_enqueues_dispatch_event() -> None:
    from orchestrator.dispatch import _DISPATCH_EVENT_TASK, TaskDispatcher

    celery = _RecordingCelery()
    dispatcher = TaskDispatcher(
        sessionmaker=None,  # type: ignore[arg-type]
        celery_app=celery,  # type: ignore[arg-type]
        settings=SimpleNamespace(notifications_event_queue="notif-queue"),  # type: ignore[arg-type]
    )
    event = {
        "event_type": "plan_blocked",
        "tenant_id": "tenant-1",
        "context": {"plan_name": "My plan", "plan_id": "plan-1"},
    }
    asyncio.run(dispatcher._send_plan_blocked_notification(event))

    assert len(celery.calls) == 1
    name, args, queue = celery.calls[0]
    assert name == _DISPATCH_EVENT_TASK
    assert args == [event]
    assert queue == "notif-queue"


def test_plan_blocked_notification_is_best_effort_on_broker_failure() -> None:
    from orchestrator.dispatch import TaskDispatcher

    class _BoomCelery:
        def send_task(self, *_a, **_k):
            raise RuntimeError("broker down")

    dispatcher = TaskDispatcher(
        sessionmaker=None,  # type: ignore[arg-type]
        celery_app=_BoomCelery(),  # type: ignore[arg-type]
        settings=SimpleNamespace(notifications_event_queue="q"),  # type: ignore[arg-type]
    )
    # Must NOT raise — the plan is already committed `blocked`; the alert is best-effort.
    asyncio.run(
        dispatcher._send_plan_blocked_notification(
            {"event_type": "plan_blocked", "tenant_id": "t", "context": {"plan_id": "p"}}
        )
    )
