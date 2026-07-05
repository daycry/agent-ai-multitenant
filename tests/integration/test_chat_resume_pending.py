"""c9 (ciclo-vida T6) — durabilidad del turno de chat: sweep de recuperación.

Un reinicio del api-server descarta la respuesta del equipo (corre detached en
proceso) mientras el mensaje del usuario SÍ es durable. `resume_pending_replies`
reanuda al arranque solo las conversaciones cuyo ÚLTIMO mensaje es de usuario y está
estancado (> umbral): ni las frescas (aún en curso) ni las ya respondidas.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


@pytest.fixture()
def _configured(
    alembic_config,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    monkeypatch.setenv("API_SERVER_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    yield
    reset_engine_cache()
    get_settings.cache_clear()


async def _msg(conn, tenant, conv, user, kind: str, created: datetime) -> None:
    await conn.execute(
        "INSERT INTO messages (id, tenant_id, conversation_id, author_kind,"
        " author_user_id, content, mode, attachments, is_summary, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, $5, 'hi', 'planning', '[]'::jsonb, false, $6, $6)",
        uuid4(),
        tenant,
        conv,
        kind,
        user if kind == "user" else None,
        created,
    )


async def _seed(dsn: str) -> dict[str, UUID]:
    now = datetime.now(UTC)
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "user": uuid4(),
        "stale": uuid4(),
        "fresh": uuid4(),
        "answered": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE messages, conversations, projects, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org', 'org-resume')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'u@resume.test', 'x')",
            ids["user"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, status, is_template)"
            " VALUES ($1, $2, 'P', 'p', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        for key in ("stale", "fresh", "answered"):
            await conn.execute(
                "INSERT INTO conversations (id, tenant_id, project_id, current_mode)"
                " VALUES ($1, $2, $3, 'planning')",
                ids[key],
                ids["tenant"],
                ids["project"],
            )
        tid, uid = ids["tenant"], ids["user"]
        t60 = now - timedelta(seconds=60)
        t55 = now - timedelta(seconds=55)
        # stale: last message is a user message from 60s ago → resume.
        await _msg(conn, tid, ids["stale"], uid, "user", t60)
        # fresh: last message is a user message from NOW → still in-process, skip.
        await _msg(conn, tid, ids["fresh"], uid, "user", now)
        # answered: user 60s ago THEN a system reply 55s ago → latest not user, skip.
        await _msg(conn, tid, ids["answered"], uid, "user", t60)
        await _msg(conn, tid, ids["answered"], uid, "system", t55)
    finally:
        await conn.close()
    return ids


class _FakeRedis:
    def __init__(self) -> None:
        self.locked = False

    async def set(self, *_a, **_k) -> bool:  # nx single-flight lock → grant once
        if self.locked:
            return False
        self.locked = True
        return True


@pytest.mark.asyncio
async def test_resume_only_stale_unanswered(
    _configured, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_server.chat import responder

    ids = await _seed(migrations_pg_dsn)

    recorded: list[UUID] = []

    async def _recorder(*, conversation_id, tenant_id, mode, vault, redis) -> None:
        recorded.append(conversation_id)

    monkeypatch.setattr(responder, "respond_to_conversation", _recorder)

    resumed = await responder.resume_pending_replies(vault=None, redis=_FakeRedis())
    # Only the stale, unanswered conversation is resumed.
    assert resumed == 1
    await asyncio.gather(*list(responder._PENDING_REPLIES))
    assert recorded == [ids["stale"]]


@pytest.mark.asyncio
async def test_resume_single_flight_lock(
    _configured, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_server.chat import responder

    await _seed(migrations_pg_dsn)
    monkeypatch.setattr(responder, "respond_to_conversation", lambda **_k: asyncio.sleep(0))

    shared = _FakeRedis()
    first = await responder.resume_pending_replies(vault=None, redis=shared)
    second = await responder.resume_pending_replies(vault=None, redis=shared)
    # The lock grants once: a concurrent worker's sweep is a no-op.
    assert first == 1
    assert second == 0
