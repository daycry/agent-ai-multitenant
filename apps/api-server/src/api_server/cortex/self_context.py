"""Córtex — self-context unificado: el "yo" completo en UN solo prompt.

Hasta ahora cada superficie (chat, voz) componía su propio prompt a trozos:
preámbulo de identidad + recall + augment, duplicando el bloque y dejando fuera
todo lo demás (afecto, traits, relationship_model, learnings). Este módulo es
la ÚNICA costura: compone "quién soy (nombre/valores/narrativa/rasgos) + cómo
estoy (PAD/drives vivos) + qué sé (recall) + qué sé de ti (relationship_model)
+ qué aprendí y quiero contarte (learnings pendientes)".

Separación estricta composición/IO:

- La COMPOSICIÓN (:func:`compose_self_context_prompt`,
  :func:`trait_style_guidance`) es 100 % pura — testeable sin red/BD.
- La CARGA (:func:`load_self_context`, fase de cableado) vive aparte y es la
  única que toca sesión/Redis.

Decisión de seguridad (anti-inyección): dentro de los marcadores
``<<<DATOS>>>`` va TODO lo derivable de entradas del owner/web vía LLM
(nombre, valores, narrativa, relationship_model, digests de learnings) — dato,
nunca instrucción (mismo blindaje que la memoria). FUERA de los marcadores va
SOLO el copy que genera este código puro desde floats clampeados (guía de tono
y de estilo), que no es inyectable.

> Honestidad (ADR 0075 §6): la guía se rotula como derivada de un estado
> afectivo **simulado** (modelo computacional, no consciencia).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.cortex.affect_cache import read_affect_state
from api_server.cortex.affect_policy import tone_guidance
from api_server.cortex.affect_store import load_affect_state
from api_server.cortex.affective import AffectState, Language, neutral_affect_state
from api_server.cortex.identity import ensure_identity, identity_preamble
from api_server.cortex.memory import CORTEX_RECALL_LIMIT, augment_cortex_prompt, cortex_recall

_log = structlog.get_logger("api_server.cortex.self_context")

# =============================================================================
# Constantes de composición (calibrables)
# =============================================================================
#: Banda de los traits Big-Five: fuera de [TRAIT_LOW, TRAIT_HIGH] emiten guía.
TRAIT_LOW: float = 0.35
TRAIT_HIGH: float = 0.65
#: Truncado de valores derivados (digests, relationship_model) en el prompt.
FACT_TRUNCATE_LEN: int = 280


@dataclass(frozen=True)
class PendingLearning:
    """Un aprendizaje de curiosidad pendiente de contar al owner (ADR 0078).

    ``digest`` es el contenido de la memoria ``kind='learning'`` del pursuit
    (o ``""`` si no fue resoluble) — texto derivado de la web vía LLM ⇒ SIEMPRE
    dato blindado, nunca instrucción.
    """

    pursuit_id: UUID
    topic: str
    digest: str


@dataclass(frozen=True)
class SelfContext:
    """El self-model completo de un turno, ya cargado (sin I/O pendiente)."""

    identity_state: dict[str, Any]
    affect: AffectState
    known_facts: list[str]
    pending_learnings: tuple[PendingLearning, ...] = ()


def _truncate(text: str, limit: int = FACT_TRUNCATE_LEN) -> str:
    """Trunca a ``limit`` chars con elipsis (los datos no engordan el prompt)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _language_of(identity_state: dict[str, Any]) -> Language:
    return "en" if identity_state.get("language") == "en" else "es"


# =============================================================================
# Guía de estilo por traits Big-Five (pura; banda neutra silenciosa)
# =============================================================================
# trait → ((banda_baja_es, banda_baja_en), (banda_alta_es, banda_alta_en))
_TRAIT_STYLE: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "openness": (
        (
            "Sé concreto y pragmático; no divagues.",
            "Be concrete and pragmatic; don't ramble.",
        ),
        (
            "Explora ideas y alternativas con curiosidad.",
            "Explore ideas and alternatives with curiosity.",
        ),
    ),
    "conscientiousness": (
        (
            "Sé flexible y ligero, sin exceso de formalismo.",
            "Be flexible and light, without excess formality.",
        ),
        ("Sé meticuloso y estructurado.", "Be meticulous and structured."),
    ),
    "extraversion": (
        ("Sé contenido y ve al grano.", "Be reserved and get to the point."),
        ("Sé expresivo y conversacional.", "Be expressive and conversational."),
    ),
    "agreeableness": (
        (
            "Sé directo y franco, incluso al discrepar.",
            "Be direct and frank, even when disagreeing.",
        ),
        ("Sé cooperativo y empático.", "Be cooperative and empathetic."),
    ),
    "neuroticism": (
        ("Mantén un tono sereno y estable.", "Keep a calm, steady tone."),
        (
            "Señala riesgos e incertidumbres con prudencia.",
            "Point out risks and uncertainties with caution.",
        ),
    ),
}


def trait_style_guidance(
    traits: dict[str, Any] | None, *, language: Language = "es"
) -> tuple[str, ...]:
    """Guía de estilo derivada de los Big-Five (bandas; neutro ⇒ ``()``).

    Solo los traits FUERA de la banda neutra ``[TRAIT_LOW, TRAIT_HIGH]`` emiten
    una línea — copy honesto: no se finge un rasgo que no destaca. Un valor no
    numérico se ignora (nunca lanza). Puro y determinista.
    """
    idx = 0 if language == "es" else 1
    source = traits if isinstance(traits, dict) else {}
    lines: list[str] = []
    for name, (low_pair, high_pair) in _TRAIT_STYLE.items():
        raw = source.get(name)
        if isinstance(raw, bool) or raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > TRAIT_HIGH:
            lines.append(high_pair[idx])
        elif value < TRAIT_LOW:
            lines.append(low_pair[idx])
    return tuple(lines)


# =============================================================================
# Composición del prompt (pura)
# =============================================================================
_GUIDANCE_HEADER: dict[str, str] = {
    "es": (
        "Guía de conducta para este turno (derivada de tu estado afectivo "
        "simulado y tus rasgos — modelo computacional, no consciencia):"
    ),
    "en": (
        "Behavior guidance for this turn (derived from your simulated "
        "affective state and traits — computational model, not consciousness):"
    ),
}

_RELATIONSHIP_LABEL: dict[str, str] = {
    "es": "Lo que sé de mi owner",
    "en": "What I know about my owner",
}

_LEARNING_LABEL: dict[str, str] = {
    "es": "Tema que aprendí por curiosidad y quiero sacar en la conversación",
    "en": "Topic I learned out of curiosity and want to bring up",
}


def _extra_fact_lines(ctx: SelfContext, language: Language) -> list[str]:
    """Las líneas de dato ADICIONALES del self-model (van dentro de <<<DATOS>>>)."""
    lines: list[str] = []
    relationship = ctx.identity_state.get("relationship_model")
    if isinstance(relationship, dict):
        for key, value in relationship.items():
            key_text = str(key).strip()
            value_text = _truncate(str(value))
            if key_text and value_text:
                lines.append(f"{_RELATIONSHIP_LABEL[language]} — {key_text}: {value_text}")
    for learning in ctx.pending_learnings:
        topic = learning.topic.strip()
        if not topic:
            continue
        digest = _truncate(learning.digest)
        suffix = f" — {digest}" if digest else ""
        lines.append(f"{_LEARNING_LABEL[language]}: {topic}{suffix}")
    return lines


def compose_self_context_prompt(
    base_prompt: str, ctx: SelfContext, *, remember_enabled: bool
) -> str:
    """El system prompt completo del turno desde el self-model (puro).

    Estructura: [preámbulo de identidad ampliado (DATOS blindados: identidad +
    relationship_model + learnings)] + [guía de tono/estilo (fuera de DATOS,
    generada por código puro)] + [base_prompt] → todo pasa por
    :func:`augment_cortex_prompt` (el recall conserva su blindaje propio y se
    compone UNA sola vez).

    Con un contexto neutro (sin afecto/traits destacados, sin relationship ni
    learnings) degrada EXACTAMENTE al comportamiento previo: preámbulo + base +
    augment. Cero regresión.
    """
    identity_state = ctx.identity_state or {}
    language = _language_of(identity_state)

    preamble = identity_preamble(identity_state, extra_facts=_extra_fact_lines(ctx, language))
    guidance = tone_guidance(ctx.affect, language=language) + trait_style_guidance(
        identity_state.get("traits"), language=language
    )

    parts: list[str] = []
    if preamble:
        parts.append(preamble)
    if guidance:
        block = "\n".join(f"- {line}" for line in guidance)
        parts.append(f"{_GUIDANCE_HEADER[language]}\n{block}")
    parts.append(base_prompt)

    return augment_cortex_prompt(
        "\n\n".join(parts), known_facts=ctx.known_facts, remember_enabled=remember_enabled
    )


def self_context_meta(ctx: SelfContext, decision: Any) -> dict[str, Any]:
    """La metadata auditable del turno (puro): mood + decisión de effort + temas.

    ``decision`` es la :class:`~api_server.cortex.affect_policy.EffortDecision`
    del turno. Se persiste en ``cortex_turns.metadata_.self_context`` — la
    evidencia de que el self-model gobernó el turno (antes no quedaba rastro)."""
    language = _language_of(ctx.identity_state or {})
    return {
        "mood_label": ctx.affect.mood_label(language=language),
        "valence": ctx.affect.emotion.valence,
        "arousal": ctx.affect.emotion.arousal,
        "effort_base": decision.base,
        "effort_effective": decision.effective,
        "effort_reasons": list(decision.reasons),
        "surfaced_pursuits": [str(p.pursuit_id) for p in ctx.pending_learnings],
    }


# =============================================================================
# Carga del self-context (la ÚNICA costura de I/O; la composición es pura)
# =============================================================================
async def _load_live_affect(
    session: AsyncSession,
    redis: Redis | None,
    owner_user_id: UUID,
    *,
    now: datetime,
) -> AffectState:
    """El afecto VIGENTE del owner: caché Redis (decay lazy) → BD → neutro.

    Mismo orden que ``GET /owner/cortex/mind`` y la voz. **Fail-open**: cualquier
    fallo cae al estado neutro — el afecto matiza el turno, nunca lo rompe."""
    if redis is not None:
        try:
            cached = await read_affect_state(redis, str(owner_user_id), now=now)
        except Exception as exc:  # caché best-effort
            _log.warning("cortex.self_context_affect_cache_failed", error=str(exc))
            cached = None
        if cached is not None:
            return cached
    try:
        return await load_affect_state(session, owner_user_id, now=now)
    except Exception as exc:  # fail-open
        _log.warning("cortex.self_context_affect_db_failed", error=str(exc))
        return neutral_affect_state()


async def _load_pending_learnings(
    session: AsyncSession,  # noqa: ARG001 — la fase de surfacing los usa
    *,
    owner_user_id: UUID,  # noqa: ARG001
) -> tuple[PendingLearning, ...]:
    """Aprendizajes de curiosidad pendientes de contar (surfacing, ADR 0078).

    Se activa con la fase de surfacing (pursuits ``digested`` sin ``surfaced_at``);
    hasta entonces no hay nada que abrir."""
    return ()


async def load_self_context(
    session: AsyncSession,
    redis: Redis | None,
    *,
    owner_user_id: UUID,
    tenant_id: UUID,
    query: str,
    now: datetime,
    affect: AffectState | None = None,
    recall_limit: int = CORTEX_RECALL_LIMIT,
) -> SelfContext:
    """Carga el self-model completo del turno (identidad + afecto + recall + temas).

    - Identidad: ``ensure_identity`` (crea la default si no existe).
    - Afecto: el ``affect`` ya cargado si el caller lo tiene (la voz lo lee antes
      para la prosodia); si no, caché Redis → BD → neutro (fail-open).
    - Recall: el híbrido de F1 corre AQUÍ y solo aquí (el caller ya no lo llama).
    - Learnings pendientes: pursuits ``digested`` sin surfacear (fase surfacing).

    Aislamiento (ADR 0074): todo acceso filtra ``owner_user_id`` explícito — la
    identidad por su UNIQUE, el afecto por clave/filtro, el recall por
    ``user_id=owner`` + ``scope='private'``.
    """
    identity = await ensure_identity(session, owner_user_id)
    if affect is None:
        affect = await _load_live_affect(session, redis, owner_user_id, now=now)
    known_facts = await cortex_recall(
        session,
        owner_user_id=owner_user_id,
        tenant_id=tenant_id,
        query=query,
        limit=recall_limit,
    )
    learnings = await _load_pending_learnings(session, owner_user_id=owner_user_id)
    return SelfContext(
        identity_state=dict(identity.identity_state or {}),
        affect=affect,
        known_facts=known_facts,
        pending_learnings=learnings,
    )


__all__ = [
    "FACT_TRUNCATE_LEN",
    "TRAIT_HIGH",
    "TRAIT_LOW",
    "PendingLearning",
    "SelfContext",
    "compose_self_context_prompt",
    "load_self_context",
    "self_context_meta",
    "trait_style_guidance",
]
