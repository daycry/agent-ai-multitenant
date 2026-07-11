"""Córtex — política afectiva pura: afecto → conducta (ADR 0075).

Mapeos DETERMINISTAS del estado afectivo a decisiones de conducta textual:

- :func:`modulate_reasoning_effort` — el afecto mueve el ``reasoning_effort``
  COMO MÁXIMO un paso por la escalera del kind (``REASONING_OPTIONS_BY_KIND``
  **sin** ``"off"``), con suelo duro en ``"low"``. El afecto **modula, NUNCA
  bloquea** (ADR 0075): no puede apagar ni encender el razonamiento, y un kind
  desconocido o una base fuera de escalera son no-op auditables (los dobles de
  test sin ``provider_kind`` caen ahí limpiamente).
- :func:`tone_guidance` — bandas PAD/drives → guía de tono para el system
  prompt; la banda neutra no emite nada (copy honesto: sin fingir estados que
  no destacan).

Código 100 % puro: sin I/O, sin reloj, sin LLM. Los umbrales son constantes
module-level **calibrables** (desviación documentada de ADR 0075 §4/§7, igual
que las half-lives del motor).
"""

from __future__ import annotations

from dataclasses import dataclass

from api_server.cortex.affective import AffectState, Language
from api_server.db.llm_providers import REASONING_OPTIONS_BY_KIND

# =============================================================================
# Umbrales de calibración (afecto → effort / tono)
# =============================================================================
#: Sube 1 paso de effort si hay un evento afectivo fuerte y reciente.
EFFORT_UP_AROUSAL: float = 0.65
EFFORT_UP_INTENSITY: float = 0.25
#: Baja 1 paso de effort en estado apagado (arousal y curiosidad por los suelos).
EFFORT_DOWN_AROUSAL: float = 0.15
EFFORT_DOWN_CURIOSITY: float = 0.20
#: Suelo duro de la modulación: el afecto nunca baja el effort de "low".
EFFORT_FLOOR: str = "low"

#: Bandas de la guía de tono (la zona neutra central no emite nada).
TONE_VALENCE_BAND: float = 0.25
TONE_AROUSAL_HIGH: float = 0.5
TONE_AROUSAL_LOW: float = 0.3
TONE_DOMINANCE_BAND: float = 0.3
TONE_DRIVE_HIGH: float = 0.7
# C9: umbral de "todos los drives por los suelos" para el aburrimiento.
TONE_DRIVE_BORED: float = 0.15


@dataclass(frozen=True)
class EffortDecision:
    """Resultado auditable de la modulación de effort.

    ``base`` es el effort resuelto del modelo; ``effective`` el effort tras la
    modulación (idéntico a ``base`` cuando ninguna regla dispara); ``reasons``
    explica la decisión (se persiste en la metadata del turno).
    """

    base: str | None
    effective: str | None
    reasons: tuple[str, ...]


def _ladder_for(kind: str | None) -> tuple[str, ...]:
    """La escalera de efforts del kind, SIN ``"off"`` (el afecto no apaga nada)."""
    options = REASONING_OPTIONS_BY_KIND.get(kind or "", ())
    return tuple(option for option in options if option != "off")


def modulate_reasoning_effort(
    base_effort: str | None, kind: str | None, affect: AffectState
) -> EffortDecision:
    """Modula el ``reasoning_effort`` según el estado afectivo (±1 paso, acotado).

    Reglas (mutuamente excluyentes por construcción — las bandas de arousal no
    solapan):

    - **Subir 1 paso** si ``arousal >= EFFORT_UP_AROUSAL`` y la ``intensity``
      indica un evento afectivo reciente (``>= EFFORT_UP_INTENSITY``).
    - **Bajar 1 paso** si ``arousal <= EFFORT_DOWN_AROUSAL`` y el drive
      ``curiosity`` está apagado (``<= EFFORT_DOWN_CURIOSITY``). Suelo duro:
      nunca por debajo de :data:`EFFORT_FLOOR`.

    Sin base (``None``) o base fuera de la escalera del kind ⇒ no-op auditable
    (``no_base`` / ``no_ladder``). Determinista.
    """
    if base_effort is None:
        return EffortDecision(base=None, effective=None, reasons=("no_base",))

    ladder = _ladder_for(kind)
    if base_effort not in ladder:
        return EffortDecision(base=base_effort, effective=base_effort, reasons=("no_ladder",))

    emotion = affect.emotion
    delta = 0
    reasons: tuple[str, ...] = ()
    if emotion.arousal >= EFFORT_UP_AROUSAL and emotion.intensity >= EFFORT_UP_INTENSITY:
        delta = 1
        reasons = (
            f"arousal_high:{emotion.arousal:.2f}",
            f"intensity:{emotion.intensity:.2f}",
        )
    elif (
        emotion.arousal <= EFFORT_DOWN_AROUSAL and affect.drives.curiosity <= EFFORT_DOWN_CURIOSITY
    ):
        delta = -1
        reasons = (
            f"arousal_low:{emotion.arousal:.2f}",
            f"curiosity_low:{affect.drives.curiosity:.2f}",
        )

    if delta == 0:
        return EffortDecision(base=base_effort, effective=base_effort, reasons=())

    floor_index = ladder.index(EFFORT_FLOOR) if EFFORT_FLOOR in ladder else 0
    current_index = ladder.index(base_effort)
    new_index = max(floor_index, min(len(ladder) - 1, current_index + delta))
    return EffortDecision(base=base_effort, effective=ladder[new_index], reasons=reasons)


# =============================================================================
# Guía de tono — bandas PAD/drives → líneas de guía (banda neutra silenciosa)
# =============================================================================
# Cada entrada: (condición evaluada en tone_guidance) → (línea_es, línea_en).
def tone_guidance(affect: AffectState, *, language: Language = "es") -> tuple[str, ...]:
    """Guía de tono derivada del estado afectivo (bandas; neutro ⇒ ``()``).

    El texto lo genera ESTE código puro desde floats clampeados — por eso puede
    vivir FUERA de los marcadores ``<<<DATOS>>>`` del prompt (no es derivable de
    entradas del owner/web). El copy honesto ("estado afectivo simulado") lo
    añade el composer al rotular el bloque.
    """
    idx = 0 if language == "es" else 1
    emotion = affect.emotion
    drives = affect.drives
    lines: list[tuple[str, str]] = []

    if emotion.valence >= TONE_VALENCE_BAND:
        lines.append(("Usa un tono cálido y positivo.", "Use a warm, upbeat tone."))
    elif emotion.valence <= -TONE_VALENCE_BAND:
        lines.append(("Usa un tono sobrio y cuidadoso.", "Use a sober, careful tone."))

    if emotion.arousal >= TONE_AROUSAL_HIGH:
        lines.append(("Ritmo enérgico y directo.", "Energetic, direct pace."))
    elif emotion.arousal < TONE_AROUSAL_LOW:
        lines.append(("Ritmo pausado y sereno.", "Calm, unhurried pace."))

    if emotion.dominance >= TONE_DOMINANCE_BAND:
        lines.append(("Propón con seguridad.", "Propose with confidence."))
    elif emotion.dominance <= -TONE_DOMINANCE_BAND:
        lines.append(
            ("Sé tentativo: pregunta antes de asumir.", "Be tentative: ask before assuming.")
        )

    if drives.curiosity >= TONE_DRIVE_HIGH:
        lines.append(
            (
                "Muestra interés con una pregunta de seguimiento pertinente.",
                "Show interest with a relevant follow-up question.",
            )
        )
    if drives.bonding >= TONE_DRIVE_HIGH:
        lines.append(("Usa un tono cercano y personal.", "Use a close, personal tone."))

    # C9 (investigación 2026-07-11): aburrimiento — arousal bajo Y todos los
    # drives por los suelos. Un estado humano básico que faltaba; además es la
    # señal latente que alimenta la iniciativa proactiva (C1).
    if emotion.arousal < TONE_AROUSAL_LOW and all(
        d <= TONE_DRIVE_BORED
        for d in (drives.curiosity, drives.bonding, drives.coherence, drives.competence)
    ):
        lines.append(
            (
                "Estás algo aburrido: si encaja, propón un tema o pregunta algo "
                "que te interese de verdad.",
                "You are somewhat bored: if it fits, propose a topic or ask "
                "about something you genuinely wonder about.",
            )
        )

    return tuple(pair[idx] for pair in lines)


__all__ = [
    "EFFORT_DOWN_AROUSAL",
    "EFFORT_DOWN_CURIOSITY",
    "EFFORT_FLOOR",
    "EFFORT_UP_AROUSAL",
    "EFFORT_UP_INTENSITY",
    "TONE_AROUSAL_HIGH",
    "TONE_AROUSAL_LOW",
    "TONE_DOMINANCE_BAND",
    "TONE_DRIVE_BORED",
    "TONE_DRIVE_HIGH",
    "TONE_VALENCE_BAND",
    "EffortDecision",
    "modulate_reasoning_effort",
    "tone_guidance",
]
