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
``*.prom`` file into the directory node-exporter watches; we write it ATOMICALLY
(temp file + ``os.replace``) so node-exporter never reads a half-written file.

Exposed samples
---------------
``agentic_celery_queue_depth{queue}`` (gauge)
    Messages waiting in each Celery queue (Redis ``LLEN`` of the queue's list).

``agentic_tasks_by_status{status}`` (gauge)
    Count of non-deleted ``tasks`` rows in each lifecycle status, across tenants.

Best-effort: emitting metrics must never break the worker. A failure to write the
file (collector dir absent, permission error, …) is logged and swallowed.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

import structlog

_log = structlog.get_logger("workers.queue_metrics")

# Metric names — namespace ``agentic_`` (mirrors agentic_backup_*). prod-08's
# scrape rules / dashboards reference these; keep in sync.
METRIC_QUEUE_DEPTH = "agentic_celery_queue_depth"
METRIC_TASKS_BY_STATUS = "agentic_tasks_by_status"


def _escape_label(value: str) -> str:
    """Escape a Prometheus label value (backslash, double-quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_queue_metrics(
    *,
    queue_depths: dict[str, int],
    status_counts: dict[str, int],
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
    return "\n".join(lines) + "\n"


def write_queue_metrics(
    path: str | os.PathLike[str],
    *,
    queue_depths: dict[str, int],
    status_counts: dict[str, int],
) -> bool:
    """Atomically write the queue-metrics file. Returns ``True`` on success,
    ``False`` on any (swallowed) error — best-effort, never breaks the worker."""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        body = render_queue_metrics(queue_depths=queue_depths, status_counts=status_counts)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp_name, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except OSError as exc:  # pragma: no cover — defensive: metrics never break the worker
        _log.warning("queue_metrics.write_error", path=str(target), error=str(exc))
        return False
    return True
