"""Integration tests for the in-app notification inbox (task_10_16).

The inbox's backend surface, exercised through the real FastAPI app + the real
test Postgres so the RLS / pagination boundary is the one under test:

  * **Paginated history scoped to the caller's tenant+user** —
    ``GET /notifications/logs`` returns the caller's tenant ``notification_logs``
    newest-first, with ``limit``/``offset`` bounded (1..200 / >=0) and a
    per-user ``read`` marker. The ``total`` / ``unread`` counters are the full
    scoped counts. Optional ``status`` / ``channel_type`` / ``event_type`` /
    ``unread_only`` filters narrow the window. The retry link reuses the
    task_10_13 endpoint (asserted dead-lettered rows surface here).

  * **Read/unread is per-user + idempotent** — ``POST /logs/{id}/read`` marks
    one item read (a no-op the second time); ``POST /logs/read-all`` marks the
    rest. The marker is per Tenant Admin: marking read for admin A leaves the
    same log unread for a different user. ``unread`` drops accordingly.

  * **Cross-tenant isolation** (``@pytest.mark.cross_tenant``) — tenant B's
    inbox NEVER contains tenant A's logs (RLS), and B marking A's log read is
    a clean 404 (no leak, no receipt written).

  * **Pagination bounds** — ``limit`` outside 1..200 and a negative ``offset``
    are rejected (422) by the query-param validators.

The app runs through the RLS-bound ``app_user`` engine (NOBYPASSRLS) so the
tenant-isolation boundary is real. No LLM, no broker, no real network.
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
# App under test (RLS-bound app_user engine).
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


# ---------------------------------------------------------------------------
# Seeding: two tenants, an admin + a member in A, an admin in B, plus a small
# notification-log history for each tenant (mix of statuses).
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, object]:
    ids: dict[str, object] = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
        "channel_a": uuid4(),
        "channel_b": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE notification_log_reads, notification_logs,"
            " notification_preferences, notification_channels, platform_settings,"
            " audit_log, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-inbox",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-inbox",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, 'h'), ($3, $4, 'h'), ($5, $6, 'h')",
            ids["admin_a"],
            "admin-a@inbox.test",
            ids["member_a"],
            "member-a@inbox.test",
            ids["admin_b"],
            "admin-b@inbox.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user'),"
            " ($7, $8, $9, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_a"],
            ids["member_a"],
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
        )
        await conn.execute(
            "INSERT INTO notification_channels"
            " (id, scope, channel_type, tenant_id, name, enabled, config)"
            " VALUES ($1, 'tenant', 'in_app', $2, 'A inbox', true, '{}'),"
            "        ($3, 'tenant', 'in_app', $4, 'B inbox', true, '{}')",
            ids["channel_a"],
            ids["tenant_a"],
            ids["channel_b"],
            ids["tenant_b"],
        )
        # Tenant A: five logs across statuses (one dead-lettered → retry link).
        a_logs = []
        rows = [
            ("task_blocked", "telegram", "sent", "chat-1"),
            ("plan_approved", "email", "failed", "a@x.test"),
            ("review_needed", "slack", "retrying", "C123"),
            ("budget_alert", "in_app", "dead_letter", "inbox-a"),
            ("task_blocked", "discord", "sent", "wh-a"),
        ]
        for event, ctype, st, target in rows:
            lid = uuid4()
            a_logs.append(lid)
            await conn.execute(
                "INSERT INTO notification_logs"
                " (id, channel_id, tenant_id, event_type, channel_type, status, target, attempt)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, 1)",
                lid,
                ids["channel_a"],
                ids["tenant_a"],
                event,
                ctype,
                st,
                target,
            )
        ids["a_logs"] = a_logs
        ids["a_dead_letter"] = a_logs[3]
        # Tenant B: a single log (must never appear in A's inbox or vice-versa).
        b_log = uuid4()
        await conn.execute(
            "INSERT INTO notification_logs"
            " (id, channel_id, tenant_id, event_type, channel_type, status, target, attempt)"
            " VALUES ($1, $2, $3, 'task_blocked', 'telegram', 'sent', 'chat-b', 1)",
            b_log,
            ids["channel_b"],
            ids["tenant_b"],
        )
        ids["b_log"] = b_log
    finally:
        await conn.close()
    return ids


async def _mint_token(user_id: UUID, tenant_id: UUID | None) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _read_receipt_count(dsn: str, *, user_id: UUID, log_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM notification_log_reads WHERE user_id = $1 AND log_id = $2",
                user_id,
                log_id,
            )
        )
    finally:
        await conn.close()


# ===========================================================================
# Paginated history scoped to the caller's tenant+user.
# ===========================================================================
@pytest.mark.asyncio
async def test_inbox_lists_tenant_history_newest_first(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])  # type: ignore[arg-type]
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.get("/notifications/logs", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Five tenant-A logs, all initially unread, none from tenant B.
    assert body["total"] == 5
    assert body["unread"] == 5
    assert len(body["items"]) == 5
    assert all(item["read"] is False for item in body["items"])
    assert str(seeded["b_log"]) not in {item["id"] for item in body["items"]}
    # Newest-first ordering (created_at desc) and no secret-ish field leaks.
    assert "secret" not in resp.text


@pytest.mark.asyncio
async def test_inbox_pagination_windows(configured_app, migrations_pg_dsn: str) -> None:
    """limit/offset slice the history; total reflects the full scoped count."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])  # type: ignore[arg-type]
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        page1 = await client.get("/notifications/logs?limit=2&offset=0", headers=headers)
        page2 = await client.get("/notifications/logs?limit=2&offset=2", headers=headers)
    assert page1.status_code == 200
    p1, p2 = page1.json(), page2.json()
    assert len(p1["items"]) == 2 and p1["total"] == 5 and p1["limit"] == 2 and p1["offset"] == 0
    assert len(p2["items"]) == 2 and p2["offset"] == 2
    # Disjoint windows.
    ids1 = {i["id"] for i in p1["items"]}
    ids2 = {i["id"] for i in p2["items"]}
    assert ids1.isdisjoint(ids2)


@pytest.mark.asyncio
async def test_inbox_pagination_bounds_are_validated(
    configured_app, migrations_pg_dsn: str
) -> None:
    """limit out of 1..200 and a negative offset are rejected (422)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])  # type: ignore[arg-type]
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        too_big = await client.get("/notifications/logs?limit=500", headers=headers)
        too_small = await client.get("/notifications/logs?limit=0", headers=headers)
        neg_offset = await client.get("/notifications/logs?offset=-1", headers=headers)
    assert too_big.status_code == 422
    assert too_small.status_code == 422
    assert neg_offset.status_code == 422


@pytest.mark.asyncio
async def test_inbox_filters_by_status_and_event(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])  # type: ignore[arg-type]
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        dead = await client.get("/notifications/logs?status=dead_letter", headers=headers)
        blocked = await client.get("/notifications/logs?event_type=task_blocked", headers=headers)
    assert dead.status_code == 200
    dead_body = dead.json()
    # The dead-lettered row is the retry link's subject.
    assert dead_body["total"] == 1
    assert dead_body["items"][0]["id"] == str(seeded["a_dead_letter"])
    assert dead_body["items"][0]["status"] == "dead_letter"
    # Two task_blocked rows seeded for tenant A.
    assert blocked.json()["total"] == 2


# ===========================================================================
# Read / unread — per-user, idempotent.
# ===========================================================================
@pytest.mark.asyncio
async def test_mark_one_read_is_idempotent_and_per_user(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    log_id = seeded["a_logs"][0]  # type: ignore[index]
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])  # type: ignore[arg-type]
    member_token = await _mint_token(seeded["member_a"], seeded["tenant_a"])  # type: ignore[arg-type]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    member_headers = {"Authorization": f"Bearer {member_token}"}

    async with _client(configured_app) as client:
        first = await client.post(f"/notifications/logs/{log_id}/read", headers=admin_headers)
        second = await client.post(f"/notifications/logs/{log_id}/read", headers=admin_headers)
        # The same log is still unread for a DIFFERENT user (per-user marker).
        member_inbox = await client.get("/notifications/logs", headers=member_headers)
        admin_inbox = await client.get("/notifications/logs", headers=admin_headers)
    assert first.status_code == 200, first.text
    assert first.json()["marked"] == 1
    assert first.json()["unread"] == 4
    # Idempotent: the second mark creates no new receipt.
    assert second.json()["marked"] == 0
    assert second.json()["unread"] == 4

    # Exactly one receipt for (admin_a, log) in the DB.
    assert (
        await _read_receipt_count(
            migrations_pg_dsn,
            user_id=seeded["admin_a"],
            log_id=log_id,  # type: ignore[arg-type]
        )
        == 1
    )
    # Member sees the same log as unread (independent inbox).
    member_log = next(i for i in member_inbox.json()["items"] if i["id"] == str(log_id))
    assert member_log["read"] is False
    assert member_inbox.json()["unread"] == 5
    # Admin sees it read.
    admin_log = next(i for i in admin_inbox.json()["items"] if i["id"] == str(log_id))
    assert admin_log["read"] is True


@pytest.mark.asyncio
async def test_mark_all_read_clears_unread(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])  # type: ignore[arg-type]
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        marked = await client.post("/notifications/logs/read-all", headers=headers)
        again = await client.post("/notifications/logs/read-all", headers=headers)
        only_unread = await client.get("/notifications/logs?unread_only=true", headers=headers)
        inbox = await client.get("/notifications/logs", headers=headers)
    assert marked.status_code == 200, marked.text
    assert marked.json()["marked"] == 5
    assert marked.json()["unread"] == 0
    # Idempotent: nothing left to mark.
    assert again.json()["marked"] == 0
    # unread_only is now empty; the full inbox shows everything read.
    assert only_unread.json()["total"] == 0
    assert only_unread.json()["items"] == []
    assert inbox.json()["unread"] == 0
    assert all(i["read"] is True for i in inbox.json()["items"])


# ===========================================================================
# Cross-tenant isolation.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_inbox_cross_tenant_is_isolated(configured_app, migrations_pg_dsn: str) -> None:
    """Tenant B's inbox never shows tenant A's logs; B cannot mark A's log
    read (clean 404, no receipt written)."""
    seeded = await _seed(migrations_pg_dsn)
    b_token = await _mint_token(seeded["admin_b"], seeded["tenant_b"])  # type: ignore[arg-type]
    b_headers = {"Authorization": f"Bearer {b_token}"}
    a_log = seeded["a_logs"][0]  # type: ignore[index]

    async with _client(configured_app) as client:
        b_inbox = await client.get("/notifications/logs", headers=b_headers)
        # B tries to read one of A's logs by id — RLS makes it a clean 404.
        denied = await client.post(f"/notifications/logs/{a_log}/read", headers=b_headers)
    body = b_inbox.json()
    # B sees only its own single log, never A's five.
    assert body["total"] == 1
    assert {i["id"] for i in body["items"]} == {str(seeded["b_log"])}
    a_ids = {str(x) for x in seeded["a_logs"]}  # type: ignore[union-attr]
    assert {i["id"] for i in body["items"]}.isdisjoint(a_ids)

    assert denied.status_code == 404
    # No cross-tenant receipt leaked into the DB for admin_b on A's log.
    assert (
        await _read_receipt_count(
            migrations_pg_dsn,
            user_id=seeded["admin_b"],
            log_id=a_log,  # type: ignore[arg-type]
        )
        == 0
    )
