"""Boot-path test: a malformed `AGENT_TASK_SPEC` still emits a structured
`execution.error` line (F18 / audit C5).

Before the fix, `_load_spec`'s `json.loads` raised OUTSIDE the try in `main()`,
so the container died with a stderr traceback and exit 1 WITHOUT any JSON line
on stdout — the worker only saw "exited 1 with no result". This pins that the
crash now surfaces a parseable `execution.error` and returns exit 1.

Self-contained — no DB/Redis/Docker.
"""

from __future__ import annotations

import json

import pytest
from agent_runtime.__main__ import main


def _events(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_invalid_spec_emits_execution_error_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-JSON `AGENT_TASK_SPEC` yields one `execution.error` line, rc 1."""
    monkeypatch.setenv("AGENT_TASK_SPEC", "{not valid json")

    rc = main()

    assert rc == 1
    events = _events(capsys)
    assert len(events) == 1, events
    error = events[0]
    assert error["event"] == "execution.error"
    assert "invalid AGENT_TASK_SPEC" in str(error["error"])


def test_invalid_spec_does_not_print_selftest_banner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The malformed spec is NOT treated as "no spec" (which would print the
    selftest banner and exit 0) — it is an error, not a bare run."""
    monkeypatch.setenv("AGENT_TASK_SPEC", "[1, 2,")

    rc = main()

    assert rc == 1
    events = _events(capsys)
    assert all(e.get("event") == "execution.error" for e in events)
    assert all("status" not in e for e in events)
