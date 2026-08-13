"""Migración 0128 (ADR 0142) contra PostgreSQL real — `task_mkt2_01`.

Cuatro cosas, y ninguna es «la tabla existe»:

1. **RLS completa** en las dos tablas nuevas: `ENABLE` + `FORCE` + policies que
   citan `app.tenant_id`. Es lo que `tests/integration/test_rls_invariant.py`
   exige de cualquier tabla con `tenant_id`, así que aquí se comprueba
   localmente y allí de forma global.
2. **El candado de la idempotencia funciona en la BD**: dos despliegues
   `active` del mismo par son rechazados por el índice parcial; retirar el
   primero libera el hueco. Sin este test el UNIQUE parcial es una intención.
3. **Aislamiento cross-tenant real**: un despliegue del tenant A no se ve desde
   una sesión del tenant B (probado como `app_user`, que NO tiene BYPASSRLS —
   probarlo como `migrations_user` no probaría nada).
4. **El backfill sobre datos sembrados**: se siembra un listing con instalación
   ANTES de la 0128, se aplica, y se comprueba que nació su fila de versión y
   que la instalación quedó pinada a ELLA (incluido el caso feo: una instalación
   cuya versión NO es la vigente del listing, que es donde un backfill perezoso
   pinaría la fila equivocada).

El round-trip está anclado a la revisión **por nombre**
(`_REVISION_BEFORE`), nunca `downgrade("-1")`: con varios agentes añadiendo
migraciones, `-1` apunta a lo que sea que haya debajo en ese momento (la trampa
que costó las 0125/0126).
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

#: La revisión ANTERIOR a la 0128: el estado que su `downgrade` debe restaurar.
_REVISION_BEFORE = "0127_user_invitations"

_NEW_TABLES = ("marketplace_listing_versions", "marketplace_deployments")


# ---------------------------------------------------------------------------
# Introspección
# ---------------------------------------------------------------------------
async def _rls_shape(dsn: str, table: str) -> dict[str, object]:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
            " WHERE relnamespace = 'public'::regnamespace AND relname = $1",
            table,
        )
        policies = await conn.fetch(
            "SELECT policyname, coalesce(qual,'') || ' ' || coalesce(with_check,'') AS expr"
            " FROM pg_policies WHERE schemaname = 'public' AND tablename = $1",
            table,
        )
        return {
            "exists": row is not None,
            "rls": bool(row["relrowsecurity"]) if row else False,
            "force": bool(row["relforcerowsecurity"]) if row else False,
            "policies": {p["policyname"]: p["expr"] for p in policies},
        }
    finally:
        await conn.close()


async def _tables_present(dsn: str) -> set[str]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = ANY($1::text[])",
            list(_NEW_TABLES),
        )
        return {r["table_name"] for r in rows}
    finally:
        await conn.close()


async def _column_present(dsn: str, table: str, column: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return bool(
            await conn.fetchval(
                "SELECT 1 FROM information_schema.columns"
                " WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2",
                table,
                column,
            )
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Siembra
# ---------------------------------------------------------------------------
async def _seed_pre_migration(dsn: str) -> dict[str, UUID]:
    """Datos ANTES de la 0128: dos tenants, dos listings, dos instalaciones.

    La instalación `stale` pina a propósito una versión (`0.9.0`) distinta de la
    vigente del listing (`2.0.0`): es el caso en el que un backfill perezoso
    pinaría la fila equivocada y nadie se enteraría.
    """
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "user_a": uuid4(),
        "source": uuid4(),
        "listing_global": uuid4(),
        "listing_private_b": uuid4(),
        "install_current": uuid4(),
        "install_stale": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_audit_entries, marketplace_installations,"
            " marketplace_listings, marketplace_sources, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3),($4,$5,$6)",
            ids["tenant_a"],
            "Tenant A v2",
            "tenant-a-mkt2",
            ids["tenant_b"],
            "Tenant B v2",
            "tenant-b-mkt2",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,$3)",
            ids["user_a"],
            "mkt2-a@test.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type, is_trusted)"
            " VALUES ($1,'oficial-mkt2','official',true)",
            ids["source"],
        )
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level, manifest,"
            "  requested_permissions)"
            " VALUES ($1,$2,NULL,'tool','mkt2-global','2.0.0','verified',$3::jsonb,$4::jsonb)",
            ids["listing_global"],
            ids["source"],
            json.dumps(
                {
                    "implementation_type": "http_endpoint",
                    "implementation_ref": "https://x.test/api",
                    "config_schema": {"type": "object", "properties": {"base_url": {}}},
                }
            ),
            json.dumps([{"type": "network_policy", "value": "restricted"}]),
        )
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level, manifest,"
            "  requested_permissions)"
            " VALUES ($1,$2,$3,'skill','mkt2-privado','1.0.0','community',$4::jsonb,'[]'::jsonb)",
            ids["listing_private_b"],
            ids["source"],
            ids["tenant_b"],
            json.dumps({"prompt_fragment": "usa siempre pytest"}),
        )
        # Instalación al día: su versión ES la vigente del listing.
        await conn.execute(
            "INSERT INTO marketplace_installations"
            " (id, tenant_id, listing_id, version, status, installed_by)"
            " VALUES ($1,$2,$3,'2.0.0','enabled',$4)",
            ids["install_current"],
            ids["tenant_a"],
            ids["listing_global"],
            ids["user_a"],
        )
        # Instalación rezagada: pinó 0.9.0 y el listing ya va por 2.0.0.
        await conn.execute(
            "INSERT INTO marketplace_installations"
            " (id, tenant_id, listing_id, version, status, installed_by)"
            " VALUES ($1,$2,$3,'0.9.0','enabled',$4)",
            ids["install_stale"],
            ids["tenant_b"],
            ids["listing_private_b"],
            ids["user_a"],
        )
    finally:
        await conn.close()
    return ids


async def _seed_project(dsn: str, tenant_id: UUID, slug: str) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug) VALUES ($1,$2,$3,$4)",
            project_id,
            tenant_id,
            f"Proyecto {slug}",
            slug,
        )
    finally:
        await conn.close()
    return project_id


# ---------------------------------------------------------------------------
# 1. RLS completa en las dos tablas nuevas
# ---------------------------------------------------------------------------
@pytest.fixture()
def at_head(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


def test_deployments_table_has_complete_rls(at_head: None, migrations_pg_dsn: str) -> None:
    shape = asyncio.run(_rls_shape(migrations_pg_dsn, "marketplace_deployments"))
    assert shape["exists"], "la 0128 no creó marketplace_deployments"
    assert shape["rls"] is True, "sin ENABLE ROW LEVEL SECURITY"
    assert shape["force"] is True, "sin FORCE: el propietario de la tabla la esquivaría"
    policies = shape["policies"]
    assert isinstance(policies, dict)
    assert "marketplace_deployments_tenant_isolation" in policies
    assert "app.tenant_id" in policies["marketplace_deployments_tenant_isolation"]


def test_listing_versions_table_has_the_hybrid_policy_set(
    at_head: None, migrations_pg_dsn: str
) -> None:
    """Tres policies, como `marketplace_listings`: propia, global y compartida.

    La tercera no es adorno: sin ella un tenant con un listing privado
    COMPARTIDO podría instalarlo y luego no ver la versión que pinó.
    """
    shape = asyncio.run(_rls_shape(migrations_pg_dsn, "marketplace_listing_versions"))
    assert shape["exists"]
    assert shape["rls"] is True and shape["force"] is True
    policies = shape["policies"]
    assert isinstance(policies, dict)
    assert set(policies) == {
        "marketplace_listing_versions_tenant_isolation",
        "marketplace_listing_versions_global_read",
        "marketplace_listing_versions_shared_read",
    }, sorted(policies)
    assert "app.tenant_id" in policies["marketplace_listing_versions_tenant_isolation"]
    assert "marketplace_shares" in policies["marketplace_listing_versions_shared_read"]


def test_deployments_tenant_fk_exists_in_the_db(at_head: None, migrations_pg_dsn: str) -> None:
    """La FK que el ORM NO declara (la pone la migración) existe de verdad."""

    async def _probe() -> str | None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return await conn.fetchval(
                "SELECT confdeltype::text FROM pg_constraint"
                " WHERE conrelid = 'marketplace_deployments'::regclass"
                "   AND confrelid = 'organizations'::regclass AND contype = 'f'"
            )
        finally:
            await conn.close()

    # 'c' = ON DELETE CASCADE.
    assert asyncio.run(_probe()) == "c"


# ---------------------------------------------------------------------------
# 2. El candado de la idempotencia
# ---------------------------------------------------------------------------
def test_second_active_deployment_of_the_same_pair_is_rejected(
    at_head: None, migrations_pg_dsn: str
) -> None:
    async def _probe() -> None:
        ids = await _seed_pre_migration(migrations_pg_dsn)
        project = await _seed_project(migrations_pg_dsn, ids["tenant_a"], "mkt2-idem")
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            insert = (
                "INSERT INTO marketplace_deployments"
                " (id, tenant_id, installation_id, project_id, deployed_version, status)"
                " VALUES ($1,$2,$3,$4,'2.0.0',$5)"
            )
            first = uuid4()
            await conn.execute(
                insert, first, ids["tenant_a"], ids["install_current"], project, "active"
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    insert, uuid4(), ids["tenant_a"], ids["install_current"], project, "active"
                )
            # Retirar libera el hueco: el histórico acumula, el candado no estorba.
            await conn.execute(
                "UPDATE marketplace_deployments SET status='retired', retired_at=now()"
                " WHERE id = $1",
                first,
            )
            await conn.execute(
                insert, uuid4(), ids["tenant_a"], ids["install_current"], project, "active"
            )
            live = await conn.fetchval(
                "SELECT count(*) FROM marketplace_deployments WHERE status = 'active'"
            )
            total = await conn.fetchval("SELECT count(*) FROM marketplace_deployments")
            assert live == 1 and total == 2, (live, total)
        finally:
            await conn.close()

    asyncio.run(_probe())


def test_status_check_rejects_an_invented_state(at_head: None, migrations_pg_dsn: str) -> None:
    async def _probe() -> None:
        ids = await _seed_pre_migration(migrations_pg_dsn)
        project = await _seed_project(migrations_pg_dsn, ids["tenant_a"], "mkt2-check")
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "INSERT INTO marketplace_deployments"
                    " (id, tenant_id, installation_id, project_id, deployed_version, status)"
                    " VALUES ($1,$2,$3,$4,'2.0.0','desplegadisimo')",
                    uuid4(),
                    ids["tenant_a"],
                    ids["install_current"],
                    project,
                )
        finally:
            await conn.close()

    asyncio.run(_probe())


# ---------------------------------------------------------------------------
# 3. Aislamiento cross-tenant real (como app_user, NOBYPASSRLS)
# ---------------------------------------------------------------------------
def test_deployment_of_tenant_a_is_invisible_to_tenant_b(
    at_head: None, migrations_pg_dsn: str, admin_pg_dsn: str
) -> None:
    from tests.integration.conftest import (
        PG_APP_PASSWORD,
        PG_APP_USER,
        PG_HOST,
        PG_PORT,
        PG_TEST_DB,
    )

    app_dsn = f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"

    async def _probe() -> None:
        ids = await _seed_pre_migration(migrations_pg_dsn)
        project = await _seed_project(migrations_pg_dsn, ids["tenant_a"], "mkt2-rls")
        # El GRANT no lo dan las default privileges de una tabla creada por una
        # migración posterior al ALTER DEFAULT PRIVILEGES; se retro-concede.
        admin = await asyncpg.connect(admin_pg_dsn)
        try:
            await admin.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user"
            )
        finally:
            await admin.close()

        seeder = await asyncpg.connect(migrations_pg_dsn)
        try:
            await seeder.execute(
                "INSERT INTO marketplace_deployments"
                " (id, tenant_id, installation_id, project_id, deployed_version)"
                " VALUES ($1,$2,$3,$4,'2.0.0')",
                uuid4(),
                ids["tenant_a"],
                ids["install_current"],
                project,
            )
        finally:
            await seeder.close()

        app = await asyncpg.connect(app_dsn)
        try:
            await app.execute("SELECT set_config('app.tenant_id', $1, false)", str(ids["tenant_a"]))
            mine = await app.fetchval("SELECT count(*) FROM marketplace_deployments")
            await app.execute("SELECT set_config('app.tenant_id', $1, false)", str(ids["tenant_b"]))
            theirs = await app.fetchval("SELECT count(*) FROM marketplace_deployments")
        finally:
            await app.close()
        assert mine == 1, "el dueño no ve su propio despliegue: la policy es demasiado estricta"
        assert theirs == 0, "FUGA CROSS-TENANT: el tenant B ve el despliegue del A"

    asyncio.run(_probe())


# ---------------------------------------------------------------------------
# 4. El backfill sobre datos sembrados
# ---------------------------------------------------------------------------
def test_backfill_creates_a_version_row_per_listing_and_pins_every_install(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """Se siembra ANTES de la 0128 y se comprueba lo que la 0128 dedujo.

    El nodo que muerde: `install_stale` está en 0.9.0 mientras su listing va por
    1.0.0. El pin tiene que apuntar a la fila de **0.9.0** — pinar la vigente
    sería afirmar que el tenant consintió permisos que nunca vio.
    """
    # Subir primero (la BD puede llegar en cualquier revisión) y BAJAR después:
    # `command.upgrade` hacia una revisión anterior es un no-op silencioso, y
    # con él este test se creería «pre-migración» estando en head.
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    ids = asyncio.run(_seed_pre_migration(migrations_pg_dsn))
    assert not asyncio.run(_tables_present(migrations_pg_dsn)), (
        "las tablas de la 0128 existen ANTES de aplicarla: el downgrade de una"
        " ejecución previa no limpió, y este test estaría comprobando otra cosa"
    )

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    async def _check() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            rows = await conn.fetch(
                "SELECT id, listing_id, tenant_id, version,"
                "       config_schema IS NOT NULL AS has_schema"
                "  FROM marketplace_listing_versions ORDER BY version"
            )
            by_key = {(r["listing_id"], r["version"]): r for r in rows}
            # Una fila por listing (2.0.0 del global, 1.0.0 del privado)…
            assert (ids["listing_global"], "2.0.0") in by_key
            assert (ids["listing_private_b"], "1.0.0") in by_key
            # …y ADEMÁS la de la instalación rezagada.
            assert (ids["listing_private_b"], "0.9.0") in by_key, (
                "el backfill no creó la versión de la instalación rezagada: su pin"
                " caería a la vigente y mentiría sobre qué se consintió"
            )
            # La tenencia espeja al listing (NULL = catálogo global).
            assert by_key[(ids["listing_global"], "2.0.0")]["tenant_id"] is None
            assert by_key[(ids["listing_private_b"], "1.0.0")]["tenant_id"] == ids["tenant_b"]
            # El `config_schema` se extrae del manifest cuando está y solo cuando está.
            assert by_key[(ids["listing_global"], "2.0.0")]["has_schema"] is True
            assert by_key[(ids["listing_private_b"], "1.0.0")]["has_schema"] is False

            pins = {
                r["id"]: (r["pinned_version_id"], r["version"])
                for r in await conn.fetch(
                    "SELECT id, pinned_version_id, version FROM marketplace_installations"
                )
            }
            assert all(p[0] is not None for p in pins.values()), (
                f"el backfill dejó pines a NULL: {pins}"
            )
            assert pins[ids["install_current"]][0] == by_key[(ids["listing_global"], "2.0.0")]["id"]
            assert (
                pins[ids["install_stale"]][0] == by_key[(ids["listing_private_b"], "0.9.0")]["id"]
            ), "la instalación rezagada quedó pinada a la versión equivocada"
        finally:
            await conn.close()

    asyncio.run(_check())


def test_backfill_is_idempotent_across_a_downgrade_upgrade_roundtrip(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """head → 0127 → head, anclado POR NOMBRE (nunca `downgrade("-1")`).

    Comprueba que el downgrade retira de verdad las dos tablas Y la columna del
    pin — un downgrade que dropea las tablas y deja `pinned_version_id` colgando
    dejaría una FK huérfana y el `upgrade` siguiente reventaría con
    "column already exists".
    """
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    asyncio.run(_seed_pre_migration(migrations_pg_dsn))

    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    assert asyncio.run(_tables_present(migrations_pg_dsn)) == set(), "el downgrade no limpió"
    assert not asyncio.run(
        _column_present(migrations_pg_dsn, "marketplace_installations", "pinned_version_id")
    ), "el downgrade dejó la columna del pin colgando"

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert asyncio.run(_tables_present(migrations_pg_dsn)) == set(_NEW_TABLES)
    assert asyncio.run(
        _column_present(migrations_pg_dsn, "marketplace_installations", "pinned_version_id")
    )

    async def _pins() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return int(
                await conn.fetchval(
                    "SELECT count(*) FROM marketplace_installations WHERE pinned_version_id IS NULL"
                )
            )
        finally:
            await conn.close()

    assert asyncio.run(_pins()) == 0, "tras el round-trip quedaron instalaciones sin pin"
