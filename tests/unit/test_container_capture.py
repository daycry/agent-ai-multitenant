"""Unit: AgentContainerRunner streaming + log capture contract (cluster C5).

Two defects this pins down, both on the worker↔runner boundary and both
exercised with a fake docker container (no real Docker):

* **F17/P1.1** — ``run_streamed`` used to ``pump.join(timeout=5.0)`` and then
  ``remove(force=True)``. On voluminous output the pump could not drain in 5s,
  so the live tail (possibly the final ``execution.finished`` line) was cut and
  the container torn down mid-read. The runner must (a) capture the *complete*
  logs into ``ContainerResult.logs`` BEFORE removing the container, and (b) let
  the pump drain to EOF rather than cutting it short.
* **F21/P1.2** — the live pump must follow the STRUCTURED stdout channel ALONE
  (no ``demux``): the agent-runtime emits its JSON events on stdout, so reading
  stdout-only means a newline-less stderr fragment can never splice into a JSON
  line, AND the live stream is never empty (a demultiplexed ``follow`` stream
  delivered no live lines on the daemon — the regression that left the Redis
  stream empty). stderr is still captured in ``ContainerResult.logs``.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from workers.config import Settings
from workers.container import AgentContainerRunner

pytestmark = pytest.mark.unit


class _StreamingContainer:
    """Fake container whose ``logs(stream=True, stdout=True, stderr=False)`` yields
    a scripted sequence of stdout byte chunks (the structured channel), and whose
    blocking ``logs()`` returns the full combined output (what ``_capture`` reads)."""

    def __init__(
        self,
        *,
        stdout_chunks: list[bytes],
        full_logs: bytes,
        on_remove: Any = None,
    ) -> None:
        self.id = "fake-container"
        self.status = "exited"
        self.attrs: dict[str, Any] = {
            "State": {"ExitCode": 0},
            "Config": {"Env": []},
            "HostConfig": {},
            "NetworkSettings": {"Networks": {}},
        }
        self._stdout_chunks = stdout_chunks
        self._full_logs = full_logs
        self._on_remove = on_remove
        self.removed = False

    def reload(self) -> None:  # pragma: no cover - status is static
        pass

    def logs(
        self,
        *,
        stream: bool = False,
        stdout: bool = True,
        stderr: bool = True,
        follow: bool = False,
        **_: Any,
    ) -> Any:
        if stream:
            # The live pump must follow the structured stdout channel ALONE (F21):
            # stdout-only, never demultiplexed, so stderr can't splice a JSON line
            # and the follow stream is never empty.
            assert stdout and not stderr, "the live pump must follow stdout only (F21)"
            return iter(self._stdout_chunks)
        # Blocking read — full combined logs, captured before removal.
        if self.removed:
            raise AssertionError("logs read after the container was removed")
        return self._full_logs

    def kill(self) -> None:  # pragma: no cover - not exercised here
        pass

    def remove(self, *, force: bool = False) -> None:
        self.removed = True
        if self._on_remove is not None:
            self._on_remove()


def _make_runner(container: _StreamingContainer) -> AgentContainerRunner:
    runner = AgentContainerRunner(Settings(), client=object())
    # Bypass the real Docker launch path; we only test streaming + capture.
    runner._start = lambda spec: container  # type: ignore[method-assign,assignment]
    runner._await_exit = staticmethod(lambda c, budget: False)  # type: ignore[method-assign,assignment]
    return runner


# ---------------------------------------------------------------------------
# F21 — the structured stdout JSON line is forwarded live, intact; stderr noise
# (only in the full logs, never on the live channel) can't corrupt it.
# ---------------------------------------------------------------------------
def test_stdout_json_line_is_forwarded_live_intact() -> None:
    container = _StreamingContainer(
        stdout_chunks=[b'{"event": "execution.started"}\n', b'{"event": "execution.finished"}\n'],
        # stderr noise lands in the captured logs, NOT on the live stdout channel.
        full_logs=b'war: deprecation notice{"event": "execution.started"}\n'
        b'{"event": "execution.finished"}\n',
    )
    runner = _make_runner(container)

    lines: list[str] = []
    runner.run_streamed(_spec(), lines.append)

    assert '{"event": "execution.started"}' in lines
    assert '{"event": "execution.finished"}' in lines
    # stderr noise never reached the live channel (it is only in ContainerResult.logs).
    assert not any("deprecation" in line for line in lines)


def test_interleaved_partial_stdout_lines_reassemble_correctly() -> None:
    container = _StreamingContainer(
        stdout_chunks=[b'{"a":', b"1}\n", b'{"b":', b"2}\n"],  # two JSON lines, chunked
        full_logs=b'{"a":1}\n{"b":2}\n',
    )
    runner = _make_runner(container)

    lines: list[str] = []
    runner.run_streamed(_spec(), lines.append)

    assert '{"a":1}' in lines
    assert '{"b":2}' in lines


# ---------------------------------------------------------------------------
# F17 — full logs captured before removal; pump drains before teardown
# ---------------------------------------------------------------------------
def test_result_carries_complete_logs_captured_before_removal() -> None:
    container = _StreamingContainer(
        stdout_chunks=[b"line one\n", b"line two\n"],
        full_logs=b"line one\nline two\n",
    )
    runner = _make_runner(container)

    result = runner.run_streamed(_spec(), lambda _line: None)

    assert result.logs == "line one\nline two\n"
    assert container.removed is True


def test_voluminous_tail_is_not_cut_short_before_removal() -> None:
    # The pump must fully drain (to EOF) before the container is removed, so a
    # large tail is not lost to a short join timeout.
    chunks: list[bytes] = [f"line {i}\n".encode() for i in range(500)]
    full = b"".join(chunks)
    seen_at_remove: dict[str, int] = {}
    container = _StreamingContainer(stdout_chunks=chunks, full_logs=full)

    lines: list[str] = []

    def _record_remove() -> None:
        seen_at_remove["count"] = len(lines)

    container._on_remove = _record_remove
    runner = _make_runner(container)

    runner.run_streamed(_spec(), lines.append)

    # Every streamed line reached the callback, and the container was only
    # removed after the pump had drained all of them.
    assert len(lines) == 500
    assert seen_at_remove["count"] == 500


# ---------------------------------------------------------------------------
# R1 — a container that VANISHES mid-run (crashed at startup, `--rm` removed it)
# must be terminal at once, not polled until the (per-provider, huge) budget —
# that ghost-poll loop on `GET /containers/<id>/json -> 404` hung the worker.
# ---------------------------------------------------------------------------
class _VanishingContainer:
    """Fake container the daemon no longer knows: reload()/logs() 404."""

    id = "ghost"
    status = "running"  # never updates — reload() raises instead
    attrs: ClassVar[dict[str, Any]] = {}

    def reload(self) -> None:
        import docker

        raise docker.errors.NotFound("No such container: ghost")

    def logs(self, **_: Any) -> Any:
        import docker

        raise docker.errors.NotFound("No such container: ghost")

    def kill(self) -> None:  # pragma: no cover - not reached
        pass

    def remove(self, *, force: bool = False) -> None:
        pass


def test_await_exit_is_terminal_when_container_vanishes(monkeypatch: pytest.MonkeyPatch) -> None:
    # A huge budget: without the fix, a vanished container is polled for the WHOLE
    # budget (the worker hang). Patch sleep to FAIL so any polling is caught — the
    # NotFound path must return BEFORE the first poll.
    import workers.container as cont

    monkeypatch.setattr(
        cont.time, "sleep", lambda *_a, **_k: pytest.fail("polled a vanished container (R1 hang)")
    )
    timed_out = AgentContainerRunner._await_exit(_VanishingContainer(), budget=10_000)
    # Vanished/crashed is terminal, NOT a wall-clock timeout.
    assert timed_out is False


def test_capture_tolerates_a_vanished_container() -> None:
    # `_capture` reads logs() — which 404s for a gone container. It must fall back
    # to a minimal result so the run finalises `failed` ("exited with no result")
    # instead of the NotFound propagating and crashing the worker thread.
    result = AgentContainerRunner._capture(_VanishingContainer(), timed_out=False)
    assert result.exit_code == -1
    assert result.logs == ""


def test_run_streamed_does_not_hang_when_container_vanishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workers.container as cont

    monkeypatch.setattr(
        cont.time, "sleep", lambda *_a, **_k: pytest.fail("polled a vanished container (R1 hang)")
    )
    runner = AgentContainerRunner(Settings(), client=object())
    container = _VanishingContainer()
    runner._start = lambda spec: container  # type: ignore[method-assign,assignment]

    result = runner.run_streamed(_spec(), lambda _line: None)

    # The run returns a (failed-ish) result with no events, not a hang.
    assert result.exit_code == -1
    assert result.logs == ""


def _spec() -> Any:
    from workers.container import ContainerSpec

    return ContainerSpec(image="img")
