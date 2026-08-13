"""RLS de `cortex_conversations` — eje OWNER, no tenant (migración 0125).

## Por qué este fichero existe

El meta-invariante `test_rls_invariant.py` descubrió que `cortex_conversations`
tenía `tenant_id` y CERO protección: ni `relrowsecurity`, ni `FORCE`, ni una
policy. La migración 0092 lo había declarado así **a propósito** («tenant-less
sobre BYPASSRLS… el aislamiento es un filtro `owner_user_id` explícito en todo
SQL, **sin RLS de respaldo**»), y ese «sin RLS de respaldo» es precisamente lo
que la 0125 añade.

## Por qué la policy NO es `tenant_id = app.tenant_id`

Porque el eje de autorización de esta tabla **no es el tenant**. Su `tenant_id`
es, palabra por palabra del modelo, «the physical discriminator the owner's
memory needs — NOT an authorisation axis»: se resuelve como la membresía activa
más antigua del owner. Una policy por tenant afirmaría que *pertenecer al tenant
A basta para leer el hilo privado del System Owner*, que es más permisivo que lo
que este fichero comprueba, y además dejaría el hilo A OSCURO en cuanto un
`system_admin` con contexto de tenant B abriese el córtex (`open_tenant_session`
fija `app.tenant_id` al tenant elegido, no al de la conversación).

La policy correcta es la del patrón `session_owner_only` (migración 0001):
`owner_user_id = app.user_id`. Es estrictamente más restrictiva que la de tenant
(el admin del tenant A tampoco ve el hilo del owner) y no puede dejar al owner
sin historial, porque `open_tenant_session` fija `app.user_id` SIEMPRE, en las
dos variantes de sesión.

## Lo que este fichero prueba, y lo que NO

Prueba, funcionalmente y contra PostgreSQL de verdad:

  1. el catálogo (ENABLE + FORCE + la policy con `USING` y `WITH CHECK`);
  2. que un `app_user` (NOBYPASSRLS) con `app.user_id = A` ve los hilos de A y
     **no** los de B — ni con el `app.tenant_id` de B fijado;
  3. que sin `app.user_id` ve CERO (fail-closed);
  4. que el `WITH CHECK` rechaza escribir un hilo a nombre de otro owner;
  5. **que el camino REAL del córtex sigue viendo todo su historial** — la
     comprobación que importa, porque un chat que se queda a oscuras es peor que
     la desviación que se arregla. El router (`get_admin_sessionmaker`) y los
     cuatro workers del córtex conectan como `migrations_user`, que es BYPASSRLS,
     y BYPASSRLS se salta la RLS incluso con `FORCE` (medido, no supuesto: ver
     :func:`test_the_real_cortex_path_still_sees_its_whole_history`).

NO prueba que `FORCE` cambie el comportamiento hoy: no puede, porque el
propietario de la tabla (`migrations_user`) es BYPASSRLS y BYPASSRLS gana a
`FORCE`. `FORCE` es la postura que empieza a valer el día que el propietario
deje de ser BYPASSRLS (la dirección de `04-service-role.sql`), y aquí se
comprueba como bandera del catálogo, que es lo único observable.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


# Las tres tablas que tenían RLS + policy pero les faltaba `FORCE`, y la propia
# `cortex_conversations`. La 0125 se las pone a las cuatro.
_FORCE_EXPECTED = (
    "cortex_conversations",
    "review_sessions",
    "task_audit_events",
    "tenant_settings",
)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _plain_dsn(sqlalchemy_url: str) -> str:
    """`postgresql+asyncpg://…` → `postgresql://…` (asyncpg.connect crudo)."""
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class Seed:
    """Los ids sembrados: dos owners con un hilo cada uno, en dos tenants."""

    def __init__(self) -> None:
        self.owner_a: UUID = uuid4()
        self.owner_b: UUID = uuid4()
        self.tenant_a: UUID = uuid4()
        self.tenant_b: UUID = uuid4()
        self.conv_a: UUID = uuid7()
        self.conv_b: UUID = uuid7()


async def _seed(dsn: str) -> Seed:
    """Siembra dos owners/hilos como `migrations_user` (BYPASSRLS).

    Dos tenants distintos a propósito: así el test puede fijar el
    `app.tenant_id` de B y demostrar que eso NO abre el hilo de A — es decir,
    que la policy no cuelga del eje equivocado.
    """
    s = Seed()
    conn = await asyncpg.connect(dsn)
    try:
        # El GRANT es defensivo: `ALTER DEFAULT PRIVILEGES` ya lo cubre en un
        # arranque limpio, pero si el orden de fixtures cambiase, un
        # `permission denied` enmascararía el 0-filas que queremos observar.
        await conn.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON cortex_conversations TO app_user"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            s.tenant_a,
            "Cortex RLS A",
            f"cortex-rls-a-{s.tenant_a.hex[:8]}",
            s.tenant_b,
            "Cortex RLS B",
            f"cortex-rls-b-{s.tenant_b.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            s.owner_a,
            f"a-{s.owner_a.hex[:8]}@cortex-rls.test",
            "h",
            s.owner_b,
            f"b-{s.owner_b.hex[:8]}@cortex-rls.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO cortex_conversations (id, owner_user_id, tenant_id, title)"
            " VALUES ($1, $2, $3, 'hilo del owner A'), ($4, $5, $6, 'hilo del owner B')",
            s.conv_a,
            s.owner_a,
            s.tenant_a,
            s.conv_b,
            s.owner_b,
            s.tenant_b,
        )
    finally:
        await conn.close()
    return s


async def _titles_as_app_user(
    dsn: str, *, user_id: UUID | None, tenant_id: UUID | None = None
) -> list[str]:
    """Títulos que ve `app_user` con los GUC indicados (None = sin fijar)."""
    conn = await asyncpg.connect(dsn)
    try:
        if user_id is not None:
            await conn.execute("SELECT set_config('app.user_id', $1, false)", str(user_id))
        if tenant_id is not None:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))
        rows = await conn.fetch("SELECT title FROM cortex_conversations ORDER BY title")
        return [r["title"] for r in rows]
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
def test_cortex_conversations_has_enable_force_and_owner_policy(migrated: str) -> None:
    cat = asyncio.run(_catalog(migrated))
    meta = cat["cortex_conversations"]
    assert meta["rls"] is True, "cortex_conversations sin ENABLE ROW LEVEL SECURITY"
    assert meta["force"] is True, "cortex_conversations sin FORCE ROW LEVEL SECURITY"

    policies = {p[0]: (p[1], p[2]) for p in meta["policies"]}  # type: ignore[union-attr]
    assert "cortex_conversations_owner_only" in policies, (
        f"falta la policy owner-only; hay {sorted(policies)}"
    )
    using, with_check = policies["cortex_conversations_owner_only"]
    assert using and "app.user_id" in using, f"USING no cuelga de app.user_id: {using!r}"
    assert with_check and "app.user_id" in with_check, (
        f"WITH CHECK no cuelga de app.user_id: {with_check!r}"
    )
    # Y el eje NO es el tenant: si alguien "arregla" esto cambiando el
    # predicado, el hilo del owner se volvería legible para el tenant entero.
    assert "app.tenant_id" not in (using or ""), (
        "la policy cuelga de app.tenant_id: eso haría el hilo privado del owner"
        " legible por cualquier sesión de su tenant (ver docstring del fichero)"
    )


def test_the_three_tables_that_lacked_force_now_have_it(migrated: str) -> None:
    """Postura, no comportamiento: ver el docstring del módulo.

    `review_sessions`, `task_audit_events` y `tenant_settings` ya tenían ENABLE
    + policy desde las migraciones 0024/0025/0023; les faltaba `FORCE`, que es
    lo que impide que el PROPIETARIO de la tabla se salte sus propias policies.
    """
    cat = asyncio.run(_catalog(migrated))
    without = [t for t in _FORCE_EXPECTED if not cat[t]["force"]]
    assert not without, f"siguen sin FORCE ROW LEVEL SECURITY: {without}"
    # Y no perdieron su policy por tenant en el camino.
    for table in ("review_sessions", "task_audit_events", "tenant_settings"):
        guc = [
            p[0]
            for p in cat[table]["policies"]  # type: ignore[union-attr]
            if "app.tenant_id" in ((p[1] or "") + (p[2] or ""))
        ]
        assert guc, f"{table} perdió su policy por app.tenant_id"


# ===========================================================================
# 2. Aislamiento funcional bajo app_user (NOBYPASSRLS) — el USING
# ===========================================================================
def test_app_user_sees_only_the_threads_of_the_owner_in_the_guc(
    migrated: str, app_database_url: str
) -> None:
    seed = asyncio.run(_seed(migrated))
    app_dsn = _plain_dsn(app_database_url)

    as_a = asyncio.run(_titles_as_app_user(app_dsn, user_id=seed.owner_a))
    assert as_a == ["hilo del owner A"], f"el owner A ve {as_a}"

    as_b = asyncio.run(_titles_as_app_user(app_dsn, user_id=seed.owner_b))
    assert as_b == ["hilo del owner B"], f"el owner B ve {as_b}"


def test_the_tenant_guc_does_not_open_another_owners_thread(
    migrated: str, app_database_url: str
) -> None:
    """La prueba de que el eje es el owner y no el tenant.

    Fijamos `app.user_id = B` y `app.tenant_id = A` (el tenant DEL HILO DE A).
    Con una policy por tenant, B leería el hilo privado de A. Con la policy
    owner-only, no.
    """
    seed = asyncio.run(_seed(migrated))
    app_dsn = _plain_dsn(app_database_url)
    seen = asyncio.run(_titles_as_app_user(app_dsn, user_id=seed.owner_b, tenant_id=seed.tenant_a))
    assert seen == ["hilo del owner B"], (
        f"con el app.tenant_id del hilo ajeno, el owner B vio: {seen}"
    )


def test_app_user_without_the_user_guc_sees_nothing(migrated: str, app_database_url: str) -> None:
    """Fail-closed: sin `app.user_id`, cero filas (no «todas»)."""
    asyncio.run(_seed(migrated))
    app_dsn = _plain_dsn(app_database_url)
    assert asyncio.run(_titles_as_app_user(app_dsn, user_id=None)) == []


# ===========================================================================
# 3. El WITH CHECK: no se puede escribir a nombre de otro owner
# ===========================================================================
def test_app_user_cannot_insert_a_thread_for_another_owner(
    migrated: str, app_database_url: str
) -> None:
    seed = asyncio.run(_seed(migrated))
    app_dsn = _plain_dsn(app_database_url)

    async def _attempt() -> str:
        conn = await asyncpg.connect(app_dsn)
        try:
            await conn.execute("SELECT set_config('app.user_id', $1, false)", str(seed.owner_a))
            try:
                await conn.execute(
                    "INSERT INTO cortex_conversations (id, owner_user_id, tenant_id, title)"
                    " VALUES ($1, $2, $3, 'hilo robado')",
                    uuid7(),
                    seed.owner_b,
                    seed.tenant_b,
                )
            except asyncpg.exceptions.InsufficientPrivilegeError as exc:
                return str(exc)
            return ""
        finally:
            await conn.close()

    message = asyncio.run(_attempt())
    assert message, "el INSERT a nombre de otro owner NO fue rechazado"
    assert "row-level security policy" in message, message


# ===========================================================================
# 4. La comprobación que de verdad importa: el córtex NO se queda a oscuras
# ===========================================================================
def test_the_real_cortex_path_still_sees_its_whole_history(migrated: str) -> None:
    """El camino real del córtex es BYPASSRLS, y BYPASSRLS gana a FORCE.

    Todos los consumidores de `cortex_conversations` conectan como
    `migrations_user` (BYPASSRLS): el router `/owner/cortex/*` y
    `cortex_voice.py` vía `get_admin_sessionmaker()`, y los workers
    `cortex_initiative` / `cortex_affect` / `cortex_curiosity` /
    `cortex_reflection` vía `WORKERS_DATABASE_URL`. Ninguno fija
    `app.user_id`, así que si `FORCE` les aplicase verían CERO hilos y el chat
    del owner perdería su historial en silencio. Este test lo mide.
    """
    seed = asyncio.run(_seed(migrated))

    async def _as_cortex_role() -> tuple[bool, list[str]]:
        conn = await asyncpg.connect(migrated)
        try:
            bypass = await conn.fetchval(
                "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            rows = await conn.fetch(
                "SELECT title FROM cortex_conversations"
                " WHERE owner_user_id = ANY($1::uuid[]) ORDER BY title",
                [seed.owner_a, seed.owner_b],
            )
            return bool(bypass), [r["title"] for r in rows]
        finally:
            await conn.close()

    bypass, seen = asyncio.run(_as_cortex_role())
    # La PREMISA, explícita para que se pueda romper: si algún día el rol de los
    # servicios deja de ser BYPASSRLS (la dirección de `04-service-role.sql`),
    # esta policy SÍ le aplicará, y como ningún camino del córtex fija
    # `app.user_id` el owner perdería su historial en silencio. Entonces hay que
    # cablear el GUC en `get_admin_sessionmaker`, no relajar la policy.
    assert bypass, (
        "el rol con el que conecta el córtex ya NO es BYPASSRLS: la policy"
        " owner-only de la 0125 le aplica, y ningún camino del córtex fija"
        " `app.user_id`. Cablea el GUC antes de quitar el BYPASSRLS."
    )
    assert seen == [
        "hilo del owner A",
        "hilo del owner B",
    ], f"la sesión BYPASSRLS del córtex dejó de ver su historial con la RLS activa: {seen}"


# ===========================================================================
# 5. Reversibilidad de verdad: head → antes-de-la-0125 → head
# ===========================================================================

#: La revisión ANTERIOR a la 0125, o sea el estado que su `downgrade` debe restaurar.
#: Se nombra explícitamente y NO se usa `-1`: con `-1` el test solo funcionaba
#: mientras la 0125 fuera la cabeza, y la primera migración que aterrizó encima
#: (`0126_perf_indexes_uniqueness`) lo puso rojo sin que nada estuviera roto —
#: bajaba un paso, se quedaba en la 0125 y encontraba su RLS todavía puesta, que es
#: exactamente lo correcto. Anclado a la revisión, el test sigue midiendo lo suyo por
#: muchas migraciones que se apilen después.
_REVISION_BEFORE = "0124_junction_tenant_rls"


def test_migration_round_trip_restores_and_reapplies(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    try:
        command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
        cat = asyncio.run(_catalog(migrations_pg_dsn))
        assert cat["cortex_conversations"]["rls"] is False, "el downgrade dejó la RLS puesta"
        assert not [
            p
            for p in cat["cortex_conversations"]["policies"]  # type: ignore[union-attr]
            if p[0] == "cortex_conversations_owner_only"
        ], "el downgrade dejó la policy huérfana"
        for table in ("review_sessions", "task_audit_events", "tenant_settings"):
            assert cat[table]["force"] is False, f"el downgrade dejó FORCE en {table}"
            assert cat[table]["rls"] is True, (
                f"el downgrade apagó la RLS de {table}, que NO era suya"
            )
    finally:
        command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    cat = asyncio.run(_catalog(migrations_pg_dsn))
    assert cat["cortex_conversations"]["rls"] and cat["cortex_conversations"]["force"]
    assert all(cat[t]["force"] for t in _FORCE_EXPECTED)
