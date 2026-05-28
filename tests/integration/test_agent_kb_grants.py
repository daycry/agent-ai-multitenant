"""Integration tests for `/agents/{id}/knowledge-bases` (Plan 06.9 task_06_9_01-02).

Drives the three endpoints end-to-end:

  - GET    /agents/{id}/knowledge-bases   tenant_member
  - POST   /agents/{id}/knowledge-bases   tenant_admin  (grant)
  - DELETE /agents/{id}/knowledge-bases/{kb_id}   tenant_admin  (revoke)

Plus the rules:

  * grant on `global_builtin` agent → 403 (must fork first).
  * cross-tenant KB grant → 404 (RLS hides it).
  * grant on missing KB → 404.
  * re-granting is idempotent.
  * revoke of a missing grant is idempotent (204).
  * tenant_user cannot grant or revoke.
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


# ---------------------------------------------------------------------------
# Seed: one tenant, two users (admin + user), one tenant-template agent,
# one global_builtin agent, two KBs in the tenant + one KB in a foreign
# tenant (for the cross-tenant 404 check).
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    foreign_tenant = uuid4()
    admin_user = uuid4()
    plain_user = uuid4()
    template_agent = uuid4()
    builtin_agent = uuid4()
    kb_a = uuid4()
    kb_b = uuid4()
    kb_foreign = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_knowledge_bases, kb_projects, chunks, documents,"
            " knowledge_bases, agents, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant,
            "Acme",
            "acme-kb",
            foreign_tenant,
            "Beta",
            "beta-kb",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, 'admin@acme.test', 'h'),"
            " ($2, 'user@acme.test', 'h')",
            admin_user,
            plain_user,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user')",
            uuid4(),
            tenant,
            admin_user,
            uuid4(),
            tenant,
            plain_user,
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt)"
            " VALUES ($1, $2, 'backend-dev-template', 'backend_dev',"
            "         'global_tenant_template', 'ai', 'You are a backend dev.'),"
            "        ($3, $4, 'builtin-pm', 'project_manager',"
            "         'global_builtin', 'ai', 'You are a PM.')",
            template_agent,
            tenant,
            builtin_agent,
            tenant,
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES"
            " ($1, $2, 'API REST design'),"
            " ($3, $4, 'Python conventions'),"
            " ($5, $6, 'Foreign KB')",
            kb_a,
            tenant,
            kb_b,
            tenant,
            kb_foreign,
            foreign_tenant,
        )
    finally:
        await conn.close()
    return {
        "tenant": tenant,
        "foreign_tenant": foreign_tenant,
        "admin_user": admin_user,
        "plain_user": plain_user,
        "template_agent": template_agent,
        "builtin_agent": builtin_agent,
        "kb_a": kb_a,
        "kb_b": kb_b,
        "kb_foreign": kb_foreign,
    }


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------
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


async def _mint(user_id: UUID, tenant_id: UUID | None) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Happy path: grant → list → revoke
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_grant_list_revoke_roundtrip(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Initially empty
        r = await client.get(f"/agents/{seed['template_agent']}/knowledge-bases", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json() == []

        # Grant KB A
        r = await client.post(
            f"/agents/{seed['template_agent']}/knowledge-bases",
            headers=headers,
            json={"kb_id": str(seed["kb_a"])},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["agent_id"] == str(seed["template_agent"])
        assert body["kb_id"] == str(seed["kb_a"])

        # List shows it
        r = await client.get(f"/agents/{seed['template_agent']}/knowledge-bases", headers=headers)
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["kb_id"] == str(seed["kb_a"])
        assert rows[0]["name"] == "API REST design"

        # Revoke
        r = await client.delete(
            f"/agents/{seed['template_agent']}/knowledge-bases/{seed['kb_a']}",
            headers=headers,
        )
        assert r.status_code == 204

        # List back to empty
        r = await client.get(f"/agents/{seed['template_agent']}/knowledge-bases", headers=headers)
        assert r.status_code == 200
        assert r.json() == []


# ---------------------------------------------------------------------------
# Re-grant is idempotent (no 409)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_re_grant_is_idempotent(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        for _ in range(2):
            r = await client.post(
                f"/agents/{seed['template_agent']}/knowledge-bases",
                headers=headers,
                json={"kb_id": str(seed["kb_a"])},
            )
            assert r.status_code == 201, r.text

        r = await client.get(f"/agents/{seed['template_agent']}/knowledge-bases", headers=headers)
        assert len(r.json()) == 1


# ---------------------------------------------------------------------------
# Revoke of a missing grant returns 204 (no 404)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revoke_missing_grant_is_idempotent(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.delete(
            f"/agents/{seed['template_agent']}/knowledge-bases/{seed['kb_b']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 204


# ---------------------------------------------------------------------------
# global_builtin agent rejects grant with 403
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_grant_on_builtin_agent_is_403(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/agents/{seed['builtin_agent']}/knowledge-bases",
            headers={"Authorization": f"Bearer {token}"},
            json={"kb_id": str(seed["kb_a"])},
        )
        assert r.status_code == 403, r.text
        assert "global_builtin" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Cross-tenant KB → 404 (RLS hides it; we surface a clean 404)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_grant_cross_tenant_kb_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/agents/{seed['template_agent']}/knowledge-bases",
            headers={"Authorization": f"Bearer {token}"},
            json={"kb_id": str(seed["kb_foreign"])},
        )
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Missing KB → 404
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_grant_missing_kb_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/agents/{seed['template_agent']}/knowledge-bases",
            headers={"Authorization": f"Bearer {token}"},
            json={"kb_id": str(uuid4())},
        )
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# tenant_user cannot grant (Plan 06.8 gate)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_user_cannot_grant(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/agents/{seed['template_agent']}/knowledge-bases",
            headers={"Authorization": f"Bearer {token}"},
            json={"kb_id": str(seed["kb_a"])},
        )
        assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# tenant_user CAN list (read endpoint is tenant_member)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tenant_user_can_list(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["plain_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.get(
            f"/agents/{seed['template_agent']}/knowledge-bases",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Body validation: missing kb_id → 422
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_grant_missing_kb_id_is_422(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/agents/{seed['template_agent']}/knowledge-bases",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        assert r.status_code == 422, r.text
