"""Integration tests for the LiteLLM price-feed sync (Plan 11 task_11_15).

The network is **fully mocked** — these tests never hit a real LiteLLM URL.
Two seams are exercised:

  - the service ``sync_prices_from_litellm`` directly against the real
    Postgres + the global-read RLS of migration 0049, with a
    ``StaticPriceFeedFetcher`` feeding a fixture JSON: a feed creates /
    updates catalog rows with ``source = litellm`` + USD normalisation
    (per-token → per-1M); an unchanged price is a no-op (no new period);
    malformed entries are skipped with a typed warning, not a crash;
  - the ``POST /admin/model-prices/sync`` endpoint, with ``httpx.AsyncClient``
    wired to an ``httpx.MockTransport`` returning the fixture JSON: a System
    Admin sync succeeds and a non-System-Admin caller is a clean 403.

Fixture / app wiring mirrors ``test_prices_endpoints.py``.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
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
def _feed() -> dict:
    return {
        # Documentation pseudo-entry — must be ignored, not mapped.
        "sample_spec": {
            "litellm_provider": "openai",
            "mode": "chat",
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
        },
        # A normal chat model with a cache-read price + context window.
        "claude-sonnet-4-5": {
            "litellm_provider": "anthropic",
            "mode": "chat",
            "input_cost_per_token": 0.000003,  # -> 3.0 per 1M
            "output_cost_per_token": 0.000015,  # -> 15.0 per 1M
            "cache_read_input_token_cost": 0.0000003,  # -> 0.30 per 1M
            "max_input_tokens": 200000,
        },
        # An embedding model (priced input only).
        "text-embedding-3-small": {
            "litellm_provider": "openai",
            "mode": "embedding",
            "input_cost_per_token": 0.00000002,  # -> 0.02 per 1M
            "max_input_tokens": 8191,
        },
        # Malformed: no provider -> skipped with a typed reason.
        "broken-no-provider": {
            "mode": "chat",
            "input_cost_per_token": 0.000001,
        },
        # Malformed: no usable price -> skipped.
        "free-model": {
            "litellm_provider": "ollama",
            "mode": "chat",
        },
        # Malformed: not an object -> skipped.
        "garbage-entry": "not-an-object",
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
            "TRUNCATE model_prices, llm_providers, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-sync",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-sync",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@sync.test",
            "h",
            ids["member_a"],
            "member-a@sync.test",
            "h",
            ids["sysadmin"],
            "sysadmin@sync.test",
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


async def _seed_active_providers(dsn: str, kinds: tuple[str, ...]) -> None:
    """Insert one ACTIVE platform provider per kind (BYPASSRLS migrations user).

    The endpoint computes the family allowlist from the active ``llm_providers``
    (plan price-sync-active-providers), so the endpoint tests must seed the
    provider kinds whose families (ADR 0028) cover the fixture feed.
    """
    conn = await asyncpg.connect(dsn)
    try:
        for kind in kinds:
            await conn.execute(
                "INSERT INTO llm_providers (id, kind, slug, display_name, is_active)"
                " VALUES ($1, $2, $4, $3, true)",
                (pid := uuid4()),
                kind,
                f"{kind} (test)",
                str(pid),
            )
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


@pytest.fixture()
def admin_session_factory(admin_database_url: str):
    """A BYPASSRLS admin AsyncSession factory for service-level tests."""
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


# ===========================================================================
# Service-level: sync against the real DB with a STATIC (mocked) feed
# ===========================================================================
@pytest.mark.asyncio
async def test_sync_creates_rows_with_litellm_source_and_usd_normalisation(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        sync_prices_from_litellm,
    )

    async with admin_session_factory() as session, session.begin():
        summary = await sync_prices_from_litellm(
            session, fetcher=StaticPriceFeedFetcher(payload=_feed())
        )

    # Two mappable rows (claude chat + embedding); three malformed skipped;
    # sample_spec ignored silently (not counted as skipped).
    assert summary.created == 2
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.skipped_count == 3
    skipped_keys = {s.model_key for s in summary.skipped}
    assert skipped_keys == {"broken-no-provider", "free-model", "garbage-entry"}

    # The rows landed with source=litellm and per-token -> per-1M USD.
    async with admin_session_factory() as session:
        rows = (
            (await session.execute(select(ModelPrice).order_by(ModelPrice.model_id)))
            .scalars()
            .all()
        )
    by_model = {r.model_id: r for r in rows}
    assert set(by_model) == {"claude-sonnet-4-5", "text-embedding-3-small"}

    claude = by_model["claude-sonnet-4-5"]
    assert claude.source == "litellm"
    assert claude.provider == "anthropic"
    assert claude.modality == "text"
    assert claude.unit == "per_1m_tokens"
    assert claude.currency == "USD"
    assert claude.input_price == Decimal("3.0")
    assert claude.output_price == Decimal("15.0")
    assert claude.cached_input_price == Decimal("0.30")
    assert claude.context_window == 200000
    assert claude.effective_to is None  # the open (current) period

    emb = by_model["text-embedding-3-small"]
    assert emb.modality == "embedding"
    assert emb.input_price == Decimal("0.02")
    assert emb.output_price == Decimal("0")  # embedding feed prices input only
    assert emb.cached_input_price is None


@pytest.mark.asyncio
async def test_unchanged_price_is_a_noop_no_new_period(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        sync_prices_from_litellm,
    )

    feed = _feed()

    # First sync: creates the rows.
    async with admin_session_factory() as session, session.begin():
        first = await sync_prices_from_litellm(
            session, fetcher=StaticPriceFeedFetcher(payload=feed)
        )
    assert first.created == 2

    # Second sync with the SAME feed: every priced row is unchanged -> no-op.
    async with admin_session_factory() as session, session.begin():
        second = await sync_prices_from_litellm(
            session, fetcher=StaticPriceFeedFetcher(payload=feed)
        )
    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged == 2

    # No extra (closed) periods were opened — exactly two rows survive.
    async with admin_session_factory() as session:
        count = len((await session.execute(select(ModelPrice))).scalars().all())
    assert count == 2


@pytest.mark.asyncio
async def test_changed_price_closes_current_and_opens_new_period(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        sync_prices_from_litellm,
    )

    feed = _feed()
    async with admin_session_factory() as session, session.begin():
        await sync_prices_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    # Bump the claude output price by a hair (<10% so it is applied, not deferred).
    feed["claude-sonnet-4-5"]["output_cost_per_token"] = 0.000016  # 15.0 -> 16.0 (+6.7%)

    async with admin_session_factory() as session, session.begin():
        summary = await sync_prices_from_litellm(
            session, fetcher=StaticPriceFeedFetcher(payload=feed)
        )
    assert summary.updated == 1
    assert summary.unchanged == 1  # the embedding row is untouched
    assert summary.large_increases == []

    async with admin_session_factory() as session:
        claude_rows = (
            (
                await session.execute(
                    select(ModelPrice)
                    .where(ModelPrice.model_id == "claude-sonnet-4-5")
                    .order_by(ModelPrice.effective_from)
                )
            )
            .scalars()
            .all()
        )
    # Effective dating: the old period is CLOSED, the new one is OPEN.
    assert len(claude_rows) == 2
    closed = [r for r in claude_rows if r.effective_to is not None]
    open_rows = [r for r in claude_rows if r.effective_to is None]
    assert len(closed) == 1 and len(open_rows) == 1
    assert closed[0].output_price == Decimal("15.0")
    assert open_rows[0].output_price == Decimal("16.0")


@pytest.mark.asyncio
async def test_large_increase_is_deferred_until_confirmed(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        sync_prices_from_litellm,
    )

    feed = _feed()
    async with admin_session_factory() as session, session.begin():
        await sync_prices_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    # Double the input price (+100% >> +10%) -> deferred unless confirmed.
    feed["claude-sonnet-4-5"]["input_cost_per_token"] = 0.000006  # 3.0 -> 6.0

    async with admin_session_factory() as session, session.begin():
        deferred = await sync_prices_from_litellm(
            session, fetcher=StaticPriceFeedFetcher(payload=feed)
        )
    assert deferred.updated == 0
    assert len(deferred.large_increases) == 1
    li = deferred.large_increases[0]
    assert li.model_id == "claude-sonnet-4-5"
    assert li.field == "input_price"

    # The catalog is untouched (still the old price, single open period).
    async with admin_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(ModelPrice).where(ModelPrice.model_id == "claude-sonnet-4-5")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].input_price == Decimal("3.0")

    # Confirm: the increase is now applied (closes old, opens new).
    async with admin_session_factory() as session, session.begin():
        confirmed = await sync_prices_from_litellm(
            session,
            fetcher=StaticPriceFeedFetcher(payload=feed),
            confirm_large_increases=True,
        )
    assert confirmed.updated == 1
    assert confirmed.large_increases == []


@pytest.mark.asyncio
async def test_manual_override_is_not_stomped(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice, PriceSource
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        sync_prices_from_litellm,
    )

    # A System Admin hand-entered a manual price that differs from the feed.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO model_prices"
            " (id, provider, model_id, modality, input_price, output_price, source)"
            " VALUES ($1, 'anthropic', 'claude-sonnet-4-5', 'text', 9.0, 9.0, 'manual')",
            uuid4(),
        )
    finally:
        await conn.close()

    feed = _feed()
    async with admin_session_factory() as session, session.begin():
        summary = await sync_prices_from_litellm(
            session, fetcher=StaticPriceFeedFetcher(payload=feed)
        )
    # The manual claude row is left alone (counted as unchanged); the
    # embedding row is created.
    assert summary.created == 1
    assert summary.unchanged == 1

    async with admin_session_factory() as session:
        claude = (
            await session.execute(
                select(ModelPrice).where(ModelPrice.model_id == "claude-sonnet-4-5")
            )
        ).scalar_one()
    assert claude.source == PriceSource.MANUAL.value
    assert claude.input_price == Decimal("9.0")  # untouched


# ===========================================================================
# Endpoint-level: System Admin can sync (network mocked); tenant is 403
# ===========================================================================
@pytest.mark.asyncio
async def test_endpoint_system_admin_can_sync(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    # The endpoint syncs only the families of the ACTIVE providers; seed
    # claude_sdk (→ anthropic) + azure_foundry (→ azure/azure_ai/openai) so the
    # fixture feed's anthropic + openai entries are both in scope.
    await _seed_active_providers(migrations_pg_dsn, ("claude_sdk", "azure_foundry"))
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    # Mock the network: the endpoint builds an httpx.AsyncClient; force it to
    # use a MockTransport that returns the fixture feed for any GET.
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_feed())

    import api_server.routers.model_prices as mp

    real_client = httpx.AsyncClient

    def _fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(mp.httpx, "AsyncClient", _fake_client)

    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices/sync", json={}, headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 2
    assert body["changed"] == 2
    assert body["unchanged"] == 0
    assert {s["model_key"] for s in body["skipped"]} == {
        "broken-no-provider",
        "free-model",
        "garbage-entry",
    }
    assert body["large_increases"] == []


@pytest.mark.asyncio
async def test_endpoint_tenant_admin_cannot_sync(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices/sync", json={}, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_endpoint_member_cannot_sync(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices/sync", json={}, headers=headers)
    assert resp.status_code == 403
