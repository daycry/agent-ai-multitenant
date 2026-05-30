"""Integration tests for the notification-dispatcher service (Plan 10 task_10_02).

Drives the dispatcher end to end against the real Postgres (the Plan 10
Fase A tables created by migration 0045) + a real Redis dead-letter
stream:

  - a queued notification over an enabled channel **dispatches and logs
    ``sent``** (the default ``in_app`` no-op adapter delivers it),
  - a **failing send logs ``failed`` and dead-letters** onto
    ``dlq:notifications`` — never a blind auto-retry,
  - a **cross-tenant ownership mismatch is rejected** at the worker
    boundary: a Celery payload that pairs tenant A with tenant B's channel
    raises ``CrossTenantNotificationError`` and writes NO log row
    (``@pytest.mark.cross_tenant``).

The dispatcher connects as the BYPASSRLS ``migrations_user`` role (exactly
like ``apps/workers``) because it legitimately delivers across tenants —
so RLS cannot catch a tampered payload and the ``channel.tenant_id ==
request.tenant_id`` boundary check is the guarantee under test.

Fixture wiring mirrors ``test_marketplace_migration.py``: bring the schema
to head, seed via the BYPASSRLS migrations role, then drive the async
dispatch core with an injected sessionmaker + a registered fake adapter so
we exercise the real lookup → ownership-check → adapter → log path without
the Celery broker.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from notification_dispatcher.adapters import (
    ChannelMessage,
    ChannelSendError,
    DeliveryResult,
    InAppAdapter,
    register_adapter,
)
from notification_dispatcher.config import Settings
from notification_dispatcher.tasks import (
    CrossTenantNotificationError,
    SendRequest,
    _dispatch,
    send_notification,
)
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
_PG_PORT = int(os.environ.get("TEST_PG_PORT", "15432"))
_PG_MIG_USER = os.environ.get("TEST_PG_MIGRATIONS_USER", "migrations_user")
_PG_MIG_PASSWORD = os.environ.get("TEST_PG_MIGRATIONS_PASSWORD", "changeme-migrations-dev-only")
_PG_TEST_DB = os.environ.get("TEST_PG_DB_NAME", "agentic_platform_test")
_TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


def _migrations_async_url() -> str:
    return (
        f"postgresql+asyncpg://{_PG_MIG_USER}:{_PG_MIG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{_PG_TEST_DB}"
    )


def _test_settings() -> Settings:
    """Dispatcher Settings pinned to the test DB + test Redis DB 15.

    The dispatcher is BYPASSRLS in production; the migrations role is the
    test analogue (same role the workers integration tests use).
    """
    return Settings(
        database_url=_migrations_async_url(),
        events_redis_url=_TEST_REDIS_URL,
        # Distinct stream so the assertions don't collide with the workers'
        # dlq:executions stream on the shared test Redis DB.
        dead_letter_stream="dlq:notifications:test",
        environment="dev",
    )


# ---------------------------------------------------------------------------
# Fake adapters — record a send / force a failure without any real I/O.
# ---------------------------------------------------------------------------
class _RecordingAdapter:
    """A fake ``in_app`` adapter that records the message it was handed.

    Used to assert the secret never reaches the adapter as plaintext when
    the channel is secretless, and that the dispatch path actually calls
    the adapter.
    """

    channel_type = "in_app"

    def __init__(self) -> None:
        self.sent: list[ChannelMessage] = []

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        self.sent.append(message)
        return DeliveryResult(ok=True, provider_message_id="rec-1")


class _FailingAdapter:
    """A fake ``in_app`` adapter that always reports a terminal failure."""

    channel_type = "in_app"

    async def send(self, message: ChannelMessage) -> DeliveryResult:
        return DeliveryResult(ok=False, error="simulated provider 500")


@pytest.fixture(autouse=True)
def _restore_inapp_adapter():
    """Each test may swap the ``in_app`` adapter; restore the no-op after."""
    yield
    register_adapter(InAppAdapter())


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    """Run migrations to head (sync — ``alembic.command`` owns its loop)."""
    command.upgrade(alembic_config, "head")


async def _seed_two_tenants_with_channels(
    dsn: str,
) -> tuple[UUID, UUID, UUID, UUID]:
    """Two tenants, one enabled ``in_app`` channel each. Returns
    (tenant_a, tenant_b, channel_a, channel_b)."""
    tenant_a, tenant_b = uuid4(), uuid4()
    channel_a, channel_b = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE notification_logs, notification_preferences,"
            " notification_channels, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a-notif",
            tenant_b,
            "Tenant B",
            "tenant-b-notif",
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


async def _count_logs(dsn: str, *, channel_id: UUID) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(
            "SELECT status, tenant_id, event_type, channel_type, target, attempt"
            " FROM notification_logs WHERE channel_id = $1",
            channel_id,
        )
    finally:
        await conn.close()


def _sync_dsn() -> str:
    return f"postgresql://{_PG_MIG_USER}:{_PG_MIG_PASSWORD}" f"@{_PG_HOST}:{_PG_PORT}/{_PG_TEST_DB}"


# ===========================================================================
# Happy path — a queued notification dispatches + logs sent.
# ===========================================================================
@pytest.mark.asyncio
async def test_dispatch_logs_sent(schema_at_head) -> None:
    settings = _test_settings()
    tenant_a, _tenant_b, channel_a, _channel_b = await _seed_two_tenants_with_channels(_sync_dsn())

    recorder = _RecordingAdapter()
    register_adapter(recorder)

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        request = SendRequest(
            channel_id=str(channel_a),
            event_type="task_blocked",
            tenant_id=str(tenant_a),
            body="Task X is blocked.",
        )
        result = await _dispatch(request, settings=settings, sessionmaker=sessionmaker)
    finally:
        await engine.dispose()

    assert result["status"] == "sent"
    assert result["channel_type"] == "in_app"
    assert result["attempt"] == 1

    # The adapter was actually called, with the channel's config target and
    # NO plaintext secret (the channel is secretless).
    assert len(recorder.sent) == 1
    assert recorder.sent[0].target == "inbox-a"
    assert recorder.sent[0].secret is None

    rows = await _count_logs(_sync_dsn(), channel_id=channel_a)
    assert len(rows) == 1
    assert rows[0]["status"] == "sent"
    assert rows[0]["tenant_id"] == tenant_a
    assert rows[0]["event_type"] == "task_blocked"


# ===========================================================================
# Failure path — a failing send logs failed + dead-letters.
#
# NOTE: this test is SYNC (no @pytest.mark.asyncio). The celery task body
# (`send_notification`) calls `asyncio.run` internally; driving it from a
# running asyncio test loop would raise "asyncio.run() cannot be called
# from a running event loop". So we stay synchronous and run the
# DB/Redis seeding + probes through `asyncio.run` in this loop-free thread
# — the same pattern `test_marketplace_migration.py` uses for its sync
# reversibility test.
# ===========================================================================
def test_failed_send_logs_failed_and_dead_letters(schema_at_head) -> None:
    import asyncio

    settings = _test_settings()
    tenant_a, _tb, channel_a, _cb = asyncio.run(_seed_two_tenants_with_channels(_sync_dsn()))

    register_adapter(_FailingAdapter())

    async def _clear_dlq() -> None:
        redis: Redis = Redis.from_url(settings.events_redis_url, decode_responses=True)
        try:
            await redis.delete(settings.dead_letter_stream)
        finally:
            await redis.aclose()

    asyncio.run(_clear_dlq())

    request = SendRequest(
        channel_id=str(channel_a),
        event_type="budget_alert",
        tenant_id=str(tenant_a),
        body="Budget exceeded.",
    ).as_dict()

    # Point the task wrapper's settings at the test DB/Redis. The failing
    # adapter makes _dispatch raise ChannelSendError AFTER committing the
    # 'failed' log row; the task wrapper then dead-letters and re-raises.
    import notification_dispatcher.tasks as tasks_mod

    original_get = tasks_mod.get_settings
    tasks_mod.get_settings = _test_settings  # type: ignore[assignment]
    try:
        with pytest.raises(ChannelSendError):
            send_notification(request)
    finally:
        tasks_mod.get_settings = original_get

    # The failed attempt was recorded as a 'failed' log row.
    rows = asyncio.run(_count_logs(_sync_dsn(), channel_id=channel_a))
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["event_type"] == "budget_alert"

    # And the send was dead-lettered (NOT auto-retried).
    async def _read_dlq() -> list:
        redis: Redis = Redis.from_url(settings.events_redis_url, decode_responses=True)
        try:
            entries = await redis.xrange(settings.dead_letter_stream)
            await redis.delete(settings.dead_letter_stream)
            return entries
        finally:
            await redis.aclose()

    entries = asyncio.run(_read_dlq())
    assert len(entries) == 1
    _entry_id, fields = entries[0]
    assert fields["task"] == "notification_dispatcher.send_notification"
    assert fields["channel_id"] == str(channel_a)
    assert fields["event_type"] == "budget_alert"
    assert "tenant_id" in fields


# ===========================================================================
# Cross-tenant ownership mismatch is rejected at the worker boundary.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_send_rejected(schema_at_head) -> None:
    """A payload pairing tenant A with tenant B's channel must be rejected
    and write NO log row (the BYPASSRLS dispatcher's boundary guarantee)."""
    settings = _test_settings()
    tenant_a, _tenant_b, _channel_a, channel_b = await _seed_two_tenants_with_channels(_sync_dsn())

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        # tenant_a claims tenant_b's channel — cross-tenant attempt.
        request = SendRequest(
            channel_id=str(channel_b),
            event_type="task_blocked",
            tenant_id=str(tenant_a),
            body="Should never be delivered.",
        )
        with pytest.raises(CrossTenantNotificationError):
            await _dispatch(request, settings=settings, sessionmaker=sessionmaker)
    finally:
        await engine.dispose()

    # No log row was written for the cross-tenant channel — the boundary
    # check fired before any send / log.
    rows = await _count_logs(_sync_dsn(), channel_id=channel_b)
    assert rows == []
