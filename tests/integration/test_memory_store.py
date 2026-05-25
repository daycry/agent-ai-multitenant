"""Integration tests for POST /memories (Plan 04 task_04_05).

Drives the four scope branches end-to-end:

  - private        — user_id derived from the authenticated principal,
  - team_shared    — team_id required in the body,
  - project_shared — project_id required in the body,
  - global         — only tenant_admin can write.

Plus the basic listing endpoint and the 422 paths.
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


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    admin_id = uuid4()
    member_id = uuid4()
    team_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, plans, conversations, projects, agents,"
            " teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Store",
            "tenant-store",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-store",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            admin_id,
            "admin@store.test",
            "h",
            member_id,
            "member@store.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_member')",
            uuid4(),
            tenant_id,
            admin_id,
            uuid4(),
            tenant_id,
            member_id,
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3)",
            team_id,
            tenant_id,
            "Team A",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Store Project",
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "admin_id": admin_id,
        "member_id": member_id,
        "team_id": team_id,
        "project_id": project_id,
    }


# ---------------------------------------------------------------------------
# App fixture (mirrors test_plan_approval.py)
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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Scope branches
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_private_scope_pins_to_authenticated_user(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/memories",
            json={
                "content": "Friday deploys are forbidden in this project.",
                "type": "semantic",
                "scope": "private",
                "tags": ["deploys", "policy"],
            },
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scope"] == "private"
    assert body["user_id"] == str(seeded["member_id"])
    assert body["team_id"] is None
    assert body["project_id"] is None
    assert body["tags"] == ["deploys", "policy"]
    assert body["has_embedding"] is False


@pytest.mark.asyncio
async def test_team_shared_requires_team_id(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Missing team_id → 422 from Pydantic.
        missing = await client.post(
            "/memories",
            json={
                "content": "Team prefers REST over GraphQL.",
                "type": "semantic",
                "scope": "team_shared",
            },
            headers=headers,
        )
        assert missing.status_code == 422, missing.text

        ok = await client.post(
            "/memories",
            json={
                "content": "Team prefers REST over GraphQL.",
                "type": "semantic",
                "scope": "team_shared",
                "team_id": str(seeded["team_id"]),
            },
            headers=headers,
        )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["team_id"] == str(seeded["team_id"])
    assert body["user_id"] is None


@pytest.mark.asyncio
async def test_project_shared_requires_project_id(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/memories",
            json={
                "content": "Project uses asyncpg, never psycopg3.",
                "type": "semantic",
                "scope": "project_shared",
                "project_id": str(seeded["project_id"]),
            },
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["project_id"] == str(seeded["project_id"])


@pytest.mark.asyncio
async def test_global_scope_requires_tenant_admin(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_id"], seeded["tenant_id"])
    member_token = await _mint_token(seeded["member_id"], seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # tenant_member is forbidden.
        forbidden = await client.post(
            "/memories",
            json={
                "content": "Global rule.",
                "type": "semantic",
                "scope": "global",
            },
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert forbidden.status_code == 403, forbidden.text

        # tenant_admin can write.
        ok = await client.post(
            "/memories",
            json={
                "content": "Global rule.",
                "type": "semantic",
                "scope": "global",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["scope"] == "global"
    assert body["user_id"] is None
    assert body["team_id"] is None
    assert body["project_id"] is None


# ---------------------------------------------------------------------------
# Listing + soft delete
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_and_delete_round_trip(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Create 3 memories of different shapes.
        await client.post(
            "/memories",
            json={"content": "private one", "scope": "private"},
            headers=headers,
        )
        await client.post(
            "/memories",
            json={
                "content": "team one",
                "scope": "team_shared",
                "team_id": str(seeded["team_id"]),
            },
            headers=headers,
        )
        await client.post(
            "/memories",
            json={
                "content": "project one",
                "scope": "project_shared",
                "project_id": str(seeded["project_id"]),
            },
            headers=headers,
        )

        listed = await client.get("/memories", headers=headers)
        assert listed.status_code == 200, listed.text
        rows = listed.json()
        assert len(rows) == 3

        # Filter by scope.
        only_team = await client.get("/memories?scope=team_shared", headers=headers)
        team_rows = only_team.json()
        assert len(team_rows) == 1
        assert team_rows[0]["scope"] == "team_shared"

        # Soft-delete the team row.
        target_id = team_rows[0]["id"]
        deleted = await client.delete(f"/memories/{target_id}", headers=headers)
        assert deleted.status_code == 204

        # Re-listing must not show it.
        after = await client.get("/memories", headers=headers)
        assert target_id not in [r["id"] for r in after.json()]


@pytest.mark.asyncio
async def test_content_oversize_is_rejected_by_pydantic(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/memories",
            json={"content": "x" * 3000, "scope": "private"},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text
