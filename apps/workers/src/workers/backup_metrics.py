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
# AUD16-19: la copia OFFSITE era invisible (uploaded=[] en todos los bundles y
# ninguna métrica lo decía). El timestamp del último upload BUENO se preserva
# en runs sin upload — la regla BackupOffsiteStale mide desde ahí y solo arma
# cuando alguna vez hubo offsite (ts > 0): un host sin destino no alerta.
_M_OFFSITE_UPLOADED = "agentic_backup_offsite_uploaded"
_M_OFFSITE_LAST_TS = "agentic_backup_offsite_last_success_timestamp_seconds"


def render_backup_metrics(
    *,
    success: bool,
    now: float,
    last_success_ts: float,
    offsite_uploaded: int = 0,
    offsite_last_success_ts: float = 0.0,
) -> str:
    """Render the Prometheus text-exposition body for a backup run.

    Pure function (no I/O) so the format is unit-testable. ``last_success_ts`` is
    the timestamp to publish for the last *successful* run — the caller passes
    ``now`` on success or the previously-recorded value (``0`` if never) on
    failure, so a failed run does not reset the age clock. Same contract for
    ``offsite_last_success_ts`` (AUD16-19).
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
        f"# HELP {_M_OFFSITE_UPLOADED} Artifacts uploaded offsite by the most recent run.",
        f"# TYPE {_M_OFFSITE_UPLOADED} gauge",
        f"{_M_OFFSITE_UPLOADED} {max(0, offsite_uploaded)}",
        f"# HELP {_M_OFFSITE_LAST_TS} Unix time of the most recent offsite upload (0 = never).",
        f"# TYPE {_M_OFFSITE_LAST_TS} gauge",
        f"{_M_OFFSITE_LAST_TS} {offsite_last_success_ts:.0f}",
    ]
    return "\n".join(lines) + "\n"


def _read_published_ts(path: Path, metric: str) -> float:
    """Best-effort read of a previously-published timestamp gauge.

    Lets a failed run preserve the age clock (so ``BackupTooOld`` /
    ``BackupOffsiteStale`` keep measuring from the last GOOD run, not from the
    failure). Any parse/IO error → 0.
    """
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(metric + " "):
                return float(line.split(" ", 1)[1].strip())
    except (OSError, ValueError):
        return 0.0
    return 0.0


def write_backup_metrics(
    path: str | os.PathLike[str], *, success: bool, offsite_uploaded: int = 0
) -> bool:
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
        # keeps measuring from the last GOOD backup. Same contract for the
        # offsite clock (AUD16-19).
        last_success_ts = now if success else _read_published_ts(target, _M_LAST_SUCCESS_TS)
        offsite_last_ts = (
            now if offsite_uploaded > 0 else _read_published_ts(target, _M_OFFSITE_LAST_TS)
        )
        return render_backup_metrics(
            success=success,
            now=now,
            last_success_ts=last_success_ts,
            offsite_uploaded=offsite_uploaded,
            offsite_last_success_ts=offsite_last_ts,
        )

    ok = write_textfile_metric(target, _render, event_prefix="backup.metrics")
    if ok:
        _log.info(
            "backup.metrics.written",
            path=str(target),
            success=success,
            offsite_uploaded=offsite_uploaded,
        )
    return ok
