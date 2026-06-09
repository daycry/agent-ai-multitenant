"""Integration tests: the price sync is filtered to the active providers'
families (plan price-sync-active-providers, task_psa_01).

The network is **fully mocked** — these tests never hit a real LiteLLM URL.
They exercise, against the real Postgres + the global-read RLS of migration
0049 and the platform-global ``llm_providers`` table (ADR 0028):

  - :func:`active_litellm_families` — derives the family allowlist from the
    ACTIVE ``llm_providers`` rows (kind→family union, ADR 0028); 0 active ⇒ the
    EMPTY allowlist (no fallback to the closed catalogue);
  - the System-Admin override (``price_sync.allowed_families`` platform setting)
    WINS over the derived set when present;
  - :func:`sync_prices_from_litellm` with ``allowed_families``: only in-scope
    families are imported; out-of-scope feed entries are typed
    ``family_not_active`` skips; on re-sync a family that LEFT the allowlist has
    its open period CLOSED (the row is kept — never hard-deleted — its history /
    snapshots survive);
  - the ``POST /admin/model-prices/sync`` endpoint computes the allowlist from
    the active providers and applies it end-to-end.

Fixture / app wiring mirrors ``test_sync_prices_litellm.py``.
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
# A fixture LiteLLM feed covering three families (anthropic / openai / ollama).
# ---------------------------------------------------------------------------
def _feed() -> dict:
    return {
        "claude-sonnet-4-5": {
            "litellm_provider": "anthropic",
            "mode": "chat",
            "input_cost_per_token": 0.000003,  # -> 3.0 per 1M
            "output_cost_per_token": 0.000015,  # -> 15.0 per 1M
            "max_input_tokens": 200000,
        },
        "gpt-4o": {
            "litellm_provider": "openai",
            "mode": "chat",
            "input_cost_per_token": 0.0000025,  # -> 2.5 per 1M
            "output_cost_per_token": 0.00001,  # -> 10.0 per 1M
        },
        "llama3": {
            "litellm_provider": "ollama",
            "mode": "chat",
            "input_cost_per_token": 0.0000001,  # -> 0.10 per 1M
            "output_cost_per_token": 0.0000002,  # -> 0.20 per 1M
        },
    }


# ---------------------------------------------------------------------------
# Seed: a tenant with an admin + member, plus a System Admin user. Truncates
# llm_providers + platform_settings too so each test sets its own scope.
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
            "TRUNCATE model_prices, llm_providers, platform_settings,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-fam",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-fam",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@fam.test",
            "h",
            ids["member_a"],
            "member-a@fam.test",
            "h",
            ids["sysadmin"],
            "sysadmin@fam.test",
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


async def _seed_provider(dsn: str, kind: str, *, is_active: bool = True) -> UUID:
    """Insert one platform provider of ``kind`` (BYPASSRLS migrations user)."""
    provider_id = uuid7()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO llm_providers (id, kind, slug, display_name, is_active)"
            " VALUES ($1, $2, $5, $3, $4)",
            provider_id,
            kind,
            f"{kind} (test)",
            is_active,
            str(provider_id),
        )
    finally:
        await conn.close()
    return provider_id


async def _set_override(dsn: str, families: list[str], actor_id: UUID) -> None:
    """Write the price_sync.allowed_families platform-setting override."""
    import json

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value, updated_by)"
            " VALUES ('price_sync.allowed_families', $1::jsonb, $2)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            json.dumps(families),
            actor_id,
        )
    finally:
        await conn.close()


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


def _mock_httpx(monkeypatch: pytest.MonkeyPatch, feed: dict) -> None:
    """Force the router's httpx.AsyncClient onto a MockTransport (no real net)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=feed)

    import api_server.routers.model_prices as mp

    real_client = httpx.AsyncClient

    def _fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(mp.httpx, "AsyncClient", _fake_client)


# ===========================================================================
# active_litellm_families: derived from the ACTIVE providers; override wins.
# ===========================================================================
@pytest.mark.asyncio
async def test_active_families_derived_from_active_providers(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)
    await _seed_provider(migrations_pg_dsn, "ollama")
    # An INACTIVE azure_foundry provider must NOT contribute its families.
    await _seed_provider(migrations_pg_dsn, "azure_foundry", is_active=False)

    from api_server.pricing.litellm_sync import active_litellm_families

    async with admin_session_factory() as session:
        families = await active_litellm_families(session)

    assert families == frozenset({"ollama"})


@pytest.mark.asyncio
async def test_active_families_zero_active_is_empty(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)
    # Only an inactive provider -> NO fallback to the closed catalogue.
    await _seed_provider(migrations_pg_dsn, "ollama", is_active=False)

    from api_server.pricing.litellm_sync import active_litellm_families

    async with admin_session_factory() as session:
        families = await active_litellm_families(session)

    assert families == frozenset()


@pytest.mark.asyncio
async def test_active_families_unions_multiple_active_providers(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)
    await _seed_provider(migrations_pg_dsn, "claude_sdk")
    await _seed_provider(migrations_pg_dsn, "azure_foundry")

    from api_server.pricing.litellm_sync import active_litellm_families

    async with admin_session_factory() as session:
        families = await active_litellm_families(session)

    assert families == frozenset({"anthropic", "azure", "azure_ai", "openai"})


@pytest.mark.asyncio
async def test_settings_override_wins_over_active_providers(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    # Active providers would derive {ollama}; the override pins {anthropic}.
    await _seed_provider(migrations_pg_dsn, "ollama")
    await _set_override(migrations_pg_dsn, ["anthropic"], seeded["sysadmin"])

    from api_server.pricing.litellm_sync import active_litellm_families

    async with admin_session_factory() as session:
        families = await active_litellm_families(session)

    assert families == frozenset({"anthropic"})


@pytest.mark.asyncio
async def test_empty_settings_override_pins_empty_allowlist(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    """An explicit empty override pins the allowlist EMPTY (sync nothing), even
    though an active provider exists — the override always wins."""
    seeded = await _seed(migrations_pg_dsn)
    await _seed_provider(migrations_pg_dsn, "ollama")
    await _set_override(migrations_pg_dsn, [], seeded["sysadmin"])

    from api_server.pricing.litellm_sync import active_litellm_families

    async with admin_session_factory() as session:
        families = await active_litellm_families(session)

    assert families == frozenset()


# ===========================================================================
# Service: only in-scope families are imported; out-of-scope are typed skips.
# ===========================================================================
@pytest.mark.asyncio
async def test_sync_imports_only_active_families(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)
    await _seed_provider(migrations_pg_dsn, "ollama")

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        SKIP_FAMILY_NOT_ACTIVE,
        StaticPriceFeedFetcher,
        active_litellm_families,
        sync_prices_from_litellm,
    )

    async with admin_session_factory() as session, session.begin():
        families = await active_litellm_families(session)
        summary = await sync_prices_from_litellm(
            session,
            fetcher=StaticPriceFeedFetcher(payload=_feed()),
            allowed_families=families,
        )

    # Only the ollama model is created; anthropic + openai are family skips.
    assert summary.created == 1
    assert summary.discontinued == 0
    family_skips = [s for s in summary.skipped if s.reason == SKIP_FAMILY_NOT_ACTIVE]
    assert {s.model_key for s in family_skips} == {"claude-sonnet-4-5", "gpt-4o"}

    async with admin_session_factory() as session:
        rows = (await session.execute(select(ModelPrice))).scalars().all()
    assert {r.model_id for r in rows} == {"llama3"}
    assert rows[0].provider == "ollama"


@pytest.mark.asyncio
async def test_zero_active_providers_imports_nothing(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)
    # No providers at all -> empty allowlist -> nothing imported.

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        active_litellm_families,
        sync_prices_from_litellm,
    )

    async with admin_session_factory() as session, session.begin():
        families = await active_litellm_families(session)
        summary = await sync_prices_from_litellm(
            session,
            fetcher=StaticPriceFeedFetcher(payload=_feed()),
            allowed_families=families,
        )

    assert families == frozenset()
    assert summary.created == 0
    assert summary.skipped_count == 3  # all three feed entries out-of-scope

    async with admin_session_factory() as session:
        count = len((await session.execute(select(ModelPrice))).scalars().all())
    assert count == 0


# ===========================================================================
# Re-sync: a family that LEFT the allowlist has its open period CLOSED
# (the row is kept — never hard-deleted).
# ===========================================================================
@pytest.mark.asyncio
async def test_resync_closes_period_of_family_removed_from_allowlist(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)
    # Initially anthropic + ollama are active.
    await _seed_provider(migrations_pg_dsn, "claude_sdk")
    ollama_id = await _seed_provider(migrations_pg_dsn, "ollama")

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        active_litellm_families,
        sync_prices_from_litellm,
    )

    feed = _feed()
    # First sync imports anthropic (claude) + ollama (llama3); openai skipped.
    async with admin_session_factory() as session, session.begin():
        families = await active_litellm_families(session)
        first = await sync_prices_from_litellm(
            session,
            fetcher=StaticPriceFeedFetcher(payload=feed),
            allowed_families=families,
        )
    assert first.created == 2
    assert families == frozenset({"anthropic", "ollama"})

    # Operator deactivates the Ollama provider -> ollama leaves the allowlist.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("UPDATE llm_providers SET is_active = false WHERE id = $1", ollama_id)
    finally:
        await conn.close()

    # Re-sync: ollama is now out-of-scope. Its open period is CLOSED (treated as
    # discontinued); the row is NOT deleted. Anthropic stays current.
    async with admin_session_factory() as session, session.begin():
        families2 = await active_litellm_families(session)
        second = await sync_prices_from_litellm(
            session,
            fetcher=StaticPriceFeedFetcher(payload=feed),
            allowed_families=families2,
        )
    assert families2 == frozenset({"anthropic"})
    assert second.discontinued == 1
    assert second.discontinued_models[0].model_id == "llama3"

    async with admin_session_factory() as session:
        llama_rows = (
            (await session.execute(select(ModelPrice).where(ModelPrice.model_id == "llama3")))
            .scalars()
            .all()
        )
        claude = (
            await session.execute(
                select(ModelPrice).where(
                    ModelPrice.model_id == "claude-sonnet-4-5",
                    ModelPrice.effective_to.is_(None),
                )
            )
        ).scalar_one()
    # The ollama row SURVIVES (history kept) but its period is now closed.
    assert len(llama_rows) == 1
    assert llama_rows[0].effective_to is not None
    assert llama_rows[0].input_price == Decimal("0.10")  # historical price intact
    # Anthropic is untouched: still the current price.
    assert claude.effective_to is None
    assert claude.input_price == Decimal("3.0")


# ===========================================================================
# Endpoint: /sync computes the allowlist from the active providers.
# ===========================================================================
@pytest.mark.asyncio
async def test_endpoint_sync_respects_active_providers(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    await _seed_provider(migrations_pg_dsn, "ollama")  # only ollama active
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}
    _mock_httpx(monkeypatch, _feed())

    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices/sync", json={}, headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Only the ollama model imported; anthropic + openai counted as skips.
    assert body["created"] == 1
    family_skips = [s for s in body["skipped"] if s["reason"] == "family_not_active"]
    assert {s["model_key"] for s in family_skips} == {"claude-sonnet-4-5", "gpt-4o"}


@pytest.mark.asyncio
async def test_endpoint_sync_no_active_providers_imports_nothing(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    # No active providers configured.
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}
    _mock_httpx(monkeypatch, _feed())

    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices/sync", json={}, headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 0
    assert body["changed"] == 0
    # All three feed entries are out-of-scope skips.
    assert len([s for s in body["skipped"] if s["reason"] == "family_not_active"]) == 3
