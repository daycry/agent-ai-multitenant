"""Córtex F4 — bucle de curiosidad autónoma (Celery, fail-open, ADR 0078).

Cuando NADIE habla y el drive ``curiosity`` baja, el córtex elige un tema entre las
entities que el owner ha mencionado, lo investiga con las **web tools existentes**
(``api_server.cortex.web.web_search`` — egress por el egress-proxy + anti-SSRF, ADR
0067), destila el resultado a una memoria ``semantic/learning`` con Ollama LOCAL,
sacia el drive y deja una fila de auditoría en ``cortex_curiosity_pursuits``.

Gobierno NO negociable (ADR 0078, parte del MVP — :mod:`api_server.cortex.autonomy`):

  1. **Kill-switch** ``cortex.autonomy_enabled`` (default OFF) ⇒ no-op total.
  2. **web_enabled** ``cortex.web_enabled`` (default OFF) ⇒ no-op (la curiosidad NO
     abre egress nuevo; respeta el gate de la web del córtex).
  3. **Circuit-breaker**: abierto ⇒ no-op; un fallo lo acerca a abrirse, un éxito lo
     resetea.
  4. **Budget cap diario** de búsquedas en Redis: agotado ⇒ pursuit ``skipped``.
  5. **Drive gate**: solo si ``curiosity < threshold`` (hambre de aprender).

Invariantes (espejo de :mod:`workers.cortex_reflection`): **fail-open** (Ollama/web
caídos ⇒ no-op, nunca rompe), **BYPASSRLS + filtro owner explícito** (ADR 0074),
**catálogo cerrado** (Ollama local para el digest; NINGÚN proveedor nuevo),
**idempotencia** por ``cortex_pursuit_id`` en la memoria.

> Honestidad: es un comportamiento PROGRAMADO con límites de coste auditables, no
> curiosidad consciente.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from shared_llm.base import LLMProvider
from shared_llm.providers import OllamaProvider
from shared_llm.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.cortex_curiosity")

# Factory del provider del digest (Ollama local), sobreescrita en tests por un fake.
LLMFactory = Callable[[Settings], LLMProvider]
# Fn de búsqueda web inyectable en tests; en prod resuelve la host tool del córtex.
#   (query, limit) -> [{"title","url","snippet"}, ...]
SearchFn = Callable[[str, int], Awaitable[list[dict[str, str]]]]

#: Cuántos resultados de búsqueda pide la curiosidad por pasada (acota coste/egress).
_SEARCH_LIMIT = 5
#: Cuántos snippets se pasan al digest (los más relevantes).
_DIGEST_MAX_SNIPPETS = 5
#: Ventana de dedup: no re-investigar el mismo tema si se persiguió en estos días.
_DEDUP_DAYS = 7

_DIGEST_SYSTEM_PROMPT = (
    "Eres el proceso de CURIOSIDAD de un córtex (modelo COMPUTACIONAL, NO "
    "consciencia). Recibes un TEMA y unos resultados de búsqueda web (título + "
    "fragmento). Destila lo aprendido en 1-3 frases claras y útiles, en PRIMERA "
    "persona, en el idioma del tema. NO inventes: cíñete a lo que dicen los "
    "fragmentos; si no aportan nada, dilo en una frase. Responde SOLO con el texto "
    "del aprendizaje, sin prosa adicional ni markdown."
)


def _default_llm_factory(settings: Settings) -> LLMProvider:
    """Provider por defecto del digest: Ollama local (ADR 0021, sin egress)."""
    return OllamaProvider(
        base_url=settings.cortex_affect_llm_base_url,
        default_model=settings.cortex_affect_llm_model,
    )


async def _default_search_fn(query: str, limit: int) -> list[dict[str, str]]:
    """Búsqueda web por la host tool del córtex (egress-proxy + anti-SSRF, ADR 0067).

    Reusa EXACTAMENTE la pila de ``api_server.cortex.web`` que el chat del córtex ya
    usa: resuelve el proveedor (searxng/brave) desde ``Settings`` y sale SOLO por el
    egress-proxy. NO abre egress nuevo. Import perezoso (solo el worker que corre la
    curiosidad paga el coste de importar ``api_server.cortex.*``)."""
    from api_server.config import get_settings as get_api_settings
    from api_server.cortex.web import select_web_search_provider, web_search

    cfg = get_api_settings()
    brave_key = cfg.brave_search_api_key.get_secret_value() if cfg.brave_search_api_key else None
    provider = select_web_search_provider(
        provider_name=cfg.cortex_web_search_provider,
        searxng_url=cfg.cortex_searxng_url,
        brave_api_key=brave_key,
        brave_url=cfg.cortex_brave_search_url,
        proxy_url=cfg.cortex_egress_proxy_url,
    )
    return await web_search(query, limit=limit, provider=provider)


@app.task(name="workers.cortex_curiosity_loop")  # type: ignore[misc]
def cortex_curiosity_loop() -> dict[str, Any]:
    """Celery entry point. Una pasada de curiosidad autónoma del córtex.

    Devuelve un dict con el rastro de la pasada (cada rama del gate observable)."""
    settings = get_settings()
    return asyncio.run(
        _run_curiosity_loop(
            settings, llm_factory=_default_llm_factory, search_fn=_default_search_fn
        )
    )


# Los múltiples `return` y el largo del cuerpo son intencionales: cada guard de
# seguridad (kill-switch, web off, drive no-bajo, sin budget, breaker abierto, sin
# tema, fail-open) es un return temprano legible; aplanarlo en uno solo lo haría peor.
async def _run_curiosity_loop(  # noqa: PLR0911
    settings: Settings,
    *,
    llm_factory: LLMFactory,
    search_fn: SearchFn,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Núcleo async, testeable con ``llm_factory`` + ``search_fn`` inyectados (sin red).

    Posee el engine lifecycle; corre BYPASSRLS (sin ``set_config app.tenant_id``);
    captura sus propias excepciones (best-effort, nunca tumba beat)."""
    now = now or datetime.now(UTC)
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    redis = _get_redis()
    try:
        from api_server.db.platform_settings import (
            get_cortex_autonomy_enabled,
            get_cortex_curiosity_cb_fails,
            get_cortex_curiosity_daily_searches_cap,
            get_cortex_curiosity_drive_threshold,
            get_cortex_web_enabled,
        )

        # (1) Kill-switch + (2) web gate.
        async with sessionmaker() as session:
            if not await get_cortex_autonomy_enabled(session):
                return {"skipped": "disabled"}
            if not await get_cortex_web_enabled(session):
                return {"skipped": "web_disabled"}
            searches_cap = await get_cortex_curiosity_daily_searches_cap(session)
            drive_threshold = await get_cortex_curiosity_drive_threshold(session)
            cb_threshold = await get_cortex_curiosity_cb_fails(session)

        # (3) Owner (singleton). Sin owner ⇒ no-op.
        owner = await _resolve_owner(sessionmaker)
        if owner is None:
            return {"skipped": "no_owner"}
        owner_id, tenant_id = owner

        # (4) Circuit-breaker.
        from api_server.cortex.autonomy import is_circuit_open

        if await is_circuit_open(redis, owner_user_id=str(owner_id)):
            return {"skipped": "circuit_open"}

        # (5) Drive gate: solo si hay hambre de curiosidad.
        drives = await _load_drives(sessionmaker, redis, owner_id, now=now)
        if drives["curiosity"] >= drive_threshold:
            return {"skipped": "drive_satisfied", "curiosity": drives["curiosity"]}

        # (6) Budget gate.
        from api_server.cortex.autonomy import check_searches_budget

        budget = await check_searches_budget(
            redis, owner_user_id=str(owner_id), cap=searches_cap, now=now
        )
        if not budget.allowed:
            await _record_pursuit_skipped(sessionmaker, owner_id, reason=budget.reason)
            return {"skipped": "budget", "reason": budget.reason}

        # (7) Selección de tema (dedup por tema reciente).
        topic, source_entities = await _select_topic(sessionmaker, owner_id, now=now)
        if topic is None:
            return {"skipped": "no_topic"}

        # (8) Fila de pursuit 'selected'.
        pursuit_id = await _insert_pursuit(
            sessionmaker, owner_id, topic=topic, source_entities=source_entities, drives=drives
        )

        # (9) Investigar (web_search → digest). Fail-open + circuit-breaker.
        try:
            digest, search_count = await _research(
                settings=settings,
                llm_factory=llm_factory,
                search_fn=search_fn,
                topic=topic,
            )
        except Exception as exc:  # fallo de la investigación
            from api_server.cortex.autonomy import record_failure

            opened = await record_failure(
                redis,
                owner_user_id=str(owner_id),
                threshold=cb_threshold,
                cooldown_s=settings.cortex_curiosity_cb_cooldown_s,
            )
            await _mark_pursuit_failed(sessionmaker, pursuit_id, reason=str(exc))
            _log.warning("cortex_curiosity.research_failed", error=str(exc), cb_opened=opened)
            return {"failed": "research_error", "pursuit_id": str(pursuit_id)}

        # Consumimos el budget de búsquedas (tras la búsqueda real).
        from api_server.cortex.autonomy import record_searches, record_success

        await record_searches(redis, owner_user_id=str(owner_id), count=search_count, now=now)

        if not digest:
            # Sin digest útil: no escribimos memoria; cuenta como skip (no fallo).
            await _mark_pursuit_skipped_by_id(sessionmaker, pursuit_id, reason="empty_digest")
            await record_success(redis, owner_user_id=str(owner_id))
            return {"skipped": "empty_digest", "pursuit_id": str(pursuit_id)}

        # (10) Memoria learning (idempotente por pursuit_id) → 'digested'.
        memory_id = await _persist_and_mark_digested(
            sessionmaker,
            owner_id=owner_id,
            tenant_id=tenant_id,
            topic=topic,
            digest=digest,
            pursuit_id=pursuit_id,
            source_entities=source_entities,
            search_count=search_count,
        )

        # (11) Saciar el drive 'curiosity' (snapshot) + éxito del breaker.
        await _satisfy_curiosity(sessionmaker, redis, owner_id, now=now)
        await record_success(redis, owner_user_id=str(owner_id))

        _log.info(
            "cortex_curiosity.digested",
            owner=str(owner_id),
            topic=topic,
            pursuit_id=str(pursuit_id),
        )
        return {
            "digested": True,
            "topic": topic,
            "pursuit_id": str(pursuit_id),
            "learning_memory_id": str(memory_id) if memory_id else None,
            "search_count": search_count,
        }
    except Exception as exc:  # best-effort: jamás propaga al worker
        _log.exception("cortex_curiosity.failed", error=str(exc))
        return {"error": str(exc)}
    finally:
        with contextlib.suppress(Exception):  # cleanup best-effort
            await redis.aclose()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Investigación — web_search (host tool, egress-proxy) + digest (Ollama local)
# ---------------------------------------------------------------------------
async def _research(
    *,
    settings: Settings,
    llm_factory: LLMFactory,
    search_fn: SearchFn,
    topic: str,
) -> tuple[str, int]:
    """``web_search(topic)`` → digest con Ollama local. Devuelve ``(digest, n_busquedas)``.

    Cualquier excepción la propaga al caller (que la trata como fallo del bucle +
    circuit-breaker). Si la búsqueda no devuelve resultados, el digest es ``""`` (el
    caller lo trata como skip, no como fallo)."""
    results = await search_fn(topic, _SEARCH_LIMIT)
    search_count = 1  # una llamada de búsqueda consumida (independiente de #resultados)
    if not results:
        return "", search_count

    snippets = "\n".join(
        f"- {r.get('title', '')}: {r.get('snippet', '')}" for r in results[:_DIGEST_MAX_SNIPPETS]
    )
    user_prompt = f"TEMA: {topic}\n\nResultados de búsqueda:\n{snippets}\n\nDestila lo aprendido."
    llm = llm_factory(settings)
    try:
        resp = await llm.complete(
            [
                Message(role="system", content=_DIGEST_SYSTEM_PROMPT),
                Message(role="user", content=user_prompt),
            ],
            max_tokens=384,
            temperature=0.2,
        )
    finally:
        await llm.aclose()
    return (resp.content or "").strip(), search_count


# ---------------------------------------------------------------------------
# DB helpers (todo filtra owner_user_id explícito; BYPASSRLS sin RLS, ADR 0074)
# ---------------------------------------------------------------------------
async def _resolve_owner(
    sessionmaker: async_sessionmaker[Any],
) -> tuple[UUID, UUID] | None:
    """El owner (singleton ``is_system_owner``) + su ``tenant_id`` (discriminante D1).

    El ``tenant_id`` sale del hilo del córtex más reciente del owner (la memoria
    ``learning`` lo necesita físicamente). Sin hilo ⇒ usamos la membership más
    antigua del owner. Sin tenant resoluble ⇒ ``None`` (no podemos persistir)."""
    from api_server.db.cortex import CortexConversation
    from api_server.db.models import User, UserOrganizationMembership

    async with sessionmaker() as session:
        owner_id = (
            await session.execute(
                select(User.id).where(User.is_system_owner.is_(True), User.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if owner_id is None:
            return None
        tenant_id = (
            await session.execute(
                select(CortexConversation.tenant_id)
                .where(CortexConversation.owner_user_id == owner_id)
                .order_by(CortexConversation.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            tenant_id = (
                await session.execute(
                    select(UserOrganizationMembership.tenant_id)
                    .where(UserOrganizationMembership.user_id == owner_id)
                    .order_by(UserOrganizationMembership.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if tenant_id is None:
            return None
        return owner_id, tenant_id


async def _load_drives(
    sessionmaker: async_sessionmaker[Any], redis: Any, owner_id: UUID, *, now: datetime
) -> dict[str, float]:
    """Los drives del owner (caché Redis con decay lazy → BD). Filtro owner explícito."""
    from api_server.cortex.affect_cache import read_affect_state
    from api_server.cortex.affect_store import load_affect_state

    state = await read_affect_state(redis, str(owner_id), now=now)
    if state is None:
        async with sessionmaker() as session:
            state = await load_affect_state(session, owner_id, now=now)
    return state.drives.as_dict()


async def _select_topic(
    sessionmaker: async_sessionmaker[Any], owner_id: UUID, *, now: datetime
) -> tuple[str | None, list[str]]:
    """Elige un tema NO investigado recientemente, sesgado a learning_goals (F3)."""
    from datetime import timedelta

    from api_server.cortex.curiosity import gather_owner_entities, pick_topic
    from api_server.cortex.identity import get_identity
    from api_server.db.cortex_curiosity import CortexCuriosityPursuit

    async with sessionmaker() as session:
        entity_freqs = await gather_owner_entities(session, owner_user_id=owner_id)
        if not entity_freqs:
            return None, []
        # Temas ya APRENDIDOS recientemente (dedup), filtro owner explícito. Solo
        # 'digested' cuenta: un tema cuya búsqueda falló o se saltó (failed/skipped)
        # sigue siendo elegible — la repetición de fallos la frena el circuit-breaker,
        # no el dedup (no queremos enterrar un tema válido por un fallo transitorio).
        cutoff = now - timedelta(days=_DEDUP_DAYS)
        recent_rows = (
            (
                await session.execute(
                    select(CortexCuriosityPursuit.topic).where(
                        CortexCuriosityPursuit.owner_user_id == owner_id,
                        CortexCuriosityPursuit.created_at >= cutoff,
                        CortexCuriosityPursuit.status == "digested",
                    )
                )
            )
            .scalars()
            .all()
        )
        recently_pursued = {str(t) for t in recent_rows}
        identity = await get_identity(session, owner_id)
        learning_goals: list[str] = []
        if identity is not None:
            learning_goals = [
                str(g) for g in (identity.identity_state or {}).get("learning_goals", [])
            ]

    topic = pick_topic(
        entity_freqs, recently_pursued=recently_pursued, learning_goals=learning_goals
    )
    if topic is None:
        return None, []
    return topic, [e for e, _ in entity_freqs[:10]]


async def _insert_pursuit(
    sessionmaker: async_sessionmaker[Any],
    owner_id: UUID,
    *,
    topic: str,
    source_entities: list[str],
    drives: dict[str, float],
) -> UUID:
    """Inserta la fila de auditoría ``status='selected'`` y devuelve su id."""
    from api_server.db.cortex_curiosity import CortexCuriosityPursuit

    pursuit_id = uuid4()
    async with sessionmaker() as session, session.begin():
        session.add(
            CortexCuriosityPursuit(
                id=pursuit_id,
                owner_user_id=owner_id,
                topic=topic,
                source_entities=source_entities,
                status="selected",
                drive_snapshot=drives,
            )
        )
    return pursuit_id


async def _record_pursuit_skipped(
    sessionmaker: async_sessionmaker[Any], owner_id: UUID, *, reason: str
) -> None:
    """Inserta una fila ``status='skipped'`` (budget agotado, etc.) — auditoría."""
    from api_server.db.cortex_curiosity import CortexCuriosityPursuit

    async with sessionmaker() as session, session.begin():
        session.add(
            CortexCuriosityPursuit(
                id=uuid4(),
                owner_user_id=owner_id,
                topic="(skipped)",
                status="skipped",
                metadata_={"reason": reason},
            )
        )


async def _mark_pursuit_skipped_by_id(
    sessionmaker: async_sessionmaker[Any], pursuit_id: UUID, *, reason: str
) -> None:
    from api_server.db.cortex_curiosity import CortexCuriosityPursuit

    async with sessionmaker() as session, session.begin():
        row = await session.get(CortexCuriosityPursuit, pursuit_id)
        if row is not None:
            row.status = "skipped"
            row.metadata_ = {**(row.metadata_ or {}), "reason": reason}


async def _mark_pursuit_failed(
    sessionmaker: async_sessionmaker[Any], pursuit_id: UUID, *, reason: str
) -> None:
    from api_server.db.cortex_curiosity import CortexCuriosityPursuit

    async with sessionmaker() as session, session.begin():
        row = await session.get(CortexCuriosityPursuit, pursuit_id)
        if row is not None:
            row.status = "failed"
            row.metadata_ = {**(row.metadata_ or {}), "reason": reason[:500]}


async def _persist_and_mark_digested(
    sessionmaker: async_sessionmaker[Any],
    *,
    owner_id: UUID,
    tenant_id: UUID,
    topic: str,
    digest: str,
    pursuit_id: UUID,
    source_entities: list[str],
    search_count: int,
) -> UUID | None:
    """Escribe la memoria ``learning`` (idempotente) y marca el pursuit ``digested``."""
    from api_server.cortex.curiosity import persist_learning_memory
    from api_server.db.cortex_curiosity import CortexCuriosityPursuit

    async with sessionmaker() as session, session.begin():
        memory_id = await persist_learning_memory(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            topic=topic,
            digest=digest,
            pursuit_id=pursuit_id,
            entities=tuple(source_entities),
        )
        row = await session.get(CortexCuriosityPursuit, pursuit_id)
        if row is not None:
            row.status = "digested"
            row.learning_memory_id = memory_id
            row.search_count = search_count
    return memory_id


async def _satisfy_curiosity(
    sessionmaker: async_sessionmaker[Any], redis: Any, owner_id: UUID, *, now: datetime
) -> None:
    """Sacia el drive ``curiosity`` (motor PAD de F2) y escribe un snapshot + caché.

    Reusa ``satisfy_drive`` (determinista) sobre el estado afectivo actual del owner
    (con decay lazy), persiste un snapshot de mantenimiento (sin ``source_turn_id``) y
    refresca la caché Redis viva. Best-effort: un fallo aquí no debe tumbar el bucle
    (la memoria learning ya se escribió)."""
    from api_server.cortex.affect_cache import write_affect_state
    from api_server.cortex.affect_store import load_affect_state, save_affect_snapshot
    from api_server.cortex.affective import AffectState, satisfy_drive
    from api_server.cortex.identity import effective_mood_baseline, get_identity

    #: Cuánto sube el drive curiosity al saciarlo tras una pasada exitosa.
    delta = 0.3
    try:
        async with sessionmaker() as session, session.begin():
            identity = await get_identity(session, owner_id)
            baseline = effective_mood_baseline(identity.identity_state if identity else None)
            state = await load_affect_state(session, owner_id, now=now, baseline=baseline)
            new_drives = satisfy_drive(state.drives, "curiosity", delta)
            new_state = AffectState(emotion=state.emotion, mood=state.mood, drives=new_drives)
            await save_affect_snapshot(
                session,
                owner_user_id=owner_id,
                state=new_state,
                appraisal_reason=None,
                source_turn_id=None,
                language="es",
            )
        await write_affect_state(redis, str(owner_id), new_state, now=now, baseline=baseline)
    except Exception as exc:  # best-effort
        _log.warning("cortex_curiosity.satisfy_failed", owner=str(owner_id), error=str(exc))


def _get_redis() -> Any:
    """Cliente Redis del api-server (mismo DB que la caché afectiva viva)."""
    from api_server.auth.deps import get_redis

    return get_redis()


# ---------------------------------------------------------------------------
# Trigger (lo agenda F4 con el beat; o un disparo manual)
# ---------------------------------------------------------------------------
def trigger_cortex_curiosity() -> bool:
    """Encola una pasada de curiosidad (cola ``default``). Best-effort."""
    try:
        cortex_curiosity_loop.apply_async(queue="default")
    except Exception as exc:
        _log.warning("cortex_curiosity.enqueue_failed", error=str(exc))
        return False
    return True


__all__ = ["cortex_curiosity_loop", "trigger_cortex_curiosity"]
