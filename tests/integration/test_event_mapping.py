"""Integration tests for the system event → notification mapping (task_10_04).

Drives the dispatcher's event-mapping resolver
(``notification_dispatcher.event_mapping.resolve_event_dispatch``) end to
end against the real Postgres (the Plan 10 Fase A tables created by
migrations 0045 / 0046):

  - a representative event **fans out to the right channels per
    preferences** (an opted-in channel SENDs, a not-subscribed channel of a
    type outside the event's default set is skipped),
  - an **opt-out preference suppresses** the send on that channel
    (the human_10_02 "mute budget_alert on Slack" primitive),
  - a **quiet-hours window defers** the send (computes an ETA past the
    window) rather than dropping it,
  - an **event with zero subscribers is a no-op** (nothing enqueued),
  - **tenant isolation**: a tenant-A event NEVER resolves a tenant-B channel
    even though the dispatcher is BYPASSRLS (``@pytest.mark.cross_tenant``).

Fixture wiring mirrors ``test_dispatcher.py``: bring the schema to head,
seed via the BYPASSRLS migrations role, then drive the pure async resolver
with an injected sessionmaker so we exercise the real channel/preference/
template lookup + decision path without the Celery broker. The resolver is
deterministic because the event carries an injectable ``now`` for the
quiet-hours evaluation.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from notification_dispatcher.config import Settings
from notification_dispatcher.event_mapping import (
    DispatchDecision,
    IncomingEvent,
    resolve_event_dispatch,
)
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


def _sync_dsn() -> str:
    return f"postgresql://{_PG_MIG_USER}:{_PG_MIG_PASSWORD}@{_PG_HOST}:{_PG_PORT}/{_PG_TEST_DB}"


def _test_settings() -> Settings:
    return Settings(
        database_url=_migrations_async_url(),
        events_redis_url=_TEST_REDIS_URL,
        dead_letter_stream="dlq:notifications:test",
        environment="dev",
    )


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    """Run migrations to head (sync — ``alembic.command`` owns its loop)."""
    command.upgrade(alembic_config, "head")


# ---------------------------------------------------------------------------
# Seeding helpers — all writes go through the BYPASSRLS migrations role.
# ---------------------------------------------------------------------------
async def _reset(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "TRUNCATE notification_templates, notification_logs,"
        " notification_preferences, notification_channels,"
        " organizations, users RESTART IDENTITY CASCADE"
    )


async def _add_channel(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID | None,
    channel_type: str,
    name: str,
    target: str,
    enabled: bool = True,
) -> UUID:
    channel_id = uuid4()
    scope = "tenant" if tenant_id is not None else "platform"
    await conn.execute(
        "INSERT INTO notification_channels"
        " (id, scope, channel_type, tenant_id, name, enabled, config)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
        channel_id,
        scope,
        channel_type,
        tenant_id,
        name,
        enabled,
        f'{{"target": "{target}"}}',
    )
    return channel_id


async def _add_pref(
    conn: asyncpg.Connection,
    *,
    tenant_id: UUID | None,
    event_type: str,
    channel_type: str,
    enabled: bool = True,
    quiet_start: int | None = None,
    quiet_end: int | None = None,
    quiet_tz: str | None = None,
) -> None:
    scope = "tenant" if tenant_id is not None else "platform"
    await conn.execute(
        "INSERT INTO notification_preferences"
        " (id, scope, tenant_id, event_type, channel_type, enabled,"
        "  quiet_hours_start, quiet_hours_end, quiet_hours_tz)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        uuid4(),
        scope,
        tenant_id,
        event_type,
        channel_type,
        enabled,
        quiet_start,
        quiet_end,
        quiet_tz,
    )


async def _seed_tenant(conn: asyncpg.Connection, slug: str) -> UUID:
    tenant_id = uuid4()
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
        tenant_id,
        slug.title(),
        slug,
    )
    return tenant_id


async def _resolve(event: IncomingEvent):
    settings = _test_settings()
    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        return await resolve_event_dispatch(event, settings=settings, sessionmaker=sessionmaker)
    finally:
        await engine.dispose()


# ===========================================================================
# Fan-out: an event reaches the right channels per preferences.
# ===========================================================================
@pytest.mark.asyncio
async def test_event_fans_out_to_subscribed_channels(schema_at_head) -> None:
    conn = await asyncpg.connect(_sync_dsn())
    try:
        await _reset(conn)
        tenant = await _seed_tenant(conn, "tenant-fanout")
        # in_app is a default channel for review_requested → fans out with
        # no explicit preference. telegram is opted IN explicitly. email is
        # NOT a default for review_requested and has no preference → skipped.
        ch_inapp = await _add_channel(
            conn, tenant_id=tenant, channel_type="in_app", name="inbox", target="inbox-1"
        )
        ch_tg = await _add_channel(
            conn, tenant_id=tenant, channel_type="telegram", name="tg", target="chat-1"
        )
        await _add_channel(
            conn, tenant_id=tenant, channel_type="email", name="mail", target="a@b.c"
        )
        await _add_pref(
            conn, tenant_id=tenant, event_type="review_requested", channel_type="telegram"
        )
    finally:
        await conn.close()

    plan = await _resolve(
        IncomingEvent(
            event_type="review_requested",
            tenant_id=str(tenant),
            context={"task_title": "Fix auth", "project_name": "Core"},
            locale="en",
        )
    )

    assert plan.no_op is False
    sent = {d.channel_id: d for d in plan.to_send}
    assert ch_inapp in sent  # default fan-out
    assert ch_tg in sent  # explicit opt-in
    # email had neither a preference nor a default → not in the plan at all.
    assert all(d.channel_type != "email" for d in plan.decisions)
    # All sends carry a rendered body (the EN builtin).
    for d in plan.to_send:
        assert d.send_request is not None
        assert "review" in d.send_request["body"].lower()


# ===========================================================================
# Opt-out suppresses the send on that channel.
# ===========================================================================
@pytest.mark.asyncio
async def test_opt_out_suppresses(schema_at_head) -> None:
    conn = await asyncpg.connect(_sync_dsn())
    try:
        await _reset(conn)
        tenant = await _seed_tenant(conn, "tenant-optout")
        ch_inapp = await _add_channel(
            conn, tenant_id=tenant, channel_type="in_app", name="inbox", target="inbox-1"
        )
        # budget_alert defaults to (in_app, email). Opt OUT of in_app.
        await _add_pref(
            conn,
            tenant_id=tenant,
            event_type="budget_alert",
            channel_type="in_app",
            enabled=False,
        )
    finally:
        await conn.close()

    plan = await _resolve(
        IncomingEvent(
            event_type="budget_alert",
            tenant_id=str(tenant),
            context={"plan_name": "Q3"},
            locale="en",
        )
    )

    inapp = next(d for d in plan.decisions if d.channel_id == ch_inapp)
    assert inapp.decision is DispatchDecision.SUPPRESSED
    assert plan.no_op is True  # the only channel was suppressed
    assert not plan.to_send


# ===========================================================================
# Quiet hours defer (not drop) the send.
# ===========================================================================
@pytest.mark.asyncio
async def test_quiet_hours_defer(schema_at_head) -> None:
    conn = await asyncpg.connect(_sync_dsn())
    try:
        await _reset(conn)
        tenant = await _seed_tenant(conn, "tenant-quiet")
        ch_tg = await _add_channel(
            conn, tenant_id=tenant, channel_type="telegram", name="tg", target="chat-1"
        )
        # Quiet hours 22:00 (1320) → 07:00 (420) UTC, opted in.
        await _add_pref(
            conn,
            tenant_id=tenant,
            event_type="task_blocked",
            channel_type="telegram",
            quiet_start=1320,
            quiet_end=420,
            quiet_tz="UTC",
        )
    finally:
        await conn.close()

    # 23:30 UTC — inside the wrap-around window → deferred to 07:00 next day.
    now = datetime(2026, 5, 30, 23, 30, tzinfo=UTC)
    plan = await _resolve(
        IncomingEvent(
            event_type="task_blocked",
            tenant_id=str(tenant),
            context={"task_title": "T", "project_name": "P", "reason": "dep"},
            locale="en",
            now=now,
        )
    )

    tg = next(d for d in plan.decisions if d.channel_id == ch_tg)
    assert tg.decision is DispatchDecision.DEFERRED
    assert tg.eta is not None
    assert tg.eta > now
    # The deferred send still carries a rendered, ready-to-enqueue request.
    assert tg.send_request is not None
    # Window ends 07:00 the next day.
    assert tg.eta.astimezone(UTC).hour == 7
    assert tg.eta.date() == datetime(2026, 5, 31, tzinfo=UTC).date()


@pytest.mark.asyncio
async def test_quiet_hours_outside_window_sends_now(schema_at_head) -> None:
    conn = await asyncpg.connect(_sync_dsn())
    try:
        await _reset(conn)
        tenant = await _seed_tenant(conn, "tenant-quiet-2")
        ch_tg = await _add_channel(
            conn, tenant_id=tenant, channel_type="telegram", name="tg", target="chat-1"
        )
        await _add_pref(
            conn,
            tenant_id=tenant,
            event_type="task_blocked",
            channel_type="telegram",
            quiet_start=1320,
            quiet_end=420,
            quiet_tz="UTC",
        )
    finally:
        await conn.close()

    # 12:00 UTC — outside the window → sends now.
    now = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    plan = await _resolve(
        IncomingEvent(
            event_type="task_blocked",
            tenant_id=str(tenant),
            context={"task_title": "T", "project_name": "P", "reason": "dep"},
            locale="en",
            now=now,
        )
    )
    tg = next(d for d in plan.decisions if d.channel_id == ch_tg)
    assert tg.decision is DispatchDecision.SEND
    assert tg.eta is None


# ===========================================================================
# No subscribers → no-op.
# ===========================================================================
@pytest.mark.asyncio
async def test_no_subscribers_is_no_op(schema_at_head) -> None:
    conn = await asyncpg.connect(_sync_dsn())
    try:
        await _reset(conn)
        tenant = await _seed_tenant(conn, "tenant-empty")
        # An email channel only — plan_approved defaults to (in_app,) and the
        # email channel has no preference, so nothing is subscribed.
        await _add_channel(
            conn, tenant_id=tenant, channel_type="email", name="mail", target="a@b.c"
        )
    finally:
        await conn.close()

    plan = await _resolve(
        IncomingEvent(
            event_type="plan_approved",
            tenant_id=str(tenant),
            context={"plan_name": "X"},
            locale="en",
        )
    )
    assert plan.no_op is True
    assert not plan.to_send


@pytest.mark.asyncio
async def test_unknown_event_is_no_op(schema_at_head) -> None:
    conn = await asyncpg.connect(_sync_dsn())
    try:
        await _reset(conn)
        tenant = await _seed_tenant(conn, "tenant-unknown")
        await _add_channel(conn, tenant_id=tenant, channel_type="in_app", name="inbox", target="i")
    finally:
        await conn.close()

    plan = await _resolve(IncomingEvent(event_type="not_a_real_event", tenant_id=str(tenant)))
    assert plan.no_op is True
    assert plan.decisions == ()


# ===========================================================================
# Tenant isolation — a tenant-A event NEVER resolves a tenant-B channel.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_isolation_event_never_crosses(schema_at_head) -> None:
    conn = await asyncpg.connect(_sync_dsn())
    try:
        await _reset(conn)
        tenant_a = await _seed_tenant(conn, "tenant-iso-a")
        tenant_b = await _seed_tenant(conn, "tenant-iso-b")
        # Both tenants have an identical in_app channel + opt-in for the same
        # event. A tenant-A event must reach ONLY tenant A's channel.
        ch_a = await _add_channel(
            conn, tenant_id=tenant_a, channel_type="in_app", name="inbox", target="a"
        )
        ch_b = await _add_channel(
            conn, tenant_id=tenant_b, channel_type="in_app", name="inbox", target="b"
        )
        for t in (tenant_a, tenant_b):
            await _add_pref(conn, tenant_id=t, event_type="plan_approved", channel_type="in_app")
    finally:
        await conn.close()

    plan = await _resolve(
        IncomingEvent(
            event_type="plan_approved",
            tenant_id=str(tenant_a),
            context={"plan_name": "A's plan"},
            locale="en",
        )
    )

    resolved_ids = {d.channel_id for d in plan.decisions}
    assert ch_a in resolved_ids
    assert ch_b not in resolved_ids  # tenant B's channel was never resolved
    # Every send is scoped to tenant A.
    for d in plan.to_send:
        assert d.send_request is not None
        assert d.send_request["tenant_id"] == str(tenant_a)


# ===========================================================================
# NOTIF-1 (auditoría notificaciones 2026-07-12): el structured del send_request
# debe llevar body + event_type + severity. Antes solo llevaba subject, así que
# (a) WhatsApp rellenaba sus plantillas con body VACÍO (los params se resuelven
# de structured, no de message.body) y (b) slack/teams/discord/webhook perdían
# la metadata de evento y el color por severidad — features muertas de facto.
# ===========================================================================
@pytest.mark.asyncio
async def test_send_request_structured_carries_body_event_type_and_severity(
    schema_at_head,
) -> None:
    conn = await asyncpg.connect(_sync_dsn())
    try:
        await _reset(conn)
        tenant = await _seed_tenant(conn, "tenant-structured")
        await _add_channel(
            conn, tenant_id=tenant, channel_type="telegram", name="tg", target="chat-1"
        )
    finally:
        await conn.close()

    plan = await _resolve(
        IncomingEvent(
            event_type="task_blocked",
            tenant_id=str(tenant),
            context={"task_title": "Fix auth", "project_name": "Core", "severity": "critical"},
            locale="es",
        )
    )

    assert plan.to_send, plan.decisions
    for d in plan.to_send:
        st = d.send_request["structured"]
        assert st["body"] == d.send_request["body"]
        assert st["event_type"] == "task_blocked"
        assert st["severity"] == "critical"
