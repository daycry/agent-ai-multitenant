"""Review-runtime expiry sweep — `workers.expire_review_runtimes`, every 5 min.

Expires overdue review-runtimes, suspends idle ones and reaps terminal ones
(C8 F40/F41). Best-effort: a single failure must not crash beat itself.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

_log = structlog.get_logger("workers.maintenance")

# Idle window after which a `running` review-runtime is suspended.
# NO coincide con `workers.review_runtime.DEFAULT_IDLE_SUSPEND_S` (4 h): esta
# ventana de 24 h es la que manda, y es la que fija
# `tests/integration/test_review_suspension.py`. Si algún día se unifican, que
# sea por decisión explícita — no dando por hecho que ya son la misma.
_SUSPEND_IDLE_AFTER = timedelta(hours=24)

# Terminal review-session statuses — a session here no longer holds a runtime, so
# the expiry sweep reaps its containers (`docker rm -f`) and clears its
# container_ids (C8 F41); la fila sobrevive con veredicto+motivo (ADR 0107).
_TERMINAL_REVIEW_STATUSES = ("approved", "rejected", "expired", "cancelled")


@app.task(name="workers.expire_review_runtimes")  # type: ignore[untyped-decorator]
def expire_review_runtimes() -> dict[str, Any]:
    """Expire overdue review-runtimes, suspend idle ones, reap terminal ones.

    Four DB sweeps (C8 F40/F41 — the in-memory ReviewRuntimeManager logic, now
    cabled to the repo-DB + beat as the single source of truth):
      1. ``status='running' AND expires_at < now`` → ``expired``, AND the owning
         Plan ``pending_human_validation`` → ``blocked`` (idempotent), AND an
         escalation notification to the owner.
      2. ``status='running' AND last_activity_at < now - 24h`` → ``suspended``
         (containers paused by the worker that owns them; out of scope here).
      3. Every TERMINAL session (approved/rejected/expired/cancelled) with leftover
         containers → ``docker rm -f`` them by id + clear its ``container_ids``
         (closes the container leak the verdict path left — submit_verdict only
         marks terminal). The row itself SURVIVES: the verdict and the
         ``rejection_reason`` feed the panel's fallback view and
         generate-corrections (ADR 0107).
    """
    settings = get_settings()
    return asyncio.run(_expire_review_runtimes(settings))


def plan_status_after_expiry(current_status: str) -> str | None:
    """Pure decision: what status a plan moves to when its review session expires.

    A plan still awaiting human validation (``pending_human_validation``) is moved
    to ``blocked`` so the operator sees it needs attention; any other status is left
    untouched (``None``) — IDEMPOTENT, so re-running the sweep never re-transitions
    an already-blocked / completed / rejected plan (C8 F40)."""
    return "blocked" if current_status == "pending_human_validation" else None


async def _block_plan_for_expired_session(db: AsyncSession, row: Any) -> dict[str, Any] | None:
    """Idempotently move an expired session's plan off ``pending_human_validation``
    and return the owner-notification payload (or ``None`` when no transition was
    warranted). The Plan load is BYPASSRLS (worker engine); the session row already
    carries the tenant scope."""
    from api_server.chat.plan_state_machine import transition_plan_status
    from api_server.db.domain import Plan

    # ADR 0130: an on-demand PROJECT preview has no plan — nothing to block or
    # escalate when it expires (it just gets reaped).
    if row.plan_id is None:
        return None
    plan = await db.get(Plan, row.plan_id)
    if plan is None:
        return None
    new_status = plan_status_after_expiry(plan.status)
    if new_status is None:
        return None
    # T4 (ciclo-vida): toda mutación de estado de plan pasa por la única puerta
    # (el edge pending_human_validation→blocked es legal en la tabla, C8 F40).
    old_status = plan.status
    transition_plan_status(plan, new_status)
    await db.flush()
    spec = row.spec or {}
    return {
        "tenant_id": str(row.tenant_id),
        "plan_id": str(row.plan_id),
        "session_id": str(row.id),
        "plan_title": str(spec.get("plan_title") or spec.get("title") or ""),
        "owner_user_id": spec.get("owner_user_id"),
        # task_wf_32: datos del anuncio al tablero, que va DESPUÉS del commit
        # junto a la notificación. Se llevan aquí porque tras el commit el
        # objeto ORM está expirado.
        "project_id": str(plan.project_id),
        "old_status": old_status,
        "new_status": new_status,
    }


async def _announce_expired_plan_move(payload: dict[str, Any]) -> None:
    """Publica al bus de planes el bloqueo por review caducada (`task_wf_32`).

    Best-effort y con su propia conexión: el beat no mantiene una, y un fallo
    aquí no puede deshacer un bloqueo ya commiteado."""
    try:
        from api_server.events import publish_plan_status_changed
        from redis.asyncio import Redis

        redis = Redis.from_url(get_settings().events_redis_url, decode_responses=True)
        try:
            await publish_plan_status_changed(
                redis,
                plan_id=payload["plan_id"],
                tenant_id=payload["tenant_id"],
                project_id=payload["project_id"],
                old_status=payload["old_status"],
                new_status=payload["new_status"],
                title=payload.get("plan_title") or "",
            )
        finally:
            await redis.aclose()
    except Exception as exc:  # pragma: no cover - bus best-effort
        _log.warning("maintenance.plan_move_announce_failed", error=str(exc))


async def _enqueue_review_expiry_notification(payload: dict[str, Any]) -> None:
    """Best-effort: escalate an expired review session to the owner (C8 F40).

    Reuses the registered ``review_escalated`` event (priority lane). A broker /
    import failure is swallowed — the plan is already ``blocked`` in the DB, so the
    escalation is a notification, not a transaction to roll back."""
    try:
        from api_server.celery_client import enqueue_event_dispatch
    except ImportError:  # pragma: no cover - api_server always present in workers
        return
    event = {
        "event_type": "review_escalated",
        "tenant_id": payload["tenant_id"],
        "context": {
            "task_title": payload.get("plan_title") or "",
            "plan_id": payload["plan_id"],
            "session_id": payload["session_id"],
            "owner_user_id": payload.get("owner_user_id"),
            "reason": "verdict_timeout",
        },
        "locale": None,
    }
    await enqueue_event_dispatch(event)


def _reap_review_containers(container_ids: list[str]) -> int:
    """``docker rm -f`` a terminal session's leftover containers (C8 F41).

    Best-effort + idempotent: a missing daemon, an unimportable SDK, or an
    already-gone container each no-op. Returns how many containers were removed.
    Runs OUTSIDE any DB transaction (Docker I/O must never hold a txn open)."""
    if not container_ids:
        return 0
    from workers.docker_client import get_docker_client

    client = get_docker_client()
    if client is None:
        return 0
    removed = 0
    for cid in container_ids:
        try:
            client.containers.get(str(cid)).remove(force=True)
            removed += 1
        except Exception:  # already gone / not found — idempotent
            continue
    return removed


async def _list_terminal_sessions_with_containers(db: AsyncSession) -> list[Any]:
    """Terminal (approved/rejected/expired/cancelled), not soft-deleted sessions
    that still carry container ids — the reap candidates (C8 F41)."""
    from api_server.db.models import ReviewSession

    rows = (
        (
            await db.execute(
                select(ReviewSession).where(
                    ReviewSession.status.in_(_TERMINAL_REVIEW_STATUSES),
                    ReviewSession.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return [r for r in rows if r.container_ids]


async def _expire_review_runtimes(settings: Settings) -> dict[str, Any]:
    """Async core — owns the engine lifecycle."""
    # Lazy import — avoids paying the api_server import cost on workers
    # that don't route the `review` queue.
    from api_server.db.review_session_repo import (
        get_review_session,
        list_running_idle,
        list_running_overdue,
        mark_terminal,
        suspend_session,
    )

    expired = 0
    suspended = 0
    reaped = 0
    notify_payloads: list[dict[str, Any]] = []
    # task_wf_32: transiciones de plan ganadas en esta pasada, anunciadas al
    # tablero gerencial tras el commit (el mismo momento que la notificación).
    plan_moves: list[dict[str, Any]] = []
    engine = worker_engine(settings)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        # 1. Overdue → expired + plan blocked + escalation notification.
        async with sessionmaker() as db, db.begin():
            overdue = await list_running_overdue(db)
            for row in overdue:
                await mark_terminal(db, row.id, status="expired")
                expired += 1
                payload = await _block_plan_for_expired_session(db, row)
                if payload is not None:
                    notify_payloads.append(payload)
                    plan_moves.append(payload)
        # 2. Idle → suspended.
        async with sessionmaker() as db, db.begin():
            idle = await list_running_idle(db, idle_for=_SUSPEND_IDLE_AFTER)
            for row in idle:
                await suspend_session(db, row.id)
                suspended += 1
        # 3. Reap terminal sessions' leftover containers and clear their
        #    container_ids. Docker I/O runs OUTSIDE the txn; clearing the ids
        #    already makes the sweep idempotent (a reaped row is no longer
        #    re-listed). La fila NO se soft-borra (ADR 0107): el veredicto y el
        #    rejection_reason son historia que consumen el panel (fallback a la
        #    sesión terminal más reciente) y generate-corrections — el
        #    soft-delete original destruía el motivo minutos después del
        #    rechazo (visto en vivo con el plan CI4).
        async with sessionmaker() as db:
            terminal = await _list_terminal_sessions_with_containers(db)
            to_reap = [(r.id, [str(c) for c in r.container_ids]) for r in terminal]
        for _session_id, container_ids in to_reap:
            _reap_review_containers(container_ids)
            reaped += 1
        if to_reap:
            async with sessionmaker() as db, db.begin():
                for session_id, _container_ids in to_reap:
                    reaped_row = await get_review_session(db, session_id)
                    if reaped_row is not None:
                        reaped_row.container_ids = []
                        await db.flush()
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.expire_review_runtimes.error", error=str(exc))
        return {
            "expired": expired,
            "suspended": suspended,
            "reaped": reaped,
            "error": str(exc),
        }
    finally:
        await engine.dispose()

    # Notifications OUTSIDE the engine lifecycle — best-effort, one per expired plan.
    for payload in notify_payloads:
        await _enqueue_review_expiry_notification(payload)
    # task_wf_32: y el mismo movimiento, al tablero gerencial. Una review que
    # caduca bloquea el plan sin que medie gesto humano: si no se anuncia, la
    # tarjeta se queda diciendo «pendiente de validación» indefinidamente.
    for move in plan_moves:
        await _announce_expired_plan_move(move)

    _log.info(
        "maintenance.expire_review_runtimes.done",
        expired=expired,
        suspended=suspended,
        reaped=reaped,
    )
    return {"expired": expired, "suspended": suspended, "reaped": reaped}
