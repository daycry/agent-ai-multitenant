"""Integration tests for the per-sync audit log (Plan 11 task_11_19).

Every price-catalog sync — manual (the admin-panel endpoints) or scheduled
(the Celery-beat job) — must leave ONE immutable audit row: who ran it, when,
from what source, the counts, the held >10% spikes, and a compact diff. The
network is **fully mocked** — these tests never hit a real LiteLLM URL.

What is asserted (task_11_19):

  * a manual sync writes an audit row with the actor (``user:<uuid>``), the
    counts, and the compact diff;
  * a scheduled sync (the beat task) attributes its audit row to the
    ``scheduler``, not a user;
  * the audit is append-only / immutable: a NOBYPASSRLS (tenant) session can
    read the global history but CANNOT insert / update / delete a row;
  * nothing is silently applied without an audit trail — an apply that is
    rejected for an unconfirmed >10% spike writes NEITHER a catalog change
    NOR an audit row, while a proceeding apply writes exactly one;
  * the history endpoint surfaces the rows (System Admin) and is 403 to a
    tenant caller.

Fixture / app wiring mirrors ``test_sync_prices_litellm.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# A fixture LiteLLM feed (per-token USD, the upstream shape).
# ---------------------------------------------------------------------------
def _feed() -> dict[str, Any]:
    return {
        "claude-sonnet-4-5": {
            "litellm_provider": "anthropic",
            "mode": "chat",
            "input_cost_per_token": 0.000003,  # -> 3.0 per 1M
            "output_cost_per_token": 0.000015,  # -> 15.0 per 1M
            "cache_read_input_token_cost": 0.0000003,  # -> 0.30 per 1M
            "max_input_tokens": 200000,
        },
        "text-embedding-3-small": {
            "litellm_provider": "openai",
            "mode": "embedding",
            "input_cost_per_token": 0.00000002,  # -> 0.02 per 1M
            "max_input_tokens": 8191,
        },
        # Malformed: no provider -> skipped with a typed reason (audited).
        "broken-no-provider": {
            "mode": "chat",
            "input_cost_per_token": 0.000001,
        },
    }


# ---------------------------------------------------------------------------
# Seed: a tenant with an admin + member, plus a System Admin user.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "sysadmin": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE price_sync_audit, model_prices, llm_providers, platform_settings,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        # The sync endpoints scope to the active providers' families (plan
        # price-sync-active-providers). Seed claude_sdk (→ anthropic) +
        # azure_foundry (→ azure/azure_ai/openai) so the fixture feed's
        # anthropic + openai entries are in scope (the malformed entry is still
        # the only skip).
        for kind in ("claude_sdk", "azure_foundry"):
            await conn.execute(
                "INSERT INTO llm_providers (id, kind, slug, display_name, is_active)"
                " VALUES ($1, $2, $4, $3, true)",
                (pid := uuid4()),
                kind,
                f"{kind} (test)",
                str(pid),
            )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-audit",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-audit",
        )
        await conn.execute(
            # prod-09 task_prod09_04: `require_system_admin` re-reads
            # `users.is_system_admin` from the DB, so the System Admin fixture
            # must actually CARRY the flag — a `sys` JWT claim over a row whose
            # flag is false is exactly the privilege the gate now refuses.
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, $3, false), ($4, $5, $6, false), ($7, $8, $9, true)",
            ids["admin_a"],
            "admin-a@audit.test",
            "h",
            ids["member_a"],
            "member-a@audit.test",
            "h",
            ids["sysadmin"],
            "sysadmin@audit.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_a"],
            ids["member_a"],
        )
    finally:
        await conn.close()
    return ids


# ---------------------------------------------------------------------------
# Fixtures (identical wiring to test_sync_prices_litellm.configured_app)
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


@pytest.fixture()
def admin_session_factory(admin_database_url: str):
    """A BYPASSRLS admin AsyncSession factory for service-level checks."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        asyncio.run(engine.dispose())


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


class _FeedHolder:
    """A mutable feed payload so a SINGLE httpx patch can serve a feed that
    changes between requests (a re-patch would nest transports and the inner
    one would win, serving a stale feed)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload


def _mock_httpx(monkeypatch: pytest.MonkeyPatch, holder: _FeedHolder) -> None:
    """Force the endpoint's httpx.AsyncClient onto a MockTransport (no network).

    Reads ``holder.payload`` at request time so mutating the holder between
    requests serves the new feed without re-patching.
    """
    real_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=holder.payload)

    def _fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(*args, **kwargs)

    import api_server.routers.model_prices as mp

    monkeypatch.setattr(mp.httpx, "AsyncClient", _fake_client)


# ===========================================================================
# A manual sync writes an audit row (actor + counts + diff)
# ===========================================================================
@pytest.mark.asyncio
async def test_manual_sync_writes_audit_row(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_server.db.price_sync_audit import PriceSyncAudit, SyncTrigger, actor_for_user

    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}
    _mock_httpx(monkeypatch, _FeedHolder(_feed()))

    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices/sync", json={}, headers=headers)
    assert resp.status_code == 200, resp.text

    # Exactly one audit row, attributed to the System Admin user, with the
    # run's counts and a compact diff (skipped entries surfaced).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch("SELECT * FROM price_sync_audit")
    finally:
        await conn.close()
    assert len(rows) == 1
    row = rows[0]
    assert row["actor"] == actor_for_user(seeded["sysadmin"])
    assert row["actor_user_id"] == seeded["sysadmin"]
    assert row["trigger"] == SyncTrigger.MANUAL.value
    assert row["source"] == "litellm"
    assert row["created"] == 2
    assert row["updated"] == 0
    assert row["skipped"] == 1  # broken-no-provider
    assert row["held_large_increases"] == 0
    # The compact diff records the skipped feed entry by key.
    import json

    diff = json.loads(row["diff"])
    assert {s["model_key"] for s in diff["skipped"]} == {"broken-no-provider"}

    # Sanity: the ORM model reads the same row.
    async with admin_orm(migrations_pg_dsn) as session:
        audit = (await session.execute(select(PriceSyncAudit))).scalar_one()
    assert audit.created == 2


# A tiny ORM-session helper bound to the BYPASSRLS DSN (asyncpg DSN -> URL).
class admin_orm:  # noqa: N801 - context-manager helper
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def __aenter__(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        url = "postgresql+asyncpg://" + self._dsn.split("://", 1)[1]
        self._engine = create_async_engine(url)
        self._session = async_sessionmaker(self._engine, expire_on_commit=False)()
        return self._session

    async def __aexit__(self, *exc: object) -> None:
        await self._session.close()
        await self._engine.dispose()


# ===========================================================================
# A held >10% spike: the lower-level /sync still audits (it applied the rest)
# ===========================================================================
@pytest.mark.asyncio
async def test_sync_audits_even_when_a_spike_is_held(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    # First sync seeds the catalog (audit #1).
    holder = _FeedHolder(_feed())
    _mock_httpx(monkeypatch, holder)
    async with _client(configured_app) as client:
        first = await client.post("/admin/model-prices/sync", json={}, headers=headers)
    assert first.status_code == 200

    # Second feed doubles the claude input price (+100% >> +10%) -> held.
    spiked = _feed()
    spiked["claude-sonnet-4-5"]["input_cost_per_token"] = 0.000006
    holder.payload = spiked
    async with _client(configured_app) as client:
        second = await client.post("/admin/model-prices/sync", json={}, headers=headers)
    assert second.status_code == 200
    assert len(second.json()["large_increases"]) == 1

    # Two audit rows; the latest records the held spike in its diff + count.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch("SELECT * FROM price_sync_audit ORDER BY created_at, id")
    finally:
        await conn.close()
    assert len(rows) == 2
    import json

    latest = rows[-1]
    assert latest["held_large_increases"] == 1
    diff = json.loads(latest["diff"])
    assert diff["large_increases"][0]["model_id"] == "claude-sonnet-4-5"
    assert diff["large_increases"][0]["field"] == "input_price"


# ===========================================================================
# A rejected apply (unconfirmed spike) writes NEITHER catalog NOR audit row
# ===========================================================================
@pytest.mark.asyncio
async def test_rejected_apply_writes_no_audit_row(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    # Seed via apply (no spike) -> one audit row.
    holder = _FeedHolder(_feed())
    _mock_httpx(monkeypatch, holder)
    async with _client(configured_app) as client:
        seed_resp = await client.post("/admin/model-prices/sync/apply", json={}, headers=headers)
    assert seed_resp.status_code == 200

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        before = await conn.fetchval("SELECT count(*) FROM price_sync_audit")
    finally:
        await conn.close()
    assert before == 1

    # Now a spike with confirm=false -> the apply is REJECTED (409): nothing
    # applied, so NO new audit row (nothing silently applied without a trail).
    spiked = _feed()
    spiked["claude-sonnet-4-5"]["input_cost_per_token"] = 0.000006  # +100%
    holder.payload = spiked
    async with _client(configured_app) as client:
        rejected = await client.post(
            "/admin/model-prices/sync/apply", json={"confirm": False}, headers=headers
        )
    assert rejected.status_code == 409

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        after = await conn.fetchval("SELECT count(*) FROM price_sync_audit")
    finally:
        await conn.close()
    assert after == 1  # unchanged — the rejected apply left no audit row

    # Confirming the spike applies it AND writes the audit row.
    async with _client(configured_app) as client:
        confirmed = await client.post(
            "/admin/model-prices/sync/apply", json={"confirm": True}, headers=headers
        )
    assert confirmed.status_code == 200
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        final = await conn.fetch(
            "SELECT confirmed, updated FROM price_sync_audit ORDER BY created_at, id"
        )
    finally:
        await conn.close()
    assert len(final) == 2
    assert final[-1]["confirmed"] is True
    assert final[-1]["updated"] == 1


# ===========================================================================
# A scheduled sync attributes the audit to the scheduler (no user)
# ===========================================================================
@pytest.mark.asyncio
async def test_scheduled_sync_attributes_audit_to_scheduler(
    configured_app, migrations_pg_dsn: str, admin_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_server.db.price_sync_audit import ACTOR_SCHEDULER, SyncTrigger
    from workers.config import Settings
    from workers.price_sync import _sync_model_prices

    await _seed(migrations_pg_dsn)

    # Mock the feed fetcher the beat task builds (no real network).
    import api_server.pricing.litellm_sync as ls

    def _fake_fetcher(*_a: Any, **_k: Any) -> ls.StaticPriceFeedFetcher:
        return ls.StaticPriceFeedFetcher(payload=_feed())

    monkeypatch.setattr(ls, "HttpxPriceFeedFetcher", _fake_fetcher)

    settings = Settings(
        database_url=admin_database_url,
        litellm_price_feed_url="http://feed.invalid/model_prices.json",
    )
    result = await _sync_model_prices(settings)
    assert result["enabled"] is True
    assert result["created"] == 2

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow("SELECT * FROM price_sync_audit")
    finally:
        await conn.close()
    assert row is not None
    assert row["actor"] == ACTOR_SCHEDULER
    assert row["actor_user_id"] is None
    assert row["trigger"] == SyncTrigger.SCHEDULED.value
    assert row["created"] == 2
    assert row["feed_url"] == "http://feed.invalid/model_prices.json"


# ===========================================================================
# The audit is append-only / immutable for a NOBYPASSRLS (tenant) session
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_audit_is_append_only_for_tenant_session(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.conftest import (
        PG_APP_PASSWORD,
        PG_APP_USER,
        PG_HOST,
        PG_PORT,
        PG_TEST_DB,
        _grant_app_user_existing_tables,
    )

    seeded = await _seed(migrations_pg_dsn)
    await _grant_app_user_existing_tables()
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    # Write one audit row via a real sync.
    _mock_httpx(monkeypatch, _FeedHolder(_feed()))
    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices/sync", json={}, headers=headers)
    assert resp.status_code == 200

    # A NOBYPASSRLS (tenant) app_user session: the global-read RLS lets it
    # SELECT, but the ABSENCE of write policies denies INSERT (a hard
    # privilege error) and makes UPDATE / DELETE touch zero rows (immutable).
    app_dsn = f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"
    app_conn = await asyncpg.connect(app_dsn)
    try:
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(uuid4()))

        # Read is allowed (global-read policy).
        visible = await app_conn.fetchval("SELECT count(*) FROM price_sync_audit")
        assert visible == 1

        # INSERT denied — no write policy exists for a NOBYPASSRLS role.
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(
                "INSERT INTO price_sync_audit (id, actor, trigger) VALUES ($1, 'forged', 'manual')",
                uuid4(),
            )

        # UPDATE / DELETE affect zero rows (no write policy) — append-only.
        await app_conn.execute("UPDATE price_sync_audit SET created = 999")
        await app_conn.execute("DELETE FROM price_sync_audit")
    finally:
        await app_conn.close()

    # The original row is intact: not edited, not erased, no forged row added.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch("SELECT actor, created FROM price_sync_audit")
    finally:
        await conn.close()
    assert len(rows) == 1
    assert rows[0]["actor"] != "forged"
    assert rows[0]["created"] == 2  # NOT mutated to 999, NOT deleted


# ===========================================================================
# The history endpoint surfaces the rows (System Admin); tenant is 403
# ===========================================================================
@pytest.mark.asyncio
async def test_history_endpoint_lists_rows_and_gates_tenants(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    sysadmin_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    tenant_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    sysadmin_headers = {"Authorization": f"Bearer {sysadmin_token}"}
    tenant_headers = {"Authorization": f"Bearer {tenant_token}"}

    _mock_httpx(monkeypatch, _FeedHolder(_feed()))
    async with _client(configured_app) as client:
        await client.post("/admin/model-prices/sync", json={}, headers=sysadmin_headers)

        # System Admin reads the history.
        hist = await client.get("/admin/model-prices/sync/audit", headers=sysadmin_headers)
        assert hist.status_code == 200, hist.text
        body = hist.json()
        assert len(body) == 1
        assert body[0]["trigger"] == "manual"
        assert body[0]["created"] == 2

        # The trigger filter narrows by manual/scheduled.
        sched = await client.get(
            "/admin/model-prices/sync/audit?trigger=scheduled", headers=sysadmin_headers
        )
        assert sched.status_code == 200
        assert sched.json() == []

        # A tenant_admin is a clean 403 — the history is a platform surface.
        denied = await client.get("/admin/model-prices/sync/audit", headers=tenant_headers)
    assert denied.status_code == 403
