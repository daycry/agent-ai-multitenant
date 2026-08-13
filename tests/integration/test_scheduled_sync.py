"""Integration tests for the scheduled price-catalog sync (Plan 11 task_11_18).

The scheduled sync is a Celery beat task, ``workers.sync_model_prices``, that
periodically refreshes ``model_prices`` from the community LiteLLM price JSON
(ADR 0021: a DATA FEED only — NOT a provider runtime; no ``litellm`` dep).

The network is **fully mocked** — these tests never hit a real LiteLLM URL.
The beat task builds an ``HttpxPriceFeedFetcher`` internally, so we monkeypatch
that symbol in ``api_server.pricing.litellm_sync`` to a static, fixture-feeding
fetcher (an ``httpx.MockTransport`` would also work; this is simpler and just as
network-free).

What is asserted (task_11_18):

  * the beat task is registered on the Celery app and the schedule reads its
    cadence from config (``Settings.price_sync_cron``) — not a hardcoded magic
    schedule, and the entry can be turned off via the platform setting;
  * a scheduled run applies SAFE (<=10%) changes automatically;
  * a >10% spike is HELD for manual confirm (NOT auto-applied) and recorded;
  * disabling the schedule (``price_sync_enabled=false``) is honoured — the run
    is a no-op (no feed fetch, no catalog write).

Fixture / DB wiring mirrors ``test_sync_prices_litellm.py``.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from alembic import command
from sqlalchemy import select

pytestmark = pytest.mark.integration


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
    }


# ---------------------------------------------------------------------------
# Fixtures: schema up + clean catalog (no app server needed for the task).
# ---------------------------------------------------------------------------
@pytest.fixture()
def migrated_db(alembic_config, migrations_pg_dsn: str):
    command.upgrade(alembic_config, "head")

    async def _truncate() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "TRUNCATE model_prices, llm_providers, platform_settings RESTART IDENTITY CASCADE"
            )
        finally:
            await conn.close()

    asyncio.run(_truncate())
    return migrations_pg_dsn


async def _seed_active_providers(dsn: str, kinds: tuple[str, ...]) -> None:
    """Insert one ACTIVE platform provider per kind (BYPASSRLS migrations user).

    The scheduled sync scopes to the active providers' families (plan
    price-sync-active-providers), so a run that should import the fixture feed
    must seed the provider kinds whose families (ADR 0028) cover it."""
    from uuid import uuid4

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


def _worker_settings(admin_database_url: str, *, cron: str = "0 4 * * *"):
    """A workers Settings pointed at the (BYPASSRLS) test DB."""
    from workers.config import Settings

    return Settings(
        database_url=admin_database_url,
        price_sync_cron=cron,
        litellm_price_feed_url="http://feed.invalid/model_prices.json",
    )


def _patch_fetcher(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    """Replace HttpxPriceFeedFetcher with a static one — NO real network.

    The beat task builds ``HttpxPriceFeedFetcher(client=..., url=...)`` inside
    its async core. We swap the symbol the task imports for a constructor that
    ignores its kwargs and serves the fixture payload.
    """
    import api_server.pricing.litellm_sync as ls

    def _fake_fetcher(*_args: Any, **_kwargs: Any) -> ls.StaticPriceFeedFetcher:
        return ls.StaticPriceFeedFetcher(payload=payload)

    monkeypatch.setattr(ls, "HttpxPriceFeedFetcher", _fake_fetcher)


async def _set_enabled(dsn: str, enabled: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value) VALUES ('price_sync_enabled', $1::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            "true" if enabled else "false",
        )
    finally:
        await conn.close()


# ===========================================================================
# The beat task is registered + the schedule reads its cadence from config
# ===========================================================================
def test_beat_task_registered_and_schedule_from_config() -> None:
    # The task name is registered on the Celery app (importing the module
    # registers it against `app`).
    import workers.price_sync  # noqa: F401  (registers the task)
    from workers.beat_schedule import (
        PRICE_SYNC_BEAT_ENTRY,
        build_beat_schedule,
    )
    from workers.celery_app import build_celery_app
    from workers.config import Settings

    app = build_celery_app(
        Settings(broker_url="redis://localhost:6379/1", result_backend="redis://localhost:6379/2")
    )
    assert "workers.sync_model_prices" in app.tasks

    # The schedule carries the price-sync entry, pointed at the task, NOT a
    # hardcoded magic cadence — it comes from Settings.price_sync_cron.
    default_sched = build_beat_schedule(Settings())
    assert PRICE_SYNC_BEAT_ENTRY in default_sched
    entry = default_sched[PRICE_SYNC_BEAT_ENTRY]
    assert entry["task"] == "workers.sync_model_prices"

    from celery.schedules import crontab

    # Default cadence: daily 04:00 UTC.
    assert isinstance(entry["schedule"], crontab)
    assert entry["schedule"].hour == {4}
    assert entry["schedule"].minute == {0}

    # A different configured cron is honoured (every 6 hours here).
    custom = build_beat_schedule(Settings(price_sync_cron="30 */6 * * *"))
    custom_cron = custom[PRICE_SYNC_BEAT_ENTRY]["schedule"]
    assert isinstance(custom_cron, crontab)
    assert custom_cron.minute == {30}
    assert custom_cron.hour == {0, 6, 12, 18}


def test_malformed_cron_falls_back_to_daily_0400() -> None:
    from celery.schedules import crontab
    from workers.beat_schedule import PRICE_SYNC_BEAT_ENTRY, build_beat_schedule
    from workers.config import Settings

    sched = build_beat_schedule(Settings(price_sync_cron="not a cron"))
    entry = sched[PRICE_SYNC_BEAT_ENTRY]["schedule"]
    assert isinstance(entry, crontab)
    assert entry.hour == {4}
    assert entry.minute == {0}


# ===========================================================================
# A scheduled run applies SAFE changes automatically
# ===========================================================================
@pytest.mark.asyncio
async def test_scheduled_run_applies_safe_changes(
    migrated_db: str, admin_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_server.db.model_prices import ModelPrice
    from workers.price_sync import _sync_model_prices

    # Scope: claude_sdk (→ anthropic) + azure_foundry (→ openai) cover the feed.
    await _seed_active_providers(migrated_db, ("claude_sdk", "azure_foundry"))
    _patch_fetcher(monkeypatch, _feed())
    settings = _worker_settings(admin_database_url)

    result = await _sync_model_prices(settings)

    # Both mappable models are created automatically (no confirmation needed).
    assert result["enabled"] is True
    assert result["created"] == 2
    assert result["held_large_increases"] == 0

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            rows = (
                (await db.execute(select(ModelPrice).order_by(ModelPrice.model_id))).scalars().all()
            )
    finally:
        await engine.dispose()

    by_model = {r.model_id: r for r in rows}
    assert set(by_model) == {"claude-sonnet-4-5", "text-embedding-3-small"}
    claude = by_model["claude-sonnet-4-5"]
    assert claude.source == "litellm"  # written by the feed, USD canonical
    assert claude.input_price == Decimal("3.0")
    assert claude.output_price == Decimal("15.0")
    assert claude.effective_to is None  # open (current) period


# ===========================================================================
# A >10% spike is HELD for manual confirm (NOT auto-applied) + recorded
# ===========================================================================
@pytest.mark.asyncio
async def test_scheduled_run_holds_large_increase_for_manual_confirm(
    migrated_db: str, admin_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_server.db.model_prices import ModelPrice
    from workers.price_sync import _sync_model_prices

    # Scope: claude_sdk (→ anthropic) + azure_foundry (→ openai) cover the feed.
    await _seed_active_providers(migrated_db, ("claude_sdk", "azure_foundry"))
    settings = _worker_settings(admin_database_url)

    # First scheduled run seeds the catalog.
    _patch_fetcher(monkeypatch, _feed())
    first = await _sync_model_prices(settings)
    assert first["created"] == 2

    # The feed now DOUBLES the claude input price (+100% >> +10%). A scheduled
    # run must NOT auto-apply it — it is held for manual confirmation.
    spiked = _feed()
    spiked["claude-sonnet-4-5"]["input_cost_per_token"] = 0.000006  # 3.0 -> 6.0
    _patch_fetcher(monkeypatch, spiked)

    second = await _sync_model_prices(settings)
    assert second["updated"] == 0  # the spike was NOT applied
    assert second["held_large_increases"] == 1  # ...it was recorded/held

    # The catalog still carries the OLD price (single open period, untouched).
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            claude_rows = (
                (
                    await db.execute(
                        select(ModelPrice).where(ModelPrice.model_id == "claude-sonnet-4-5")
                    )
                )
                .scalars()
                .all()
            )
    finally:
        await engine.dispose()

    assert len(claude_rows) == 1
    assert claude_rows[0].input_price == Decimal("3.0")  # NOT raised to 6.0
    assert claude_rows[0].effective_to is None


# ===========================================================================
# Disabling the schedule is honoured — the run is a no-op
# ===========================================================================
@pytest.mark.asyncio
async def test_disabled_schedule_is_a_noop(
    migrated_db: str, admin_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api_server.db.model_prices import ModelPrice
    from workers.price_sync import _sync_model_prices

    # A System Admin turned the scheduled sync OFF.
    await _set_enabled(migrated_db, enabled=False)

    # Wire a fetcher that explodes if touched — proving the disabled run never
    # fetches the feed nor writes the catalog.
    import api_server.pricing.litellm_sync as ls

    def _exploding_fetcher(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("disabled run must not build a feed fetcher")

    monkeypatch.setattr(ls, "HttpxPriceFeedFetcher", _exploding_fetcher)

    settings = _worker_settings(admin_database_url)
    result = await _sync_model_prices(settings)

    assert result == {"enabled": False, "skipped": True}

    # The catalog stayed empty — no write happened.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            count = len((await db.execute(select(ModelPrice))).scalars().all())
    finally:
        await engine.dispose()
    assert count == 0
