"""Córtex F5 — mapeo PURO afecto → prosodia de voz (ADR 0073 voz + ADR 0075 afecto).

El WS de voz del córtex (``/ws/owner/cortex/voice``) sintetiza cada respuesta con
Kokoro modulando la **velocidad** según el estado afectivo vigente del córtex: un
``arousal`` alto ⇒ habla algo más rápida; un ``arousal`` bajo ⇒ pausada. El
``valence`` entra como matiz secundario (positivo acelera un pelín, negativo
ralentiza), siempre **dentro de la banda clampeada** para que la voz nunca suene
ni catatónica ni atropellada.

:func:`voice_params_from_affect` es la única costura: PURA (sin I/O, sin LLM,
sin reloj), determinista y auditable (ADR 0075 §4/§6 — dinámica afectiva
determinista, NO sentimientos reales). El WS la invoca con el afecto leído de la
caché/BD y la voz elegida del allowlist, y reenvía el ``speed`` resultante a
``HttpTextToSpeech.synthesize(speed=...)``.

Diseño del mapeo (documentado como desviación calibrable):

    speed = clamp(BASE + AROUSAL_GAIN*arousal + VALENCE_GAIN*valence,
                  SPEED_MIN, SPEED_MAX)

con ``BASE=0.85``, ``AROUSAL_GAIN=0.40`` (arousal ∈ [0,1] ⇒ aporta hasta +0.40,
de 0.85 a 1.25, los extremos exactos de la banda) y ``VALENCE_GAIN=0.05``
(valence ∈ [-1,1] ⇒ ±0.05, un matiz, nunca el factor dominante). La banda
``[0.85, 1.25]`` es la zona "natural" de Kokoro (el parámetro ``speed`` de
``/v1/audio/speech``). La voz es **passthrough**: la autoridad del allowlist M/F
vive en el WS (``_resolve_voice``), aquí sólo se reenvía la elegida.
"""

from __future__ import annotations

from typing import TypedDict

from api_server.cortex.affective import AffectState

#: Banda natural de velocidad de Kokoro. Fuera de aquí la voz suena artificial.
SPEED_MIN: float = 0.85
SPEED_MAX: float = 1.25

#: Velocidad base (arousal=0, valence=0) — coincide con :data:`SPEED_MIN`.
_BASE_SPEED: float = 0.85
#: Ganancia del arousal: cubre exactamente la banda (0.85 → 1.25 en [0,1]).
_AROUSAL_GAIN: float = 0.40
#: Ganancia del valence: matiz secundario (±0.05), nunca domina sobre el arousal.
_VALENCE_GAIN: float = 0.05


class VoiceParams(TypedDict):
    """Parámetros de síntesis de un turno de voz: voz Kokoro + velocidad."""

    voice: str
    speed: float


def _clamp(x: float, lo: float, hi: float) -> float:
    """Recorta ``x`` a ``[lo, hi]`` (nunca lanza)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def arousal_to_speed(arousal: float, *, valence: float = 0.0) -> float:
    """Velocidad de habla a partir del ``arousal`` (con ``valence`` de matiz).

    Función pura y determinista (sin I/O ni reloj): mapeo afín de ``arousal``
    sobre la banda :data:`SPEED_MIN`..:data:`SPEED_MAX`, con un empujón secundario
    de ``valence``, recortado duro a la banda. Monótona en ``arousal``. Tolera
    valores fuera de rango (se clampan vía la banda de salida)."""
    raw = _BASE_SPEED + _AROUSAL_GAIN * arousal + _VALENCE_GAIN * valence
    return _clamp(raw, SPEED_MIN, SPEED_MAX)


def voice_params_from_affect(affect: AffectState, *, voice: str) -> VoiceParams:
    """Parámetros de síntesis Kokoro para el afecto vigente del córtex.

    ``speed`` se deriva del ``arousal`` de la EMOCIÓN viva (la capa rápida; con
    ``valence`` de matiz) vía :func:`arousal_to_speed`. ``voice`` es passthrough
    de la voz ya validada por el allowlist del WS. Determinista y puro: dado el
    mismo ``affect`` y ``voice`` devuelve siempre lo mismo, así que el WS sólo lo
    invoca y reenvía el ``speed`` a la TTS (ADR 0075 §6: modelo computacional,
    NO sentimientos reales)."""
    speed = arousal_to_speed(affect.emotion.arousal, valence=affect.emotion.valence)
    return VoiceParams(voice=voice, speed=speed)


__all__ = [
    "SPEED_MAX",
    "SPEED_MIN",
    "VoiceParams",
    "arousal_to_speed",
    "voice_params_from_affect",
]
