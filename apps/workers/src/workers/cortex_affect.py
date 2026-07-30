"""Córtex F2 — distilador afectivo asíncrono (Celery, Ollama local, fail-open).

ADR 0075, decisión 3 (appraisal ASÍNCRONO): el turno del córtex responde primero;
*después* esta tarea Celery puntúa el turno contra los drives/identidad y emite un
``delta PAD + razón``; el **motor determinista** (``api_server.cortex.affective``)
lo aplica, escribe un snapshot a ``cortex_affect_snapshots``, refresca la caché
viva en Redis (``cortex:affect:{owner}``), publica un frame de telemetría en
``cortex:telemetry:{owner}`` (lo tailea el WS del Panel de Mente) y deja una
memoria episódica emocional en ``memory_entries`` (ADR 0077).

Tres invariantes (espejo de :mod:`workers.memorizer`):

  * **Fail-open** (ADR 0075 §3): Ollama caído / timeout / JSON inválido ⇒
    ``delta=0`` + ``appraisal_reason=None``; el snapshot se escribe igualmente con
    el estado decaído y la tarea devuelve ``ok:fail_open``. El turno YA respondió,
    así que el appraisal NUNCA puede romper nada. El ``try/except`` global hace que
    la tarea jamás propague una excepción al worker.
  * **Idempotente** por ``source_turn_id``: ``task_acks_late`` es global, así que
    una re-entrega re-corre la tarea; el UNIQUE parcial
    ``uq_cortex_affect_snapshot_per_turn`` la rechaza y devolvemos
    ``ok:already_distilled`` sin distilar de nuevo (sin segunda llamada al LLM).
  * **Sin egress / catálogo cerrado** (ADR 0021): usa Ollama LOCAL (ya en el
    catálogo); cero proveedor nuevo. Modelo y URL operator-tunables (default local).

> Honestidad (ADR 0075 §6): el delta PAD y la razón son una **simulación
> computacional de afecto**, NO sentimientos reales.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from api_server.cortex.affect_store import save_affect_snapshot
from api_server.cortex.affective import (
    AffectState,
    PADState,
    apply_event,
    satisfy_drive,
    update_mood,
)
from api_server.db.cortex import CortexConversation, CortexTurn
from api_server.memorizer import MemoryCandidate, persist_memory_candidates
from shared_llm.base import LLMProvider
from shared_llm.providers import OllamaProvider
from shared_llm.types import Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

_log = structlog.get_logger("workers.cortex_affect")

# Factory del provider que el distilador llama, sobreescrita en tests por un fake.
LLMFactory = Callable[[Settings], LLMProvider]

#: Drives saciables que el distilador puede señalar (resto = no-op en el motor).
_DRIVE_NAMES = ("curiosity", "bonding", "coherence", "competence")

#: System prompt del distilador. Pide SÓLO el JSON del delta+razón (sin prosa).
#: El delta es PEQUEÑO por turno: la dinámica lenta vive en el motor (EWMA del
#: mood), no en el appraisal. Bilingüe (responde en el idioma del owner).
_DISTILL_SYSTEM_PROMPT = (
    "Eres un evaluador afectivo (appraisal) de un modelo COMPUTACIONAL de afecto "
    "(modelo PAD: valence/arousal/dominance), NO sentimientos reales. Lees un turno "
    "de conversación (mensaje del owner + respuesta del córtex) y su estado afectivo "
    "actual, y emites cómo ese turno DESPLAZA el estado. Responde EXCLUSIVAMENTE con "
    "un objeto JSON, sin texto alrededor, con esta forma:\n"
    '{"delta": {"valence": <float -1..1>, "arousal": <float -1..1>, '
    '"dominance": <float -1..1>, "intensity": <float 0..1>}, '
    '"reason": "<una frase breve, en el idioma del turno, de POR QUÉ>", '
    '"drive_satisfied": "curiosity|bonding|coherence|competence|null", '
    '"drive_amount": <float 0..1>}\n'
    "Los deltas son PEQUEÑOS (típicamente |x| <= 0.4): un elogio sube valence; una "
    "crítica la baja; una pregunta interesante sacia 'curiosity'; una despedida fría "
    "baja 'bonding'. Si el turno es neutro, devuelve deltas 0 y drive_satisfied null. "
    "No inventes sentimientos: describes un desplazamiento del modelo."
)


def _default_llm_factory(settings: Settings) -> LLMProvider:
    """Provider por defecto del distilador: Ollama local (ADR 0021, sin egress)."""
    return OllamaProvider(
        base_url=settings.cortex_affect_llm_base_url,
        default_model=settings.cortex_affect_llm_model,
    )


@app.task(name="workers.cortex_distill_affect")  # type: ignore[untyped-decorator]
def cortex_distill_affect(turn_id: str) -> dict[str, Any]:
    """Celery entry point. Distila el afecto de un turno del córtex.

    Devuelve un dict para que el result backend deje un rastro útil:

      {"turn_id": ..., "reason": "ok"|"ok:fail_open"|"ok:already_distilled"|...}
    """
    settings = get_settings()
    return asyncio.run(
        _distill_affect_async(
            UUID(turn_id),
            settings=settings,
            llm_factory=_default_llm_factory,
        )
    )


async def _distill_affect_async(
    turn_id: UUID,
    *,
    settings: Settings,
    llm_factory: LLMFactory,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Núcleo async, testeable con un ``llm_factory`` inyectado (sin red).

    El reloj entra como ``now`` (default: ahora) para hacer el decay determinista
    en los tests."""
    now = now or datetime.now(UTC)
    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            ctx = await _load_turn_context(session, turn_id)
        if ctx is None:
            return _result(turn_id, "skipped:turn_not_found")

        owner_id: UUID = ctx["owner_user_id"]

        # Baseline EVOLUTIVO de la identidad (set-point del decay): se carga UNA
        # vez y viaja tanto al decay del prior como embebido en la caché viva.
        baseline = await _load_identity_baseline(sessionmaker, owner_id)

        # Estado afectivo de partida: el último snapshot del owner con decay lazy
        # aplicado hasta `now` (sin snapshot ⇒ baseline neutro). NO usamos la caché
        # Redis aquí: la BD es la fuente de verdad de la serie temporal.
        prior = await _load_prior_state(sessionmaker, owner_id, now=now, baseline=baseline)

        # --- Appraisal (fail-open) -------------------------------------------------
        delta, appraisal_reason, drive_name, drive_amount = await _appraise(
            settings=settings,
            llm_factory=llm_factory,
            user_text=ctx["user_text"],
            cortex_text=ctx["cortex_text"],
            prior=prior,
        )

        # --- Motor determinista ----------------------------------------------------
        new_emotion = apply_event(prior.emotion, delta)
        new_mood = update_mood(prior.mood, new_emotion)
        new_drives = prior.drives
        if drive_name in _DRIVE_NAMES and drive_amount > 0.0:
            new_drives = satisfy_drive(new_drives, drive_name, drive_amount)
        new_state = AffectState(emotion=new_emotion, mood=new_mood, drives=new_drives)

        # --- Snapshot (idempotente por source_turn_id) -----------------------------
        try:
            async with sessionmaker() as session, session.begin():
                await save_affect_snapshot(
                    session,
                    owner_user_id=owner_id,
                    state=new_state,
                    appraisal_reason=appraisal_reason,
                    source_turn_id=turn_id,
                    language="es",
                )
        except IntegrityError:
            # UNIQUE parcial por turno → ya se distiló este turno (re-entrega).
            _log.info("cortex_affect.already_distilled", turn_id=str(turn_id))
            return _result(turn_id, "ok:already_distilled")

        mood_label = new_state.mood_label(language="es")

        # --- Caché viva + telemetría (best-effort, nunca rompen) -------------------
        await _refresh_live_state(owner_id, new_state, now=now, baseline=baseline)
        await _publish_frame(
            owner_id,
            state=new_state,
            mood_label=mood_label,
            appraisal_reason=appraisal_reason,
            now=now,
        )

        # --- Episódica emocional en memory_entries (ADR 0077; directo, NO memorizer)
        await _persist_emotional_episode(
            sessionmaker,
            owner_id=owner_id,
            tenant_id=ctx["tenant_id"],
            user_text=ctx["user_text"],
            cortex_text=ctx["cortex_text"],
            state=new_state,
            mood_label=mood_label,
            appraisal_reason=appraisal_reason,
        )

        reason = "ok:fail_open" if appraisal_reason is None else "ok"
        _log.info(
            "cortex_affect.distilled",
            turn_id=str(turn_id),
            mood_label=mood_label,
            reason=reason,
        )
        return _result(turn_id, reason)
    except Exception as exc:
        # Belt + braces: un fallo del distilador JAMÁS debe propagar al worker
        # (el turno ya respondió; el afecto es un nice-to-have).
        _log.exception("cortex_affect.failed", turn_id=str(turn_id), error=str(exc))
        return _result(turn_id, f"error:{exc}")
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Appraisal — la única parte que toca el LLM (fail-open)
# ---------------------------------------------------------------------------
async def _appraise(
    *,
    settings: Settings,
    llm_factory: LLMFactory,
    user_text: str,
    cortex_text: str,
    prior: AffectState,
) -> tuple[PADState, str | None, str | None, float]:
    """Llama al LLM y parsea el JSON → ``(delta, reason, drive_name, drive_amount)``.

    **Fail-open**: cualquier excepción (Ollama caído/timeout) o JSON inválido ⇒
    ``(delta cero, None, None, 0.0)`` — el motor deja el estado intacto (sólo
    decae con el tiempo) y el snapshot nace con ``appraisal_reason=None``."""
    zero = PADState(valence=0.0, arousal=0.0, dominance=0.0, intensity=0.0)
    llm = llm_factory(settings)
    try:
        user_prompt = _build_user_prompt(user_text, cortex_text, prior)
        resp = await llm.complete(
            [
                Message(role="system", content=_DISTILL_SYSTEM_PROMPT),
                Message(role="user", content=user_prompt),
            ],
            max_tokens=256,
            temperature=0.0,
        )
    except Exception as exc:
        # Fail-open: Ollama caído/timeout ⇒ delta 0, razón None.
        _log.warning("cortex_affect.appraisal_failed_open", error=str(exc))
        return zero, None, None, 0.0
    finally:
        await llm.aclose()

    parsed = _parse_delta(resp.content)
    if parsed is None:
        _log.warning("cortex_affect.appraisal_unparseable")
        return zero, None, None, 0.0
    return parsed


def _build_user_prompt(user_text: str, cortex_text: str, prior: AffectState) -> str:
    """El turno + el estado actual, en un bloque que el LLM puntúa."""
    e = prior.emotion
    return (
        "Estado afectivo actual (modelo PAD):\n"
        f"  valence={e.valence:.2f} arousal={e.arousal:.2f} dominance={e.dominance:.2f}\n"
        f"  drives={json.dumps(prior.drives.as_dict())}\n\n"
        "Turno a evaluar:\n"
        f"  Owner: {user_text}\n"
        f"  Córtex: {cortex_text}\n\n"
        "Devuelve SÓLO el JSON del delta + razón + drive saciado."
    )


def _parse_delta(content: str) -> tuple[PADState, str | None, str | None, float] | None:
    """Parsea el JSON del distilador. ``None`` si no es JSON válido / falta delta.

    Tolerante: extrae el primer objeto ``{...}`` del texto (algunos modelos
    locales envuelven el JSON en prosa pese a la instrucción)."""
    raw = _extract_json_object(content)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        delta_obj = data["delta"]
        delta = PADState(
            valence=float(delta_obj.get("valence", 0.0)),
            arousal=float(delta_obj.get("arousal", 0.0)),
            dominance=float(delta_obj.get("dominance", 0.0)),
            intensity=float(delta_obj.get("intensity", 0.0)),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    reason = data.get("reason")
    reason_str = str(reason).strip() if reason not in (None, "") else None
    drive = data.get("drive_satisfied")
    drive_name = str(drive) if drive in _DRIVE_NAMES else None
    try:
        drive_amount = float(data.get("drive_amount", 0.0))
    except (TypeError, ValueError):
        drive_amount = 0.0
    return delta, reason_str, drive_name, max(0.0, drive_amount)


def _extract_json_object(content: str) -> str | None:
    """El primer objeto JSON balanceado ``{...}`` del texto, o ``None``."""
    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(content)):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    return None


# ---------------------------------------------------------------------------
# DB / Redis helpers
# ---------------------------------------------------------------------------
async def _load_turn_context(session: AsyncSession, turn_id: UUID) -> dict[str, Any] | None:
    """Carga el turno del córtex + su turno user previo + el tenant del hilo.

    Filtra por ``id`` (el back-link viene del propio turno); devuelve ``None`` si
    el turno se borró entre el trigger y el pickup. Aislamiento: leemos el
    ``owner_user_id`` del propio turno y todo lo demás se filtra por él."""
    turn = await session.get(CortexTurn, turn_id)
    if turn is None:
        return None
    owner_id = turn.owner_user_id
    conv = await session.get(CortexConversation, turn.conversation_id)
    tenant_id = conv.tenant_id if conv is not None else None
    if tenant_id is None:
        return None

    # El turno 'user' inmediatamente anterior en el MISMO hilo (filtro owner
    # explícito; tablas tenant-less sin RLS, ADR 0074).
    user_text = ""
    if turn.role == "cortex":
        stmt = (
            select(CortexTurn.content)
            .where(
                CortexTurn.conversation_id == turn.conversation_id,
                CortexTurn.owner_user_id == owner_id,
                CortexTurn.role == "user",
                CortexTurn.created_at <= turn.created_at,
            )
            .order_by(CortexTurn.created_at.desc(), CortexTurn.id.desc())
            .limit(1)
        )
        prev = (await session.execute(stmt)).scalar_one_or_none()
        user_text = prev or ""

    return {
        "owner_user_id": owner_id,
        "tenant_id": tenant_id,
        "user_text": user_text,
        "cortex_text": turn.content if turn.role == "cortex" else "",
    }


async def _load_identity_baseline(
    sessionmaker: async_sessionmaker[AsyncSession], owner_id: UUID
) -> PADState:
    """El baseline evolutivo de la identidad del owner (fail-open a BASELINE_PAD).

    Un fallo aquí (tabla ausente, sesión rota…) degrada al neutro del motor:
    el baseline es un matiz del decay, nunca un bloqueo del distilador."""
    from api_server.cortex.affective import BASELINE_PAD

    try:
        from api_server.cortex.identity import effective_mood_baseline, get_identity

        async with sessionmaker() as session:
            identity = await get_identity(session, owner_id)
        return effective_mood_baseline(identity.identity_state if identity else None)
    except Exception as exc:  # fail-open
        _log.warning(
            "cortex_affect.baseline_load_failed", owner_user_id=str(owner_id), error=str(exc)
        )
        return BASELINE_PAD


async def _load_prior_state(
    sessionmaker: async_sessionmaker[AsyncSession],
    owner_id: UUID,
    *,
    now: datetime,
    baseline: PADState | None = None,
) -> AffectState:
    """El estado afectivo de partida: último snapshot del owner con decay lazy.

    Reusa el store (filtro ``owner_user_id`` explícito + decay determinista
    hacia el baseline evolutivo, que el caller ya cargó)."""
    from api_server.cortex.affect_store import load_affect_state

    async with sessionmaker() as session:
        return await load_affect_state(session, owner_id, now=now, baseline=baseline)


async def _refresh_live_state(
    owner_id: UUID, state: AffectState, *, now: datetime, baseline: PADState | None = None
) -> None:
    """Refresca la caché Redis viva ``cortex:affect:{owner}`` (best-effort).

    El ``baseline`` evolutivo viaja embebido para que las lecturas de la caché
    decaigan hacia el temperamento del córtex sin tocar la BD."""
    from api_server.cortex.affect_cache import write_affect_state

    redis = _get_redis()
    try:
        await write_affect_state(redis, str(owner_id), state, now=now, baseline=baseline)
    finally:
        await redis.aclose()


async def _publish_frame(
    owner_id: UUID,
    *,
    state: AffectState,
    mood_label: str,
    appraisal_reason: str | None,
    now: datetime,
) -> None:
    """Publica el frame de telemetría en ``cortex:telemetry:{owner}`` (best-effort)."""
    from api_server.events import publish_cortex_affect_event

    redis = _get_redis()
    try:
        await publish_cortex_affect_event(
            redis,
            str(owner_id),
            payload={
                "valence": state.emotion.valence,
                "arousal": state.emotion.arousal,
                "dominance": state.emotion.dominance,
                "intensity": state.emotion.intensity,
                "mood_valence": state.mood.valence,
                "mood_arousal": state.mood.arousal,
                "mood_dominance": state.mood.dominance,
                "mood_label": mood_label,
                "drives": state.drives.as_dict(),
                "appraisal_reason": appraisal_reason,
                "occurred_at": now.astimezone(UTC).isoformat(),
            },
        )
    finally:
        await redis.aclose()


async def _persist_emotional_episode(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    owner_id: UUID,
    tenant_id: UUID,
    user_text: str,
    cortex_text: str,
    state: AffectState,
    mood_label: str,
    appraisal_reason: str | None,
) -> None:
    """Escribe la episódica emocional del owner en ``memory_entries`` (ADR 0077).

    DIRECTO vía :func:`persist_memory_candidates` (NO vía ``workers.memorizer``):
    scope=private, ``user_id=owner``, ``metadata_.cortex=true`` y
    ``metadata_.emotion={valence,arousal,dominance,intensity,mood_label,appraisal_reason}``,
    que es lo que ``GET /owner/cortex/episodes`` lee. Best-effort: un fallo aquí no
    debe tumbar el snapshot ya escrito (la memoria es un nice-to-have)."""
    # El contenido recoge AMBOS lados del turno + la razón del appraisal (lo que el
    # mapa de episodios muestra). La razón va además en metadata_.emotion para el hover.
    context = f"Owner: {user_text[:160]} / Córtex: {cortex_text[:160]}"
    summary = (
        f"{appraisal_reason} ({context})"
        if appraisal_reason
        else f"Turno del córtex (mood={mood_label}). {context}"
    )
    candidate = MemoryCandidate(
        content=summary,
        type="episodic",
        tags=("cortex", "affect", mood_label),
    )
    emotion_meta = {
        "valence": state.emotion.valence,
        "arousal": state.emotion.arousal,
        "dominance": state.emotion.dominance,
        "intensity": state.emotion.intensity,
        "mood_label": mood_label,
        "appraisal_reason": appraisal_reason,
    }
    try:
        async with sessionmaker() as session, session.begin():
            await persist_memory_candidates(
                session,
                [candidate],
                tenant_id=tenant_id,
                scope="private",
                user_id=owner_id,
                extra_metadata={"cortex": True, "emotion": emotion_meta},
            )
    except Exception as exc:  # episódica best-effort, nunca rompe el distilador
        _log.warning(
            "cortex_affect.episode_persist_failed", owner_user_id=str(owner_id), error=str(exc)
        )


def _get_redis() -> Any:
    """Cliente Redis del bus de eventos del WORKER (la misma DB 0 que el WS del
    api-server tailea — invariante H10/AUD16).

    Bug cazado en vivo (2026-07-18): esto usaba `api_server.auth.deps.get_redis`,
    cuya env (API_SERVER_REDIS_URL) no existe en el contenedor del worker → caía
    al default localhost:6379 y el caché vivo de afecto + la telemetría WS
    fallaban SIEMPRE (best-effort, así que en silencio salvo el WARNING)."""
    from redis.asyncio import Redis

    from workers.config import get_settings

    return Redis.from_url(get_settings().events_redis_url)


def _result(turn_id: UUID, reason: str) -> dict[str, Any]:
    return {"turn_id": str(turn_id), "reason": reason}


# ---------------------------------------------------------------------------
# Trigger post-turno (cableado en routers/cortex.py)
# ---------------------------------------------------------------------------
def trigger_cortex_distill_affect(turn_id: UUID) -> bool:
    """Encola el distilador afectivo para un turno del córtex (cola ``default``).

    Llamado por el endpoint del turno (``POST /owner/cortex/turns``) JUSTO tras
    persistir el turno cortex, fire-and-forget fuera del hot-path. Devuelve True
    si se encoló. Un fallo del broker se traga y loguea (devuelve False): el
    appraisal NUNCA puede romper el turno (espejo de ``trigger_memorize``)."""
    try:
        cortex_distill_affect.apply_async(args=[str(turn_id)], queue="default")
    except Exception as exc:
        _log.warning("cortex_affect.enqueue_failed", turn_id=str(turn_id), error=str(exc))
        return False
    return True


__all__ = [
    "cortex_distill_affect",
    "trigger_cortex_distill_affect",
]
