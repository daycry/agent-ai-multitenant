"""Recall automático de memorias en el boot (revisión memorias 2026-07-03, D1).

El nodo `recall` del grafo llevaba desde Plan 02 siendo un stub («placeholder
until Plan 04»): `AgentDeps.recall` por defecto es `_no_recall` y `run_task`
nunca lo cableaba, así que las memorias del tenant solo llegaban al agente si
el LLM decidía llamar la tool `memory_recall` (históricamente 2/15 runs). Estos
tests pinnean el cableado real: el boot construye un recall que consulta
`/internal/agent/memory-recall` (scope-safe server-side) con la task como
query, best-effort — un fallo del API jamás rompe el run.

Self-contained — sin DB/Redis/Docker: el cliente interno se monkeypatchea.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent_runtime.__main__ import run_task

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
        "task": {"id": "t-1", "title": "Auditar dependencias", "description": "fijar versiones"},
        "model": dict(_FINISH_ONLY),
    }


class _FakeAPI:
    """Doble del InternalAgentAPI: registra la query y devuelve hits fijos."""

    def __init__(self, hits: list[dict[str, Any]] | Exception) -> None:
        self._hits = hits
        self.queries: list[str] = []

    def memory_recall(self, *, query: str, limit: int = 5, **_: Any) -> list[dict[str, Any]]:
        self.queries.append(query)
        if isinstance(self._hits, Exception):
            raise self._hits
        return self._hits[:limit]


def test_recall_node_injects_memories_from_internal_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = _FakeAPI(
        [
            {"memory_id": "m-1", "content": "composer.lock se comitea", "scope": "project_shared"},
            {"memory_id": "m-2", "content": "usar php 8.3", "scope": "team_shared"},
        ]
    )
    monkeypatch.setattr("agent_runtime.__main__._build_internal_api", lambda: api)

    step = _recall_step(_run(_spec(), capsys))

    assert step["hits"] == 2
    # Ya no es un placeholder: hay recall real cableado.
    assert not step.get("placeholder")
    assert "placeholder" not in step["summary"]
    # La query se construye de la task (título + descripción).
    assert api.queries and "Auditar dependencias" in api.queries[0]


def test_recall_is_best_effort_api_error_never_breaks_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "agent_runtime.__main__._build_internal_api", lambda: _FakeAPI(RuntimeError("api caída"))
    )

    events = _run(_spec(), capsys)

    step = _recall_step(events)
    assert step["hits"] == 0
    finished = [e for e in events if e.get("event") == "execution.finished"]
    assert finished and finished[0]["result"]["status"] == "done"


def test_recall_without_internal_api_stays_empty_and_honest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sin token/URL del API interno (bare run) no hay recall: hits=0 y el step
    se declara placeholder — la honestidad pre-existente se conserva."""
    monkeypatch.setattr("agent_runtime.__main__._build_internal_api", lambda: None)

    step = _recall_step(_run(_spec(), capsys))

    assert step["hits"] == 0
    assert step.get("placeholder") is True


def test_recall_caps_hits_and_content_size(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """El contexto no se infla: máx 5 hits y contenido truncado."""
    api = _FakeAPI(
        [
            {"memory_id": f"m-{i}", "content": "x" * 5000, "scope": "project_shared"}
            for i in range(9)
        ]
    )
    monkeypatch.setattr("agent_runtime.__main__._build_internal_api", lambda: api)

    step = _recall_step(_run(_spec(), capsys))

    assert step["hits"] == 5


def test_recall_query_includes_role_and_acceptance_criteria(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """P1-3: la query del recall lleva rol + criterios, no solo el título."""
    api = _FakeAPI([])
    monkeypatch.setattr("agent_runtime.__main__._build_internal_api", lambda: api)

    spec = _spec()
    spec["agent_persona"] = {"prompt": "x", "role": "backend_dev"}
    spec["task"]["acceptance_criteria"] = ["endpoint devuelve 200", "cobertura > 70%"]
    _run(spec, capsys)

    assert api.queries, "no hubo query de recall"
    query = api.queries[0]
    assert "backend_dev" in query
    assert "endpoint devuelve 200" in query
