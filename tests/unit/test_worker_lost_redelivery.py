"""Unit test — prod-06 task_prod06_zombi_02.

A LOST worker (OOM / SIGKILL / hard-limit) must REJECT + requeue its in-flight
task so the run is retried instead of silently dropped. Celery only does that
when BOTH ``task_acks_late`` and ``task_reject_on_worker_lost`` are on. The
``supersede_running_executions`` guard (tested in execution_repo's own suite)
absorbs the redelivered duplicate so the retry never spawns a second container.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_worker_lost_rejects_and_requeues() -> None:
    from workers.celery_app import build_celery_app

    app = build_celery_app()
    assert app.conf.task_reject_on_worker_lost is True
    # reject_on_worker_lost is meaningless without late acks — both must hold.
    assert app.conf.task_acks_late is True
