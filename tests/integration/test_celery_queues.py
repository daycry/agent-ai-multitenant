"""Integration tests for the Celery queue topology (task_02_02).

These assert the Celery app is wired with the 7 queues and the
safety-oriented defaults (acks_late, prefetch=1, JSON-only). They
don't need a running broker — the config is the unit under test.
"""

from __future__ import annotations

import pytest
from workers.celery_app import DEFAULT_QUEUE, QUEUE_NAMES, build_celery_app
from workers.config import Settings

pytestmark = pytest.mark.integration


def _app():
    return build_celery_app(
        Settings(
            broker_url="redis://localhost:6379/1",
            result_backend="redis://localhost:6379/2",
        )
    )


def test_seven_queues_are_declared() -> None:
    app = _app()
    declared = {q.name for q in app.conf.task_queues}
    assert declared == {
        "default",
        "heavy",
        "gpu",
        "ingestion",
        "test",
        "review",
        "privileged",
    }
    assert len(QUEUE_NAMES) == 7


def test_default_queue_is_default() -> None:
    app = _app()
    assert app.conf.task_default_queue == DEFAULT_QUEUE == "default"


def test_broker_and_backend_point_at_configured_redis() -> None:
    app = _app()
    # Broker on DB 1, result backend on DB 2 — both off DB 0.
    assert app.conf.broker_url == "redis://localhost:6379/1"
    assert app.conf.result_backend == "redis://localhost:6379/2"


def test_long_job_safety_defaults() -> None:
    app = _app()
    # Ack after completion so a crashed worker re-queues the job.
    assert app.conf.task_acks_late is True
    # One job at a time — no prefetch stalling the queue behind a
    # slow agent run.
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_track_started is True


def test_json_only_serialization() -> None:
    """Never accept pickle off the broker."""
    app = _app()
    assert app.conf.task_serializer == "json"
    assert app.conf.result_serializer == "json"
    assert list(app.conf.accept_content) == ["json"]


def test_module_level_app_is_importable() -> None:
    """`celery -A workers.celery_app` needs a module-level `app`."""
    from workers.celery_app import app

    assert app.main == "agentic-workers"
