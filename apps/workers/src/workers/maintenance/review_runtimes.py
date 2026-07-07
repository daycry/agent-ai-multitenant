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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.maintenance")

# Idle window after which a `running` review-runtime is suspended
# (containers paused). Mirrors the in-memory manager default.
_SUSPEND_IDLE_AFTER = timedelta(hours=24)

# Terminal review-session statuses — a session here no longer holds a runtime, so
# the expiry sweep reaps its containers (`docker rm -f`) + soft-deletes it (C8 F41).
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
         containers → ``docker rm -f`` them by id + soft-delete the row (closes the
         container leak the verdict path left — submit_verdict only marks terminal).
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
    from api_server.db.domain import Plan

    plan = await db.get(Plan, row.plan_id)
    if plan is None:
        return None
    new_status = plan_status_after_expiry(plan.status)
    if new_status is None:
        return None
    plan.status = new_status
    await db.flush()
    spec = row.spec or {}
    return {
        "tenant_id": str(row.tenant_id),
        "plan_id": str(row.plan_id),
        "session_id": str(row.id),
        "plan_title": str(spec.get("plan_title") or spec.get("title") or ""),
        "owner_user_id": spec.get("owner_user_id"),
    }


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
    try:
        import docker
    except ImportError:
        return 0
    try:
        client = docker.from_env()
        client.ping()
    except Exception:  # docker.errors.DockerException — daemon unavailable
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
        list_running_idle,
        list_running_overdue,
        mark_terminal,
        soft_delete_session,
        suspend_session,
    )

    expired = 0
    suspended = 0
    reaped = 0
    notify_payloads: list[dict[str, Any]] = []
    engine = create_async_engine(settings.database_url)
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
        # 2. Idle → suspended.
        async with sessionmaker() as db, db.begin():
            idle = await list_running_idle(db, idle_for=_SUSPEND_IDLE_AFTER)
            for row in idle:
                await suspend_session(db, row.id)
                suspended += 1
        # 3. Reap terminal sessions' leftover containers, then soft-delete them.
        #    Docker I/O runs OUTSIDE the txn; the soft-delete makes the sweep
        #    idempotent (a reaped row is no longer re-listed).
        async with sessionmaker() as db:
            terminal = await _list_terminal_sessions_with_containers(db)
            to_reap = [(r.id, [str(c) for c in r.container_ids]) for r in terminal]
        for _session_id, container_ids in to_reap:
            _reap_review_containers(container_ids)
            reaped += 1
        if to_reap:
            async with sessionmaker() as db, db.begin():
                for session_id, _container_ids in to_reap:
                    deleted = await soft_delete_session(db, session_id)
                    if deleted is not None:
                        deleted.container_ids = []
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

    _log.info(
        "maintenance.expire_review_runtimes.done",
        expired=expired,
        suspended=suspended,
        reaped=reaped,
    )
    return {"expired": expired, "suspended": suspended, "reaped": reaped}
