"""El explorador de runs deja de tocar `steps_log` (prod-13 task_prod13_18).

La tarea tenía dos mitades. La primera —no traerse el JSONB al proceso del
api-server— se entregó el 2026-08-01 seleccionando columnas escalares. Ésta es
la segunda: las columnas denormalizadas `last_model` / `tokens_in` /
`tokens_out` de la **migración 0139**, que quitan el
`jsonb_array_elements(steps_log)` de las tres consultas que quedaban.

El riesgo de un cambio así no es que falle, es que **cambie las cifras en
silencio**: el panel enseñaría números distintos de los de ayer y nadie lo
notaría hasta que alguien cuadre una factura. Por eso el test que manda aquí es
el de EQUIVALENCIA — el backfill SQL de la migración contra el cálculo en
Python que escribe los runs nuevos, sobre los mismos `steps_log`. Si divergen,
la mitad vieja del histórico y la mitad nueva contarían distinto.

Se prueba además, en este orden:

  * que la migración es **reversible de verdad** (down → up con datos dentro);
  * que las consultas ya no mencionan `jsonb_array_elements`;
  * que el CIERRE de un run estampa las columnas — un backfill sin escritor
    deja el sistema mintiendo desde el primer run nuevo.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.execution_repo import steps_rollup

pytestmark = pytest.mark.integration

PREVIOUS = "0138_revoke_backfill_grants"

#: Cuatro formas de `steps_log` cuyo roll-up NO es obvio. Un caso feliz no
#: distingue una implementación correcta de una que suma mal.
_CASES: dict[str, list[dict[str, Any]]] = {
    # Los `index` desordenados: el modelo es el del índice más alto, no el del
    # último elemento del array.
    "desordenado": [
        {
            "kind": "model_call",
            "index": 2,
            "model": "claude-opus-4",
            "tokens_in": 10,
            "tokens_out": 5,
        },
        {
            "kind": "model_call",
            "index": 0,
            "model": "claude-haiku-4",
            "tokens_in": 1,
            "tokens_out": 1,
        },
        {
            "kind": "model_call",
            "index": 1,
            "model": "claude-sonnet-4",
            "tokens_in": 3,
            "tokens_out": 2,
        },
    ],
    # Pasos que NO son model_call mezclados, con tokens propios que no cuentan.
    "mezclado": [
        {"kind": "node", "index": 0, "tokens_in": 999, "tokens_out": 999},
        {"kind": "model_call", "index": 1, "model": "gpt-4o", "tokens_in": 120, "tokens_out": 40},
        {"kind": "tool_call", "index": 2, "tokens_in": 999},
        {"kind": "model_call", "index": 3, "model": "gpt-4o-mini", "tokens_in": 7, "tokens_out": 4},
    ],
    # Un model_call SIN modelo detrás del último con modelo: no puede borrarlo.
    "sin_modelo_al_final": [
        {"kind": "model_call", "index": 0, "model": "llama3.1", "tokens_in": 8, "tokens_out": 2},
        {"kind": "model_call", "index": 1, "tokens_in": 0, "tokens_out": 0},
    ],
    # Un run que nunca llamó a un modelo: NULL y ceros, no 0 y "".
    "sin_modelo": [{"kind": "node", "index": 0, "summary": "abortado antes del primer turno"}],
}


def _backfill_sql() -> str:
    """El SQL de la migración, leído de la migración.

    Cargado del fichero y no copiado aquí: una copia se desincroniza en
    silencio y este test dejaría de probar la migración que se ejecuta.
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "api-server"
        / "migrations"
        / "versions"
        / "20260812_0139_executions_steps_rollup.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0139", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module._BACKFILL)


async def _seed_tree(conn: asyncpg.Connection, slug: str) -> tuple[UUID, UUID]:
    """`(tenant_id, task_id)` — el árbol mínimo para colgar una execution."""
    tenant = uuid4()
    project = uuid4()
    task = uuid4()
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)", tenant, slug, slug
    )
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
        project,
        tenant,
        f"proj-{slug}",
    )
    await conn.execute(
        "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
        " VALUES ($1, $2, $3, $4, 'backlog', 'medium')",
        task,
        tenant,
        project,
        f"task-{slug}",
    )
    return tenant, task


async def _column_names(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'executions'"
    )
    return {r["column_name"] for r in rows}


# ---------------------------------------------------------------------------
# 1. La migración: reversible y equivalente
# ---------------------------------------------------------------------------
def test_the_backfill_matches_the_python_rollup_for_every_shape(
    alembic_config, migrations_pg_dsn: str
) -> None:
    # Función SÍNCRONA a propósito: `command.upgrade` monta su propio bucle con
    # `asyncio.run`, que revienta dentro de un test async.
    command.upgrade(alembic_config, "head")
    # Atrás: las tres columnas desaparecen (reversibilidad, con datos dentro).
    command.downgrade(alembic_config, PREVIOUS)

    async def _sow() -> dict[str, UUID]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("TRUNCATE organizations RESTART IDENTITY CASCADE")
            columns = await _column_names(conn)
            assert {"last_model", "tokens_in", "tokens_out"} & columns == set()

            tenant, task = await _seed_tree(conn, f"rollup-{uuid4().hex[:8]}")
            ids: dict[str, UUID] = {}
            for name, steps in _CASES.items():
                execution = uuid4()
                ids[name] = execution
                await conn.execute(
                    "INSERT INTO executions (id, tenant_id, task_id, status, steps_log)"
                    " VALUES ($1, $2, $3, 'done', $4::jsonb)",
                    execution,
                    tenant,
                    task,
                    json.dumps(steps),
                )
            return ids
        finally:
            await conn.close()

    ids = asyncio.run(_sow())

    # Adelante otra vez: crea las columnas y rellena el histórico.
    command.upgrade(alembic_config, "head")

    async def _check() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            assert {"last_model", "tokens_in", "tokens_out"} <= await _column_names(conn)
            for name, steps in _CASES.items():
                row = await conn.fetchrow(
                    "SELECT last_model, tokens_in, tokens_out FROM executions WHERE id = $1",
                    ids[name],
                )
                assert row is not None, name
                expected = steps_rollup(steps)
                assert row["last_model"] == expected.last_model, name
                assert row["tokens_in"] == expected.tokens_in, name
                assert row["tokens_out"] == expected.tokens_out, name
        finally:
            await conn.close()

    asyncio.run(_check())


def test_the_backfill_is_idempotent(alembic_config, migrations_pg_dsn: str) -> None:
    """Re-ejecutarlo no puede duplicar ni desplazar nada: es un `UPDATE … SET`
    a valores absolutos, no un incremento. Lo comprueba corriéndolo dos veces."""
    command.upgrade(alembic_config, "head")
    sql = _backfill_sql()

    async def _body() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("TRUNCATE organizations RESTART IDENTITY CASCADE")
            tenant, task = await _seed_tree(conn, f"idem-{uuid4().hex[:8]}")
            execution = uuid4()
            await conn.execute(
                "INSERT INTO executions (id, tenant_id, task_id, status, steps_log)"
                " VALUES ($1, $2, $3, 'done', $4::jsonb)",
                execution,
                tenant,
                task,
                json.dumps(_CASES["mezclado"]),
            )
            await conn.execute(sql)
            first = await conn.fetchrow(
                "SELECT last_model, tokens_in, tokens_out FROM executions WHERE id = $1", execution
            )
            await conn.execute(sql)
            second = await conn.fetchrow(
                "SELECT last_model, tokens_in, tokens_out FROM executions WHERE id = $1", execution
            )
            assert first is not None and dict(first) == dict(second or {})
            assert first["tokens_in"] == 127
        finally:
            await conn.close()

    asyncio.run(_body())


# ---------------------------------------------------------------------------
# 2. Las consultas ya no expanden el JSONB
# ---------------------------------------------------------------------------
def test_the_runs_query_no_longer_unrolls_steps_log() -> None:
    """La guarda es sobre el SQL COMPILADO, no sobre el texto del fichero: lo
    que importa es lo que se le manda a PostgreSQL."""
    from api_server.db.domain import Execution
    from api_server.routers.tenant_stats import runs_select

    compiled = str(runs_select([Execution.tenant_id == uuid4()], limit=50).compile())
    assert "jsonb_array_elements" not in compiled
    assert "steps_log" not in compiled
    assert "last_model" in compiled


def test_filtering_by_model_is_a_column_predicate() -> None:
    """El caso que más dolía: `?model=` metía la subconsulta correlacionada en
    el WHERE, donde el planificador no tenía nada que indexar."""
    from api_server.routers.tenant_stats import _exec_filters

    filters = _exec_filters(tenant_id=uuid4(), model="claude-opus-4")
    compiled = " ".join(str(f.compile()) for f in filters)
    assert "jsonb_array_elements" not in compiled
    assert "executions.last_model" in compiled


# ---------------------------------------------------------------------------
# 3. El escritor: cerrar un run estampa las columnas
# ---------------------------------------------------------------------------
@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    """Fixture SÍNCRONA: `command.upgrade` monta su propio bucle de eventos."""
    command.upgrade(alembic_config, "head")


@pytest.mark.asyncio
async def test_recording_a_run_stamps_the_denormalised_columns(
    _migrated: None, admin_database_url: str
) -> None:
    """Sin esto el backfill sería una foto: correcta para el histórico y
    mentirosa a partir del primer run nuevo."""
    from agent_runtime.graph import ExecutionResult
    from api_server.db.execution_repo import get_execution, record_execution
    from api_server.db.models import Organization
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await s.execute(text("TRUNCATE organizations RESTART IDENTITY CASCADE"))

        tenant, project, task = uuid4(), uuid4(), uuid4()
        async with sm() as s, s.begin():
            s.add(Organization(id=tenant, name="Rollup", slug=f"rollup-{tenant.hex[:8]}"))
            await s.flush()
            await s.execute(
                text(
                    "INSERT INTO projects (id, tenant_id, name) VALUES (:p, :t, 'Rollup project')"
                ),
                {"p": project, "t": tenant},
            )
            await s.execute(
                text(
                    "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
                    " VALUES (:k, :t, :p, 'Rollup task', 'backlog', 'medium')"
                ),
                {"k": task, "t": tenant, "p": project},
            )

        steps = _CASES["mezclado"]
        result = ExecutionResult(
            status="done",
            abort_code=None,
            output="ok",
            iterations=2,
            steps=steps,
            usage={"total_tokens": 171, "cost_usd": 0.01, "tool_calls": 1, "model_calls": 2},
        )
        async with sm() as s, s.begin():
            execution = await record_execution(s, tenant_id=tenant, task_id=task, result=result)
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        expected = steps_rollup(steps)
        assert loaded.last_model == expected.last_model == "gpt-4o-mini"
        assert loaded.tokens_in == expected.tokens_in == 127
        assert loaded.tokens_out == expected.tokens_out == 44
    finally:
        await engine.dispose()
