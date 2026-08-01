"""prod-13 · task_prod13_11 — la ventana de gasto como RANGO, no como `date()`.

La migración 0126 creó `ix_executions_tenant_created_at (tenant_id, created_at)`.
El índice por sí solo no sirve de nada: mientras el predicado fuese
`date(executions.created_at) >= :start`, PostgreSQL no puede empujar el rango al
índice (no hay índice sobre esa EXPRESIÓN), así que lo resuelve como *Filter*
sobre todas las filas del tenant. El índice existía y no lo usaba nadie — el
patrón «mecanismo entregado, cero llamantes» del apartado 5 de
`verificar-antes-de-implementar.md`, aquí en su versión de esquema.

Lo que se fija aquí es de dos naturalezas distintas y las dos importan:

1. **Rendimiento**: el `EXPLAIN` de la consulta real pone el rango de
   `created_at` en el `Index Cond`, no en el `Filter`. El test lleva su propio
   CONTROL: explica también la forma antigua con `date()` y comprueba que
   aquélla NO llega al `Index Cond`. Sin ese control, el día que el planificador
   cambiara de idea el test seguiría pasando sin medir nada.

2. **Corrección**: `date(timestamptz)` se evalúa en la ZONA HORARIA DE LA SESIÓN.
   En un despliegue cuyo PostgreSQL no esté en UTC, el corte del período de
   presupuesto se movía varias horas — un gasto de las 02:00 UTC del día 1 caía
   en el mes ANTERIOR y el presupuesto del mes en curso no lo contaba. El rango
   explícito fija el corte en UTC, que es la zona en la que ya están definidos
   `started_at`/`completed_at` y todo lo demás de la plataforma.

Pre-condición: postgres (15432) de docker-compose sano; los fixtures crean una
BD desechable.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

# Importar el ORM entero para que SQLAlchemy resuelva las FK que el JOIN de la
# consulta de gasto necesita (Execution.task_id -> tasks.id).
import api_server.db.domain  # noqa: F401
import asyncpg
import pytest
from alembic import command
from api_server.budgets.consumption import _spend_usd_in_window
from api_server.budgets.period import BudgetPeriodWindow
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# Mayo de 2026, semiabierto: [2026-05-01, 2026-06-01).
_WINDOW = BudgetPeriodWindow(start=date(2026, 5, 1), end=date(2026, 6, 1))

# Una zona horaria NEGATIVA respecto de UTC. Con ella, `date(timestamptz)` de un
# instante de la madrugada UTC devuelve el día ANTERIOR: es justo el caso que
# desplazaba el corte del período.
_WESTERN_TZ = "America/New_York"


@pytest.fixture()
def schema_at_head(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


async def _seed(dsn: str) -> tuple[UUID, UUID]:
    """Un tenant con un proyecto y una tarea; devuelve (tenant_id, task_id)."""
    tenant_id, project_id, task_id = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, plans, projects, organizations" " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            tenant_id,
            f"t-{tenant_id.hex[:10]}",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, 'P', 'active')",
            project_id,
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, retry_count)"
            " VALUES ($1, $2, $3, NULL, 'T', 'done', 0)",
            task_id,
            tenant_id,
            project_id,
        )
    finally:
        await conn.close()
    return tenant_id, task_id


async def _seed_execution(
    dsn: str, *, tenant_id: UUID, task_id: UUID, at: datetime, cost: str
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, agent_id, status, steps_log,"
            " total_tokens, total_cost_usd, started_at, completed_at, created_at)"
            " VALUES ($1, $2, $3, NULL, 'done', $4::jsonb, 0, $5, $6, $6, $6)",
            uuid4(),
            tenant_id,
            task_id,
            json.dumps([]),
            Decimal(cost),
            at,
        )
    finally:
        await conn.close()


async def _spend(app_database_url: str, tenant_id: UUID, *, timezone: str | None) -> Decimal:
    engine = create_async_engine(app_database_url)
    try:
        session = async_sessionmaker(engine, expire_on_commit=False)()
        try:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(tenant_id)},
            )
            if timezone is not None:
                await session.execute(sa_text(f"SET TIME ZONE '{timezone}'"))
            return await _spend_usd_in_window(
                session, tenant_id=tenant_id, window=_WINDOW, project_id=None
            )
        finally:
            await session.close()
    finally:
        await engine.dispose()


# ===========================================================================
# 2. Corrección: el corte es UTC, no la zona de la sesión
# ===========================================================================
@pytest.mark.asyncio
async def test_the_period_cut_is_utc_not_the_session_timezone(
    schema_at_head: None, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    tenant_id, task_id = await _seed(migrations_pg_dsn)

    # 02:00 UTC del 1 de mayo. En America/New_York son las 22:00 del 30 de ABRIL,
    # así que `date(created_at)` lo dejaba fuera del período de mayo.
    await _seed_execution(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        task_id=task_id,
        at=datetime(2026, 5, 1, 2, 0, tzinfo=UTC),
        cost="7.00",
    )

    spend = await _spend(app_database_url, tenant_id, timezone=_WESTERN_TZ)

    assert spend == Decimal("7.00"), (
        "el gasto de las 02:00 UTC del primer día del período se perdió: el corte"
        f" sigue dependiendo de la zona horaria de la sesión ({_WESTERN_TZ}), no de"
        f" UTC. Sumó {spend}"
    )


@pytest.mark.asyncio
async def test_the_window_is_half_open_on_both_ends(
    schema_at_head: None, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    tenant_id, task_id = await _seed(migrations_pg_dsn)

    # Justo antes del inicio: fuera.
    await _seed_execution(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        task_id=task_id,
        at=datetime(2026, 4, 30, 23, 59, 59, 999999, tzinfo=UTC),
        cost="100.00",
    )
    # El instante exacto del inicio: dentro.
    await _seed_execution(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        task_id=task_id,
        at=datetime(2026, 5, 1, 0, 0, tzinfo=UTC),
        cost="1.00",
    )
    # El último instante del período: dentro.
    await _seed_execution(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        task_id=task_id,
        at=datetime(2026, 5, 31, 23, 59, 59, 999999, tzinfo=UTC),
        cost="2.00",
    )
    # El instante exacto del fin: FUERA (`end` es exclusivo).
    await _seed_execution(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        task_id=task_id,
        at=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        cost="200.00",
    )

    spend = await _spend(app_database_url, tenant_id, timezone=None)

    assert spend == Decimal("3.00"), (
        "la ventana [start, end) dejó de ser semiabierta en UTC: se esperaba 3.00"
        f" (1.00 + 2.00) y salió {spend}"
    )


# ===========================================================================
# 1. Rendimiento: el rango llega al Index Cond (con su control)
# ===========================================================================
def _explain_sql(stmt: object) -> str:
    from sqlalchemy.dialects import postgresql

    compiled = stmt.compile(  # type: ignore[attr-defined]
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    return f"EXPLAIN (FORMAT JSON) {compiled}"


def _index_conds(plan: object) -> list[str]:
    """Todos los `Index Cond` del árbol de plan, recursivamente."""
    found: list[str] = []
    if isinstance(plan, dict):
        cond = plan.get("Index Cond")
        if isinstance(cond, str):
            found.append(cond)
        for value in plan.values():
            found.extend(_index_conds(value))
    elif isinstance(plan, list):
        for item in plan:
            found.extend(_index_conds(item))
    return found


@pytest.mark.asyncio
async def test_the_created_at_range_is_pushed_into_the_index(
    schema_at_head: None, migrations_pg_dsn: str
) -> None:
    """El `EXPLAIN` de la consulta real, con su control de la forma antigua."""
    from api_server.budgets.consumption import spend_in_window_stmt
    from api_server.db.domain import Execution
    from sqlalchemy import func, select

    tenant_id, task_id = await _seed(migrations_pg_dsn)
    # Suficientes filas para que el índice sea plausible; `enable_seqscan = off`
    # hace el resto determinista.
    for day in range(1, 20):
        await _seed_execution(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            task_id=task_id,
            at=datetime(2026, 5, day, 12, 0, tzinfo=UTC),
            cost="0.10",
        )

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("ANALYZE executions")
        await conn.execute("SET enable_seqscan = off")
        await conn.execute("SET enable_bitmapscan = off")

        real = spend_in_window_stmt(tenant_id=tenant_id, window=_WINDOW, project_id=None)
        plan = json.loads(await conn.fetchval(_explain_sql(real)))
        conds = " | ".join(_index_conds(plan))

        assert "created_at" in conds, (
            "el rango de `created_at` no llegó al Index Cond, así que"
            f" ix_executions_tenant_created_at no acota nada. Index Conds: {conds!r}"
        )

        # CONTROL: la forma antigua, con `date()`. Si ESTA también empujara el
        # rango al índice, la aserción de arriba no estaría midiendo nada.
        exec_date = func.date(Execution.created_at)
        old = (
            select(func.coalesce(func.sum(Execution.total_cost_usd), 0))
            .select_from(Execution)
            .where(
                Execution.tenant_id == tenant_id,
                exec_date >= _WINDOW.start,
                exec_date < _WINDOW.end,
            )
        )
        old_plan = json.loads(await conn.fetchval(_explain_sql(old)))
        old_conds = " | ".join(_index_conds(old_plan))

        assert "created_at" not in old_conds, (
            "el control dejó de discriminar: PostgreSQL ahora SÍ empuja"
            f" `date(created_at)` al índice ({old_conds!r}), así que este test ya no"
            " demuestra que la reescritura sargable sirviera de algo"
        )
    finally:
        await conn.close()
