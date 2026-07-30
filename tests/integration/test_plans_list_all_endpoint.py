"""c8/T11 (ADR 0008): GET /plans lists every plan across the tenant's projects.

The management board consumes this (a Kanban of PLANS). RLS scopes it to the caller's
tenant (a second tenant's plans never leak); ``?project_id`` / ``?status`` filter.
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

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "user_a": uuid4(),
        "tenant_b": uuid4(),
        "proj1": uuid4(),
        "proj2": uuid4(),
        "proj_b": uuid4(),
        "plan1": uuid4(),
        "plan2": uuid4(),
        "plan3": uuid4(),
        "plan_b": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE plans, projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'A', 'org-a-plans'),"
            " ($2, 'B', 'org-b-plans'), ($3, 'Platform', 'platform-plans')",
            ids["tenant_a"],
            ids["tenant_b"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@plans.test', 'x')",
            ids["user_a"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["user_a"],
        )
        for key, tenant in (("proj1", "tenant_a"), ("proj2", "tenant_a"), ("proj_b", "tenant_b")):
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name, slug, status, is_template)"
                " VALUES ($1, $2, $3, $3, 'active', false)",
                ids[key],
                ids[tenant],
                key,
            )
        # tenant_a: 2 plans in proj1 (in_progress, completed), 1 in proj2 (blocked).
        for plan, proj, st in (
            ("plan1", "proj1", "in_progress"),
            ("plan2", "proj1", "completed"),
            ("plan3", "proj2", "blocked"),
        ):
            await conn.execute(
                "INSERT INTO plans (id, tenant_id, project_id, title, slug, status)"
                " VALUES ($1, $2, $3, $4, $4, $5)",
                ids[plan],
                ids["tenant_a"],
                ids[proj],
                plan,
                st,
            )
        # tenant_b: one plan that tenant_a must NEVER see.
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, slug, status)"
            " VALUES ($1, $2, $3, 'plan-b', 'plan-b', 'in_progress')",
            ids["plan_b"],
            ids["tenant_b"],
            ids["proj_b"],
        )
    finally:
        await conn.close()
    return ids


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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_list_all_plans_is_tenant_scoped_and_filterable(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # All of tenant A's plans across BOTH projects — never tenant B's.
        resp = await client.get("/plans", headers=headers)
        assert resp.status_code == 200, resp.text
        got = {p["id"] for p in resp.json()}
        assert got == {str(ids["plan1"]), str(ids["plan2"]), str(ids["plan3"])}
        assert str(ids["plan_b"]) not in got

        # ?status filter.
        blocked = await client.get("/plans?status=blocked", headers=headers)
        assert {p["id"] for p in blocked.json()} == {str(ids["plan3"])}

        # ?project_id filter.
        proj1 = await client.get(f"/plans?project_id={ids['proj1']}", headers=headers)
        assert {p["id"] for p in proj1.json()} == {str(ids["plan1"]), str(ids["plan2"])}
