"""Córtex F4 — bucle de curiosidad autónoma (Celery, fail-open, ADR 0078).

Cuando NADIE habla y el drive ``curiosity`` baja, el córtex elige un tema entre las
entities que el owner ha mencionado, lo investiga con las **web tools existentes**
(``api_server.cortex.web.web_search`` — egress por el egress-proxy + anti-SSRF, ADR
0067), destila el resultado a una memoria ``semantic/learning`` con Ollama LOCAL,
sacia el drive y deja una fila de auditoría en ``cortex_curiosity_pursuits``.

Gobierno NO negociable (ADR 0078, parte del MVP — :mod:`api_server.cortex.autonomy`):

  1. **Kill-switch** ``cortex.autonomy_enabled`` (default OFF) ⇒ no-op total.
  2. **Enable propio** ``cortex.curiosity_enabled`` (default OFF) ⇒ no-op. Separado
     del kill-switch porque la curiosidad es el único bucle que sale a Internet y
     gasta: se puede dejar la reflexión y el mantenimiento (locales) corriendo con la
     curiosidad apagada.
  3. **web_enabled** ``cortex.web_enabled`` (default OFF) ⇒ no-op (la curiosidad NO
     abre egress nuevo; respeta el gate de la web del córtex).
  4. **Circuit-breaker**: abierto ⇒ no-op; un fallo lo acerca a abrirse, un éxito lo
     resetea.
  5. **Budget cap diario** en DOS dimensiones (búsquedas y dólares): agotada
     cualquiera ⇒ pursuit ``skipped`` con el motivo.
  6. **Drive gate**: solo si ``curiosity < threshold`` (hambre de aprender).
  7. **Owner-approval gate** ``cortex.curiosity_approval_gate`` (default ON): el
     córtex elige el tema, deja el pursuit ``selected`` con ``approved IS NULL`` y
     **NO busca** hasta que el owner lo aprueba. Aprobado, la pasada siguiente lo
     RETOMA (no elige otro: aprobar A y buscar B sería un fraude del gate).

Dos caminos de investigación, no uno (ADR 0076, cerrado ``accepted`` con la
divergencia deliberada 3→4):

  * **Con ``claude_sdk``** (punto 3, el recomendado): las WebSearch/WebFetch
    **nativas** del SDK vía :mod:`api_server.cortex.researcher` — anti-SSRF gratis y
    coste real reportado por el SDK.
  * **Sin SDK** (punto 4, el camino de este stack): la tool web propia del córtex
    con anti-SSRF obligatorio + digest con Ollama local. El caso «sin SDK ⇒ skipped»
    que pedía el plan NO se implementa a propósito: saltarse la pasada dejaría la
    curiosidad muerta en el único despliegue que existe (el owner usa Ollama).

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
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import structlog
from shared_llm.base import LLMProvider
from shared_llm.providers import OllamaProvider
from shared_llm.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

if TYPE_CHECKING:  # solo para tipar; el import real es perezoso (patrón del módulo)
    from api_server.cortex.researcher import ResearchResult

_log = structlog.get_logger("workers.cortex_curiosity")

# Factory del provider del digest (Ollama local), sobreescrita en tests por un fake.
LLMFactory = Callable[[Settings], LLMProvider]
# Fn de búsqueda web inyectable en tests; en prod resuelve la host tool del córtex.
#   (query, limit) -> [{"title","url","snippet"}, ...]
SearchFn = Callable[[str, int], Awaitable[list[dict[str, str]]]]
# Investigador agéntico (claude_sdk) inyectable en tests: (topic) -> ResearchResult.
# Un resultado con ``skipped=True`` significa "este despliegue no tiene SDK" y el
# bucle cae al camino de la tool web propia (ADR 0076, divergencia 3→4).
SdkResearcherFn = Callable[[str], Awaitable["ResearchResult"]]

#: Cuántos resultados de búsqueda pide la curiosidad por pasada (acota coste/egress).
_SEARCH_LIMIT = 5
#: Cuántos snippets se pasan al digest (los más relevantes).
_DIGEST_MAX_SNIPPETS = 5
#: Ventana de dedup: no re-investigar el mismo tema si se persiguió en estos días.
_DEDUP_DAYS = 7
#: Cuántos días vive una propuesta pendiente antes de considerarse rancia. Pasado ese
#: plazo el bucle propone otra cosa: si el owner no contestó en una semana, el tema ya
#: no es "lo último que le interesaba" y no debe secuestrar el bucle para siempre.
_PENDING_MAX_AGE_DAYS = 7

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


async def _default_sdk_researcher(topic: str) -> ResearchResult:
    """Investiga con las web tools NATIVAS del SDK si el córtex usa ``claude_sdk``.

    Camino recomendado del ADR 0076 (punto 3): resuelve el modelo del córtex
    (``cortex.default_model``, F1), y si su kind es ``claude_sdk`` construye el
    provider con su credencial de Vault y delega en
    :func:`api_server.cortex.researcher.research_topic`.

    Devuelve ``ResearchResult(skipped=True, reason=...)`` —no levanta— cuando este
    despliegue no puede tomar ese camino: sin modelo configurado, con otro kind, sin
    el SDK instalado (extra ``claude``, ADR 0064) o sin poder construir el provider.
    Esa es la señal para que el caller use la tool web propia (punto 4). Un fallo
    DENTRO del run agéntico sí se propaga: es una avería y la trata el
    circuit-breaker."""
    from api_server.cortex.researcher import ResearchResult, research_topic

    try:
        from api_server.assistant.model_config import to_provider_model_name
        from api_server.cortex.model_config import resolve_cortex_model
        from api_server.db.session import get_admin_sessionmaker
        from api_server.llm_providers.factory import build_llm_provider

        from workers.execution import _default_vault_store

        sessionmaker = get_admin_sessionmaker()
        async with sessionmaker() as session:
            resolved = await resolve_cortex_model(session)
            if resolved is None:
                return ResearchResult(skipped=True, reason="no_model")
            if resolved.provider_kind != "claude_sdk":
                # El camino del ADR 0076 punto 4 (tool web propia). No es un fallo.
                return ResearchResult(skipped=True, reason="no_sdk")
            api_model = to_provider_model_name(resolved.provider_kind, resolved.model_id)
            provider = await build_llm_provider(
                session,
                provider_id=resolved.provider_id,
                model=api_model,
                vault=_default_vault_store(),
            )
    except Exception as exc:  # resolución/credencial/SDK ausente ⇒ degradar, no fallar
        _log.info("cortex_curiosity.sdk_unavailable", error=str(exc))
        return ResearchResult(skipped=True, reason="no_sdk")
    if provider is None:
        return ResearchResult(skipped=True, reason="no_sdk")
    try:
        return await research_topic(
            provider,
            topic=topic,
            model=api_model,
            effort=resolved.reasoning_effort or "high",
        )
    finally:
        with contextlib.suppress(Exception):
            await provider.aclose()


@app.task(name="workers.cortex_curiosity_loop")  # type: ignore[untyped-decorator]
def cortex_curiosity_loop() -> dict[str, Any]:
    """Celery entry point. Una pasada de curiosidad autónoma del córtex.

    Devuelve un dict con el rastro de la pasada (cada rama del gate observable) y
    publica las métricas de esa pasada (ADR 0078, Sub-fase 4.6). La emisión va aquí
    —no dentro del core async— porque el core es el que los tests inyectan, y una
    métrica es contabilidad del PROCESO, no del algoritmo."""
    from workers.cortex_curiosity_metrics import publish_pass_metrics

    settings = get_settings()
    result = asyncio.run(
        _run_curiosity_loop(
            settings,
            llm_factory=_default_llm_factory,
            search_fn=_default_search_fn,
            sdk_researcher=_default_sdk_researcher,
        )
    )
    publish_pass_metrics(result)  # best-effort: nunca rompe la pasada
    return result


# Los múltiples `return` y el largo del cuerpo son intencionales: cada guard de
# seguridad (kill-switch, web off, drive no-bajo, sin budget, breaker abierto, sin
# tema, fail-open) es un return temprano legible; aplanarlo en uno solo lo haría peor.
async def _run_curiosity_loop(  # noqa: PLR0911, PLR0912, PLR0915
    settings: Settings,
    *,
    llm_factory: LLMFactory,
    search_fn: SearchFn,
    sdk_researcher: SdkResearcherFn | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Núcleo async, testeable con ``llm_factory`` + ``search_fn`` inyectados (sin red).

    Posee el engine lifecycle; corre BYPASSRLS (sin ``set_config app.tenant_id``);
    captura sus propias excepciones (best-effort, nunca tumba beat)."""
    now = now or datetime.now(UTC)
    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    redis = _get_redis()
    try:
        from api_server.db.platform_settings import (
            get_cortex_autonomy_enabled,
            get_cortex_curiosity_approval_gate,
            get_cortex_curiosity_cb_fails,
            get_cortex_curiosity_daily_searches_cap,
            get_cortex_curiosity_daily_usd_cap,
            get_cortex_curiosity_drive_threshold,
            get_cortex_curiosity_enabled,
            get_cortex_web_enabled,
        )

        # (1) Kill-switch + enable propio + (2) web gate.
        async with sessionmaker() as session:
            if not await get_cortex_autonomy_enabled(session):
                return {"skipped": "disabled"}
            if not await get_cortex_curiosity_enabled(session):
                # Motivo PROPIO (no el `disabled` genérico del plan): la aceptación
                # pide cada rama del gate observable en el dict, y con un solo motivo
                # habría que mirar la BD para saber qué llave paró la pasada.
                return {"skipped": "curiosity_disabled"}
            if not await get_cortex_web_enabled(session):
                return {"skipped": "web_disabled"}
            searches_cap = await get_cortex_curiosity_daily_searches_cap(session)
            usd_cap = await get_cortex_curiosity_daily_usd_cap(session)
            drive_threshold = await get_cortex_curiosity_drive_threshold(session)
            cb_threshold = await get_cortex_curiosity_cb_fails(session)
            approval_gate = await get_cortex_curiosity_approval_gate(session)

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

        # (6) Budget gate: búsquedas Y dólares (AND — agotada cualquiera, no salimos).
        from api_server.cortex.autonomy import check_and_reserve

        budget = await check_and_reserve(
            redis,
            owner_user_id=str(owner_id),
            usd_cap=usd_cap,
            searches_cap=searches_cap,
            now=now,
        )
        if not budget.allowed:
            await _record_pursuit_skipped(sessionmaker, owner_id, reason=budget.reason)
            return {"skipped": "budget", "reason": budget.reason}

        # (7) ¿Hay una persecución en curso que RETOMAR? Una propuesta ya aprobada por
        # el owner (o una `selected` viva con el gate bajado) manda sobre elegir tema
        # nuevo: aprobar A y salir a buscar B sería un fraude del gate. Y evita que
        # cada pasada (cada 30 min) apile una propuesta más mientras nadie contesta.
        resumed = await _find_resumable_pursuit(sessionmaker, owner_id, now=now)
        if resumed is not None:
            # Las `source_entities` salen de la FILA, no se recalculan: la memoria
            # learning debe nacer con las entities que motivaron el tema cuando se
            # eligió, no con las de hoy (que pueden ser otras si el owner ha hablado
            # de cosas nuevas mientras la propuesta esperaba su aprobación).
            pursuit_id, topic, approved, source_entities = resumed
            if approval_gate and approved is None:
                _log.info(
                    "cortex_curiosity.awaiting_approval",
                    owner=str(owner_id),
                    pursuit_id=str(pursuit_id),
                    topic=topic,
                )
                return {
                    "awaiting_approval": True,
                    "topic": topic,
                    "pursuit_id": str(pursuit_id),
                }
        else:
            # (7b) Selección de tema (dedup por tema reciente) + fila 'selected'.
            selected_topic, source_entities = await _select_topic(sessionmaker, owner_id, now=now)
            if selected_topic is None:
                return {"skipped": "no_topic"}
            topic = selected_topic
            pursuit_id = await _insert_pursuit(
                sessionmaker, owner_id, topic=topic, source_entities=source_entities, drives=drives
            )
            if approval_gate:
                # El córtex PROPONE y espera. Queda `selected` con `approved IS NULL`
                # (el tri-estado de la migración 0123): ni buscó ni gastó.
                _log.info(
                    "cortex_curiosity.pending_approval",
                    owner=str(owner_id),
                    pursuit_id=str(pursuit_id),
                    topic=topic,
                )
                return {
                    "pending_approval": True,
                    "topic": topic,
                    "pursuit_id": str(pursuit_id),
                }

        # (8) Investigar. Fail-open + circuit-breaker. `searching` deja constancia de
        # que la pasada llegó a salir (el estado que el /approve del router mueve).
        await _mark_pursuit_searching(sessionmaker, pursuit_id)
        try:
            digest, search_count, cost_usd = await _research_dispatch(
                settings=settings,
                llm_factory=llm_factory,
                search_fn=search_fn,
                sdk_researcher=sdk_researcher,
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
            # `cb_opened` viaja en el retorno para que la métrica del breaker salga de
            # aquí y no de una segunda consulta a Redis (que podría contradecirlo).
            return {
                "failed": "research_error",
                "pursuit_id": str(pursuit_id),
                "cb_opened": opened,
            }

        # Consumimos el budget REAL de la pasada (búsquedas + dólares), ya gastado.
        from api_server.cortex.autonomy import record_spend, record_success

        await record_spend(
            redis,
            owner_user_id=str(owner_id),
            cost_usd=cost_usd,
            searches=search_count,
            now=now,
        )

        if not digest:
            # Sin digest útil: no escribimos memoria; cuenta como skip (no fallo). El
            # coste se persiste igual: se gastó aunque no se aprendiese.
            await _mark_pursuit_skipped_by_id(
                sessionmaker, pursuit_id, reason="empty_digest", cost_usd=cost_usd
            )
            await record_success(redis, owner_user_id=str(owner_id))
            return {
                "skipped": "empty_digest",
                "pursuit_id": str(pursuit_id),
                "cost_usd": cost_usd,
            }

        # (9) Memoria learning (idempotente por pursuit_id) → 'digested'.
        memory_id = await _persist_and_mark_digested(
            sessionmaker,
            owner_id=owner_id,
            tenant_id=tenant_id,
            topic=topic,
            digest=digest,
            pursuit_id=pursuit_id,
            source_entities=source_entities,
            search_count=search_count,
            cost_usd=cost_usd,
        )

        # (10) Saciar el drive 'curiosity' (snapshot) + éxito del breaker.
        await _satisfy_curiosity(sessionmaker, redis, owner_id, now=now)
        await record_success(redis, owner_user_id=str(owner_id))

        _log.info(
            "cortex_curiosity.digested",
            owner=str(owner_id),
            topic=topic,
            pursuit_id=str(pursuit_id),
            cost_usd=cost_usd,
        )
        return {
            "digested": True,
            "topic": topic,
            "pursuit_id": str(pursuit_id),
            "learning_memory_id": str(memory_id) if memory_id else None,
            "search_count": search_count,
            "cost_usd": cost_usd,
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
async def _research_dispatch(
    *,
    settings: Settings,
    llm_factory: LLMFactory,
    search_fn: SearchFn,
    sdk_researcher: SdkResearcherFn | None,
    topic: str,
) -> tuple[str, int, float]:
    """Elige el camino de investigación y devuelve ``(digest, búsquedas, coste_usd)``.

    Primero el camino RECOMENDADO (ADR 0076 punto 3): las web tools nativas del
    ``claude_sdk``, que traen anti-SSRF gratis y reportan el coste real. Si este
    despliegue no lo tiene (``skipped=True``), cae al camino de la tool web propia
    con anti-SSRF + digest con Ollama local (punto 4, la divergencia aceptada).

    NO se salta la pasada por no tener SDK: hacerlo dejaría la curiosidad muerta en
    el único despliegue que existe (el owner usa Ollama)."""
    if sdk_researcher is not None:
        result = await sdk_researcher(topic)
        if not result.skipped:
            return result.digest, result.search_count, result.cost_usd
        _log.debug("cortex_curiosity.sdk_path_skipped", reason=result.reason, topic=topic)
    return await _research(
        settings=settings, llm_factory=llm_factory, search_fn=search_fn, topic=topic
    )


async def _research(
    *,
    settings: Settings,
    llm_factory: LLMFactory,
    search_fn: SearchFn,
    topic: str,
) -> tuple[str, int, float]:
    """``web_search(topic)`` → digest con Ollama local. ``(digest, búsquedas, coste)``.

    Cualquier excepción la propaga al caller (que la trata como fallo del bucle +
    circuit-breaker). Si la búsqueda no devuelve resultados, el digest es ``""`` (el
    caller lo trata como skip, no como fallo).

    El coste sale del ``usage`` que reporte el provider en vez de asumirse 0: con
    Ollama local es 0.0 (sin factura de API, ADR 0021), pero si el operador apunta
    ``cortex_affect_llm_base_url`` a un endpoint gestionado, ese gasto SÍ debe contar
    contra el cap de dólares."""
    results = await search_fn(topic, _SEARCH_LIMIT)
    search_count = 1  # una llamada de búsqueda consumida (independiente de #resultados)
    if not results:
        return "", search_count, 0.0

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
    cost_usd = float(getattr(resp.usage, "cost_usd", 0.0) or 0.0)
    return (resp.content or "").strip(), search_count, cost_usd


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


async def _find_resumable_pursuit(
    sessionmaker: async_sessionmaker[Any], owner_id: UUID, *, now: datetime
) -> tuple[UUID, str, bool | None, list[str]] | None:
    """La persecución a RETOMAR: ``(id, topic, approved, source_entities)`` o ``None``.

    Es la más reciente en ``status='selected'`` del owner que NO haya sido rechazada
    (``approved IS NOT FALSE``) y no sea rancia. Cumple dos funciones a la vez:

      * **retomar lo aprobado**: sin esto, el ``/approve`` del router escribiría
        ``approved=true`` y no pasaría nada nunca — el patrón "mecanismo entregado,
        cero llamantes" que esta base ya ha sufrido varias veces;
      * **no apilar propuestas**: el bucle corre cada 30 minutos; si cada pasada
        insertase una propuesta nueva mientras el owner no contesta, un fin de semana
        dejaría ~100 filas del mismo tema.

    Rancias fuera (``_PENDING_MAX_AGE_DAYS``): si el owner no contestó en una semana,
    el tema ya no es "lo último que le interesaba" y no debe secuestrar el bucle para
    siempre. Filtro ``owner_user_id`` explícito (tabla tenant-less, ADR 0074)."""
    from datetime import timedelta

    from api_server.db.cortex_curiosity import CortexCuriosityPursuit

    cutoff = now - timedelta(days=_PENDING_MAX_AGE_DAYS)
    async with sessionmaker() as session:
        row = (
            await session.execute(
                select(
                    CortexCuriosityPursuit.id,
                    CortexCuriosityPursuit.topic,
                    CortexCuriosityPursuit.approved,
                    CortexCuriosityPursuit.source_entities,
                )
                .where(
                    CortexCuriosityPursuit.owner_user_id == owner_id,
                    CortexCuriosityPursuit.status == "selected",
                    CortexCuriosityPursuit.approved.isnot(False),
                    CortexCuriosityPursuit.created_at >= cutoff,
                )
                .order_by(CortexCuriosityPursuit.created_at.desc())
                .limit(1)
            )
        ).first()
    if row is None:
        return None
    entities = [str(e) for e in (row[3] or [])] or [str(row[1])]
    return row[0], str(row[1]), row[2], entities


async def _mark_pursuit_searching(sessionmaker: async_sessionmaker[Any], pursuit_id: UUID) -> None:
    """Marca ``status='searching'`` antes de salir a la web (paso 8 del plan).

    Deja constancia de que la pasada llegó a investigar: un pursuit que se queda en
    ``searching`` para siempre delata un worker muerto a mitad, algo que la
    transición directa ``selected → digested`` escondía."""
    from api_server.db.cortex_curiosity import CortexCuriosityPursuit

    async with sessionmaker() as session, session.begin():
        row = await session.get(CortexCuriosityPursuit, pursuit_id)
        if row is not None:
            row.status = "searching"


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
    sessionmaker: async_sessionmaker[Any],
    pursuit_id: UUID,
    *,
    reason: str,
    cost_usd: float = 0.0,
) -> None:
    """Marca el pursuit ``skipped`` y persiste el coste YA gastado.

    El coste va aunque no se aprendiese nada: una búsqueda sin resultados útiles se
    ha pagado igual, y ocultarlo haría que la suma del panel no cuadrase con el
    contador del budget."""
    from api_server.db.cortex_curiosity import CortexCuriosityPursuit

    async with sessionmaker() as session, session.begin():
        row = await session.get(CortexCuriosityPursuit, pursuit_id)
        if row is not None:
            row.status = "skipped"
            row.metadata_ = {**(row.metadata_ or {}), "reason": reason}
            if cost_usd > 0:
                row.cost_usd = cost_usd


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
    cost_usd: float = 0.0,
) -> UUID | None:
    """Escribe la memoria ``learning`` (idempotente) y marca el pursuit ``digested``.

    Aquí aterriza el ``cost_usd`` de la pasada: era la columna que el panel leía y
    que nadie escribía nunca (auditoría 2026-07-27), de ahí el "coste real" a 0.00."""
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
            row.cost_usd = cost_usd
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
