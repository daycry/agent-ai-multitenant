"""Restos de menor riesgo de la auditoría 2026-09-01, lo que el modelo ve (`task_cv_45`).

- D-08: la observación no llevaba `args`, así que el modelo no veía qué acción
  produjo cada resultado y la línea condensada de un `shell_exec` evictado era
  `- [observation] shell_exec True`. Ahora viaja `args` capado.
- D-09: `ask_human` sin techo de preguntas por task: cada respuesta re-despacha
  con presupuesto fresco. El dispatcher pasa el restante y el runtime deja de
  preguntar cuando se agota.
- D-10: en el lote read-only, el elemento que exige aprobación se expulsaba en
  silencio y `review` iba sin `args` (no se podía canjear una acción ya
  aprobada dentro de un lote). Ahora `review` recibe los args y lo expulsado se
  anuncia en la observación.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.graph import (
    AgentDeps,
    _AgentLoop,
    _compact_args,
    _filter_batch_for_approval,
)
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.model import DecisionKind, ModelDecision
from agent_runtime.providers import _condense_evicted
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import initial_state


class _Model:
    def decide(self, state: dict) -> Any:  # noqa: ARG002  # pragma: no cover
        raise AssertionError

    def review(self, state: dict) -> Any:  # noqa: ARG002  # pragma: no cover
        raise AssertionError


def _loop(**deps_kw: Any) -> _AgentLoop:
    deps = AgentDeps(model=_Model(), guardrails=None, **deps_kw)  # type: ignore[arg-type]
    return _AgentLoop(deps, SafeguardTracker(Budgets()), LoopDetector())


# ------------------------------------------------------------- D-08 args


def test_compact_args_is_short_and_readable() -> None:
    text = _compact_args({"cmd": "ls -la", "cwd": "/workspace", "big": "x" * 500})

    assert "ls -la" in text and "cwd" in text
    assert len(text) <= 200


def test_the_observation_carries_the_args_that_produced_it() -> None:
    loop = _loop()
    state = dict(initial_state({"title": "t", "description": ""}))
    state["steps"] = []
    state["last_decision"] = {
        "kind": "act",
        "tool": "noop",
        "tool_args": {"reason": "waiting for the cache"},
        "batch_calls": [],
    }

    out = loop.act(state)  # type: ignore[arg-type]

    observation = out["last_observation"]
    assert "waiting for the cache" in str(observation.get("args"))


def test_an_evicted_observation_line_names_its_args() -> None:
    lines = _condense_evicted(
        [{"role": "observation", "tool": "shell_exec", "ok": True, "args": "cmd=pytest -q"}]
    )

    assert lines == ["- [observation] shell_exec True cmd=pytest -q"]


# ------------------------------------------------------------- D-09 ask_human


def _ask(question: str = "¿Cuál es el puerto?") -> ModelDecision:
    return ModelDecision(kind=DecisionKind.ACT, tool="ask_human", tool_args={"question": question})


def test_an_exhausted_question_budget_turns_ask_human_into_a_visible_noop() -> None:
    loop = _loop(ask_human_remaining=0)

    decision, park = loop._maybe_park_ask_human(_ask(), [], 0)

    assert park is None
    assert decision.tool == "noop"
    assert "ask_human" in str(decision.tool_args.get("reason"))
    assert "exhausted" in str(decision.tool_args.get("reason")).lower()


def test_with_questions_left_ask_human_still_parks() -> None:
    loop = _loop(ask_human_remaining=2)

    decision, park = loop._maybe_park_ask_human(_ask(), [], 0)

    assert decision.tool == "ask_human"
    assert park is not None


def test_without_a_budget_from_the_dispatcher_nothing_changes() -> None:
    loop = _loop()

    decision, park = loop._maybe_park_ask_human(_ask(), [], 0)

    assert decision.tool == "ask_human" and park is not None


# ------------------------------------------------------------- D-10 batch


class _Gate:
    def __init__(self) -> None:
        self.seen: list[tuple[str, Any]] = []

    def review(self, tool: str | None, args: Any = None) -> str | None:
        self.seen.append((str(tool), args))
        return "network_egress" if tool == "http_post" else None


def test_the_batch_filter_reviews_with_args_and_announces_the_evicted() -> None:
    gate = _Gate()
    decision = ModelDecision(
        kind=DecisionKind.ACT,
        tool="read_file",
        tool_args={"path": "a"},
        batch_calls=(
            {"tool": "read_file", "args": {"path": "b"}},
            {"tool": "http_post", "args": {"url": "https://x"}},
        ),
    )

    filtered = _filter_batch_for_approval(decision, gate)  # type: ignore[arg-type]

    assert [c["tool"] for c in filtered.batch_calls] == ["read_file"]
    assert filtered.batch_dropped == (
        {"tool": "http_post", "args": {"url": "https://x"}, "category": "network_egress"},
    )
    assert ("http_post", {"url": "https://x"}) in gate.seen, "review fue sin args"


def test_the_act_observation_reports_what_the_batch_dropped() -> None:
    loop = _loop()
    state = dict(initial_state({"title": "t", "description": ""}))
    state["steps"] = []
    state["last_decision"] = {
        "kind": "act",
        "tool": "noop",
        "tool_args": {"reason": "x"},
        "batch_calls": [],
        "batch_dropped": [{"tool": "http_post", "args": {"url": "https://x"}, "category": "net"}],
    }

    out = loop.act(state)  # type: ignore[arg-type]

    dropped = out["last_observation"].get("batch_dropped")
    assert dropped and dropped[0]["tool"] == "http_post"


def test_the_decision_round_trips_batch_dropped() -> None:
    decision = ModelDecision(
        kind=DecisionKind.ACT, tool="noop", batch_dropped=({"tool": "x", "args": {}},)
    )

    assert decision.as_dict()["batch_dropped"] == [{"tool": "x", "args": {}}]
