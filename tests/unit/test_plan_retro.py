"""Retrospectiva automática de planes (ADR 0124) — núcleo puro con fakes.

Al cerrarse un plan (completed/cancelled), el PM destila la retro — qué se
atascó, reintentos, escalados, coste, lección — y la persiste como memoria
`project_shared`: los agentes del siguiente plan la recuerdan. El beat barre
planes recién cerrados sin retro (marker en Redis, sin migración); el LLM
solo redacta la lección (fail-open a la versión estructurada).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from workers.plan_retro import ClosedPlan, PlanStats, _format_retro, _run_retros

pytestmark = pytest.mark.unit


def _plan(**over: Any) -> ClosedPlan:
    base: dict[str, Any] = {
        "plan_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "project_id": str(uuid4()),
        "title": "Plan CI4",
        "status": "completed",
    }
    base.update(over)
    return ClosedPlan(**base)


_STATS = PlanStats(
    tasks_total=6,
    tasks_done=5,
    tasks_cancelled=1,
    runs_total=14,
    runs_escalated=2,
    runs_aborted=3,
    total_cost_usd=4.5678,
    duration_hours=26.5,
)


class _Marker:
    def __init__(self, done: set[str] | None = None) -> None:
        self.done = done or set()

    async def is_done(self, plan_id: str) -> bool:
        return plan_id in self.done

    async def mark(self, plan_id: str) -> None:
        self.done.add(plan_id)


class _Persister:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def save(self, *, plan: ClosedPlan, content: str) -> None:
        self.saved.append({"plan": plan, "content": content})


class _OkLLM:
    async def complete(self, messages: Any, **_: Any) -> Any:
        class R:
            content = "Lección: acotar mejor los criterios de aceptación."

        return R()

    async def aclose(self) -> None:
        pass


class _BrokenLLM:
    async def complete(self, messages: Any, **_: Any) -> Any:
        raise RuntimeError("down")

    async def aclose(self) -> None:
        pass


def _run(plans, marker=None, llm_factory=None, stats=_STATS):
    persister = _Persister()
    marker = marker or _Marker()

    async def collector(_plan: ClosedPlan) -> PlanStats:
        return stats

    result = asyncio.run(
        _run_retros(
            plans=plans,
            marker=marker,
            collector=collector,
            llm_factory=llm_factory or (lambda _t: _OkLLM()),
            persister=persister,
        )
    )
    return persister, marker, result


def test_format_retro_carries_the_numbers_and_the_status() -> None:
    text = _format_retro(_plan(), _STATS)
    for needle in ("Plan CI4", "completed", "5/6", "2", "3", "$4.57", "26.5"):
        assert needle in text


def test_retro_is_persisted_once_and_marked() -> None:
    plan = _plan()
    persister, marker, result = _run([plan])
    assert result == {"processed": 1, "skipped": 0}
    assert len(persister.saved) == 1
    assert "Lección" in persister.saved[0]["content"]
    # Idempotencia: la segunda pasada la salta por el marker.
    persister2, _, result2 = _run([plan], marker=marker)
    assert result2 == {"processed": 0, "skipped": 1}
    assert persister2.saved == []


def test_llm_failure_persists_the_structured_retro() -> None:
    persister, _, _ = _run([_plan()], llm_factory=lambda _t: _BrokenLLM())
    assert len(persister.saved) == 1
    assert "$4.57" in persister.saved[0]["content"]  # fail-open: la retro nunca se pierde


def test_a_failing_plan_does_not_stop_the_rest() -> None:
    bad, good = _plan(title="Malo"), _plan(title="Bueno")
    persister = _Persister()
    marker = _Marker()

    async def collector(plan: ClosedPlan) -> PlanStats:
        if plan.title == "Malo":
            raise RuntimeError("db hiccup")
        return _STATS

    result = asyncio.run(
        _run_retros(
            plans=[bad, good],
            marker=marker,
            collector=collector,
            llm_factory=lambda _t: _OkLLM(),
            persister=persister,
        )
    )
    assert result == {"processed": 1, "skipped": 1}
    assert [s["plan"].title for s in persister.saved] == ["Bueno"]


# ---------------------------------------------------------------------------
# task_wf_34: la retro se ata a SU plan
# ---------------------------------------------------------------------------
class _CapturingSession:
    """Sesión mínima que retiene los parámetros del INSERT."""

    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self._sink = sink

    async def execute(self, _stmt: Any, params: dict[str, Any]) -> None:
        self._sink.append(params)

    def begin(self) -> Any:
        return _AsyncNull()

    async def __aenter__(self) -> _CapturingSession:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _AsyncNull:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def test_the_retro_carries_the_id_of_its_plan() -> None:
    # Se guardaba con `tags` fijo a ["plan_retro"], así que una vez escrita no
    # había forma de saber de qué plan era: el detalle del plan no podía
    # enseñarla y la retro se escribía para nadie.
    import json

    from workers.plan_retro import DbRetroPersister

    captured: list[dict[str, Any]] = []
    plan = _plan()
    persister = DbRetroPersister(lambda: _CapturingSession(captured))
    asyncio.run(persister.save(plan=plan, content="Retrospectiva…"))

    assert len(captured) == 1
    tags = json.loads(captured[0]["tags"])
    assert tags == ["plan_retro", f"plan:{plan.plan_id}"]


def test_the_plan_tag_is_the_same_string_both_sides_read() -> None:
    # El worker la escribe y el api-server la consulta: si las dos mitades
    # componen la etiqueta por su cuenta, divergen y la retro deja de
    # encontrarse sin que ningún test lo note.
    from shared_domain.memory_tags import retro_plan_tag

    assert retro_plan_tag("abc") == "plan:abc"
