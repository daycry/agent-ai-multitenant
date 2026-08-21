"""Endpoints del Panel de Mente — ``/owner/cortex/{mind,affect/timeseries,episodes}``.

Córtex F2 (ADR 0075). Todos gated por ``require_system_owner`` (DB-authoritative,
ADR 0074) y **owner-scoped**: las tablas ``cortex_*`` son tenant-less sobre
BYPASSRLS (excepción consciente al Principio 1), así que TODO ``SELECT`` filtra
``owner_user_id`` explícito; la episódica (``memory_entries``) además por
``user_id=owner`` (ahí sí hay RLS, pero el filtro explícito es defensa en
profundidad y la prueba de mérito es el test cross-owner).

  GET  /owner/cortex/mind                estado vivo (Redis con decay lazy → BD).
  GET  /owner/cortex/affect/timeseries   snapshots del owner (gráfico de mood + 2D).
  GET  /owner/cortex/episodes            episódicas emocionales (mapa, hover=razón).
  GET  /owner/cortex/identity/history    timeline de versiones CON su diff (F3).
  POST /owner/cortex/identity/onboarding onboarding co-diseñado: el córtex se
                                         autonombra y el owner confirma (F3.3).
  GET  /owner/cortex/curiosity/pursuits  temas que el córtex ha investigado (F4).
  POST /owner/cortex/curiosity/pursuits/{id}/approve
                                         owner-approval gate de la curiosidad (F4).
  GET  /owner/cortex/autonomy            kill-switch + gates + budget del día en
                                         sus DOS dimensiones: búsquedas y dólares.
  PUT  /owner/cortex/autonomy            flipa el kill-switch y los gates (F4).

> Honestidad (ADR 0075 §6): ``/mind`` devuelve un bloque ``honesty`` con el copy
> "modelo computacional de afecto, NO sentimientos reales" que la UI rotula
> siempre; el estado es una simulación determinista, nunca sentimientos reales.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_llm.exceptions import AuthError, LLMError, RateLimitError
from sqlalchemy import select

from api_server.assistant.graph import AssistantModelClient
from api_server.auth.deps import AuthPrincipal, get_redis, require_system_owner
from api_server.cortex.affect_cache import read_affect_state
from api_server.cortex.affect_store import load_affect_state
from api_server.cortex.affective import AffectState
from api_server.cortex.identity import (
    clamp_baseline,
    clamp_traits,
    compute_diff,
    editable_owner_state,
    ensure_identity,
    update_identity,
)
from api_server.cortex.onboarding import apply_onboarding, propose_onboarding
from api_server.cortex.threads import CortexNoTenantError, resolve_cortex_tenant_id
from api_server.cortex.tools import CortexToolContext
from api_server.db.cortex_affect import CortexAffectSnapshot
from api_server.db.cortex_curiosity import CortexCuriosityPursuit
from api_server.db.memory import MemoryEntry
from api_server.db.models import User
from api_server.db.platform_settings import PlatformSettingForbiddenError
from api_server.db.session import get_admin_sessionmaker
from api_server.routers.cortex import get_cortex_model
from api_server.schemas.cortex_autonomy import (
    CortexAutonomyBudget,
    CortexAutonomyResponse,
    CortexAutonomyUpdateRequest,
)
from api_server.schemas.cortex_curiosity import CortexPursuitDecisionRequest, CortexPursuitItem
from api_server.schemas.cortex_identity import (
    CortexBaseline,
    CortexIdentityResponse,
    CortexIdentityUpdateRequest,
    CortexIdentityVersionItem,
    CortexOnboardingRequest,
    CortexOnboardingResponse,
    CortexReflectResponse,
    CortexTraits,
)
from api_server.schemas.cortex_mind import (
    CortexAffectPoint,
    CortexDrives,
    CortexEpisodeItem,
    CortexJournalEntry,
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
    + **``metadata_ ? 'emotion'``** + (opcional)
    ``metadata_->'emotion'->>'mood_label' = :emotion``. Cada item lleva
    ``appraisal_reason`` para el hover del mapa afectivo. NUNCA devuelve memorias de
    otro usuario (filtro ``user_id`` explícito).

    La condición de ``emotion`` PRESENTE no es redundante con el filtro opcional:
    aquél solo actúa cuando se pasa el query param, y sin ésta el mapa se llenaba de
    memorias sin afecto (las escribe ``cortex_remember``, que también marca
    ``cortex=true``) pintadas con un PAD de ceros."""
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
                # Cuarta condición del contrato (cortex-f2-afectivo.md): la
                # memoria tiene que traer `emotion`. Faltaba, y `cortex=true` NO
                # es exclusivo del distilador afectivo — lo escribe también
                # `cortex_remember`, así que el mapa de episodios se llenaba de
                # memorias SIN afecto que el render pinta con un PAD de ceros:
                # un episodio neutro inventado donde no hubo emoción ninguna.
                # Detectado al escribir el test del contrato completo (2026-07-28).
                MemoryEntry.metadata_.has_key("emotion"),  # — operador JSONB `?`
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


@router.get("/journal", response_model=list[CortexJournalEntry])
async def get_journal(
    principal: AuthPrincipal = Depends(require_system_owner),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[CortexJournalEntry]:
    """El diario del córtex (C4, investigación 2026-07-11): línea temporal única.

    La vida interior existía (PAD, drives, episodios, pursuits) pero sin RELATO:
    la narrativa es UN campo que cada reflexión sobrescribe. Este endpoint
    reconstruye la línea temporal mezclando (a) las narrativas versionadas de
    ``cortex_identity_history`` (dedup de consecutivas idénticas, con su
    ``reason``) y (b) las memorias ``kind='reflection'`` / ``kind='learning'``
    — entradas de diario de facto. Aislamiento: ``owner_user_id`` explícito."""
    from api_server.db.cortex_identity import CortexIdentityHistory

    owner_id: UUID = principal.user_id
    sessionmaker = get_admin_sessionmaker()
    entries: list[CortexJournalEntry] = []
    async with sessionmaker() as session:
        history = list(
            (
                await session.execute(
                    select(CortexIdentityHistory)
                    .where(CortexIdentityHistory.owner_user_id == owner_id)
                    .order_by(CortexIdentityHistory.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        prev_narrative: str | None = None
        for row in reversed(history):  # cronológico para dedup de consecutivas
            narrative = str((row.identity_state or {}).get("narrative") or "").strip()
            if narrative and narrative != prev_narrative:
                entries.append(
                    CortexJournalEntry(
                        kind="narrative",
                        content=narrative,
                        reason=row.reason,
                        created_at=row.created_at,
                    )
                )
            prev_narrative = narrative or prev_narrative

        memories = list(
            (
                await session.execute(
                    select(MemoryEntry)
                    .where(
                        MemoryEntry.user_id == owner_id,
                        MemoryEntry.scope == "private",
                        MemoryEntry.deleted_at.is_(None),
                        MemoryEntry.metadata_["cortex"].astext == "true",
                        MemoryEntry.metadata_["kind"].astext.in_(("reflection", "learning")),
                    )
                    .order_by(MemoryEntry.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for mem in memories:
            entries.append(
                CortexJournalEntry(
                    kind=str((mem.metadata_ or {}).get("kind") or "reflection"),
                    content=mem.content,
                    reason=None,
                    created_at=mem.created_at,
                )
            )

    entries.sort(key=lambda e: e.created_at, reverse=True)
    return entries[:limit]


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
    return [_pursuit_item(p) for p in pursuits]


def _pursuit_item(p: CortexCuriosityPursuit) -> CortexPursuitItem:
    """Proyecta una fila de pursuit al schema de respuesta.

    Los dos ``Numeric`` de la tabla llegan como ``Decimal`` y hay que convertirlos a
    mano: ``search_count`` es ``Numeric(10,0)`` (debió ser ``Integer``, divergencia
    conocida) y ``cost_usd`` es ``Numeric(12,6)``. Sin el ``float()``, Pydantic
    serializaría el ``Decimal`` y el cliente TypeScript recibiría un string donde su
    tipo dice ``number``."""
    return CortexPursuitItem(
        id=p.id,
        topic=p.topic,
        status=p.status,
        created_at=p.created_at,
        surfaced_at=p.surfaced_at,
        learning_memory_id=p.learning_memory_id,
        search_count=int(p.search_count),
        approved=p.approved,
        cost_usd=float(p.cost_usd or 0.0),
    )


@router.post("/curiosity/pursuits/{pursuit_id}/approve", response_model=CortexPursuitItem)
async def decide_curiosity_pursuit(
    pursuit_id: UUID,
    payload: CortexPursuitDecisionRequest,
    principal: AuthPrincipal = Depends(require_system_owner),
) -> CortexPursuitItem:
    """**Owner-approval gate** de la curiosidad autónoma (paso 7 del bucle, ADR 0078).

    Con ``cortex.curiosity_approval_gate`` ON (default), el bucle elige un tema, deja
    el pursuit en ``selected`` con ``approved IS NULL`` y **NO sale a Internet**. Este
    endpoint es la ÚNICA vía por la que ese veredicto se escribe.

    Efecto de cada decisión, y por qué son asimétricas:

    * **Aprobar** ⇒ ``approved=true`` y el ``status`` se queda en ``selected``. NO lo
      adelanta a ``searching``: el que sale a buscar es el bucle, y su consulta de
      reanudación (``workers/cortex_curiosity.py::_find_resumable_pursuit``) exige
      ``status='selected' AND approved IS NOT FALSE``. Escribir ``searching`` aquí
      dejaría el pursuit aprobado fuera de esa consulta y nadie lo investigaría nunca.
    * **Rechazar** ⇒ ``approved=false`` **y** ``status='skipped'``. El ``false`` lo saca
      de la consulta del bucle; el ``skipped`` lo cierra para el panel (en ``selected``
      seguiría apareciendo como "esperando decisión" ya decidido). La razón queda en
      ``metadata.reason``, la misma clave que usa el bucle.

    Solo un pursuit PENDIENTE es decidible (``status='selected'`` y ``approved IS
    NULL``). Exigir las dos condiciones importa: las filas anteriores a la migración
    0123 quedaron en ``approved IS NULL`` a propósito, así que ``approved IS NULL`` a
    solas dejaría "aprobar" una persecución de hace meses ya terminada.

    Códigos: **404** si el pursuit no existe *o no es del owner* (un 403 confirmaría
    que el id existe, y la tabla es tenant-less sin RLS de respaldo — el aislamiento es
    el filtro ``owner_user_id`` explícito de este UPDATE); **409** si ya estaba decidido
    al contrario o no está pendiente. Repetir la MISMA decisión es un no-op 200
    (idempotente: el doble clic del panel no debe ser un error)."""
    owner_id = principal.user_id
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        # Filtro por (id, owner) en el MISMO SELECT: nunca se carga una fila ajena
        # para decidir después si se puede tocar.
        #
        # ``with_for_update()`` porque esto es un read-modify-write sobre una fila que
        # el bucle de fondo también muta (``_mark_pursuit_searching``): sin el lock,
        # decidir y avanzar a la vez podrían leer el mismo estado previo y la última
        # escritura ganaría. NO cierra la ventana entera —si el bucle ya salió a buscar,
        # un rechazo posterior no deshace la búsqueda ya pagada—, pero sí garantiza que
        # el veredicto no se pierda ni se aplique sobre un estado rancio.
        row = (
            await session.execute(
                select(CortexCuriosityPursuit)
                .where(
                    CortexCuriosityPursuit.id == pursuit_id,
                    CortexCuriosityPursuit.owner_user_id == owner_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pursuit not found")

        if row.approved == payload.approved:
            # Misma decisión otra vez: no-op idempotente (doble clic / reintento).
            return _pursuit_item(row)
        if row.approved is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "pursuit already decided: no se puede invertir un veredicto ya"
                    " dictado (la búsqueda pudo haberse hecho y el gasto no se deshace)"
                ),
            )
        if row.status != "selected":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"pursuit not awaiting approval (status={row.status})",
            )

        row.approved = payload.approved
        if not payload.approved:
            row.status = "skipped"
            row.metadata_ = {**(row.metadata_ or {}), "reason": "owner_rejected"}
        await session.flush()
        return _pursuit_item(row)


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


@router.post("/identity/onboarding", response_model=CortexOnboardingResponse)
async def post_identity_onboarding(
    payload: CortexOnboardingRequest | None = None,
    principal: AuthPrincipal = Depends(require_system_owner),
    model: AssistantModelClient = Depends(get_cortex_model),
) -> CortexOnboardingResponse:
    """Onboarding **co-diseñado**: el córtex se autonombra y el owner confirma (F3.3).

    Hasta aquí el «autonombrado» del plan no existía: ``propose_identity`` estaba
    escrita y probada, pero nadie generaba el turno, así que el owner rellenaba el
    formulario de ``PUT /identity`` a mano. Este endpoint es el llamante que
    faltaba, en DOS pasos sobre la misma ruta:

    * **sin ``confirm``** — corre UN turno con el grafo del córtex de F1
      (``run_cortex_turn``, sin duplicar el turn-loop) y devuelve el
      ``identity_state`` candidato + el ``diff`` contra el vigente + el texto
      literal del turno. **No persiste** la propuesta: ``onboarded_at`` sigue nulo.
    * **``confirm=true``** — persiste lo que el owner acepta (``apply_onboarding``:
      ``updated_by='onboarding'``, ``onboarded_at=now``, versión en
      ``cortex_identity_history``).

    **Idempotente**: con ``onboarded_at`` ya puesto devuelve ``already_onboarded``
    sin gastar un turno de LLM ni reescribir la identidad — que la UI se recargue no
    puede costar dinero ni borrar el nombre que el owner eligió. A partir de ahí el
    camino de edición es ``PUT /identity`` (``owner_override``, ADR 0157).

    El turno corre con **cero tools** (autonombrarse no necesita memoria, web ni
    navegador; con el catálogo puesto, ``cortex_remember`` escribiría memoria
    durante una propuesta aún no confirmada) y sin modulación afectiva: es un turno
    de arranque, no una conversación.

    Guardrail ADR 0074 por las DOS puertas: ``traits``/``mood_baseline`` los
    descarta ``propose_identity`` si los propone el modelo, y los rechaza con 422 el
    schema (``extra='forbid'``) si los manda el owner. Los deriva la reflexión.

    El paso de propuesta necesita el modelo del córtex (503 si no hay
    ``cortex.default_model``); ``PUT /identity`` sigue siendo el camino sin LLM.
    Aislamiento (ADR 0074/0156): sesión admin/BYPASSRLS con filtro ``owner_user_id``
    explícito en todo el acceso.
    """
    body = payload or CortexOnboardingRequest()
    owner_id = principal.user_id
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        identity = await ensure_identity(session, owner_id)
        current = dict(identity.identity_state or {})

        if identity.onboarded_at is not None:
            # Ya onboardado: ni turno LLM ni reescritura (idempotencia barata; la
            # dura vive dentro de ``apply_onboarding``).
            return CortexOnboardingResponse(
                already_onboarded=True,
                applied=False,
                identity=_identity_response(
                    current,
                    version=identity.version,
                    updated_by=identity.updated_by,
                    onboarded_at=identity.onboarded_at,
                ),
            )

        if body.confirm:
            updated, applied = await apply_onboarding(
                session,
                owner_id,
                {
                    "name": body.name,
                    "core_values": body.core_values,
                    "narrative": body.narrative,
                    "language": body.language,
                    "learning_goals": body.learning_goals,
                },
            )
            confirmed = dict(updated.identity_state or {})
            return CortexOnboardingResponse(
                already_onboarded=not applied,
                applied=applied,
                identity=_identity_response(
                    confirmed,
                    version=updated.version,
                    updated_by=updated.updated_by,
                    onboarded_at=updated.onboarded_at,
                ),
                diff=compute_diff(current, confirmed),
            )

        # Paso de propuesta: el córtex habla. El tenant es el discriminante físico
        # de la memoria del owner (Decisión D1); aquí queda inerte porque el turno
        # no despacha ninguna tool, pero se resuelve igual que en el chat para no
        # tener dos reglas distintas sobre qué necesita el córtex para hablar.
        try:
            tenant_id = await resolve_cortex_tenant_id(session, owner_id)
        except CortexNoTenantError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

        try:
            proposal = await propose_onboarding(
                model,
                current_state=current,
                tool_ctx=CortexToolContext(
                    session=session, owner_user_id=owner_id, tenant_id=tenant_id
                ),
            )
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"el proveedor LLM rechazó las credenciales (auth): {exc}",
            ) from exc
        except RateLimitError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"el proveedor LLM está limitando las peticiones: {exc}",
            ) from exc
        except LLMError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"el proveedor LLM del córtex falló: {exc}",
            ) from exc

        # ADR 0116: el turno de onboarding también quema tokens del owner y también
        # se contabiliza (best-effort; tenant_id=None — es consumo de plataforma).
        from api_server.llm_usage import record_llm_usage

        await record_llm_usage(
            session, source="cortex", model_client=model, tenant_id=None, user_id=owner_id
        )

        return CortexOnboardingResponse(
            already_onboarded=False,
            applied=False,
            proposal=proposal.text,
            identity=_identity_response(
                proposal.state,
                version=identity.version,
                updated_by=identity.updated_by,
                onboarded_at=None,
            ),
            diff=proposal.diff,
        )


@router.get("/identity/history", response_model=list[CortexIdentityVersionItem])
async def get_identity_history_endpoint(
    principal: AuthPrincipal = Depends(require_system_owner),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[CortexIdentityVersionItem]:
    """El **timeline de versiones** de la identidad del owner (más reciente primero).

    Cada entrada trae su ``diff`` (``{campo:{before,after}}``, solo lo que cambió),
    quién la escribió (``updated_by``) y por qué (``reason``). Es la lectura que
    faltaba: ``GET /owner/cortex/journal`` también recorre
    ``cortex_identity_history``, pero deduplica narrativas y **descarta el diff**, así
    que la traza de qué tocó cada reflexión no era consultable desde ningún sitio y el
    timeline del panel era inconstruible (auditoría 2026-07-27).

    Un córtex sin ningún override todavía devuelve ``[]``, no 404:
    ``ensure_identity`` crea la identidad en ``version=0`` SIN fila de histórico (el
    versionado arranca en la primera reescritura real), así que "vacío" es un estado
    legítimo que la UI debe poder pintar.

    Aislamiento: ``list_history`` filtra ``owner_user_id`` explícito sobre la sesión
    BYPASSRLS (ADR 0074) y, desde el ADR 0156 + migración 0140, la tabla lleva ADEMÁS
    RLS de eje owner (``ENABLE`` + ``FORCE`` + policy ``owner_user_id = app.user_id``):
    son dos capas, no una. Este docstring decía «sin RLS de respaldo», que era cierto
    hasta el 2026-08-19 y dejó de serlo."""
    from api_server.cortex.identity import list_history

    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        versions = await list_history(session, principal.user_id, limit)
    return [
        CortexIdentityVersionItem(
            version=v.version,
            created_at=v.created_at,
            updated_by=v.updated_by,
            reason=v.reason,
            diff=dict(v.diff or {}),
        )
        for v in versions
    ]


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
# programada + mantenimiento) y consulta el budget consumido hoy vs el cap en sus DOS
# dimensiones: búsquedas (egress) y dólares (dinero). Default OFF: ningún bucle
# hace trabajo hasta que el owner lo enciende explícitamente. Copy honesto: la
# curiosidad es un comportamiento PROGRAMADO con límites de coste auditables, no
# curiosidad consciente.
async def _autonomy_snapshot(owner_id: UUID) -> CortexAutonomyResponse:
    """Estado vivo de la autonomía: settings (BD) + budget/breaker (Redis)."""
    from api_server.cortex.autonomy import (
        CURIOSITY_KIND,
        circuit_key,
        read_budget_usage,
    )
    from api_server.db.platform_settings import (
        get_cortex_autonomy_enabled,
        get_cortex_browser_enabled,
        get_cortex_curiosity_daily_searches_cap,
        get_cortex_curiosity_daily_usd_cap,
        get_cortex_curiosity_drive_threshold,
        get_cortex_web_enabled,
    )

    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        autonomy = await get_cortex_autonomy_enabled(session)
        web = await get_cortex_web_enabled(session)
        browser = await get_cortex_browser_enabled(session)
        cap = await get_cortex_curiosity_daily_searches_cap(session)
        usd_cap = await get_cortex_curiosity_daily_usd_cap(session)
        threshold = await get_cortex_curiosity_drive_threshold(session)

    now = datetime.now(UTC)
    redis = get_redis()
    searches_today = 0
    cost_usd_today = 0.0
    breaker_open = False
    try:
        # Las DOS dimensiones del budget por el MISMO lector que usa el gate
        # (`read_budget_usage`), no un `GET` a mano: si la clave del gasto cambia
        # de forma, el panel y el gate se mueven juntos en vez de divergir.
        searches_today, cost_usd_today = await read_budget_usage(
            redis, owner_user_id=str(owner_id), now=now
        )
        breaker_open = bool(await redis.exists(circuit_key(str(owner_id), CURIOSITY_KIND)))
    except Exception:  # estado vivo best-effort; la BD es la fuente de verdad
        searches_today = 0
        cost_usd_today = 0.0
        breaker_open = False

    return CortexAutonomyResponse(
        autonomy_enabled=autonomy,
        web_enabled=web,
        browser_enabled=browser,
        curiosity_drive_threshold=threshold,
        circuit_breaker_open=breaker_open,
        budget=CortexAutonomyBudget(
            searches_today=searches_today,
            searches_cap=cap,
            cost_usd_today=cost_usd_today,
            cost_usd_cap=usd_cap,
        ),
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
        CORTEX_BROWSER_ENABLED_KEY,
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
            if payload.browser_enabled is not None:
                # ADR 0080: encender el navegador NO da vía libre — solo permite
                # que el córtex PIDA sesiones, que el owner aprueba una a una.
                await set_platform_setting(
                    session, CORTEX_BROWSER_ENABLED_KEY, payload.browser_enabled, actor=actor
                )
        except PlatformSettingForbiddenError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return await _autonomy_snapshot(principal.user_id)


__all__ = ["router"]
