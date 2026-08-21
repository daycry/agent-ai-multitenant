"""Córtex F3 — reflexión periódica de la identidad (Celery, Ollama local, fail-open).

ADR 0074/0077: el córtex sintetiza sus turnos recientes en una **narrativa
reescrita** y un **ajuste ACOTADO de traits/baseline**, versionado y nunca
auto-borrado. El bucle es de FONDO (consume LLM cuando nadie habla), así que es
deliberadamente barato y conservador.

Invariantes (espejo del distilador afectivo :mod:`workers.cortex_affect`):

  * **Gobernada** (ADR 0078): el núcleo consulta el kill-switch global
    ``cortex.autonomy_enabled`` y un **budget diario por owner** ANTES de gastar
    LLM. Aplica a los DOS caminos —el beat y el botón "Reflexionar ahora"— porque
    ambos gastan lo mismo; antes el manual esquivaba el kill-switch y no tenía
    tope ninguno. Ver :data:`REFLECTION_DAILY_CAP`.
  * **Idempotente por marca**: sólo sintetiza turnos POSTERIORES a la última
    reflexión (``metadata_.reflected_through``). Sin esto, con el beat cada 6 h y
    un owner que no habla, cada pasada releía los mismos 20 turnos y derivaba la
    identidad otra vez sin información nueva.
  * **Fail-open** (ADR 0064): Ollama caído / timeout / JSON inválido ⇒ NO-OP — la
    identidad queda INTACTA (sin nueva versión) y la tarea devuelve
    ``ok:fail_open``. El ``try/except`` global hace que la tarea jamás propague.
  * **Deriva acotada** (ADR 0074): el delta de traits/baseline se recorta a
    ``BASELINE_MAX_DELTA_PER_REFLECTION`` por ciclo (``cortex/identity.py``:
    ``apply_reflection_delta`` compone clamp + bounded). Una pasada NUNCA puede
    derivar la identidad de forma salvaje; converge sin oscilar.
  * **Sin egress / catálogo cerrado** (ADR 0021): usa Ollama LOCAL, un modelo
    pequeño y barato. La síntesis profunda NO es el objetivo: el ajuste es lento.

La identidad **nunca se auto-olvida** (ADR 0077): la reflexión solo reescribe
``narrative``/``traits``/``mood_baseline`` (versionado en
``cortex_identity_history``) y deja una memoria semántica ``metadata_.kind =
'reflection'`` (protegida del olvido). NO programa el beat aquí (el scheduler es
de F4): expone :func:`trigger_cortex_reflection` para que F4 lo agende y un
disparo manual desde el endpoint ``POST /owner/cortex/reflect``.

> Honestidad: es un modelo computacional de identidad que evoluciona, NO
> consciencia ni un "yo" real.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from api_server.cortex.autonomy import BudgetDecision
from api_server.cortex.identity import (
    apply_owner_model_delta,
    apply_reflection_delta,
    ensure_identity,
    update_identity,
)
from api_server.db.cortex import CortexConversation, CortexTurn
from api_server.memorizer import MemoryCandidate, persist_memory_candidates
from shared_llm.base import LLMProvider
from shared_llm.providers import OllamaProvider
from shared_llm.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine

_log = structlog.get_logger("workers.cortex_reflection")

# Factory del provider que la reflexión llama, sobreescrita en tests por un fake.
LLMFactory = Callable[[Settings], LLMProvider]

#: Cuántos turnos recientes del owner alimentan la síntesis (acotado: barato).
_RECENT_TURNS_LIMIT = 20

#: Tope de hechos duraderos sobre el owner por ciclo de reflexión (barato).
_OWNER_FACTS_PER_CYCLE = 3

#: ``kind`` de la reflexión en el gobierno de F4 (``cortex:budget:{owner}:reflection:
#: {yyyymmdd}``, ``cortex:cb:{owner}:reflection``). Separado del de la curiosidad a
#: propósito: son gastos distintos y un tope no debe consumir el del otro.
REFLECTION_KIND = "reflection"

#: Tope DIARIO de pasadas de reflexión por owner (ventana UTC, ADR 0078). El beat
#: corre cada 6 h (4 pasadas/día, ver ``Settings.cortex_reflection_cron``), así que
#: 12 deja margen holgado para disparos manuales del owner y a la vez impide que
#: pulsar "Reflexionar ahora" en bucle vacíe la cuota de LLM: era el hueco literal
#: («el owner puede pulsar sin tope y el gasto no se contabiliza en ninguna parte»).
#:
#: Constante y no platform setting por alcance: hacerlo operator-tunable exige una
#: clave nueva en ``db/platform_settings.py``, que es de otro dominio. El cap se
#: cambia aquí mientras eso no exista.
REFLECTION_DAILY_CAP = 12

#: Cuánto sube el drive ``coherence`` una reflexión exitosa (paso 8 del plan).
#: Mismo delta que la curiosidad usa para el suyo — el motor PAD lo clampa a 1.0.
_COHERENCE_SATISFY_DELTA = 0.3

#: System prompt de la reflexión. Pide SÓLO el JSON (sin prosa). El ajuste es
#: PEQUEÑO (la cota dura la impone el motor, no el LLM). Bilingüe.
_REFLECT_SYSTEM_PROMPT = (
    "Eres el proceso de REFLEXIÓN de un córtex con identidad evolutiva (modelo "
    "COMPUTACIONAL, NO consciencia). Lees los turnos recientes del owner y la "
    "identidad actual del córtex, y sintetizas: (1) una NARRATIVA autobiográfica "
    "reescrita en PRIMERA persona (1-3 frases, en el idioma del owner), (2) un "
    "AJUSTE PEQUEÑO de los rasgos Big-Five y del baseline de ánimo, y (3) lo que "
    "aprendiste SOBRE EL OWNER (su modelo). Responde EXCLUSIVAMENTE con un objeto "
    "JSON, sin texto alrededor, con esta forma:\n"
    '{"narrative": "<narrativa en 1ª persona>", '
    '"traits": {"openness": <0..1>, "conscientiousness": <0..1>, '
    '"extraversion": <0..1>, "agreeableness": <0..1>, "neuroticism": <0..1>}, '
    '"mood_baseline": {"valence": <-1..1>, "arousal": <0..1>, "dominance": <-1..1>}, '
    '"owner_model": {"<clave-corta>": "<valor breve sobre el OWNER: preferencias, '
    'estilo, metas, contexto>", "<clave-a-retirar>": ""}, '
    '"owner_facts": ["<hecho DURADERO sobre el owner>", "..."], '
    '"summary": "<una frase de QUÉ aprendiste en este ciclo>"}\n'
    "Los ajustes de traits/baseline son SUTILES (la plataforma los recorta a un "
    "delta pequeño por ciclo de todos modos): describe la TENDENCIA, no un salto. "
    "En owner_model ACTUALIZA el modelo actual que se te muestra (una clave con "
    'valor "" la retira si quedó obsoleta); máximo 0-3 owner_facts, solo hechos '
    "duraderos (no anécdotas del turno). owner_model y owner_facts son OPCIONALES: "
    "omítelos si no aprendiste nada nuevo del owner. No afirmes sentimientos "
    "reales: es un modelo de identidad que evoluciona."
)


@dataclass(frozen=True)
class ReflectionProposal:
    """La propuesta parseada de un ciclo de reflexión (todo opcional, granular).

    ``owner_model`` es el delta de "lo que sé de mi owner" (``relationship_model``)
    y ``owner_facts`` los hechos duraderos a persistir como memorias
    ``kind='owner_model'`` (ya protegidas del olvido, ADR 0077)."""

    narrative: str | None
    traits: dict[str, Any] | None
    mood_baseline: dict[str, Any] | None
    summary: str | None
    owner_model: dict[str, Any] | None
    owner_facts: tuple[str, ...]


def _default_llm_factory(settings: Settings) -> LLMProvider:
    """Provider por defecto de la reflexión: Ollama local (ADR 0021, sin egress)."""
    return OllamaProvider(
        base_url=settings.cortex_affect_llm_base_url,
        default_model=settings.cortex_affect_llm_model,
    )


@app.task(name="workers.cortex_reflect")  # type: ignore[untyped-decorator]
def cortex_reflect(owner_user_id: str) -> dict[str, Any]:
    """Celery entry point. Reflexiona la identidad del córtex de un owner.

    Devuelve un dict para que el result backend deje un rastro útil:

      {"owner_user_id": ..., "reason": "ok"|"ok:fail_open"|"skipped:..."|...}
    """
    settings = get_settings()
    return asyncio.run(
        _reflect_async(
            UUID(owner_user_id),
            settings=settings,
            llm_factory=_default_llm_factory,
        )
    )


@app.task(name="workers.cortex_reflect_scheduled")  # type: ignore[untyped-decorator]
def cortex_reflect_scheduled() -> dict[str, Any]:
    """Entry point del BEAT (sin args): reflexión AUTÓNOMA del córtex.

    A diferencia de ``cortex_reflect`` (disparo manual del owner, sin gate), la
    versión programada respeta el KILL-SWITCH ``cortex.autonomy_enabled`` (default
    OFF ⇒ no-op) y resuelve el owner singleton ella misma. Best-effort: jamás
    propaga al worker (no tumba el beat)."""
    settings = get_settings()
    return asyncio.run(_reflect_scheduled_async(settings, llm_factory=_default_llm_factory))


async def _reflect_scheduled_async(
    settings: Settings, *, llm_factory: LLMFactory
) -> dict[str, Any]:
    """Núcleo del beat: kill-switch → owners(singleton) → reflexión por owner.

    El kill-switch se mira aquí para salir barato sin resolver owners, y ``_reflect_async``
    lo vuelve a mirar por owner (es donde vive el gobierno completo, ADR 0078, para que el
    disparo manual también lo respete). La redundancia es intencional: una consulta."""
    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        from api_server.db.models import User
        from api_server.db.platform_settings import get_cortex_autonomy_enabled

        async with sessionmaker() as session:
            if not await get_cortex_autonomy_enabled(session):
                return {"skipped": "disabled"}
            owners = [
                r[0]
                for r in (
                    await session.execute(
                        select(User.id).where(
                            User.is_system_owner.is_(True), User.deleted_at.is_(None)
                        )
                    )
                ).all()
            ]
        if not owners:
            return {"skipped": "no_owner"}
        results = [
            await _reflect_async(owner_id, settings=settings, llm_factory=llm_factory)
            for owner_id in owners
        ]
        return {"owners": len(owners), "results": results}
    except Exception as exc:  # best-effort: jamás propaga al beat
        _log.exception("cortex_reflect_scheduled.failed", error=str(exc))
        return {"error": str(exc)}
    finally:
        await engine.dispose()


async def _reflect_async(
    owner_user_id: UUID,
    *,
    settings: Settings,
    llm_factory: LLMFactory,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Núcleo async, testeable con un ``llm_factory`` inyectado (sin red).

    El ``settings.database_url`` es un rol BYPASSRLS (como el resto del córtex);
    TODO acceso filtra ``owner_user_id`` explícito (defensa cross-owner). La
    aplicación del delta es determinista y ACOTADA (``apply_reflection_delta``).

    Lo ejecutan los DOS caminos —``cortex_reflect`` (botón del owner) y
    ``cortex_reflect_scheduled`` (beat)— así que el gobierno de ADR 0078
    (kill-switch + budget diario) vive AQUÍ y no en la entrada del beat: de otro
    modo el disparo manual lo esquivaba. El reloj entra como ``now`` para que la
    ventana del budget sea determinista en test.
    """
    now = now or datetime.now(UTC)
    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # (0) Gobierno (ADR 0078): kill-switch global y budget diario por owner,
        # ANTES de tocar identidad o LLM. Un no-op aquí no escribe NADA.
        from api_server.db.platform_settings import get_cortex_autonomy_enabled

        async with sessionmaker() as session:
            if not await get_cortex_autonomy_enabled(session):
                _log.info("cortex_reflection.disabled", owner_user_id=str(owner_user_id))
                return _result(owner_user_id, "skipped:disabled")

        redis = _get_redis()
        budget = await _check_reflection_budget(redis, owner_user_id, now=now)
        if not budget.allowed:
            _log.info(
                "cortex_reflection.budget_exhausted",
                owner_user_id=str(owner_user_id),
                used=budget.used,
                cap=budget.cap,
            )
            return _result(owner_user_id, f"skipped:budget:{budget.reason}")

        # (1) Turnos NUEVOS desde la última reflexión (filtro owner explícito; sin
        # RLS, ADR 0074). La marca hace la pasada idempotente: sin conversación
        # nueva no hay nada que sintetizar y no se gasta LLM.
        watermark = await _last_reflected_turn_at(sessionmaker, owner_user_id)
        turns, tenant_id, newest_turn_at = await _load_recent_turns(
            sessionmaker, owner_user_id, after=watermark
        )
        if not turns:
            reason = "skipped:no_new_turns" if watermark is not None else "skipped:no_recent_turns"
            _log.info("cortex_reflection.no_turns", owner_user_id=str(owner_user_id), reason=reason)
            return _result(owner_user_id, reason)

        # (2) Identidad actual (crea la default si no existe — versión 0).
        async with sessionmaker() as session, session.begin():
            identity = await ensure_identity(session, owner_user_id)
            current_state: dict[str, Any] = dict(identity.identity_state or {})

        # (3) Síntesis con el LLM (FAIL-OPEN: cualquier fallo ⇒ no-op).
        proposal = await _synthesize(
            settings=settings, llm_factory=llm_factory, turns=turns, current_state=current_state
        )
        # El gasto se contabiliza por INTENTO, no por éxito: un modelo que devuelve
        # basura consume tokens igual, y si sólo contásemos las pasadas que parsean
        # bien el cap no frenaría precisamente el bucle caro (fail-open en serie).
        await _record_reflection_run(redis, owner_user_id, now=now)
        if proposal is None:
            _log.warning("cortex_reflection.fail_open", owner_user_id=str(owner_user_id))
            return _result(owner_user_id, "ok:fail_open")

        # (4) Aplicación DETERMINISTA + ACOTADA (clamp + bounded por ciclo) +
        # merge acotado del owner-model ("aprender DE MÍ", ADR 0074).
        new_state = apply_reflection_delta(
            current_state,
            narrative=proposal.narrative,
            traits=proposal.traits,
            mood_baseline=proposal.mood_baseline,
        )
        if proposal.owner_model is not None:
            new_state = apply_owner_model_delta(new_state, proposal.owner_model)

        # (5) Versionado (updated_by='reflection'); la identidad NUNCA se borra.
        async with sessionmaker() as session, session.begin():
            await update_identity(
                session,
                owner_user_id,
                new_state=new_state,
                reason=proposal.summary or "reflexión periódica",
                updated_by="reflection",
            )

        # (6) Memoria semántica kind='reflection' (ADR 0077: protegida del olvido).
        await _persist_reflection_memory(
            sessionmaker,
            owner_user_id=owner_user_id,
            tenant_id=tenant_id,
            narrative=new_state.get("narrative", ""),
            summary=proposal.summary,
        )

        # (7) Hechos duraderos sobre el owner → memorias kind='owner_model'
        # (protegidas del olvido; el self-context las recalla). Best-effort.
        await _persist_owner_model_memories(
            sessionmaker,
            owner_user_id=owner_user_id,
            tenant_id=tenant_id,
            facts=proposal.owner_facts,
        )

        # (7b) Marca de idempotencia: hasta aquí llegó esta reflexión.
        await _mark_reflected_through(
            sessionmaker, owner_user_id=owner_user_id, through=newest_turn_at
        )

        # (8) Saciar el drive `coherence` (motor PAD de F2): pensar sobre uno mismo
        # calma la necesidad de coherencia. Sin esto el drive subía por decay y nada
        # lo bajaba nunca, así que el córtex quedaba hambriento de una síntesis que
        # sí estaba haciendo. Best-effort.
        await _satisfy_coherence(sessionmaker, redis, owner_user_id, now=now)

        _log.info("cortex_reflection.done", owner_user_id=str(owner_user_id))
        return _result(owner_user_id, "ok")
    except Exception as exc:
        # Belt + braces: un fallo de la reflexión JAMÁS debe propagar al worker
        # (es un bucle de fondo opcional; la identidad ya existía y queda intacta).
        _log.exception("cortex_reflection.failed", owner_user_id=str(owner_user_id), error=str(exc))
        return _result(owner_user_id, f"error:{exc}")
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Síntesis — la única parte que toca el LLM (fail-open)
# ---------------------------------------------------------------------------
async def _synthesize(
    *,
    settings: Settings,
    llm_factory: LLMFactory,
    turns: list[tuple[str, str]],
    current_state: dict[str, Any],
) -> ReflectionProposal | None:
    """Llama al LLM y parsea el JSON → :class:`ReflectionProposal`.

    **Fail-open**: cualquier excepción (Ollama caído/timeout) o JSON inválido ⇒
    ``None`` (el caller lo trata como no-op: la identidad queda intacta)."""
    llm = llm_factory(settings)
    try:
        user_prompt = _build_user_prompt(turns, current_state)
        resp = await llm.complete(
            [
                Message(role="system", content=_REFLECT_SYSTEM_PROMPT),
                Message(role="user", content=user_prompt),
            ],
            max_tokens=768,
            temperature=0.2,
        )
    except Exception as exc:
        _log.warning("cortex_reflection.synthesize_failed_open", error=str(exc))
        return None
    finally:
        await llm.aclose()

    return _parse_proposal(resp.content)


def _build_user_prompt(turns: list[tuple[str, str]], current_state: dict[str, Any]) -> str:
    """Los turnos recientes + la identidad actual, en un bloque que el LLM sintetiza."""
    convo = "\n".join(f"  {role}: {content[:240]}" for role, content in turns)
    name = current_state.get("name") or "(sin nombre)"
    narrative = current_state.get("narrative") or "(sin narrativa todavía)"
    values = ", ".join(str(v) for v in (current_state.get("core_values") or [])) or "(sin valores)"
    relationship = current_state.get("relationship_model") or {}
    return (
        "Identidad actual del córtex:\n"
        f"  Nombre: {name}\n"
        f"  Valores: {values}\n"
        f"  Narrativa: {narrative}\n"
        f"  Traits: {json.dumps(current_state.get('traits', {}))}\n"
        f"  Baseline de ánimo: {json.dumps(current_state.get('mood_baseline', {}))}\n"
        f"  Lo que ya sé de mi owner (owner_model actual): {json.dumps(relationship)}\n\n"
        "Turnos recientes (más antiguos primero):\n"
        f"{convo}\n\n"
        "Devuelve SÓLO el JSON de la narrativa reescrita + el ajuste de "
        "traits/baseline + (si aprendiste algo del owner) owner_model/owner_facts."
    )


def _parse_proposal(content: str) -> ReflectionProposal | None:
    """Parsea el JSON de la reflexión. ``None`` si no es JSON válido.

    Tolerante y GRANULAR (fail-open por campo): extrae el primer objeto ``{...}``
    balanceado del texto (algunos modelos locales envuelven el JSON en prosa);
    un ``owner_model``/``owner_facts`` malformado se ignora SIN invalidar
    narrative/traits. Un objeto sin NINGÚN campo útil (narrative/traits/
    mood_baseline/owner_model/owner_facts) se trata como no-op (``None``)."""
    raw = _extract_json_object(content)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    narrative = data.get("narrative")
    narrative_str = (
        str(narrative).strip() if isinstance(narrative, str) and narrative.strip() else None
    )
    traits = data.get("traits") if isinstance(data.get("traits"), dict) else None
    baseline = data.get("mood_baseline") if isinstance(data.get("mood_baseline"), dict) else None
    summary = data.get("summary")
    summary_str = str(summary).strip() if isinstance(summary, str) and summary.strip() else None

    owner_model = data.get("owner_model") if isinstance(data.get("owner_model"), dict) else None
    raw_facts = data.get("owner_facts")
    owner_facts: tuple[str, ...] = ()
    if isinstance(raw_facts, list):
        owner_facts = tuple(
            str(fact).strip() for fact in raw_facts if isinstance(fact, str) and fact.strip()
        )[:_OWNER_FACTS_PER_CYCLE]

    # Nada útil que aplicar ⇒ no-op (no versionamos por un objeto vacío).
    if (
        narrative_str is None
        and traits is None
        and baseline is None
        and owner_model is None
        and not owner_facts
    ):
        return None
    return ReflectionProposal(
        narrative=narrative_str,
        traits=traits,
        mood_baseline=baseline,
        summary=summary_str,
        owner_model=owner_model,
        owner_facts=owner_facts,
    )


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
# DB helpers
# ---------------------------------------------------------------------------
async def _load_recent_turns(
    sessionmaker: async_sessionmaker[Any],
    owner_user_id: UUID,
    *,
    after: datetime | None = None,
) -> tuple[list[tuple[str, str]], UUID | None, datetime | None]:
    """Los ``_RECENT_TURNS_LIMIT`` turnos más recientes del owner (orden cronológico).

    Filtro ``owner_user_id`` explícito (tablas tenant-less sin RLS, ADR 0074). El
    ``tenant_id`` (para la memoria) sale del hilo del turno más reciente.

    ``after`` acota a los turnos POSTERIORES a la última reflexión (idempotencia:
    ver :func:`_last_reflected_turn_at`). La comparación es ESTRICTA — un empate
    exacto de ``created_at`` con la marca se considera ya procesado; perder un turno
    en un empate al microsegundo es inocuo para un resumen, re-procesar los 20 no.

    Devuelve además el ``created_at`` del turno MÁS NUEVO leído, que es la marca a
    escribir si la pasada acaba bien."""
    async with sessionmaker() as session:
        stmt = select(
            CortexTurn.role, CortexTurn.content, CortexTurn.conversation_id, CortexTurn.created_at
        ).where(CortexTurn.owner_user_id == owner_user_id)
        if after is not None:
            stmt = stmt.where(CortexTurn.created_at > after)
        stmt = stmt.order_by(CortexTurn.created_at.desc(), CortexTurn.id.desc()).limit(
            _RECENT_TURNS_LIMIT
        )
        rows = list((await session.execute(stmt)).all())
        if not rows:
            return [], None, None
        newest_at = rows[0].created_at
        # tenant del hilo del turno más reciente (defensa: filtro owner explícito).
        latest_conv_id = rows[0].conversation_id
        conv = await session.get(CortexConversation, latest_conv_id)
        tenant_id = (
            conv.tenant_id if conv is not None and conv.owner_user_id == owner_user_id else None
        )

    rows.reverse()  # cronológico (más antiguo primero) para la síntesis.
    turns = [(r.role, r.content) for r in rows]
    return turns, tenant_id, newest_at


# ---------------------------------------------------------------------------
# Idempotencia — la marca de "hasta aquí ya reflexioné"
# ---------------------------------------------------------------------------
#: Clave de la marca dentro del ``metadata_`` de la memoria de reflexión.
_REFLECTED_THROUGH = "reflected_through"


async def _last_reflected_turn_at(
    sessionmaker: async_sessionmaker[Any], owner_user_id: UUID
) -> datetime | None:
    """El ``created_at`` del último turno que una reflexión ya consumió, o ``None``.

    La marca vive en ``metadata_.reflected_through`` de la memoria semántica
    ``kind='reflection'`` más reciente del owner — la que la propia reflexión
    escribe, ya protegida del auto-olvido (ADR 0077), así que la marca es durable.
    Filtro ``owner_user_id`` explícito.

    ``None`` (⇒ se procesan los últimos 20 turnos) significa "nunca he reflexionado,
    o la memoria de la última reflexión no pudo escribirse". Ese segundo caso sólo
    ocurre cuando el owner no tiene tenant resoluble y ya implica que no hay
    memorias en absoluto; degradar a re-sintetizar es preferible a no reflexionar.
    Tolerante: una marca ilegible se trata como ausente."""
    from api_server.db.memory import MemoryEntry

    async with sessionmaker() as session:
        raw_meta = (
            await session.execute(
                select(MemoryEntry.metadata_)
                .where(
                    MemoryEntry.user_id == owner_user_id,
                    MemoryEntry.scope == "private",
                    MemoryEntry.metadata_["cortex"].astext == "true",
                    MemoryEntry.metadata_["kind"].astext == REFLECTION_KIND,
                    # `has_key` es el operador JSONB `?`, no el dict de Python.
                    MemoryEntry.metadata_.has_key(_REFLECTED_THROUGH),
                )
                .order_by(MemoryEntry.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    raw = (raw_meta or {}).get(_REFLECTED_THROUGH)
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        _log.warning("cortex_reflection.bad_watermark", owner_user_id=str(owner_user_id), value=raw)
        return None


async def _mark_reflected_through(
    sessionmaker: async_sessionmaker[Any], *, owner_user_id: UUID, through: datetime | None
) -> None:
    """Escribe la marca en la memoria de reflexión más reciente del owner.

    Se hace como UPDATE separado y no vía ``extra_metadata`` al crear la memoria a
    propósito: ``persist_memory_candidates`` DEDUPLICA por contenido, así que dos
    ciclos con el mismo ``summary`` no crearían fila nueva y la marca se quedaría
    congelada en el valor viejo. Actualizando la fila más reciente el avance ocurre
    igual (la marca describe el PROGRESO, no el contenido de esa memoria).

    Best-effort: un fallo aquí sólo hace que la próxima pasada vuelva a leer los
    mismos turnos, nunca rompe la versión de identidad ya escrita."""
    if through is None:
        return
    from api_server.db.memory import MemoryEntry
    from sqlalchemy import update

    try:
        async with sessionmaker() as session, session.begin():
            latest = (
                await session.execute(
                    select(MemoryEntry.id)
                    .where(
                        MemoryEntry.user_id == owner_user_id,
                        MemoryEntry.scope == "private",
                        MemoryEntry.metadata_["cortex"].astext == "true",
                        MemoryEntry.metadata_["kind"].astext == REFLECTION_KIND,
                    )
                    .order_by(MemoryEntry.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if latest is None:
                return
            await session.execute(
                update(MemoryEntry)
                .where(MemoryEntry.id == latest, MemoryEntry.user_id == owner_user_id)
                .values(
                    metadata_=MemoryEntry.metadata_.concat(
                        {_REFLECTED_THROUGH: through.astimezone(UTC).isoformat()}
                    )
                )
            )
    except Exception as exc:  # marca best-effort
        _log.warning(
            "cortex_reflection.watermark_failed", owner_user_id=str(owner_user_id), error=str(exc)
        )


# ---------------------------------------------------------------------------
# Gobierno (ADR 0078) — budget diario por owner sobre el namespace `cortex:*` de F4
# ---------------------------------------------------------------------------
def _get_redis() -> Any:
    """Cliente Redis del api-server (mismo DB que el gobierno de F4 y la caché de F2).

    Mismo seam que :mod:`workers.cortex_curiosity` / :mod:`workers.cortex_maintenance`:
    no se abre infra nueva, se reusa el namespace ``cortex:*``."""
    from api_server.auth.deps import get_redis

    return get_redis()


async def _check_reflection_budget(
    redis: Any, owner_user_id: UUID, *, now: datetime
) -> BudgetDecision:
    """¿Queda budget de reflexiones hoy para este owner? (NO reserva todavía).

    Reusa el esquema de claves de F4 (:func:`daily_budget_key`, ventana diaria UTC
    que se autolimpia) con el ``kind`` propio :data:`REFLECTION_KIND`. Es un gemelo
    de ``autonomy.check_searches_budget``, que está atado al ``kind`` de la
    curiosidad y a su unidad (búsquedas web); generalizar aquélla para aceptar
    ``kind`` permitiría borrar esta función, pero vive en otro módulo.

    Fail-SAFE del coste: si Redis falla NO autorizamos gasto (igual que la
    curiosidad) — ante la duda, el córtex no consume LLM por su cuenta."""
    from api_server.cortex.autonomy import daily_budget_key

    cap = REFLECTION_DAILY_CAP
    if cap <= 0:
        return BudgetDecision(allowed=False, reason="cap_zero", used=0, cap=cap)
    key = daily_budget_key(str(owner_user_id), REFLECTION_KIND, now=now)
    try:
        raw = await redis.get(key)
    except Exception:  # Redis caído ⇒ fail-safe del coste
        return BudgetDecision(allowed=False, reason="redis_error", used=0, cap=cap)
    used = int(raw) if raw is not None else 0
    if used >= cap:
        return BudgetDecision(allowed=False, reason="budget_exhausted", used=used, cap=cap)
    return BudgetDecision(allowed=True, reason="ok", used=used, cap=cap)


async def _record_reflection_run(redis: Any, owner_user_id: UUID, *, now: datetime) -> None:
    """Suma UNA pasada al contador del día (``INCR`` + TTL a medianoche UTC).

    Best-effort: un fallo de Redis se traga (el trabajo ya se hizo; la contabilidad
    es secundaria). Sin este productor el cap sería inalcanzable."""
    from api_server.cortex.autonomy import daily_budget_key, seconds_until_utc_midnight

    key = daily_budget_key(str(owner_user_id), REFLECTION_KIND, now=now)
    try:
        await redis.incrby(key, 1)
        await redis.expire(key, seconds_until_utc_midnight(now))
    except Exception:  # contabilidad best-effort
        return


async def _satisfy_coherence(
    sessionmaker: async_sessionmaker[Any], redis: Any, owner_user_id: UUID, *, now: datetime
) -> None:
    """Sacia el drive ``coherence`` del motor PAD (F2) y refresca snapshot + caché.

    Espejo exacto de ``cortex_curiosity._satisfy_curiosity`` para el drive que le
    toca a la reflexión (paso 8 del plan F3): estado actual con decay lazy →
    ``satisfy_drive`` determinista → snapshot de mantenimiento (sin
    ``source_turn_id``) → caché viva de Redis, para que el Panel de Mente y el
    self-context lo vean sin esperar a un turno.

    Best-effort: un fallo aquí no debe tumbar la versión de identidad ya escrita."""
    from api_server.cortex.affect_cache import write_affect_state
    from api_server.cortex.affect_store import load_affect_state, save_affect_snapshot
    from api_server.cortex.affective import AffectState, satisfy_drive
    from api_server.cortex.identity import effective_mood_baseline, get_identity

    try:
        async with sessionmaker() as session, session.begin():
            identity = await get_identity(session, owner_user_id)
            baseline = effective_mood_baseline(identity.identity_state if identity else None)
            state = await load_affect_state(session, owner_user_id, now=now, baseline=baseline)
            new_drives = satisfy_drive(state.drives, "coherence", _COHERENCE_SATISFY_DELTA)
            new_state = AffectState(emotion=state.emotion, mood=state.mood, drives=new_drives)
            await save_affect_snapshot(
                session,
                owner_user_id=owner_user_id,
                state=new_state,
                appraisal_reason=None,
                source_turn_id=None,
                language="es",
            )
        await write_affect_state(redis, str(owner_user_id), new_state, now=now, baseline=baseline)
    except Exception as exc:  # best-effort
        _log.warning(
            "cortex_reflection.satisfy_coherence_failed",
            owner_user_id=str(owner_user_id),
            error=str(exc),
        )


async def _persist_reflection_memory(
    sessionmaker: async_sessionmaker[Any],
    *,
    owner_user_id: UUID,
    tenant_id: UUID | None,
    narrative: str,
    summary: str | None,
) -> None:
    """Escribe el insight de la reflexión como memoria semántica del owner.

    DIRECTO vía :func:`persist_memory_candidates` (NO ``workers.memorizer``, que
    enruta episodic→project_shared): scope=private, ``user_id=owner``,
    ``metadata_.kind='reflection'`` (ADR 0077: protegida del olvido) +
    ``metadata_.cortex=true``. Best-effort: un fallo aquí no debe tumbar la
    versión de identidad ya escrita (la memoria es un nice-to-have)."""
    if tenant_id is None:
        # Sin tenant resoluble no podemos persistir en memory_entries (necesita
        # tenant_id físico, D1). La identidad ya quedó versionada igualmente.
        return
    content = summary or narrative or "Reflexión periódica del córtex."
    candidate = MemoryCandidate(
        content=content,
        type="semantic",
        tags=("cortex", "reflection", "identity"),
    )
    try:
        async with sessionmaker() as session, session.begin():
            await persist_memory_candidates(
                session,
                [candidate],
                tenant_id=tenant_id,
                scope="private",
                user_id=owner_user_id,
                extra_metadata={"cortex": True, "kind": "reflection"},
            )
    except Exception as exc:  # memoria best-effort, nunca rompe la reflexión.
        _log.warning(
            "cortex_reflection.memory_persist_failed",
            owner_user_id=str(owner_user_id),
            error=str(exc),
        )


async def _persist_owner_model_memories(
    sessionmaker: async_sessionmaker[Any],
    *,
    owner_user_id: UUID,
    tenant_id: UUID | None,
    facts: tuple[str, ...],
) -> None:
    """Persiste los hechos duraderos sobre el owner como memorias ``owner_model``.

    DIRECTO vía :func:`persist_memory_candidates` (scope=private, ``user_id=owner``,
    ``metadata_.kind='owner_model'`` — protegida del olvido, ADR 0077;
    ``metadata_.cortex=true`` — recallable por el self-context). Dedup por
    contenido normalizado (patrón de ``cortex_remember``): re-aprender el mismo
    hecho es no-op. Best-effort: un fallo aquí no tumba la versión ya escrita."""
    if not facts or tenant_id is None:
        return
    from api_server.db.memory import MemoryEntry
    from sqlalchemy import func

    try:
        async with sessionmaker() as session, session.begin():
            for fact in facts:
                normalised = " ".join(fact.split())
                if not normalised:
                    continue
                existing = await session.execute(
                    select(MemoryEntry.id)
                    .where(
                        MemoryEntry.user_id == owner_user_id,
                        MemoryEntry.scope == "private",
                        MemoryEntry.deleted_at.is_(None),
                        MemoryEntry.metadata_["cortex"].astext == "true",
                        func.lower(func.btrim(MemoryEntry.content)) == normalised.lower(),
                    )
                    .limit(1)
                )
                if existing.scalar_one_or_none() is not None:
                    continue
                await persist_memory_candidates(
                    session,
                    [
                        MemoryCandidate(
                            content=normalised,
                            type="semantic",
                            tags=("cortex", "owner_model"),
                        )
                    ],
                    tenant_id=tenant_id,
                    scope="private",
                    user_id=owner_user_id,
                    extra_metadata={"cortex": True, "kind": "owner_model"},
                )
    except Exception as exc:  # memoria best-effort, nunca rompe la reflexión.
        _log.warning(
            "cortex_reflection.owner_model_persist_failed",
            owner_user_id=str(owner_user_id),
            error=str(exc),
        )


def _result(owner_user_id: UUID, reason: str) -> dict[str, Any]:
    return {"owner_user_id": str(owner_user_id), "reason": reason}


# ---------------------------------------------------------------------------
# Trigger (lo agenda F4 con el beat; o un disparo manual desde el endpoint)
# ---------------------------------------------------------------------------
def trigger_cortex_reflection(owner_user_id: UUID) -> bool:
    """Encola una pasada de reflexión para el córtex de un owner (cola ``default``).

    NO programa el beat (el scheduler es de F4): este helper es lo que F4 agendará
    y lo que el endpoint ``POST /owner/cortex/reflect`` invoca para un disparo
    manual/test. Best-effort: un fallo del broker se traga y loguea (devuelve
    False) — espejo de ``trigger_cortex_distill_affect``."""
    try:
        cortex_reflect.apply_async(args=[str(owner_user_id)], queue="default")
    except Exception as exc:
        _log.warning(
            "cortex_reflection.enqueue_failed", owner_user_id=str(owner_user_id), error=str(exc)
        )
        return False
    return True


__all__ = [
    "REFLECTION_DAILY_CAP",
    "REFLECTION_KIND",
    "cortex_reflect",
    "trigger_cortex_reflection",
]
