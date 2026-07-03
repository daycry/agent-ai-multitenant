"""Unit: the `stack_exec` builtin tool + its family wiring (ADR 0093).

`stack_exec` is the agent's only way to run its project toolchain (composer /
phpunit / php spark): it forwards the command to the worker over
`InternalAgentAPI.run_stack`, which launches the stack runtime-template on the
task's worktree. The runtime itself holds no Docker — so the tool is a thin
adapter around the internal-API call, mapping rc→ok.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.builtin_families import (
    FAMILY_STACK,
    register_builtin_families,
)
from agent_runtime.internal_api import InternalAPIError
from agent_runtime.orchestration_tools import OrchestrationSink
from agent_runtime.stack_exec_tool import StackExecTool
from agent_runtime.tools import ToolRegistry


class _FakeApi:
    """Records the run_stack call and returns a scripted result."""

    def __init__(self, result: dict[str, Any] | None = None, raise_exc: Exception | None = None):
        self._result = result or {"exit_code": 0, "logs": "ok", "timed_out": False}
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    def run_stack(self, *, task_id: str, command: str, timeout_s: int = 600) -> dict[str, Any]:
        self.calls.append({"task_id": task_id, "command": command, "timeout_s": timeout_s})
        if self._raise is not None:
            raise self._raise
        return self._result


def test_stack_exec_forwards_command_and_maps_rc0_to_ok() -> None:
    api = _FakeApi({"exit_code": 0, "logs": "composer done", "timed_out": False})
    tool = StackExecTool(api, task_id="task-1")  # type: ignore[arg-type]

    result = tool({"command": "composer install"})

    assert result.ok is True
    assert result.output == {"exit_code": 0, "logs": "composer done", "timed_out": False}
    assert api.calls == [{"task_id": "task-1", "command": "composer install", "timeout_s": 600}]


def test_stack_exec_maps_nonzero_rc_to_failure() -> None:
    api = _FakeApi({"exit_code": 2, "logs": "1 test failed", "timed_out": False})
    tool = StackExecTool(api, task_id="t")  # type: ignore[arg-type]

    result = tool({"command": "vendor/bin/phpunit"})

    assert result.ok is False
    assert result.error == "command exited with code 2"
    assert result.output["exit_code"] == 2


def test_stack_exec_honours_explicit_timeout() -> None:
    api = _FakeApi()
    tool = StackExecTool(api, task_id="t")  # type: ignore[arg-type]

    tool({"command": "composer install", "timeout_s": 120})

    assert api.calls[0]["timeout_s"] == 120


def test_run_stack_http_margin_exceeds_server_wait() -> None:
    """Plan guardas-research-por-novedad D2 (run 019f252e): el margen httpx del
    runtime debe ser MAYOR que la espera del server (`timeout_s + 120` en
    `run_stack_command_and_wait`) — si empatan, la carrera la gana httpx y el
    agente recibe un `ReadTimeout` opaco en vez del 502 estructurado con causa."""
    from agent_runtime.internal_api import InternalAgentAPI

    captured: dict[str, Any] = {}

    class _FakeHttpClient:
        def post(self, url: str, *, json: Any, headers: Any, timeout: Any) -> Any:
            del url, json, headers  # firma keyword de httpx.Client.post; solo importa timeout
            captured["timeout"] = timeout

            class _Resp:
                status_code = 200

                @staticmethod
                def json() -> dict[str, Any]:
                    return {"exit_code": 0, "logs": "", "timed_out": False}

            return _Resp()

    api = InternalAgentAPI(
        base_url="http://api-server:8000",
        bearer_token="t",
        client=_FakeHttpClient(),  # type: ignore[arg-type]
    )
    api.run_stack(task_id="t", command="vendor/bin/phpunit", timeout_s=240)
    assert captured["timeout"] > 240 + 120  # margen del server + holgura real


def test_stack_exec_rejects_empty_command() -> None:
    api = _FakeApi()
    tool = StackExecTool(api, task_id="t")  # type: ignore[arg-type]

    result = tool({"command": "   "})

    assert result.ok is False
    assert "non-empty" in (result.error or "")
    assert api.calls == []  # never reached the worker


def test_stack_exec_wraps_internal_api_error() -> None:
    api = _FakeApi(raise_exc=InternalAPIError("worker down"))
    tool = StackExecTool(api, task_id="t")  # type: ignore[arg-type]

    result = tool({"command": "php -v"})

    assert result.ok is False
    assert "worker down" in (result.error or "")


# --- family wiring --------------------------------------------------------
class _DummyApi:
    """Stand-in InternalAgentAPI: StackExecTool only stores it."""


def test_stack_family_wires_stack_exec_with_api_and_task() -> None:
    registry = ToolRegistry()
    registered = register_builtin_families(
        registry,
        api=_DummyApi(),  # type: ignore[arg-type]
        sink=OrchestrationSink(),
        task_id="task-99",
        flags={
            "file": False,
            "red": False,
            "notificacion": False,
            "orquestacion": False,
            "conocimiento": False,
            "memoria": False,
        },
    )
    assert registered == ["stack_exec"]
    assert "stack_exec" in registry.names()


def test_stack_family_skipped_without_api() -> None:
    registry = ToolRegistry()
    registered = register_builtin_families(
        registry, api=None, sink=OrchestrationSink(), task_id="task-99"
    )
    assert "stack_exec" not in registered


def test_stack_family_skipped_without_task_id() -> None:
    registry = ToolRegistry()
    registered = register_builtin_families(
        registry,
        api=_DummyApi(),
        sink=OrchestrationSink(),
        task_id=None,  # type: ignore[arg-type]
    )
    assert "stack_exec" not in registered


def test_stack_family_honours_flag() -> None:
    registry = ToolRegistry()
    registered = register_builtin_families(
        registry,
        api=_DummyApi(),  # type: ignore[arg-type]
        sink=OrchestrationSink(),
        task_id="task-99",
        flags={FAMILY_STACK: False},
    )
    assert "stack_exec" not in registered
