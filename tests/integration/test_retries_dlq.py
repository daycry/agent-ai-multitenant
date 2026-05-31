"""Integration tests for exponential retries + DLQ + manual retry (task_10_13).

Three layers of guarantee, all without a live broker and without any real
network (channels are MOCKED via a registered fake adapter, exactly the
established Phase A/B dispatcher pattern in ``test_dispatcher.py``):

  * **Exponential backoff is bounded + growing.** ``compute_backoff`` produces a
    non-decreasing, jitter-bounded, ``max_backoff``-clamped schedule — the math
    the Celery task uses to pick each ``self.retry`` countdown. No magic
    numbers: every knob is a :class:`Settings` field.

  * **Automatic retry → success, and exhaustion → dead-letter.** Driving the
    Celery ``send_notification`` task in EAGER mode against the real Postgres +
    a real Redis DLQ stream: a transient failure that clears on the Nth attempt
    ends ``sent`` (the intervening attempts logged ``retrying``); a failure that
    never clears exhausts ``max_retries`` and lands ``dead_letter`` (a
    ``notification_logs`` row with status=dead_letter AND a DLQ stream entry) —
    never an unbounded retry loop.

  * **Manual retry endpoint is RBAC-gated, RLS-scoped, audited + idempotent.**
    ``POST /notifications/logs/{id}/retry`` re-enqueues a dead-lettered log: a
    ``tenant_admin`` succeeds (a fresh ``queued`` row is written + the send is
    re-enqueued onto the broker + an ``audit_log`` row is appended), a plain
    ``tenant_user`` is 403, retrying a NON-dead-lettered log is 409, and the
    re-enqueue flips the source row out of ``dead_letter`` so a double-click
    does not double-send (idempotent). ``@pytest.mark.cross_tenant``: tenant B
    cannot retry tenant A's dead-lettered log (clean 404 via RLS).

The dispatcher connects as the BYPASSRLS ``migrations_user`` role (like
``apps/workers``) because it legitimately delivers across tenants; the
api-server endpoint runs through the RLS-bound ``app_user`` engine so the
tenant-isolation boundary is the one under test.
"""

from __future__ import annotations

import asyncio
import itertools
import os
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from notification_dispatcher.adapters import (
    ChannelMessage,
    DeliveryResult,
    InAppAdapter,
    register_adapter,
)
from notification_dispatcher.config import Settings
from notification_dispatcher.retry import compute_backoff
from redis.asyncio import Redis
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
_PG_PORT = int(os.environ.get("TEST_PG_PORT", "15432"))
_PG_MIG_USER = os.environ.get("TEST_PG_MIGRATIONS_USER", "migrations_user")
_PG_MIG_PASSWORD = os.environ.get("TEST_PG_MIGRATIONS_PASSWORD", "changeme-migrations-dev-only")
_PG_TEST_DB = os.environ.get("TEST_PG_DB_NAME", "agentic_platform_test")
_TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")

_MAX_RETRIES = 3
_DLQ_STREAM = "dlq:notifications:retries-test"


# ===========================================================================
# compute_backoff — pure exponential-backoff math (no Celery / DB / network).
# ===========================================================================
def test_backoff_is_exponential_and_growing() -> None:
    """With jitter off, successive retries wait roughly 2x longer, clamped."""
    kw = {"base_backoff_s": 2.0, "max_backoff_s": 600.0, "jitter": 0.0}
    delays = [compute_backoff(n, **kw) for n in range(5)]
    # base * 2**n: 2, 4, 8, 16, 32 — strictly growing.
    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0]
    assert all(b > a for a, b in itertools.pairwise(delays))


def test_backoff_is_clamped_to_max() -> None:
    """A large retry count never exceeds max_backoff (never unbounded)."""
    delay = compute_backoff(50, base_backoff_s=2.0, max_backoff_s=600.0, jitter=0.0)
    assert delay == 600.0


def test_backoff_jitter_stays_within_window() -> None:
    """Full-jitter keeps the delay inside [capped*(1-jitter), capped]."""
    for _ in range(200):
        d = compute_backoff(3, base_backoff_s=2.0, max_backoff_s=600.0, jitter=0.5)
        # capped = 2 * 2**3 = 16; window [8, 16].
        assert 8.0 <= d <= 16.0


# ===========================================================================
# Fake adapters — flaky (fail N-1 times then succeed) / always-fail.
# ===========================================================================
class _FlakyAdapter:
    """An ``in_app`` adapter that fails the first ``fail_times`` sends then
    succeeds — models a transient channel/provider outage that clears."""

    channel_type = "in_app"

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            return DeliveryResult(ok=False, error=f"transient 503 (call {self.calls})")
        return DeliveryResult(ok=True, provider_message_id="recovered")


class _AlwaysFailAdapter:
    """An ``in_app`` adapter that always reports a transient failure."""

    channel_type = "in_app"

    def __init__(self) -> None:
        self.calls = 0

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        self.calls += 1
        return DeliveryResult(ok=False, error="provider permanently down")


@pytest.fixture(autouse=True)
def _restore_inapp_adapter():
    yield
    register_adapter(InAppAdapter())


# ---------------------------------------------------------------------------
# Dispatcher Settings + Celery eager mode (no broker, no real sleeps).
# ---------------------------------------------------------------------------
def _dispatch_settings() -> Settings:
    return Settings(
        database_url=_migrations_async_url(),
        events_redis_url=_TEST_REDIS_URL,
        dead_letter_stream=_DLQ_STREAM,
        max_retries=_MAX_RETRIES,
        # Tiny backoff + no jitter so eager retries don't actually wait.
        retry_base_backoff_s=0.001,
        retry_max_backoff_s=0.001,
        retry_jitter=0.0,
        environment="dev",
    )


def _migrations_async_url() -> str:
    return (
        f"postgresql+asyncpg://{_PG_MIG_USER}:{_PG_MIG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{_PG_TEST_DB}"
    )


def _sync_dsn() -> str:
    return f"postgresql://{_PG_MIG_USER}:{_PG_MIG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_PG_TEST_DB}"


@pytest.fixture()
def eager_celery():
    """Run the dispatcher Celery app eagerly so ``self.retry`` retries inline
    (no broker) — and point the task wrapper's settings at the test DB/Redis.

    ``task_eager_propagates=False`` so an exhausted-retries failure surfaces as
    a captured EagerResult exception instead of bubbling, mirroring how a real
    worker records the terminal failure.
    """
    import notification_dispatcher.tasks as tasks_mod
    from notification_dispatcher.celery_app import app as celery_app

    prev_eager = celery_app.conf.task_always_eager
    prev_prop = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False

    original_get = tasks_mod.get_settings
    tasks_mod.get_settings = _dispatch_settings  # type: ignore[assignment]
    try:
        yield tasks_mod
    finally:
        tasks_mod.get_settings = original_get
        celery_app.conf.task_always_eager = prev_eager
        celery_app.conf.task_eager_propagates = prev_prop


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


# ---------------------------------------------------------------------------
# Seeding helpers.
# ---------------------------------------------------------------------------
async def _seed_two_tenants_with_channels(dsn: str) -> tuple[UUID, UUID, UUID, UUID]:
    """Two tenants, one enabled ``in_app`` channel each. Returns
    (tenant_a, tenant_b, channel_a, channel_b)."""
    tenant_a, tenant_b = uuid4(), uuid4()
    channel_a, channel_b = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE notification_logs, notification_preferences,"
            " notification_channels, audit_log, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a-retry",
            tenant_b,
            "Tenant B",
            "tenant-b-retry",
        )
        await conn.execute(
            "INSERT INTO notification_channels"
            " (id, scope, channel_type, tenant_id, name, enabled, config)"
            " VALUES ($1, 'tenant', 'in_app', $2, 'A inbox', true, '{\"target\": \"inbox-a\"}'),"
            "        ($3, 'tenant', 'in_app', $4, 'B inbox', true, '{\"target\": \"inbox-b\"}')",
            channel_a,
            tenant_a,
            channel_b,
            tenant_b,
        )
    finally:
        await conn.close()
    return tenant_a, tenant_b, channel_a, channel_b


async def _logs_for_channel(dsn: str, *, channel_id: UUID) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(
            "SELECT status, attempt, event_type, error FROM notification_logs"
            " WHERE channel_id = $1 ORDER BY attempt, created_at",
            channel_id,
        )
    finally:
        await conn.close()


async def _clear_stream(url: str, stream: str) -> None:
    redis: Redis = Redis.from_url(url, decode_responses=True)
    try:
        await redis.delete(stream)
    finally:
        await redis.aclose()


async def _read_stream(url: str, stream: str) -> list:
    redis: Redis = Redis.from_url(url, decode_responses=True)
    try:
        return await redis.xrange(stream)
    finally:
        await redis.aclose()


# ===========================================================================
# Automatic retry: a transient failure retries with growing backoff, succeeds.
# ===========================================================================
def test_transient_failure_retries_then_succeeds(schema_at_head, eager_celery) -> None:
    from notification_dispatcher.tasks import SendRequest

    tasks_mod = eager_celery
    tenant_a, _tb, channel_a, _cb = asyncio.run(_seed_two_tenants_with_channels(_sync_dsn()))
    asyncio.run(_clear_stream(_TEST_REDIS_URL, _DLQ_STREAM))

    # Fail the first two attempts, then recover on the third.
    adapter = _FlakyAdapter(fail_times=2)
    register_adapter(adapter)

    request = SendRequest(
        channel_id=str(channel_a),
        event_type="task_blocked",
        tenant_id=str(tenant_a),
        body="Task X is blocked.",
    ).as_dict()

    result = tasks_mod.send_notification.apply(args=[request])
    payload = result.get()

    assert payload["status"] == "sent"
    assert payload["attempt"] == 3  # recovered on the 3rd attempt
    assert adapter.calls == 3

    # Two ``retrying`` rows (attempts 1, 2) + one ``sent`` row (attempt 3).
    rows = asyncio.run(_logs_for_channel(_sync_dsn(), channel_id=channel_a))
    statuses = [(r["attempt"], r["status"]) for r in rows]
    assert statuses == [(1, "retrying"), (2, "retrying"), (3, "sent")]

    # A recovered send is NOT dead-lettered.
    assert asyncio.run(_read_stream(_TEST_REDIS_URL, _DLQ_STREAM)) == []


# ===========================================================================
# Exhausting retries dead-letters (status=dead_letter + DLQ entry).
# ===========================================================================
def test_exhausting_retries_dead_letters(schema_at_head, eager_celery) -> None:
    from notification_dispatcher.tasks import SendRequest

    tasks_mod = eager_celery
    tenant_a, _tb, channel_a, _cb = asyncio.run(_seed_two_tenants_with_channels(_sync_dsn()))
    asyncio.run(_clear_stream(_TEST_REDIS_URL, _DLQ_STREAM))

    adapter = _AlwaysFailAdapter()
    register_adapter(adapter)

    request = SendRequest(
        channel_id=str(channel_a),
        event_type="budget_alert",
        tenant_id=str(tenant_a),
        body="Budget exceeded.",
    ).as_dict()

    result = tasks_mod.send_notification.apply(args=[request])
    # Retries exhausted -> the terminal attempt re-raises (captured, not propagated).
    assert result.failed()

    # max_retries=3 -> 1 initial + 3 retries = 4 attempts.
    assert adapter.calls == _MAX_RETRIES + 1

    rows = asyncio.run(_logs_for_channel(_sync_dsn(), channel_id=channel_a))
    statuses = [(r["attempt"], r["status"]) for r in rows]
    # attempts 1..max_retries are ``retrying``; the last is ``dead_letter``.
    assert statuses == [
        (1, "retrying"),
        (2, "retrying"),
        (3, "retrying"),
        (4, "dead_letter"),
    ]

    # And the send was parked on the DLQ stream (operator visibility).
    entries = asyncio.run(_read_stream(_TEST_REDIS_URL, _DLQ_STREAM))
    assert len(entries) == 1
    _entry_id, fields = entries[0]
    assert fields["task"] == "notification_dispatcher.send_notification"
    assert fields["channel_id"] == str(channel_a)
    assert fields["event_type"] == "budget_alert"


# ===========================================================================
# Manual retry endpoint — RBAC-gated, RLS-scoped, audited, idempotent.
# ===========================================================================
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
    from api_server.celery_client import reset_celery_client_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    reset_celery_client_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        reset_celery_client_cache()
        get_settings.cache_clear()


async def _seed_users_and_dead_letter(dsn: str) -> dict[str, UUID]:
    """Two tenants, an admin + a plain member in A, an admin in B, an A
    in_app channel, and a DEAD-LETTERED log row owned by tenant A."""
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
        "channel_a": uuid4(),
        "log_a": uuid4(),
        "log_a_sent": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE notification_logs, notification_preferences,"
            " notification_channels, audit_log, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-mr",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-mr",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@mr.test",
            "h",
            ids["member_a"],
            "member-a@mr.test",
            "h",
            ids["admin_b"],
            "admin-b@mr.test",
            "h",
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
            " VALUES ($1, 'tenant', 'in_app', $2, 'A inbox', true, '{\"target\": \"inbox-a\"}')",
            ids["channel_a"],
            ids["tenant_a"],
        )
        # A dead-lettered log (the manual-retry subject) + a benign sent log.
        await conn.execute(
            "INSERT INTO notification_logs"
            " (id, channel_id, tenant_id, event_type, channel_type, status, target, attempt, error)"
            " VALUES"
            " ($1, $2, $3, 'budget_alert', 'in_app', 'dead_letter', 'inbox-a', 4, 'provider down'),"
            " ($4, $2, $3, 'task_blocked', 'in_app', 'sent', 'inbox-a', 1, NULL)",
            ids["log_a"],
            ids["channel_a"],
            ids["tenant_a"],
            ids["log_a_sent"],
        )
    finally:
        await conn.close()
    return ids


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture()
def captured_enqueues(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture the dispatcher re-enqueue instead of hitting a live broker."""
    import api_server.routers.notifications as notif_router

    sent: list[dict] = []

    async def _fake_enqueue(send_request: dict, *, queue: str) -> bool:
        sent.append({"request": send_request, "queue": queue})
        return True

    monkeypatch.setattr(notif_router, "enqueue_notification_send", _fake_enqueue)
    return sent


async def _audit_count(dsn: str, tenant_id: UUID, action: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM audit_log WHERE tenant_id = $1 AND action = $2",
            tenant_id,
            action,
        )
        return int(row["n"])
    finally:
        await conn.close()


async def _log_status(dsn: str, log_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT status FROM notification_logs WHERE id = $1", log_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_manual_retry_reenqueues_dead_letter(
    configured_app, migrations_pg_dsn: str, captured_enqueues: list[dict]
) -> None:
    """A tenant_admin re-enqueues a dead-lettered log: a fresh queued row is
    written, the send is re-enqueued, and an audit row is appended."""
    seeded = await _seed_users_and_dead_letter(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(f"/notifications/logs/{seeded['log_a']}/retry", headers=headers)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"

    # The send was re-enqueued onto the dispatcher's default lane.
    assert len(captured_enqueues) == 1
    enq = captured_enqueues[0]
    assert enq["request"]["channel_id"] == str(seeded["channel_a"])
    assert enq["request"]["event_type"] == "budget_alert"
    assert enq["request"]["tenant_id"] == str(seeded["tenant_a"])

    # A fresh append-only ``queued`` row was written (attempt incremented).
    new_log_id = UUID(body["log_id"])
    assert await _log_status(migrations_pg_dsn, new_log_id) == "queued"

    # The source dead-lettered row was flipped out of dead_letter (idempotency).
    assert await _log_status(migrations_pg_dsn, seeded["log_a"]) != "dead_letter"

    # Mandatory audit row.
    assert await _audit_count(migrations_pg_dsn, seeded["tenant_a"], "notification.retry") == 1


@pytest.mark.asyncio
async def test_manual_retry_requires_tenant_admin(
    configured_app, migrations_pg_dsn: str, captured_enqueues: list[dict]
) -> None:
    """A plain tenant_user cannot trigger a manual retry (403, no enqueue)."""
    seeded = await _seed_users_and_dead_letter(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(f"/notifications/logs/{seeded['log_a']}/retry", headers=headers)
    assert resp.status_code == 403
    assert captured_enqueues == []
    assert await _log_status(migrations_pg_dsn, seeded["log_a"]) == "dead_letter"


@pytest.mark.asyncio
async def test_manual_retry_rejects_non_dead_letter(
    configured_app, migrations_pg_dsn: str, captured_enqueues: list[dict]
) -> None:
    """Retrying a log that is not dead-lettered is a 409 (nothing to retry)."""
    seeded = await _seed_users_and_dead_letter(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/notifications/logs/{seeded['log_a_sent']}/retry", headers=headers
        )
    assert resp.status_code == 409
    assert captured_enqueues == []


@pytest.mark.asyncio
async def test_manual_retry_is_idempotent(
    configured_app, migrations_pg_dsn: str, captured_enqueues: list[dict]
) -> None:
    """A double-click does not double-send: the second retry of the now-flipped
    dead-letter row is a 409 and enqueues nothing more."""
    seeded = await _seed_users_and_dead_letter(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        first = await client.post(f"/notifications/logs/{seeded['log_a']}/retry", headers=headers)
        second = await client.post(f"/notifications/logs/{seeded['log_a']}/retry", headers=headers)
    assert first.status_code == 202, first.text
    assert second.status_code == 409
    # Exactly one live re-enqueue.
    assert len(captured_enqueues) == 1


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_manual_retry_cross_tenant_is_404(
    configured_app, migrations_pg_dsn: str, captured_enqueues: list[dict]
) -> None:
    """Tenant B cannot retry tenant A's dead-lettered log — RLS makes it a
    clean 404 (no cross-tenant leak, no enqueue)."""
    seeded = await _seed_users_and_dead_letter(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_b"], seeded["tenant_b"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(f"/notifications/logs/{seeded['log_a']}/retry", headers=headers)
    assert resp.status_code == 404
    assert captured_enqueues == []
    # Tenant A's row is untouched.
    assert await _log_status(migrations_pg_dsn, seeded["log_a"]) == "dead_letter"
