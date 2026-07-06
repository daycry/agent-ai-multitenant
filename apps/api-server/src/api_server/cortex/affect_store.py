"""Córtex F2 — persistencia del estado afectivo, owner-scoped (BYPASSRLS).

Capa fina sobre :class:`CortexAffectSnapshot` (la serie temporal del motor PAD).
Igual que el resto del córtex, las tablas son **tenant-less** (sin RLS): **TODO
`SELECT` lleva un filtro `owner_user_id` explícito** (defensa en profundidad; el
test cross-owner de F2 es la prueba de mérito).

Dos operaciones:

- :func:`load_affect_state` — lee el último snapshot del owner y le aplica el
  **decay lazy en lectura** (la emoción y los drives decaen según el tiempo
  transcurrido desde ``created_at``; el mood, capa lenta, NO decae). Sin
  snapshot ⇒ baseline neutro. El reloj entra como parámetro ``now`` para que la
  lectura sea determinista (no usa el reloj real internamente). El decay
  converge al **baseline EVOLUTIVO de la identidad** (``identity.mood_baseline``
  vía :func:`api_server.cortex.identity.effective_mood_baseline`): se puede
  pasar ya cargado (``baseline=``) o se resuelve aquí en la misma sesión
  (fail-open a ``BASELINE_PAD`` si algo falla).
- :func:`save_affect_snapshot` — escribe una fila nueva (append-only) con la
  etiqueta de mood derivada ya calculada para la UI.

> Honestidad (ADR 0075 §6): simulación afectiva determinista, NO emociones
> reales.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.cortex.affective import (
    BASELINE_PAD,
    AffectState,
    Drives,
    Language,
    PADState,
    decay_drives,
    decay_emotion,
    neutral_affect_state,
)
from api_server.db.cortex_affect import CortexAffectSnapshot

_log = structlog.get_logger("api_server.cortex.affect_store")


def _snapshot_to_state(snapshot: CortexAffectSnapshot) -> AffectState:
    """Reconstruye el :class:`AffectState` persistido (SIN decay todavía)."""
    return AffectState(
        emotion=PADState(
            valence=snapshot.valence,
            arousal=snapshot.arousal,
            dominance=snapshot.dominance,
            intensity=snapshot.intensity,
        ),
        mood=PADState(
            valence=snapshot.mood_valence,
            arousal=snapshot.mood_arousal,
            dominance=snapshot.mood_dominance,
            intensity=0.0,
        ),
        drives=Drives.from_mapping(snapshot.drives or {}),
    )


def _elapsed_seconds(created_at: datetime, now: datetime) -> float:
    """Segundos entre ``created_at`` y ``now`` (≥ 0; tolerante a tz/naïve)."""
    # Los snapshots se guardan TIMESTAMPTZ; ``created_at`` viene aware. Si por lo
    # que sea ``now`` llega naïve, lo normalizamos a la tz de ``created_at``.
    if created_at.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=created_at.tzinfo)
    elif created_at.tzinfo is None and now.tzinfo is not None:
        created_at = created_at.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - created_at).total_seconds())


async def _identity_baseline(session: AsyncSession, owner_user_id: UUID) -> PADState:
    """El baseline evolutivo del owner (``identity.mood_baseline``), fail-open.

    Import local para no acoplar el módulo en import-time; cualquier fallo (tabla
    ausente en un downgrade, sesión rota…) cae a ``BASELINE_PAD`` — el decay
    NUNCA puede romper una lectura de afecto."""
    try:
        from api_server.cortex.identity import effective_mood_baseline, get_identity

        identity = await get_identity(session, owner_user_id)
        return effective_mood_baseline(identity.identity_state if identity else None)
    except Exception as exc:  # fail-open: el baseline es un matiz, no un bloqueo
        _log.warning(
            "cortex.affect_baseline_load_failed",
            owner_user_id=str(owner_user_id),
            error=str(exc),
        )
        return BASELINE_PAD


async def load_affect_state(
    session: AsyncSession,
    owner_user_id: UUID,
    *,
    now: datetime,
    baseline: PADState | None = None,
) -> AffectState:
    """Último snapshot del owner con **decay lazy** aplicado en lectura.

    Lee la fila más reciente filtrando ``owner_user_id`` explícito; si no hay
    ninguna devuelve :func:`neutral_affect_state`. Aplica :func:`decay_emotion`
    y :func:`decay_drives` con el tiempo transcurrido desde ``created_at`` hasta
    ``now`` (el mood, capa lenta, no decae). 100% determinista dado ``now``.

    El decay converge al **baseline evolutivo de la identidad**: pásalo ya
    cargado (``baseline=``) para evitar el SELECT, o se resuelve aquí en la
    misma sesión (fail-open a ``BASELINE_PAD``).
    """
    stmt = (
        select(CortexAffectSnapshot)
        .where(CortexAffectSnapshot.owner_user_id == owner_user_id)
        .order_by(CortexAffectSnapshot.created_at.desc())
        .limit(1)
    )
    snapshot = (await session.execute(stmt)).scalar_one_or_none()
    if snapshot is None:
        return neutral_affect_state()

    state = _snapshot_to_state(snapshot)
    elapsed_s = _elapsed_seconds(snapshot.created_at, now)
    if elapsed_s <= 0.0:
        return state

    if baseline is None:
        baseline = await _identity_baseline(session, owner_user_id)
    return AffectState(
        emotion=decay_emotion(state.emotion, baseline, elapsed_s=elapsed_s),
        mood=state.mood,  # capa lenta: no decae con el reloj
        drives=decay_drives(state.drives, elapsed_s=elapsed_s),
    )


async def save_affect_snapshot(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    state: AffectState,
    appraisal_reason: str | None = None,
    source_turn_id: UUID | None = None,
    language: Language = "es",
) -> CortexAffectSnapshot:
    """Persiste un snapshot inmutable del estado (flush, sin commit).

    La ``mood_label`` se deriva del mood y se guarda solo como conveniencia de
    UI (no es fuente de verdad). ``source_turn_id`` da idempotencia (UNIQUE
    parcial): el caller debe capturar la violación si re-entrega un turno.
    """
    snapshot = CortexAffectSnapshot(
        owner_user_id=owner_user_id,
        valence=state.emotion.valence,
        arousal=state.emotion.arousal,
        dominance=state.emotion.dominance,
        intensity=state.emotion.intensity,
        mood_valence=state.mood.valence,
        mood_arousal=state.mood.arousal,
        mood_dominance=state.mood.dominance,
        mood_label=state.mood_label(language=language),
        drives=state.drives.as_dict(),
        appraisal_reason=appraisal_reason,
        source_turn_id=source_turn_id,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


__all__ = ["load_affect_state", "save_affect_snapshot"]
