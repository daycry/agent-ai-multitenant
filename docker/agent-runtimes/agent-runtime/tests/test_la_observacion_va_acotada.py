"""El tope de una observación se aplica donde nace, y se anuncia (`task_cv_21`, D-02).

Auditoría 2026-09-01. Una lectura de 900 KB entraba DOS veces al prompt (como
`last_observation` y como entrada del contexto: 1,8 M de caracteres, ~450k
tokens en un turno) y entera al `steps_log`; el presupuesto de tokens se
evaluaba al turno siguiente, con el gasto ya hecho. Ahora `act()` recorta la
salida de la tool a `_MAX_OBSERVATION_CHARS` con un marcador explícito al estilo
de `list_files` («showing the first N of M chars; use offset/limit»), el step
guarda el `result` recortado con `truncated` y `bytes_total`, y `read_file`
acepta `offset`/`limit` reales para leer el resto por tramos.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_runtime.file_tools import WorkspaceFiles
from agent_runtime.graph import _MAX_OBSERVATION_CHARS, AgentDeps, _AgentLoop
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import initial_state
from agent_runtime.tools import ToolRegistry, ToolResult

_BIG = "x" * 900_000


class _NoModel:
    def decide(self, state: Any) -> Any:  # noqa: ARG002  # pragma: no cover
        raise AssertionError

    def review(self, state: Any) -> Any:  # noqa: ARG002  # pragma: no cover
        raise AssertionError


def _loop_with(tool_fn: Any) -> _AgentLoop:
    registry = ToolRegistry()
    registry.register("big_read", tool_fn)
    deps = AgentDeps(model=_NoModel(), tools=registry)  # type: ignore[arg-type]
    return _AgentLoop(deps, SafeguardTracker(Budgets()), LoopDetector())


def _state_calling(tool: str) -> dict[str, Any]:
    state = dict(initial_state({"title": "t", "description": ""}, system_preamble=""))
    state["steps"] = []
    state["context"] = []
    state["last_decision"] = {"tool": tool, "tool_args": {"path": "big.txt"}}
    return state


def test_a_huge_tool_output_is_capped_in_the_observation_and_in_the_step() -> None:
    loop = _loop_with(
        lambda _args: ToolResult(ok=True, output={"path": "big.txt", "content": _BIG})
    )

    out = loop.act(_state_calling("big_read"))  # type: ignore[arg-type]

    observation = out["last_observation"]
    assert len(json.dumps(observation)) < 30_000, "la observación entera fue al prompt"
    assert "showing the first" in json.dumps(observation)
    assert "offset" in json.dumps(observation)
    step = out["steps"][0]
    assert step["result"]["truncated"] is True
    assert step["result"]["bytes_total"] >= 900_000
    assert len(json.dumps(step["result"])) < 30_000, "el steps_log se llevó los 900 KB"
    assert _MAX_OBSERVATION_CHARS <= 32_000


def test_a_small_output_is_untouched() -> None:
    loop = _loop_with(lambda _args: ToolResult(ok=True, output={"content": "hola"}))
    out = loop.act(_state_calling("big_read"))  # type: ignore[arg-type]
    assert out["last_observation"]["output"] == {"content": "hola"}
    assert "truncated" not in out["steps"][0]["result"]


def test_read_file_pages_with_offset_and_limit(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("0123456789" * 10, encoding="utf-8")
    files = WorkspaceFiles(str(tmp_path))

    page = files.file_read({"path": "big.txt", "offset": 10, "limit": 5})

    assert page.ok is True, page.error
    assert page.output["content"] == "01234"
    assert page.output["offset"] == 10
    assert page.output["total_chars"] == 100
    assert page.output["truncated"] is True

    whole = files.file_read({"path": "big.txt"})
    assert whole.ok and whole.output["content"] == "0123456789" * 10
    assert "truncated" not in whole.output


def test_read_file_rejects_a_bad_offset(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("abc", encoding="utf-8")
    bad = WorkspaceFiles(str(tmp_path)).file_read({"path": "f.txt", "offset": -1})
    assert bad.ok is False and "offset" in str(bad.error)
