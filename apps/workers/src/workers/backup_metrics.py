"""Backup health metrics for Prometheus (Plan 12 task_12_14).

The "last backup failed" alert (``BackupLastRunFailed`` / ``BackupTooOld`` in
``docker/monitoring/prometheus/rules/host_alerts.yml``) needs a metric that
reflects the outcome of the most recent backup run. On a single-machine Docker
host the simplest, dependency-free way to surface that to Prometheus is the
**node-exporter textfile collector**: a process drops a ``*.prom`` file into a
directory node-exporter watches, and node-exporter re-exports the samples in it.

This module is the writer. After every scheduled backup the daily task
(:mod:`workers.backup_task`) calls :func:`write_backup_metrics` with the run's
outcome; we render the two samples below and write them ATOMICALLY (write a temp
file in the same directory, then ``os.replace`` over the target) so node-exporter
— which polls the file on its own cadence — never reads a half-written file.

Exposed samples
---------------
``agentic_backup_last_success`` (gauge)
    ``1`` when the most recent run produced a verified-good bundle, ``0`` when it
    failed or did not verify. This is the direct signal for "last backup
    failed".

``agentic_backup_last_success_timestamp_seconds`` (gauge)
    Unix timestamp of the most recent SUCCESSFUL run. Only advanced on success,
    so a string of failures leaves it pointing at the last good backup — which
    is exactly what ``BackupTooOld`` (age threshold) needs. ``0`` until the
    first success.

``agentic_backup_last_run_timestamp_seconds`` (gauge)
    Unix timestamp of the most recent run regardless of outcome (diagnostic:
    proves the job is firing even while it is failing).

Best-effort
-----------
Emitting metrics must never break a backup. The publish goes through the shared
:func:`workers.textfile_collector.write_textfile_metric` helper, which treats an
absent textfile sink (the monitoring overlay isn't up, so the collector dir
can't be provisioned) as an expected posture logged quietly, and a genuine write
failure on a provisioned sink as a loud fault — the backup itself already
succeeded or failed independently of whether we managed to record it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import structlog

from workers.textfile_collector import write_textfile_metric

_log = structlog.get_logger("workers.backup_metrics")

# Metric names — mirrored by the alert rules in host_alerts.yml. Keep in sync.
_M_LAST_SUCCESS = "agentic_backup_last_success"
_M_LAST_SUCCESS_TS = "agentic_backup_last_success_timestamp_seconds"
_M_LAST_RUN_TS = "agentic_backup_last_run_timestamp_seconds"


def render_backup_metrics(
    *,
    success: bool,
    now: float,
    last_success_ts: float,
) -> str:
    """Render the Prometheus text-exposition body for a backup run.

    Pure function (no I/O) so the format is unit-testable. ``last_success_ts`` is
    the timestamp to publish for the last *successful* run — the caller passes
    ``now`` on success or the previously-recorded value (``0`` if never) on
    failure, so a failed run does not reset the age clock.
    """
    lines = [
        f"# HELP {_M_LAST_SUCCESS} 1 if the most recent backup run verified good, 0 if it failed.",
        f"# TYPE {_M_LAST_SUCCESS} gauge",
        f"{_M_LAST_SUCCESS} {1 if success else 0}",
        f"# HELP {_M_LAST_SUCCESS_TS} Unix time of the most recent successful backup.",
        f"# TYPE {_M_LAST_SUCCESS_TS} gauge",
        f"{_M_LAST_SUCCESS_TS} {last_success_ts:.0f}",
        f"# HELP {_M_LAST_RUN_TS} Unix time of the most recent backup run (any outcome).",
        f"# TYPE {_M_LAST_RUN_TS} gauge",
        f"{_M_LAST_RUN_TS} {now:.0f}",
    ]
    return "\n".join(lines) + "\n"


def _read_last_success_ts(path: Path) -> float:
    """Best-effort read of the previously-published success timestamp.

    Lets a failed run preserve the age clock (so ``BackupTooOld`` keeps measuring
    from the last good backup, not from the failure). Any parse/IO error → 0.
    """
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(_M_LAST_SUCCESS_TS + " "):
                return float(line.split(" ", 1)[1].strip())
    except (OSError, ValueError):
        return 0.0
    return 0.0


def write_backup_metrics(path: str | os.PathLike[str], *, success: bool) -> bool:
    """Atomically write the backup health metrics file.

    Returns ``True`` if the file was written, ``False`` on any (swallowed)
    error — emitting metrics is best-effort and must not affect the backup
    outcome. The write is atomic (temp file + ``os.replace``) so node-exporter
    never observes a partial file.
    """
    target = Path(path)
    now = time.time()

    def _render() -> str:
        # Called only once the collector dir is known to exist (phase 1 of the
        # helper). On success advance the success clock to now; on failure
        # preserve the previously-published success timestamp so the age alert
        # keeps measuring from the last GOOD backup.
        last_success_ts = now if success else _read_last_success_ts(target)
        return render_backup_metrics(success=success, now=now, last_success_ts=last_success_ts)

    ok = write_textfile_metric(target, _render, event_prefix="backup.metrics")
    if ok:
        _log.info("backup.metrics.written", path=str(target), success=success)
    return ok
