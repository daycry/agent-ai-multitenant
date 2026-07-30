"""Unit test — shared node-exporter textfile-collector writer.

``workers.textfile_collector.write_textfile_metric`` is the single place that
publishes a ``*.prom`` file for the node-exporter textfile collector (used by
both ``workers.queue_metrics`` and ``workers.backup_metrics``). It is
deliberately TWO-PHASE with different log levels per failure mode:

  * **sink not deployed** — the collector directory cannot be provisioned (e.g.
    the monitoring overlay that bind-mounts ``/host/textfile`` isn't running, so
    the mount point's parent is read-only). Emitting a ``.prom`` nobody scrapes
    is pointless, so we skip QUIETLY (debug). This is an expected deployment
    posture, NOT a fault — the ``sample_queue_metrics`` beat fires every 30s, so
    a WARNING here would flood the logs ~2880 times/day on any stack without
    monitoring.
  * **real write failure** — the directory exists but the atomic write fails
    (disk full, perms on the file). That IS a fault and is logged loudly.
"""

from __future__ import annotations

import pytest
from workers.textfile_collector import write_textfile_metric

pytestmark = pytest.mark.unit


class _RecordingLogger:
    """Captures log calls regardless of the global logging config (asserting via
    caplog is order-dependent — the app's logging setup can disable propagation).
    Monkeypatched over the module ``_log`` so the test sees the calls directly."""

    def __init__(self) -> None:
        self.debug_events: list[str] = []
        self.info_events: list[str] = []
        self.warning_events: list[str] = []

    def debug(self, event: str, **_kw: object) -> None:
        self.debug_events.append(event)

    def info(self, event: str, **_kw: object) -> None:
        self.info_events.append(event)

    def warning(self, event: str, **_kw: object) -> None:
        self.warning_events.append(event)


def _patch_logger(monkeypatch: pytest.MonkeyPatch) -> _RecordingLogger:
    rec = _RecordingLogger()
    monkeypatch.setattr("workers.textfile_collector._log", rec)
    # Reset the once-per-process sink-absent dedupe so each test starts clean.
    monkeypatch.setattr("workers.textfile_collector._reported_absent_sinks", set())
    return rec


def test_published_file_is_world_readable(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-07-03: `mkstemp` crea el temp con modo 0600 y `os.replace` lo
    conserva — un `.prom` publicado por root (workers-backup) quedaba ilegible
    para node-exporter (uid nobody): «permission denied» en el collector y la
    métrica jamás llegaba a Prometheus. El writer debe publicar 0644."""
    import os
    import stat

    _patch_logger(monkeypatch)
    target = tmp_path / "collector" / "m.prom"  # type: ignore[operator]

    assert write_textfile_metric(target, lambda: "x 1\n", event_prefix="t") is True

    mode = stat.S_IMODE(os.stat(target).st_mode)
    # En Windows los bits POSIX son aproximados; lo esencial es lectura-para-todos.
    assert mode & stat.S_IROTH, f"published .prom not world-readable (mode {oct(mode)})"


def test_writes_when_collector_dir_is_creatable(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The collector dir doesn't exist yet but its parent is writable → it is
    # provisioned and the body is written atomically. (Mirrors the backup
    # writer's `tmp_path/"sub"/...` contract — must keep working.)
    rec = _patch_logger(monkeypatch)
    target = tmp_path / "textfile" / "m.prom"  # type: ignore[operator]

    ok = write_textfile_metric(target, lambda: "BODY\n", event_prefix="queue_metrics")

    assert ok is True
    assert target.read_text(encoding="utf-8") == "BODY\n"  # type: ignore[attr-defined]
    assert rec.warning_events == []
    # Atomic: no temp files left behind.
    assert not list(target.parent.glob("*.tmp"))  # type: ignore[attr-defined]


def test_sink_absent_skips_quietly_without_rendering(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An ancestor is a regular FILE → the collector dir cannot be created →
    # `mkdir` raises an OSError subclass (NotADirectoryError on POSIX,
    # FileExistsError [WinError 183] on Windows — both caught by `except OSError`).
    # This stands in for "/host not mounted": the sink isn't deployed here.
    rec = _patch_logger(monkeypatch)
    blocker = tmp_path / "blocker"  # type: ignore[operator]
    blocker.write_text("i am a file, not a directory", encoding="utf-8")  # type: ignore[attr-defined]
    target = blocker / "textfile" / "agentic_queue_depth.prom"

    rendered: list[int] = []

    def _render() -> str:
        rendered.append(1)
        return "BODY\n"

    ok = write_textfile_metric(target, _render, event_prefix="queue_metrics")

    assert ok is False
    # Bailed in phase 1 — we don't even pay to render a body nobody can store.
    assert rendered == []
    # QUIET: never a WARNING; a single INFO breadcrumb naming the absent sink.
    assert rec.warning_events == []
    assert "queue_metrics.sink_absent" in rec.info_events


def test_absent_sink_is_reported_only_once_per_process(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The 30s sampler would re-hit the same unprovisioned sink forever — the
    # absence is a STATIC fact, so it must be logged once and then stay silent.
    rec = _patch_logger(monkeypatch)
    blocker = tmp_path / "blocker"  # type: ignore[operator]
    blocker.write_text("x", encoding="utf-8")  # type: ignore[attr-defined]
    target = blocker / "textfile" / "agentic_queue_depth.prom"

    for _ in range(5):  # five samples, same absent sink
        assert write_textfile_metric(target, lambda: "B\n", event_prefix="queue_metrics") is False

    # Logged exactly once across all five fires.
    assert rec.info_events.count("queue_metrics.sink_absent") == 1
    assert rec.warning_events == []


def test_real_write_failure_is_loud(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    # The collector dir exists but the TARGET path is itself a directory, so the
    # atomic `os.replace(tmp, target)` fails with an OSError subclass
    # (IsADirectoryError on POSIX, PermissionError [WinError 5] on Windows).
    # A genuine write fault on a provisioned sink must be surfaced LOUDLY.
    rec = _patch_logger(monkeypatch)
    target = tmp_path / "m.prom"  # type: ignore[operator]
    target.mkdir()  # type: ignore[attr-defined]  # collides with the file we try to write

    ok = write_textfile_metric(target, lambda: "BODY\n", event_prefix="queue_metrics")

    assert ok is False
    assert "queue_metrics.write_error" in rec.warning_events
    # Still atomic: the temp file is cleaned up even on failure.
    assert not list(target.glob("*.tmp"))  # type: ignore[attr-defined]


def test_render_exception_never_escapes(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    # Best-effort contract (module docstring): publishing a metric must NEVER
    # raise to the caller — a glitch in a render callback cannot be allowed to
    # crash the beat worker. The collector dir exists (phase 1 passes), so we
    # reach phase 2; a non-OSError raised by render must be swallowed + logged,
    # not propagated.
    rec = _patch_logger(monkeypatch)
    target = tmp_path / "m.prom"  # type: ignore[operator]  # parent (tmp_path) exists

    def _boom() -> str:
        raise ValueError("render blew up")

    ok = write_textfile_metric(target, _boom, event_prefix="queue_metrics")

    assert ok is False
    assert "queue_metrics.write_error" in rec.warning_events
    # Nothing published, no temp file leaked (render raised before mkstemp).
    assert not target.exists()  # type: ignore[attr-defined]
    assert not list(target.parent.glob("*.tmp"))  # type: ignore[attr-defined]


def test_event_prefix_is_honoured(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    # Each caller keeps its own structlog event namespace (backup.metrics.* vs
    # queue_metrics.*) so existing log-based dashboards/greps don't shift.
    rec = _patch_logger(monkeypatch)
    blocker = tmp_path / "f"  # type: ignore[operator]
    blocker.write_text("x", encoding="utf-8")  # type: ignore[attr-defined]
    target = blocker / "d" / "agentic_backup.prom"

    write_textfile_metric(target, lambda: "B\n", event_prefix="backup.metrics")

    assert "backup.metrics.sink_absent" in rec.info_events


def test_write_queue_metrics_is_quiet_when_sink_absent(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    # End-to-end regression for the user-visible bug: the 30s queue sampler must
    # NOT emit a WARNING every fire when the textfile dir isn't mounted.
    from workers.queue_metrics import write_queue_metrics

    rec = _patch_logger(monkeypatch)
    blocker = tmp_path / "blocker"  # type: ignore[operator]
    blocker.write_text("x", encoding="utf-8")  # type: ignore[attr-defined]
    target = blocker / "textfile" / "agentic_queue_depth.prom"

    ok = write_queue_metrics(target, queue_depths={"default": 1}, status_counts={"backlog": 2})

    assert ok is False
    assert rec.warning_events == []
    assert "queue_metrics.sink_absent" in rec.info_events
