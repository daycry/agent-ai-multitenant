"""Queue-depth + task-state metrics for Prometheus (prod-06 task_prod06_dag_03, parte B).

The autonomous execution loop can stall in ways that are invisible without
observability: messages piling up in a Celery queue (no worker draining it), or
tasks accumulating in a lifecycle state (e.g. a growing ``in_review`` backlog —
exactly the gap the reviewer-bridge wiring of dag_03's part A closes). prod-08
owns the scrape job, the alert rules (``CeleryQueueGrowing``) and the dashboard;
this module only EMITS the samples it consumes.

On a single-machine Docker host the dependency-free way to surface app metrics to
Prometheus is the **node-exporter textfile collector** — the same pattern as
:mod:`workers.backup_metrics`. A beat task samples the metrics and drops a
``*.prom`` file into the directory node-exporter watches via the shared
:func:`workers.textfile_collector.write_textfile_metric` helper (atomic write +
"sink-absent is expected, not a fault" semantics — see that module).

Exposed samples
---------------
``agentic_celery_queue_depth{queue}`` (gauge)
    Messages waiting in each Celery queue (Redis ``LLEN`` of the queue's list).

``agentic_tasks_by_status{status}`` (gauge)
    Count of non-deleted ``tasks`` rows in each lifecycle status, across tenants.

Best-effort: emitting metrics must never break the worker.
"""

from __future__ import annotations

import os

from workers.textfile_collector import write_textfile_metric

# Metric names — namespace ``agentic_`` (mirrors agentic_backup_*). prod-08's
# scrape rules / dashboards reference these; keep in sync.
METRIC_QUEUE_DEPTH = "agentic_celery_queue_depth"
METRIC_TASKS_BY_STATUS = "agentic_tasks_by_status"
# prod-08 núcleo (2026-07-12): profundidad de los streams DLQ (XLEN) — un
# mensaje dead-lettered es trabajo PERDIDO hasta que un humano lo mira.
METRIC_DLQ_DEPTH = "agentic_dlq_depth"
# FASE 2b monitorización (2026-07-12): actividad real de runs — ejecuciones por
# estado en las últimas 24h (el pulso del sistema agéntico para el dashboard).
METRIC_EXECUTIONS_24H = "agentic_executions_24h"


def _escape_label(value: str) -> str:
    """Escape a Prometheus label value (backslash, double-quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_queue_metrics(
    *,
    queue_depths: dict[str, int],
    status_counts: dict[str, int],
    dlq_depths: dict[str, int] | None = None,
    execution_counts: dict[str, int] | None = None,
) -> str:
    """Render the Prometheus text-exposition body. Pure (no I/O) so it is
    unit-testable. Keys are emitted in sorted order for a noise-free file diff."""
    lines = [
        f"# HELP {METRIC_QUEUE_DEPTH} Messages waiting in each Celery queue (Redis LLEN).",
        f"# TYPE {METRIC_QUEUE_DEPTH} gauge",
    ]
    for queue in sorted(queue_depths):
        lines.append(
            f'{METRIC_QUEUE_DEPTH}{{queue="{_escape_label(queue)}"}} {queue_depths[queue]}'
        )
    lines.append(
        f"# HELP {METRIC_TASKS_BY_STATUS} Non-deleted tasks in each lifecycle status (all tenants)."
    )
    lines.append(f"# TYPE {METRIC_TASKS_BY_STATUS} gauge")
    for status in sorted(status_counts):
        lines.append(
            f'{METRIC_TASKS_BY_STATUS}{{status="{_escape_label(status)}"}} {status_counts[status]}'
        )
    if dlq_depths:
        lines.append(f"# HELP {METRIC_DLQ_DEPTH} Entries in each dead-letter stream (Redis XLEN).")
        lines.append(f"# TYPE {METRIC_DLQ_DEPTH} gauge")
        for stream in sorted(dlq_depths):
            lines.append(
                f'{METRIC_DLQ_DEPTH}{{stream="{_escape_label(stream)}"}} {dlq_depths[stream]}'
            )
    if execution_counts:
        lines.append(
            f"# HELP {METRIC_EXECUTIONS_24H} Executions per terminal status, last 24 hours."
        )
        lines.append(f"# TYPE {METRIC_EXECUTIONS_24H} gauge")
        for exec_status in sorted(execution_counts):
            lines.append(
                f'{METRIC_EXECUTIONS_24H}{{status="{_escape_label(exec_status)}"}} '
                f"{execution_counts[exec_status]}"
            )
    return "\n".join(lines) + "\n"


def write_queue_metrics(
    path: str | os.PathLike[str],
    *,
    queue_depths: dict[str, int],
    status_counts: dict[str, int],
    dlq_depths: dict[str, int] | None = None,
    execution_counts: dict[str, int] | None = None,
) -> bool:
    """Atomically write the queue-metrics file. Returns ``True`` on success,
    ``False`` otherwise — best-effort, never breaks the worker. Delegates the
    publish (and the sink-absent-vs-real-failure log semantics) to
    :func:`workers.textfile_collector.write_textfile_metric`."""
    return write_textfile_metric(
        path,
        lambda: render_queue_metrics(
            queue_depths=queue_depths,
            status_counts=status_counts,
            dlq_depths=dlq_depths,
            execution_counts=execution_counts,
        ),
        event_prefix="queue_metrics",
    )
