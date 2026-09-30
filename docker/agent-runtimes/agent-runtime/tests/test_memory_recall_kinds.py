"""task_mem_04 — `memory_recall` acepta `kinds` y lo normaliza como los scopes.

Un modelo pequeño escribe «Decisión», «bug» o un tipo inventado: se corrige o se
descarta antes de la llamada HTTP, nunca cuesta un 422 ni un turno.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.internal_api import InternalAgentAPI
from agent_runtime.memory_tools import MemoryTools, _coerce_kinds
from agent_runtime.tools import ToolResult


def test_coerce_kinds_maps_case_accents_and_aliases_and_drops_unknowns() -> None:
    assert _coerce_kinds(["Decision", "decisión", "BUG", "banana", "gotcha"]) == [
        "decision",
        "bugfix",
        "gotcha",
    ]
    assert _coerce_kinds(["banana"]) == []


class _FakeAPI:
    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    def memory_recall(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.seen = kwargs
        return []


def test_memory_recall_forwards_coerced_kinds() -> None:
    api = _FakeAPI()
    result = MemoryTools(api=api).memory_recall(  # type: ignore[arg-type]
        {"query": "driver", "kinds": ["Decisión", "raro"]}
    )
    assert isinstance(result, ToolResult) and result.ok
    assert api.seen["kinds"] == ["decision"]


def test_memory_recall_without_kinds_sends_no_filter() -> None:
    api = _FakeAPI()
    MemoryTools(api=api).memory_recall({"query": "q"})  # type: ignore[arg-type]
    assert api.seen.get("kinds") is None


def test_memory_recall_rejects_kinds_that_is_not_a_list() -> None:
    api = _FakeAPI()
    result = MemoryTools(api=api).memory_recall(  # type: ignore[arg-type]
        {"query": "q", "kinds": "decision"}
    )
    assert not result.ok and "kinds" in (result.error or "")


def test_the_http_body_carries_kinds(monkeypatch: Any) -> None:
    api = InternalAgentAPI.__new__(InternalAgentAPI)
    sent: dict[str, Any] = {}

    def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        sent.update(path=path, body=body)
        return {"hits": []}

    monkeypatch.setattr(api, "_post", _post, raising=False)
    api.memory_recall(query="q", kinds=["decision"])
    assert sent["body"]["kinds"] == ["decision"]
