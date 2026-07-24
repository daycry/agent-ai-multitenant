"""Repository for `review_sessions` (Plan 06.5 task_06_5_03).

Persistence layer for `workers.review_runtime.ReviewRuntimeManager`.
The manager already operates in-memory; this module is the seam
through which a future async worker (Plan 06.5 Fase C) writes each
transition (create / suspend / verdict / expire) to the DB so that
state survives a worker restart mid-review.

The functions here are session-bound (the caller owns the
transaction) and tenant-scoped via RLS — the SQLAlchemy session must
have already executed `SET LOCAL app.tenant_id = ...` (the standard
api-server middleware does this).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.models import ReviewSession


async def create_review_session(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    plan_id: UUID | None,
    spec: dict[str, Any],
    expires_at: datetime,
    container_ids: list[str] | None = None,
    kind: str = "plan",
) -> ReviewSession:
    """Persist a freshly-spawned session. `id` is auto-generated.

    ``plan_id`` is optional since ADR 0130: an on-demand PROJECT preview
    (``kind='preview'``) has no plan. The DB check enforces plan_id NULL ⇒
    kind='preview'."""
    row = ReviewSession(
        tenant_id=tenant_id,
        plan_id=plan_id,
        spec=spec,
        container_ids=list(container_ids or []),
        expires_at=expires_at,
        status="running",
        kind=kind,
    )
    session.add(row)
    await session.flush()
    return row


async def get_review_session(session: AsyncSession, session_id: UUID) -> ReviewSession | None:
    """Lookup by id; returns None if not visible (RLS) or soft-deleted."""
    result = await session.execute(
        select(ReviewSession).where(
            ReviewSession.id == session_id,
            ReviewSession.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_review_sessions_for_plan(
    session: AsyncSession, plan_id: UUID
) -> list[ReviewSession]:
    """Human-validation sessions of a plan, newest first.

    Excludes on-demand previews (``kind='preview'``, ADR 0130) so a preview a
    user launched for the plan never masquerades as its validation session in
    the panel / corrections view / verdict flow."""
    result = await session.execute(
        select(ReviewSession)
        .where(
            ReviewSession.plan_id == plan_id,
            ReviewSession.kind == "plan",
            ReviewSession.deleted_at.is_(None),
        )
        .order_by(ReviewSession.created_at.desc())
    )
    return list(result.scalars().all())


async def list_active_preview_sessions(
    session: AsyncSession,
    *,
    project_id: UUID | None = None,
    plan_id: UUID | None = None,
) -> list[ReviewSession]:
    """Active (running/suspended) on-demand previews (ADR 0130), newest first.

    Filter by ``plan_id`` for a plan preview, or by ``project_id`` (matched
    against ``spec->>'project_id'``) for a project preview. Used by the poll
    endpoint (hand the operator the signed app URL) and to avoid piling up
    duplicate previews for the same target."""
    stmt = select(ReviewSession).where(
        ReviewSession.kind == "preview",
        ReviewSession.status.in_(("running", "suspended")),
        ReviewSession.deleted_at.is_(None),
    )
    if plan_id is not None:
        stmt = stmt.where(ReviewSession.plan_id == plan_id)
    else:
        stmt = stmt.where(ReviewSession.plan_id.is_(None))
    if project_id is not None:
        stmt = stmt.where(ReviewSession.spec["project_id"].astext == str(project_id))
    stmt = stmt.order_by(ReviewSession.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_running_overdue(
    session: AsyncSession, now: datetime | None = None
) -> list[ReviewSession]:
    """Sesiones ACTIVAS (`running` O `suspended`) con `expires_at < now`.
    Driven by the `expire_review_runtimes` beat schedule (Plan 06.5
    task_06_5_13). PROY2-07: una `suspended` vencida también expira — antes
    quedaba zombie para siempre y el autostart/reconciler la contaba como
    activa (ACTIVE_REVIEW_STATUSES), bloqueando nuevas sesiones del plan."""
    when = now or datetime.now(UTC)
    result = await session.execute(
        select(ReviewSession).where(
            ReviewSession.status.in_(("running", "suspended")),
            ReviewSession.expires_at < when,
            ReviewSession.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def list_running_idle(
    session: AsyncSession,
    idle_for: timedelta,
    now: datetime | None = None,
) -> list[ReviewSession]:
    """`status='running' AND last_activity_at < now - idle_for`.
    Driven by the same beat that suspends idle sessions."""
    when = now or datetime.now(UTC)
    threshold = when - idle_for
    result = await session.execute(
        select(ReviewSession).where(
            ReviewSession.status == "running",
            ReviewSession.last_activity_at < threshold,
            ReviewSession.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def mark_terminal(
    session: AsyncSession,
    session_id: UUID,
    *,
    status: str,
    verdict: str | None = None,
    rejection_reason: str | None = None,
) -> ReviewSession | None:
    """Move a session to a terminal status (approved/rejected/expired/
    cancelled). Returns the updated row, or None if not visible."""
    row = await get_review_session(session, session_id)
    if row is None:
        return None
    row.status = status
    if verdict is not None:
        row.verdict = verdict
    if rejection_reason is not None:
        row.rejection_reason = rejection_reason
    await session.flush()
    return row


async def mark_other_plan_sessions_terminal(
    session: AsyncSession,
    plan_id: UUID,
    *,
    exclude_session_id: UUID,
) -> int:
    """PROY2-07: al cerrar el plan (veredicto), las OTRAS sesiones activas
    (`running`/`suspended`) del plan se marcan `expired` — quedaban zombies
    contando como activas para el autostart/reconciler. Devuelve cuántas cerró."""
    result = await session.execute(
        select(ReviewSession).where(
            ReviewSession.plan_id == plan_id,
            ReviewSession.id != exclude_session_id,
            ReviewSession.status.in_(("running", "suspended")),
            ReviewSession.deleted_at.is_(None),
        )
    )
    rows = list(result.scalars().all())
    for row in rows:
        row.status = "expired"
    if rows:
        await session.flush()
    return len(rows)


async def mark_rerun_requested(session: AsyncSession, session_id: UUID) -> ReviewSession | None:
    """Idempotent: sets `rerun_requested=True`. The worker picks it up
    on the next sweep and re-runs the test-runtime."""
    row = await get_review_session(session, session_id)
    if row is None:
        return None
    row.rerun_requested = True
    row.last_activity_at = datetime.now(UTC)
    await session.flush()
    return row


async def touch_activity(session: AsyncSession, session_id: UUID) -> ReviewSession | None:
    """Bump `last_activity_at = now()`. Called when the human opens a
    page of the review SPA — extends the idle window."""
    row = await get_review_session(session, session_id)
    if row is None:
        return None
    row.last_activity_at = datetime.now(UTC)
    await session.flush()
    return row


async def suspend_session(session: AsyncSession, session_id: UUID) -> ReviewSession | None:
    """`running` → `suspended` + stamps `suspended_at`. The container
    process is paused externally (caller's responsibility)."""
    row = await get_review_session(session, session_id)
    if row is None:
        return None
    row.status = "suspended"
    row.suspended_at = datetime.now(UTC)
    await session.flush()
    return row


async def soft_delete_session(session: AsyncSession, session_id: UUID) -> ReviewSession | None:
    """Soft-delete (RLS keeps it invisible). Hard delete only via the
    `purge_dep_cache`-style maintenance job (out of scope here)."""
    row = await get_review_session(session, session_id)
    if row is None:
        return None
    row.deleted_at = datetime.now(UTC)
    await session.flush()
    return row
