"""RLS efectiva en las 4 tablas de unión (plan prod-14, tenancy-1).

Hasta la migración ``0124_junction_tenant_rls`` estas cuatro tablas
—``agent_skills``, ``agent_tools``, ``team_members``, ``task_dependencies``—
no tenían ``tenant_id`` y por tanto **ninguna política RLS las protegía**: a
nivel de base de datos cualquier sesión podía leer las asignaciones de otro
tenant (incluido ``agent_tools.config_override``, que transporta configuración
por agente) e insertar filas apuntando a un padre ajeno. Que no hubiera fuga
explotable dependía por completo de la disciplina de cada router.

Este fichero es la justificación de esa migración. Comprueba, hablando SQL
directo como ``app_user`` (NOBYPASSRLS, el rol de la api-server), que:

* SELECT solo devuelve las filas del tenant de la sesión;
* sin ``app.tenant_id`` el resultado es 0 filas (fail-closed);
* INSERT apuntando a un padre de otro tenant se rechaza;
* UPDATE/DELETE sobre filas ajenas afectan 0 filas;
* el ``tenant_id`` se DERIVA del padre en el trigger, así que un servicio
  BYPASSRLS que pase un ``tenant_id`` contradictorio también es rechazado
  (el agujero que la policy sola no tapa);
* y la contra-prueba de no-regresión: las filas de unión de los built-in de
  plataforma SIGUEN siendo legibles por cualquier tenant, porque de eso vive
  el fork de agentes (``_clone_agent_capabilities``) y la adopción de equipos
  (``_fork_team_deep``). Sin esa policy de lectura, adoptar un equipo built-in
  produciría un equipo vacío.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

JUNCTIONS: tuple[str, ...] = ("agent_skills", "agent_tools", "team_members", "task_dependencies")


# ---------------------------------------------------------------------------
# Semilla: dos tenants completos + un built-in de plataforma (agente
# global_builtin con tool/skill asignadas y equipo is_builtin con miembro).
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids: dict[str, UUID] = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "project_a": uuid4(),
        "project_b": uuid4(),
        "agent_a": uuid4(),
        "agent_b": uuid4(),
        "agent_builtin": uuid4(),
        "skill_a": uuid4(),
        "skill_b": uuid4(),
        "skill_builtin": uuid4(),
        "tool_a": uuid4(),
        "tool_b": uuid4(),
        "tool_builtin": uuid4(),
        "team_a": uuid4(),
        "team_b": uuid4(),
        "team_builtin": uuid4(),
        "task_a1": uuid4(),
        "task_a2": uuid4(),
        "task_b1": uuid4(),
        "task_b2": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_skills, agent_tools, team_members, task_dependencies,"
            " skills, tools, teams, tasks, agents, projects, organizations"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)"
            " VALUES ($1, 'Acme', 'acme-jrls'), ($2, 'Globex', 'globex-jrls'),"
            "        ($3, 'Platform', 'platform')",
            ids["tenant_a"],
            ids["tenant_b"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name)"
            " VALUES ($1, $2, 'A-app'), ($3, $4, 'B-app')",
            ids["project_a"],
            ids["tenant_a"],
            ids["project_b"],
            ids["tenant_b"],
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt, project_id)"
            " VALUES"
            " ($1, $2, 'a-dev', 'backend_dev', 'project_local', 'ai', 'p', $3),"
            " ($4, $5, 'b-dev', 'backend_dev', 'project_local', 'ai', 'p', $6),"
            " ($7, $8, 'builtin-dev', 'backend_dev', 'global_builtin', 'ai', 'p', NULL)",
            ids["agent_a"],
            ids["tenant_a"],
            ids["project_a"],
            ids["agent_b"],
            ids["tenant_b"],
            ids["project_b"],
            ids["agent_builtin"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO skills (id, tenant_id, name, category, prompt_fragment, is_builtin)"
            " VALUES ($1, $2, 'a-skill', 'backend', 'f', false),"
            "        ($3, $4, 'b-skill', 'backend', 'f', false),"
            "        ($5, $6, 'plat-skill', 'backend', 'f', true)",
            ids["skill_a"],
            ids["tenant_a"],
            ids["skill_b"],
            ids["tenant_b"],
            ids["skill_builtin"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO tools (id, tenant_id, name, category, implementation_type, is_builtin)"
            " VALUES ($1, $2, 'a-tool', 'custom', 'builtin', false),"
            "        ($3, $4, 'b-tool', 'custom', 'builtin', false),"
            "        ($5, $6, 'plat-tool', 'custom', 'builtin', true)",
            ids["tool_a"],
            ids["tenant_a"],
            ids["tool_b"],
            ids["tenant_b"],
            ids["tool_builtin"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name, is_builtin)"
            " VALUES ($1, $2, 'A-team', false), ($3, $4, 'B-team', false),"
            "        ($5, $6, 'Plat-team', true)",
            ids["team_a"],
            ids["tenant_a"],
            ids["team_b"],
            ids["tenant_b"],
            ids["team_builtin"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, description, status, priority)"
            " VALUES ($1, $2, $3, 'A1', 'd', 'backlog', 'medium'),"
            "        ($4, $2, $3, 'A2', 'd', 'backlog', 'medium'),"
            "        ($5, $6, $7, 'B1', 'd', 'backlog', 'medium'),"
            "        ($8, $6, $7, 'B2', 'd', 'backlog', 'medium')",
            ids["task_a1"],
            ids["tenant_a"],
            ids["project_a"],
            ids["task_a2"],
            ids["task_b1"],
            ids["tenant_b"],
            ids["project_b"],
            ids["task_b2"],
        )

        # --- filas de unión: una por tenant + las del built-in de plataforma ---
        await conn.execute(
            "INSERT INTO agent_skills (agent_id, skill_id) VALUES ($1, $2), ($3, $4), ($5, $6)",
            ids["agent_a"],
            ids["skill_a"],
            ids["agent_b"],
            ids["skill_b"],
            ids["agent_builtin"],
            ids["skill_builtin"],
        )
        await conn.execute(
            "INSERT INTO agent_tools (agent_id, tool_id, config_override)"
            ' VALUES ($1, $2, \'{"secret": "A-ONLY"}\'::jsonb),'
            '        ($3, $4, \'{"secret": "B-ONLY"}\'::jsonb),'
            "        ($5, $6, NULL)",
            ids["agent_a"],
            ids["tool_a"],
            ids["agent_b"],
            ids["tool_b"],
            ids["agent_builtin"],
            ids["tool_builtin"],
        )
        await conn.execute(
            "INSERT INTO team_members (team_id, agent_id) VALUES ($1, $2), ($3, $4), ($5, $6)",
            ids["team_a"],
            ids["agent_a"],
            ids["team_b"],
            ids["agent_b"],
            ids["team_builtin"],
            ids["agent_builtin"],
        )
        await conn.execute(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES ($1, $2), ($3, $4)",
            ids["task_a2"],
            ids["task_a1"],
            ids["task_b2"],
            ids["task_b1"],
        )
    finally:
        await conn.close()
    return ids


@pytest.fixture()
def seeded(alembic_config: object, migrations_pg_dsn: str) -> dict[str, UUID]:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    asyncio.run(_grant_app_user())
    return asyncio.run(_seed(migrations_pg_dsn))


async def _grant_app_user() -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()


# ---------------------------------------------------------------------------
# Helpers de sesión: app_user (NOBYPASSRLS) con/sin app.tenant_id.
# ---------------------------------------------------------------------------
async def _as_tenant(app_dsn: str, tenant_id: UUID | None, sql: str, *args: Any) -> list[Any]:
    conn = await asyncpg.connect(app_dsn)
    try:
        if tenant_id is not None:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))
        return list(await conn.fetch(sql, *args))
    finally:
        await conn.close()


async def _exec_as_tenant(app_dsn: str, tenant_id: UUID | None, sql: str, *args: Any) -> str:
    conn = await asyncpg.connect(app_dsn)
    try:
        if tenant_id is not None:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))
        return await conn.execute(sql, *args)
    finally:
        await conn.close()


def _app_dsn(app_database_url: str) -> str:
    # app_database_url viene en dialecto SQLAlchemy; asyncpg quiere DSN puro.
    return app_database_url.replace("postgresql+asyncpg://", "postgresql://")


# ===========================================================================
# 1. La columna existe, es NOT NULL y quedó poblada por el backfill.
# ===========================================================================
def test_backfill_populated_tenant_id_from_parents(seeded, migrations_pg_dsn: str) -> None:
    ids = seeded

    async def _check() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            for table in JUNCTIONS:
                nulls = await conn.fetchval(f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL")
                assert nulls == 0, f"{table} tiene filas sin tenant_id tras el backfill"
                notnull = await conn.fetchval(
                    "SELECT attnotnull FROM pg_attribute"
                    " WHERE attrelid = $1::regclass AND attname = 'tenant_id'",
                    table,
                )
                assert notnull is True, f"{table}.tenant_id debería ser NOT NULL"

            # El tenant_id se deriva del padre PROPIETARIO, no del hijo: la fila
            # (agente de A, skill built-in de plataforma) es del tenant A.
            owner = await conn.fetchval(
                "SELECT tenant_id FROM agent_skills WHERE agent_id = $1", ids["agent_a"]
            )
            assert owner == ids["tenant_a"]
            builtin = await conn.fetchval(
                "SELECT tenant_id FROM agent_skills WHERE agent_id = $1", ids["agent_builtin"]
            )
            assert builtin == _PLATFORM_TENANT_ID
        finally:
            await conn.close()

    asyncio.run(_check())


# ===========================================================================
# 2. RLS activada + FORCE + policy en las cuatro tablas.
# ===========================================================================
def test_rls_enabled_forced_and_policied(seeded, migrations_pg_dsn: str) -> None:
    async def _check() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            for table in JUNCTIONS:
                row = await conn.fetchrow(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
                    " WHERE oid = $1::regclass",
                    table,
                )
                assert row is not None
                assert row["relrowsecurity"], f"{table}: RLS sin ENABLE"
                assert row["relforcerowsecurity"], f"{table}: RLS sin FORCE"
                names = {
                    r["policyname"]
                    for r in await conn.fetch(
                        "SELECT policyname FROM pg_policies"
                        " WHERE schemaname = 'public' AND tablename = $1",
                        table,
                    )
                }
                assert f"{table}_tenant_isolation" in names, f"{table}: falta policy de aislamiento"
        finally:
            await conn.close()

    asyncio.run(_check())


# ===========================================================================
# 3. SELECT: cada tenant ve SOLO lo suyo (+ los built-in de plataforma).
# ===========================================================================
def test_select_is_isolated_per_tenant(seeded, app_database_url: str) -> None:
    ids = seeded
    dsn = _app_dsn(app_database_url)

    rows = asyncio.run(
        _as_tenant(dsn, ids["tenant_b"], "SELECT agent_id, tenant_id FROM agent_skills")
    )
    seen = {r["tenant_id"] for r in rows}
    assert ids["tenant_a"] not in seen, "tenant B ve filas agent_skills del tenant A"
    assert ids["tenant_b"] in seen, "tenant B no ve sus propias filas"

    rows = asyncio.run(
        _as_tenant(dsn, ids["tenant_b"], "SELECT team_id, tenant_id FROM team_members")
    )
    assert ids["tenant_a"] not in {r["tenant_id"] for r in rows}

    rows = asyncio.run(
        _as_tenant(dsn, ids["tenant_b"], "SELECT task_id, tenant_id FROM task_dependencies")
    )
    assert [r["tenant_id"] for r in rows] == [ids["tenant_b"]]


def test_config_override_of_other_tenant_is_not_readable(seeded, app_database_url: str) -> None:
    """`agent_tools.config_override` transporta config por agente: es el dato
    con valor real de estas junctions, y el que un tenant no debe poder leer."""
    ids = seeded
    dsn = _app_dsn(app_database_url)

    rows = asyncio.run(
        _as_tenant(
            dsn,
            ids["tenant_b"],
            "SELECT config_override::text AS c FROM agent_tools WHERE agent_id = $1",
            ids["agent_a"],
        )
    )
    assert rows == [], "tenant B leyó el config_override de un agente del tenant A"

    own = asyncio.run(
        _as_tenant(
            dsn,
            ids["tenant_b"],
            "SELECT config_override::text AS c FROM agent_tools WHERE agent_id = $1",
            ids["agent_b"],
        )
    )
    assert len(own) == 1 and "B-ONLY" in own[0]["c"]


# ===========================================================================
# 4. Fail-closed: sin app.tenant_id, 0 filas en las cuatro tablas.
# ===========================================================================
def test_without_tenant_guc_returns_zero_rows(seeded, app_database_url: str) -> None:
    dsn = _app_dsn(app_database_url)
    for table in JUNCTIONS:
        rows = asyncio.run(_as_tenant(dsn, None, f"SELECT * FROM {table}"))
        # `team_members`/`agent_*` de plataforma siguen visibles por la policy de
        # built-in (fork/adopt lo necesitan); lo que NO puede aparecer es una
        # fila de tenant real sin GUC.
        assert all(r["tenant_id"] == _PLATFORM_TENANT_ID for r in rows), (
            f"{table}: sin app.tenant_id se filtraron filas de tenants reales"
        )


# ===========================================================================
# 5. INSERT apuntando a un padre de OTRO tenant: rechazado.
# ===========================================================================
def test_insert_pointing_at_foreign_parent_is_rejected(seeded, app_database_url: str) -> None:
    ids = seeded
    dsn = _app_dsn(app_database_url)

    cases: list[tuple[str, str, tuple[Any, ...]]] = [
        (
            "agent_skills",
            "INSERT INTO agent_skills (agent_id, skill_id) VALUES ($1, $2)",
            (ids["agent_a"], ids["skill_b"]),
        ),
        (
            "agent_tools",
            "INSERT INTO agent_tools (agent_id, tool_id) VALUES ($1, $2)",
            (ids["agent_a"], ids["tool_b"]),
        ),
        (
            "team_members",
            "INSERT INTO team_members (team_id, agent_id) VALUES ($1, $2)",
            (ids["team_a"], ids["agent_b"]),
        ),
        (
            "task_dependencies",
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES ($1, $2)",
            (ids["task_a1"], ids["task_b1"]),
        ),
    ]
    for table, sql, args in cases:
        with pytest.raises(asyncpg.PostgresError) as exc:
            asyncio.run(_exec_as_tenant(dsn, ids["tenant_b"], sql, *args))
        assert exc.value.sqlstate in {"42501", "23514"}, (
            f"{table}: el INSERT cross-tenant falló por la razón equivocada"
            f" ({exc.value.sqlstate}: {exc.value})"
        )


def test_insert_of_own_parent_succeeds_and_stamps_tenant(seeded, app_database_url: str) -> None:
    """Contra-prueba: la guarda no está simplemente cerrada a todo. Un INSERT
    legítimo (sin pasar tenant_id) funciona y queda estampado con el tenant."""
    ids = seeded
    dsn = _app_dsn(app_database_url)

    asyncio.run(
        _exec_as_tenant(
            dsn,
            ids["tenant_b"],
            "INSERT INTO agent_tools (agent_id, tool_id) VALUES ($1, $2)",
            ids["agent_b"],
            ids["tool_builtin"],
        )
    )
    rows = asyncio.run(
        _as_tenant(
            dsn,
            ids["tenant_b"],
            "SELECT tenant_id FROM agent_tools WHERE agent_id = $1 AND tool_id = $2",
            ids["agent_b"],
            ids["tool_builtin"],
        )
    )
    assert [r["tenant_id"] for r in rows] == [ids["tenant_b"]]


# ===========================================================================
# 6. UPDATE / DELETE sobre filas ajenas: 0 filas afectadas.
# ===========================================================================
def test_update_and_delete_of_foreign_rows_affect_nothing(seeded, app_database_url: str) -> None:
    ids = seeded
    dsn = _app_dsn(app_database_url)

    status = asyncio.run(
        _exec_as_tenant(
            dsn,
            ids["tenant_b"],
            "UPDATE agent_tools SET config_override = '{\"pwned\": true}'::jsonb"
            " WHERE agent_id = $1",
            ids["agent_a"],
        )
    )
    assert status == "UPDATE 0", f"tenant B pudo actualizar filas del tenant A: {status}"

    for table, where in (
        ("agent_skills", "agent_id"),
        ("agent_tools", "agent_id"),
        ("team_members", "team_id"),
    ):
        key = ids["agent_a"] if where == "agent_id" else ids["team_a"]
        status = asyncio.run(
            _exec_as_tenant(dsn, ids["tenant_b"], f"DELETE FROM {table} WHERE {where} = $1", key)
        )
        assert status == "DELETE 0", f"tenant B pudo borrar filas de {table} del tenant A"

    status = asyncio.run(
        _exec_as_tenant(
            dsn,
            ids["tenant_b"],
            "DELETE FROM task_dependencies WHERE task_id = $1",
            ids["task_a2"],
        )
    )
    assert status == "DELETE 0"

    # Y las filas de A siguen intactas (leído con BYPASSRLS más abajo por el
    # test de backfill; aquí basta con que A las siga viendo con su propio GUC).
    rows = asyncio.run(
        _as_tenant(
            dsn,
            ids["tenant_a"],
            "SELECT config_override::text AS c FROM agent_tools WHERE agent_id = $1",
            ids["agent_a"],
        )
    )
    assert len(rows) == 1 and "A-ONLY" in rows[0]["c"]


# ===========================================================================
# 7. El agujero que la policy NO tapa: un servicio BYPASSRLS con tenant_id
#    contradictorio. El trigger lo rechaza igual.
# ===========================================================================
def test_bypassrls_insert_with_contradictory_tenant_id_is_rejected(
    seeded, migrations_pg_dsn: str
) -> None:
    ids = seeded

    async def _attempt() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "INSERT INTO agent_tools (agent_id, tool_id, tenant_id) VALUES ($1, $2, $3)",
                ids["agent_a"],
                ids["tool_builtin"],
                ids["tenant_b"],  # miente: el agente es del tenant A
            )
        finally:
            await conn.close()

    with pytest.raises(asyncpg.PostgresError) as exc:
        asyncio.run(_attempt())
    assert exc.value.sqlstate == "23514", f"esperaba check_violation, vino {exc.value.sqlstate}"


def test_bypassrls_cross_tenant_task_dependency_is_rejected(seeded, migrations_pg_dsn: str) -> None:
    """Una dependencia entre tareas de tenants distintos es un DAG imposible:
    ni siquiera un servicio BYPASSRLS debe poder crearla."""
    ids = seeded

    async def _attempt() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES ($1, $2)",
                ids["task_a1"],
                ids["task_b1"],
            )
        finally:
            await conn.close()

    with pytest.raises(asyncpg.PostgresError) as exc:
        asyncio.run(_attempt())
    assert exc.value.sqlstate == "23514"


# ===========================================================================
# 8. No-regresión: las junctions de los built-in de plataforma siguen legibles
#    por cualquier tenant. De esto viven el fork de agentes y la adopción de
#    equipos; una policy estricta las habría dejado vacías en silencio.
# ===========================================================================
def test_builtin_junction_rows_stay_readable_cross_tenant(seeded, app_database_url: str) -> None:
    ids = seeded
    dsn = _app_dsn(app_database_url)

    tools = asyncio.run(
        _as_tenant(
            dsn,
            ids["tenant_a"],
            "SELECT tool_id FROM agent_tools WHERE agent_id = $1",
            ids["agent_builtin"],
        )
    )
    assert [r["tool_id"] for r in tools] == [ids["tool_builtin"]], (
        "un tenant dejó de ver las tools del agente built-in: el fork de agentes"
        " (_clone_agent_capabilities) heredaría CERO tools"
    )

    skills = asyncio.run(
        _as_tenant(
            dsn,
            ids["tenant_a"],
            "SELECT skill_id FROM agent_skills WHERE agent_id = $1",
            ids["agent_builtin"],
        )
    )
    assert [r["skill_id"] for r in skills] == [ids["skill_builtin"]]

    members = asyncio.run(
        _as_tenant(
            dsn,
            ids["tenant_a"],
            "SELECT agent_id FROM team_members WHERE team_id = $1",
            ids["team_builtin"],
        )
    )
    assert [r["agent_id"] for r in members] == [ids["agent_builtin"]], (
        "un tenant dejó de ver los miembros del equipo built-in: adoptar un"
        " equipo (_fork_team_deep) produciría un equipo VACÍO"
    )

    # …y esa lectura es SOLO lectura: no puede escribir sobre el built-in.
    with pytest.raises(asyncpg.PostgresError):
        asyncio.run(
            _exec_as_tenant(
                dsn,
                ids["tenant_a"],
                "INSERT INTO team_members (team_id, agent_id) VALUES ($1, $2)",
                ids["team_builtin"],
                ids["agent_a"],
            )
        )


# ===========================================================================
# 9. El BACKFILL de la migración, con datos de verdad.
#
#    Los tests de arriba siembran DESPUÉS de `upgrade head`, así que las filas
#    nacen ya estampadas por el trigger y el `UPDATE ... FROM padre` del paso 2
#    de la migración no se ejecuta nunca sobre datos. Estos dos tests bajan a
#    0123, siembran filas de unión SIN tenant_id (como estaban en producción) y
#    vuelven a subir. Sin ellos, el backfill y el pre-check serían código no
#    ejercitado: exactamente el patrón «guarda que no puede fallar».
# ===========================================================================
_PREV_REVISION = "0123_cortex_pursuit_approved"


def test_backfill_stamps_pre_existing_rows_on_upgrade(
    seeded, alembic_config: object, migrations_pg_dsn: str
) -> None:
    ids = seeded
    command.downgrade(alembic_config, _PREV_REVISION)  # type: ignore[arg-type]
    try:

        async def _pre_seed() -> None:
            conn = await asyncpg.connect(migrations_pg_dsn)
            try:
                # Sin la columna: es el estado que la migración debe reparar.
                await conn.execute(
                    "INSERT INTO agent_skills (agent_id, skill_id) VALUES ($1, $2)",
                    ids["agent_a"],
                    ids["skill_builtin"],  # hijo de plataforma: caso legítimo
                )
                await conn.execute(
                    "INSERT INTO team_members (team_id, agent_id) VALUES ($1, $2)",
                    ids["team_a"],
                    ids["agent_builtin"],  # miembro built-in en equipo de tenant
                )
                await conn.execute(
                    "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES ($1, $2)",
                    ids["task_b1"],
                    ids["task_b2"],
                )
            finally:
                await conn.close()

        asyncio.run(_pre_seed())
        command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

        async def _assert() -> None:
            conn = await asyncpg.connect(migrations_pg_dsn)
            try:
                assert (
                    await conn.fetchval(
                        "SELECT tenant_id FROM agent_skills WHERE agent_id = $1 AND skill_id = $2",
                        ids["agent_a"],
                        ids["skill_builtin"],
                    )
                    == ids["tenant_a"]
                ), "el backfill debe tomar el tenant del AGENTE, no de la skill built-in"
                assert (
                    await conn.fetchval(
                        "SELECT tenant_id FROM team_members WHERE team_id = $1 AND agent_id = $2",
                        ids["team_a"],
                        ids["agent_builtin"],
                    )
                    == ids["tenant_a"]
                ), "el backfill debe tomar el tenant del EQUIPO, no del agente built-in"
                assert (
                    await conn.fetchval(
                        "SELECT tenant_id FROM task_dependencies WHERE task_id = $1",
                        ids["task_b1"],
                    )
                    == ids["tenant_b"]
                )
                for table in JUNCTIONS:
                    assert (
                        await conn.fetchval(f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL")
                        == 0
                    )
            finally:
                await conn.close()

        asyncio.run(_assert())
    finally:
        command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


def test_upgrade_aborts_on_genuinely_cross_tenant_rows(
    seeded, alembic_config: object, migrations_pg_dsn: str
) -> None:
    """El pre-check es la mitigación del riesgo nº 1 del plan: sin él, el
    backfill le INVENTARÍA un tenant a una fila incoherente y lo consolidaría."""
    ids = seeded
    command.downgrade(alembic_config, _PREV_REVISION)  # type: ignore[arg-type]
    try:

        async def _poison() -> None:
            conn = await asyncpg.connect(migrations_pg_dsn)
            try:
                await conn.execute(
                    "INSERT INTO agent_skills (agent_id, skill_id) VALUES ($1, $2)",
                    ids["agent_a"],  # tenant A
                    ids["skill_b"],  # tenant B y NO built-in → incoherente
                )
            finally:
                await conn.close()

        asyncio.run(_poison())

        with pytest.raises(RuntimeError, match="agent_skills"):
            command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

        async def _still_at_prev() -> None:
            conn = await asyncpg.connect(migrations_pg_dsn)
            try:
                rev = await conn.fetchval("SELECT version_num FROM alembic_version")
                assert rev == _PREV_REVISION, f"la migración no revirtió limpia: {rev}"
                cols = await conn.fetchval(
                    "SELECT count(*) FROM information_schema.columns"
                    " WHERE table_name = 'agent_skills' AND column_name = 'tenant_id'"
                )
                assert cols == 0, "el abort dejó la columna a medias"
                # …y limpiamos la fila envenenada para que el upgrade final pase.
                await conn.execute(
                    "DELETE FROM agent_skills WHERE agent_id = $1 AND skill_id = $2",
                    ids["agent_a"],
                    ids["skill_b"],
                )
            finally:
                await conn.close()

        asyncio.run(_still_at_prev())
        command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    finally:
        command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
