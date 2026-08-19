"""Migration coverage for ``agents.agent_type`` (Plan 16 task_16_01).

The ``agent_type`` column ships with the domain-minimum migration (0002) as
``String(16) NOT NULL DEFAULT 'ai'``; migration 0066 adds the
``ck_agents_agent_type`` CHECK enforcing the :class:`AgentType` value set
(ai|human). These tests assert the end state required by the plan:

  - after ``upgrade head`` the column exists and defaults to 'ai';
  - an agent row pre-existing at revision 0065 (before the CHECK) is 'ai'
    after upgrading to head (no behaviour change for AI agents);
  - the CHECK actually rejects an out-of-set value at the DB level;
  - the migration is reversible (head -> 0065 -> head) and the constraint
    comes and goes with the migration;
  - the ORM ``AgentType`` StrEnum round-trips through the column.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _fetchval(dsn: str, sql: str, *args: object) -> object:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


async def _execute(dsn: str, sql: str, *args: object) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


def _column_default(dsn: str, table: str, column: str) -> str | None:
    val = asyncio.run(
        _fetchval(
            dsn,
            """
            SELECT column_default
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = $1
               AND column_name = $2
            """,
            table,
            column,
        )
    )
    return None if val is None else str(val)


def _agent_type_check_exists(dsn: str) -> bool:
    val = asyncio.run(
        _fetchval(
            dsn,
            """
            SELECT count(*)
              FROM pg_constraint
             WHERE conrelid = 'agents'::regclass
               AND contype = 'c'
               AND conname = 'ck_agents_agent_type'
            """,
        )
    )
    return bool(val)


def _insert_global_agent(dsn: str, agent_id: UUID, tenant_id: UUID, agent_type: str | None) -> None:
    """Insert a `global_tenant_template` agent (project_id NULL). When
    `agent_type` is None we omit the column so the server_default applies.

    El nombre se deriva del `agent_id` a propósito. Era `'seed-agent'` fijo, y
    desde la migración ``0126_perf_indexes_uniqueness`` (prod-14
    `task_prod14_10`) hay un índice único parcial ``uq_agents_tenant_name_global_live``
    sobre ``(tenant_id, name)``: dos agentes del MISMO tenant con el mismo nombre
    ya no caben. `test_orm_agent_type_round_trips` inserta dos —uno `ai` y otro
    `human`— bajo el mismo tenant, así que reventaba con
    ``UniqueViolationError`` en el segundo. El defecto era del arnés, no de la
    unicidad: el test necesita dos filas distinguibles, no dos filas iguales.

    Y se usa el hex **entero**, no un prefijo: ``uuid7`` empieza por la marca de
    tiempo, así que dos ids generados en el mismo milisegundo comparten los
    primeros dígitos. Con ``agent_id.hex[:8]`` los dos agentes volvían a llamarse
    igual y el arreglo no arreglaba nada.
    """
    name = f"seed-agent-{agent_id.hex}"
    if agent_type is None:
        sql = (
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope)"
            " VALUES ($1, $2, $3, 'backend_dev', 'You are a dev.',"
            "         'global_tenant_template')"
        )
        asyncio.run(_execute(dsn, sql, agent_id, tenant_id, name))
    else:
        sql = (
            "INSERT INTO agents"
            " (id, tenant_id, name, role, system_prompt, scope, agent_type)"
            " VALUES ($1, $2, $3, 'backend_dev', 'You are a dev.',"
            "         'global_tenant_template', $4)"
        )
        asyncio.run(_execute(dsn, sql, agent_id, tenant_id, name, agent_type))


def _agent_type(dsn: str, agent_id: UUID) -> str:
    return str(asyncio.run(_fetchval(dsn, "SELECT agent_type FROM agents WHERE id = $1", agent_id)))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_upgrade_head_column_exists_and_defaults_to_ai(
    alembic_config: object, admin_pg_dsn: str
) -> None:
    """After upgrade head the column exists with a server_default of 'ai'."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    default = _column_default(admin_pg_dsn, "agents", "agent_type")
    assert default is not None, "agents.agent_type column is missing after upgrade head"
    assert "'ai'" in default, f"agent_type default is not 'ai': {default!r}"


def test_new_agent_without_agent_type_defaults_to_ai(
    alembic_config: object, admin_pg_dsn: str, migrations_pg_dsn: str
) -> None:
    """A row inserted without agent_type takes the 'ai' server_default."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    agent_id = uuid7()
    tenant_id = uuid7()
    _insert_global_agent(migrations_pg_dsn, agent_id, tenant_id, agent_type=None)

    assert _agent_type(admin_pg_dsn, agent_id) == "ai"


def test_preexisting_agent_is_ai_after_upgrade(
    alembic_config: object, admin_pg_dsn: str, migrations_pg_dsn: str
) -> None:
    """An agent that existed at revision 0065 (before the CHECK) is 'ai'
    after upgrading to head — modelling the 'every existing agent becomes
    agent_type=ai' guarantee with no behaviour change for AI agents."""
    command.upgrade(alembic_config, "0065_organization_budget_pause")  # type: ignore[arg-type]

    agent_id = uuid7()
    tenant_id = uuid7()
    _insert_global_agent(migrations_pg_dsn, agent_id, tenant_id, agent_type=None)
    assert _agent_type(admin_pg_dsn, agent_id) == "ai"

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    # The CHECK now exists and the pre-existing row still satisfies it.
    assert _agent_type_check_exists(admin_pg_dsn)
    assert _agent_type(admin_pg_dsn, agent_id) == "ai"


def test_check_rejects_value_outside_enum(alembic_config: object, migrations_pg_dsn: str) -> None:
    """At head the CHECK rejects an agent_type outside the AgentType set."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    agent_id = uuid7()
    tenant_id = uuid7()
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        _insert_global_agent(migrations_pg_dsn, agent_id, tenant_id, agent_type="robot")


def test_orm_agent_type_round_trips(
    alembic_config: object, admin_pg_dsn: str, migrations_pg_dsn: str
) -> None:
    """Both AgentType members persist and read back through the column."""
    from api_server.db.domain import AgentType

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    ai_id = uuid7()
    human_id = uuid7()
    tenant_id = uuid7()
    _insert_global_agent(migrations_pg_dsn, ai_id, tenant_id, agent_type=AgentType.AI.value)
    _insert_global_agent(migrations_pg_dsn, human_id, tenant_id, agent_type=AgentType.HUMAN.value)

    assert _agent_type(admin_pg_dsn, ai_id) == AgentType.AI
    assert _agent_type(admin_pg_dsn, human_id) == AgentType.HUMAN


def test_migration_is_reversible(alembic_config: object, admin_pg_dsn: str) -> None:
    """head -> 0065 -> head: the CHECK is dropped on downgrade and recreated
    on the second upgrade (idempotent, fully reversible)."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _agent_type_check_exists(admin_pg_dsn)

    command.downgrade(alembic_config, "0065_organization_budget_pause")  # type: ignore[arg-type]
    assert not _agent_type_check_exists(admin_pg_dsn)
    # The column itself survives the downgrade (it predates this migration).
    assert _column_default(admin_pg_dsn, "agents", "agent_type") is not None

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _agent_type_check_exists(admin_pg_dsn)
