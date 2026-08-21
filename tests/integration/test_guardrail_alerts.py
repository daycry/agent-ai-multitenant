"""Integration tests for the configurable guardrail ALERTS (Plan 11 task_11_21).

Exercises the tenant-scoped ``guardrail_alert_rules`` table, the evaluator
(``api_server.guardrails.alerts``), the Plan 10 dispatch seam, and the
Tenant-Admin CRUD endpoints end to end against a real Postgres + the
tenant-isolation RLS of migration 0053:

  - crossing the threshold within the window fires EXACTLY ONE alert (the
    alert is dispatched as a ``guardrail_alert`` event via the Plan 10
    notifier — asserted on a fake dispatcher that stands in for the actual
    channel send);
  - staying UNDER the threshold does NOT fire;
  - the threshold / window are CONFIGURABLE (a custom rule fires at its own
    threshold, not a hardcoded one);
  - DEBOUNCE: a second evaluation within the same window does NOT fire a
    second alert (one alert per rule per window);
  - per-tenant isolation (``@pytest.mark.cross_tenant``): tenant A's
    violations never alert tenant B;
  - the default dispatcher enqueues through the Plan 10 path
    (``notification_dispatcher.dispatch_event`` by name);
  - RBAC: a non-admin (plain ``tenant_user``) cannot manage rules (403),
    while a ``tenant_admin`` can.

Fixture wiring mirrors ``test_guardrail_events.py``: migrate to head, seed
two tenants (admin + member each) via the BYPASSRLS migrations role, mint
JWTs, drive the API via AsyncClient. Events are inserted directly (the
recorder/host writes them in production); the evaluator runs on a
tenant-scoped RLS session — the same path the recorder uses.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

from ._partitions import ensure_partition_for

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed: two tenants, each with an admin + a member.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "admin_b": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE guardrail_alert_rules, guardrail_events, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-alerts",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-alerts",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@alerts.test",
            "h",
            ids["member_a"],
            "member-a@alerts.test",
            "h",
            ids["admin_b"],
            "admin-b@alerts.test",
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
    finally:
        await conn.close()
    return ids


async def _insert_event(
    dsn: str,
    *,
    tenant_id: UUID,
    guardrail_type: str = "secret_leakage",
    severity: str = "high",
    created_at: datetime | None = None,
) -> None:
    """Insert one guardrail_events row directly as the BYPASSRLS migrations user."""
    if created_at is not None:
        # `guardrail_events` está particionada por mes y SIN DEFAULT (ADR 0151): el
        # llamante retrofecha (`now - 2h`) y esa fila cae en el mes anterior si el
        # test corre en las dos primeras horas del mes. Ver
        # docs/03-guides/gotchas/sembrar-filas-retrofechadas-en-tabla-particionada.md
        await ensure_partition_for(dsn, "guardrail_events", created_at)
    conn = await asyncpg.connect(dsn)
    try:
        if created_at is None:
            await conn.execute(
                "INSERT INTO guardrail_events"
                " (id, tenant_id, guardrail_type, hook_point, severity, action, detail)"
                " VALUES ($1, $2, $3, 'post_llm', $4, 'redact', 'masked')",
                uuid4(),
                tenant_id,
                guardrail_type,
                severity,
            )
        else:
            await conn.execute(
                "INSERT INTO guardrail_events"
                " (id, tenant_id, guardrail_type, hook_point, severity, action, detail,"
                " created_at)"
                " VALUES ($1, $2, $3, 'post_llm', $4, 'redact', 'masked', $5)",
                uuid4(),
                tenant_id,
                guardrail_type,
                severity,
                created_at,
            )
    finally:
        await conn.close()


async def _insert_rule(
    dsn: str,
    *,
    tenant_id: UUID,
    name: str = "spike",
    threshold: int = 3,
    window_seconds: int = 3600,
    guardrail_type: str | None = None,
    min_severity: str | None = None,
    enabled: bool = True,
    last_fired_at: datetime | None = None,
) -> UUID:
    """Insert one alert rule directly as the BYPASSRLS migrations user."""
    rule_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO guardrail_alert_rules"
            " (id, tenant_id, name, threshold, window_seconds, guardrail_type,"
            " min_severity, enabled, last_fired_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
            rule_id,
            tenant_id,
            name,
            threshold,
            window_seconds,
            guardrail_type,
            min_severity,
            enabled,
            last_fired_at,
        )
    finally:
        await conn.close()
    return rule_id


async def _rule_last_fired(dsn: str, rule_id: UUID) -> datetime | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT last_fired_at FROM guardrail_alert_rules WHERE id = $1", rule_id
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Fixtures (identical wiring to test_guardrail_events.configured_app)
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


async def _mint_token(
    user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False
) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(
        user_id=user_id,
        session_id=sid,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class _FakeDispatcher:
    """Stands in for the Plan 10 notifier — captures the dispatched events
    instead of enqueuing a real ``dispatch_event`` task (mocks the actual
    channel send)."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def dispatch(self, event: dict[str, object]) -> bool:
        self.events.append(event)
        return True


async def _evaluate(tenant_id: UUID, dispatcher, *, now: datetime | None = None):
    """Run the evaluator on a tenant-scoped RLS session (the recorder's path)."""
    from api_server.auth.deps import AuthPrincipal, open_tenant_session
    from api_server.guardrails.alerts import evaluate_tenant_alert_rules

    principal = AuthPrincipal(user_id=uuid4(), session_id=uuid7(), tenant_id=tenant_id)
    async with open_tenant_session(principal) as session:
        return await evaluate_tenant_alert_rules(
            session, tenant_id=tenant_id, dispatcher=dispatcher, now=now
        )


# ===========================================================================
# Crossing the threshold fires exactly ONE alert through the Plan 10 notifier
# ===========================================================================
@pytest.mark.asyncio
async def test_crossing_threshold_fires_one_alert(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]
    rule_id = await _insert_rule(migrations_pg_dsn, tenant_id=tenant_a, threshold=3)
    # Exactly at the threshold (3 >= 3).
    for _ in range(3):
        await _insert_event(migrations_pg_dsn, tenant_id=tenant_a)

    dispatcher = _FakeDispatcher()
    result = await _evaluate(tenant_a, dispatcher)

    # Exactly one alert, dispatched as a guardrail_alert event for tenant A.
    assert len(dispatcher.events) == 1
    event = dispatcher.events[0]
    assert event["event_type"] == "guardrail_alert"
    assert event["tenant_id"] == str(tenant_a)
    ctx = event["context"]
    assert ctx["count"] == 3
    assert ctx["threshold"] == 3
    assert len(result.fired) == 1
    assert result.fired[0].rule_id == rule_id
    assert result.fired[0].dispatched is True
    # The debounce anchor was stamped.
    assert await _rule_last_fired(migrations_pg_dsn, rule_id) is not None


# ===========================================================================
# Staying under the threshold does NOT fire
# ===========================================================================
@pytest.mark.asyncio
async def test_under_threshold_does_not_fire(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]
    rule_id = await _insert_rule(migrations_pg_dsn, tenant_id=tenant_a, threshold=5)
    for _ in range(4):  # 4 < 5
        await _insert_event(migrations_pg_dsn, tenant_id=tenant_a)

    dispatcher = _FakeDispatcher()
    result = await _evaluate(tenant_a, dispatcher)

    assert dispatcher.events == []
    assert result.fired == []
    assert await _rule_last_fired(migrations_pg_dsn, rule_id) is None


# ===========================================================================
# Threshold / window are CONFIGURABLE (a custom rule fires at its own threshold)
# ===========================================================================
@pytest.mark.asyncio
async def test_threshold_and_window_are_configurable(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]
    # A strict rule (threshold 2) and a lax rule (threshold 50). With 2
    # events, only the strict rule should fire — proving the threshold is
    # read from the rule, not hardcoded.
    strict = await _insert_rule(migrations_pg_dsn, tenant_id=tenant_a, name="strict", threshold=2)
    await _insert_rule(migrations_pg_dsn, tenant_id=tenant_a, name="lax", threshold=50)
    for _ in range(2):
        await _insert_event(migrations_pg_dsn, tenant_id=tenant_a)

    dispatcher = _FakeDispatcher()
    result = await _evaluate(tenant_a, dispatcher)

    assert len(dispatcher.events) == 1
    fired_ids = {f.rule_id for f in result.fired}
    assert fired_ids == {strict}

    # The window is also honoured: a rule with a SHORT window must not count
    # an OLD event. Insert an event 2h ago; a 1h-window rule (threshold 1)
    # must not fire on it.
    await _insert_rule(
        migrations_pg_dsn,
        tenant_id=tenant_a,
        name="short-window",
        threshold=1,
        window_seconds=3600,
        guardrail_type="prompt_injection",
    )
    await _insert_event(
        migrations_pg_dsn,
        tenant_id=tenant_a,
        guardrail_type="prompt_injection",
        created_at=datetime.now(tz=UTC) - timedelta(hours=2),
    )
    dispatcher2 = _FakeDispatcher()
    result2 = await _evaluate(tenant_a, dispatcher2)
    # The short-window/prompt_injection rule sees zero IN-WINDOW events.
    assert all(f.guardrail_type != "prompt_injection" for f in result2.fired)


# ===========================================================================
# Type + severity scoping
# ===========================================================================
@pytest.mark.asyncio
async def test_type_and_severity_scoping(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]
    # Rule scoped to pii + min_severity high.
    await _insert_rule(
        migrations_pg_dsn,
        tenant_id=tenant_a,
        name="pii-high",
        threshold=2,
        guardrail_type="pii",
        min_severity="high",
    )
    # 2 pii/high (count), 1 pii/low (below severity), 3 secret_leakage (wrong type).
    await _insert_event(
        migrations_pg_dsn, tenant_id=tenant_a, guardrail_type="pii", severity="high"
    )
    await _insert_event(
        migrations_pg_dsn, tenant_id=tenant_a, guardrail_type="pii", severity="critical"
    )
    await _insert_event(migrations_pg_dsn, tenant_id=tenant_a, guardrail_type="pii", severity="low")
    for _ in range(3):
        await _insert_event(
            migrations_pg_dsn, tenant_id=tenant_a, guardrail_type="secret_leakage", severity="high"
        )

    dispatcher = _FakeDispatcher()
    result = await _evaluate(tenant_a, dispatcher)
    # Only the 2 pii/high+critical events count → exactly at threshold 2.
    assert len(result.fired) == 1
    assert result.fired[0].count == 2


# ===========================================================================
# Debounce: a second evaluation within the same window does NOT re-fire
# ===========================================================================
@pytest.mark.asyncio
async def test_debounce_suppresses_second_alert(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]
    rule_id = await _insert_rule(
        migrations_pg_dsn, tenant_id=tenant_a, threshold=3, window_seconds=3600
    )
    # A controllable clock so the trailing window + debounce are deterministic.
    base = datetime.now(tz=UTC)
    # First wave: 5 events at `base` (a sustained breach within the window).
    for _ in range(5):
        await _insert_event(migrations_pg_dsn, tenant_id=tenant_a, created_at=base)

    dispatcher = _FakeDispatcher()
    # First evaluation: fires.
    r1 = await _evaluate(tenant_a, dispatcher, now=base)
    assert len(dispatcher.events) == 1
    assert len(r1.fired) == 1

    # Second evaluation 10 minutes later (still inside the 1h window): the
    # breach persists but the debounce suppresses a second alert.
    r2 = await _evaluate(tenant_a, dispatcher, now=base + timedelta(minutes=10))
    assert len(dispatcher.events) == 1  # still ONE — no spam
    assert r2.fired == []
    assert rule_id in r2.suppressed_rule_ids

    # The breach keeps going: a second wave of fresh events past the debounce
    # window. At base+1h1min the debounce (== the 1h window since last_fired)
    # has elapsed, and these new events are inside the trailing window → the
    # rule fires again (exactly once more, not on every event).
    later = base + timedelta(hours=1, minutes=1)
    for _ in range(5):
        await _insert_event(migrations_pg_dsn, tenant_id=tenant_a, created_at=later)
    r3 = await _evaluate(tenant_a, dispatcher, now=later)
    assert len(dispatcher.events) == 2
    assert len(r3.fired) == 1


# ===========================================================================
# Per-tenant isolation: tenant A's violations never alert tenant B
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_alerts_are_tenant_isolated(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a, tenant_b = seeded["tenant_a"], seeded["tenant_b"]
    # A rule in EACH tenant, both threshold 3.
    rule_a = await _insert_rule(migrations_pg_dsn, tenant_id=tenant_a, threshold=3)
    rule_b = await _insert_rule(migrations_pg_dsn, tenant_id=tenant_b, threshold=3)
    # Only tenant A has a spike (5 events). Tenant B has none.
    for _ in range(5):
        await _insert_event(migrations_pg_dsn, tenant_id=tenant_a)

    # Evaluating tenant B sees NONE of tenant A's events → no alert.
    dispatcher_b = _FakeDispatcher()
    result_b = await _evaluate(tenant_b, dispatcher_b)
    assert dispatcher_b.events == []
    assert result_b.fired == []
    assert await _rule_last_fired(migrations_pg_dsn, rule_b) is None

    # Evaluating tenant A fires exactly one alert, scoped to tenant A.
    dispatcher_a = _FakeDispatcher()
    result_a = await _evaluate(tenant_a, dispatcher_a)
    assert len(dispatcher_a.events) == 1
    assert dispatcher_a.events[0]["tenant_id"] == str(tenant_a)
    assert {f.rule_id for f in result_a.fired} == {rule_a}


# ===========================================================================
# The default dispatcher goes THROUGH the Plan 10 path (dispatch_event task)
# ===========================================================================
@pytest.mark.asyncio
async def test_default_dispatcher_enqueues_plan10_event(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]
    await _insert_rule(migrations_pg_dsn, tenant_id=tenant_a, threshold=2)
    for _ in range(2):
        await _insert_event(migrations_pg_dsn, tenant_id=tenant_a)

    # Capture the Celery task the producer enqueues — this proves the alert
    # rides the Plan 10 dispatch_event task (the notifier owns the send).
    sent: list[tuple[str, dict]] = []

    class _FakeCelery:
        def send_task(self, name: str, *, args, queue):
            sent.append((name, {"args": args, "queue": queue}))

    import api_server.celery_client as cc

    monkeypatch.setattr(cc, "get_celery_client", _FakeCelery)

    # Use the DEFAULT dispatcher (CeleryAlertDispatcher) by passing None.
    from api_server.auth.deps import AuthPrincipal, open_tenant_session
    from api_server.guardrails.alerts import evaluate_tenant_alert_rules

    principal = AuthPrincipal(user_id=uuid4(), session_id=uuid7(), tenant_id=tenant_a)
    async with open_tenant_session(principal) as session:
        await evaluate_tenant_alert_rules(session, tenant_id=tenant_a, dispatcher=None)

    assert len(sent) == 1
    name, payload = sent[0]
    assert name == "notification_dispatcher.dispatch_event"
    assert payload["args"][0]["event_type"] == "guardrail_alert"
    assert payload["args"][0]["tenant_id"] == str(tenant_a)


# ===========================================================================
# RBAC: a tenant_admin can CRUD rules; a plain member cannot
# ===========================================================================
@pytest.mark.asyncio
async def test_admin_can_crud_rules(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        # Create.
        created = await client.post(
            "/guardrails/alert-rules",
            headers=headers,
            json={
                "name": "PII spike",
                "threshold": 5,
                "window_seconds": 1800,
                "guardrail_type": "pii",
                "min_severity": "high",
            },
        )
        assert created.status_code == 201, created.text
        rule = created.json()
        assert rule["tenant_id"] == str(seeded["tenant_a"])
        assert rule["threshold"] == 5
        assert rule["window_seconds"] == 1800
        assert rule["guardrail_type"] == "pii"
        assert rule["min_severity"] == "high"
        rule_id = rule["id"]

        # List.
        listed = await client.get("/guardrails/alert-rules", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        # Get one.
        got = await client.get(f"/guardrails/alert-rules/{rule_id}", headers=headers)
        assert got.status_code == 200

        # Patch (configurable: change the threshold).
        patched = await client.patch(
            f"/guardrails/alert-rules/{rule_id}",
            headers=headers,
            json={"threshold": 20, "enabled": False},
        )
        assert patched.status_code == 200
        assert patched.json()["threshold"] == 20
        assert patched.json()["enabled"] is False

        # Empty patch → 422.
        empty = await client.patch(f"/guardrails/alert-rules/{rule_id}", headers=headers, json={})
        assert empty.status_code == 422

        # Out-of-range window → 422 (not a silent clamp).
        bad = await client.post(
            "/guardrails/alert-rules",
            headers=headers,
            json={"name": "bad", "window_seconds": 5},  # below MIN_WINDOW_SECONDS
        )
        assert bad.status_code == 422

        # Delete (soft).
        deleted = await client.delete(f"/guardrails/alert-rules/{rule_id}", headers=headers)
        assert deleted.status_code == 204
        # Gone from the list.
        listed2 = await client.get("/guardrails/alert-rules", headers=headers)
        assert listed2.json() == []


@pytest.mark.asyncio
async def test_member_cannot_manage_rules(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        # Create is denied.
        created = await client.post(
            "/guardrails/alert-rules",
            headers=headers,
            json={"name": "nope", "threshold": 3},
        )
        assert created.status_code == 403
        # List is denied too (managing rules is an admin surface).
        listed = await client.get("/guardrails/alert-rules", headers=headers)
        assert listed.status_code == 403


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_admin_cannot_touch_other_tenant_rule(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    # A rule owned by tenant B.
    rule_b = await _insert_rule(migrations_pg_dsn, tenant_id=seeded["tenant_b"])
    # Tenant A's admin must not be able to fetch / patch / delete it (404 —
    # RLS hides it, we never reveal it exists).
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token_a}"}
    async with _client(configured_app) as client:
        assert (
            await client.get(f"/guardrails/alert-rules/{rule_b}", headers=headers)
        ).status_code == 404
        assert (
            await client.patch(
                f"/guardrails/alert-rules/{rule_b}", headers=headers, json={"threshold": 1}
            )
        ).status_code == 404
        assert (
            await client.delete(f"/guardrails/alert-rules/{rule_b}", headers=headers)
        ).status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_is_401(configured_app) -> None:
    async with _client(configured_app) as client:
        resp = await client.get("/guardrails/alert-rules")
    assert resp.status_code == 401
