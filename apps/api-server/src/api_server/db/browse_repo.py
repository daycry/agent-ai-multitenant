"""Persistencia de las sesiones de navegador del córtex (ADR 0080).

Una fila por sesión pedida. El gate humano vive en la máquina de estados de
:mod:`api_server.cortex.browse`: aquí solo se persiste, siempre pasando cada
cambio de estado por esas transiciones (nadie escribe ``status`` a mano, así que
no hay forma de colar un ``running`` sin ``approved``).

``tenant_id`` es el discriminante físico (RLS en la tabla); el owner del córtex
es plataforma, y esta tabla la tocan sesiones admin — igual que la memoria del
córtex (Decisión D1 / ADR 0116).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from api_server.cortex.browse import (
    BROWSE_PENDING,
    BrowseSessionState,
    approve,
    fail,
    finish,
    reject,
    start,
)
from api_server.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BrowseSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Una sesión de navegación pedida por el córtex (ADR 0080).

    Nace ``pending_approval``: el owner ve el guion (URLs, clicks, lo que se va
    a teclear) y decide. Solo tras su aprobación el worker lanza el
    `browser-runtime` efímero."""

    __tablename__ = "browse_sessions"

    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    owner_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=BROWSE_PENDING,
        server_default=text(f"'{BROWSE_PENDING}'"),
    )
    goal: Mapped[str] = mapped_column(String(500), nullable=False)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    budgets: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decided_by_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # timezone=True: escribimos datetimes aware (UTC) — igual que la migración 0112.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def _state(row: BrowseSession) -> BrowseSessionState:
    return BrowseSessionState(status=row.status, error=row.error)


def _apply(row: BrowseSession, state: BrowseSessionState) -> BrowseSession:
    row.status = state.status
    row.error = state.error
    return row


async def create_pending(
    session: AsyncSession,
    *,
    tenant_id: UUID | None,
    owner_user_id: UUID,
    goal: str,
    steps: list[dict[str, Any]],
    budgets: dict[str, Any] | None = None,
) -> BrowseSession:
    """Registra la petición del córtex. NO navega: queda esperando al owner."""
    row = BrowseSession(
        id=uuid4(),
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        status=BROWSE_PENDING,
        goal=goal,
        steps=steps,
        budgets=budgets or {},
    )
    session.add(row)
    await session.flush()
    return row


async def get_browse_session(
    session: AsyncSession, session_id: UUID, *, owner_user_id: UUID | None = None
) -> BrowseSession | None:
    stmt = select(BrowseSession).where(BrowseSession.id == session_id)
    if owner_user_id is not None:
        stmt = stmt.where(BrowseSession.owner_user_id == owner_user_id)
    return (await session.execute(stmt)).scalars().first()


async def list_pending(
    session: AsyncSession, *, owner_user_id: UUID, limit: int = 50
) -> list[BrowseSession]:
    """El inbox del owner: lo que el córtex quiere navegar y aún no ha visto nadie."""
    rows = await session.execute(
        select(BrowseSession)
        .where(
            BrowseSession.owner_user_id == owner_user_id,
            BrowseSession.status == BROWSE_PENDING,
        )
        .order_by(BrowseSession.created_at.asc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def approve_session(
    session: AsyncSession,  # noqa: ARG001 — firma uniforme del repo
    row: BrowseSession,
    *,
    decided_by: UUID,
) -> BrowseSession:
    _apply(row, approve(_state(row)))
    row.decided_by_user_id = decided_by
    row.decided_at = datetime.now(UTC)
    return row


async def reject_session(
    session: AsyncSession,  # noqa: ARG001 — firma uniforme del repo
    row: BrowseSession,
    *,
    decided_by: UUID,
    reason: str = "",
) -> BrowseSession:
    _apply(row, reject(_state(row), reason=reason))
    row.decided_by_user_id = decided_by
    row.decided_at = datetime.now(UTC)
    return row


async def mark_running(
    session: AsyncSession,  # noqa: ARG001 — firma uniforme del repo
    row: BrowseSession,
) -> BrowseSession:
    return _apply(row, start(_state(row)))


async def mark_done(
    session: AsyncSession,  # noqa: ARG001 — firma uniforme del repo
    row: BrowseSession,
    *,
    result: dict[str, Any],
) -> BrowseSession:
    _apply(row, finish(_state(row)))
    row.result = result
    row.finished_at = datetime.now(UTC)
    return row


async def mark_failed(
    session: AsyncSession,  # noqa: ARG001 — firma uniforme del repo
    row: BrowseSession,
    *,
    error: str,
) -> BrowseSession:
    _apply(row, fail(_state(row), error=error))
    row.finished_at = datetime.now(UTC)
    return row


__all__ = [
    "BrowseSession",
    "approve_session",
    "create_pending",
    "get_browse_session",
    "list_pending",
    "mark_done",
    "mark_failed",
    "mark_running",
    "reject_session",
]
