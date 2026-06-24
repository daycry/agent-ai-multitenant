"""Endpoints del Panel de Mente — ``/owner/cortex/{mind,affect/timeseries,episodes}``.

Córtex F2 (ADR 0075). Todos gated por ``require_system_owner`` (DB-authoritative,
ADR 0074) y **owner-scoped**: las tablas ``cortex_*`` son tenant-less sobre
BYPASSRLS (excepción consciente al Principio 1), así que TODO ``SELECT`` filtra
``owner_user_id`` explícito; la episódica (``memory_entries``) además por
``user_id=owner`` (ahí sí hay RLS, pero el filtro explícito es defensa en
profundidad y la prueba de mérito es el test cross-owner).

  GET /owner/cortex/mind                estado vivo (Redis con decay lazy → BD).
  GET /owner/cortex/affect/timeseries   snapshots del owner (gráfico de mood + 2D).
  GET /owner/cortex/episodes            episódicas emocionales (mapa, hover=razón).

> Honestidad (ADR 0075 §6): ``/mind`` devuelve un bloque ``honesty`` con el copy
> "modelo computacional de afecto, NO sentimientos reales" que la UI rotula
> siempre; el estado es una simulación determinista, nunca sentimientos reales.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from api_server.auth.deps import AuthPrincipal, get_redis, require_system_owner
from api_server.cortex.affect_cache import read_affect_state
from api_server.cortex.affect_store import load_affect_state
from api_server.cortex.affective import AffectState
from api_server.db.cortex_affect import CortexAffectSnapshot
from api_server.db.memory import MemoryEntry
from api_server.db.session import get_admin_sessionmaker
from api_server.schemas.cortex_mind import (
    CortexAffectPoint,
    CortexDrives,
    CortexEpisodeItem,
    CortexMindResponse,
)

router = APIRouter(
    prefix="/owner/cortex",
    tags=["cortex"],
    dependencies=[Depends(require_system_owner)],
)


def _drives_schema(state: AffectState) -> CortexDrives:
    d = state.drives
    return CortexDrives(
        curiosity=d.curiosity,
        bonding=d.bonding,
        coherence=d.coherence,
        competence=d.competence,
    )


@router.get("/mind", response_model=CortexMindResponse)
async def get_mind(
    principal: AuthPrincipal = Depends(require_system_owner),
) -> CortexMindResponse:
    """El estado afectivo vivo del owner: emoción (Redis con decay lazy aplicado),
    mood + drives + ``mood_label`` del último snapshot, y el bloque honesty.

    Lee la caché Redis ``cortex:affect:{owner}`` (decay lazy en lectura); si no
    hay clave cae a la BD (``load_affect_state``, mismo decay). Sin snapshot ⇒
    baseline neutro. NUNCA mezcla owners (clave-por-owner / filtro explícito)."""
    owner_id = principal.user_id
    now = datetime.now(UTC)

    redis = get_redis()
    state = await read_affect_state(redis, str(owner_id), now=now)
    if state is None:
        # Caché fría → la BD es la fuente de verdad (mismo decay determinista).
        sessionmaker = get_admin_sessionmaker()
        async with sessionmaker() as session:
            state = await load_affect_state(session, owner_id, now=now)

    return CortexMindResponse(
        valence=state.emotion.valence,
        arousal=state.emotion.arousal,
        dominance=state.emotion.dominance,
        intensity=state.emotion.intensity,
        mood_valence=state.mood.valence,
        mood_arousal=state.mood.arousal,
        mood_dominance=state.mood.dominance,
        mood_label=state.mood_label(language="es"),
        drives=_drives_schema(state),
    )


@router.get("/affect/timeseries", response_model=list[CortexAffectPoint])
async def get_affect_timeseries(
    principal: AuthPrincipal = Depends(require_system_owner),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[CortexAffectPoint]:
    """Snapshots del owner en orden cronológico (filtro ``owner_user_id`` explícito).

    Sirve el gráfico de mood y el espacio PAD 2D con estela. ``since/until`` acotan
    por ``created_at``; ``limit`` los más recientes (devueltos en orden ASC). Un
    snapshot de OTRO owner nunca aparece (test cross-owner)."""
    owner_id = principal.user_id
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        stmt = select(CortexAffectSnapshot).where(CortexAffectSnapshot.owner_user_id == owner_id)
        if since is not None:
            stmt = stmt.where(CortexAffectSnapshot.created_at >= since)
        if until is not None:
            stmt = stmt.where(CortexAffectSnapshot.created_at <= until)
        # Los `limit` más recientes, luego reordenados ASC para el gráfico.
        stmt = stmt.order_by(CortexAffectSnapshot.created_at.desc()).limit(limit)
        rows = list((await session.execute(stmt)).scalars().all())
    rows.reverse()
    return [
        CortexAffectPoint(
            created_at=r.created_at,
            valence=r.valence,
            arousal=r.arousal,
            dominance=r.dominance,
            intensity=r.intensity,
            mood_valence=r.mood_valence,
            mood_arousal=r.mood_arousal,
            mood_dominance=r.mood_dominance,
            mood_label=r.mood_label,
            drives=CortexDrives(
                curiosity=float((r.drives or {}).get("curiosity", 0.5)),
                bonding=float((r.drives or {}).get("bonding", 0.5)),
                coherence=float((r.drives or {}).get("coherence", 0.5)),
                competence=float((r.drives or {}).get("competence", 0.5)),
            ),
        )
        for r in rows
    ]


@router.get("/episodes", response_model=list[CortexEpisodeItem])
async def get_episodes(
    principal: AuthPrincipal = Depends(require_system_owner),
    emotion: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[CortexEpisodeItem]:
    """Memorias episódicas emocionales del owner desde ``memory_entries`` (ADR 0077).

    Filtra ``user_id=owner`` + ``scope='private'`` + ``metadata_->>'cortex'='true'``
    + (opcional) ``metadata_->'emotion'->>'mood_label' = :emotion``. Cada item lleva
    ``appraisal_reason`` para el hover del mapa afectivo. NUNCA devuelve memorias de
    otro usuario (filtro ``user_id`` explícito)."""
    owner_id: UUID = principal.user_id
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        stmt = (
            select(MemoryEntry)
            .where(
                MemoryEntry.user_id == owner_id,
                MemoryEntry.scope == "private",
                MemoryEntry.deleted_at.is_(None),
                MemoryEntry.metadata_["cortex"].astext == "true",
            )
            .order_by(MemoryEntry.created_at.desc())
            .limit(limit)
        )
        if emotion:
            stmt = stmt.where(MemoryEntry.metadata_["emotion"]["mood_label"].astext == emotion)
        rows = list((await session.execute(stmt)).scalars().all())

    out: list[CortexEpisodeItem] = []
    for r in rows:
        emo = (r.metadata_ or {}).get("emotion") or {}
        out.append(
            CortexEpisodeItem(
                id=r.id,
                content=r.content,
                created_at=r.created_at,
                mood_label=emo.get("mood_label"),
                valence=emo.get("valence"),
                arousal=emo.get("arousal"),
                dominance=emo.get("dominance"),
                intensity=emo.get("intensity"),
                appraisal_reason=emo.get("appraisal_reason"),
            )
        )
    return out


__all__ = ["router"]
