"""La migración 0146 contra PostgreSQL real: a quién le lleva `move_file`.

El banco reproduce las NUEVE formas que un agente puede tener hoy en la base
viva, porque —igual que en la 0145— la pregunta interesante no es «¿inserta?»
sino «¿a quién NO toca?». Las cuatro primeras son las que fijan la condición de
seguridad de esta migración concreta:

1. **La copia atrapada** — clon de un built-in, rol que escribe, con
   `write_file` Y `delete_file` y sin `move_file`. Es el incidente del
   2026-08-31: `composer create-project .` exige un directorio vacío, el agente
   llegó solo al plan correcto (instalar en `tmpci/` y mover el resultado) y de
   sus tres pasos el único ejecutable era el destructivo. Borró `app/` entera,
   85 ficheros ya commiteados por la tarea anterior.
2. **La que sólo BORRA** — tiene `delete_file` y no `write_file`. NO se toca:
   mover pone bytes en una ruta donde no había nada, y borrar no sabe hacer eso.
   Concedérselo sería autoridad nueva.
3. **La que sólo ESCRIBE** — tiene `write_file` y no `delete_file`. NO se toca
   por el lado simétrico: `move_file("app", "app.old")` retira `app/` de su sitio
   igual que borrarla, y a este agente su tenant le dejó sin esa mitad.
4. **La copia sin ninguna puerta** — ni una ni otra. NO se toca.
5. **El reviewer clonado CON escritura** — y no es un caso hipotético: hasta el
   2026-08-30 el seed del equipo CodeIgniter le daba `write-file` y
   `delete-file` a su reviewer mientras el mapa por rol decía `_READ`, así que
   las copias adoptadas antes de esa fecha tienen exactamente esta forma. Es el
   caso que demuestra que el filtro por ROL es load-bearing: sin él, la pareja
   de tools sola le concedería `move_file` a quien monta el worktree en
   READ-ONLY (ADR 0095).
6. **La copia que ya la tiene** — no se duplica y no entra en el respaldo, para
   que un `downgrade` no le quite lo que ya era suyo.
7. **El escritor que NO ejecuta** — un `technical_writer` con la pareja. SÍ se
   toca, y es la diferencia deliberada con la 0145: aquélla repartía una puerta
   de EJECUCIÓN y su población eran los roles que corren el toolchain; ésta
   reparte una puerta de ESCRITURA y su población son los nueve roles que
   escriben ficheros. Copiar la lista de la 0145 habría dejado fuera al
   technical_writer y al researcher.
8. **El agente propio del tenant** — sin `forked_from_agent_id`. No es una copia
   de fábrica; no es asunto de esta migración.
9. **La copia de una plantilla del propio tenant** — el origen no es
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

#: La revisión ANTERIOR a la 0146: el estado que su `downgrade` debe restaurar.
#: Por NOMBRE, nunca `-1` — con varios carriles añadiendo migraciones, `-1`
#: apunta a lo que haya debajo en ese momento.
_REVISION_BEFORE = "0145_stack_exec_builtin_forks"
_REVISION = "0146_move_file_builtin_forks"

_BACKFILL_TABLE = "agent_tools_backfill_0146"

_PLATFORM_TENANT = UUID("00000000-0000-0000-0000-000000000001")
#: Los ids reales del catálogo (`uuid5(TOOL_SEED_NAMESPACE, f"tool:{slug}")`).
_MOVE_TOOL = UUID("bf961ec4-edc8-545f-8eb8-d9c3365c9ea7")
_WRITE_TOOL = UUID("6454eff5-6bd6-5f93-9ca1-5f13d256da15")
_DELETE_TOOL = UUID("3704c26f-46a8-5c46-b6ca-32b8869fec19")

#: ``(clave, rol, tiene_write, tiene_delete, tiene_move, tipo_de_origen)``.
#: ``origen``: "builtin" = copia de fábrica; "tenant" = copia de una plantilla
#: del propio tenant; "none" = agente propio, sin origen.
_BANK: tuple[tuple[str, str, bool, bool, bool, str], ...] = (
    ("atrapada", "backend_dev", True, True, False, "builtin"),
    ("solo-borra", "backend_dev", False, True, False, "builtin"),
    ("solo-escribe", "backend_dev", True, False, False, "builtin"),
    ("sin-puertas", "backend_dev", False, False, False, "builtin"),
    ("reviewer-con-escritura", "reviewer", True, True, False, "builtin"),
    ("ya-la-tiene", "qa", True, True, True, "builtin"),
    ("escritor-no-ejecutor", "technical_writer", True, True, False, "builtin"),
    ("propio", "backend_dev", True, True, False, "none"),
    ("copia-de-tenant", "backend_dev", True, True, False, "tenant"),
)

#: Los únicos que la migración debe tocar.
_EXPECTED_GRANTED = {"atrapada", "escritor-no-ejecutor"}


async def _seed(dsn: str, *, catalogo_con_move: bool = True) -> dict[str, UUID]:
    """Deja el banco montado y devuelve ``clave -> agent_id``.

    `catalogo_con_move` distingue los dos ÓRDENES posibles, que es la
    diferencia entre que esta migración haga algo o sea un no-op:

    * ``True`` (por defecto) — el catálogo ya está sembrado, como en una base que
      lleva tiempo viva. Las filas de `tools` llevan `description`, igual que las
      deja `seed_builtin_tools`.
    * ``False`` — el orden REAL del despliegue de esta migración: el one-shot
      `migrations` corre ANTES de que el api-server arranque y siembre el
      catálogo, y `move_file` es una fila nueva del 2026-08-31. Ahí la tool no
      existe todavía.
    """
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
        catalogo = [(_WRITE_TOOL, "write_file"), (_DELETE_TOOL, "delete_file")]
        if catalogo_con_move:
            catalogo.append((_MOVE_TOOL, "move_file"))
        for tool_id, name in catalogo:
            # Con `description`, que es como las deja `seed_builtin_tools`: el
            # `downgrade` usa ese campo para distinguir una fila del catálogo
            # (que no es suya) de la fila mínima que crea el `upgrade`.
            await conn.execute(
                "INSERT INTO tools (id, tenant_id, name, description, category,"
                " implementation_type, security_level, is_builtin)"
                " VALUES ($1, $2, $3, $4, 'file', 'builtin', 'sandboxed', true)",
                tool_id,
                _PLATFORM_TENANT,
                name,
                f"sembrada por el catálogo: {name}",
            )
        presentes = {tool_id for tool_id, _ in catalogo}

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
        for key, role, has_write, has_delete, has_move, origin in _BANK:
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
            for tool_id, wanted in (
                (_WRITE_TOOL, has_write),
                (_DELETE_TOOL, has_delete),
                (_MOVE_TOOL, has_move),
            ):
                if wanted and tool_id in presentes:
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


def _seeded_before_the_migration(
    alembic_config: object, dsn: str, *, catalogo_con_move: bool = True
) -> dict[str, UUID]:
    """Deja la base en 0145 con el banco sembrado, listo para subir a la 0146."""
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    return asyncio.run(_seed(dsn, catalogo_con_move=catalogo_con_move))


async def _move_tool_rows(dsn: str) -> list[asyncpg.Record]:
    """Todas las filas de `tools` llamadas `move_file`, borradas incluidas."""
    return await _fetch(
        dsn,
        "SELECT id, description, is_builtin, security_level, deleted_at"
        "  FROM tools WHERE name = 'move_file'",
    )


# ---------------------------------------------------------------------------
# 1. A quién toca — y, sobre todo, a quién no
# ---------------------------------------------------------------------------
def test_only_copies_that_already_write_and_delete_get_move_file(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_grants(migrations_pg_dsn, ids))
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    after = asyncio.run(_grants(migrations_pg_dsn, ids))

    granted = {key for key in ids if "move_file" in after[key] and "move_file" not in before[key]}
    assert granted == _EXPECTED_GRANTED, (
        f"la migración concedió move_file a {sorted(granted)}; se esperaba "
        f"{sorted(_EXPECTED_GRANTED)}"
    )
    # Nada se pierde por el camino: sólo se AÑADE.
    for key in ids:
        assert before[key] <= after[key], f"{key}: la migración quitó tools ({before[key]})"


def test_half_a_pair_is_not_enough(alembic_config: object, migrations_pg_dsn: str) -> None:
    """El corazón del argumento de autoridad, afirmado por separado.

    Va suelto y no dentro del test de arriba porque es la única razón por la que
    esta migración exige la PAREJA y no sólo `delete_file`: a quien le falta
    `write_file`, mover le regala poner bytes en una ruta nueva; a quien le falta
    `delete_file`, le regala retirar un árbol de su sitio. Un fallo aquí tiene un
    nombre que dice qué se rompió, no «el conjunto no coincide».
    """
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    after = asyncio.run(_grants(migrations_pg_dsn, ids))

    assert "move_file" not in after["solo-borra"], (
        "un agente al que su tenant dejó sin `write_file` recibió `move_file`: "
        "mover pone bytes en una ruta donde no había nada, y eso es autoridad nueva"
    )
    assert "move_file" not in after["solo-escribe"], (
        "un agente al que su tenant dejó sin `delete_file` recibió `move_file`: "
        "mover retira un árbol de su sitio, y eso es autoridad nueva"
    )
    assert "move_file" not in after["sin-puertas"]


def test_the_reviewer_is_left_alone_even_holding_both_doors(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """El filtro por rol es load-bearing, y este caso existe de verdad.

    Hasta el 2026-08-30 el seed del equipo CodeIgniter le daba `write-file` y
    `delete-file` a su reviewer mientras `ROLE_DEFAULT_TOOLS` le daba `_READ`;
    las copias adoptadas antes de esa fecha tienen las dos puertas. Sin el filtro
    por rol, la condición de la pareja sola le concedería `move_file` a quien
    trabaja sobre un worktree montado READ-ONLY (ADR 0095) — una puerta que sólo
    le enseñaría a perseguir un EROFS.
    """
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    after = asyncio.run(_grants(migrations_pg_dsn, ids))
    assert {"write_file", "delete_file"} <= after["reviewer-con-escritura"], (
        "el banco ya no reproduce al reviewer CON escritura: esta guarda pasaría vacía"
    )
    assert "move_file" not in after["reviewer-con-escritura"]


def test_a_writer_that_does_not_run_the_toolchain_is_included(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """La diferencia deliberada con la 0145, afirmada para que no se copie mal.

    La 0145 repartía una puerta de EJECUCIÓN y su población eran los roles de
    `ROLES_THAT_EXECUTE_TOOLCHAIN`. Ésta reparte una puerta de ESCRITURA: el
    `technical_writer` y el `researcher` escriben ficheros y no corren el
    toolchain, así que reutilizar aquella lista los habría dejado fuera.
    """
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    after = asyncio.run(_grants(migrations_pg_dsn, ids))
    assert "move_file" in after["escritor-no-ejecutor"]


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
            _MOVE_TOOL,
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
    assert "move_file" not in after["atrapada"]


# ---------------------------------------------------------------------------
# 2. El orden real del despliegue: la migración corre ANTES que el seed
# ---------------------------------------------------------------------------
# Es la diferencia entre que esta migración arregle algo o no haga nada, y no se
# hereda del precedente: la 0145 repartía `stack_exec`, cuya fila del catálogo
# llevaba en `tools` desde el ADR 0093, así que su búsqueda por nombre siempre
# encontraba algo. `move_file` es una fila NUEVA del mismo día que esta
# migración, y el one-shot `migrations` del compose corre ANTES de que el
# api-server arranque y siembre el catálogo (`depends_on: migrations:
# service_completed_successfully`, y `bootstrap/database.py` además exige que las
# revisiones estén aplicadas antes de sembrar).
#
# O sea que en el despliegue REAL —el único que importa— la tool no existe
# cuando la migración corre. Una migración que la busque por nombre encuentra
# cero filas, el `CROSS JOIN` se queda vacío, no inserta nada y `alembic_version`
# la marca aplicada para siempre: no-op silencioso, y el arreglo no llega
# justamente al proyecto que pagó el incidente.
def test_the_migration_does_not_depend_on_the_seed_having_run(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn, catalogo_con_move=False)
    assert not asyncio.run(_move_tool_rows(migrations_pg_dsn)), (
        "el banco arrancó CON la tool en el catálogo: este test estaría "
        "comprobando el orden fácil, no el real"
    )

    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]

    after = asyncio.run(_grants(migrations_pg_dsn, ids))
    granted = {key for key in ids if "move_file" in after[key]}
    # `ya-la-tiene` se suma en ESTE escenario y no es una excepción a la regla:
    # sin fila de catálogo nadie puede tener la tool concedida de antes (la FK lo
    # impide), así que en este orden es sencillamente otra copia atrapada. Que
    # aparezca es la prueba de que la población se decide por las tools que el
    # agente YA tiene, no por una lista de nombres.
    assert granted == _EXPECTED_GRANTED | {"ya-la-tiene"}, (
        "con el catálogo aún sin sembrar la migración no repartió lo que debía: "
        "sin crear la fila que falta es un no-op silencioso, que es exactamente "
        "el orden en que se va a desplegar"
    )

    filas = asyncio.run(_move_tool_rows(migrations_pg_dsn))
    assert len(filas) == 1, "la migración debe dejar UNA fila de catálogo, no cero ni dos"
    assert filas[0]["is_builtin"] is True
    assert filas[0]["deleted_at"] is None


def test_the_row_it_creates_is_the_one_the_seed_will_recognise(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Mismo `id` que `_tool_id("move-file")`, o el seed crearía una fila gemela.

    `seed_builtin_tools` hace `ON CONFLICT (id) DO UPDATE`: si la migración usara
    otro id, el seed insertaría una SEGUNDA fila `move_file` —que además chocaría
    con el índice único parcial `uq_tools_tenant_name`— y los grants repartidos
    aquí colgarían de la fila equivocada, invisible para el catálogo.
    """
    from api_server.seeds.builtin_tools import BUILTIN_TOOLS

    _seeded_before_the_migration(alembic_config, migrations_pg_dsn, catalogo_con_move=False)
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    filas = asyncio.run(_move_tool_rows(migrations_pg_dsn))
    assert filas[0]["id"] == _MOVE_TOOL

    # Y los valores que la fila mínima SÍ declara son los del catálogo: el seed
    # hará `ON CONFLICT (id) DO UPDATE` sobre ella, y una fila que entre medias
    # ya sirvió para conceder permisos no debería cambiar de nivel de seguridad
    # al arrancar. `description` y los schemas se quedan para el seed a propósito.
    move = next(tool for tool in BUILTIN_TOOLS if tool.slug == "move-file")
    assert filas[0]["security_level"] == move.security_level
    assert filas[0]["description"] is None, (
        "la fila mínima trae description: el `downgrade` la usa como marca de "
        "«el seed no la ha tocado» y dejaría de poder distinguirlas"
    )


def test_a_deliberately_retired_move_file_is_not_resurrected(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Si la plataforma retiró la tool (como la 0122 con los `run_*`), se respeta.

    Crear la fila que falta es legítimo porque el catálogo aún no se ha sembrado;
    revivir una que alguien borró a propósito sería la misma clase de error que
    devolverle a un tenant una tool que quitó.
    """
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    asyncio.run(
        _fetch(
            migrations_pg_dsn,
            "UPDATE tools SET deleted_at = now() WHERE id = $1 RETURNING id",
            _MOVE_TOOL,
        )
    )
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]

    filas = asyncio.run(_move_tool_rows(migrations_pg_dsn))
    assert len(filas) == 1, "la migración insertó una fila gemela sobre una retirada"
    assert filas[0]["deleted_at"] is not None, "la migración resucitó una tool retirada"
    after = asyncio.run(_grants(migrations_pg_dsn, ids))
    assert "move_file" not in after["atrapada"]


def test_the_downgrade_takes_back_the_catalog_row_it_created(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Reversibilidad completa: si el `upgrade` creó la fila, el `downgrade` la quita."""
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn, catalogo_con_move=False)
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    assert not asyncio.run(_move_tool_rows(migrations_pg_dsn)), (
        "el downgrade dejó atrás la fila de catálogo que había creado el upgrade"
    )


def test_the_downgrade_leaves_a_catalog_row_the_seed_owns(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Y por el otro lado: lo que sembró el catálogo NO es suyo para borrarlo.

    En cuanto el api-server arranca, `seed_builtin_tools` completa la fila
    (`description` incluida). A partir de ahí la fila la afirma el CÓDIGO en cada
    arranque; un `downgrade` que se la llevara estaría deshaciendo trabajo que no
    hizo — el mismo error que recalcular los grants en vez de leer el respaldo.
    """
    _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    filas = asyncio.run(_move_tool_rows(migrations_pg_dsn))
    assert len(filas) == 1 and filas[0]["deleted_at"] is None, (
        "el downgrade borró una fila de catálogo que había sembrado el seed"
    )


# ---------------------------------------------------------------------------
# 3. Idempotencia y vuelta atrás
# ---------------------------------------------------------------------------
def test_a_downgrade_and_a_second_upgrade_land_on_the_same_state(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Bajar y volver a subir deja exactamente lo mismo, respaldo incluido.

    El nombre dice «downgrade y segundo upgrade» y no «correrla dos veces»
    porque el cuerpo hace lo primero: Alembic no vuelve a ejecutar una revisión
    ya aplicada, así que un `upgrade` repetido es un no-op del runner y no
    probaría nada de esta migración. El ciclo completo sí: es la operación real
    de una vuelta atrás seguida de un re-despliegue, y es donde un backfill mal
    escrito duplica grants o deja el respaldo con filas de más.
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
    # `move_file` es anterior a la migración y tiene que sobrevivir al bajar.
    assert "move_file" in after["ya-la-tiene"]
    survivors = asyncio.run(
        _fetch(migrations_pg_dsn, "SELECT to_regclass($1) AS t", _BACKFILL_TABLE)
    )
    assert survivors[0]["t"] is None, "el respaldo debe irse con el downgrade"


# ---------------------------------------------------------------------------
# 4. El respaldo no es legible por la aplicación (lección de la 0138)
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
