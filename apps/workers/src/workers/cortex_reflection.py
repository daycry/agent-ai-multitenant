"""Córtex F3 — reflexión periódica de la identidad (Celery, Ollama local, fail-open).

ADR 0074/0077: el córtex sintetiza sus turnos recientes en una **narrativa
reescrita** y un **ajuste ACOTADO de traits/baseline**, versionado y nunca
auto-borrado. El bucle es de FONDO (consume LLM cuando nadie habla), así que es
deliberadamente barato y conservador.

Tres invariantes (espejo del distilador afectivo :mod:`workers.cortex_affect`):

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
from typing import Any
from uuid import UUID

import structlog
from api_server.cortex.identity import apply_reflection_delta, ensure_identity, update_identity
from api_server.db.cortex import CortexConversation, CortexTurn
from api_server.memorizer import MemoryCandidate, persist_memory_candidates
from shared_llm.base import LLMProvider
from shared_llm.providers import OllamaProvider
from shared_llm.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.cortex_reflection")

# Factory del provider que la reflexión llama, sobreescrita en tests por un fake.
LLMFactory = Callable[[Settings], LLMProvider]

#: Cuántos turnos recientes del owner alimentan la síntesis (acotado: barato).
_RECENT_TURNS_LIMIT = 20

#: System prompt de la reflexión. Pide SÓLO el JSON (sin prosa). El ajuste es
#: PEQUEÑO (la cota dura la impone el motor, no el LLM). Bilingüe.
_REFLECT_SYSTEM_PROMPT = (
    "Eres el proceso de REFLEXIÓN de un córtex con identidad evolutiva (modelo "
    "COMPUTACIONAL, NO consciencia). Lees los turnos recientes del owner y la "
    "identidad actual del córtex, y sintetizas: (1) una NARRATIVA autobiográfica "
    "reescrita en PRIMERA persona (1-3 frases, en el idioma del owner) y (2) un "
    "AJUSTE PEQUEÑO de los rasgos Big-Five y del baseline de ánimo. Responde "
    "EXCLUSIVAMENTE con un objeto JSON, sin texto alrededor, con esta forma:\n"
    '{"narrative": "<narrativa en 1ª persona>", '
    '"traits": {"openness": <0..1>, "conscientiousness": <0..1>, '
    '"extraversion": <0..1>, "agreeableness": <0..1>, "neuroticism": <0..1>}, '
    '"mood_baseline": {"valence": <-1..1>, "arousal": <0..1>, "dominance": <-1..1>}, '
    '"summary": "<una frase de QUÉ aprendiste de ti en este ciclo>"}\n'
    "Los ajustes de traits/baseline son SUTILES (la plataforma los recorta a un "
    "delta pequeño por ciclo de todos modos): describe la TENDENCIA, no un salto. "
    "No afirmes sentimientos reales: es un modelo de identidad que evoluciona."
)


def _default_llm_factory(settings: Settings) -> LLMProvider:
    """Provider por defecto de la reflexión: Ollama local (ADR 0021, sin egress)."""
    return OllamaProvider(
        base_url=settings.cortex_affect_llm_base_url,
        default_model=settings.cortex_affect_llm_model,
    )


@app.task(name="workers.cortex_reflect")  # type: ignore[misc]
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


@app.task(name="workers.cortex_reflect_scheduled")  # type: ignore[misc]
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
    """Núcleo del beat: kill-switch → owners(singleton) → reflexión por owner."""
    engine = create_async_engine(settings.database_url)
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
) -> dict[str, Any]:
    """Núcleo async, testeable con un ``llm_factory`` inyectado (sin red).

    El ``settings.database_url`` es un rol BYPASSRLS (como el resto del córtex);
    TODO acceso filtra ``owner_user_id`` explícito (defensa cross-owner). La
    aplicación del delta es determinista y ACOTADA (``apply_reflection_delta``).
    """
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # (1) Turnos recientes del owner (filtro owner explícito; sin RLS, ADR 0074).
        turns, tenant_id = await _load_recent_turns(sessionmaker, owner_user_id)
        if not turns:
            _log.info("cortex_reflection.no_turns", owner_user_id=str(owner_user_id))
            return _result(owner_user_id, "skipped:no_recent_turns")

        # (2) Identidad actual (crea la default si no existe — versión 0).
        async with sessionmaker() as session, session.begin():
            identity = await ensure_identity(session, owner_user_id)
            current_state: dict[str, Any] = dict(identity.identity_state or {})

        # (3) Síntesis con el LLM (FAIL-OPEN: cualquier fallo ⇒ no-op).
        proposal = await _synthesize(
            settings=settings, llm_factory=llm_factory, turns=turns, current_state=current_state
        )
        if proposal is None:
            _log.warning("cortex_reflection.fail_open", owner_user_id=str(owner_user_id))
            return _result(owner_user_id, "ok:fail_open")

        narrative, traits, baseline, summary = proposal

        # (4) Aplicación DETERMINISTA + ACOTADA (clamp + bounded por ciclo).
        new_state = apply_reflection_delta(
            current_state, narrative=narrative, traits=traits, mood_baseline=baseline
        )

        # (5) Versionado (updated_by='reflection'); la identidad NUNCA se borra.
        async with sessionmaker() as session, session.begin():
            await update_identity(
                session,
                owner_user_id,
                new_state=new_state,
                reason=summary or "reflexión periódica",
                updated_by="reflection",
            )

        # (6) Memoria semántica kind='reflection' (ADR 0077: protegida del olvido).
        await _persist_reflection_memory(
            sessionmaker,
            owner_user_id=owner_user_id,
            tenant_id=tenant_id,
            narrative=new_state.get("narrative", ""),
            summary=summary,
        )

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
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, str | None] | None:
    """Llama al LLM y parsea el JSON → ``(narrative, traits, baseline, summary)``.

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
            max_tokens=512,
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
    return (
        "Identidad actual del córtex:\n"
        f"  Nombre: {name}\n"
        f"  Valores: {values}\n"
        f"  Narrativa: {narrative}\n"
        f"  Traits: {json.dumps(current_state.get('traits', {}))}\n"
        f"  Baseline de ánimo: {json.dumps(current_state.get('mood_baseline', {}))}\n\n"
        "Turnos recientes (más antiguos primero):\n"
        f"{convo}\n\n"
        "Devuelve SÓLO el JSON de la narrativa reescrita + el ajuste de traits/baseline."
    )


def _parse_proposal(
    content: str,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None, str | None] | None:
    """Parsea el JSON de la reflexión. ``None`` si no es JSON válido.

    Tolerante: extrae el primer objeto ``{...}`` balanceado del texto (algunos
    modelos locales envuelven el JSON en prosa). Un objeto sin NINGUNO de los
    campos útiles (narrative/traits/mood_baseline) se trata como no-op (``None``)."""
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

    # Nada útil que aplicar ⇒ no-op (no versionamos por un objeto vacío).
    if narrative_str is None and traits is None and baseline is None:
        return None
    return narrative_str, traits, baseline, summary_str


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
    sessionmaker: async_sessionmaker[Any], owner_user_id: UUID
) -> tuple[list[tuple[str, str]], UUID | None]:
    """Los ``_RECENT_TURNS_LIMIT`` turnos más recientes del owner (orden cronológico).

    Filtro ``owner_user_id`` explícito (tablas tenant-less sin RLS, ADR 0074). El
    ``tenant_id`` (para la memoria) sale del hilo del turno más reciente."""
    async with sessionmaker() as session:
        stmt = (
            select(CortexTurn.role, CortexTurn.content, CortexTurn.conversation_id)
            .where(CortexTurn.owner_user_id == owner_user_id)
            .order_by(CortexTurn.created_at.desc(), CortexTurn.id.desc())
            .limit(_RECENT_TURNS_LIMIT)
        )
        rows = list((await session.execute(stmt)).all())
        if not rows:
            return [], None
        # tenant del hilo del turno más reciente (defensa: filtro owner explícito).
        latest_conv_id = rows[0].conversation_id
        conv = await session.get(CortexConversation, latest_conv_id)
        tenant_id = (
            conv.tenant_id if conv is not None and conv.owner_user_id == owner_user_id else None
        )

    rows.reverse()  # cronológico (más antiguo primero) para la síntesis.
    turns = [(r.role, r.content) for r in rows]
    return turns, tenant_id


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
    "cortex_reflect",
    "trigger_cortex_reflection",
]
