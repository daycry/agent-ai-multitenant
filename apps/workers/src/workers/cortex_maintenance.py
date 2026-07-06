"""Córtex F4 — mantenimiento de fondo de la mente (Celery, fail-open, ADR 0077/0078).

Tarea periódica que mantiene la "mente" del córtex acotada y viva cuando nadie
habla. Tres acciones por owner (singleton ``is_system_owner``):

  1. **Decay snapshot**: lee el estado afectivo con decay lazy aplicado y, si el
     último snapshot es viejo, escribe uno nuevo (sin ``source_turn_id``) capturando
     el mood/drives decaídos — así la serie temporal refleja el paso del tiempo
     aunque no haya turnos. Idempotente dentro de una ventana (no escribe si ya hay
     un snapshot reciente).
  2. **Olvido/consolidación (ADR 0077)**: recalcula el ``retention_score`` de la
     memoria EPISÓDICA del córtex del owner y hace **soft-delete** (reversible) de
     la de baja retención. **NUNCA** toca identity ni el owner-model (protección
     dura de :func:`api_server.cortex.forgetting.decide_forget`).
  3. **Poda de snapshots viejos**: soft-cap de la tabla append-only borrando los
     snapshots más antiguos de una ventana de retención (la serie reciente basta
     para el Panel de Mente).

Invariantes (espejo de :mod:`workers.cortex_reflection`):

  * **Kill-switch** (ADR 0078): si ``cortex.autonomy_enabled`` está OFF (default) →
    no-op total (no toca BD). El beat tickea, la tarea sale.
  * **Fail-open**: cualquier excepción se captura y loguea; la tarea jamás propaga
    al worker (es mantenimiento de fondo, best-effort).
  * **BYPASSRLS + filtro owner explícito** (ADR 0074): tablas tenant-less sin RLS.
  * **Idempotente**: re-ejecutar no duplica ni borra de más (el soft-delete ya es
    idempotente; el decay snapshot respeta una ventana).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.cortex_maintenance")

#: No escribir un decay snapshot si ya hay uno más reciente que esto (idempotencia
#: dentro de la ventana: el beat puede tickear varias veces sin engordar la serie).
_DECAY_SNAPSHOT_MIN_GAP = timedelta(hours=6)

#: Retención de la serie de snapshots: los más antiguos que esto se podan (la serie
#: reciente basta para el Panel de Mente). Generoso (90 días).
_SNAPSHOT_RETENTION = timedelta(days=90)

#: Tope de filas episódicas escaneadas por pasada (acota el coste; la próxima pasada
#: continúa — el soft-delete es idempotente).
_FORGET_SCAN_LIMIT = 500


@app.task(name="workers.cortex_maintenance")  # type: ignore[misc]
def cortex_maintenance() -> dict[str, Any]:
    """Celery entry point. Mantenimiento de fondo de la mente del córtex.

    Devuelve un dict con el rastro de la pasada (skipped / por-owner counts)."""
    settings = get_settings()
    return asyncio.run(_run_maintenance(settings))


async def _run_maintenance(settings: Settings, *, now: datetime | None = None) -> dict[str, Any]:
    """Núcleo async (testeable con ``now`` inyectado). Posee el engine lifecycle.

    Corre BYPASSRLS (sin ``set_config app.tenant_id``); TODO acceso filtra
    ``owner_user_id`` explícito."""
    now = now or datetime.now(UTC)
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # (1) Kill-switch global (ADR 0078): OFF (default) ⇒ no-op total.
        from api_server.db.platform_settings import get_cortex_autonomy_enabled

        async with sessionmaker() as session:
            if not await get_cortex_autonomy_enabled(session):
                _log.info("cortex_maintenance.disabled")
                return {"skipped": "disabled"}

        owners = await _resolve_owners(sessionmaker)
        if not owners:
            return {"skipped": "no_owner"}

        results: list[dict[str, Any]] = []
        for owner_id in owners:
            results.append(await _maintain_owner(sessionmaker, owner_id, now=now))
        _log.info("cortex_maintenance.done", owners=len(owners))
        return {"owners": len(owners), "results": results}
    except Exception as exc:  # best-effort: jamás propaga al worker
        _log.exception("cortex_maintenance.failed", error=str(exc))
        return {"error": str(exc)}
    finally:
        await engine.dispose()


async def _resolve_owners(sessionmaker: async_sessionmaker[Any]) -> list[UUID]:
    """Los user ids con ``is_system_owner`` (en la práctica el singleton owner).

    El SQL filtra explícitamente; si en el futuro hubiese varios owners, cada uno se
    mantiene de forma aislada (cross-owner safe)."""
    from api_server.db.models import User

    async with sessionmaker() as session:
        rows = await session.execute(
            select(User.id).where(User.is_system_owner.is_(True), User.deleted_at.is_(None))
        )
        return [r[0] for r in rows.all()]


async def _maintain_owner(
    sessionmaker: async_sessionmaker[Any], owner_id: UUID, *, now: datetime
) -> dict[str, Any]:
    """Las tres acciones de mantenimiento para UN owner (aislado por owner_id)."""
    snapshotted = await _decay_snapshot(sessionmaker, owner_id, now=now)
    forgotten = await _forget_low_retention(sessionmaker, owner_id, now=now)
    pruned = await _prune_old_snapshots(sessionmaker, owner_id, now=now)
    return {
        "owner_user_id": str(owner_id),
        "decay_snapshot_written": snapshotted,
        "forgotten": forgotten,
        "pruned_snapshots": pruned,
    }


async def _decay_snapshot(
    sessionmaker: async_sessionmaker[Any], owner_id: UUID, *, now: datetime
) -> bool:
    """Escribe un snapshot del estado decaído si el último es viejo (idempotente).

    Reusa ``load_affect_state`` (decay lazy determinista) y ``save_affect_snapshot``
    (sin ``source_turn_id`` ⇒ snapshot de mantenimiento). No escribe si ya hay un
    snapshot a menos de :data:`_DECAY_SNAPSHOT_MIN_GAP`, así varias pasadas seguidas
    no engordan la serie. Best-effort."""
    from api_server.cortex.affect_store import load_affect_state, save_affect_snapshot
    from api_server.db.cortex_affect import CortexAffectSnapshot

    try:
        async with sessionmaker() as session:
            latest = (
                await session.execute(
                    select(CortexAffectSnapshot.created_at)
                    .where(CortexAffectSnapshot.owner_user_id == owner_id)
                    .order_by(CortexAffectSnapshot.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            # Sin ningún snapshot ⇒ no hay estado que decaer (el owner aún no
            # interactuó); no fabricamos una serie de la nada.
            if latest is None:
                return False
            gap = now - (latest if latest.tzinfo else latest.replace(tzinfo=UTC))
            if gap < _DECAY_SNAPSHOT_MIN_GAP:
                return False

        async with sessionmaker() as session, session.begin():
            state = await load_affect_state(session, owner_id, now=now)
            await save_affect_snapshot(
                session,
                owner_user_id=owner_id,
                state=state,
                appraisal_reason=None,  # decay puro, sin appraisal
                source_turn_id=None,
                language="es",
            )
        return True
    except Exception as exc:  # best-effort
        _log.warning(
            "cortex_maintenance.decay_snapshot_failed", owner=str(owner_id), error=str(exc)
        )
        return False


async def _forget_low_retention(
    sessionmaker: async_sessionmaker[Any], owner_id: UUID, *, now: datetime
) -> int:
    """Soft-delete (reversible) de la episódica del córtex de BAJA retención.

    Escanea las memorias EPISÓDICAS del owner marcadas ``metadata_.cortex=true`` y
    aún vivas; aplica :func:`decide_forget` (protección dura de identity/owner-model
    + umbral de retención) y pone ``deleted_at=now`` a las candidatas. NUNCA borra
    físicamente (ADR 0059) ni toca identity/owner-model/reflection/learning.
    Idempotente: una fila ya soft-deleted no se re-selecciona."""
    from api_server.cortex.forgetting import decide_forget, recall_frequency_factor
    from api_server.db.memory import MemoryEntry

    forgotten = 0
    try:
        async with sessionmaker() as session, session.begin():
            rows = list(
                (
                    await session.execute(
                        select(MemoryEntry)
                        .where(
                            MemoryEntry.user_id == owner_id,
                            MemoryEntry.scope == "private",
                            MemoryEntry.deleted_at.is_(None),
                            MemoryEntry.metadata_["cortex"].astext == "true",
                            MemoryEntry.type == "episodic",
                        )
                        .order_by(MemoryEntry.created_at.asc())
                        .limit(_FORGET_SCAN_LIMIT)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                metadata = row.metadata_ or {}
                decision = decide_forget(
                    created_at=row.created_at,
                    now=now,
                    metadata=metadata,
                    memory_type=row.type,
                    # Uso real (ADR 0077): el contador que cortex_recall incrementa.
                    recall_frequency=recall_frequency_factor(metadata.get("recall_count", 0)),
                )
                if decision.forget:
                    row.deleted_at = now
                    # Deja rastro auditable de POR QUÉ se olvidó (reversible).
                    meta = dict(row.metadata_ or {})
                    meta["forgotten"] = {
                        "reason": decision.reason,
                        "score": decision.score,
                        "at": now.astimezone(UTC).isoformat(),
                    }
                    row.metadata_ = meta
                    forgotten += 1
    except Exception as exc:  # best-effort
        _log.warning("cortex_maintenance.forget_failed", owner=str(owner_id), error=str(exc))
        return forgotten
    return forgotten


async def _prune_old_snapshots(
    sessionmaker: async_sessionmaker[Any], owner_id: UUID, *, now: datetime
) -> int:
    """Poda los snapshots afectivos del owner más antiguos que la ventana de retención.

    La tabla es append-only inmutable: el delete físico aquí es de SERIE TEMPORAL
    vieja (no de memoria semántica del owner), acotado a ``owner_user_id`` explícito y
    a ``created_at < now - retention``. Idempotente. Best-effort."""
    from api_server.db.cortex_affect import CortexAffectSnapshot
    from sqlalchemy import delete

    cutoff = now - _SNAPSHOT_RETENTION
    try:
        async with sessionmaker() as session, session.begin():
            result = await session.execute(
                delete(CortexAffectSnapshot).where(
                    CortexAffectSnapshot.owner_user_id == owner_id,
                    CortexAffectSnapshot.created_at < cutoff,
                )
            )
            return int(result.rowcount or 0)
    except Exception as exc:  # best-effort
        _log.warning("cortex_maintenance.prune_failed", owner=str(owner_id), error=str(exc))
        return 0


# ---------------------------------------------------------------------------
# Trigger (lo agenda F4 con el beat; o un disparo manual)
# ---------------------------------------------------------------------------
def trigger_cortex_maintenance() -> bool:
    """Encola una pasada de mantenimiento del córtex (cola ``default``).

    Best-effort: un fallo del broker se traga y loguea (devuelve False)."""
    try:
        cortex_maintenance.apply_async(queue="default")
    except Exception as exc:
        _log.warning("cortex_maintenance.enqueue_failed", error=str(exc))
        return False
    return True


__all__ = ["cortex_maintenance", "trigger_cortex_maintenance"]
