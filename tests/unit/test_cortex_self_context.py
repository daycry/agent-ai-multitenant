"""Córtex — self-context unificado (composición pura del "yo" en el prompt).

Cubre la capa PURA de ``cortex/self_context.py``:

  * ``trait_style_guidance`` — bandas Big-Five (<0.35 / >0.65) → guía de estilo;
    la banda neutra no emite nada (sin fingir rasgos que no destacan).
  * ``compose_self_context_prompt`` — UN solo prompt que compone: identidad
    (nombre/valores/narrativa) + "lo que sé de mi owner" (relationship_model) +
    learnings pendientes de contar, TODO dentro de ``<<<DATOS>>>`` (derivable de
    entradas del owner/web ⇒ dato, nunca instrucción); y las guías de tono/estilo
    FUERA de los marcadores (copy generado por nuestro código puro desde floats
    clampeados). Cierra con el augment de recall existente (una sola vez).

Regla de oro: con un contexto neutro (sin afecto destacado, traits neutros, sin
relationship/learnings) la composición degrada EXACTAMENTE al comportamiento
actual (preámbulo + base + augment) — cero regresión.
"""

from __future__ import annotations

import re
from uuid import uuid4

from api_server.cortex.affective import (
    AffectState,
    Drives,
    PADState,
    neutral_affect_state,
)
from api_server.cortex.identity import default_identity_state, identity_preamble
from api_server.cortex.memory import augment_cortex_prompt
from api_server.cortex.self_context import (
    PendingLearning,
    SelfContext,
    compose_self_context_prompt,
    trait_style_guidance,
)

_BASE = "Eres el córtex del system_owner."


def _datos_sections(prompt: str) -> list[str]:
    """El contenido de cada bloque ``<<<DATOS>>>…<<<FIN DATOS>>>`` del prompt."""
    return re.findall(r"<<<DATOS>>>(.*?)<<<FIN DATOS>>>", prompt, re.S)


def _ctx(
    *,
    identity_state: dict | None = None,
    affect: AffectState | None = None,
    known_facts: list[str] | None = None,
    learnings: tuple[PendingLearning, ...] = (),
) -> SelfContext:
    return SelfContext(
        identity_state=identity_state if identity_state is not None else default_identity_state(),
        affect=affect if affect is not None else neutral_affect_state(),
        known_facts=known_facts or [],
        pending_learnings=learnings,
    )


def _excited_affect() -> AffectState:
    return AffectState(
        emotion=PADState(valence=0.5, arousal=0.7, dominance=0.4, intensity=0.4),
        mood=PADState(valence=0.5, arousal=0.7, dominance=0.4),
        drives=Drives(curiosity=0.8, bonding=0.8, coherence=0.5, competence=0.5),
    )


# ---------------------------------------------------------------------------
# trait_style_guidance — bandas Big-Five → estilo; banda neutra silenciosa
# ---------------------------------------------------------------------------
def test_traits_neutros_no_emiten_nada() -> None:
    neutral = dict.fromkeys(
        ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"), 0.5
    )
    assert trait_style_guidance(neutral) == ()


def test_openness_alta_emite_exploracion() -> None:
    lines = trait_style_guidance({"openness": 0.9})
    assert any("explora" in line.lower() for line in lines)


def test_agreeableness_baja_emite_franqueza() -> None:
    lines = trait_style_guidance({"agreeableness": 0.2})
    assert any("directo" in line.lower() for line in lines)


def test_traits_bilingue() -> None:
    lines = trait_style_guidance({"openness": 0.9}, language="en")
    assert lines
    assert any("explore" in line.lower() for line in lines)


# ---------------------------------------------------------------------------
# compose_self_context_prompt — degradación exacta con contexto neutro
# ---------------------------------------------------------------------------
def test_ctx_neutro_degrada_al_comportamiento_actual() -> None:
    state = default_identity_state()
    ctx = _ctx(identity_state=state, known_facts=["al owner le gusta el TDD"])
    prompt = compose_self_context_prompt(_BASE, ctx, remember_enabled=True)

    expected_base = f"{identity_preamble(state)}\n\n{_BASE}"
    expected = augment_cortex_prompt(
        expected_base, known_facts=["al owner le gusta el TDD"], remember_enabled=True
    )
    assert prompt == expected


# ---------------------------------------------------------------------------
# compose_self_context_prompt — relationship_model y learnings DENTRO de DATOS
# ---------------------------------------------------------------------------
def test_relationship_model_va_dentro_de_datos() -> None:
    state = default_identity_state()
    state["relationship_model"] = {"prefiere": "respuestas directas y con evidencia"}
    prompt = compose_self_context_prompt(_BASE, _ctx(identity_state=state), remember_enabled=True)

    sections = _datos_sections(prompt)
    assert any("respuestas directas y con evidencia" in s for s in sections)


def test_learnings_pendientes_van_dentro_de_datos() -> None:
    learning = PendingLearning(
        pursuit_id=uuid4(),
        topic="arquitectura hexagonal",
        digest="La arquitectura hexagonal separa dominio de adaptadores.",
    )
    prompt = compose_self_context_prompt(_BASE, _ctx(learnings=(learning,)), remember_enabled=True)
    sections = _datos_sections(prompt)
    assert any("arquitectura hexagonal" in s for s in sections)
    assert any("separa dominio de adaptadores" in s for s in sections)


def test_digest_de_learning_se_trunca() -> None:
    learning = PendingLearning(pursuit_id=uuid4(), topic="tema", digest="x" * 500)
    prompt = compose_self_context_prompt(_BASE, _ctx(learnings=(learning,)), remember_enabled=True)
    assert "x" * 500 not in prompt
    assert "x" * 100 in prompt  # está, pero acotado


# ---------------------------------------------------------------------------
# compose_self_context_prompt — guías FUERA de los marcadores + copy honesto
# ---------------------------------------------------------------------------
def test_guias_de_tono_y_estilo_van_fuera_de_datos() -> None:
    state = default_identity_state()
    state["traits"]["openness"] = 0.9
    prompt = compose_self_context_prompt(
        _BASE, _ctx(identity_state=state, affect=_excited_affect()), remember_enabled=True
    )

    guidance_lines = [
        line
        for line in prompt.splitlines()
        if "cálido" in line.lower() or "explora" in line.lower()
    ]
    assert guidance_lines, "las guías de tono/estilo deben estar en el prompt"
    for section in _datos_sections(prompt):
        assert "cálido" not in section.lower()
        assert "explora" not in section.lower()


def test_guias_llevan_copy_honesto() -> None:
    prompt = compose_self_context_prompt(
        _BASE, _ctx(affect=_excited_affect()), remember_enabled=True
    )
    assert "simulado" in prompt.lower()


def test_sin_afecto_destacado_no_hay_bloque_de_guia() -> None:
    prompt = compose_self_context_prompt(_BASE, _ctx(), remember_enabled=True)
    assert "simulado" not in prompt.lower()


# ---------------------------------------------------------------------------
# C3 (investigación córtex 2026-07-11): conciencia temporal. El córtex no sabía
# qué día/hora es ni cuánto hacía que no hablaba con su owner — el paso del
# tiempo solo existía como decay de floats, invisible como "hecho".
# ---------------------------------------------------------------------------
def test_temporal_lines_carry_date_and_reunion_gap() -> None:
    from datetime import UTC, datetime

    from api_server.cortex.self_context import temporal_context_lines

    now = datetime(2026, 7, 12, 9, 30, tzinfo=UTC)
    last = datetime(2026, 7, 9, 18, 0, tzinfo=UTC)
    lines = temporal_context_lines(now, last, language="es")
    joined = " ".join(lines)
    assert "2026" in joined and ("julio" in joined or "07" in joined)
    assert "2 día" in joined  # ~2.6 días → floor honesto: «hace 2 día(s)»


def test_temporal_lines_recent_turn_reads_as_continuation() -> None:
    from datetime import UTC, datetime, timedelta

    from api_server.cortex.self_context import temporal_context_lines

    now = datetime(2026, 7, 12, 9, 30, tzinfo=UTC)
    lines = temporal_context_lines(now, now - timedelta(minutes=5), language="es")
    assert any("continu" in line.lower() for line in lines)


def test_temporal_lines_first_conversation() -> None:
    from datetime import UTC, datetime

    from api_server.cortex.self_context import temporal_context_lines

    lines = temporal_context_lines(datetime(2026, 7, 12, tzinfo=UTC), None, language="es")
    assert any("primera" in line.lower() for line in lines)


def test_compose_includes_temporal_context_outside_datos() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 7, 12, 9, 30, tzinfo=UTC)
    ctx = _ctx()
    ctx = SelfContext(
        identity_state=ctx.identity_state,
        affect=ctx.affect,
        known_facts=ctx.known_facts,
        pending_learnings=ctx.pending_learnings,
        now=now,
        last_turn_at=now - timedelta(days=2),
    )
    prompt = compose_self_context_prompt("BASE", ctx, remember_enabled=False)
    assert "2026" in prompt
    assert "2 día" in prompt
    # Generado por código puro, confiable → fuera del blindaje DATOS.
    assert all("2026" not in s for s in _datos_sections(prompt))


def test_compose_without_now_stays_untouched() -> None:
    prompt = compose_self_context_prompt("BASE", _ctx(), remember_enabled=False)
    assert "2026" not in prompt
