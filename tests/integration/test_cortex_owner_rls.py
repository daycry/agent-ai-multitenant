"""RLS de eje OWNER en las cinco tablas del córtex que faltaban (migración 0140).

## Por qué este fichero existe

La migración `0125` protegió `cortex_conversations` y dejó escrito, en su propio
docstring, lo que NO cerraba: `cortex_turns` —donde vive el TEXTO del chat—,
`cortex_identity`, `cortex_identity_history`, `cortex_affect_snapshots` y
`cortex_curiosity_pursuits` seguían sin `ENABLE`, sin `FORCE` y sin una sola
policy. Como ninguna tiene columna `tenant_id`, el meta-invariante
`test_rls_invariant.py` ni las miraba: salían por la puerta del invariante nº 2
con una frase en una allowlist —«Córtex (ADR 0074): aislado por `owner_user_id`»—
que nadie comprobaba y que era falsa en el único sentido que importa.

El [ADR 0156](../../docs/05-architecture-decisions/0156-aislamiento-estructural-del-cortex.md)
ratifica el eje (la persona, no el tenant) y obliga a defenderlo con RLS. Este
fichero es la comprobación FUNCIONAL de esa migración, contra PostgreSQL de
verdad; la estática vive en `tests/security/test_pentest_findings.py` y la de
catálogo en el invariante nº 5 de `test_rls_invariant.py`.

## Qué se prueba, y qué NO

Se prueba:

  1. el catálogo — `ENABLE` + `FORCE` + la policy con `USING` y `WITH CHECK`;
  2. que un `app_user` (NOBYPASSRLS) con `app.user_id = A` ve las filas de A y
     **no** las de B, en las cinco tablas;
  3. que sin `app.user_id` ve CERO (fail-closed, gracias al `NULLIF(…, '')`);
  4. que el `WITH CHECK` rechaza escribir a nombre de otro owner;
  5. **que el camino REAL del córtex sigue viéndolo todo** — la comprobación que
     de verdad importa, porque un córtex que se queda a oscuras es peor que la
     desviación que se arregla;
  6. la reversibilidad de la migración, anclada A LA REVISIÓN POR SU NOMBRE.

NO se prueba que `FORCE` cambie el comportamiento hoy: no puede, porque el
propietario de las tablas (`migrations_user`) es BYPASSRLS y BYPASSRLS gana a
`FORCE`. `FORCE` es la postura que empieza a valer el día que el propietario deje
de serlo (la dirección de `04-service-role.sql`), y aquí se comprueba como
bandera del catálogo, que es lo único observable.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


#: Las cinco tablas que protege la 0140, con la columna de texto por la que el
#: test las distingue. `cortex_conversations` NO está: es de la 0125 y tiene su
#: propio fichero (`test_cortex_conversations_rls.py`).
_TABLES: dict[str, str] = {
    "cortex_turns": "content",
    "cortex_identity": "updated_by",
    "cortex_identity_history": "updated_by",
    "cortex_affect_snapshots": "mood_label",
    "cortex_curiosity_pursuits": "topic",
}

#: La revisión ANTERIOR a la 0140, o sea el estado que su `downgrade` debe
#: restaurar. Se ancla por NOMBRE y NUNCA con `-1`: `-1` es relativo a la CABEZA
#: del árbol, así que la primera migración que aterrice encima de ésta dejaría el
#: round-trip apuntando a otra cosa — pasaría en verde sin probar nada, o se
#: pondría rojo culpando a una migración inocente. Es un fallo real de este repo,
#: documentado en `docs/03-guides/gotchas/alembic-round-trip-anclado-por-nombre.md`.
_REVISION_BEFORE = "0139_executions_steps_rollup"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _plain_dsn(sqlalchemy_url: str) -> str:
    """`postgresql+asyncpg://…` → `postgresql://…` (asyncpg.connect crudo)."""
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class Seed:
    """Dos owners, en dos tenants, con una fila suya en cada una de las cinco."""

    def __init__(self) -> None:
        self.owner_a: UUID = uuid4()
        self.owner_b: UUID = uuid4()
        self.tenant_a: UUID = uuid4()
        self.tenant_b: UUID = uuid4()
        self.conv_a: UUID = uuid7()
        self.conv_b: UUID = uuid7()

    def marker(self, owner: str) -> str:
        """El texto que identifica las filas de un owner en cualquiera de las 5."""
        return f"owner-{owner}"


async def _seed(dsn: str) -> Seed:
    """Siembra como `migrations_user` (BYPASSRLS) para poder sembrar a los dos.

    Dos tenants distintos a propósito: así el test puede fijar el `app.tenant_id`
    del otro y demostrar que eso NO abre nada — es decir, que la policy no cuelga
    del eje equivocado.
    """
    s = Seed()
    conn = await asyncpg.connect(dsn)
    try:
        # El GRANT es defensivo: `ALTER DEFAULT PRIVILEGES` ya lo cubre en un
        # arranque limpio, pero si el orden de fixtures cambiase, un
        # `permission denied` enmascararía el 0-filas que queremos observar.
        for table in _TABLES:
            await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app_user")
        await conn.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON cortex_conversations TO app_user"
        )

        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            s.tenant_a,
            "Cortex owner RLS A",
            f"cortex-own-a-{s.tenant_a.hex[:8]}",
            s.tenant_b,
            "Cortex owner RLS B",
            f"cortex-own-b-{s.tenant_b.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            s.owner_a,
            f"a-{s.owner_a.hex[:8]}@cortex-own.test",
            "h",
            s.owner_b,
            f"b-{s.owner_b.hex[:8]}@cortex-own.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO cortex_conversations (id, owner_user_id, tenant_id, title)"
            " VALUES ($1, $2, $3, 'hilo A'), ($4, $5, $6, 'hilo B')",
            s.conv_a,
            s.owner_a,
            s.tenant_a,
            s.conv_b,
            s.owner_b,
            s.tenant_b,
        )

        for owner, conv in ((s.owner_a, s.conv_a), (s.owner_b, s.conv_b)):
            marker = s.marker("a" if owner == s.owner_a else "b")
            await conn.execute(
                "INSERT INTO cortex_turns (id, conversation_id, owner_user_id, role, content)"
                " VALUES ($1, $2, $3, 'user', $4)",
                uuid7(),
                conv,
                owner,
                marker,
            )
            await conn.execute(
                "INSERT INTO cortex_identity (id, owner_user_id, updated_by) VALUES ($1, $2, $3)",
                uuid7(),
                owner,
                marker,
            )
            await conn.execute(
                "INSERT INTO cortex_identity_history"
                " (id, owner_user_id, version, updated_by) VALUES ($1, $2, 1, $3)",
                uuid7(),
                owner,
                marker,
            )
            await conn.execute(
                "INSERT INTO cortex_affect_snapshots"
                " (id, owner_user_id, valence, arousal, dominance, intensity,"
                "  mood_valence, mood_arousal, mood_dominance, mood_label)"
                " VALUES ($1, $2, 0, 0.5, 0, 0.5, 0, 0.5, 0, $3)",
                uuid7(),
                owner,
                marker,
            )
            await conn.execute(
                "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic)"
                " VALUES ($1, $2, $3)",
                uuid7(),
                owner,
                marker,
            )
    finally:
        await conn.close()
    return s


async def _markers_as_app_user(
    dsn: str, table: str, column: str, *, user_id: UUID | None, tenant_id: UUID | None = None
) -> list[str]:
    """Los marcadores que ve `app_user` en `table` con los GUC indicados."""
    conn = await asyncpg.connect(dsn)
    try:
        if user_id is not None:
            await conn.execute("SELECT set_config('app.user_id', $1, false)", str(user_id))
        if tenant_id is not None:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))
        rows = await conn.fetch(
            f"SELECT {column} AS marker FROM {table}"
            f" WHERE {column} LIKE 'owner-%' ORDER BY {column}"
        )
        return [r["marker"] for r in rows]
    finally:
        await conn.close()


async def _catalog(dsn: str) -> dict[str, dict[str, object]]:
    conn = await asyncpg.connect(dsn)
    try:
        flags = {
            r["relname"]: (r["relrowsecurity"], r["relforcerowsecurity"])
            for r in await conn.fetch(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class"
                " WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
            )
        }
        pols: dict[str, list[tuple[str, str | None, str | None]]] = {}
        for r in await conn.fetch(
            "SELECT tablename, policyname, qual, with_check FROM pg_policies"
            " WHERE schemaname = 'public'"
        ):
            pols.setdefault(r["tablename"], []).append(
                (r["policyname"], r["qual"], r["with_check"])
            )
        return {
            t: {
                "rls": flags.get(t, (False, False))[0],
                "force": flags.get(t, (False, False))[1],
                "policies": pols.get(t, []),
            }
            for t in flags
        }
    finally:
        await conn.close()


@pytest.fixture()
def migrated(alembic_config: object, migrations_pg_dsn: str) -> str:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    return migrations_pg_dsn


# ===========================================================================
# 1. Catálogo: ENABLE + FORCE + la policy, con USING y WITH CHECK
# ===========================================================================
def test_the_five_tables_have_enable_force_and_owner_policy(migrated: str) -> None:
    cat = asyncio.run(_catalog(migrated))
    problems: list[str] = []
    for table in _TABLES:
        meta = cat.get(table)
        if meta is None:
            problems.append(f"{table}: no existe en el esquema")
            continue
        if not meta["rls"]:
            problems.append(f"{table}: sin ENABLE ROW LEVEL SECURITY")
        if not meta["force"]:
            problems.append(f"{table}: sin FORCE ROW LEVEL SECURITY")
        policies = {p[0]: (p[1], p[2]) for p in meta["policies"]}  # type: ignore[union-attr]
        name = f"{table}_owner_only"
        if name not in policies:
            problems.append(f"{table}: falta la policy {name} (hay {sorted(policies)})")
            continue
        using, with_check = policies[name]
        if not using or "app.user_id" not in using:
            problems.append(f"{table}: USING no cuelga de app.user_id ({using!r})")
        if not with_check or "app.user_id" not in with_check:
            problems.append(f"{table}: WITH CHECK no cuelga de app.user_id ({with_check!r})")
        # Y el eje NO es el tenant: si alguien "arregla" esto copiando el bloque
        # canónico, la mente privada del owner se vuelve legible por su tenant.
        if "app.tenant_id" in ((using or "") + (with_check or "")):
            problems.append(f"{table}: la policy cuelga de app.tenant_id (ver ADR 0156)")
    assert len(_TABLES) == 5, "el catálogo de tablas de este test se ha encogido"
    assert not problems, f"RLS incompleta en las tablas del córtex: {problems}"


def test_the_conversation_table_keeps_the_rls_the_0125_gave_it(migrated: str) -> None:
    """La 0140 no pisa a la 0125. Control positivo del alcance de esta migración."""
    cat = asyncio.run(_catalog(migrated))
    meta = cat["cortex_conversations"]
    assert meta["rls"] and meta["force"], "la 0140 tocó la RLS de cortex_conversations"
    names = [p[0] for p in meta["policies"]]  # type: ignore[union-attr]
    assert "cortex_conversations_owner_only" in names, names


# ===========================================================================
# 2. Aislamiento funcional bajo app_user (NOBYPASSRLS) — el USING
# ===========================================================================
def test_app_user_sees_only_the_rows_of_the_owner_in_the_guc(
    migrated: str, app_database_url: str
) -> None:
    seed = asyncio.run(_seed(migrated))
    app_dsn = _plain_dsn(app_database_url)

    for table, column in _TABLES.items():
        as_a = asyncio.run(_markers_as_app_user(app_dsn, table, column, user_id=seed.owner_a))
        assert as_a == [seed.marker("a")], f"{table}: el owner A ve {as_a}"
        as_b = asyncio.run(_markers_as_app_user(app_dsn, table, column, user_id=seed.owner_b))
        assert as_b == [seed.marker("b")], f"{table}: el owner B ve {as_b}"


def test_the_tenant_guc_does_not_open_another_owners_mind(
    migrated: str, app_database_url: str
) -> None:
    """La prueba de que el eje es el owner y no el tenant.

    Fijamos `app.user_id = B` y `app.tenant_id = A` (el tenant del OTRO). Con una
    policy por tenant, B leería la identidad, el afecto y las conversaciones
    privadas de A. Con la policy owner-only, no.
    """
    seed = asyncio.run(_seed(migrated))
    app_dsn = _plain_dsn(app_database_url)
    for table, column in _TABLES.items():
        seen = asyncio.run(
            _markers_as_app_user(
                app_dsn, table, column, user_id=seed.owner_b, tenant_id=seed.tenant_a
            )
        )
        assert seen == [seed.marker("b")], f"{table}: con el tenant ajeno, B vio {seen}"


def test_app_user_without_the_user_guc_sees_nothing(migrated: str, app_database_url: str) -> None:
    """Fail-closed: sin `app.user_id`, cero filas (no «todas»).

    Lo garantiza el `NULLIF(…, '')` del predicado: sin GUC la comparación es
    contra NULL, que no es TRUE. Sin él, el cast desde cadena vacía reventaría y
    el modo de fallo sería un 500, no un cero.
    """
    asyncio.run(_seed(migrated))
    app_dsn = _plain_dsn(app_database_url)
    for table, column in _TABLES.items():
        seen = asyncio.run(_markers_as_app_user(app_dsn, table, column, user_id=None))
        assert seen == [], f"{table}: sin app.user_id se vieron {seen}"


# ===========================================================================
# 3. El WITH CHECK: no se puede escribir a nombre de otro owner
# ===========================================================================
def test_app_user_cannot_write_into_another_owners_mind(
    migrated: str, app_database_url: str
) -> None:
    seed = asyncio.run(_seed(migrated))
    app_dsn = _plain_dsn(app_database_url)

    async def _attempt() -> dict[str, str]:
        conn = await asyncpg.connect(app_dsn)
        out: dict[str, str] = {}
        try:
            await conn.execute("SELECT set_config('app.user_id', $1, false)", str(seed.owner_a))
            attempts: tuple[tuple[str, str, tuple[object, ...]], ...] = (
                (
                    "cortex_turns",
                    "INSERT INTO cortex_turns"
                    " (id, conversation_id, owner_user_id, role, content)"
                    " VALUES ($1, $2, $3, 'user', 'robado')",
                    (uuid7(), seed.conv_b, seed.owner_b),
                ),
                # `cortex_affect_snapshots` y no `cortex_identity` a propósito:
                # la identidad tiene un UNIQUE por owner, así que un segundo
                # INSERT podría fallar por el índice y el test se creería la
                # negativa sin que la RLS hubiera intervenido. Aquí el único
                # motivo posible de rechazo es la policy.
                (
                    "cortex_affect_snapshots",
                    "INSERT INTO cortex_affect_snapshots"
                    " (id, owner_user_id, valence, arousal, dominance, intensity,"
                    "  mood_valence, mood_arousal, mood_dominance, mood_label)"
                    " VALUES ($1, $2, 0, 0.5, 0, 0.5, 0, 0.5, 0, 'robado')",
                    (uuid7(), seed.owner_b),
                ),
                (
                    "cortex_curiosity_pursuits",
                    "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic)"
                    " VALUES ($1, $2, 'robado')",
                    (uuid7(), seed.owner_b),
                ),
            )
            for table, sql, args in attempts:
                try:
                    await conn.execute(sql, *args)
                    out[table] = ""
                except asyncpg.exceptions.InsufficientPrivilegeError as exc:
                    out[table] = str(exc)
            return out
        finally:
            await conn.close()

    results = asyncio.run(_attempt())
    assert len(results) == 3, results
    for table, message in results.items():
        assert message, f"{table}: el INSERT a nombre de otro owner NO fue rechazado"
        assert "row-level security policy" in message, f"{table}: {message}"


# ===========================================================================
# 4. La comprobación que de verdad importa: el córtex NO se queda a oscuras
# ===========================================================================
def test_the_real_cortex_path_still_sees_everything(migrated: str) -> None:
    """El camino real del córtex es BYPASSRLS, y BYPASSRLS gana a FORCE.

    Todos los consumidores de estas tablas conectan como `migrations_user` o
    `service_user`: los routers `/owner/cortex/*` y `cortex_voice.py` vía
    `get_admin_sessionmaker()`, y los workers `cortex_affect` /
    `cortex_curiosity` / `cortex_initiative` / `cortex_reflection` /
    `cortex_maintenance` vía `WORKERS_DATABASE_URL`. Ninguno fija `app.user_id`,
    así que si la policy les aplicase verían CERO filas y el córtex perdería su
    identidad, su afecto y su historial **en silencio**.
    """
    seed = asyncio.run(_seed(migrated))

    async def _as_cortex_role() -> tuple[bool, dict[str, list[str]]]:
        conn = await asyncpg.connect(migrated)
        try:
            bypass = await conn.fetchval(
                "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            seen: dict[str, list[str]] = {}
            for table, column in _TABLES.items():
                rows = await conn.fetch(
                    f"SELECT {column} AS marker FROM {table}"
                    " WHERE owner_user_id = ANY($1::uuid[])"
                    f" AND {column} LIKE 'owner-%' ORDER BY {column}",
                    [seed.owner_a, seed.owner_b],
                )
                seen[table] = [r["marker"] for r in rows]
            return bool(bypass), seen
        finally:
            await conn.close()

    bypass, seen = asyncio.run(_as_cortex_role())
    # La PREMISA, explícita para que se pueda romper: si algún día el rol de los
    # servicios deja de ser BYPASSRLS (la dirección de `04-service-role.sql`),
    # estas policies SÍ le aplicarán, y como ningún camino del córtex fija
    # `app.user_id` el owner se quedaría sin mente. Entonces hay que cablear el
    # GUC en `get_admin_sessionmaker`, no relajar las policies (ADR 0156).
    assert bypass, (
        "el rol con el que conecta el córtex ya NO es BYPASSRLS: las policies"
        " owner-only de la 0140 le aplican, y ningún camino del córtex fija"
        " `app.user_id`. Cablea el GUC antes de quitar el BYPASSRLS."
    )
    expected = [seed.marker("a"), seed.marker("b")]
    for table, markers in seen.items():
        assert markers == expected, (
            f"{table}: la sesión BYPASSRLS del córtex dejó de ver sus filas con la"
            f" RLS activa: {markers}"
        )


# ===========================================================================
# 5. Reversibilidad de verdad: head → antes-de-la-0140 → head
# ===========================================================================
def test_migration_round_trip_restores_and_reapplies(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    try:
        command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
        cat = asyncio.run(_catalog(migrations_pg_dsn))
        for table in _TABLES:
            assert cat[table]["rls"] is False, f"el downgrade dejó la RLS puesta en {table}"
            assert cat[table]["force"] is False, f"el downgrade dejó FORCE en {table}"
            huerfanas = [
                p[0]
                for p in cat[table]["policies"]  # type: ignore[union-attr]
                if p[0] == f"{table}_owner_only"
            ]
            assert not huerfanas, f"el downgrade dejó la policy huérfana en {table}"
        # Y NO se lleva por delante lo que no es suyo: la RLS de
        # `cortex_conversations` es de la 0125 y sobrevive a este downgrade.
        assert cat["cortex_conversations"]["rls"] is True, (
            "el downgrade de la 0140 apagó la RLS de cortex_conversations, que es de la 0125"
        )
    finally:
        command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    cat = asyncio.run(_catalog(migrations_pg_dsn))
    for table in _TABLES:
        assert cat[table]["rls"] and cat[table]["force"], f"{table} no recuperó su RLS"
