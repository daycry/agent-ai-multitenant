"""ADR 0114 — rail de ask_human fuera del runtime.

Tres costuras: (1) la categoría ``human_question`` SIEMPRE requiere humano
(bypasa la política por categorías del proyecto); (2) ``run_spec`` transporta
``human_answers`` al spec del runtime; (3) el dispatcher lee las Q&A
respondidas de los ApprovalRequest aprobados de la task.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


def test_human_question_always_requires_human() -> None:
    from api_server.db.approval_repo import requires_human_approval

    # Sin política, con política vacía y con la categoría marcada auto:
    # human_question SIEMPRE va a humano.
    assert requires_human_approval(None, "human_question") is True
    assert requires_human_approval({}, "human_question") is True
    assert (
        requires_human_approval({"categories": {"human_question": "auto"}}, "human_question")
        is True
    )
    # Las demás categorías conservan su semántica (unlisted → auto → False).
    assert requires_human_approval(None, "deploy") is False


def _request(answers: list[dict[str, Any]] | None) -> Any:
    from workers.execution import ExecutionRequest

    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=str(uuid4()),
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "ollama"},
        human_answers=answers,
    )


def test_run_spec_threads_human_answers() -> None:
    from workers.execution import _agent_spec

    spec = _agent_spec(_request([{"question": "¿REST?", "answer": "sí"}]), None)
    assert spec["human_answers"] == [{"question": "¿REST?", "answer": "sí"}]


def test_run_spec_omits_empty_human_answers() -> None:
    from workers.execution import _agent_spec

    assert "human_answers" not in _agent_spec(_request(None), None)
    assert "human_answers" not in _agent_spec(_request([]), None)


def test_execution_request_roundtrips_human_answers() -> None:
    from workers.execution import ExecutionRequest

    rebuilt = ExecutionRequest.from_dict(_request([{"question": "q", "answer": "a"}]).as_dict())
    assert rebuilt.human_answers == [{"question": "q", "answer": "a"}]


@pytest.mark.asyncio
async def test_dispatcher_reads_answered_questions() -> None:
    from orchestrator.dispatch import TaskDispatcher

    class _Rows:
        def __init__(self, rows: list[tuple[Any, Any]]) -> None:
            self._rows = rows

        def all(self) -> list[tuple[Any, Any]]:
            return self._rows

    class _Session:
        async def execute(self, stmt: Any) -> _Rows:
            return _Rows(
                [
                    ({"tool": "ask_human", "args": {"question": "¿REST?"}}, "sí, REST"),
                    ({"tool": "ask_human", "args": {"question": ""}}, "huérfana"),
                    ({"tool": "ask_human", "args": {"question": "¿BD?"}}, None),
                ]
            )

    class _Task:
        id = uuid4()
        tenant_id = uuid4()

    dispatcher = TaskDispatcher.__new__(TaskDispatcher)  # sin __init__ (solo el método)
    answers = await dispatcher._read_prior_human_answers(_Session(), _Task())
    # Solo la Q&A completa sobrevive (pregunta vacía / respuesta ausente fuera).
    assert answers == [{"question": "¿REST?", "answer": "sí, REST"}]
