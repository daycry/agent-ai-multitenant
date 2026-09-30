"""task_mem_01 / task_mem_02 — el recall devuelve un ÍNDICE; el detalle se pide por id.

Revelación progresiva (plan memoria-agentes-2026-09-09): el preámbulo del run lleva
`id + titular` de cada memoria (barato) y el agente pide el contenido completo de las
que le sirven con `memory_get`. La cita `[mem:<id corto>]` deja rastro de cuál
condicionó una decisión.

Self-contained — sin DB/Redis/Docker: el cliente interno se sustituye por un doble.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent_runtime.__main__ import _build_auto_recall, run_task
from agent_runtime.builtin_families import SYSTEM_FAMILY_TOOL_NAMES
from agent_runtime.memory_tools import MemoryTools
from agent_runtime.providers import _decide_messages
from agent_runtime.state import initial_state

_ID_A = "11111111-1111-1111-1111-111111111111"
_ID_B = "22222222-2222-2222-2222-222222222222"
_FULL = "composer.lock se comitea siempre y se revisa en el PR. " * 8


def _index_hit(mem_id: str, headline: str) -> dict[str, Any]:
    return {
        "id": mem_id,
        "headline": headline,
        "type": "semantic",
        "kind": None,
        "scope": "project_shared",
        "created_at": "2026-09-30T10:00:00Z",
        "agent": "Backend Dev",
        "project": "Facturas",
        "score": 0.03,
        "tokens": 120,
    }


class _FakeAPI:
    def __init__(self) -> None:
        self.get_calls: list[list[str]] = []

    def memory_recall(self, *, query: str, limit: int = 5, **_: Any) -> list[dict[str, Any]]:
        del query
        return [
            _index_hit(_ID_A, "composer.lock se comitea"),
            _index_hit(_ID_B, "usar php 8.3"),
        ][:limit]

    def memory_get(self, *, ids: list[str]) -> list[dict[str, Any]]:
        self.get_calls.append(list(ids))
        return [{"id": i, "content": _FULL, "scope": "project_shared"} for i in ids]


def test_memory_get_is_a_system_family_tool() -> None:
    assert "memory_get" in SYSTEM_FAMILY_TOOL_NAMES


def test_memory_get_tool_forwards_ids_and_returns_the_detail() -> None:
    api = _FakeAPI()
    result = MemoryTools(api=api).memory_get({"ids": [_ID_A]})  # type: ignore[arg-type]
    assert result.ok is True
    assert api.get_calls == [[_ID_A]]
    assert result.output["memories"][0]["content"] == _FULL
    assert result.output["count"] == 1


def test_memory_get_rejects_empty_and_oversized_id_lists() -> None:
    tools = MemoryTools(api=_FakeAPI())  # type: ignore[arg-type]
    assert tools.memory_get({"ids": []}).ok is False
    assert tools.memory_get({}).ok is False
    assert tools.memory_get({"ids": "no-es-lista"}).ok is False
    assert tools.memory_get({"ids": [f"id-{i}" for i in range(21)]}).ok is False


def test_auto_recall_puts_the_index_not_the_content_in_the_context() -> None:
    recall = _build_auto_recall(_FakeAPI())
    assert recall is not None
    out = recall({"title": "Auditar dependencias", "description": "fijar versiones"})
    assert [e["id"] for e in out] == [_ID_A, _ID_B]
    assert out[0]["headline"] == "composer.lock se comitea"
    # el índice NO arrastra el contenido: eso lo pide el agente con memory_get
    assert all("content" not in e for e in out)


def test_auto_recall_limit_is_higher_now_that_a_hit_is_cheap() -> None:
    from agent_runtime.__main__ import _AUTO_RECALL_LIMIT

    assert _AUTO_RECALL_LIMIT >= 10


def test_the_preamble_lists_ids_and_tells_the_agent_to_use_memory_get() -> None:
    state = dict(initial_state({"title": "t", "description": ""}, system_preamble=""))
    state["steps"] = []
    state["context"] = [
        {"role": "memory", **_index_hit(_ID_A, "composer.lock se comitea")},
        {"role": "memory", **_index_hit(_ID_B, "usar php 8.3")},
    ]
    user = _decide_messages(state)[-1].content
    assert _ID_A in user and _ID_B in user
    assert "memory_get" in user
    assert "[mem:" in user  # la convención de cita


def test_a_scripted_model_calls_memory_get_and_receives_the_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _FakeAPI()
    monkeypatch.setattr("agent_runtime.__main__._build_internal_api", lambda: api)
    spec = {
        "task": {"id": "t-1", "title": "Auditar dependencias", "description": "x"},
        "model": {
            "kind": "scripted",
            "decisions": [
                {"kind": "act", "tool": "memory_get", "tool_args": {"ids": [_ID_A]}},
                {"kind": "finish", "output": f"hecho [mem:{_ID_A[:8]}]"},
            ],
            "reviews": [{"passed": True}],
        },
    }
    assert run_task(spec) == 0
    events = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    acts = [
        e["step"] for e in events if e.get("event") == "step" and e["step"].get("node") == "act"
    ]
    assert len(acts) == 1, acts
    assert acts[0]["result"]["ok"] is True
    assert acts[0]["result"]["output"]["memories"][0]["content"] == _FULL
    assert api.get_calls == [[_ID_A]]
