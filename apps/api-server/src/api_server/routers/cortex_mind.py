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
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from api_server.auth.deps import AuthPrincipal, get_redis, require_system_owner
from api_server.cortex.affect_cache import read_affect_state
from api_server.cortex.affect_store import load_affect_state
from api_server.cortex.affective import AffectState
from api_server.cortex.identity import (
    clamp_baseline,
    clamp_traits,
    editable_owner_state,
    ensure_identity,
    update_identity,
)
from api_server.db.cortex_affect import CortexAffectSnapshot
from api_server.db.cortex_curiosity import CortexCuriosityPursuit
from api_server.db.memory import MemoryEntry
from api_server.db.models import User
from api_server.db.platform_settings import PlatformSettingForbiddenError
from api_server.db.session import get_admin_sessionmaker
from api_server.schemas.cortex_autonomy import (
    CortexAutonomyBudget,
    CortexAutonomyResponse,
    CortexAutonomyUpdateRequest,
)
from api_server.schemas.cortex_curiosity import CortexPursuitItem
from api_server.schemas.cortex_identity import (
    CortexBaseline,
    CortexIdentityResponse,
    CortexIdentityUpdateRequest,
    CortexReflectResponse,
    CortexTraits,
)
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


# ===========================================================================
# Identidad evolutiva del córtex (Córtex F3, ADR 0074/0077)
# ===========================================================================
# Onboarding co-diseñado + override del owner. La identidad es un SINGLETON por
# owner sobre tablas tenant-less (BYPASSRLS): TODO acceso filtra ``owner_user_id``
# explícito. El owner co-diseña name/core_values/narrative/language y fija
# learning_goals; los rasgos Big-Five, el mood_baseline y el modelo del owner los
# DERIVA la reflexión periódica (clampeada + versionada) — el owner NO los pisa a
# mano (guardrail de auto-modificación, ADR 0074). La identidad NUNCA se borra
# (ADR 0077): cada cambio se versiona en ``cortex_identity_history``.
@router.get("/curiosity/pursuits", response_model=list[CortexPursuitItem])
async def list_curiosity_pursuits(
    principal: AuthPrincipal = Depends(require_system_owner),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[CortexPursuitItem]:
    """El historial de curiosidad del owner ("lo que está aprendiendo", ADR 0078).

    Filtro ``owner_user_id`` explícito (tenant-less, BYPASSRLS — ADR 0074), orden
    ``created_at DESC``, filtro opcional por ``status``. Copy honesto en la UI:
    es el bucle de curiosidad programado, no curiosidad consciente."""
    owner_id = principal.user_id
    stmt = (
        select(CortexCuriosityPursuit)
        .where(CortexCuriosityPursuit.owner_user_id == owner_id)
        .order_by(CortexCuriosityPursuit.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(CortexCuriosityPursuit.status == status_filter)
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        pursuits = (await session.execute(stmt)).scalars().all()
    return [
        CortexPursuitItem(
            id=p.id,
            topic=p.topic,
            status=p.status,
            created_at=p.created_at,
            surfaced_at=p.surfaced_at,
            learning_memory_id=p.learning_memory_id,
            search_count=int(p.search_count),
        )
        for p in pursuits
    ]


def _identity_response(
    state: dict[str, Any], *, version: int, updated_by: str, onboarded_at: datetime | None
) -> CortexIdentityResponse:
    """Mapea un ``identity_state`` (+ metadatos) al schema de respuesta.

    Los derivados (traits/mood_baseline) se devuelven CLAMPEADOS a su rango
    canónico (defensa en profundidad: una fila vieja/sucia nunca desborda)."""
    traits = clamp_traits(state.get("traits"))
    baseline = clamp_baseline(state.get("mood_baseline"))
    name = state.get("name")
    raw_relationship = state.get("relationship_model")
    relationship = (
        {str(k): str(v) for k, v in raw_relationship.items()}
        if isinstance(raw_relationship, dict)
        else {}
    )
    return CortexIdentityResponse(
        name=(name if isinstance(name, str) and name.strip() else None),
        core_values=[str(v) for v in (state.get("core_values") or [])],
        narrative=str(state.get("narrative") or ""),
        language=str(state.get("language") or "es"),
        learning_goals=[str(v) for v in (state.get("learning_goals") or [])],
        traits=CortexTraits(**traits),
        mood_baseline=CortexBaseline(**baseline),
        relationship_model=relationship,
        version=version,
        updated_by=updated_by,
        onboarded_at=onboarded_at,
    )


@router.get("/identity", response_model=CortexIdentityResponse)
async def get_identity_endpoint(
    principal: AuthPrincipal = Depends(require_system_owner),
) -> CortexIdentityResponse:
    """La identidad actual del córtex del owner (crea la default si no existe).

    ``onboarded_at=null`` ⇒ onboarding pendiente (la UI lo muestra de forma
    prominente). Filtra ``owner_user_id`` explícito sobre la sesión BYPASSRLS."""
    owner_id = principal.user_id
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        identity = await ensure_identity(session, owner_id)
        return _identity_response(
            dict(identity.identity_state or {}),
            version=identity.version,
            updated_by=identity.updated_by,
            onboarded_at=identity.onboarded_at,
        )


@router.put("/identity", response_model=CortexIdentityResponse)
async def put_identity_endpoint(
    payload: CortexIdentityUpdateRequest,
    principal: AuthPrincipal = Depends(require_system_owner),
) -> CortexIdentityResponse:
    """Onboarding co-diseñado / override del owner de la identidad del córtex.

    El owner fija SOLO name/core_values/narrative/language/learning_goals (campos
    co-diseñados); ``traits``/``mood_baseline`` se PRESERVAN (los deriva la
    reflexión — el schema rechaza con 422 cualquier intento de tocarlos). Versiona
    en ``cortex_identity_history`` (``updated_by='owner_override'``,
    ``reason='owner_onboarding'``) y marca ``onboarded_at`` si era NULL (la primera
    vez es el onboarding; luego es un override idempotente que no re-marca)."""
    owner_id = principal.user_id
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        identity = await ensure_identity(session, owner_id)
        new_state = editable_owner_state(
            dict(identity.identity_state or {}),
            name=payload.name,
            core_values=payload.core_values,
            narrative=payload.narrative,
            language=payload.language,
            learning_goals=payload.learning_goals,
        )
        updated = await update_identity(
            session,
            owner_id,
            new_state=new_state,
            reason="owner_onboarding",
            updated_by="owner_override",
        )
        # Marca onboarded_at en el PRIMER override (era NULL = onboarding pendiente).
        if updated.onboarded_at is None:
            updated.onboarded_at = datetime.now(UTC)
        await session.flush()
        return _identity_response(
            dict(updated.identity_state or {}),
            version=updated.version,
            updated_by=updated.updated_by,
            onboarded_at=updated.onboarded_at,
        )


@router.post("/reflect", response_model=CortexReflectResponse)
async def reflect_now_endpoint(
    principal: AuthPrincipal = Depends(require_system_owner),
) -> CortexReflectResponse:
    """Dispara una pasada de reflexión de la identidad del córtex (manual/test).

    Encola ``workers.cortex_reflect`` para el córtex del owner (fire-and-forget,
    fuera del hot-path). La tarea sintetiza los turnos recientes en una narrativa
    reescrita + un ajuste CLAMPEADO de traits/baseline (Ollama-local, fail-open),
    versionado en ``cortex_identity_history``. La cadencia RECURRENTE la agenda el
    beat de F4; este endpoint solo permite un disparo bajo demanda. Best-effort: un
    fallo del broker devuelve ``enqueued=false`` sin romper."""
    from api_server.celery_client import enqueue_cortex_reflection

    enqueued = await enqueue_cortex_reflection(principal.user_id)
    return CortexReflectResponse(enqueued=enqueued)


# ===========================================================================
# Autonomía: kill-switch global de los bucles cognitivos de fondo (F4, ADR 0078)
# ===========================================================================
# El owner ve/activa el KILL-SWITCH global de la autonomía (curiosidad + reflexión
# programada + mantenimiento) y consulta el budget de búsquedas consumido hoy vs el
# cap. Default OFF: ningún bucle hace trabajo hasta que el owner lo enciende
# explícitamente. Copy honesto: la curiosidad es un comportamiento PROGRAMADO con
# límites de coste auditables, no curiosidad consciente.
async def _autonomy_snapshot(owner_id: UUID) -> CortexAutonomyResponse:
    """Estado vivo de la autonomía: settings (BD) + budget/breaker (Redis)."""
    from api_server.cortex.autonomy import (
        CURIOSITY_KIND,
        circuit_key,
        daily_budget_key,
    )
    from api_server.db.platform_settings import (
        get_cortex_autonomy_enabled,
        get_cortex_curiosity_daily_searches_cap,
        get_cortex_curiosity_drive_threshold,
        get_cortex_web_enabled,
    )

    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        autonomy = await get_cortex_autonomy_enabled(session)
        web = await get_cortex_web_enabled(session)
        cap = await get_cortex_curiosity_daily_searches_cap(session)
        threshold = await get_cortex_curiosity_drive_threshold(session)

    now = datetime.now(UTC)
    redis = get_redis()
    searches_today = 0
    breaker_open = False
    try:
        raw = await redis.get(daily_budget_key(str(owner_id), CURIOSITY_KIND, now=now))
        searches_today = int(raw) if raw is not None else 0
        breaker_open = bool(await redis.exists(circuit_key(str(owner_id), CURIOSITY_KIND)))
    except Exception:  # estado vivo best-effort; la BD es la fuente de verdad
        searches_today = 0
        breaker_open = False

    return CortexAutonomyResponse(
        autonomy_enabled=autonomy,
        web_enabled=web,
        curiosity_drive_threshold=threshold,
        circuit_breaker_open=breaker_open,
        budget=CortexAutonomyBudget(searches_today=searches_today, searches_cap=cap),
    )


@router.get("/autonomy", response_model=CortexAutonomyResponse)
async def get_autonomy(
    principal: AuthPrincipal = Depends(require_system_owner),
) -> CortexAutonomyResponse:
    """Estado de la autonomía del córtex: kill-switch + gates + budget consumido hoy.

    Owner-scoped: el budget/breaker se leen por la clave-por-owner del principal."""
    return await _autonomy_snapshot(principal.user_id)


@router.put("/autonomy", response_model=CortexAutonomyResponse)
async def put_autonomy(
    payload: CortexAutonomyUpdateRequest,
    principal: AuthPrincipal = Depends(require_system_owner),
) -> CortexAutonomyResponse:
    """Update PARCIAL de los gates del córtex (System Owner, desde la UI).

    ``autonomy_enabled`` flipa el kill-switch global (``cortex.autonomy_enabled``);
    ``web_enabled`` el gate de la web del córtex (``cortex.web_enabled``, ADR
    0067). ``set_platform_setting`` re-verifica que el actor es System Admin (el
    owner del despliegue lo es, ADR 0074); un owner que NO fuese admin recibiría
    un 403 honesto en vez de una escritura silenciosa. Con el kill-switch OFF,
    la siguiente pasada de CADA bucle sale no-op."""
    from api_server.db.platform_settings import (
        CORTEX_AUTONOMY_ENABLED_KEY,
        CORTEX_WEB_ENABLED_KEY,
        set_platform_setting,
    )

    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        actor = await session.get(User, principal.user_id)
        if actor is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="actor user not found"
            )
        try:
            if payload.autonomy_enabled is not None:
                await set_platform_setting(
                    session, CORTEX_AUTONOMY_ENABLED_KEY, payload.autonomy_enabled, actor=actor
                )
            if payload.web_enabled is not None:
                await set_platform_setting(
                    session, CORTEX_WEB_ENABLED_KEY, payload.web_enabled, actor=actor
                )
        except PlatformSettingForbiddenError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _autonomy_snapshot(principal.user_id)


__all__ = ["router"]
