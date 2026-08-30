"""La migración 0145 contra PostgreSQL real: a quién le devuelve `stack_exec`.

El banco reproduce las SEIS formas que un agente puede tener hoy en la base
viva, porque la única pregunta interesante de esta migración no es «¿inserta?»
sino «¿a quién NO toca?»:

1. **La copia atrapada** — clon de un built-in, rol que ejecuta, con `shell_exec`
   y sin `stack_exec`. Es el run de 2,22 USD: pide `composer`, la lista común
   (ADR 0162) lo acepta por la puerta del sandbox, y el binario no está.
2. **La copia sin ninguna puerta** — a la que su tenant dejó sin ejecución. NO se
   toca: ahí `stack_exec` sí sería autoridad nueva, y ese es justo el «no pisar
   personalizaciones» del enunciado.
3. **El reviewer clonado** — tiene `shell_exec` y rol `reviewer`. NO se toca: el
   ADR 0095 le monta el worktree del implementador en READ-ONLY, y `stack_exec`
   lo lanza el worker sobre ese mismo worktree en ESCRITURA.
4. **La copia que ya la tiene** — no se duplica y no entra en el respaldo, para
   que un `downgrade` no le quite lo que ya era suyo.
5. **El agente propio del tenant** — sin `forked_from_agent_id`. No es una copia
   de fábrica; no es asunto de esta migración.
6. **La copia de una plantilla del propio tenant** — el origen no es
   `global_builtin`. Idem.

Y el borrado: `downgrade` deja la base como estaba, ni una fila de más ni una de
menos, leyendo el respaldo en vez de recalcular (después del `upgrade` un grant
que puso la migración y uno que puso un administrador son indistinguibles).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = [pytest.mark.integration]

#: La revisión ANTERIOR a la 0145: el estado que su `downgrade` debe restaurar.
#: Por NOMBRE, nunca `-1` — con varios carriles añadiendo migraciones, `-1`
#: apunta a lo que haya debajo en ese momento.
_REVISION_BEFORE = "0144_timestamps_not_null"
_REVISION = "0145_stack_exec_builtin_forks"

_BACKFILL_TABLE = "agent_tools_backfill_0145"

_PLATFORM_TENANT = UUID("00000000-0000-0000-0000-000000000001")
_STACK_TOOL = UUID("11c92c57-5546-5285-a56a-519f3603547f")
_SHELL_TOOL = UUID("794009a8-fbed-5f71-9150-74b5415a2a8e")

#: ``(clave, rol, tiene_shell, tiene_stack, tipo_de_origen)``.
#: ``origen``: "builtin" = copia de fábrica; "tenant" = copia de una plantilla
#: del propio tenant; "none" = agente propio, sin origen.
_BANK: tuple[tuple[str, str, bool, bool, str], ...] = (
    ("atrapada", "backend_dev", True, False, "builtin"),
    ("sin-puertas", "backend_dev", False, False, "builtin"),
    ("reviewer", "reviewer", True, False, "builtin"),
    ("ya-la-tiene", "qa", True, True, "builtin"),
    ("propio", "backend_dev", True, False, "none"),
    ("copia-de-tenant", "backend_dev", True, False, "tenant"),
)

#: Los únicos que la migración debe tocar.
_EXPECTED_GRANTED = {"atrapada"}


async def _seed(dsn: str) -> dict[str, UUID]:
    """Deja el banco montado y devuelve ``clave -> agent_id``."""
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE agents RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE tools RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE projects RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)"
            " ON CONFLICT (id) DO NOTHING",
            _PLATFORM_TENANT,
            "platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "banco",
            f"banco-{tenant_id.hex[:8]}",
        )
        for tool_id, name in ((_STACK_TOOL, "stack_exec"), (_SHELL_TOOL, "shell_exec")):
            await conn.execute(
                "INSERT INTO tools (id, tenant_id, name, category, implementation_type,"
                " security_level, is_builtin)"
                " VALUES ($1, $2, $3, 'command', 'builtin', 'privileged', true)",
                tool_id,
                _PLATFORM_TENANT,
                name,
            )

        # Los dos posibles ORÍGENES de una copia.
        builtin_src, tenant_src = uuid4(), uuid4()
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope, is_template)"
            " VALUES ($1, $2, 'origen builtin', 'backend_dev', 'x', 'global_builtin', true)",
            builtin_src,
            _PLATFORM_TENANT,
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope, is_template)"
            " VALUES ($1, $2, 'origen del tenant', 'backend_dev', 'x',"
            " 'global_tenant_template', true)",
            tenant_src,
            tenant_id,
        )
        sources = {"builtin": builtin_src, "tenant": tenant_src, "none": None}

        # Una copia real vive en un proyecto (`scope = 'project_local'`), que es
        # como la deja la adopción de equipo. El banco lo reproduce en vez de
        # usar un scope global más cómodo: la migración no filtra por scope, y
        # probarla sobre una forma que no existe en producción es probar otra cosa.
        project_id = uuid4()
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'banco')",
            project_id,
            tenant_id,
        )

        ids: dict[str, UUID] = {}
        for key, role, has_shell, has_stack, origin in _BANK:
            agent_id = uuid4()
            ids[key] = agent_id
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope,"
                " project_id, forked_from_agent_id)"
                " VALUES ($1, $2, $3, $4, 'x', 'project_local', $5, $6)",
                agent_id,
                tenant_id,
                f"banco-{key}",
                role,
                project_id,
                sources[origin],
            )
            for tool_id, wanted in ((_SHELL_TOOL, has_shell), (_STACK_TOOL, has_stack)):
                if wanted:
                    await conn.execute(
                        "INSERT INTO agent_tools (agent_id, tool_id) VALUES ($1, $2)",
                        agent_id,
                        tool_id,
                    )
        return ids
    finally:
        await conn.close()


async def _grants(dsn: str, ids: dict[str, UUID]) -> dict[str, set[str]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT g.agent_id, t.name FROM agent_tools g JOIN tools t ON t.id = g.tool_id"
        )
    finally:
        await conn.close()
    by_id = {agent_id: key for key, agent_id in ids.items()}
    out: dict[str, set[str]] = {key: set() for key in ids}
    for row in rows:
        key = by_id.get(row["agent_id"])
        if key is not None:
            out[key].add(row["name"])
    return out


async def _fetch(dsn: str, sql: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return list(await conn.fetch(sql, *args))
    finally:
        await conn.close()


def _seeded_before_the_migration(alembic_config: object, dsn: str) -> dict[str, UUID]:
    """Deja la base en 0144 con el banco sembrado, listo para subir a la 0145."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    return asyncio.run(_seed(dsn))


# ---------------------------------------------------------------------------
# 1. A quién toca — y, sobre todo, a quién no
# ---------------------------------------------------------------------------
def test_only_the_trapped_copies_get_stack_exec(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_grants(migrations_pg_dsn, ids))
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    after = asyncio.run(_grants(migrations_pg_dsn, ids))

    granted = {key for key in ids if "stack_exec" in after[key] and "stack_exec" not in before[key]}
    assert granted == _EXPECTED_GRANTED, (
        f"la migración concedió stack_exec a {sorted(granted)}; se esperaba "
        f"{sorted(_EXPECTED_GRANTED)}"
    )
    # Nada se pierde por el camino: sólo se AÑADE.
    for key in ids:
        assert before[key] <= after[key], f"{key}: la migración quitó tools ({before[key]})"


def test_the_new_grant_carries_the_forks_own_tenant(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Multi-tenancy: la fila nueva pertenece al tenant de la COPIA.

    El trigger de la 0124 lo deriva del agente y aborta si contradice, pero eso
    lo hace correcto sólo si la migración apunta al agente correcto: un `JOIN`
    mal puesto insertaría a nombre del tenant de PLATAFORMA sin que nada más se
    quejara.
    """
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    rows = asyncio.run(
        _fetch(
            migrations_pg_dsn,
            "SELECT g.tenant_id AS grant_tenant, a.tenant_id AS agent_tenant"
            "  FROM agent_tools g JOIN agents a ON a.id = g.agent_id"
            " WHERE g.agent_id = $1 AND g.tool_id = $2",
            ids["atrapada"],
            _STACK_TOOL,
        )
    )
    assert len(rows) == 1
    assert rows[0]["grant_tenant"] == rows[0]["agent_tenant"] != _PLATFORM_TENANT


def test_a_soft_deleted_copy_is_left_alone(alembic_config: object, migrations_pg_dsn: str) -> None:
    """Un agente borrado no se resucita a medias con una capacidad nueva."""
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    asyncio.run(
        _fetch(
            migrations_pg_dsn,
            "UPDATE agents SET deleted_at = now() WHERE id = $1 RETURNING id",
            ids["atrapada"],
        )
    )
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    after = asyncio.run(_grants(migrations_pg_dsn, ids))
    assert "stack_exec" not in after["atrapada"]


# ---------------------------------------------------------------------------
# 2. Idempotencia y vuelta atrás
# ---------------------------------------------------------------------------
def test_a_downgrade_and_a_second_upgrade_land_on_the_same_state(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Bajar y volver a subir deja exactamente lo mismo, respaldo incluido.

    El nombre dice «downgrade y segundo upgrade» y no «correrla dos veces»
    porque el cuerpo hace lo primero: Alembic no vuelve a ejecutar una revisión
    que ya está aplicada, así que un `upgrade` repetido es un no-op del runner y
    no probaría nada de esta migración. El ciclo completo sí: es la operación
    real de una vuelta atrás seguida de un re-despliegue, y es donde un backfill
    mal escrito duplica grants o deja el respaldo con filas de más.
    """
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    first = asyncio.run(_grants(migrations_pg_dsn, ids))
    backfill_first = asyncio.run(_fetch(migrations_pg_dsn, f"SELECT * FROM {_BACKFILL_TABLE}"))

    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    second = asyncio.run(_grants(migrations_pg_dsn, ids))
    backfill_second = asyncio.run(_fetch(migrations_pg_dsn, f"SELECT * FROM {_BACKFILL_TABLE}"))

    assert first == second
    assert len(backfill_first) == len(backfill_second) == len(_EXPECTED_GRANTED)


def test_downgrade_removes_exactly_what_the_upgrade_added(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_grants(migrations_pg_dsn, ids))
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    after = asyncio.run(_grants(migrations_pg_dsn, ids))

    assert after == before, "el downgrade no dejó la base como estaba"
    # `ya-la-tiene` es el que demuestra que el borrado NO se recalcula: su
    # `stack_exec` es anterior a la migración y tiene que sobrevivir al bajar.
    assert "stack_exec" in after["ya-la-tiene"]
    survivors = asyncio.run(
        _fetch(migrations_pg_dsn, "SELECT to_regclass($1) AS t", _BACKFILL_TABLE)
    )
    assert survivors[0]["t"] is None, "el respaldo debe irse con el downgrade"


# ---------------------------------------------------------------------------
# 3. El respaldo no es legible por la aplicación (lección de la 0138)
# ---------------------------------------------------------------------------
def test_the_backfill_table_is_unreachable_from_the_app(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Nace sin permisos para `app_user`, no «se le ponen luego».

    Los default privileges de `docker/postgres/init/02-roles.sh` alcanzan a toda
    tabla que Alembic cree, así que una tabla de bookkeeping sin `tenant_id` ni
    RLS nacería legible cross-tenant. Es literalmente el agujero que destapó la
    0138 sobre el respaldo de la 0133.
    """
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    rows = asyncio.run(
        _fetch(
            migrations_pg_dsn,
            "SELECT rolname, has_table_privilege(rolname, $1, 'SELECT') AS can_read"
            "  FROM pg_roles WHERE rolname IN ('app_user', 'service_user')",
            _BACKFILL_TABLE,
        )
    )
    assert rows, "el arnés no tiene ni app_user ni service_user: la guarda pasaría vacía"
    for row in rows:
        assert not row["can_read"], f"{row['rolname']} puede leer {_BACKFILL_TABLE}"
