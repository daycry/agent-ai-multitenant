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
