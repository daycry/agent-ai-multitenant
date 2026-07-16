"""Unit test — prod-06 task_prod06_dag_03 (parte B: métrica de cola/estado).

The worker samples Celery queue depth (Redis LLEN per queue) and task counts per
lifecycle status, and renders them in the Prometheus text-exposition format that
the node-exporter textfile collector re-exports (the same dependency-free pattern
as ``backup_metrics``). prod-08 owns the scrape/alert/dashboard; this only EMITS.
"""

from __future__ import annotations

import pytest
from workers.queue_metrics import (
    METRIC_EXECUTIONS_24H,
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


def test_render_emits_executions_24h_when_present() -> None:
    body = render_queue_metrics(
        queue_depths={},
        status_counts={},
        execution_counts={"completed": 4, "failed": 1},
    )
    assert f"# TYPE {METRIC_EXECUTIONS_24H} gauge" in body
    assert f'{METRIC_EXECUTIONS_24H}{{status="completed"}} 4' in body
    assert f'{METRIC_EXECUTIONS_24H}{{status="failed"}} 1' in body


def test_render_omits_executions_24h_when_absent() -> None:
    # Sin muestra (colector falló o no hay ejecuciones) → la métrica no aparece,
    # igual que dlq_depths: ausencia ≠ cero.
    body = render_queue_metrics(queue_depths={}, status_counts={})
    assert METRIC_EXECUTIONS_24H not in body


@pytest.mark.asyncio
async def test_collect_execution_counts_groups_by_status() -> None:
    from workers.maintenance.queue_sampler import _collect_execution_counts

    class _Rows:
        @staticmethod
        def all() -> list[tuple[str, int]]:
            return [("completed", 7), ("failed", 2)]

    class _Session:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def execute(self, stmt: object) -> _Rows:
            self.queries.append(str(stmt))
            return _Rows()

    session = _Session()
    counts = await _collect_execution_counts(session)
    assert counts == {"completed": 7, "failed": 2}
    # La ventana de 24h y el GROUP BY viajan en la propia query.
    assert "24 hours" in session.queries[0]
    assert "executions" in session.queries[0]


def test_render_escapes_label_values() -> None:
    # Defensive: a status/queue with a quote or backslash must not break the
    # exposition format.
    body = render_queue_metrics(queue_depths={'we"ird': 1}, status_counts={})
    assert r'queue="we\"ird"' in body


# ---------------------------------------------------------------------------
# AUD16-09 (auditoría 2026-07-16): «No data» era indistinguible de «sampler
# muerto». El sampler emite ahora un heartbeat (timestamp del último muestreo,
# para una regla de staleness en Prometheus) y un gauge up 1/0 por colector —
# un colector que falló esta pasada se ve como up=0, no como ausencia muda.
# ---------------------------------------------------------------------------
def test_render_emits_sampler_heartbeat() -> None:
    from workers.queue_metrics import METRIC_SAMPLER_LAST_RUN

    body = render_queue_metrics(
        queue_depths={},
        status_counts={},
        sampled_at=1_752_000_000.0,
    )
    assert f"# TYPE {METRIC_SAMPLER_LAST_RUN} gauge" in body
    assert f"{METRIC_SAMPLER_LAST_RUN} 1752000000" in body


def test_render_emits_collector_up_per_known_collector() -> None:
    from workers.queue_metrics import KNOWN_COLLECTORS, METRIC_COLLECTOR_UP

    body = render_queue_metrics(
        queue_depths={},
        status_counts={},
        sampled_at=1_752_000_000.0,
        collector_failures=frozenset({"executions_24h"}),
    )
    assert f"# TYPE {METRIC_COLLECTOR_UP} gauge" in body
    assert f'{METRIC_COLLECTOR_UP}{{collector="executions_24h"}} 0' in body
    for collector in KNOWN_COLLECTORS:
        if collector != "executions_24h":
            assert f'{METRIC_COLLECTOR_UP}{{collector="{collector}"}} 1' in body


def test_render_without_heartbeat_keeps_legacy_shape() -> None:
    from workers.queue_metrics import METRIC_COLLECTOR_UP, METRIC_SAMPLER_LAST_RUN

    body = render_queue_metrics(queue_depths={}, status_counts={})
    assert METRIC_SAMPLER_LAST_RUN not in body
    assert METRIC_COLLECTOR_UP not in body
