"""prod-03 task_prod03_07 — la tabla `guardrail_configs`, contra PostgreSQL.

Hasta hoy la config de guardrails vivía en DOS capas sueltas y ninguna tabla:
`platform_settings.guardrails_config` (global, sin RLS) y
`projects.guardrails_config` (migración 0110). Faltaba la capa **tenant** entera
y faltaba el sitio donde `shared_guardrails.layers.resolve_config` —que sabe
fusionar las TRES desde el Plan 11— tuviera las tres que fusionar.

Lo que fija este fichero, contra la base de datos de verdad y no contra el ORM:

  * la forma: scope cerrado, y qué columnas exige cada scope (un `platform` con
    `tenant_id` sería una capa de plataforma que pertenece a un tenant, o sea
    una contradicción que la BD tiene que rechazar, no la aplicación);
  * la unicidad por capa: una fila de plataforma, una por tenant, una por
    proyecto. Dos filas «efectivas» para el mismo ámbito es una config
    ambigua, y una config de seguridad ambigua se resuelve mal;
  * **el aislamiento cross-tenant** (Principio nº1), con la asimetría
    deliberada de esta tabla: la fila de PLATAFORMA se lee desde cualquier
    tenant —es el baseline que todos heredan y no contiene dato de nadie— pero
    NO se escribe desde ningún tenant. Un `USING` que abre y un `WITH CHECK`
    que cierra;
  * la **reversibilidad real** de la migración: el `downgrade` baja de verdad y
    el `upgrade` vuelve a dejar la tabla como estaba, con RLS incluida. Una
    migración cuyo downgrade no se ha ejecutado nunca es una migración
    irreversible que todavía no lo sabe.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_TABLE = "guardrail_configs"


@pytest.fixture()
def migrated(alembic_config: object, migrations_pg_dsn: str) -> str:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    return migrations_pg_dsn


def _app_dsn(migrations_dsn: str) -> str:
    """El mismo DSN pero como `app_user` (NOBYPASSRLS), que es quien sufre RLS."""
    from tests.integration.conftest import PG_APP_PASSWORD, PG_APP_USER

    _, _, tail = migrations_dsn.partition("@")
    return f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}@{tail}"


class _Seed:
    def __init__(self) -> None:
        self.tenant_a: UUID = uuid4()
        self.tenant_b: UUID = uuid4()
        self.project_a: UUID = uuid4()


async def _seed(dsn: str) -> _Seed:
    """Siembra dos tenants + un proyecto + las tres capas, como BYPASSRLS."""
    s = _Seed()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_user")
        await conn.execute(f"DELETE FROM {_TABLE}")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            s.tenant_a,
            "GC A",
            f"gc-a-{s.tenant_a.hex[:8]}",
            s.tenant_b,
            "GC B",
            f"gc-b-{s.tenant_b.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P A', 'active', false)",
            s.project_a,
            s.tenant_a,
        )
        await conn.execute(
            f"INSERT INTO {_TABLE} (id, scope, tenant_id, project_id, config)"
            " VALUES ($1, 'platform', NULL, NULL, '{\"guardrails\": {}}'::jsonb),"
            "        ($2, 'tenant', $3, NULL, '{\"guardrails\": {}}'::jsonb),"
            "        ($4, 'tenant', $5, NULL, '{\"guardrails\": {}}'::jsonb),"
            "        ($6, 'project', $7, $8, '{\"guardrails\": {}}'::jsonb)",
            uuid4(),
            uuid4(),
            s.tenant_a,
            uuid4(),
            s.tenant_b,
            uuid4(),
            s.tenant_a,
            s.project_a,
        )
    finally:
        await conn.close()
    return s


async def _scopes_seen_by(dsn: str, tenant_id: UUID | None) -> list[tuple[str, str | None]]:
    conn = await asyncpg.connect(dsn)
    try:
        if tenant_id is not None:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))
        rows = await conn.fetch(
            f"SELECT scope, tenant_id::text FROM {_TABLE} ORDER BY scope, tenant_id"
        )
        return [(r["scope"], r["tenant_id"]) for r in rows]
    finally:
        await conn.close()


async def _catalog(dsn: str) -> dict[str, object]:
    conn = await asyncpg.connect(dsn)
    try:
        flags = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE relnamespace = 'public'::regnamespace AND relname = $1",
            _TABLE,
        )
        policies = await conn.fetch(
            "SELECT policyname, qual, with_check FROM pg_policies"
            " WHERE schemaname = 'public' AND tablename = $1",
            _TABLE,
        )
        return {
            "rls": bool(flags["relrowsecurity"]) if flags else False,
            "force": bool(flags["relforcerowsecurity"]) if flags else False,
            "policies": [(p["policyname"], p["qual"], p["with_check"]) for p in policies],
        }
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Forma
# ---------------------------------------------------------------------------
def test_the_scope_column_only_accepts_the_three_layers(migrated: str) -> None:
    async def go() -> None:
        conn = await asyncpg.connect(migrated)
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    f"INSERT INTO {_TABLE} (id, scope, config)"
                    " VALUES ($1, 'galaxy', '{}'::jsonb)",
                    uuid4(),
                )
        finally:
            await conn.close()

    asyncio.run(go())


@pytest.mark.parametrize(
    ("scope", "with_tenant", "with_project"),
    [
        # Una capa de plataforma que pertenece a un tenant no es una capa de
        # plataforma; un proyecto sin tenant no existe; un tenant con proyecto
        # es en realidad una capa de proyecto mal etiquetada.
        ("platform", True, False),
        ("tenant", False, False),
        ("tenant", True, True),
        ("project", True, False),
        ("project", False, False),
    ],
)
def test_each_scope_requires_exactly_its_own_columns(
    migrated: str, scope: str, with_tenant: bool, with_project: bool
) -> None:
    async def go() -> None:
        seed = await _seed(migrated)
        conn = await asyncpg.connect(migrated)
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    f"INSERT INTO {_TABLE} (id, scope, tenant_id, project_id, config)"
                    " VALUES ($1, $2, $3, $4, '{}'::jsonb)",
                    uuid4(),
                    scope,
                    seed.tenant_a if with_tenant else None,
                    seed.project_a if with_project else None,
                )
        finally:
            await conn.close()

    asyncio.run(go())


def test_each_layer_has_at_most_one_effective_row(migrated: str) -> None:
    """Dos filas para el mismo ámbito = config ambigua. La BD lo impide."""

    async def go() -> None:
        seed = await _seed(migrated)
        conn = await asyncpg.connect(migrated)
        try:
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    f"INSERT INTO {_TABLE} (id, scope, config)"
                    " VALUES ($1, 'platform', '{}'::jsonb)",
                    uuid4(),
                )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    f"INSERT INTO {_TABLE} (id, scope, tenant_id, config)"
                    " VALUES ($1, 'tenant', $2, '{}'::jsonb)",
                    uuid4(),
                    seed.tenant_a,
                )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    f"INSERT INTO {_TABLE}"
                    " (id, scope, tenant_id, project_id, config)"
                    " VALUES ($1, 'project', $2, $3, '{}'::jsonb)",
                    uuid4(),
                    seed.tenant_a,
                    seed.project_a,
                )
        finally:
            await conn.close()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# RLS
# ---------------------------------------------------------------------------
def test_the_catalog_carries_enable_force_and_a_tenant_policy(migrated: str) -> None:
    cat = asyncio.run(_catalog(migrated))

    assert cat["rls"] is True
    assert cat["force"] is True
    policies = cat["policies"]
    assert policies, "sin policy, ENABLE deja la tabla a cero filas para todos"
    names = {p[0] for p in policies}  # type: ignore[index]
    assert "tenant_isolation" in names
    qual = next(p[1] for p in policies if p[0] == "tenant_isolation")  # type: ignore[index]
    check = next(p[2] for p in policies if p[0] == "tenant_isolation")  # type: ignore[index]
    assert "app.tenant_id" in str(qual)
    # El WITH CHECK NO admite la rama NULL: escribir la capa de plataforma desde
    # una sesión de tenant queda prohibido por la BD, no por buena voluntad.
    assert "app.tenant_id" in str(check)
    assert "IS NULL" not in str(check).upper()


def test_a_tenant_sees_its_own_layer_and_the_platform_one_but_never_a_neighbours(
    migrated: str,
) -> None:
    async def go() -> None:
        seed = await _seed(migrated)
        app_dsn = _app_dsn(migrated)

        seen_a = await _scopes_seen_by(app_dsn, seed.tenant_a)
        assert ("platform", None) in seen_a
        assert ("tenant", str(seed.tenant_a)) in seen_a
        assert ("project", str(seed.tenant_a)) in seen_a
        assert ("tenant", str(seed.tenant_b)) not in seen_a

        seen_b = await _scopes_seen_by(app_dsn, seed.tenant_b)
        assert ("platform", None) in seen_b
        assert ("tenant", str(seed.tenant_b)) in seen_b
        assert ("tenant", str(seed.tenant_a)) not in seen_b
        assert ("project", str(seed.tenant_a)) not in seen_b

    asyncio.run(go())


def test_without_a_tenant_bound_only_the_platform_baseline_is_visible(migrated: str) -> None:
    """Fail-closed: sin `app.tenant_id` no se filtra el dato de ningún tenant.

    El baseline SÍ se ve, y eso es deliberado: no contiene dato de nadie y es lo
    que un arranque sin contexto necesita para no quedarse sin guardrails.
    """

    async def go() -> None:
        await _seed(migrated)
        seen = await _scopes_seen_by(_app_dsn(migrated), None)
        assert seen == [("platform", None)]

    asyncio.run(go())


def test_a_tenant_cannot_write_the_platform_layer(migrated: str) -> None:
    """La lectura del baseline es abierta; su escritura, no."""

    async def go() -> None:
        seed = await _seed(migrated)
        conn = await asyncpg.connect(_app_dsn(migrated))
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(seed.tenant_a))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    f"INSERT INTO {_TABLE} (id, scope, config)"
                    " VALUES ($1, 'platform', '{}'::jsonb)",
                    uuid4(),
                )
        finally:
            await conn.close()

    asyncio.run(go())


def test_a_tenant_cannot_write_a_layer_for_another_tenant(migrated: str) -> None:
    async def go() -> None:
        seed = await _seed(migrated)
        conn = await asyncpg.connect(_app_dsn(migrated))
        try:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(seed.tenant_a))
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    f"INSERT INTO {_TABLE} (id, scope, tenant_id, config)"
                    " VALUES ($1, 'tenant', $2, '{}'::jsonb)",
                    uuid4(),
                    seed.tenant_b,
                )
        finally:
            await conn.close()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Reversibilidad
# ---------------------------------------------------------------------------
# La revisión inmediatamente ANTERIOR a la que crea la tabla. Se ancla por
# NOMBRE y nunca con `downgrade("-1")`: `-1` significa «una revisión por debajo
# de la cabeza actual», no «deshaz 0132». Cuando este fichero se escribió, 0132
# ERA la cabeza y las dos cosas coincidían; hoy la cabeza es 0139, así que `-1`
# se limitaba a deshacer 0139 y dejaba `guardrail_configs` en su sitio — el test
# fallaba sin que la migración tuviera nada malo. Con el nombre, el round-trip
# sigue apuntando a lo que este test quiere probar aunque encima se apilen otras
# veinte migraciones. Mismo razonamiento (y misma solución) que en
# `test_migrations.py::test_fk_cleanup_migration_is_reversible`.
_REVISION_BEFORE = "0131_partition_guardrail_events"


def test_downgrade_drops_the_table_and_upgrade_puts_it_back_with_rls(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    asyncio.run(_seed(migrations_pg_dsn))

    # Bajar hasta 0131 deshace de paso 0133..0139, entre ellas las cuatro que
    # convierten tablas a particionadas (ADR 0151). Es a propósito: el ida y
    # vuelta completo es justo lo que CLAUDE.md exige comprobar antes de
    # desplegar, y un downgrade incompleto de cualquiera de ellas rompe aquí.
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    assert asyncio.run(_table_exists(migrations_pg_dsn)) is False

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert asyncio.run(_table_exists(migrations_pg_dsn)) is True
    cat = asyncio.run(_catalog(migrations_pg_dsn))
    assert cat["rls"] is True and cat["force"] is True
    assert cat["policies"]


async def _table_exists(dsn: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{_TABLE}"))
    finally:
        await conn.close()
