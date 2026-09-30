"""task_mem_10: la tool `memory_feedback` del runtime.

Valida `verdict` (`useful|wrong|outdated`) y `memory_id`, reenvía al endpoint interno y se
registra como capacidad de SISTEMA (exenta del allowlist por agente). Es ESCRITURA: pasa por
el gate de aprobación con la misma categoría que `memory_store`.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.approval import DEFAULT_TOOL_CATEGORIES
from agent_runtime.builtin_families import SYSTEM_FAMILY_TOOL_NAMES, register_system_families
from agent_runtime.internal_api import InternalAPIHTTPError
from agent_runtime.memory_tools import MemoryTools
from agent_runtime.orchestration_tools import OrchestrationSink
from agent_runtime.tools import ToolRegistry


class _FakeAPI:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def memory_feedback(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {
            "memory_id": kwargs["memory_id"],
            "verdict": kwargs["verdict"],
            "useful_count": 0,
            "harmful_count": 1,
            "disputed": True,
        }


def test_forwards_a_valid_verdict_with_its_note() -> None:
    api = _FakeAPI()
    result = MemoryTools(api=api).memory_feedback(  # type: ignore[arg-type]
        {"memory_id": " m-1 ", "verdict": "wrong", "note": "  ya no aplica  "}
    )
    assert result.ok
    assert api.calls == [{"memory_id": "m-1", "verdict": "wrong", "note": "ya no aplica"}]
    assert result.output["disputed"] is True


def test_note_is_optional() -> None:
    api = _FakeAPI()
    assert (
        MemoryTools(api=api)
        .memory_feedback(  # type: ignore[arg-type]
            {"memory_id": "m-1", "verdict": "useful"}
        )
        .ok
    )
    assert api.calls[0]["note"] is None


def test_rejects_a_missing_id_or_an_unknown_verdict() -> None:
    api = _FakeAPI()
    tools = MemoryTools(api=api)  # type: ignore[arg-type]
    assert not tools.memory_feedback({"verdict": "useful"}).ok
    assert not tools.memory_feedback({"memory_id": "", "verdict": "useful"}).ok
    assert not tools.memory_feedback({"memory_id": "m-1"}).ok
    assert not tools.memory_feedback({"memory_id": "m-1", "verdict": "great"}).ok
    assert not tools.memory_feedback({"memory_id": "m-1", "verdict": "useful", "note": 3}).ok
    assert api.calls == []  # nada llega al servidor con argumentos inválidos


def test_verdict_is_case_and_space_tolerant() -> None:
    """Un modelo pequeño (ADR 0169) escribe `Wrong` o ` outdated`."""
    api = _FakeAPI()
    tools = MemoryTools(api=api)  # type: ignore[arg-type]
    assert tools.memory_feedback({"memory_id": "m-1", "verdict": " Outdated "}).ok
    assert api.calls[0]["verdict"] == "outdated"


def test_http_error_folds_into_tool_result() -> None:
    api = _FakeAPI(error=InternalAPIHTTPError(404, "memory not found"))
    result = MemoryTools(api=api).memory_feedback(  # type: ignore[arg-type]
        {"memory_id": "m-1", "verdict": "wrong"}
    )
    assert not result.ok
    assert "404" in (result.error or "")


def test_registered_as_system_tool_and_gated_like_memory_store() -> None:
    assert "memory_feedback" in SYSTEM_FAMILY_TOOL_NAMES
    registry = ToolRegistry()
    register_system_families(registry, api=_FakeAPI(), sink=OrchestrationSink())  # type: ignore[arg-type]
    assert "memory_feedback" in registry.names()
    # Es escritura persistente: misma categoría de aprobación que `memory_store`.
    assert DEFAULT_TOOL_CATEGORIES["memory_feedback"] == DEFAULT_TOOL_CATEGORIES["memory_store"]
