"""Córtex F2 — caché Redis del estado afectivo "vivo" (decay lazy en lectura).

El snapshot de PostgreSQL (``cortex_affect_snapshots``, ver
:mod:`api_server.cortex.affect_store`) es la **serie temporal append-only** y la
fuente de verdad durable. Esta caché Redis ``cortex:affect:{owner}`` es el estado
**vivo** para lecturas rápidas del Panel de Mente / la telemetría: el distilador
la escribe al aplicar un delta y el endpoint ``GET /owner/cortex/mind`` la lee con
el **decay lazy aplicado en lectura** (la emoción y los drives decaen según el
tiempo transcurrido desde ``updated_at``; el mood, capa lenta, NO decae), igual
que :func:`api_server.cortex.affect_store.load_affect_state` pero sin tocar la BD.

Aislamiento (excepción consciente al Principio 1, ADR 0074): el córtex es
tenant-less; la **clave-por-owner** es el eje de aislamiento (un owner nunca lee
la clave de otro). TTL largo: aunque la clave expire, el endpoint cae a la BD; y
un estado viejo leído con decay lazy converge al baseline igualmente.

El reloj entra como parámetro ``now`` para que la lectura sea determinista (no usa
el reloj real internamente), reusando el motor puro de :mod:`api_server.cortex.affective`.

> Honestidad (ADR 0075 §6): simulación afectiva determinista, NO emociones reales.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import structlog
from redis.asyncio import Redis

from api_server.cortex.affective import (
    AffectState,
    Drives,
    PADState,
    decay_drives,
    decay_emotion,
    neutral_affect_state,
)

_log = structlog.get_logger("api_server.cortex.affect_cache")

#: TTL de la clave viva. Largo (24 h): si expira el endpoint cae a la BD; y un
#: estado viejo leído con decay lazy ya lee ≈baseline. Operator-tunable a futuro.
AFFECT_CACHE_TTL_S: int = 24 * 3600


def affect_cache_key(owner_user_id: str) -> str:
    """Clave Redis del estado afectivo vivo de un owner."""
    return f"cortex:affect:{owner_user_id}"


def _serialize(state: AffectState, *, updated_at: datetime) -> str:
    """JSON del estado vivo + el ``updated_at`` que ancla el decay lazy."""
    return json.dumps(
        {
            "updated_at": updated_at.astimezone(UTC).isoformat(),
            "emotion": {
                "valence": state.emotion.valence,
                "arousal": state.emotion.arousal,
                "dominance": state.emotion.dominance,
                "intensity": state.emotion.intensity,
            },
            "mood": {
                "valence": state.mood.valence,
                "arousal": state.mood.arousal,
                "dominance": state.mood.dominance,
            },
            "drives": state.drives.as_dict(),
        }
    )


def _deserialize(raw: str) -> tuple[AffectState, datetime] | None:
    """Reconstruye ``(state, updated_at)`` del JSON; ``None`` si está corrupto."""
    try:
        data = json.loads(raw)
        emo = data["emotion"]
        mood = data["mood"]
        updated_at = datetime.fromisoformat(data["updated_at"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    state = AffectState(
        emotion=PADState(
            valence=float(emo["valence"]),
            arousal=float(emo["arousal"]),
            dominance=float(emo["dominance"]),
            intensity=float(emo.get("intensity", 0.0)),
        ),
        mood=PADState(
            valence=float(mood["valence"]),
            arousal=float(mood["arousal"]),
            dominance=float(mood["dominance"]),
            intensity=0.0,
        ),
        drives=Drives.from_mapping(data.get("drives") or {}),
    )
    return state, updated_at


def _elapsed_seconds(updated_at: datetime, now: datetime) -> float:
    """Segundos entre ``updated_at`` y ``now`` (≥ 0; tolerante a tz/naïve)."""
    if updated_at.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=updated_at.tzinfo)
    elif updated_at.tzinfo is None and now.tzinfo is not None:
        updated_at = updated_at.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - updated_at).total_seconds())


async def write_affect_state(
    redis: Redis,
    owner_user_id: str,
    state: AffectState,
    *,
    now: datetime,
) -> None:
    """Persiste el estado vivo del owner en Redis (best-effort, con TTL).

    Guarda ``now`` como ``updated_at`` para que la siguiente lectura calcule el
    decay lazy. Nunca lanza: el snapshot de la BD ya es la fuente de verdad, así
    que un fallo de Redis no debe romper al distilador."""
    try:
        await redis.set(
            affect_cache_key(owner_user_id),
            _serialize(state, updated_at=now),
            ex=AFFECT_CACHE_TTL_S,
        )
    except Exception as exc:  # caché best-effort; la BD es la fuente de verdad
        _log.warning(
            "cortex.affect_cache_write_failed", owner_user_id=owner_user_id, error=str(exc)
        )


async def read_affect_state(
    redis: Redis,
    owner_user_id: str,
    *,
    now: datetime,
) -> AffectState | None:
    """Lee el estado vivo del owner con **decay lazy** aplicado en lectura.

    Devuelve ``None`` si no hay clave (el caller cae a la BD) o si está corrupta.
    Aplica :func:`decay_emotion`/:func:`decay_drives` con el tiempo transcurrido
    desde ``updated_at`` hasta ``now`` (el mood, capa lenta, no decae). 100%
    determinista dado ``now``. Best-effort: un fallo de Redis devuelve ``None``."""
    try:
        raw = await redis.get(affect_cache_key(owner_user_id))
    except Exception as exc:  # caché best-effort; el caller cae a la BD
        _log.warning("cortex.affect_cache_read_failed", owner_user_id=owner_user_id, error=str(exc))
        return None
    if raw is None:
        return None
    parsed = _deserialize(raw if isinstance(raw, str) else raw.decode())
    if parsed is None:
        return None
    state, updated_at = parsed
    elapsed_s = _elapsed_seconds(updated_at, now)
    if elapsed_s <= 0.0:
        return state
    baseline = neutral_affect_state().emotion
    return AffectState(
        emotion=decay_emotion(state.emotion, baseline, elapsed_s=elapsed_s),
        mood=state.mood,  # capa lenta: no decae con el reloj
        drives=decay_drives(state.drives, elapsed_s=elapsed_s),
    )


async def delete_affect_state(redis: Redis, owner_user_id: str) -> None:
    """Borra la clave viva del owner (best-effort)."""
    try:
        await redis.delete(affect_cache_key(owner_user_id))
    except Exception as exc:  # cleanup best-effort, nunca falla al caller
        _log.warning(
            "cortex.affect_cache_delete_failed", owner_user_id=owner_user_id, error=str(exc)
        )


__all__ = [
    "AFFECT_CACHE_TTL_S",
    "affect_cache_key",
    "delete_affect_state",
    "read_affect_state",
    "write_affect_state",
]
