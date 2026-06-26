"""Unit test — prod-06 task_prod06_dag_03 (parte B: métrica de cola/estado).

The worker samples Celery queue depth (Redis LLEN per queue) and task counts per
lifecycle status, and renders them in the Prometheus text-exposition format that
the node-exporter textfile collector re-exports (the same dependency-free pattern
as ``backup_metrics``). prod-08 owns the scrape/alert/dashboard; this only EMITS.
"""

from __future__ import annotations

import pytest
from workers.queue_metrics import (
    METRIC_QUEUE_DEPTH,
    METRIC_TASKS_BY_STATUS,
    render_queue_metrics,
)

pytestmark = pytest.mark.unit


def test_render_emits_gauge_per_queue_and_status() -> None:
    body = render_queue_metrics(
        queue_depths={"default": 3, "ingestion": 0, "review": 1},
        status_counts={"in_review": 2, "backlog": 5},
    )
    # Prometheus type declarations present for both metrics.
    assert f"# TYPE {METRIC_QUEUE_DEPTH} gauge" in body
    assert f"# TYPE {METRIC_TASKS_BY_STATUS} gauge" in body
    # One labelled sample per queue / per status.
    assert f'{METRIC_QUEUE_DEPTH}{{queue="default"}} 3' in body
    assert f'{METRIC_QUEUE_DEPTH}{{queue="ingestion"}} 0' in body
    assert f'{METRIC_QUEUE_DEPTH}{{queue="review"}} 1' in body
    assert f'{METRIC_TASKS_BY_STATUS}{{status="in_review"}} 2' in body
    assert f'{METRIC_TASKS_BY_STATUS}{{status="backlog"}} 5' in body
    # Ends with a trailing newline (valid exposition format).
    assert body.endswith("\n")


def test_render_is_deterministic_sorted() -> None:
    body = render_queue_metrics(
        queue_depths={"review": 1, "default": 3},
        status_counts={},
    )
    # Stable, sorted ordering so the file diff is noise-free.
    assert body.index('queue="default"') < body.index('queue="review"')


def test_render_handles_empty() -> None:
    # No queues / no tasks → still valid output with the HELP/TYPE headers.
    body = render_queue_metrics(queue_depths={}, status_counts={})
    assert f"# TYPE {METRIC_QUEUE_DEPTH} gauge" in body
    assert f"# TYPE {METRIC_TASKS_BY_STATUS} gauge" in body
    assert body.endswith("\n")


def test_render_escapes_label_values() -> None:
    # Defensive: a status/queue with a quote or backslash must not break the
    # exposition format.
    body = render_queue_metrics(queue_depths={'we"ird': 1}, status_counts={})
    assert r'queue="we\"ird"' in body
