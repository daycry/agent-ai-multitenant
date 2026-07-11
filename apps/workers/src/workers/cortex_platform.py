"""Córtex C2 — pulso de plataforma → afecto (investigación 2026-07-11).

El córtex era CIEGO al sistema que su owner opera: su afecto solo se movía por
el texto del chat. Este beat (cada 15 min, gated por ``cortex.autonomy_enabled``
como el resto de bucles F4) recuenta lo que pasó en la plataforma desde la
última pasada — runs completados/fallidos, planes completados/bloqueados — y lo
convierte en un delta PAD DETERMINISTA (``platform_affect.pulse_appraisal``,
sin LLM: coste cero) aplicado por el MISMO motor que el appraisal
conversacional. Snapshot + caché viva + telemetría con razón honesta («pulso de
plataforma: …»); ventana tranquila = no-op (el silencio no es un evento).

El checkpoint de ventana vive en Redis (``cortex:platform_pulse:last``); sin
checkpoint (primera pasada / Redis limpio) la ventana es el intervalo del beat,
así que un backlog viejo nunca golpea el humor de golpe.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from api_server.cortex.affect_store import save_affect_snapshot
from api_server.cortex.affective import AffectState, apply_event, satisfy_drive, update_mood
from api_server.cortex.platform_affect import PlatformPulse, pulse_appraisal
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.cortex_affect import (
    _load_identity_baseline,
    _load_prior_state,
    _publish_frame,
    _refresh_live_state,
)

_log = structlog.get_logger("workers.cortex_platform")

_CHECKPOINT_KEY = "cortex:platform_pulse:last"
# Ventana por defecto (= intervalo del beat): sin checkpoint no miramos más
# atrás — un backlog histórico no debe golpear el humor de golpe.
_DEFAULT_WINDOW_S = 900.0
_DRIVE_NAMES = frozenset({"curiosity", "bonding", "coherence", "competence"})


@app.task(name="workers.cortex_platform_pulse")  # type: ignore[untyped-decorator]
def cortex_platform_pulse() -> dict[str, Any]:
    """Celery entry point — una pasada del pulso de plataforma."""
    settings = get_settings()
    return asyncio.run(_run_platform_pulse(settings))


async def _run_platform_pulse(settings: Settings, *, now: datetime | None = None) -> dict[str, Any]:
    """Núcleo async (testeable con ``now`` inyectado). Best-effort: nunca tumba
    el beat; cada guard es un return temprano observable."""
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

        window_start = await _window_start(now)
        pulse = await _count_pulse(sessionmaker, since=window_start, until=now)
        if pulse.is_quiet:
            await _store_checkpoint(now)
            return {"skipped": "quiet", "since": window_start.isoformat()}

        delta, reason, drive_name, drive_amount = pulse_appraisal(pulse)

        baseline = await _load_identity_baseline(sessionmaker, owner_id)
        prior = await _load_prior_state(sessionmaker, owner_id, now=now, baseline=baseline)
        new_emotion = apply_event(prior.emotion, delta)
        new_mood = update_mood(prior.mood, new_emotion)
        new_drives = prior.drives
        if drive_name in _DRIVE_NAMES and drive_amount > 0.0:
            new_drives = satisfy_drive(new_drives, drive_name, drive_amount)
        new_state = AffectState(emotion=new_emotion, mood=new_mood, drives=new_drives)

        async with sessionmaker() as session, session.begin():
            await save_affect_snapshot(
                session,
                owner_user_id=owner_id,
                state=new_state,
                appraisal_reason=reason,
                source_turn_id=None,
                language="es",
            )
        await _refresh_live_state(owner_id, new_state, now=now, baseline=baseline)
        await _publish_frame(
            owner_id,
            state=new_state,
            mood_label=new_state.mood_label(language="es"),
            appraisal_reason=reason,
            now=now,
        )
        await _store_checkpoint(now)
        _log.info("cortex_platform.pulse_applied", reason=reason)
        return {
            "applied": True,
            "reason": reason,
            "pulse": {
                "done": pulse.executions_done,
                "failed": pulse.executions_failed,
                "plans_completed": pulse.plans_completed,
                "plans_blocked": pulse.plans_blocked,
            },
        }
    except Exception as exc:  # best-effort: el pulso jamás tumba el beat
        _log.warning("cortex_platform.pulse_failed", error=str(exc))
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


async def _count_pulse(
    sessionmaker: async_sessionmaker[AsyncSession], *, since: datetime, until: datetime
) -> PlatformPulse:
    """Recuento cross-tenant de la ventana (el córtex siente TODA la plataforma
    que su owner opera; BYPASSRLS deliberado, solo agregados sin contenido)."""
    from api_server.db.domain import Execution, Plan

    async with sessionmaker() as session:
        done = (
            await session.execute(
                select(func.count(Execution.id)).where(
                    Execution.status == "done",
                    Execution.updated_at >= since,
                    Execution.updated_at < until,
                )
            )
        ).scalar_one()
        failed = (
            await session.execute(
                select(func.count(Execution.id)).where(
                    Execution.status.in_(("failed", "aborted")),
                    Execution.updated_at >= since,
                    Execution.updated_at < until,
                )
            )
        ).scalar_one()
        plans_completed = (
            await session.execute(
                select(func.count(Plan.id)).where(
                    Plan.status == "completed",
                    Plan.updated_at >= since,
                    Plan.updated_at < until,
                )
            )
        ).scalar_one()
        plans_blocked = (
            await session.execute(
                select(func.count(Plan.id)).where(
                    Plan.status == "blocked",
                    Plan.updated_at >= since,
                    Plan.updated_at < until,
                )
            )
        ).scalar_one()
    return PlatformPulse(
        executions_done=int(done),
        executions_failed=int(failed),
        plans_blocked=int(plans_blocked),
        plans_completed=int(plans_completed),
    )


async def _window_start(now: datetime) -> datetime:
    """Inicio de ventana desde el checkpoint Redis (acotado al intervalo)."""
    fallback = now - timedelta(seconds=_DEFAULT_WINDOW_S)
    try:
        redis = _get_redis()
        raw = await redis.get(_CHECKPOINT_KEY)
        await redis.aclose()
        if not raw:
            return fallback
        stamp = datetime.fromisoformat(
            raw.decode() if isinstance(raw, bytes | bytearray) else str(raw)
        )
        # Nunca mirar más atrás que 4 ventanas: un beat parado días no debe
        # volcar el histórico entero sobre el humor.
        floor = now - timedelta(seconds=_DEFAULT_WINDOW_S * 4)
        return max(stamp, floor)
    except Exception:
        return fallback


async def _store_checkpoint(now: datetime) -> None:
    try:
        redis = _get_redis()
        await redis.set(_CHECKPOINT_KEY, now.isoformat())
        await redis.aclose()
    except Exception:  # best-effort
        pass


def _get_redis() -> Redis:
    client: Redis = Redis.from_url(get_settings().events_redis_url)
    return client
