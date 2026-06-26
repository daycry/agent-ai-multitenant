"""Shared writer for node-exporter **textfile-collector** metric files.

On a single-machine Docker host the dependency-free way to surface app metrics
to Prometheus is the node-exporter textfile collector: a process drops a
``*.prom`` file into the directory node-exporter watches
(``--collector.textfile.directory``), and node-exporter re-exports its samples.
Both :mod:`workers.queue_metrics` (every 30s) and :mod:`workers.backup_metrics`
(daily) publish through this single helper so the publish semantics live in one
place and cannot drift.

Why two phases with different log levels
----------------------------------------
The collector directory is **infrastructure** — a bind/volume mount provided by
the orchestration layer (``docker-compose.monitoring.yml`` mounts the
``node_exporter_textfile`` volume at ``/host/textfile``). A worker must never
fabricate it: creating ``/host/textfile`` inside the container would land on the
container's own filesystem, which node-exporter does not watch.

So we split the publish into two phases:

1. **Ensure the collector dir exists.** If it cannot be provisioned (the mount
   isn't present, so an ancestor like ``/host`` is read-only), the textfile sink
   is simply NOT DEPLOYED in this topology. Emitting a ``.prom`` nobody scrapes
   is pointless, so we skip QUIETLY (``debug``). This is an expected deployment
   posture, not a fault. Crucially, the queue sampler fires every 30s — a
   WARNING here would flood the logs ~2880 times/day on any stack without the
   monitoring overlay.
2. **Write atomically** (temp file + ``os.replace``) so node-exporter never reads
   a half-written file. A failure HERE — the dir exists but the write fails
   (disk full, perms on the file) — IS a real fault and is logged loudly.

Best-effort: publishing metrics must never raise to the caller.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import structlog

_log = structlog.get_logger("workers.textfile_collector")

# Sinks (collector directories) already reported as absent, keyed by directory
# path. Whether the textfile dir is mounted is a STATIC deployment fact — it does
# not change over a process's life — so we report it ONCE per process and then
# stay silent, instead of re-logging on every 30s sample. (The worker process
# uses structlog's default config, which does not filter by level, so we cannot
# rely on a DEBUG level being dropped to silence the repetition.)
_reported_absent_sinks: set[str] = set()


def write_textfile_metric(
    path: str | os.PathLike[str],
    render: Callable[[], str],
    *,
    event_prefix: str,
) -> bool:
    """Atomically publish a node-exporter textfile-collector metric file.

    ``render`` is called lazily — only once the collector directory is known to
    exist — and must return the full Prometheus text-exposition body. Each caller
    passes its own ``event_prefix`` (e.g. ``"queue_metrics"``, ``"backup.metrics"``)
    so log events keep their existing namespace: ``{prefix}.sink_absent`` (info,
    once per process) and ``{prefix}.write_error`` (warning).

    Returns ``True`` iff the file was published; ``False`` otherwise. Best-effort:
    it never raises — neither a write failure NOR an exception from ``render`` can
    propagate to (and crash) the caller.
    """
    target = Path(path)
    parent = target.parent

    # Phase 1 — ensure the collector dir exists. A failure means the textfile
    # sink isn't provisioned in this deployment: skip QUIETLY (expected posture).
    # Report the absent sink ONCE per process, then stay silent (the dedupe set
    # is bounded — callers publish to a small, fixed set of collector dirs).
    #
    # We deliberately do NOT discriminate by errno here. `mkdir(exist_ok=True)`
    # only raises when the dir genuinely cannot be provisioned — its parent is
    # missing or read-only, or an ancestor is a file. The real "sink not mounted"
    # case manifests as EACCES (`[Errno 13] Permission denied: '/host'`), NOT
    # ENOENT, so treating only ENOENT/ENOTDIR as "absent" would misfire on the
    # common case and bring the WARNING flood back. A *mounted but read-only*
    # dir does NOT land here — `exist_ok=True` returns cleanly for an existing
    # dir — it fails LOUDLY in phase 2 instead, which is the correct signal.
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sink = str(parent)
        if sink not in _reported_absent_sinks:
            _reported_absent_sinks.add(sink)
            _log.info(
                f"{event_prefix}.sink_absent",
                path=str(target),
                error=str(exc),
                detail="node-exporter textfile dir not mounted; "
                "skipping metric publish (reported once per process)",
            )
        return False

    # Phase 2 — render + atomic write into the provisioned dir. A failure here is
    # a real fault (disk full, perms on the file, or a buggy render callback) and
    # is surfaced LOUDLY. We catch ``Exception`` (not just ``OSError``) so a
    # render glitch can never propagate and crash the worker — best-effort. Note
    # ``BaseException`` (KeyboardInterrupt / SystemExit) is intentionally NOT
    # swallowed here; only the temp-file cleanup below traps it to re-raise.
    try:
        body = render()
        fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=target.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp_name, target)
        except BaseException:
            # Clean up the temp file on any failure mid-write.
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    except Exception as exc:
        _log.warning(f"{event_prefix}.write_error", path=str(target), error=str(exc))
        return False
    return True
