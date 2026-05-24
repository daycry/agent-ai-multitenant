"""Integration tests for inline plan comments (Plan 03 task_03_21).

Exercises the new endpoints `/plans/{id}/comments` end-to-end. The
canonical-template `specification` we ship is intentionally small so
the test focuses on the comment lifecycle, not the rich plan body
already covered by test_plan_persistence.
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
    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE plan_comments, plans, conversations, projects, agents,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)" " VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Comments",
            "tenant-comments",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-comments",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@comments.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_id,
            user_id,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Comments Project",
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id, "project_id": project_id}


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


async def _create_plan(client: AsyncClient, seeded: dict, headers: dict) -> str:
    resp = await client.post(
        f"/projects/{seeded['project_id']}/plans",
        json={
            "title": "Plan con comentarios",
            "specification": {
                "tasks": [
                    {"id": "t1", "title": "A"},
                    {"id": "t2", "title": "B", "depends_on": ["t1"]},
                ],
            },
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ===========================================================================
# Tests
# ===========================================================================
@pytest.mark.asyncio
async def test_plan_scoped_comment_round_trips(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded, headers)

        post = await client.post(
            f"/plans/{plan_id}/comments",
            json={"target_kind": "plan", "content": "Falta detalle de seguridad."},
            headers=headers,
        )
        assert post.status_code == 201, post.text
        body = post.json()
        assert body["target_kind"] == "plan"
        assert body["target_ref"] is None
        assert body["author_user_id"] == str(seeded["user_id"])

        listed = await client.get(f"/plans/{plan_id}/comments", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["content"] == "Falta detalle de seguridad."


@pytest.mark.asyncio
async def test_task_scoped_comment_requires_known_task_ref(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded, headers)

        ok = await client.post(
            f"/plans/{plan_id}/comments",
            json={
                "target_kind": "task",
                "target_ref": "t2",
                "content": "¿Qué proveedor JWT?",
            },
            headers=headers,
        )
        assert ok.status_code == 201, ok.text
        assert ok.json()["target_kind"] == "task"
        assert ok.json()["target_ref"] == "t2"

        # Unknown task id is rejected at the API layer.
        bad = await client.post(
            f"/plans/{plan_id}/comments",
            json={
                "target_kind": "task",
                "target_ref": "ghost",
                "content": "should fail",
            },
            headers=headers,
        )
        assert bad.status_code == 404


@pytest.mark.asyncio
async def test_plan_comment_pydantic_rejects_inconsistent_target_ref(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Pydantic enforces: plan -> no target_ref; task/phase -> required."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded, headers)

        # plan with a target_ref -> 422.
        bad1 = await client.post(
            f"/plans/{plan_id}/comments",
            json={"target_kind": "plan", "target_ref": "t1", "content": "x"},
            headers=headers,
        )
        assert bad1.status_code == 422

        # task without a target_ref -> 422.
        bad2 = await client.post(
            f"/plans/{plan_id}/comments",
            json={"target_kind": "task", "content": "x"},
            headers=headers,
        )
        assert bad2.status_code == 422
