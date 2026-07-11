"""P0-2 (investigación 2026-07-11): auto-inyección de KB al arrancar el run.

Las memorias YA se auto-inyectaban en el nodo ``recall`` (D1), pero los pasajes
de la KB no: el agente solo veía el conocimiento del tenant si ÉL decidía llamar
la tool ``rag_search`` — un modelo flojo no la invocaba nunca. Ahora el boot
cablea además ``AgentDeps.knowledge`` (``_build_auto_rag``): consulta
``/internal/agent/rag-search`` con la task como query e inyecta los pasajes al
contexto inicial, escaneados por el guardrail como input no confiable (ADR
0102), igual que las memorias. Best-effort — un fallo del API jamás rompe el
run; bare run sin API interno = sin knowledge, honesto.

Self-contained — sin DB/Redis/Docker: el cliente interno se monkeypatchea.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent_runtime.__main__ import _build_auto_rag, run_task

_FINISH_ONLY = {
    "kind": "scripted",
    "decisions": [{"kind": "finish", "output": "done"}],
    "reviews": [{"passed": True}],
}


def _run(spec: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    rc = run_task(spec)
    assert rc == 0
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _recall_step(events: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [
        e["step"] for e in events if e.get("event") == "step" and e["step"].get("node") == "recall"
    ]
    assert len(steps) == 1, steps
    return steps[0]


def _spec() -> dict[str, Any]:
    return {
        "task": {"id": "t-1", "title": "Endpoint de facturas", "description": "con paginación"},
        "model": dict(_FINISH_ONLY),
    }


class _FakeAPI:
    """Doble del InternalAgentAPI con memoria y KB."""

    def __init__(
        self,
        *,
        memories: list[dict[str, Any]] | None = None,
        chunks: list[dict[str, Any]] | Exception | None = None,
    ) -> None:
        self._memories = memories or []
        self._chunks = chunks if chunks is not None else []
        self.rag_queries: list[str] = []

    def memory_recall(self, *, query: str, limit: int = 5, **_: Any) -> list[dict[str, Any]]:
        del query  # la query de memoria no se afirma en estos tests
        return self._memories[:limit]

    def rag_search(self, *, query: str, limit: int = 5, **_: Any) -> list[dict[str, Any]]:
        self.rag_queries.append(query)
        if isinstance(self._chunks, Exception):
            raise self._chunks
        return self._chunks[:limit]


def test_recall_node_also_injects_kb_passages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _FakeAPI(
        memories=[{"content": "recuerda X", "scope": "project_shared"}],
        chunks=[
            {"content": "Las facturas usan el patrón Repository.", "kb_id": "kb-1"},
            {"content": "La paginación es cursor-based.", "kb_id": "kb-1"},
        ],
    )
    monkeypatch.setattr("agent_runtime.__main__._build_internal_api", lambda: api)

    step = _recall_step(_run(_spec(), capsys))

    # El summary declara ambos: memorias Y pasajes de conocimiento.
    assert "2 knowledge" in step["summary"]
    assert not step.get("placeholder")
    # La query del RAG se construye de la task.
    assert api.rag_queries and "Endpoint de facturas" in api.rag_queries[0]


def test_auto_rag_is_best_effort_api_error_never_breaks_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _FakeAPI(chunks=RuntimeError("rag caído"))
    monkeypatch.setattr("agent_runtime.__main__._build_internal_api", lambda: api)

    step = _recall_step(_run(_spec(), capsys))
    assert "0 knowledge" in step["summary"]


def test_bare_run_without_api_has_no_knowledge_and_stays_placeholder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("agent_runtime.__main__._build_internal_api", lambda: None)
    step = _recall_step(_run(_spec(), capsys))
    assert step.get("placeholder")
    assert "knowledge" not in step["summary"]


def test_build_auto_rag_caps_and_sanitizes() -> None:
    api = _FakeAPI(
        chunks=[
            {"content": "x" * 5000, "kb_id": "kb-1"},
            "no soy un dict",  # type: ignore[list-item]
            {"content": "ok", "kb_id": "kb-3"},
        ]
    )
    auto_rag = _build_auto_rag(api)
    assert auto_rag is not None
    hits = auto_rag({"id": "t", "title": "T", "description": "d"})
    contents = [h["content"] for h in hits]
    assert "ok" in contents
    assert all(len(c) <= 700 for c in contents)
    assert all(isinstance(h, dict) and h["content"] for h in hits)


def test_build_auto_rag_none_without_api() -> None:
    assert _build_auto_rag(None) is None
