"""Integration tests for the guardrail-events substrate (Plan 11 task_11_20).

Exercises the tenant-scoped ``guardrail_events`` table, the recorder
service, and the dashboard endpoints end to end against a real Postgres +
the tenant-isolation RLS of migration 0052:

  - a triggered guardrail RECORDS an event whose detail is MASKED — the raw
    secret that tripped it is NEVER persisted (not in ``detail``, not
    anywhere in ``detail_payload``). Driven through the real
    ``secret_leakage`` guardrail + the engine so the masking is the
    engine's own redaction, not a test stub.
  - the dashboard / list endpoints return ONLY the caller tenant's events
    (``@pytest.mark.cross_tenant``): tenant B never sees tenant A's events,
    and the aggregate counts are per-tenant.
  - pagination (``limit`` / ``offset`` ge/le) + filters (``type`` /
    ``severity``) work, and an out-of-range page is a clean 422.

Fixture wiring mirrors ``test_prices_endpoints.py``: seed via the BYPASSRLS
migrations role, mint JWTs, drive the API via AsyncClient. Events are
written directly through the recorder service on a tenant-scoped session
(the same path the host uses) rather than via an HTTP write endpoint —
there is no write endpoint (the engine/host records; the dashboard reads).
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

# A secret value that MUST NEVER appear in the persisted event. A realistic
# GitHub PAT shape so the real `secret_leakage` guardrail detects + redacts it.
_RAW_SECRET = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"  # (test fixture)


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
            "TRUNCATE guardrail_events, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-guardrails",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-guardrails",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@guardrails.test",
            "h",
            ids["member_a"],
            "member-a@guardrails.test",
            "h",
            ids["admin_b"],
            "admin-b@guardrails.test",
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
    action: str | None = "redact",
    detail: str = "masked detail",
    hook_point: str = "post_llm",
) -> UUID:
    """Insert one guardrail_events row directly as the BYPASSRLS migrations user."""
    event_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO guardrail_events"
            " (id, tenant_id, guardrail_type, hook_point, severity, action, detail)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            event_id,
            tenant_id,
            guardrail_type,
            hook_point,
            severity,
            action,
            detail,
        )
    finally:
        await conn.close()
    return event_id


async def _count_events(dsn: str, tenant_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchval(
            "SELECT count(*) FROM guardrail_events WHERE tenant_id = $1", tenant_id
        )
        return int(row)
    finally:
        await conn.close()


async def _fetch_event_raw(dsn: str, tenant_id: UUID) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT guardrail_type, severity, action, detail, detail_payload::text AS payload"
            " FROM guardrail_events WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 1",
            tenant_id,
        )
        return dict(row) if row is not None else {}
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Fixtures (identical wiring to test_prices_endpoints.configured_app)
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


# ===========================================================================
# The recorder masks: a triggered guardrail records an event, never the raw secret
# ===========================================================================
@pytest.mark.asyncio
async def test_triggered_guardrail_records_masked_event(
    configured_app, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Run the real secret_leakage guardrail on text containing a token, then
    persist the engine's decision via the recorder. The event must be
    recorded with a MASKED detail and must NOT contain the raw secret."""
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]

    from shared_guardrails.pipeline import GuardrailPipeline
    from shared_guardrails.types import GuardrailContext

    # A pipeline with the secret_leakage guardrail at post_llm.
    pipeline = GuardrailPipeline.from_dict(
        {"guardrails": {"post_llm": [{"type": "secret_leakage"}]}}
    )
    ctx = GuardrailContext(
        hook="post_llm",
        response=f"Here is the deploy token: {_RAW_SECRET}\nUse it in CI.",
    )
    decision = pipeline.run(ctx)
    assert decision.triggered, "the secret_leakage guardrail should have fired"

    # Persist the decision via the recorder on a tenant-scoped RLS session.
    from api_server.auth.deps import AuthPrincipal, open_tenant_session
    from api_server.guardrails.events import GuardrailEventContext, record_pipeline_decision

    principal = AuthPrincipal(user_id=seeded["admin_a"], session_id=uuid7(), tenant_id=tenant_a)
    async with open_tenant_session(principal) as session:
        written = await record_pipeline_decision(
            session,
            decision,
            context=GuardrailEventContext(tenant_id=tenant_a, agent_label="planner"),
        )
        assert len(written) == 1

    # Exactly one event for tenant A, and the raw secret is nowhere in it.
    assert await _count_events(migrations_pg_dsn, tenant_a) == 1
    row = await _fetch_event_raw(migrations_pg_dsn, tenant_a)
    assert row["guardrail_type"] == "secret_leakage"
    assert row["severity"] in {"info", "low", "medium", "high", "critical"}
    # The masked detail names the family but never the value.
    assert _RAW_SECRET not in row["detail"]
    assert _RAW_SECRET not in (row["payload"] or "")
    # And the redacted_text key the guardrail emitted was dropped entirely.
    assert "redacted_text" not in (row["payload"] or "")


# ===========================================================================
# Cross-tenant isolation: a tenant sees ONLY its own events / dashboard
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_events_list_is_tenant_isolated(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    # 2 events for A, 1 for B.
    await _insert_event(migrations_pg_dsn, tenant_id=seeded["tenant_a"], severity="high")
    await _insert_event(migrations_pg_dsn, tenant_id=seeded["tenant_a"], severity="low")
    await _insert_event(migrations_pg_dsn, tenant_id=seeded["tenant_b"], severity="critical")

    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with _client(configured_app) as client:
        resp_a = await client.get(
            "/guardrails/events", headers={"Authorization": f"Bearer {token_a}"}
        )
        resp_b = await client.get(
            "/guardrails/events", headers={"Authorization": f"Bearer {token_b}"}
        )
    assert resp_a.status_code == 200, resp_a.text
    assert resp_b.status_code == 200, resp_b.text
    events_a = resp_a.json()
    events_b = resp_b.json()
    assert len(events_a) == 2
    assert len(events_b) == 1
    # Every row A sees belongs to A; B never appears.
    assert all(e["tenant_id"] == str(seeded["tenant_a"]) for e in events_a)
    assert all(e["tenant_id"] == str(seeded["tenant_b"]) for e in events_b)


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_dashboard_is_tenant_isolated(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    await _insert_event(
        migrations_pg_dsn, tenant_id=seeded["tenant_a"], guardrail_type="pii", severity="high"
    )
    await _insert_event(
        migrations_pg_dsn,
        tenant_id=seeded["tenant_a"],
        guardrail_type="secret_leakage",
        severity="high",
    )
    await _insert_event(
        migrations_pg_dsn,
        tenant_id=seeded["tenant_b"],
        guardrail_type="prompt_injection",
        severity="critical",
    )

    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with _client(configured_app) as client:
        dash_a = (
            await client.get(
                "/guardrails/dashboard", headers={"Authorization": f"Bearer {token_a}"}
            )
        ).json()
        dash_b = (
            await client.get(
                "/guardrails/dashboard", headers={"Authorization": f"Bearer {token_b}"}
            )
        ).json()

    assert dash_a["total"] == 2
    assert dash_b["total"] == 1
    # A's type breakdown is exactly its two types; prompt_injection (B's) is absent.
    a_types = {row["guardrail_type"] for row in dash_a["by_type"]}
    assert a_types == {"pii", "secret_leakage"}
    assert "prompt_injection" not in a_types
    # B sees only its own.
    b_types = {row["guardrail_type"] for row in dash_b["by_type"]}
    assert b_types == {"prompt_injection"}


# ===========================================================================
# RBAC: a plain tenant_user cannot read the admin dashboard
# ===========================================================================
@pytest.mark.asyncio
async def test_member_cannot_read_dashboard(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    async with _client(configured_app) as client:
        resp = await client.get(
            "/guardrails/dashboard", headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_is_401(configured_app) -> None:
    async with _client(configured_app) as client:
        resp = await client.get("/guardrails/events")
    assert resp.status_code == 401


# ===========================================================================
# Pagination + filters
# ===========================================================================
@pytest.mark.asyncio
async def test_pagination_limits_and_filters(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    tenant_a = seeded["tenant_a"]
    # 3 high pii + 2 low secret_leakage.
    for _ in range(3):
        await _insert_event(
            migrations_pg_dsn, tenant_id=tenant_a, guardrail_type="pii", severity="high"
        )
    for _ in range(2):
        await _insert_event(
            migrations_pg_dsn,
            tenant_id=tenant_a,
            guardrail_type="secret_leakage",
            severity="low",
        )

    token = await _mint_token(seeded["admin_a"], tenant_a)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        # limit caps the page.
        page = await client.get("/guardrails/events?limit=2", headers=headers)
        assert page.status_code == 200
        assert len(page.json()) == 2

        # offset pages through.
        page2 = await client.get("/guardrails/events?limit=2&offset=4", headers=headers)
        assert page2.status_code == 200
        assert len(page2.json()) == 1  # only 5 total

        # type filter.
        by_type = await client.get("/guardrails/events?guardrail_type=pii", headers=headers)
        assert by_type.status_code == 200
        assert len(by_type.json()) == 3
        assert all(e["guardrail_type"] == "pii" for e in by_type.json())

        # severity filter.
        by_sev = await client.get("/guardrails/events?severity=low", headers=headers)
        assert by_sev.status_code == 200
        assert len(by_sev.json()) == 2
        assert all(e["severity"] == "low" for e in by_sev.json())

        # out-of-range limit is a clean 422 (not a silent clamp).
        bad = await client.get("/guardrails/events?limit=99999", headers=headers)
        assert bad.status_code == 422

        # invalid severity (not in the enum) is a clean 422.
        bad_sev = await client.get("/guardrails/events?severity=nope", headers=headers)
        assert bad_sev.status_code == 422
