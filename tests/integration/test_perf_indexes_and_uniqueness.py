"""prod-13 · task_prod13_11 + task_prod13_13 — la migración 0126, contra PostgreSQL.

Tres cosas se verifican aquí, y ninguna se puede verificar leyendo el modelo:

1. `ix_executions_tenant_created_at` existe y su definición lleva las dos
   columnas EN ORDEN (`tenant_id` primero: es la igualdad; `created_at` después:
   es el rango). Un índice `(created_at, tenant_id)` pasaría un test que solo
   comprobase el nombre y no serviría para la ventana de gasto.
2. Los índices únicos parciales de `teams` / `skills` / `agents` existen, son
   UNIQUE, y **rechazan de verdad** el segundo INSERT — se prueba insertando.
3. El caso que el plan daba por imposible: un agente `project_local` forkeado de
   su plantilla `global_tenant_template` conserva el nombre, y ESO TIENE QUE
   SEGUIR FUNCIONANDO. Es la razón de que `agents` lleve dos índices partidos
   por `project_id` en vez del `(tenant_id, name)` que pedía el plan.

Más: `documents.source_size_bytes` es BIGINT y admite un valor > 2^31-1, y
`ix_chunks_content_fts` sigue construido sobre `public.es_unaccent` (lo dejó la
0107; el test lo fija como contrato para que nadie lo devuelva a `'simple'`).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_INT4_MAX = 2**31 - 1


#: La revisión ANTERIOR a la 0126: el estado que su `downgrade` debe restaurar.
#: Anclado a la revisión y NO a `-1`, que solo acierta mientras la 0126 sea la
#: cabeza. Ese atajo ya puso en rojo el round-trip de la 0125 el día que se apiló
#: una migración encima: bajaba un paso, se quedaba en la 0125 y veía su propio
#: efecto todavía aplicado. Con el nombre, el test mide lo suyo para siempre.
_REVISION_BEFORE = "0125_cortex_conv_rls"


@pytest.fixture()
def migrated(alembic_config, test_database_url: str) -> None:
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())


async def _indexdef(conn: asyncpg.Connection, table: str, index: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT indexdef FROM pg_indexes WHERE tablename = $1 AND indexname = $2",
        table,
        index,
    )
    return None if row is None else str(row["indexdef"])


async def _new_tenant(conn: asyncpg.Connection) -> UUID:
    tenant = uuid4()
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
        tenant,
        f"t-{tenant.hex[:10]}",
    )
    return tenant


async def _insert_agent(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID,
    name: str,
    scope: str,
    project_id: UUID | None,
) -> UUID:
    agent_id = uuid4()
    await conn.execute(
        "INSERT INTO agents (id, tenant_id, name, role, agent_type, scope, project_id,"
        " system_prompt)"
        " VALUES ($1, $2, $3, 'backend_dev', 'ai', $4, $5, 'p')",
        agent_id,
        tenant_id,
        name,
        scope,
        project_id,
    )
    return agent_id


async def _new_project(conn: asyncpg.Connection, tenant_id: UUID) -> UUID:
    project_id = uuid4()
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name, slug) VALUES ($1, $2, 'P', $3)",
        project_id,
        tenant_id,
        f"p-{project_id.hex[:10]}",
    )
    return project_id


# ---------------------------------------------------------------------------
# 1. executions (tenant_id, created_at)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_executions_tenant_created_at_index_columns_in_order(
    migrated: None, migrations_pg_dsn: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        indexdef = await _indexdef(conn, "executions", "ix_executions_tenant_created_at")
    finally:
        await conn.close()

    assert indexdef is not None, "la 0126 no creó ix_executions_tenant_created_at"
    # `(tenant_id, created_at)` y no al revés: el prefijo tiene que ser la
    # igualdad para que el rango de fechas sea un index scan.
    assert "(tenant_id, created_at)" in indexdef, indexdef


# ---------------------------------------------------------------------------
# 2. Unicidad: los índices existen Y rechazan el duplicado
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("table", "index"),
    [
        ("teams", "uq_teams_tenant_name_live"),
        ("skills", "uq_skills_tenant_name_live"),
        ("agents", "uq_agents_tenant_project_name_live"),
        ("agents", "uq_agents_tenant_name_global_live"),
    ],
)
@pytest.mark.asyncio
async def test_partial_unique_indexes_exist(
    migrated: None, migrations_pg_dsn: str, table: str, index: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        indexdef = await _indexdef(conn, table, index)
    finally:
        await conn.close()

    assert indexdef is not None, f"falta {index} en {table}"
    assert "UNIQUE" in indexdef, indexdef
    assert "deleted_at IS NULL" in indexdef, indexdef


@pytest.mark.asyncio
async def test_teams_duplicate_live_name_is_rejected(
    migrated: None, migrations_pg_dsn: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        tenant = await _new_tenant(conn)
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, 'Equipo')",
            uuid4(),
            tenant,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, 'Equipo')",
                uuid4(),
                tenant,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_soft_deleted_team_name_is_free_again(migrated: None, migrations_pg_dsn: str) -> None:
    """El índice es PARCIAL: soft-borrar libera el nombre. Sin el `WHERE`, esto
    fallaría y un tenant no podría reutilizar el nombre de un equipo borrado."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        tenant = await _new_tenant(conn)
        first = uuid4()
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, 'Reutilizable')",
            first,
            tenant,
        )
        await conn.execute("UPDATE teams SET deleted_at = now() WHERE id = $1", first)
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, 'Reutilizable')",
            uuid4(),
            tenant,
        )
        live = await conn.fetchval(
            "SELECT count(*) FROM teams WHERE tenant_id = $1 AND deleted_at IS NULL",
            tenant,
        )
    finally:
        await conn.close()
    assert live == 1


@pytest.mark.asyncio
async def test_skills_duplicate_live_name_is_rejected(
    migrated: None, migrations_pg_dsn: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        tenant = await _new_tenant(conn)
        for _ in range(1):
            await conn.execute(
                "INSERT INTO skills (id, tenant_id, name, category, prompt_fragment)"
                " VALUES ($1, $2, 'Habilidad', 'backend', 'f')",
                uuid4(),
                tenant,
            )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO skills (id, tenant_id, name, category, prompt_fragment)"
                " VALUES ($1, $2, 'Habilidad', 'backend', 'f')",
                uuid4(),
                tenant,
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 3. El fork de agentes NO se rompe (la razón de los dos índices)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_project_local_fork_may_reuse_the_template_name(
    migrated: None, migrations_pg_dsn: str
) -> None:
    """Un `(tenant_id, name)` único habría prohibido esto, y el fork de agentes
    lo hace en cada proyecto. Medido en la BD de desarrollo: 10 grupos así."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        tenant = await _new_tenant(conn)
        project = await _new_project(conn, tenant)
        await _insert_agent(
            conn,
            tenant_id=tenant,
            name="CodeIgniter 4 — Backend Dev",
            scope="global_tenant_template",
            project_id=None,
        )
        await _insert_agent(
            conn,
            tenant_id=tenant,
            name="CodeIgniter 4 — Backend Dev",
            scope="project_local",
            project_id=project,
        )
        same_name = await conn.fetchval(
            "SELECT count(*) FROM agents WHERE tenant_id = $1 AND deleted_at IS NULL",
            tenant,
        )
    finally:
        await conn.close()
    assert same_name == 2


@pytest.mark.asyncio
async def test_two_global_agents_with_the_same_name_are_rejected(
    migrated: None, migrations_pg_dsn: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        tenant = await _new_tenant(conn)
        await _insert_agent(
            conn,
            tenant_id=tenant,
            name="Plantilla",
            scope="global_tenant_template",
            project_id=None,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_agent(
                conn,
                tenant_id=tenant,
                name="Plantilla",
                scope="global_builtin",
                project_id=None,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_two_project_local_agents_with_the_same_name_in_one_project_are_rejected(
    migrated: None, migrations_pg_dsn: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        tenant = await _new_tenant(conn)
        project = await _new_project(conn, tenant)
        await _insert_agent(
            conn, tenant_id=tenant, name="Local", scope="project_local", project_id=project
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_agent(
                conn, tenant_id=tenant, name="Local", scope="project_local", project_id=project
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_same_name_in_two_different_projects_is_allowed(
    migrated: None, migrations_pg_dsn: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        tenant = await _new_tenant(conn)
        first, second = await _new_project(conn, tenant), await _new_project(conn, tenant)
        await _insert_agent(
            conn, tenant_id=tenant, name="QA", scope="project_local", project_id=first
        )
        await _insert_agent(
            conn, tenant_id=tenant, name="QA", scope="project_local", project_id=second
        )
        total = await conn.fetchval(
            "SELECT count(*) FROM agents WHERE tenant_id = $1 AND name = 'QA'", tenant
        )
    finally:
        await conn.close()
    assert total == 2


# ---------------------------------------------------------------------------
# Extras de la misma migración
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_source_size_bytes_is_bigint_and_holds_over_2gib(
    migrated: None, migrations_pg_dsn: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        data_type = await conn.fetchval(
            "SELECT data_type FROM information_schema.columns"
            " WHERE table_name = 'documents' AND column_name = 'source_size_bytes'"
        )
        assert data_type == "bigint", data_type

        tenant = await _new_tenant(conn)
        kb_id, doc_id = uuid4(), uuid4()
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, 'KB')",
            kb_id,
            tenant,
        )
        big = _INT4_MAX + 1_000
        await conn.execute(
            "INSERT INTO documents (id, tenant_id, kb_id, title, source_filename,"
            " source_mime_type, source_storage_key, source_size_bytes)"
            " VALUES ($1, $2, $3, 'D', 'd.pdf', 'application/pdf', 'k', $4)",
            doc_id,
            tenant,
            kb_id,
            big,
        )
        stored = await conn.fetchval(
            "SELECT source_size_bytes FROM documents WHERE id = $1", doc_id
        )
    finally:
        await conn.close()
    assert stored == big


@pytest.mark.asyncio
async def test_chunks_fts_index_still_uses_es_unaccent(
    migrated: None, migrations_pg_dsn: str
) -> None:
    """task_prod13_10 ya la cerró la migración 0107. Este test la fija como
    contrato: si alguien devuelve el índice a `'simple'`, el agente y el
    operador vuelven a ver resultados distintos para la misma consulta."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        indexdef = await _indexdef(conn, "chunks", "ix_chunks_content_fts")
    finally:
        await conn.close()
    assert indexdef is not None
    assert "es_unaccent" in indexdef, indexdef
    assert "'simple'" not in indexdef, indexdef


async def _insert_oversized_document(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        tenant = await _new_tenant(conn)
        kb_id = uuid4()
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, 'KB-big')",
            kb_id,
            tenant,
        )
        await conn.execute(
            "INSERT INTO documents (id, tenant_id, kb_id, title, source_filename,"
            " source_mime_type, source_storage_key, source_size_bytes)"
            " VALUES ($1, $2, $3, 'D', 'd.pdf', 'application/pdf', 'k', $4)",
            uuid4(),
            tenant,
            kb_id,
            _INT4_MAX + 7,
        )
    finally:
        await conn.close()


async def _delete_oversized_documents(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("DELETE FROM documents WHERE source_size_bytes > $1", _INT4_MAX)
    finally:
        await conn.close()


async def _shape(dsn: str) -> dict[str, str | None]:
    """Foto de lo que la 0126 toca: los cinco índices y el tipo de la columna."""
    conn = await asyncpg.connect(dsn)
    try:
        shape: dict[str, str | None] = {
            f"{table}.{name}": await _indexdef(conn, table, name)
            for name, table in (
                ("ix_executions_tenant_created_at", "executions"),
                ("uq_teams_tenant_name_live", "teams"),
                ("uq_skills_tenant_name_live", "skills"),
                ("uq_agents_tenant_project_name_live", "agents"),
                ("uq_agents_tenant_name_global_live", "agents"),
                ("ix_teams_tenant_name", "teams"),
            )
        }
        shape["documents.source_size_bytes"] = await conn.fetchval(
            "SELECT data_type FROM information_schema.columns WHERE table_name = 'documents'"
            " AND column_name = 'source_size_bytes'"
        )
        return shape
    finally:
        await conn.close()


def test_downgrade_upgrade_roundtrip_restores_both_shapes(
    migrated: None, alembic_config, migrations_pg_dsn: str
) -> None:
    """`downgrade -1` deja el esquema como lo dejó la 0125, y `upgrade head` lo
    devuelve. Criterio de cierre 5 del plan.

    Lo que se comprueba de verdad: al bajar desaparecen los cinco índices nuevos
    y **reaparece** `ix_teams_tenant_name` (que el upgrade retiró) como índice NO
    único, y `source_size_bytes` vuelve a INTEGER. Un downgrade que solo dropease
    los índices dejaría a `teams` sin ningún índice sobre `(tenant_id, name)`.

    Test SÍNCRONO a propósito: el `env.py` de Alembic hace su propio
    `asyncio.run`, así que `command.downgrade` no puede invocarse desde dentro de
    un bucle de eventos ya en marcha (gotcha real: la primera versión de este
    test reventaba con "asyncio.run() cannot be called from a running event
    loop").
    """
    at_head = asyncio.run(_shape(migrations_pg_dsn))

    # El downgrade a INTEGER NO puede ser silencioso: si hay una fila con un
    # tamaño que no cabe en int4, PostgreSQL lo rechaza y la migración revienta.
    # Es el comportamiento correcto (truncar el tamaño real sería peor) y se fija
    # aquí como contrato antes de limpiar y bajar de verdad.
    asyncio.run(_insert_oversized_document(migrations_pg_dsn))
    with pytest.raises(Exception, match="out of range"):
        command.downgrade(alembic_config, _REVISION_BEFORE)
    asyncio.run(_delete_oversized_documents(migrations_pg_dsn))

    command.downgrade(alembic_config, _REVISION_BEFORE)
    at_0125 = asyncio.run(_shape(migrations_pg_dsn))
    for key in (
        "executions.ix_executions_tenant_created_at",
        "teams.uq_teams_tenant_name_live",
        "skills.uq_skills_tenant_name_live",
        "agents.uq_agents_tenant_project_name_live",
        "agents.uq_agents_tenant_name_global_live",
    ):
        assert at_0125[key] is None, f"{key} sobrevivió al downgrade"
    restored = at_0125["teams.ix_teams_tenant_name"]
    assert restored is not None, "el downgrade no restauró ix_teams_tenant_name"
    assert "UNIQUE" not in restored, restored
    assert at_0125["documents.source_size_bytes"] == "integer"

    command.upgrade(alembic_config, "head")
    assert asyncio.run(_shape(migrations_pg_dsn)) == at_head
