"""Scheduled exchange-rates fetcher beat task (Plan 11.1 task_11_1_02).

A Celery beat task, ``workers.fetch_exchange_rates``, that downloads the daily
reference rates from the configured source (ECB by default), parses them into
``currency -> rate_vs_usd`` rows (ECB publishes vs EUR — converted to vs-USD via
the USD rate), and upserts them into the global ``exchange_rates`` catalog for
the feed's ``as_of_date``. Wired to beat by :mod:`workers.beat_schedule` on a
CONFIGURABLE cadence (``WORKERS_FX_FETCH_CRON``, default daily 06:00 UTC) and
gated by a live ``fx_fetch_enabled`` PLATFORM setting a System Admin flips from
the admin panel. The SOURCE is also a live platform setting (``fx_source``,
default ECB) so a System Admin can switch feeds without a code change.

USD is canonical (Plan 11 task_11_13); a tenant's ``display_currency`` is a
DISPLAY concern converted on the fly with each execution's date. This job only
keeps the rate catalog fresh — it never touches cost (always USD).

Best-effort
-----------
Like the other beat tasks (:mod:`workers.maintenance` / :mod:`workers.price_sync`),
a single run failure must NOT crash beat: a fetch/parse error is caught, logged
AND alerted (a platform-scoped ops signal via the Plan 10 notifier), then the
task returns a summary dict; beat keeps firing on cadence. The catalog keeps its
last good rates, and conversion falls back to the most-recent-prior rate.

Multi-tenancy / RBAC
--------------------
``exchange_rates`` is platform-global with a global-read RLS policy + no write
policy (migration 0062). The upsert writes through the worker's BYPASSRLS
database role. A tenant CANNOT trigger or schedule it — the schedule lives in
the platform beat process and the enable flag / source are platform settings
only a System Admin can write. The failure alert is platform-scoped
(``tenant_id=None``) — a System-Admin ops signal, not a per-tenant one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.fx_fetcher")

#: The domain event an FX-fetch failure raises. Mapped to a priority-lane
#: notification in the dispatcher's EVENT_REGISTRY (Plan 10 task_10_04).
FX_FETCH_FAILED_EVENT = "fx_fetch_failed"


# ---------------------------------------------------------------------------
# Failure-alert seam (injectable; tests assert without a real broker)
# ---------------------------------------------------------------------------
class FxFetchNotifier(Protocol):
    """Raises an FX-fetch-failure alert (a platform-scoped ops signal)."""

    def alert_failure(self, *, source: str, error: str) -> None:  # pragma: no cover - Protocol
        ...


@dataclass(frozen=True)
class CeleryFxFetchNotifier:
    """Default :class:`FxFetchNotifier` — enqueues a Plan 10 event by name.

    Mirrors :class:`workers.credential_rotation.CeleryRotationNotifier`: the
    worker only PRODUCES the ``notification_dispatcher.dispatch_event`` task by
    name onto the priority lane — it never imports the dispatcher package (clean
    app boundary). The event is platform-scoped (``tenant_id=None``: a
    System-Admin ops signal). The ``context`` carries only the source name + the
    non-leaky error string.
    """

    broker_url: str
    dispatch_task: str = "notification_dispatcher.dispatch_event"
    priority_queue: str = "notifications.priority"

    def alert_failure(self, *, source: str, error: str) -> None:
        from celery import Celery

        event = {
            "event_type": FX_FETCH_FAILED_EVENT,
            "tenant_id": None,  # platform-scoped ops alert
            "context": {"source": source, "error": error},
        }
        Celery(broker=self.broker_url).send_task(
            self.dispatch_task,
            args=[event],
            queue=self.priority_queue,
        )


@app.task(name="workers.fetch_exchange_rates")  # type: ignore[untyped-decorator]
def fetch_exchange_rates() -> dict[str, Any]:
    """Fetch + upsert the daily FX reference rates (scheduled).

    Honours the ``fx_fetch_enabled`` platform setting (a System Admin's live OFF
    switch) and the ``fx_source`` setting (default ECB). Best-effort: a failure
    is logged + alerted, never raised, so beat keeps its cadence. Returns a small
    dict summarising the run.
    """
    settings = get_settings()
    notifier = CeleryFxFetchNotifier(
        broker_url=settings.broker_url,
        priority_queue="notifications.priority",
    )
    return asyncio.run(_fetch_exchange_rates(settings, notifier=notifier))


def _build_fetcher(settings: Settings) -> tuple[Any, Any]:
    """Resolve the production fetcher + its http client (ECB today).

    Lazily imports httpx + the api_server fetcher so a disabled run never
    touches the network stack and a worker that never routes the beat schedule
    doesn't pay the import cost (mirrors workers.price_sync). Only ECB is wired
    today; an unknown source falls back to ECB at the platform-setting read
    (``get_fx_source``), so this always builds the ECB fetcher. The httpx client
    lifecycle is owned by the caller (closed in the ``finally`` of the core).
    """
    import httpx
    from api_server.fx.fetcher import EcbRateFetcher

    client = httpx.AsyncClient()
    return EcbRateFetcher(client=client, url=settings.ecb_fx_feed_url), client


async def _fetch_exchange_rates(
    settings: Settings,
    *,
    notifier: FxFetchNotifier | None,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    """Async core — owns the engine lifecycle (mirrors workers.price_sync).

    ``fetcher`` is injectable so tests drive the whole flow against a static,
    fixture-feeding fetcher with NO real network. A real run resolves the ECB
    fetcher from settings (see :func:`fetch_exchange_rates`).
    """
    # Lazy import — avoids paying the api_server import cost on workers that
    # never route the beat schedule (mirrors workers.maintenance / price_sync).
    from api_server.db.platform_settings import get_fx_fetch_enabled, get_fx_source
    from api_server.fx.fetcher import FxFeedError, fetch_and_upsert_rates

    engine = create_async_engine(settings.database_url)
    http_client: Any | None = None
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        # 1) Live enable/disable lever + source selection (System-Admin platform
        #    settings). When OFF the run is a no-op — no feed fetch, no write.
        async with sessionmaker() as db:
            enabled = await get_fx_fetch_enabled(db)
            source = await get_fx_source(db)
        if not enabled:
            _log.info("fx_fetch.skipped", reason="disabled")
            return {"enabled": False, "skipped": True}

        # 2) Resolve the fetcher (injected in tests; ECB over httpx in prod).
        if fetcher is None:
            fetcher, http_client = _build_fetcher(settings)

        # 3) Fetch + parse + upsert on the BYPASSRLS worker session.
        async with sessionmaker() as db, db.begin():
            summary = await fetch_and_upsert_rates(db, fetcher=fetcher, source=source)
    except FxFeedError as exc:
        _log.warning("fx_fetch.feed_error", error=str(exc), source=source)
        if notifier is not None:
            notifier.alert_failure(source=source, error=str(exc))
        return {"enabled": True, "ok": False, "error": str(exc), "source": source}
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("fx_fetch.error", error=str(exc))
        if notifier is not None:
            notifier.alert_failure(source=source, error=str(exc))
        return {"enabled": True, "ok": False, "error": str(exc)}
    finally:
        if http_client is not None:
            await http_client.aclose()
        await engine.dispose()

    _log.info(
        "fx_fetch.done",
        source=summary.source,
        as_of_date=summary.as_of_date.isoformat() if summary.as_of_date else None,
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        unchanged=summary.unchanged,
    )
    return {
        "enabled": True,
        "ok": True,
        "source": summary.source,
        "as_of_date": summary.as_of_date.isoformat() if summary.as_of_date else None,
        "fetched": summary.fetched,
        "created": summary.created,
        "updated": summary.updated,
        "unchanged": summary.unchanged,
    }


__all__ = [
    "FX_FETCH_FAILED_EVENT",
    "CeleryFxFetchNotifier",
    "FxFetchNotifier",
    "fetch_exchange_rates",
]
