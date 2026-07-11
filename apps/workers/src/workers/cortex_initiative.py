"""Córtex C1 — iniciativa proactiva: el córtex escribe primero (2026-07-12).

Todo el córtex era estrictamente reactivo: el surfacing de pursuits solo
disparaba dentro de un turno del owner. Este beat (cada 30 min, gated por
``cortex.autonomy_enabled``) comprueba con lógica PURA
(:mod:`api_server.cortex.initiative`) si tiene sentido escribir primero —
aprendizajes ``digested`` pendientes + silencio ≥20 h + sin una iniciativa
previa sin respuesta + nunca el primer contacto — y entonces:

  1. crea una conversación nueva y persiste el turno ``role='cortex'`` con
     ``metadata_.initiative=true`` (el mensaje es determinista, sin LLM);
  2. marca los pursuits mencionados como ``surfaced`` (misma transacción);
  3. notifica al owner por el Plan 10 (evento ``cortex_message``, best-effort).

Anti-acoso por construcción: la propia iniciativa es un turno ``cortex``, así
que el gap se resetea (≥20 h hasta la siguiente) y mientras el owner no
responda ``unanswered_initiative`` bloquea cualquier otra.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from api_server.cortex.initiative import compose_initiative_message, should_reach_out
from api_server.cortex.self_context import _load_pending_learnings, mark_pursuits_surfaced
from api_server.cortex.threads import append_turn, create_conversation, resolve_cortex_tenant_id
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.cortex_initiative")

_CORTEX_MESSAGE_EVENT = "cortex_message"


@app.task(name="workers.cortex_initiative")  # type: ignore[untyped-decorator]
def cortex_initiative() -> dict[str, Any]:
    """Celery entry point — una pasada de iniciativa proactiva."""
    settings = get_settings()
    return asyncio.run(_run_initiative(settings))


async def _run_initiative(settings: Settings, *, now: datetime | None = None) -> dict[str, Any]:
    """Núcleo async (testeable con ``now`` inyectado); best-effort siempre."""
    now = now or datetime.now(UTC)
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        from api_server.db.platform_settings import get_cortex_autonomy_enabled

        async with sessionmaker() as session:
            if not await get_cortex_autonomy_enabled(session):
                return {"skipped": "disabled"}

        owner_id = await _load_owner(sessionmaker)
        if owner_id is None:
            return {"skipped": "no_owner"}

        async with sessionmaker() as session:
            last_turn = await _latest_turn(session, owner_id)
            learnings = await _load_pending_learnings(session, owner_user_id=owner_id)

        last_turn_at = last_turn[0] if last_turn else None
        unanswered = bool(last_turn and last_turn[1] == "cortex" and last_turn[2])
        if not should_reach_out(
            now=now,
            last_turn_at=last_turn_at,
            has_pending_learnings=bool(learnings),
            unanswered_initiative=unanswered,
        ):
            return {"skipped": "not_now", "pending": len(learnings)}

        assert last_turn_at is not None  # should_reach_out lo garantiza
        message = compose_initiative_message(
            learnings, now=now, last_turn_at=last_turn_at, language="es"
        )
        if message is None:
            return {"skipped": "nothing_to_tell"}

        async with sessionmaker() as session, session.begin():
            tenant_id = await resolve_cortex_tenant_id(session, owner_id)
            conv = await create_conversation(session, owner_user_id=owner_id, tenant_id=tenant_id)
            await append_turn(
                session,
                conversation_id=conv.id,
                owner_user_id=owner_id,
                role="cortex",
                content=message,
                metadata={"initiative": True},
            )
            await mark_pursuits_surfaced(
                session,
                owner_user_id=owner_id,
                pursuit_ids=[entry.pursuit_id for entry in learnings[:2]],
                now=now,
            )

        await _notify_owner(message)
        _log.info("cortex_initiative.sent", conversation_id=str(conv.id))
        return {"sent": True, "conversation_id": str(conv.id)}
    except Exception as exc:  # best-effort: la iniciativa jamás tumba el beat
        _log.warning("cortex_initiative.failed", error=str(exc))
        return {"error": str(exc)}
    finally:
        await engine.dispose()


async def _load_owner(sessionmaker: async_sessionmaker[AsyncSession]) -> UUID | None:
    from api_server.db.models import User

    async with sessionmaker() as session:
        owner_id = (
            await session.execute(
                select(User.id).where(User.is_system_owner.is_(True), User.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
    return UUID(str(owner_id)) if owner_id is not None else None


async def _latest_turn(session: AsyncSession, owner_id: UUID) -> tuple[datetime, str, bool] | None:
    """(created_at, role, es_iniciativa) del último turno del owner, o None."""
    from api_server.db.cortex import CortexTurn

    row = (
        await session.execute(
            select(CortexTurn.created_at, CortexTurn.role, CortexTurn.metadata_)
            .where(CortexTurn.owner_user_id == owner_id)
            .order_by(CortexTurn.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    metadata = row[2] or {}
    return (row[0], str(row[1]), bool(metadata.get("initiative")))


async def _notify_owner(message: str) -> None:
    """Evento ``cortex_message`` al dispatcher (platform-scoped, best-effort)."""
    try:
        from api_server.celery_client import enqueue_event_dispatch

        preview = message.splitlines()[0][:180]
        await enqueue_event_dispatch(
            {
                "event_type": _CORTEX_MESSAGE_EVENT,
                "tenant_id": None,  # el owner es el System Admin
                "context": {"preview": preview},
            }
        )
    except Exception as exc:  # la notificación nunca rompe la iniciativa
        _log.warning("cortex_initiative.notify_failed", error=str(exc))
