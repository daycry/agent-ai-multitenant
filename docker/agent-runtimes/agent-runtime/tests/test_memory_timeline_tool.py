"""task_mem_03: la tool `memory_timeline` del runtime.

Valida los argumentos (exactamente uno de memory_id / execution_id), reenvia al
endpoint interno y se registra como capacidad de SISTEMA (exenta del allowlist).
"""

from __future__ import annotations

from typing import Any

from agent_runtime.builtin_families import SYSTEM_FAMILY_TOOL_NAMES, register_system_families
from agent_runtime.internal_api import InternalAPIHTTPError
from agent_runtime.memory_tools import MemoryTools
from agent_runtime.orchestration_tools import OrchestrationSink
from agent_runtime.tools import ToolRegistry


class _FakeAPI:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def memory_timeline(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"executions": [{"position": 0, "memories": []}]}


def test_requires_exactly_one_anchor() -> None:
    tools = MemoryTools(api=_FakeAPI())  # type: ignore[arg-type]
    assert not tools.memory_timeline({}).ok
    assert not tools.memory_timeline({"memory_id": "a", "execution_id": "b"}).ok


def test_forwards_anchor_and_window() -> None:
    api = _FakeAPI()
    result = MemoryTools(api=api).memory_timeline(  # type: ignore[arg-type]
        {"memory_id": "m-1", "window": "3"}
    )
    assert result.ok
    assert api.calls == [{"memory_id": "m-1", "execution_id": None, "window": 3}]
    assert result.output == {"executions": [{"position": 0, "memories": []}]}


def test_window_defaults_to_five_and_rejects_garbage() -> None:
    api = _FakeAPI()
    tools = MemoryTools(api=api)  # type: ignore[arg-type]
    assert tools.memory_timeline({"execution_id": "e-1"}).ok
    assert api.calls[0]["window"] == 5
    assert not tools.memory_timeline({"execution_id": "e-1", "window": "many"}).ok


def test_http_error_folds_into_tool_result() -> None:
    api = _FakeAPI(error=InternalAPIHTTPError(404, "memory not found"))
    result = MemoryTools(api=api).memory_timeline({"memory_id": "m-1"})  # type: ignore[arg-type]
    assert not result.ok
    assert "404" in (result.error or "")


def test_registered_as_system_tool() -> None:
    assert "memory_timeline" in SYSTEM_FAMILY_TOOL_NAMES
    registry = ToolRegistry()
    register_system_families(registry, api=_FakeAPI(), sink=OrchestrationSink())  # type: ignore[arg-type]
    assert "memory_timeline" in registry.names()
