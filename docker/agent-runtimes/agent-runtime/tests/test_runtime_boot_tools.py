"""Boot-path tests: `__main__.run_task` wires real tools (Plan 06.18
task_06_18_05).

The boot path used to mount only echo/noop + a conditional shell_exec, so
an agent assigned `read_file` / `run_pytest` / a network tool got a silent
"unknown tool". This pins the fix: with the worker-serialised `tool_specs`
in the spec, the boot registers every assigned family under its canonical
name and the loop executes them; backward-compat (no `tool_specs`) keeps the
old echo/noop behaviour byte-for-byte.

Self-contained — no DB/Redis/Docker. The scripted ModelClient drives the
loop deterministically; the docker_command tool's client is monkeypatched.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest
from agent_runtime.__main__ import run_task


def _scripted(act_tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "scripted",
        "decisions": [
            {"kind": "act", "tool": act_tool, "tool_args": args},
            {"kind": "finish", "output": "done"},
        ],
        "reviews": [{"passed": True}],
    }


def _run(spec: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    rc = run_task(spec)
    assert rc == 0
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _act_step(events: list[dict[str, Any]]) -> dict[str, Any]:
    acts = [
        e["step"] for e in events if e.get("event") == "step" and e["step"].get("node") == "act"
    ]
    assert len(acts) == 1, acts
    return acts[0]


# ---------------------------------------------------------------------------
# An assigned builtin (read_file) is wired and executes — not "unknown tool".
# ---------------------------------------------------------------------------
def test_assigned_read_file_is_wired_and_runs(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "hello.txt").write_text("from disk", encoding="utf-8")
    spec = {
        "task": {"id": "t-1", "title": "read", "description": ""},
        "model": _scripted("read_file", {"path": "hello.txt"}),
        "allowed_tools": ["read_file"],
        "tool_specs": [
            {"name": "read_file", "implementation_type": "builtin", "config": {}},
        ],
    }
    step = _act_step(_run(spec, capsys))
    assert step["result"]["ok"] is True
    assert step["result"]["output"]["content"] == "from disk"
    assert "unknown tool" not in (step["result"].get("error") or "")


# ---------------------------------------------------------------------------
# An assigned network tool (http_get) is wired — the call may fail (no server)
# but it is NEVER "unknown tool" (the bug this task closes).
# ---------------------------------------------------------------------------
def test_assigned_network_tool_is_wired_not_unknown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = {
        "task": {"id": "t-2", "title": "net", "description": ""},
        "model": _scripted("http_get", {"url": "http://blocked.example/x"}),
        "allowed_tools": ["http_get"],
        "allowed_domains": ["allowed.example"],
        "tool_specs": [
            {"name": "http_get", "implementation_type": "builtin", "config": {}},
        ],
    }
    step = _act_step(_run(spec, capsys))
    error = step["result"].get("error") or ""
    # The domain allowlist rejects blocked.example — a real tool result, not a
    # missing-tool error.
    assert "unknown tool" not in error
    assert "domain not allowed" in error


# ---------------------------------------------------------------------------
# An assigned run_* docker_command tool resolves its image and runs.
# ---------------------------------------------------------------------------
def test_assigned_run_pytest_is_wired_and_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from unittest.mock import MagicMock

    fake_client = MagicMock()
    fake_client.containers.run.return_value = b"1 passed\n"
    fake_docker = types.ModuleType("docker")
    fake_docker.from_env = lambda: fake_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    spec = {
        "task": {"id": "t-3", "title": "test", "description": ""},
        "model": _scripted("run_pytest", {"path": "tests/"}),
        "allowed_tools": ["run_pytest"],
        # The worker pre-resolves the image (it owns the runtime catalog) and
        # serialises it into the docker_command spec config.
        "tool_specs": [
            {
                "name": "run_pytest",
                "implementation_type": "docker_command",
                "config": {
                    "image": "agent-runtime-python-pytest:v1",
                    "command_template": ["pytest", "{path}"],
                },
            },
        ],
    }
    step = _act_step(_run(spec, capsys))
    assert step["result"]["ok"] is True
    assert "unknown tool" not in (step["result"].get("error") or "")


# ---------------------------------------------------------------------------
# Backward-compat: NO tool_specs → today's behaviour (echo runs unrestricted).
# ---------------------------------------------------------------------------
def test_no_tool_specs_keeps_legacy_echo_behaviour(
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = {
        "task": {"id": "t-4", "title": "legacy", "description": ""},
        "model": _scripted("echo", {"text": "hi"}),
    }
    step = _act_step(_run(spec, capsys))
    assert step["result"]["ok"] is True
    assert step["result"]["output"] == "hi"


def test_no_tool_specs_does_not_register_extra_families(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no tool_specs the boot must NOT wire the new families — an
    agent calling read_file in the legacy (unrestricted) path still gets the
    old 'unknown tool' (no behaviour change for pre-06.18 specs)."""
    spec = {
        "task": {"id": "t-5", "title": "legacy", "description": ""},
        "model": _scripted("read_file", {"path": "x"}),
    }
    step = _act_step(_run(spec, capsys))
    assert step["result"]["ok"] is False
    assert "unknown tool" in (step["result"].get("error") or "")
