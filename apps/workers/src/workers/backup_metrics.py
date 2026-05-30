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
Emitting metrics must never break a backup. A failure to write the file (the
collector dir is missing because the monitoring overlay is not up, a permission
error, ...) is logged and swallowed — the backup itself already succeeded or
failed independently of whether we managed to record it.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path

import structlog

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
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # On success, advance the success clock to now; on failure, preserve the
        # previously-published success timestamp so the age alert keeps measuring
        # from the last GOOD backup.
        last_success_ts = now if success else _read_last_success_ts(target)
        body = render_backup_metrics(success=success, now=now, last_success_ts=last_success_ts)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp_name, target)
        except BaseException:
            # Clean up the temp file on any failure mid-write.
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except OSError as exc:  # pragma: no cover — defensive: metrics never break backup
        _log.warning("backup.metrics.write_error", path=str(target), error=str(exc))
        return False
    _log.info("backup.metrics.written", path=str(target), success=success)
    return True
