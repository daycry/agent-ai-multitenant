"""Integration tests for the exchange-rates-fetcher beat job (Plan 11.1 task_11_1_02).

The FX fetcher is a Celery beat task, ``workers.fetch_exchange_rates``, that
downloads the daily reference rates from the configured source (ECB by default),
parses them into ``currency -> rate_vs_usd`` rows (ECB publishes vs EUR — the
parser converts to vs-USD via the USD rate), and upserts them into the global
``exchange_rates`` catalog for the feed's ``as_of_date``.

The network is **fully mocked** — these tests never hit a real ECB URL. The
fetch goes through an injectable :class:`StaticFxRateFetcher` (a fixture XML
body); the beat task's async core (`_fetch_exchange_rates`) accepts an injected
fetcher so the whole flow runs network-free.

What is asserted (task_11_1_02):

  * the pure parser turns a fixture ECB feed into the correct vs-USD rows (ECB
    vs-EUR → vs-USD via the USD anchor; USD itself is never stored; EUR derived
    from the USD rate);
  * the beat task is registered on the Celery app and the schedule reads its
    cadence from config (``Settings.fx_fetch_cron``) — not a hardcoded magic
    schedule;
  * the source is configurable (the ``fx_source`` platform setting drives the
    run; an unknown value falls back to ECB);
  * a network/feed failure is handled gracefully — the run is best-effort
    (logged + alerted, NEVER raised) and writes nothing;
  * the upsert is idempotent (re-running the same feed is a no-op).
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import asyncpg
import pytest
from alembic import command
from sqlalchemy import select

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# A fixture ECB daily reference-rates feed (rates vs EUR, the upstream shape).
# USD=1.08 EUR, GBP=0.85 EUR, JPY=168 EUR.
# ---------------------------------------------------------------------------
def _ecb_feed(time: str = "2026-05-29") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"'
        ' xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
        "<gesmes:subject>Reference rates</gesmes:subject>"
        "<Cube>"
        f'<Cube time="{time}">'
        '<Cube currency="USD" rate="1.08"/>'
        '<Cube currency="GBP" rate="0.85"/>'
        '<Cube currency="JPY" rate="168.0"/>'
        "</Cube>"
        "</Cube>"
        "</gesmes:Envelope>"
    )


# ---------------------------------------------------------------------------
# Fixtures: schema up + clean catalog/settings (no app server needed).
# ---------------------------------------------------------------------------
@pytest.fixture()
def migrated_db(alembic_config, migrations_pg_dsn: str):
    command.upgrade(alembic_config, "head")

    async def _truncate() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "TRUNCATE exchange_rates, platform_settings RESTART IDENTITY CASCADE"
            )
        finally:
            await conn.close()

    asyncio.run(_truncate())
    return migrations_pg_dsn


def _worker_settings(admin_database_url: str, *, cron: str = "0 6 * * *"):
    """A workers Settings pointed at the (BYPASSRLS) test DB."""
    from workers.config import Settings

    return Settings(
        database_url=admin_database_url,
        fx_fetch_cron=cron,
        ecb_fx_feed_url="http://feed.invalid/eurofxref-daily.xml",
    )


def _static_fetcher(body: str):
    from api_server.fx.fetcher import StaticFxRateFetcher

    return StaticFxRateFetcher(body=body)


async def _set_setting(dsn: str, key: str, value_json: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key,
            value_json,
        )
    finally:
        await conn.close()


class _RecordingNotifier:
    """An in-memory :class:`FxFetchNotifier` — records alerts, no real broker."""

    def __init__(self) -> None:
        self.alerts: list[dict[str, str]] = []

    def alert_failure(self, *, source: str, error: str) -> None:
        self.alerts.append({"source": source, "error": error})


# ===========================================================================
# Pure parser: a fixture ECB feed -> correct vs-USD rows
# ===========================================================================
def test_parse_ecb_feed_converts_to_vs_usd() -> None:
    from api_server.fx.fetcher import parse_ecb_feed

    parsed = parse_ecb_feed(_ecb_feed("2026-05-29"))

    assert parsed.as_of_date == date(2026, 5, 29)
    by_ccy = {r.currency: r.rate_vs_usd for r in parsed.rates}

    # USD is the identity — never stored as a row.
    assert "USD" not in by_ccy

    # EUR vs USD = 1 / (USD per EUR) = 1 / 1.08.
    assert by_ccy["EUR"] == (Decimal(1) / Decimal("1.08")).quantize(Decimal("0.0000000001"))
    # GBP vs USD = (GBP per EUR) / (USD per EUR) = 0.85 / 1.08.
    assert by_ccy["GBP"] == (Decimal("0.85") / Decimal("1.08")).quantize(Decimal("0.0000000001"))
    # JPY vs USD = 168 / 1.08 (a large-unit currency stays exact).
    assert by_ccy["JPY"] == (Decimal("168.0") / Decimal("1.08")).quantize(Decimal("0.0000000001"))


def test_parse_ecb_feed_without_usd_anchor_raises() -> None:
    from api_server.fx.fetcher import FxFeedError, parse_ecb_feed

    no_usd = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"'
        ' xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
        '<Cube><Cube time="2026-05-29"><Cube currency="GBP" rate="0.85"/>'
        "</Cube></Cube></gesmes:Envelope>"
    )
    with pytest.raises(FxFeedError):
        parse_ecb_feed(no_usd)


def test_parse_feed_unknown_source_raises() -> None:
    from api_server.fx.fetcher import FxFeedError, parse_feed

    with pytest.raises(FxFeedError):
        parse_feed("not-a-source", _ecb_feed())


# ===========================================================================
# The beat task is registered + the schedule reads its cadence from config
# ===========================================================================
def test_beat_task_registered_and_schedule_from_config() -> None:
    import workers.fx_fetcher  # noqa: F401  (registers the task)
    from celery.schedules import crontab
    from workers.beat_schedule import FX_FETCH_BEAT_ENTRY, build_beat_schedule
    from workers.celery_app import build_celery_app
    from workers.config import Settings

    app = build_celery_app(
        Settings(broker_url="redis://localhost:6379/1", result_backend="redis://localhost:6379/2")
    )
    assert "workers.fetch_exchange_rates" in app.tasks

    # The schedule carries the FX-fetch entry, pointed at the task, with the
    # cadence from Settings.fx_fetch_cron — NOT a hardcoded magic schedule.
    default_sched = build_beat_schedule(Settings())
    assert FX_FETCH_BEAT_ENTRY in default_sched
    entry = default_sched[FX_FETCH_BEAT_ENTRY]
    assert entry["task"] == "workers.fetch_exchange_rates"
    assert isinstance(entry["schedule"], crontab)
    # Default cadence: daily 06:00 UTC.
    assert entry["schedule"].hour == {6}
    assert entry["schedule"].minute == {0}

    # A different configured cron is honoured (every 12 hours here).
    custom = build_beat_schedule(Settings(fx_fetch_cron="15 */12 * * *"))
    custom_cron = custom[FX_FETCH_BEAT_ENTRY]["schedule"]
    assert isinstance(custom_cron, crontab)
    assert custom_cron.minute == {15}
    assert custom_cron.hour == {0, 12}


# ===========================================================================
# A scheduled run fetches + parses + upserts the rows (vs-USD)
# ===========================================================================
@pytest.mark.asyncio
async def test_scheduled_run_upserts_vs_usd_rows(migrated_db: str, admin_database_url: str) -> None:
    from api_server.db.exchange_rates import ExchangeRate
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from workers.fx_fetcher import _fetch_exchange_rates

    settings = _worker_settings(admin_database_url)
    result = await _fetch_exchange_rates(
        settings, notifier=None, fetcher=_static_fetcher(_ecb_feed("2026-05-29"))
    )

    assert result["enabled"] is True
    assert result["ok"] is True
    assert result["source"] == "ecb"
    assert result["as_of_date"] == "2026-05-29"
    # EUR + GBP + JPY are written; USD is the identity, never stored.
    assert result["created"] == 3
    assert result["updated"] == 0

    engine = create_async_engine(admin_database_url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            rows = (await db.execute(select(ExchangeRate))).scalars().all()
    finally:
        await engine.dispose()

    by_ccy = {r.currency: r for r in rows}
    assert set(by_ccy) == {"EUR", "GBP", "JPY"}
    assert "USD" not in by_ccy
    assert all(r.as_of_date == date(2026, 5, 29) for r in rows)
    assert all(r.source == "ecb" for r in rows)
    assert by_ccy["GBP"].rate_vs_usd == (Decimal("0.85") / Decimal("1.08")).quantize(
        Decimal("0.0000000001")
    )


# ===========================================================================
# The upsert is idempotent — re-running the same feed writes nothing new
# ===========================================================================
@pytest.mark.asyncio
async def test_scheduled_run_is_idempotent(migrated_db: str, admin_database_url: str) -> None:
    from workers.fx_fetcher import _fetch_exchange_rates

    settings = _worker_settings(admin_database_url)
    first = await _fetch_exchange_rates(
        settings, notifier=None, fetcher=_static_fetcher(_ecb_feed("2026-05-29"))
    )
    assert first["created"] == 3

    second = await _fetch_exchange_rates(
        settings, notifier=None, fetcher=_static_fetcher(_ecb_feed("2026-05-29"))
    )
    assert second["created"] == 0
    assert second["updated"] == 0
    assert second["unchanged"] == 3


# ===========================================================================
# The source is configurable (fx_source platform setting drives the run)
# ===========================================================================
@pytest.mark.asyncio
async def test_source_is_configurable(migrated_db: str, admin_database_url: str) -> None:
    from workers.fx_fetcher import _fetch_exchange_rates

    # An explicit ecb source is honoured.
    await _set_setting(migrated_db, "fx_source", '"ecb"')
    settings = _worker_settings(admin_database_url)
    result = await _fetch_exchange_rates(
        settings, notifier=None, fetcher=_static_fetcher(_ecb_feed())
    )
    assert result["ok"] is True
    assert result["source"] == "ecb"

    # An UNKNOWN configured source falls back to ECB (never crashes the run).
    await _set_setting(migrated_db, "fx_source", '"some-future-feed"')
    result2 = await _fetch_exchange_rates(
        settings, notifier=None, fetcher=_static_fetcher(_ecb_feed())
    )
    assert result2["ok"] is True
    assert result2["source"] == "ecb"


# ===========================================================================
# Disabling the schedule is honoured — the run is a no-op
# ===========================================================================
@pytest.mark.asyncio
async def test_disabled_schedule_is_a_noop(migrated_db: str, admin_database_url: str) -> None:
    from api_server.db.exchange_rates import ExchangeRate
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from workers.fx_fetcher import _fetch_exchange_rates

    await _set_setting(migrated_db, "fx_fetch_enabled", "false")

    # A fetcher that explodes if touched proves the disabled run never fetches.
    class _Exploding:
        async def fetch(self) -> str:
            raise AssertionError("disabled run must not fetch the feed")

    settings = _worker_settings(admin_database_url)
    result = await _fetch_exchange_rates(settings, notifier=None, fetcher=_Exploding())
    assert result == {"enabled": False, "skipped": True}

    engine = create_async_engine(admin_database_url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            count = len((await db.execute(select(ExchangeRate))).scalars().all())
    finally:
        await engine.dispose()
    assert count == 0


# ===========================================================================
# A network/feed failure is handled gracefully (best-effort: logged + alerted,
# never raised; writes nothing)
# ===========================================================================
@pytest.mark.asyncio
async def test_network_failure_is_handled_gracefully(
    migrated_db: str, admin_database_url: str
) -> None:
    from api_server.db.exchange_rates import ExchangeRate
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from workers.fx_fetcher import _fetch_exchange_rates

    class _Failing:
        async def fetch(self) -> str:
            raise ConnectionError("ECB unreachable")

    notifier = _RecordingNotifier()
    settings = _worker_settings(admin_database_url)

    # Best-effort: the connection error is caught, not raised.
    result = await _fetch_exchange_rates(settings, notifier=notifier, fetcher=_Failing())
    assert result["enabled"] is True
    assert result["ok"] is False
    assert "ECB unreachable" in result["error"]

    # An ops alert (a platform-scoped failure signal) was raised.
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0]["source"] == "ecb"
    assert "ECB unreachable" in notifier.alerts[0]["error"]

    # Nothing was written.
    engine = create_async_engine(admin_database_url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            count = len((await db.execute(select(ExchangeRate))).scalars().all())
    finally:
        await engine.dispose()
    assert count == 0


@pytest.mark.asyncio
async def test_malformed_feed_alerts_and_writes_nothing(
    migrated_db: str, admin_database_url: str
) -> None:
    from api_server.db.exchange_rates import ExchangeRate
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from workers.fx_fetcher import _fetch_exchange_rates

    notifier = _RecordingNotifier()
    settings = _worker_settings(admin_database_url)

    # A garbage body is a FxFeedError — caught, alerted, no write.
    result = await _fetch_exchange_rates(
        settings, notifier=notifier, fetcher=_static_fetcher("not xml at all")
    )
    assert result["ok"] is False
    assert len(notifier.alerts) == 1

    engine = create_async_engine(admin_database_url)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            count = len((await db.execute(select(ExchangeRate))).scalars().all())
    finally:
        await engine.dispose()
    assert count == 0


# ===========================================================================
# The new fx_fetch_failed event is in the dispatcher registry + has builtins.
# ===========================================================================
def test_fx_fetch_failed_event_registered_with_builtins() -> None:
    from notification_dispatcher.event_mapping import registry_event_types
    from notification_dispatcher.templates import BUILTIN_TEMPLATES
    from workers.fx_fetcher import FX_FETCH_FAILED_EVENT

    assert FX_FETCH_FAILED_EVENT in registry_event_types()
    for locale in ("es", "en"):
        assert (FX_FETCH_FAILED_EVENT, locale) in BUILTIN_TEMPLATES
