"""Scheduled price-catalog sync (Plan 11 task_11_18).

A Celery beat task, ``workers.sync_model_prices``, that periodically refreshes
the platform price catalog (``model_prices``) from the community LiteLLM price
JSON. Wired to beat by :mod:`workers.beat_schedule` on a CONFIGURABLE cadence
(``WORKERS_PRICE_SYNC_CRON``, default daily 04:00 UTC) and gated by a live
``price_sync_enabled`` PLATFORM setting a System Admin flips from the admin
panel.

What this is — and what it is NOT (ADR 0021)
--------------------------------------------
The LiteLLM JSON is read **purely as a data feed** to refresh the catalog. It
does NOT make the platform use LiteLLM as a provider runtime — the closed
runtime catalog of ADR 0021 (Claude SDK + Copilot + Azure Foundry APIM +
Ollama) is untouched. There is intentionally **no ``litellm`` dependency**.

Multi-tenancy / RBAC
--------------------
``model_prices`` is platform-global; the sync writes through the worker's
BYPASSRLS database role (``WORKERS_DATABASE_URL`` — the same admin-grade role
the worker already uses for ``executions``). A tenant CANNOT trigger or
schedule it: the schedule lives in the platform's beat process and the
enable flag is a platform setting only a System Admin can write.

The +10% guard, even when scheduled
------------------------------------
A scheduled run applies non-spiking changes automatically but MUST NOT
auto-apply a price rise above +10%. It reuses the task_11_16 guard by calling
:func:`sync_prices_from_litellm` with ``confirm_large_increases=False``: such a
rise is DEFERRED (not written) and recorded under ``large_increases`` so a
human confirms it explicitly from the admin panel (``POST .../sync/apply`` with
``confirm=true``). The summary the task returns/logs surfaces the held spikes.

Best-effort
-----------
Like the other beat tasks (:mod:`workers.maintenance`), a single run failure
must not crash beat: the task catches its own exceptions and logs them; beat
keeps firing on cadence.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

_log = structlog.get_logger("workers.price_sync")


@app.task(name="workers.sync_model_prices")  # type: ignore[untyped-decorator]
def sync_model_prices() -> dict[str, Any]:
    """Refresh the price catalog from the LiteLLM feed (scheduled).

    Honours the ``price_sync_enabled`` platform setting (a System Admin's
    live OFF switch) and the +10% confirmation guard (a spike is held, not
    applied). Best-effort: a failure is logged, never raised, so beat keeps
    its cadence. Returns a small dict summarising the run (also the audit
    surface task_11_19 will build on).
    """
    settings = get_settings()
    return asyncio.run(_sync_model_prices(settings))


async def _sync_model_prices(settings: Settings) -> dict[str, Any]:
    """Async core — owns the engine lifecycle (mirrors workers.maintenance)."""
    # Lazy import — avoids paying the api_server import cost on workers that
    # never route the beat schedule (mirrors workers.maintenance).
    from api_server.db.platform_settings import get_price_sync_enabled
    from api_server.db.price_sync_audit import SyncTrigger
    from api_server.pricing.litellm_sync import (
        HttpxPriceFeedFetcher,
        PriceFeedError,
        StaticPriceFeedFetcher,
        active_litellm_families,
        sync_prices_from_litellm,
    )
    from api_server.pricing.sync_audit import write_sync_audit

    engine = worker_engine(settings)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        # 1) Live enable/disable lever (System-Admin platform setting). When
        #    OFF the run is a no-op — no feed fetch, no catalog write.
        async with sessionmaker() as db:
            enabled = await get_price_sync_enabled(db)
        if not enabled:
            _log.info("price_sync.skipped", reason="disabled")
            return {"enabled": False, "skipped": True}

        # 2) Run the sync on the BYPASSRLS worker session. Lazily import httpx
        #    so a disabled run never touches the network stack.
        import httpx

        async with httpx.AsyncClient() as client:
            fetcher: HttpxPriceFeedFetcher | StaticPriceFeedFetcher = HttpxPriceFeedFetcher(
                client=client, url=settings.litellm_price_feed_url
            )
            async with sessionmaker() as db, db.begin():
                # plan price-sync-active-providers (task_psa_01): the scheduled
                # sync respects the active providers' families exactly like the
                # manual endpoint (System-Admin override wins; 0 active ⇒ empty
                # ⇒ nothing imported + every catalog family closed out-of-scope).
                allowed_families = await active_litellm_families(db)
                # confirm_large_increases=False: a >10% rise is DEFERRED (held
                # for manual confirm), not auto-applied — even scheduled.
                summary = await sync_prices_from_litellm(
                    db,
                    fetcher=fetcher,
                    confirm_large_increases=False,
                    allowed_families=allowed_families,
                )
                # task_11_19: a scheduled sync leaves the SAME immutable audit
                # trail as a manual one — attributed to the "scheduler" (no
                # user), written in the same transaction as the catalog writes
                # so nothing is silently applied. The held spikes + compact
                # diff land in the row's `diff`.
                await write_sync_audit(
                    db,
                    summary=summary,
                    trigger=SyncTrigger.SCHEDULED,
                    actor_user_id=None,
                    feed_url=settings.litellm_price_feed_url,
                    confirmed=False,
                )
    except PriceFeedError as exc:
        _log.warning("price_sync.feed_error", error=str(exc))
        return {"enabled": True, "error": str(exc)}
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("price_sync.error", error=str(exc))
        return {"enabled": True, "error": str(exc)}
    finally:
        await engine.dispose()

    held = len(summary.large_increases)
    _log.info(
        "price_sync.done",
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        unchanged=summary.unchanged,
        held_large_increases=held,
        skipped=summary.skipped_count,
    )
    return {
        "enabled": True,
        "fetched": summary.fetched,
        "created": summary.created,
        "updated": summary.updated,
        "unchanged": summary.unchanged,
        "held_large_increases": held,
        "skipped": summary.skipped_count,
    }
