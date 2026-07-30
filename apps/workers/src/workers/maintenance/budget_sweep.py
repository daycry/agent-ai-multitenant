"""Per-tenant budget sweep — `workers.refresh_budgets`, every 5 min
(prod-06 task_prod06_budget_01 / db-1). Best-effort: never crashes beat.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

_log = structlog.get_logger("workers.maintenance")


@app.task(name="workers.refresh_budgets")  # type: ignore[untyped-decorator]
def refresh_budgets() -> dict[str, Any]:
    """Periodic per-tenant budget sweep: re-derive the auto-pause flags and fire
    any threshold alerts.

    The dispatch START path reads ``paused_by_budget`` (``budget_pause_block``)
    but NOTHING wrote it in production (db-1): ``refresh_budget_pause_flags`` +
    ``maybe_alert_budgets`` had only tests as callers. The worker's
    post-execution hook keeps a single run's over-budget immediate; this beat is
    the safety net — it auto-clears a pause when a new period drops a scope back
    under 100%, and catches a missed hook or a manual spend correction. Cheap
    (one consumption query per tenant) and best-effort (per-tenant failures are
    isolated; never crashes beat)."""
    return asyncio.run(_refresh_budgets_async(get_settings()))


async def _refresh_budgets_async(
    settings: Settings,
    *,
    dispatcher: Any | None = None,
) -> dict[str, Any]:
    """Async core — owns the engine lifecycle. ``dispatcher`` is injectable so a
    test asserts the alert fan-out without a real broker; production builds the
    Celery dispatcher."""
    from api_server.budgets import sweep_tenant_budgets
    from api_server.budgets.consumption import CeleryBudgetAlertDispatcher
    from api_server.db.models import Organization
    from sqlalchemy import select

    engine = worker_engine(settings)
    tenants = 0
    newly_paused = 0
    newly_cleared = 0
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            tenant_ids = list(
                (
                    await db.execute(
                        select(Organization.id).where(Organization.deleted_at.is_(None))
                    )
                ).scalars()
            )
        alert_dispatcher = dispatcher if dispatcher is not None else CeleryBudgetAlertDispatcher()
        for tenant_id in tenant_ids:
            try:
                async with sessionmaker() as db, db.begin():
                    result = await sweep_tenant_budgets(
                        db, tenant_id=tenant_id, dispatcher=alert_dispatcher
                    )
                tenants += 1
                newly_paused += len(result.refresh.newly_paused)
                newly_cleared += len(result.refresh.newly_cleared)
            except Exception as exc:  # isolate a single tenant's failure
                _log.warning(
                    "maintenance.refresh_budgets.tenant_error",
                    tenant_id=str(tenant_id),
                    error=str(exc),
                )
    finally:
        await engine.dispose()

    _log.info(
        "maintenance.refresh_budgets.done",
        tenants=tenants,
        newly_paused=newly_paused,
        newly_cleared=newly_cleared,
    )
    return {"tenants": tenants, "newly_paused": newly_paused, "newly_cleared": newly_cleared}
