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
from typing import Any, ClassVar

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


# ---------------------------------------------------------------------------
# MCP servers threaded into the spec are wired and runnable (task_06_18_12).
# A fake MCPToolRunner stands in for the live SDK — no subprocess / network.
# ---------------------------------------------------------------------------
class _FakeMcpTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = None
        self.input_schema: dict[str, Any] = {}


class _FakeMcpRunner:
    """Records lifecycle + returns canned tools so the boot can register them."""

    instances: ClassVar[list[_FakeMcpRunner]] = []

    def __init__(self, vault_resolver: Any = None) -> None:
        self.vault_resolver = vault_resolver
        self.started = False
        self.closed = False
        self.connected: list[str] = []
        _FakeMcpRunner.instances.append(self)

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.closed = True

    def connect(self, config: Any) -> list[_FakeMcpTool]:
        self.connected.append(config.name)
        return [_FakeMcpTool("read_file")]

    def tools(self, _server_name: str) -> list[_FakeMcpTool]:
        return [_FakeMcpTool("read_file")]

    def max_output_bytes(self, _server_name: str) -> int:
        return 65536

    def call_tool(self, _server_name: str, tool_name: str, _arguments: Any = None) -> str:
        return json.dumps({"echoed": tool_name})


@pytest.fixture()
def _fake_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeMcpRunner.instances.clear()
    from agent_runtime import mcp_tools

    monkeypatch.setattr(mcp_tools, "MCPToolRunner", _FakeMcpRunner)


def test_mcp_server_tools_are_wired_namespaced_and_run(
    _fake_mcp: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """A declared MCP server's tools register as `<server>.<tool>` and execute —
    `<server>.read_file` is a distinct tool from the builtin `read_file`."""
    spec = {
        "task": {"id": "t-6", "title": "mcp", "description": ""},
        "model": _scripted("filesystem.read_file", {"path": "x"}),
        "allowed_tools": ["filesystem.read_file"],
        "mcp_servers": [
            {"name": "filesystem", "transport": "stdio", "command": "filesystem-mcp", "args": []},
        ],
    }
    step = _act_step(_run(spec, capsys))
    assert step["result"]["ok"] is True
    assert step["result"]["output"] == {"echoed": "read_file"}
    # The fake runner was started, connected and CLOSED (finally) — no leak.
    runner = _FakeMcpRunner.instances[-1]
    assert runner.started is True
    assert runner.connected == ["filesystem"]
    assert runner.closed is True


def test_no_mcp_servers_opens_no_session(
    _fake_mcp: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without `mcp_servers` the boot opens no MCP session (feature-safe)."""
    spec = {
        "task": {"id": "t-7", "title": "no-mcp", "description": ""},
        "model": _scripted("echo", {"text": "hi"}),
    }
    step = _act_step(_run(spec, capsys))
    assert step["result"]["ok"] is True
    # No runner was ever constructed.
    assert _FakeMcpRunner.instances == []
