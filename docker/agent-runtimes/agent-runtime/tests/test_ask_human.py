"""ADR 0114 — ask_human: pregunta a humano NO terminal durante un run.

El escalado existente es TERMINAL (blocked + inbox): una ambigüedad pequeña
costaba el run entero. `ask_human(question, options?)` es una capacidad del
LOOP (patrón `update_plan`): el nodo `plan` la intercepta y PARQUEA el run por
la maquinaria de aprobaciones existente (category `human_question` →
ApprovalRequest → task a awaiting_human_approval → al responder, BACKLOG y
re-dispatch). La respuesta humana viaja al siguiente run como preámbulo
`human_answers` (rail de run_spec, como prior_failure/comments).
"""

from __future__ import annotations

from agent_runtime.__main__ import assemble_system_preamble, build_human_answers_preamble
from agent_runtime.graph import AgentDeps, _AgentLoop
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.model import (
    DecisionKind,
    ModelDecision,
    ModelResponse,
    ScriptedModelClient,
)
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import STATUS_AWAITING_APPROVAL, initial_state


def _loop_with(decision: ModelDecision) -> _AgentLoop:
    model = ScriptedModelClient(decisions=[ModelResponse(decision=decision)])
    return _AgentLoop(AgentDeps(model=model), SafeguardTracker(Budgets()), LoopDetector())


def test_plan_parks_on_ask_human() -> None:
    loop = _loop_with(
        ModelDecision(
            kind=DecisionKind.ACT,
            tool="ask_human",
            tool_args={"question": "¿REST o GraphQL para la API pública?"},
        )
    )
    state = initial_state({"id": "t", "title": "T", "description": ""})
    delta = loop.plan(state)
    assert delta["status"] == STATUS_AWAITING_APPROVAL
    approval = delta["approval"]
    assert approval["category"] == "human_question"
    assert approval["action"]["tool"] == "ask_human"
    assert approval["action"]["args"]["question"].startswith("¿REST o GraphQL")


def test_plan_parks_on_ask_human_with_options() -> None:
    loop = _loop_with(
        ModelDecision(
            kind=DecisionKind.ACT,
            tool="ask_human",
            tool_args={"question": "¿Qué BD?", "options": ["postgres", "mysql"]},
        )
    )
    delta = loop.plan(initial_state({"id": "t", "title": "T", "description": ""}))
    assert delta["approval"]["action"]["args"]["options"] == ["postgres", "mysql"]


def test_ask_human_without_question_does_not_park() -> None:
    # Sin pregunta no hay nada que preguntar: el turno degrada a un noop con
    # razón visible (el modelo VE cómo corregirlo) en vez de parquear en vacío.
    loop = _loop_with(ModelDecision(kind=DecisionKind.ACT, tool="ask_human", tool_args={}))
    delta = loop.plan(initial_state({"id": "t", "title": "T", "description": ""}))
    assert "approval" not in delta
    assert delta["last_decision"]["tool"] == "noop"
    assert "question" in str(delta["last_decision"]["tool_args"].get("reason", ""))


# --- preámbulo de respuestas humanas (rail human_answers) ---------------------


def test_build_human_answers_preamble_renders_qa() -> None:
    preamble = build_human_answers_preamble(
        [
            {"question": "¿REST o GraphQL?", "answer": "REST, versionado /v1"},
            {"question": "¿Qué BD?", "answer": "postgres"},
        ]
    )
    assert "¿REST o GraphQL?" in preamble
    assert "REST, versionado /v1" in preamble
    assert "postgres" in preamble
    # La instrucción deja claro que son respuestas AUTORITATIVAS del humano.
    assert "HUMAN" in preamble.upper()


def test_build_human_answers_preamble_empty() -> None:
    assert build_human_answers_preamble([]) == ""
    assert build_human_answers_preamble(None) == ""
    # Entradas sin respuesta se omiten (una pregunta aún pendiente no guía).
    assert build_human_answers_preamble([{"question": "¿?", "answer": ""}]) == ""


def test_assemble_preamble_includes_human_answers() -> None:
    preamble = assemble_system_preamble(
        {
            "human_answers": [{"question": "¿REST?", "answer": "sí, REST"}],
            "task_comments": [{"scope": "task", "content": "cuidado con la paginación"}],
        }
    )
    assert preamble is not None
    # Las respuestas humanas van DESPUÉS de los comentarios (los comentarios
    # contextualizan; la respuesta es la resolución puntual más reciente).
    assert preamble.index("paginación") < preamble.index("sí, REST")
