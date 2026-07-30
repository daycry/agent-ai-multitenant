"""El bus de eventos de plan (`task_wf_32`) y quién está obligado a usarlo.

El tablero gerencial lista los planes de todo el tenant y no se refrescaba: las
dos transiciones que ocurren SIN gesto humano (`pending_human_validation` y
`blocked`) se quedaban invisibles hasta que alguien recargaba.

Lo que aquí se fija no es solo que el publicador funcione, sino **que nadie
mueva un plan sin anunciarlo**: el estado de plan se escribe desde tres
servicios, y cuatro de esos sitios usan UPDATE crudo saltándose la máquina de
estados que se declara «la única puerta».
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]


class _FakeRedis:
    def __init__(self) -> None:
        self.added: list[tuple[str, dict[str, Any]]] = []

    async def xadd(self, stream: str, fields: dict[str, Any], **_kw: Any) -> None:
        self.added.append((stream, fields))


@pytest.mark.asyncio
async def test_a_plan_move_lands_on_its_own_stream_with_both_ends() -> None:
    from api_server.events import PLANS_STREAM, publish_plan_status_changed

    redis = _FakeRedis()
    await publish_plan_status_changed(
        redis,
        plan_id="p1",
        tenant_id="t1",
        project_id="pr1",
        old_status="in_progress",
        new_status="blocked",
        title="Plan CI4",
    )
    stream, fields = redis.added[0]
    # Stream propio: `events:tasks` lo consume el orchestrator con XREADGROUP
    # para asignar tareas, y un evento de plan ahí solo es trabajo que descartar.
    assert stream == PLANS_STREAM
    assert fields["type"] == "plan.status_changed"
    assert fields["tenant_id"] == "t1"
    assert fields["plan_id"] == "p1"
    # El estado ANTERIOR viaja también: el tablero no siempre sabe de dónde
    # venía la tarjeta (pudo abrirse ya con el estado nuevo).
    assert '"old_status": "in_progress"' in fields["payload"]
    assert '"new_status": "blocked"' in fields["payload"]


@pytest.mark.asyncio
async def test_a_move_that_moves_nothing_is_not_announced() -> None:
    # Las transiciones se escriben con guarda atómica idempotente: el camino
    # perdedor no cambia nada y no debe empujar ruido al tablero.
    from api_server.events import publish_plan_status_changed

    redis = _FakeRedis()
    await publish_plan_status_changed(
        redis,
        plan_id="p1",
        tenant_id="t1",
        project_id="pr1",
        old_status="blocked",
        new_status="blocked",
    )
    assert redis.added == []


@pytest.mark.asyncio
async def test_a_redis_blip_never_breaks_the_transition() -> None:
    # El plan ya está movido y commiteado cuando se anuncia: que el bus falle
    # no puede convertirse en una excepción aguas arriba.
    from api_server.events import publish_plan_status_changed

    class _Broken:
        async def xadd(self, *_a: Any, **_k: Any) -> None:
            raise RuntimeError("redis caído")

    await publish_plan_status_changed(
        _Broken(),
        plan_id="p1",
        tenant_id="t1",
        project_id="pr1",
        old_status="in_progress",
        new_status="completed",
    )


# ---------------------------------------------------------------------------
# El guard que impide que esto se vuelva a desincronizar
# ---------------------------------------------------------------------------
_PRODUCTION_ROOTS = (
    _REPO / "apps" / "api-server" / "src",
    _REPO / "apps" / "orchestrator" / "src",
    _REPO / "apps" / "workers" / "src",
)

# Escribe el estado de un plan: o por la máquina de estados, o con UPDATE crudo.
_WRITES_PLAN_STATUS = re.compile(
    r"transition_plan_status\(|update\(Plan\)[\s\S]{0,400}?\.values\(status=",
)
# Anuncia el movimiento por cualquiera de las tres vías (directa, post-commit,
# o el envoltorio que hace las dos cosas).
_ANNOUNCES = re.compile(
    r"publish_plan_status_changed|publish_plan_transition_after_commit"
    r"|_announce_plan_move|_announce_expired_plan_move|move_plan\(",
)


def _modules_writing_plan_status() -> list[Path]:
    hits = []
    for root in _PRODUCTION_ROOTS:
        for path in root.rglob("*.py"):
            if _WRITES_PLAN_STATUS.search(path.read_text(encoding="utf-8")):
                hits.append(path)
    return hits


def test_every_module_that_moves_a_plan_also_announces_it() -> None:
    """El invariante de `task_wf_32`, en forma de test.

    `plan_state_machine` se declara «la única puerta» y no lo es: hay UPDATE
    crudo en el orchestrator y en el reconciler (por la guarda atómica, que es
    legítima). Enganchar el publicador solo a la máquina de estados dejaría sin
    anunciar justo las dos transiciones que motivan la tarea.

    Este test no exige que todo pase por la puerta — exige que quien escriba el
    estado de un plan, por donde sea, lo anuncie. Un sitio nuevo lo rompe.
    """
    writers = _modules_writing_plan_status()
    # Si esto queda en cero, el regex dejó de encontrar los escritores y el
    # test pasaría vacío para siempre.
    assert len(writers) >= 4, [str(p) for p in writers]

    silent = [
        str(p.relative_to(_REPO))
        for p in writers
        if not _ANNOUNCES.search(p.read_text(encoding="utf-8"))
        # La máquina de estados es la pieza PURA: define la transición, no la
        # ejecuta contra una sesión ni conoce el bus.
        and p.name != "plan_state_machine.py"
    ]
    assert silent == [], (
        "Estos módulos mueven el estado de un plan sin anunciarlo al tablero "
        f"gerencial: {silent}. Usa `move_plan` (api-server) o llama a "
        "`publish_plan_status_changed` tras tu commit."
    )
