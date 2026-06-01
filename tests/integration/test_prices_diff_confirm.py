"""Integration tests for the dry-run diff + mandatory-confirmation apply (Plan 11 task_11_16).

The backend gate is the testable core of task_11_16 (the diff view + dialog
are the frontend half, exercised by an unrun Playwright spec). The network is
**fully mocked** — these tests never hit a real LiteLLM URL.

Three seams are exercised against the real Postgres + the global-read RLS of
migration 0049:

  - :func:`compute_sync_diff` — the DRY-RUN: it computes a per-model diff
    (added / updated / unchanged / increased / removed, old-vs-new prices +
    % change) WITHOUT writing a single catalog row;
  - :func:`apply_sync_from_litellm` — the APPLY with the mandatory
    confirmation gate: a >10% rise is REJECTED (raises
    ``LargeIncreaseNotConfirmedError``, nothing written) unless ``confirm`` is
    True; a <=10% change applies without confirmation;
  - the ``POST /admin/model-prices/sync/diff`` + ``/sync/apply`` endpoints
    (network mocked via ``httpx.MockTransport``): the apply is a 409 on an
    unconfirmed spike and a 200 once confirmed; the dry-run never writes.

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
# A fixture LiteLLM feed (per-token USD, the upstream shape).
# ---------------------------------------------------------------------------
def _feed() -> dict:
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
            "tenant-a-diff",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-diff",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@diff.test",
            "h",
            ids["member_a"],
            "member-a@diff.test",
            "h",
            ids["sysadmin"],
            "sysadmin@diff.test",
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

    The diff/apply endpoints scope the sync to the active providers' families
    (plan price-sync-active-providers), so the endpoint tests seed the kinds
    whose families (ADR 0028) cover the fixture feed (anthropic + openai)."""
    conn = await asyncpg.connect(dsn)
    try:
        for kind in kinds:
            await conn.execute(
                "INSERT INTO llm_providers (id, kind, display_name, is_active)"
                " VALUES ($1, $2, $3, true)",
                uuid4(),
                kind,
                f"{kind} (test)",
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Fixtures (identical wiring to test_sync_prices_litellm)
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
# Service: dry-run diff is accurate and writes NOTHING
# ===========================================================================
@pytest.mark.asyncio
async def test_dry_run_diff_is_accurate_and_writes_nothing(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        DiffStatus,
        StaticPriceFeedFetcher,
        compute_sync_diff,
        sync_prices_from_litellm,
    )

    feed = _feed()

    # Seed the catalog from the feed first so the diff has a baseline.
    async with admin_session_factory() as session, session.begin():
        await sync_prices_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    # A small (<=10%) rise on output, a big (>10%) rise on input.
    feed["claude-sonnet-4-5"]["output_cost_per_token"] = 0.000016  # 15.0 -> 16.0 (+6.7%)
    feed["text-embedding-3-small"]["input_cost_per_token"] = 0.00000005  # 0.02 -> 0.05 (+150%)
    # A brand-new model the catalog has no period for.
    feed["gpt-new"] = {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 0.000001,
        "output_cost_per_token": 0.000002,
    }

    async with admin_session_factory() as session, session.begin():
        diff = await compute_sync_diff(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    by_model = {r.model_id: r for r in diff.rows}

    # claude: a within-threshold change -> updated, accurate old/new + pct.
    claude = by_model["claude-sonnet-4-5"]
    assert claude.status is DiffStatus.UPDATED
    assert claude.old_output == Decimal("15.0")
    assert claude.new_output == Decimal("16.0")
    assert claude.output_pct is not None
    assert abs(claude.output_pct - (1.0 / 15.0)) < 1e-9

    # embedding: a >10% rise -> increased (needs confirmation).
    emb = by_model["text-embedding-3-small"]
    assert emb.status is DiffStatus.INCREASED
    assert emb.old_input == Decimal("0.02")
    assert emb.new_input == Decimal("0.05")
    assert emb.input_pct is not None
    assert abs(emb.input_pct - 1.5) < 1e-9

    # gpt-new: not in the catalog -> added (no old prices).
    added = by_model["gpt-new"]
    assert added.status is DiffStatus.ADDED
    assert added.old_input is None
    assert added.new_input == Decimal("1.0")

    assert diff.has_large_increase is True
    assert diff.added == 1
    assert diff.increased == 1
    assert diff.updated == 1

    # The dry-run wrote NOTHING: still exactly the two seeded rows, unchanged.
    async with admin_session_factory() as session:
        rows = (await session.execute(select(ModelPrice))).scalars().all()
    assert len(rows) == 2
    assert {r.model_id for r in rows} == {"claude-sonnet-4-5", "text-embedding-3-small"}
    emb_row = next(r for r in rows if r.model_id == "text-embedding-3-small")
    assert emb_row.input_price == Decimal("0.02")  # untouched by the dry-run


@pytest.mark.asyncio
async def test_dry_run_flags_removed_models_not_in_feed(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)

    from api_server.pricing.litellm_sync import (
        DiffStatus,
        StaticPriceFeedFetcher,
        compute_sync_diff,
        sync_prices_from_litellm,
    )

    feed = _feed()
    async with admin_session_factory() as session, session.begin():
        await sync_prices_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    # A later feed drops the embedding model -> flagged removed (not deleted).
    feed.pop("text-embedding-3-small")

    async with admin_session_factory() as session, session.begin():
        diff = await compute_sync_diff(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    by_model = {r.model_id: r for r in diff.rows}
    removed = by_model["text-embedding-3-small"]
    assert removed.status is DiffStatus.REMOVED
    assert removed.new_input is None
    assert removed.old_input == Decimal("0.02")
    assert diff.removed == 1
    # task_11_17 lifecycle view: removed == discontinued, added == new.
    assert diff.discontinued == 1
    assert removed.model_id in {r.model_id for r in diff.discontinued_models()}


@pytest.mark.asyncio
async def test_apply_discontinue_missing_closes_period_not_deletes(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    """task_11_17: discontinue_missing CLOSES the open period — never deletes the row."""
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        apply_sync_from_litellm,
        sync_prices_from_litellm,
    )

    feed = _feed()
    async with admin_session_factory() as session, session.begin():
        await sync_prices_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    # The feed drops the embedding model.
    feed.pop("text-embedding-3-small")

    async with admin_session_factory() as session, session.begin():
        summary = await apply_sync_from_litellm(
            session,
            fetcher=StaticPriceFeedFetcher(payload=feed),
            discontinue_missing=True,
        )
    assert summary.discontinued == 1
    assert summary.discontinued_models[0].model_id == "text-embedding-3-small"

    # The row SURVIVES (not deleted) — its open period is now closed.
    async with admin_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(ModelPrice).where(ModelPrice.model_id == "text-embedding-3-small")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1  # still there
    assert rows[0].effective_to is not None  # but no longer current
    assert rows[0].input_price == Decimal("0.02")  # historical price intact


@pytest.mark.asyncio
async def test_apply_without_discontinue_missing_leaves_dropped_open(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    """The default apply does NOT flag dropped models (opt-in only)."""
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        apply_sync_from_litellm,
        sync_prices_from_litellm,
    )

    feed = _feed()
    async with admin_session_factory() as session, session.begin():
        await sync_prices_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    feed.pop("text-embedding-3-small")

    async with admin_session_factory() as session, session.begin():
        summary = await apply_sync_from_litellm(
            session, fetcher=StaticPriceFeedFetcher(payload=feed)
        )
    assert summary.discontinued == 0

    # The dropped model is still the current (open) price by default.
    async with admin_session_factory() as session:
        emb = (
            await session.execute(
                select(ModelPrice).where(
                    ModelPrice.model_id == "text-embedding-3-small",
                    ModelPrice.effective_to.is_(None),
                )
            )
        ).scalar_one()
    assert emb.input_price == Decimal("0.02")


# ===========================================================================
# Service: apply rejects an unconfirmed >10% rise; <=10% applies; confirm wins
# ===========================================================================
@pytest.mark.asyncio
async def test_apply_rejects_unconfirmed_large_increase(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        LargeIncreaseNotConfirmedError,
        StaticPriceFeedFetcher,
        apply_sync_from_litellm,
        sync_prices_from_litellm,
    )

    feed = _feed()
    async with admin_session_factory() as session, session.begin():
        await sync_prices_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    # Double the claude input price (+100% >> +10%).
    feed["claude-sonnet-4-5"]["input_cost_per_token"] = 0.000006  # 3.0 -> 6.0

    # Apply WITHOUT confirm -> the whole apply is rejected, nothing written.
    with pytest.raises(LargeIncreaseNotConfirmedError) as excinfo:
        async with admin_session_factory() as session, session.begin():
            await apply_sync_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=feed))
    assert len(excinfo.value.increases) == 1
    assert excinfo.value.increases[0].model_id == "claude-sonnet-4-5"

    # Catalog untouched: still a single open period at the old price.
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


@pytest.mark.asyncio
async def test_apply_with_confirm_applies_large_increase(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        apply_sync_from_litellm,
        sync_prices_from_litellm,
    )

    feed = _feed()
    async with admin_session_factory() as session, session.begin():
        await sync_prices_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    feed["claude-sonnet-4-5"]["input_cost_per_token"] = 0.000006  # 3.0 -> 6.0

    async with admin_session_factory() as session, session.begin():
        summary = await apply_sync_from_litellm(
            session, fetcher=StaticPriceFeedFetcher(payload=feed), confirm=True
        )
    assert summary.updated == 1

    # Effective dating: old period closed, new open period at the new price.
    async with admin_session_factory() as session:
        rows = (
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
    assert len(rows) == 2
    open_rows = [r for r in rows if r.effective_to is None]
    assert len(open_rows) == 1
    assert open_rows[0].input_price == Decimal("6.0")


@pytest.mark.asyncio
async def test_apply_small_change_applies_without_confirm(
    configured_app, admin_session_factory, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import (
        StaticPriceFeedFetcher,
        apply_sync_from_litellm,
        sync_prices_from_litellm,
    )

    feed = _feed()
    async with admin_session_factory() as session, session.begin():
        await sync_prices_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=feed))

    # A <=10% rise applies without any confirmation.
    feed["claude-sonnet-4-5"]["output_cost_per_token"] = 0.000016  # 15.0 -> 16.0 (+6.7%)

    async with admin_session_factory() as session, session.begin():
        summary = await apply_sync_from_litellm(
            session, fetcher=StaticPriceFeedFetcher(payload=feed)
        )
    assert summary.updated == 1
    assert summary.unchanged == 1  # the embedding row untouched

    async with admin_session_factory() as session:
        open_claude = (
            await session.execute(
                select(ModelPrice).where(
                    ModelPrice.model_id == "claude-sonnet-4-5",
                    ModelPrice.effective_to.is_(None),
                )
            )
        ).scalar_one()
    assert open_claude.output_price == Decimal("16.0")


# ===========================================================================
# Endpoints: diff (200, no write) + apply 409-then-200; tenant is 403
# ===========================================================================
@pytest.mark.asyncio
async def test_endpoint_diff_then_apply_confirmation_flow(
    configured_app, admin_session_factory, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    # The endpoints scope the sync to the active providers' families; seed
    # claude_sdk (→ anthropic) + azure_foundry (→ azure/azure_ai/openai) so the
    # fixture feed's anthropic + openai entries are both in scope.
    await _seed_active_providers(migrations_pg_dsn, ("claude_sdk", "azure_foundry"))
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    from api_server.db.model_prices import ModelPrice
    from api_server.pricing.litellm_sync import StaticPriceFeedFetcher, sync_prices_from_litellm

    base_feed = _feed()
    async with admin_session_factory() as session, session.begin():
        await sync_prices_from_litellm(session, fetcher=StaticPriceFeedFetcher(payload=base_feed))

    # The feed now doubles the claude input price (>10%).
    spiked = _feed()
    spiked["claude-sonnet-4-5"]["input_cost_per_token"] = 0.000006  # 3.0 -> 6.0
    _mock_httpx(monkeypatch, spiked)

    async with _client(configured_app) as client:
        # Step 1: dry-run diff -> 200, flags the large increase, writes nothing.
        diff_resp = await client.post("/admin/model-prices/sync/diff", json={}, headers=headers)
        assert diff_resp.status_code == 200, diff_resp.text
        diff_body = diff_resp.json()
        assert diff_body["has_large_increase"] is True
        assert diff_body["increased"] == 1

        # Step 2a: apply WITHOUT confirm -> 409 (the spike must be reviewed).
        rejected = await client.post("/admin/model-prices/sync/apply", json={}, headers=headers)
        assert rejected.status_code == 409, rejected.text
        detail = rejected.json()["detail"]
        assert detail["large_increases"][0]["model_id"] == "claude-sonnet-4-5"

        # The dry-run + the rejected apply both wrote nothing.
        async with admin_session_factory() as session:
            claude = (
                await session.execute(
                    select(ModelPrice).where(
                        ModelPrice.model_id == "claude-sonnet-4-5",
                        ModelPrice.effective_to.is_(None),
                    )
                )
            ).scalar_one()
        assert claude.input_price == Decimal("3.0")

        # Step 2b: apply WITH confirm -> 200 and the spike is applied.
        confirmed = await client.post(
            "/admin/model-prices/sync/apply", json={"confirm": True}, headers=headers
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["updated"] == 1

    async with admin_session_factory() as session:
        open_claude = (
            await session.execute(
                select(ModelPrice).where(
                    ModelPrice.model_id == "claude-sonnet-4-5",
                    ModelPrice.effective_to.is_(None),
                )
            )
        ).scalar_one()
    assert open_claude.input_price == Decimal("6.0")


@pytest.mark.asyncio
async def test_endpoint_tenant_admin_cannot_diff_or_apply(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        diff_resp = await client.post("/admin/model-prices/sync/diff", json={}, headers=headers)
        apply_resp = await client.post("/admin/model-prices/sync/apply", json={}, headers=headers)
    assert diff_resp.status_code == 403
    assert apply_resp.status_code == 403
