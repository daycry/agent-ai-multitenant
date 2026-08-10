"""prod-13 · task_prod13_18 + paginación por keyset del explorador de runs.

Dos propiedades de la consulta que hay detrás de ``GET /tenant-stats/runs`` y de
su export. Ninguna necesita base de datos: son sobre el SQL que se construye, que
es donde vive el defecto.

**1. El listado no materializa `steps_log`** (hallazgo perf-6). `_fetch_runs`
seleccionaba la ENTIDAD ``Execution`` completa, así que cada fila arrastraba su
JSONB de traza. Medido sobre la instancia de desarrollo el 2026-08-01:
``steps_log`` es el **76 %** de la tabla ``executions`` (1.672 KiB de 2.208), con
una media de **9,5 KiB por run** y máximos de 64 KiB. El export llega a
``MAX_EXPORT_ROWS = 5000`` filas: del orden de **50 MiB de JSONB** materializados
en el proceso del api-server para producir un CSV que no contiene ni un byte de
esa traza.

**2. La paginación es por keyset, no por OFFSET.** ``OFFSET n`` obliga a
PostgreSQL a producir y descartar las n primeras filas: la página 500 cuesta 500
páginas de trabajo. Con el índice ``(tenant_id, created_at)`` de la 0126, un
predicado de fila ``(created_at, id) < (…)`` salta directo. El ``offset`` sigue
existiendo por compatibilidad — quitarlo rompería a los clientes de hoy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.unit


def _filters() -> list:
    from api_server.routers.tenant_stats import _exec_filters

    return _exec_filters(tenant_id=uuid4())


def _sql(**kwargs: object) -> str:
    from api_server.routers.tenant_stats import runs_select

    stmt = runs_select(_filters(), **kwargs)  # type: ignore[arg-type]
    return str(stmt.compile(compile_kwargs={"literal_binds": False}))


# ---------------------------------------------------------------------------
# 1. steps_log no viaja en la lista de columnas
# ---------------------------------------------------------------------------
def test_the_outer_select_does_not_carry_steps_log() -> None:
    """La comprobación se hace sobre las columnas SELECCIONADAS, no sobre el
    texto del SQL. Nació así porque ``steps_log`` aparecía legítimamente DENTRO
    de la subconsulta correlacionada que resolvía el modelo del último paso;
    desde la **migración 0139** eso es una columna (`last_model`) y el JSONB ya
    no aparece por ningún lado — la prohibición ENTERA la fija
    ``tests/integration/test_runs_listing_no_steps_log.py``. Aquí se conserva la
    comprobación estrecha porque es la que no puede envejecer: lo que nunca
    puede ocurrir es que la FILA lo traiga de vuelta.
    """
    from api_server.routers.tenant_stats import runs_select

    stmt = runs_select(_filters(), limit=50, offset=0)
    selected = [str(col) for col in stmt.selected_columns]
    assert "executions.steps_log" not in selected, f"la fila trae steps_log de vuelta: {selected}"


def test_the_select_does_not_load_the_execution_entity() -> None:
    """La forma exacta del defecto: ``select(Execution, …)`` trae TODAS las
    columnas mapeadas, incluida la que no se pide. Con columnas escalares
    explícitas eso deja de poder pasar por descuido."""
    from api_server.db.domain import Execution
    from api_server.routers.tenant_stats import runs_select

    stmt = runs_select(_filters(), limit=50, offset=0)
    # `entity` es la clase dueña TAMBIÉN para una columna escalar, así que no
    # distingue nada; `expr` sí: es la clase cuando se pide la entidad entera y
    # un `InstrumentedAttribute` cuando se pide una columna.
    # Comparación por IDENTIDAD: un `in` normal invoca el `__eq__` de SQLAlchemy,
    # que construye una expresión SQL en vez de responder sí o no.
    exprs = [desc["expr"] for desc in stmt.column_descriptions]
    loads_entity = any(expr is Execution for expr in exprs)
    assert (
        not loads_entity
    ), "el SELECT carga la entidad Execution entera: vuelve a arrastrar steps_log"


def test_every_column_the_row_needs_is_selected() -> None:
    """El contrapeso del test anterior. Cambiar una entidad por columnas
    posicionales es justo el refactor que se rompe por un desplazamiento de
    uno, así que se fija QUÉ columnas tienen que estar."""
    from api_server.routers.tenant_stats import runs_select

    stmt = runs_select(_filters(), limit=50, offset=0)
    selected = " | ".join(str(col) for col in stmt.selected_columns)
    for needed in (
        "executions.id",
        "executions.created_at",
        "executions.task_id",
        "executions.agent_id",
        "executions.status",
        "executions.finish_status",
        "executions.total_tokens",
        "executions.total_cost_usd",
        "executions.started_at",
        "executions.completed_at",
    ):
        assert needed in selected, f"falta {needed} en el SELECT: la fila saldría incompleta"


# ---------------------------------------------------------------------------
# 2. Keyset
# ---------------------------------------------------------------------------
def test_the_cursor_round_trips() -> None:
    from api_server.routers.tenant_stats import decode_runs_cursor, encode_runs_cursor

    moment = datetime(2026, 7, 31, 12, 34, 56, 789000, tzinfo=UTC)
    ident = uuid4()
    decoded = decode_runs_cursor(encode_runs_cursor(moment, ident))
    assert decoded == (moment, ident)


@pytest.mark.parametrize("bad", ["", "no-es-base64!!", "Zm9v", "MjAyNi0wMS0wMXxub3QtdXVpZA=="])
def test_a_broken_cursor_is_a_client_error(bad: str) -> None:
    """Un cursor corrupto es un 400, no un 500: lo manda el cliente."""
    from api_server.routers.tenant_stats import decode_runs_cursor
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        decode_runs_cursor(bad)
    assert excinfo.value.status_code == 400


def test_the_keyset_predicate_replaces_the_offset() -> None:
    """Con cursor no puede quedar OFFSET: sumar los dos saltaría filas."""
    from api_server.routers.tenant_stats import encode_runs_cursor

    cursor = encode_runs_cursor(datetime(2026, 7, 1, tzinfo=UTC), uuid4())
    sql = _sql(limit=50, offset=200, cursor=cursor)
    assert "OFFSET" not in sql.upper(), sql
    # Comparación de FILA (`(a, b) < (:x, :y)`), no dos predicados sueltos: con
    # `created_at < :x AND id < :y` se perderían las filas del mismo instante
    # cuyo id fuese mayor, y con `created_at <= :x` se repetirían.
    assert "(executions.created_at, executions.id) <" in sql, sql


def test_without_a_cursor_the_offset_still_works() -> None:
    """Compatibilidad: los clientes de hoy paginan con offset y no pueden romperse."""
    sql = _sql(limit=50, offset=200)
    assert "OFFSET" in sql.upper()
    assert "(executions.created_at, executions.id) <" not in sql


def test_the_order_matches_the_keyset_direction() -> None:
    """Un keyset con el ORDER BY al revés devuelve basura silenciosamente."""
    sql = _sql(limit=50, offset=0).upper()
    order = sql[sql.index("ORDER BY") :]
    assert "CREATED_AT DESC" in order
    assert "ID DESC" in order


def test_the_cursor_of_a_row_points_at_the_next_page() -> None:
    """El cursor se construye desde la ÚLTIMA fila devuelta; que el valor sea
    exactamente el de esa fila es lo que evita saltarse o repetir una."""
    from api_server.routers.tenant_stats import (
        ExecutionRunRow,
        decode_runs_cursor,
        next_runs_cursor,
    )

    moment = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
    ident = UUID("11111111-1111-1111-1111-111111111111")
    row = ExecutionRunRow(
        id=ident,
        created_at=moment,
        task_id=uuid4(),
        task_title=None,
        plan_id=None,
        plan_title=None,
        agent_id=None,
        agent_name=None,
        agent_role=None,
        model=None,
        verdict="done",
        succeeded=True,
        finish_status=None,
        retry_count=0,
        duration_ms=None,
        total_tokens=0,
        total_cost_usd=Decimal("0"),
        started_at=None,
        completed_at=None,
    )
    assert next_runs_cursor([], limit=50) is None
    assert next_runs_cursor([row], limit=50) is None, "página incompleta: no hay siguiente"
    cursor = next_runs_cursor([row], limit=1)
    assert cursor is not None
    assert decode_runs_cursor(cursor) == (moment, ident)
