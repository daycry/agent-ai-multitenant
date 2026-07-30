"""Córtex — endpoint ``GET /owner/cortex/curiosity/pursuits`` ("lo que está aprendiendo").

Historial de persecuciones de curiosidad del owner (ADR 0078): gated
``require_system_owner`` (DB-authoritative), filtro ``owner_user_id`` explícito
(cross-owner OBLIGATORIO), filtro opcional por ``status`` y orden
``created_at DESC``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed(dsn: str) -> dict[str, UUID]:
    owner_id = uuid4()
    other_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_curiosity_pursuits, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Pursuits Tenant",
            "pursuits-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, 'h', true), ($3, $4, 'h', false)",
            owner_id,
            "owner@pursuits.test",
            other_id,
            "otro@pursuits.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin'), ($4, $2, $5, 'tenant_admin')",
            uuid4(),
            tenant_id,
            owner_id,
            uuid4(),
            other_id,
        )
        # Dos pursuits del owner (digested y surfaced) + uno del otro usuario.
        await conn.execute(
            "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status,"
            " created_at, updated_at) VALUES"
            " ($1, $2, 'tema viejo', 'digested', now() - interval '1 hour', now()),"
            " ($3, $2, 'tema nuevo', 'surfaced', now(), now()),"
            " ($4, $5, 'tema ajeno', 'digested', now(), now())",
            uuid4(),
            owner_id,
            uuid4(),
            uuid4(),
            other_id,
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "other_id": other_id, "tenant_id": tenant_id}


async def _mint(user_id: UUID, tenant_id: UUID, *, owner: bool) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=owner)


@pytest.mark.asyncio
async def test_lista_pursuits_del_owner_orden_y_filtro(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner=True)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/owner/cortex/curiosity/pursuits",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()
        # Solo los del owner (jamás 'tema ajeno'), más reciente primero.
        topics = [item["topic"] for item in items]
        assert topics == ["tema nuevo", "tema viejo"]
        assert items[0]["status"] == "surfaced"
        assert items[0]["surfaced_at"] is None or isinstance(items[0]["surfaced_at"], str)

        # Filtro por status.
        resp2 = await client.get(
            "/owner/cortex/curiosity/pursuits?status=digested",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200
        assert [item["topic"] for item in resp2.json()] == ["tema viejo"]


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_no_owner_recibe_403(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    # Claim forjado: is_system_owner=True en el token, pero la BD dice false.
    token = await _mint(seed["other_id"], seed["tenant_id"], owner=True)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/owner/cortex/curiosity/pursuits",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
