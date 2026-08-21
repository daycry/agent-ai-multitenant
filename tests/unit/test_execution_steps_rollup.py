"""La proyección de `steps_log` en columnas (prod-13 task_prod13_18).

`executions.last_model` / `tokens_in` / `tokens_out` (migración 0139) existen
para que el explorador de runs deje de expandir el JSONB con
`jsonb_array_elements` en cada consulta. Lo único que las mantiene honestas es
que se calculan **exactamente** igual que lo hacían las expresiones SQL a las
que sustituyen; si divergen, el panel enseña cifras distintas de las de ayer sin
que nada falle.

Estos tests fijan esa equivalencia sobre el cálculo en Python. La equivalencia
con el backfill SQL de la migración —la otra mitad del riesgo— se comprueba en
`tests/integration/test_runs_listing_no_steps_log.py`, contra PostgreSQL.

El último bloque fija la otra mitad del riesgo en Python: que **todo** el que
asigna `steps_log` recalcula la proyección. El diseño decía que solo había un
escritor (`db/execution_repo.py`) y era falso —`workers.execution
._mark_commit_failed` anexa el paso del conflicto de rebase en su propia sesión
BYPASSRLS—, así que la garantía que se invocaba no existía.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from api_server.db.domain import Execution
from api_server.db.execution_repo import steps_rollup

pytestmark = pytest.mark.unit


def _call(index: int, model: str | None, tin: int, tout: int) -> dict[str, Any]:
    step: dict[str, Any] = {
        "kind": "model_call",
        "index": index,
        "tokens_in": tin,
        "tokens_out": tout,
    }
    if model is not None:
        step["model"] = model
    return step


def test_the_last_model_is_the_highest_index_not_the_last_element() -> None:
    """La expresión SQL ordenaba por el `index` del propio paso, no por la
    posición en el array. Un `steps_log` reconstruido fuera de orden tiene que
    dar el mismo resultado que daba PostgreSQL."""
    rollup = steps_rollup(
        [
            _call(2, "claude-opus-4", 10, 5),
            _call(0, "claude-haiku-4", 1, 1),
            _call(1, "claude-sonnet-4", 3, 2),
        ]
    )
    assert rollup.last_model == "claude-opus-4"


def test_tokens_are_summed_only_over_model_calls() -> None:
    rollup = steps_rollup(
        [
            {"kind": "node", "index": 0, "tokens_in": 999, "tokens_out": 999},
            _call(1, "m", 10, 5),
            {"kind": "tool_call", "index": 2, "tokens_in": 999},
            _call(3, "m", 7, 4),
        ]
    )
    assert (rollup.tokens_in, rollup.tokens_out) == (17, 9)


def test_a_run_without_model_calls_has_no_model_and_zero_tokens() -> None:
    """NULL significa «no llamó a ningún modelo», no «no lo sé». Es lo que
    devolvía la subconsulta correlacionada, y el filtro `?model=` depende de
    ello para no colar runs vacíos."""
    rollup = steps_rollup([{"kind": "node", "index": 0}])
    assert rollup.last_model is None
    assert (rollup.tokens_in, rollup.tokens_out) == (0, 0)


def test_a_model_call_without_a_model_name_does_not_win_the_last_model() -> None:
    """La expresión SQL excluía los pasos con `model` NULL (`model_txt IS NOT
    NULL`). Un paso abortado antes de resolver el proveedor no puede borrar el
    modelo del run."""
    rollup = steps_rollup([_call(0, "claude-sonnet-4", 1, 1), _call(1, None, 0, 0)])
    assert rollup.last_model == "claude-sonnet-4"


def test_missing_and_malformed_token_counts_count_as_zero() -> None:
    """Un `steps_log` viejo o mal formado NO puede hacer fallar el cierre del
    run: eso convertiría un dato cosmético en un run perdido."""
    rollup = steps_rollup(
        [
            {"kind": "model_call", "index": 0, "model": "m"},
            {"kind": "model_call", "index": 1, "model": "m", "tokens_in": None},
            {"kind": "model_call", "index": 2, "model": "m", "tokens_in": "cuatro"},
            _call(3, "m", 6, 2),
        ]
    )
    assert (rollup.tokens_in, rollup.tokens_out) == (6, 2)


def test_non_dict_entries_are_ignored() -> None:
    rollup = steps_rollup(["basura", None, _call(0, "m", 2, 1)])  # type: ignore[list-item]
    assert rollup.last_model == "m"
    assert (rollup.tokens_in, rollup.tokens_out) == (2, 1)


def test_steps_without_an_index_fall_back_to_their_position() -> None:
    rollup = steps_rollup(
        [
            {"kind": "model_call", "model": "primero", "tokens_in": 1, "tokens_out": 1},
            {"kind": "model_call", "model": "segundo", "tokens_in": 1, "tokens_out": 1},
        ]
    )
    assert rollup.last_model == "segundo"


def test_an_empty_log_is_a_zeroed_rollup() -> None:
    rollup = steps_rollup([])
    assert rollup.last_model is None
    assert (rollup.tokens_in, rollup.tokens_out) == (0, 0)


# ===========================================================================
# La invariante: quien asigne `steps_log` recalcula la proyección
# ===========================================================================
class _FakeSession:
    """Sesión mínima: `_mark_commit_failed` solo abre una txn y muta el objeto."""

    def begin(self) -> _FakeSession:
        return self

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def _sessionmaker() -> Any:
    def _make() -> _FakeSession:
        return _FakeSession()

    return _make


@pytest.mark.asyncio
async def test_the_worker_marker_reprojects_the_steps_log_it_appends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_mark_commit_failed` es el SEGUNDO escritor de `steps_log`.

    Hoy el paso que anexa es `kind: node`, que `steps_rollup` ignora, así que la
    proyección no se corrompe *por casualidad*. Este test fija la propiedad de
    verdad —«tras escribir `steps_log`, las columnas lo describen»— con un paso
    que SÍ cuenta: si mañana el paso del conflicto lleva tokens (o alguien anexa
    un `model_call` por esta vía), `last_model` / `tokens_in` / `tokens_out`
    mentirían sin que nada fallase.
    """
    from workers import execution as exec_mod

    execution = Execution(
        steps_log=[_call(0, "claude-sonnet-4", 10, 5)],
        last_model="claude-sonnet-4",
        tokens_in=10,
        tokens_out=5,
        output="entregable listo",
    )

    async def fake_get_execution(_session: Any, _execution_id: Any) -> Execution:
        return execution

    def fake_conflict_note(
        _abort_code: str, _ctx: dict[str, Any] | None, *, steps_len: int
    ) -> tuple[str, dict[str, Any]]:
        return "conflicto", _call(steps_len, "claude-opus-4", 7, 3)

    monkeypatch.setattr(exec_mod, "get_execution", fake_get_execution)
    monkeypatch.setattr(exec_mod, "_conflict_note", fake_conflict_note)

    await exec_mod._mark_commit_failed(
        _sessionmaker(),
        uuid4(),
        abort_code="rebase_conflict",
        conflict_context={"files": ["a.py"]},
    )

    # Primero, que el camino se recorrió de verdad: `_mark_commit_failed` se traga
    # cualquier excepción (es best-effort), así que un doble mal montado dejaría el
    # objeto intacto y la comparación de abajo pasaría sin haber probado nada.
    assert len(execution.steps_log) == 2, "el marcador no llegó a anexar el paso"

    expected = steps_rollup(execution.steps_log)
    assert (execution.last_model, execution.tokens_in, execution.tokens_out) == (
        expected.last_model,
        expected.tokens_in,
        expected.tokens_out,
    ), (
        "el marcador de commit fallido anexó un paso a steps_log sin recalcular la "
        "proyección: las columnas describen un steps_log que ya no existe"
    )


def test_the_repo_reprojects_when_it_seeds_an_empty_steps_log() -> None:
    """`create_running_execution` también asigna `steps_log` (vacío). No basta con
    que los `server_default` coincidan: la regla es «quien asigna, proyecta», y una
    excepción tácita es la que se olvida al añadir el escritor siguiente."""
    from api_server.db.execution_repo import apply_steps_rollup

    execution = Execution(steps_log=[], last_model="basura", tokens_in=99, tokens_out=99)
    apply_steps_rollup(execution, execution.steps_log)
    assert (execution.last_model, execution.tokens_in, execution.tokens_out) == (None, 0, 0)
