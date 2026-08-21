"""prod-13 · task_prod13_21 — la OTRA mitad de perf-10: la caché de membership.

`get_platform_setting` ya se servía de Redis (probado en
`test_redis_cache_and_chat_rate_limit.py`); el lookup de membership, que corre
en CADA request de cada endpoint tenant-scoped, seguía yendo a PostgreSQL.

Esta caché tiene un riesgo que la de settings no tiene, y es el que el plan
recoge en su riesgo nº 5: **es una decisión de autorización**. Una entrada mal
invalidada mantiene dentro a un usuario al que le acaban de retirar el acceso, o
como admin a uno degradado. Por eso aquí el test que manda no es el de "va más
rápido", sino los tres de revocación:

  * retirar la membresía deniega en la petición SIGUIENTE, no en 30 s;
  * degradar el rol quita el acceso de admin en la petición siguiente;
  * y —el simétrico que se olvida— CONCEDER acceso también surte efecto ya, o un
    usuario recién invitado se pasaría medio minuto viendo 403.

Va contra Redis y PostgreSQL de verdad: el bug de una caché siempre está en la
invalidación real, y una invalidación probada con un doble no prueba nada.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from fastapi import HTTPException

pytestmark = pytest.mark.integration


@pytest.fixture()
def migrated_db(alembic_config, test_database_url: str, test_redis_url: str) -> None:
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))


@pytest.fixture()
def wired(migrated_db: None, admin_database_url: str, test_redis_url: str, monkeypatch):
    monkeypatch.setenv("API_SERVER_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    try:
        yield
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed_member(dsn: str, *, role: str = "tenant_user") -> tuple[UUID, UUID, UUID]:
    """Un tenant, un usuario y su membresía activa. Devuelve (user, tenant, membership)."""
    user_id, tenant_id, membership_id = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            f"Org {tenant_id.hex[:6]}",
            f"org-{tenant_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
            user_id,
            f"u-{user_id.hex[:8]}@test.local",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active)"
            " VALUES ($1, $2, $3, $4, true)",
            membership_id,
            tenant_id,
            user_id,
            role,
        )
    finally:
        await conn.close()
    return user_id, tenant_id, membership_id


def _principal(user_id: UUID, tenant_id: UUID | None):
    from api_server.auth.deps import AuthPrincipal

    return AuthPrincipal(user_id=user_id, tenant_id=tenant_id, session_id=uuid4())


# ---------------------------------------------------------------------------
# 1. Hay caché de verdad
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_second_membership_check_is_served_from_redis(
    wired, migrations_pg_dsn: str
) -> None:
    """Se cambia la fila POR DEBAJO (SQL directo, sin pasar por el ORM) y se
    comprueba que la comprobación sigue devolviendo el rol cacheado. Sin caché
    devolvería el nuevo: es la única forma de medir el acierto que no se puede
    falsear desde fuera."""
    from api_server.auth.deps import principal_is_tenant_admin
    from api_server.db.session import get_admin_sessionmaker

    user_id, tenant_id, _ = await _seed_member(migrations_pg_dsn, role="tenant_admin")
    principal = _principal(user_id, tenant_id)
    sessionmaker = get_admin_sessionmaker()

    async with sessionmaker() as session:
        assert await principal_is_tenant_admin(session, principal) is True

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE user_org_memberships SET role = 'tenant_user' WHERE user_id = $1", user_id
        )
    finally:
        await conn.close()

    async with sessionmaker() as session:
        still_admin = await principal_is_tenant_admin(session, principal)
    assert still_admin is True, "no hubo caché: la comprobación fue a la BD"


# ---------------------------------------------------------------------------
# 2. Los tres tests de revocación — el motivo por el que esta caché es delicada
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revoking_a_membership_denies_on_the_very_next_request(
    wired, migrations_pg_dsn: str
) -> None:
    """El agujero de autorización que describe el riesgo 5 del plan: un usuario
    al que se le retira el acceso seguiría entrando hasta que expirase el TTL."""
    from api_server.auth.deps import require_tenant_member
    from api_server.db.models import UserOrganizationMembership
    from api_server.db.session import get_admin_sessionmaker
    from sqlalchemy import select

    user_id, tenant_id, membership_id = await _seed_member(migrations_pg_dsn)
    principal = _principal(user_id, tenant_id)
    sessionmaker = get_admin_sessionmaker()

    async with sessionmaker() as session:
        assert await require_tenant_member(principal, session) is principal

    # Revocación por la vía legítima (ORM + commit), como hace el endpoint.
    async with sessionmaker() as session, session.begin():
        membership = (
            await session.execute(
                select(UserOrganizationMembership).where(
                    UserOrganizationMembership.id == membership_id
                )
            )
        ).scalar_one()
        membership.is_active = False

    async with sessionmaker() as session:
        with pytest.raises(HTTPException) as excinfo:
            await require_tenant_member(principal, session)
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_downgrading_the_role_removes_admin_on_the_very_next_request(
    wired, migrations_pg_dsn: str
) -> None:
    """Degradar a un admin es la otra mitad de la revocación: la membresía sigue
    existiendo, pero el rol cacheado no puede sobrevivirle."""
    from api_server.auth.deps import require_tenant_admin
    from api_server.db.models import UserOrganizationMembership, UserRole
    from api_server.db.session import get_admin_sessionmaker
    from sqlalchemy import select

    user_id, tenant_id, membership_id = await _seed_member(
        migrations_pg_dsn, role=UserRole.TENANT_ADMIN.value
    )
    principal = _principal(user_id, tenant_id)
    sessionmaker = get_admin_sessionmaker()

    async with sessionmaker() as session:
        assert await require_tenant_admin(principal, session) is principal

    async with sessionmaker() as session, session.begin():
        membership = (
            await session.execute(
                select(UserOrganizationMembership).where(
                    UserOrganizationMembership.id == membership_id
                )
            )
        ).scalar_one()
        membership.role = UserRole.TENANT_USER.value

    async with sessionmaker() as session:
        with pytest.raises(HTTPException) as excinfo:
            await require_tenant_admin(principal, session)
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_granting_access_also_takes_effect_immediately(wired, migrations_pg_dsn: str) -> None:
    """El simétrico que se olvida: la AUSENCIA de membresía también se cachea (es
    el caso más común y el más barato de servir), así que conceder acceso tiene
    que invalidar igual o el usuario recién invitado ve 403 medio minuto."""
    from api_server.auth.deps import require_tenant_member
    from api_server.db.models import UserOrganizationMembership, UserRole
    from api_server.db.session import get_admin_sessionmaker

    user_id, tenant_id, membership_id = await _seed_member(migrations_pg_dsn)
    principal = _principal(user_id, tenant_id)
    sessionmaker = get_admin_sessionmaker()

    # Se borra la membresía por debajo y se cachea el "no es miembro".
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("DELETE FROM user_org_memberships WHERE id = $1", membership_id)
    finally:
        await conn.close()
    from api_server.cache.membership import invalidate_membership_cache

    await invalidate_membership_cache(user_id, tenant_id)
    async with sessionmaker() as session:
        with pytest.raises(HTTPException):
            await require_tenant_member(principal, session)

    # Alta por la vía legítima.
    async with sessionmaker() as session, session.begin():
        session.add(
            UserOrganizationMembership(
                tenant_id=tenant_id,
                user_id=user_id,
                role=UserRole.TENANT_USER.value,
                is_active=True,
            )
        )

    async with sessionmaker() as session:
        assert await require_tenant_member(principal, session) is principal


# ---------------------------------------------------------------------------
# 3. Aislamiento y degradación
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_same_user_in_two_tenants_never_shares_a_cache_entry(
    wired, migrations_pg_dsn: str
) -> None:
    """Multi-tenancy: la clave lleva tenant_id. Cachear por usuario a secas daría
    a un usuario el rol que tiene en OTRO tenant."""
    from api_server.auth.deps import principal_is_tenant_admin
    from api_server.db.session import get_admin_sessionmaker

    user_id, tenant_a, _ = await _seed_member(migrations_pg_dsn, role="tenant_admin")
    _other, tenant_b, _mb = await _seed_member(migrations_pg_dsn, role="tenant_user")
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # El MISMO usuario, simple miembro en el segundo tenant.
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active)"
            " VALUES ($1, $2, $3, 'tenant_user', true)",
            uuid4(),
            tenant_b,
            user_id,
        )
    finally:
        await conn.close()

    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        assert await principal_is_tenant_admin(session, _principal(user_id, tenant_a)) is True
        assert await principal_is_tenant_admin(session, _principal(user_id, tenant_b)) is False
        # Y en el otro orden, por si el primero envenenase al segundo.
        assert await principal_is_tenant_admin(session, _principal(user_id, tenant_b)) is False
        assert await principal_is_tenant_admin(session, _principal(user_id, tenant_a)) is True


@pytest.mark.asyncio
async def test_the_check_still_works_with_redis_unreachable(
    wired, migrations_pg_dsn: str, monkeypatch
):
    """Fail-open sobre la CACHÉ (no sobre la autorización): con Redis caído la
    comprobación vuelve a ser exactamente la de antes contra PostgreSQL, que
    sigue siendo la verdad. Un 500 aquí tumbaría toda la API."""
    from api_server.auth.deps import require_tenant_member, reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import get_admin_sessionmaker

    user_id, tenant_id, _ = await _seed_member(migrations_pg_dsn)
    principal = _principal(user_id, tenant_id)

    monkeypatch.setenv("API_SERVER_REDIS_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()
    reset_redis_cache()

    async with get_admin_sessionmaker()() as session:
        assert await require_tenant_member(principal, session) is principal
        assert await require_tenant_member(principal, session) is principal


@pytest.mark.asyncio
async def test_the_ttl_is_short_enough_to_bound_a_missed_invalidation(wired) -> None:
    """El TTL es el techo del daño si alguna vía de escritura se salta la
    invalidación. El plan lo fija en ≤ 60 s."""
    from api_server.cache.membership import MEMBERSHIP_CACHE_TTL_SECONDS

    assert 0 < MEMBERSHIP_CACHE_TTL_SECONDS <= 60


@pytest.mark.asyncio
async def test_the_cached_entry_actually_carries_the_tenant_and_user_in_its_key(
    wired, migrations_pg_dsn: str
) -> None:
    """Guarda contra una caché que "funciona" porque nunca guarda nada: se
    comprueba que la clave EXISTE en Redis tras la primera lectura."""
    from api_server.auth.deps import get_redis, require_tenant_member
    from api_server.cache.membership import membership_cache_key
    from api_server.db.session import get_admin_sessionmaker

    user_id, tenant_id, _ = await _seed_member(migrations_pg_dsn)
    async with get_admin_sessionmaker()() as session:
        await require_tenant_member(_principal(user_id, tenant_id), session)

    key = membership_cache_key(user_id, tenant_id)
    assert str(tenant_id) in key and str(user_id) in key
    assert await get_redis().get(key) is not None, "no se escribió nada en Redis"
