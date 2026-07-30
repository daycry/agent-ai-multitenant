"""P1-02 (auditoría proyecto 2026-07-17): slug de proyecto único por tenant.

Dos proyectos «API v1» y «API-v1» del mismo tenant producían el MISMO slug
`api-v1` → el layout de bare repos (`/repos/{tenant}/{project_slug}`) colisiona
y el segundo proyecto opera sobre el repo del primero. Ahora:

- `POST /projects` deduplica añadiendo `-{id8}` en colisión.
- La migración 0114 crea el índice único parcial `(tenant_id, slug) WHERE
  deleted_at IS NULL` (backstop) con dedupe previo de los existentes.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

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


async def _token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant, admin = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            tenant,
            f"su-{tenant.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@su.test', 'h')", admin
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin": admin}


@pytest.mark.asyncio
async def test_colliding_names_get_distinct_slugs(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        first = await client.post("/projects", json={"name": "API v1"}, headers=headers)
        second = await client.post("/projects", json={"name": "API-v1"}, headers=headers)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        slugs = [
            r["slug"]
            for r in await conn.fetch(
                "SELECT slug FROM projects WHERE tenant_id = $1 ORDER BY created_at",
                seeded["tenant"],
            )
        ]
    finally:
        await conn.close()
    assert slugs[0] == "api-v1"
    assert slugs[1] != "api-v1"
    assert slugs[1].startswith("api-v1-")


@pytest.mark.asyncio
async def test_unique_index_backstop_exists(configured_app, migrations_pg_dsn: str) -> None:
    """La migración 0114 deja el índice único parcial como backstop."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'projects'"
            " AND indexname = 'uq_projects_tenant_slug_live'"
        )
    finally:
        await conn.close()
    assert row is not None
    assert "UNIQUE" in row["indexdef"]
    assert "deleted_at IS NULL" in row["indexdef"]
