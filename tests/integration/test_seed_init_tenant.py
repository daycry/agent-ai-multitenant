"""Integration tests for the initial-tenant seed (Plan prod-01 task_16 / deploy-1).

`api_server.seeds.init_tenant` is the invocable entrypoint the real installer
runs at the SEED_TENANT step: it creates the operator's first organization +
its admin user + a tenant_admin membership, idempotently. Without it the
installer's seed step is still a simulacrum (the audit's deploy-1 finding).
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


def _as_async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE user_org_memberships, users, organizations CASCADE")
    finally:
        await conn.close()


async def _run_init(dsn: str, **kwargs: str):
    from api_server.seeds.init_tenant import init_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            return await init_tenant(session, **kwargs)
    finally:
        await engine.dispose()


_ARGS = {
    "tenant_name": "Acme Corp",
    "slug": "acme",
    "admin_email": "Admin@Acme.com",
    "admin_password": "a-very-long-throwaway-password-123",
}


def test_init_tenant_creates_org_user_membership(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    dsn = _as_async_dsn(migrations_pg_dsn)
    asyncio.run(_truncate(migrations_pg_dsn))

    result = asyncio.run(_run_init(dsn, **_ARGS))

    assert result.created_org is True
    assert result.created_user is True
    assert result.created_membership is True
    # First user on a fresh DB is promoted to the system admin (mirrors register).
    assert result.is_system_admin is True


def test_init_tenant_is_idempotent(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    dsn = _as_async_dsn(migrations_pg_dsn)
    asyncio.run(_truncate(migrations_pg_dsn))

    first = asyncio.run(_run_init(dsn, **_ARGS))
    second = asyncio.run(_run_init(dsn, **_ARGS))

    # Re-running creates nothing new and resolves to the SAME rows.
    assert second.created_org is False
    assert second.created_user is False
    assert second.created_membership is False
    assert second.tenant_id == first.tenant_id
    assert second.user_id == first.user_id


def test_init_tenant_promotes_first_user_to_system_owner(
    alembic_config, migrations_pg_dsn: str
) -> None:
    """ADR 0134: el seed debe fijar ``is_system_owner`` igual que fija
    ``is_system_admin``.

    Hasta el 2026-07-31 el ÚNICO sitio de ``apps/`` que escribía
    ``is_system_owner=True`` era ``POST /auth/register`` con la tabla vacía, así
    que en toda instalación hecha con el instalador (que siembra por aquí)
    **nadie** era System Owner: el córtex entero —lo que protege
    ``require_system_owner``, ADR 0074— quedaba inalcanzable de forma permanente
    y solo se arreglaba con un UPDATE a mano. Al cerrar el registro público esa
    era además la última puerta al rol.
    """
    command.upgrade(alembic_config, "head")
    dsn = _as_async_dsn(migrations_pg_dsn)
    asyncio.run(_truncate(migrations_pg_dsn))

    result = asyncio.run(_run_init(dsn, **_ARGS))

    assert result.is_system_owner is True

    async def _owners() -> list[str]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            rows = await conn.fetch("SELECT email FROM users WHERE is_system_owner")
            return [r["email"] for r in rows]
        finally:
            await conn.close()

    # Exactamente UNO (el singleton del ADR 0074), y es el admin sembrado.
    assert asyncio.run(_owners()) == ["admin@acme.com"]


def test_init_tenant_does_not_mint_a_second_system_owner(
    alembic_config, migrations_pg_dsn: str
) -> None:
    """Un segundo tenant sembrado sobre una BD que YA tiene usuarios no crea un
    segundo owner: el índice único parcial ``uq_users_system_owner`` lo prohíbe,
    así que el seed tiene que respetar el `is_first_user` (si escribiera True a
    ciegas, este seed reventaría con IntegrityError)."""
    command.upgrade(alembic_config, "head")
    dsn = _as_async_dsn(migrations_pg_dsn)
    asyncio.run(_truncate(migrations_pg_dsn))

    asyncio.run(_run_init(dsn, **_ARGS))
    second = asyncio.run(
        _run_init(
            dsn,
            tenant_name="Globex",
            slug="globex",
            admin_email="admin@globex.com",
            admin_password="another-very-long-throwaway-pw-123",
        )
    )

    assert second.created_user is True
    assert second.is_system_admin is False
    assert second.is_system_owner is False

    async def _count_owners() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            value = await conn.fetchval("SELECT count(*) FROM users WHERE is_system_owner")
            return int(value)
        finally:
            await conn.close()

    assert asyncio.run(_count_owners()) == 1


def test_init_tenant_normalizes_email_and_hashes_password(
    alembic_config, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    dsn = _as_async_dsn(migrations_pg_dsn)
    asyncio.run(_truncate(migrations_pg_dsn))

    asyncio.run(_run_init(dsn, **_ARGS))

    async def _read() -> tuple[str, str, str]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            row = await conn.fetchrow(
                "SELECT email, password_hash, "
                "(SELECT role FROM user_org_memberships LIMIT 1) AS role "
                "FROM users LIMIT 1"
            )
            return row["email"], row["password_hash"], row["role"]
        finally:
            await conn.close()

    email, password_hash, role = asyncio.run(_read())
    assert email == "admin@acme.com"  # lower-cased
    # The password is stored as an argon2id hash, never plaintext.
    assert password_hash.startswith("$argon2")
    assert "a-very-long-throwaway-password-123" not in password_hash
    assert role == "tenant_admin"
