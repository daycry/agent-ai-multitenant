"""Córtex F3 (bloque 1) — cortex_identity (singleton) + cortex_identity_history.

Ejercita (a) la migración 0094: ``alembic upgrade head`` crea ``cortex_identity``
+ ``cortex_identity_history`` + sus índices; desde la migración ``0140`` (ADR 0156)
las dos llevan RLS de eje OWNER, el UNIQUE singleton ``uq_cortex_identity_owner`` rechaza un
segundo identity del MISMO owner, y ``alembic downgrade 0093_cortex_affect`` las
elimina (reversible); (b) la capa de persistencia owner-scoped:
``ensure_identity`` crea una default idempotente (singleton), ``update_identity``
versiona en history (v1, v2…) con diff+reason y ``get_identity`` devuelve el
último estado; (c) **cross-owner**: un owner ajeno NUNCA ve/edita la identidad de
otro (filtro ``owner_user_id`` explícito **y**, desde la migración ``0140`` /
ADR 0156, RLS de eje owner por debajo).

Patrón de fixtures + admin sessionmaker BYPASSRLS tomado de
``test_cortex_affect_store.py``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


def _admin_sessionmaker(admin_database_url: str):
    import api_server.db.session as session_mod
    from api_server.config import get_settings

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    return session_mod.get_admin_sessionmaker()


async def _seed_two_owners(dsn: str) -> dict[str, UUID]:
    owner_id = uuid4()
    other_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_identity_history, cortex_identity, cortex_affect_snapshots,"
            " cortex_turns, cortex_conversations, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Tenant",
            "cortex-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            owner_id,
            "owner@cortex.test",
            "h",
            other_id,
            "other@cortex.test",
            "h",
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "other_id": other_id, "tenant_id": tenant_id}


# ---------------------------------------------------------------------------
# Migración 0094: tablas + índices + UNIQUE singleton + reversible
# (+ RLS de eje owner desde la 0140 — ADR 0156)
# ---------------------------------------------------------------------------
async def _table_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_name = $1)",
            name,
        )
    )


async def _index_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public'"
            " AND indexname = $1)",
            name,
        )
    )


@pytest.mark.asyncio
async def test_identity_migration_indexes_rls_singleton_and_reversible(
    configured_app, migrations_pg_dsn: str, alembic_config
) -> None:
    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await _table_exists(conn, "cortex_identity")
        assert await _table_exists(conn, "cortex_identity_history")
        assert await _index_exists(conn, "uq_cortex_identity_owner")
        assert await _index_exists(conn, "ix_cortex_identity_history_owner_version")
        assert await _index_exists(conn, "uq_cortex_identity_history_owner_version")

        # RLS de eje OWNER (ADR 0156, migración 0140). Esta aserción decía lo
        # CONTRARIO hasta el 2026-08-19 —«ninguna tabla debe llevar RLS
        # activada»— y no era un descuido: venía del ADR 0074, que leyó
        # «tenant-less» como «sin eje que defender». El ADR 0156 corrige la
        # inferencia: que el eje no sea el tenant no exime de RLS, obliga a
        # defender el eje que sí hay. Aquí vive la identidad del System Owner,
        # y `app_user` (NOBYPASSRLS) tiene DML sobre estas tablas por los
        # default privileges, así que lo único que las separaba de una sesión
        # de tenant era que cada query recordase su filtro.
        for tname in ("cortex_identity", "cortex_identity_history"):
            relrowsecurity = await conn.fetchval(
                "SELECT relrowsecurity FROM pg_class WHERE relname = $1", tname
            )
            assert relrowsecurity is True, (
                f"{tname} se quedó sin RLS: la migración 0140 la protege por"
                " `owner_user_id = app.user_id` (ADR 0156)"
            )
            relforcerowsecurity = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = $1", tname
            )
            assert relforcerowsecurity is True, (
                f"{tname} tiene RLS pero sin FORCE: el dueño de la tabla se la"
                " saltaría, que es justo el rol de las migraciones"
            )
            policies = await conn.fetch(
                "SELECT polname, pg_get_expr(polqual, polrelid) AS qual FROM pg_policy"
                " WHERE polrelid = $1::regclass",
                tname,
            )
            assert len(policies) == 1, f"{tname}: esperaba UNA policy, vi {len(policies)}"
            qual = policies[0]["qual"] or ""
            assert "app.user_id" in qual, (
                f"{tname}: la policy no cuelga de `app.user_id` sino de {qual!r}"
            )

        # Singleton: un segundo identity para el MISMO owner viola el UNIQUE.
        await conn.execute(
            "INSERT INTO cortex_identity (id, owner_user_id, identity_state, version, updated_by)"
            " VALUES ($1, $2, '{}'::jsonb, 0, 'onboarding')",
            uuid4(),
            owner_id,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO cortex_identity"
                " (id, owner_user_id, identity_state, version, updated_by)"
                " VALUES ($1, $2, '{}'::jsonb, 0, 'onboarding')",
                uuid4(),
                owner_id,
            )

        # Versionado: dos filas history del mismo owner con versiones distintas conviven,
        # pero la MISMA (owner, version) viola el UNIQUE.
        await conn.execute(
            "INSERT INTO cortex_identity_history"
            " (id, owner_user_id, version, identity_state, diff, updated_by)"
            " VALUES ($1, $2, 1, '{}'::jsonb, '{}'::jsonb, 'onboarding')",
            uuid4(),
            owner_id,
        )
        await conn.execute(
            "INSERT INTO cortex_identity_history"
            " (id, owner_user_id, version, identity_state, diff, updated_by)"
            " VALUES ($1, $2, 2, '{}'::jsonb, '{}'::jsonb, 'reflection')",
            uuid4(),
            owner_id,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO cortex_identity_history"
                " (id, owner_user_id, version, identity_state, diff, updated_by)"
                " VALUES ($1, $2, 2, '{}'::jsonb, '{}'::jsonb, 'reflection')",
                uuid4(),
                owner_id,
            )
    finally:
        await conn.close()

    # downgrade -1 elimina ambas tablas (env.py corre asyncio.run → hilo aparte).
    await asyncio.to_thread(command.downgrade, alembic_config, "0093_cortex_affect")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert not await _table_exists(conn, "cortex_identity")
        assert not await _table_exists(conn, "cortex_identity_history")
    finally:
        await conn.close()
    # Re-upgrade para no dejar la DB de sesión a medias para tests posteriores.
    await asyncio.to_thread(command.upgrade, alembic_config, "head")


# ---------------------------------------------------------------------------
# Persistencia: ensure_identity (default idempotente) + update_identity (versionado)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ensure_identity_creates_default_idempotently(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex.identity import ensure_identity

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    sessionmaker = _admin_sessionmaker(admin_database_url)

    # Sin fila → ensure crea la default honesta.
    async with sessionmaker() as session:
        ident = await ensure_identity(session, owner_id)
        await session.commit()
        assert ident.owner_user_id == owner_id
        assert ident.version == 0
        state = ident.identity_state
        assert state["name"]  # nombre neutro honesto (no vacío)
        assert state["core_values"] == []
        # Baseline PAD neutro.
        assert state["mood_baseline"] == {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}

    # Segunda llamada → idempotente: una sola fila (singleton).
    async with sessionmaker() as session:
        again = await ensure_identity(session, owner_id)
        await session.commit()
        assert again.id == ident.id

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM cortex_identity WHERE owner_user_id = $1", owner_id
        )
        assert count == 1
        # ensure NO crea history: solo la creación inicial vive en cortex_identity.
        hist = await conn.fetchval(
            "SELECT count(*) FROM cortex_identity_history WHERE owner_user_id = $1", owner_id
        )
        assert hist == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_update_identity_versions_with_diff_and_reason(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex.identity import ensure_identity, get_identity, update_identity

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    sessionmaker = _admin_sessionmaker(admin_database_url)

    async with sessionmaker() as session:
        base = await ensure_identity(session, owner_id)
        before_state = dict(base.identity_state)
        await session.commit()

    # Update 1: el córtex se autonombra y fija valores.
    new_state_1 = {**before_state, "name": "Atlas", "core_values": ["honestidad", "curiosidad"]}
    async with sessionmaker() as session:
        upd = await update_identity(
            session, owner_id, new_state=new_state_1, reason="onboarding: autonombrado"
        )
        await session.commit()
        assert upd.version == 1
        assert upd.identity_state["name"] == "Atlas"

    # Update 2: ajusta valores.
    new_state_2 = {**new_state_1, "core_values": ["honestidad", "curiosidad", "rigor"]}
    async with sessionmaker() as session:
        upd2 = await update_identity(
            session, owner_id, new_state=new_state_2, reason="reflexión: refina valores"
        )
        await session.commit()
        assert upd2.version == 2

    # get_identity devuelve el ÚLTIMO estado.
    async with sessionmaker() as session:
        latest = await get_identity(session, owner_id)
        assert latest is not None
        assert latest.version == 2
        assert latest.identity_state["core_values"] == ["honestidad", "curiosidad", "rigor"]

    # cortex_identity_history tiene v1 y v2 con su diff + reason.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT version, identity_state, diff, reason, updated_by"
            " FROM cortex_identity_history WHERE owner_user_id = $1 ORDER BY version ASC",
            owner_id,
        )
        assert [r["version"] for r in rows] == [1, 2]
        import json

        diff_v1 = json.loads(rows[0]["diff"])
        # El diff de v1 captura el cambio de name (None → "Atlas") y core_values.
        assert diff_v1["name"]["after"] == "Atlas"
        assert "core_values" in diff_v1
        assert rows[0]["reason"] == "onboarding: autonombrado"
        diff_v2 = json.loads(rows[1]["diff"])
        # v2 solo cambió core_values (name no cambió → no aparece en el diff).
        assert "name" not in diff_v2
        assert diff_v2["core_values"]["after"] == ["honestidad", "curiosidad", "rigor"]
        assert rows[1]["reason"] == "reflexión: refina valores"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_identity_is_owner_scoped_cross_owner(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex.identity import ensure_identity, get_identity, update_identity

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]
    sessionmaker = _admin_sessionmaker(admin_database_url)

    # Owner A crea + versiona su identidad.
    async with sessionmaker() as session:
        a = await ensure_identity(session, owner_id)
        await update_identity(
            session,
            owner_id,
            new_state={**dict(a.identity_state), "name": "Atlas"},
            reason="onboarding",
        )
        await session.commit()

    # Owner B: get_identity es None (no ve la de A).
    async with sessionmaker() as session:
        b = await get_identity(session, other_id)
        assert b is None

    # ensure_identity para B crea SU PROPIA fila default, sin tocar la de A.
    async with sessionmaker() as session:
        b_ident = await ensure_identity(session, other_id)
        await session.commit()
        assert b_ident.identity_state["name"] != "Atlas"  # default, no la de A

    # update_identity para B NUNCA toca la fila de A ni su history.
    async with sessionmaker() as session:
        await update_identity(
            session,
            other_id,
            new_state={**dict(b_ident.identity_state), "name": "Eco"},
            reason="onboarding B",
        )
        await session.commit()

    async with sessionmaker() as session:
        a_after = await get_identity(session, owner_id)
        assert a_after is not None
        assert a_after.identity_state["name"] == "Atlas"  # intacta

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # El history de A no contiene nada del owner B.
        a_versions = await conn.fetch(
            "SELECT version FROM cortex_identity_history WHERE owner_user_id = $1", owner_id
        )
        b_versions = await conn.fetch(
            "SELECT version FROM cortex_identity_history WHERE owner_user_id = $1", other_id
        )
        assert len(a_versions) == 1
        assert len(b_versions) == 1
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# list_history — el timeline de versiones CON su diff (F3.2)
# ---------------------------------------------------------------------------
# Por qué existe: hasta la auditoría del 2026-07-27 no había NINGUNA función de
# lectura del histórico. El único lector era una query inline dentro de
# ``GET /owner/cortex/journal`` que aplana narrativas y **descarta el ``diff``**,
# así que el timeline de versiones ("qué tocó cada reflexión") era inconstruible.
# Estos tests fijan las tres propiedades que un timeline necesita y que una
# implementación descuidada rompe sin que nadie lo note: orden DESCENDENTE (el
# ``limit`` debe recortar por lo VIEJO, no por lo nuevo), presencia del ``diff``, y
# aislamiento por owner (estas tablas no tienen RLS de respaldo).
@pytest.mark.asyncio
async def test_list_history_returns_versions_newest_first_with_diff(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex.identity import ensure_identity, list_history, update_identity

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    sessionmaker = _admin_sessionmaker(admin_database_url)

    async with sessionmaker() as session:
        base = await ensure_identity(session, owner_id)
        state = dict(base.identity_state)
        await session.commit()

    # Tres reescrituras, cada una tocando UN campo distinto.
    steps = [
        ({"name": "Atlas"}, "onboarding: autonombrado"),
        ({"core_values": ["rigor"]}, "reflexión 1: fija un valor"),
        ({"narrative": "Aprendo del owner."}, "reflexión 2: narrativa"),
    ]
    for delta, reason in steps:
        state = {**state, **delta}
        async with sessionmaker() as session:
            await update_identity(session, owner_id, new_state=state, reason=reason)
            await session.commit()

    async with sessionmaker() as session:
        rows = await list_history(session, owner_id, 10)

    # Las tres versiones, MÁS RECIENTE PRIMERO (el índice del timeline es DESC).
    assert [r.version for r in rows] == [3, 2, 1]
    # El ``diff`` viaja (lo que el /journal descartaba) y es SOLO lo que cambió.
    assert set(rows[0].diff) == {"narrative"}
    assert rows[0].diff["narrative"]["after"] == "Aprendo del owner."
    assert set(rows[1].diff) == {"core_values"}
    assert set(rows[2].diff) == {"name"}
    # Y el resto de la fila de auditoría: reason + quién escribió.
    assert rows[0].reason == "reflexión 2: narrativa"
    assert rows[2].reason == "onboarding: autonombrado"
    assert rows[0].updated_by == "reflection"
    # El snapshot completo también, para poder reconstruir cualquier versión.
    assert rows[2].identity_state["name"] == "Atlas"


@pytest.mark.asyncio
async def test_list_history_limit_keeps_the_latest_versions(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """El defecto que atrapa: ordenar ASC y recortar con ``limit`` devolvería las N
    versiones MÁS ANTIGUAS — un timeline que nunca enseña lo último. Con 3 versiones
    y ``limit=2`` la respuesta correcta es [3, 2], no [1, 2]."""
    from api_server.cortex.identity import ensure_identity, list_history, update_identity

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    sessionmaker = _admin_sessionmaker(admin_database_url)

    async with sessionmaker() as session:
        base = await ensure_identity(session, owner_id)
        state = dict(base.identity_state)
        await session.commit()
    for i in (1, 2, 3):
        state = {**state, "narrative": f"v{i}"}
        async with sessionmaker() as session:
            await update_identity(session, owner_id, new_state=state, reason=f"r{i}")
            await session.commit()

    async with sessionmaker() as session:
        latest_two = await list_history(session, owner_id, 2)
        assert [r.version for r in latest_two] == [3, 2]
        # limit=1 → solo la última.
        assert [r.version for r in await list_history(session, owner_id, 1)] == [3]
        # limit no positivo → lista vacía, no un LIMIT negativo que reventaría en SQL.
        assert await list_history(session, owner_id, 0) == []
        assert await list_history(session, owner_id, -5) == []


@pytest.mark.asyncio
async def test_list_history_is_owner_scoped(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Cross-owner OBLIGATORIO: el filtro explícito es la primera línea, y desde
    la migración 0140 (ADR 0156) hay RLS de eje owner detrás. Este test vigila la
    primera; el catálogo de `test_cortex_owner_rls.py` vigila la segunda."""
    from api_server.cortex.identity import ensure_identity, list_history, update_identity

    seed = await _seed_two_owners(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]
    sessionmaker = _admin_sessionmaker(admin_database_url)

    async with sessionmaker() as session:
        a = await ensure_identity(session, owner_id)
        await update_identity(
            session,
            owner_id,
            new_state={**dict(a.identity_state), "name": "Atlas"},
            reason="onboarding A",
        )
        await session.commit()

    # Owner B sin identidad: su histórico está VACÍO (no ve el de A).
    async with sessionmaker() as session:
        assert await list_history(session, other_id, 50) == []

    # Y con histórico propio, sigue viendo SOLO el suyo.
    async with sessionmaker() as session:
        b = await ensure_identity(session, other_id)
        await update_identity(
            session,
            other_id,
            new_state={**dict(b.identity_state), "name": "Eco"},
            reason="onboarding B",
        )
        await session.commit()

    async with sessionmaker() as session:
        b_rows = await list_history(session, other_id, 50)
        a_rows = await list_history(session, owner_id, 50)
    assert [r.identity_state["name"] for r in b_rows] == ["Eco"]
    assert [r.identity_state["name"] for r in a_rows] == ["Atlas"]
    assert all(r.owner_user_id == other_id for r in b_rows)
    assert all(r.owner_user_id == owner_id for r in a_rows)
