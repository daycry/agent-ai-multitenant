"""prod-03 task_prod03_10 — el motor no corre DENTRO del event loop (guardrails-10).

`GuardrailPipeline.run` es síncrono y CPU-bound: regex sobre texto libre, cálculo
de entropía, `ast.parse`. El detector genérico de `secret_leakage` es
lineal-cuadrático en el peor caso. Ejecutarlo en el hilo del event loop del
api-server para en seco a TODAS las conexiones —cada WebSocket, cada request en
vuelo— mientras dura el escaneo, y el chat de planning es justo el sitio donde el
texto lo escribe un humano y puede ser arbitrariamente largo.

Esta es la costura que el plan exige cerrar ANTES de cablear el motor en más
hosts (Fase D): cablear un motor bloqueante multiplicaría el problema por cada
punto nuevo.

Lo que se fija aquí:

  * el `pipeline.run` de los dos hosts async de planning corre en OTRO hilo;
  * el texto que se le pasa está acotado (el escaneo cuadrático no se dispara
    porque alguien pegue un fichero entero en el chat), y el truncado se
    ANOTA — un escaneo parcial que se presenta como completo es peor que no
    escanear.
"""

from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

import pytest
from api_server.guardrails.planning import (
    MAX_SCANNED_CHARS,
    gate_generate_plan,
    run_planning_chat_guardrails,
)
from shared_guardrails.types import GuardrailContext, HookPoint, PipelineDecision


class _ThreadSpyPipeline:
    """Un pipeline de mentira que apunta en qué hilo lo llamaron y con qué texto."""

    def __init__(self) -> None:
        self.thread_ids: list[int] = []
        self.contexts: list[GuardrailContext] = []

    def run(self, context: GuardrailContext) -> PipelineDecision:
        self.thread_ids.append(threading.get_ident())
        self.contexts.append(context)
        return PipelineDecision(hook=context.hook, triggered=False, action=None, outcomes=[])


class _NullSession:
    """La sesión no se toca: sin outcomes disparados no hay nada que persistir."""

    def add(self, obj: Any) -> None:  # pragma: no cover - defensivo
        raise AssertionError("una decisión sin disparos no debe escribir en la BD")


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", ["pre_llm", "post_llm"])
async def test_the_chat_host_runs_the_engine_off_the_event_loop(hook: HookPoint) -> None:
    spy = _ThreadSpyPipeline()

    await run_planning_chat_guardrails(
        _NullSession(),  # type: ignore[arg-type]
        hook=hook,
        text="planificamos el proyecto",
        tenant_id=uuid4(),
        pipeline=spy,  # type: ignore[arg-type]
    )

    assert spy.thread_ids, "el pipeline ni se llamó"
    assert spy.thread_ids[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_the_plan_gate_runs_the_engine_off_the_event_loop() -> None:
    spy = _ThreadSpyPipeline()

    await gate_generate_plan(
        _NullSession(),  # type: ignore[arg-type]
        draft={"summary": {}, "tasks": []},
        tenant_id=uuid4(),
        pipeline=spy,  # type: ignore[arg-type]
    )

    assert spy.thread_ids
    assert spy.thread_ids[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_a_huge_chat_turn_is_truncated_and_says_so() -> None:
    """Un pegote de 1 MB no puede convertirse en un escaneo cuadrático de 1 MB."""
    spy = _ThreadSpyPipeline()

    await run_planning_chat_guardrails(
        _NullSession(),  # type: ignore[arg-type]
        hook="pre_llm",
        text="x" * (MAX_SCANNED_CHARS + 5_000),
        tenant_id=uuid4(),
        pipeline=spy,  # type: ignore[arg-type]
    )

    context = spy.contexts[0]
    assert context.prompt is not None
    assert len(context.prompt) == MAX_SCANNED_CHARS
    # Que el escaneo fue parcial tiene que verse en el evento, no solo aquí.
    assert context.metadata["truncated"] is True


@pytest.mark.asyncio
async def test_a_normal_chat_turn_is_not_marked_truncated() -> None:
    """La guarda de la guarda: lo normal no puede quedar marcado como parcial."""
    spy = _ThreadSpyPipeline()

    await run_planning_chat_guardrails(
        _NullSession(),  # type: ignore[arg-type]
        hook="post_llm",
        text="el equipo propone tres tareas",
        tenant_id=uuid4(),
        pipeline=spy,  # type: ignore[arg-type]
    )

    context = spy.contexts[0]
    assert context.response == "el equipo propone tres tareas"
    assert "truncated" not in context.metadata


@pytest.mark.asyncio
async def test_a_huge_draft_is_truncated_before_the_structural_gate() -> None:
    """El draft serializado entra por el mismo tope que el chat."""
    spy = _ThreadSpyPipeline()

    await gate_generate_plan(
        _NullSession(),  # type: ignore[arg-type]
        draft={"summary": {"notes": "y" * (MAX_SCANNED_CHARS + 5_000)}, "tasks": []},
        tenant_id=uuid4(),
        pipeline=spy,  # type: ignore[arg-type]
    )

    context = spy.contexts[0]
    assert context.response is not None
    assert len(context.response) == MAX_SCANNED_CHARS
    assert context.metadata["truncated"] is True
