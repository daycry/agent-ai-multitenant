"""Acceptance-timeout escalation beat task (Plan 16 task_16_06).

A Celery beat task, ``workers.escalate_human_assignments``, that periodically
sweeps the ``pending_acceptance`` :class:`HumanTaskAssignment` rows whose age
exceeds the Human Agent's ``acceptance_timeout_hours`` and either reassigns them
to the ``escalation_target_user_id`` (notifying the target) or, when escalation
is exhausted, blocks the task and notifies the Tenant Admin. The domain logic
lives in :mod:`api_server.human_agents.escalation`; this module is the thin
Celery + notifier wiring.

Wired to beat by :mod:`workers.beat_schedule` on a CONFIGURABLE cadence
(``WORKERS_HUMAN_ESCALATION_CRON``, default every 10 minutes) and gated by a
live ``human_escalation_enabled`` PLATFORM setting a System Admin flips from the
admin panel. The cadence is read by the beat PROCESS at boot; the enable lever
takes effect on the next fire without a restart — the SAME shape as price-sync /
fx-fetch / backup.

Best-effort
-----------
Like the other beat tasks (:mod:`workers.fx_fetcher` / :mod:`workers.maintenance`),
a single run failure must NOT crash beat: the sweep isolates each assignment in
its own transaction, the enable check + the whole pass are wrapped so an error is
logged, never raised, and beat keeps firing on cadence.

Multi-tenancy / RBAC
--------------------
The sweep writes through the worker's BYPASSRLS database role and scopes every
read/write to each assignment's OWN ``tenant_id`` (RLS cannot catch a BYPASSRLS
session). A tenant CANNOT trigger or schedule it — the schedule lives in the
platform beat process and the enable flag is a platform setting only a System
Admin can write. The ``task_blocked`` alert carries the assignment's tenant_id,
so the dispatcher fans it out to THAT tenant's admins only; the reassignment
``human_task_assigned`` notice is likewise tenant-scoped.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.human_escalation")


# ---------------------------------------------------------------------------
# Notifier seam (injectable; tests assert without a real broker)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CeleryHumanEscalationNotifier:
    """Default notifier — enqueues a Plan 10 event by name onto the priority lane.

    Mirrors :class:`workers.fx_fetcher.CeleryFxFetchNotifier`: the worker only
    PRODUCES the ``notification_dispatcher.dispatch_event`` task by name — it
    never imports the dispatcher package (clean app boundary). The notice
    (built by the domain sweep) already carries the tenant-scoped event_type /
    tenant_id / context, so this just forwards it.
    """

    broker_url: str
    dispatch_task: str = "notification_dispatcher.dispatch_event"
    priority_queue: str = "notifications.priority"

    def notify(self, notice: Any) -> None:
        from celery import Celery

        event = {
            "event_type": notice.event_type,
            "tenant_id": notice.tenant_id,
            "context": dict(notice.context),
        }
        Celery(broker=self.broker_url).send_task(
            self.dispatch_task,
            args=[event],
            queue=self.priority_queue,
        )


@app.task(name="workers.escalate_human_assignments")  # type: ignore[misc]
def escalate_human_assignments() -> dict[str, Any]:
    """Sweep timed-out human-task assignments + escalate/block them (scheduled).

    Honours the ``human_escalation_enabled`` platform setting (a System Admin's
    live OFF switch). Best-effort: a failure is logged, never raised, so beat
    keeps its cadence. Returns a small dict summarising the run.
    """
    settings = get_settings()
    notifier = CeleryHumanEscalationNotifier(
        broker_url=settings.broker_url,
        priority_queue="notifications.priority",
    )
    return asyncio.run(_escalate_human_assignments(settings, notifier=notifier))


async def _escalate_human_assignments(
    settings: Settings,
    *,
    notifier: Any | None,
) -> dict[str, Any]:
    """Async core — owns the engine lifecycle (mirrors workers.fx_fetcher).

    ``notifier`` is injectable so tests capture the produced notices without a
    real broker. A real run forwards each notice as a
    ``notification_dispatcher.dispatch_event`` (see
    :func:`escalate_human_assignments`).
    """
    # Lazy import — avoids paying the api_server import cost on workers that
    # never route the beat schedule (mirrors workers.fx_fetcher / price_sync).
    from api_server.db.platform_settings import get_human_escalation_enabled
    from api_server.human_agents import sweep_acceptance_timeouts

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        # Live enable/disable lever (System-Admin platform setting). When OFF
        # the run is a no-op — no reassignment, no block.
        async with sessionmaker() as db:
            enabled = await get_human_escalation_enabled(db)
        if not enabled:
            _log.info("human_escalation.skipped", reason="disabled")
            return {"enabled": False, "skipped": True}

        result = await sweep_acceptance_timeouts(sessionmaker, notifier=notifier)
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("human_escalation.error", error=str(exc))
        return {"enabled": True, "ok": False, "error": str(exc)}
    finally:
        await engine.dispose()

    return {
        "enabled": True,
        "ok": True,
        "scanned": result.scanned,
        "escalated": result.escalated,
        "blocked": result.blocked,
        "skipped": result.skipped,
    }


__all__ = [
    "CeleryHumanEscalationNotifier",
    "escalate_human_assignments",
]
