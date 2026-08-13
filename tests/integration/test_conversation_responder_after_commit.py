"""Integration: the project-chat responder fires AFTER the user message commits.

Regression guard for the C1 bug (audit 2026-06-21): the team reply was scheduled
with FastAPI ``BackgroundTasks``, which run BEFORE the yield-dependency commit, so
``respond_to_conversation`` opened its own session and read the history while the
just-posted USER message was not yet durable → the team responded to stale/empty
history. The fix schedules via ``schedule_after_commit`` + a detached task.

This test pins the ordering behaviourally: at the instant the responder is invoked,
a SEPARATE connection (fresh transaction, READ COMMITTED) must already see the USER
message row. It fails while the trigger is a BackgroundTask (uncommitted) and passes
once it is deferred to after the request's commit.
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


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_dependencies, tasks, plan_comments, plans, conversations,"
            " projects, agents, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Tenant Chat",
            "tenant-chat",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "dave@chat.test",
            "h",
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
            "Chat Project",
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


@pytest.mark.asyncio
async def test_responder_fires_after_user_message_commits(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At responder-invocation time, the USER message must already be committed."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    from api_server.chat import responder

    done = asyncio.Event()
    observed: dict[str, object] = {}

    async def spy(*, conversation_id, tenant_id, mode, vault, redis):  # type: ignore[no-untyped-def]
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            count = await conn.fetchval(
                "SELECT count(*) FROM messages WHERE conversation_id = $1 AND author_kind = 'user'",
                conversation_id,
            )
        finally:
            await conn.close()
        observed["user_messages_committed"] = count
        observed["mode"] = mode
        done.set()

    # schedule_reply()'s detached task calls the module-global respond_to_conversation.
    monkeypatch.setattr(responder, "respond_to_conversation", spy)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        created = await client.post(
            f"/projects/{seeded['project_id']}/conversations",
            json={"title": "Plan chat", "current_mode": "planning"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        conv_id = created.json()["id"]

        posted = await client.post(
            f"/conversations/{conv_id}/messages",
            json={"author_kind": "user", "content": "Necesito una landing en CI4"},
            headers=headers,
        )
        assert posted.status_code == 201, posted.text

        # The detached reply task runs on this loop; wait for the spy to fire.
        await asyncio.wait_for(done.wait(), timeout=5)

    assert observed.get("user_messages_committed") == 1, (
        "the responder ran BEFORE the USER message was committed — it would plan on "
        f"stale/empty history (saw {observed.get('user_messages_committed')!r} user messages)"
    )
    assert observed.get("mode") == "planning"
