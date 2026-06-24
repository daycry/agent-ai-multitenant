"""Córtex F2 — motor afectivo PAD determinista (ADR 0075).

Código **puro**, FUERA del LLM y de cualquier I/O: estado afectivo continuo,
determinista y auditable que el córtex usa como simulación de su disposición.

> Honestidad (ADR 0075 §6): esto es un **modelo computacional de afecto, NO
> sentimientos reales**. El estado PAD y sus etiquetas son una simulación
> determinista para modular tono / `reasoning_effort` y para graficar; nunca
> afirman consciencia ni emoción genuina.

Modelo (ADR 0075):

- **PAD dimensional** (Mehrabian-Russell): ``valence ∈ [-1,1]``,
  ``arousal ∈ [0,1]``, ``dominance ∈ [-1,1]``, ``intensity ∈ [0,1]``. La
  etiqueta categórica (alegría/calma/…) es **derivada SOLO para UI**, jamás
  fuente de verdad (§1).
- **Tres capas temporales** (§2): *emoción* (decae al baseline en minutos),
  *mood* (EWMA lento, snapshots a PostgreSQL) y *drives* homeostáticos
  (``curiosity/bonding/coherence/competence ∈ [0,1]`` que decaen y se sacian).
- **Dinámica determinista** (§4): decay lazy en lectura hacia el baseline
  (homeostasis), update por evento (aplica un delta PAD), EWMA del mood, clamps
  duros y piso/techo de mood (evita "depresión/manía" simuladas).

**Determinismo**: NINGUNA función usa el reloj real ni aleatoriedad. El tiempo
entra siempre como parámetro (``elapsed_s``), de modo que la dinámica es 100%
reproducible y testeable (suite de calibración, §7).

Constantes de calibración: el ADR 0075 fija ``MOOD_EWMA_ALPHA=0.98`` y los
rangos; el resto (half-lives, banda de temperamento, ganancia de intensidad) se
elige aquí con valores razonables y se documenta como **desviación calibrable**
(ver el reporte de F2). Todas son operator-tunables a futuro sin tocar la forma
de las fórmulas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

# =============================================================================
# Constantes de dinámica (calibración — ADR 0075 §4/§7)
# =============================================================================
#: Vida media del decay de la EMOCIÓN hacia el baseline. ADR §2: "emoción
#: (Redis, minutos, decae al baseline)" → 10 min es un punto medio razonable.
DECAY_HALF_LIFE_S: float = 600.0

#: alpha del EWMA del mood (ADR 0075, plan F2): mood lento, casi-temperamento.
MOOD_EWMA_ALPHA: float = 0.98

#: Vida media del decay de los DRIVES hacia 0 (motor de la curiosidad: un drive
#: bajo motivará el bucle de fondo en F4). ~3 h → tras 6 h ya cae a ~1/4.
DRIVE_DECAY_HALF_LIFE_S: float = 3.0 * 3600.0

#: Piso/techo de la banda de TEMPERAMENTO del mood (ADR §4: evita "depresión/
#: manía" simuladas). El mood nunca satura en los extremos de la emoción.
MOOD_FLOOR: float = -0.6
MOOD_CEIL: float = 0.6

#: Clamp del set-point (baseline) por reflexión — lo usará F3 (no aplicado aquí,
#: expuesto para que la capa de reflexión no reinvente la cota).
BASELINE_MAX_DELTA_PER_REFLECTION: float = 0.05

#: Ganancia de la intensidad respecto a la magnitud del delta del evento.
INTENSITY_GAIN: float = 1.0


def _clamp(x: float, lo: float, hi: float) -> float:
    """Recorta ``x`` al intervalo ``[lo, hi]`` (nunca lanza)."""
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _decay_factor(elapsed_s: float, half_life_s: float) -> float:
    """Factor exponencial ``0.5 ** (elapsed / half_life)`` en ``[0, 1]``.

    ``elapsed_s <= 0`` ⇒ 1.0 (sin decay). Determinista.
    """
    if elapsed_s <= 0.0 or half_life_s <= 0.0:
        return 1.0
    return math.pow(0.5, elapsed_s / half_life_s)


# =============================================================================
# PADState — punto en el espacio afectivo (emoción O mood)
# =============================================================================
@dataclass(frozen=True)
class PADState:
    """Un punto PAD. Clampa cada eje a su rango **por construcción** (§1).

    ``valence/dominance ∈ [-1, 1]``; ``arousal/intensity ∈ [0, 1]``. Inmutable:
    cada transformación devuelve un nuevo :class:`PADState`.
    """

    valence: float
    arousal: float
    dominance: float
    intensity: float = 0.0

    def __post_init__(self) -> None:
        # frozen=True ⇒ usar object.__setattr__ para clampar en el constructor.
        object.__setattr__(self, "valence", _clamp(self.valence, -1.0, 1.0))
        object.__setattr__(self, "arousal", _clamp(self.arousal, 0.0, 1.0))
        object.__setattr__(self, "dominance", _clamp(self.dominance, -1.0, 1.0))
        object.__setattr__(self, "intensity", _clamp(self.intensity, 0.0, 1.0))


@dataclass(frozen=True)
class Drives:
    """Drives homeostáticos (§2). Cada uno ∈ ``[0, 1]``, clampeado por construcción.

    Decaen hacia 0 con el tiempo (un drive bajo motiva el bucle de fondo en F4)
    y se sacian con :func:`satisfy_drive`. Aquí son **estado observable**; su
    capacidad de DISPARAR comportamiento llega en F4.
    """

    curiosity: float
    bonding: float
    coherence: float
    competence: float

    def __post_init__(self) -> None:
        for name in ("curiosity", "bonding", "coherence", "competence"):
            object.__setattr__(self, name, _clamp(getattr(self, name), 0.0, 1.0))

    def as_dict(self) -> dict[str, float]:
        return {
            "curiosity": self.curiosity,
            "bonding": self.bonding,
            "coherence": self.coherence,
            "competence": self.competence,
        }

    @classmethod
    def from_mapping(cls, data: dict[str, float]) -> Drives:
        """Reconstruye desde un dict (JSONB del snapshot); claves faltantes → 0.5."""
        return cls(
            curiosity=float(data.get("curiosity", 0.5)),
            bonding=float(data.get("bonding", 0.5)),
            coherence=float(data.get("coherence", 0.5)),
            competence=float(data.get("competence", 0.5)),
        )


#: Baseline / set-point neutro del temperamento (ADR §4). Arousal baseline > 0
#: porque arousal vive en [0,1] y "calma despierta" ≈ 0.3, no 0 (catatónico).
BASELINE_PAD: PADState = PADState(valence=0.0, arousal=0.3, dominance=0.0, intensity=0.0)

#: Drives neutros: media saturación (margen para subir y bajar).
NEUTRAL_DRIVES: Drives = Drives(curiosity=0.5, bonding=0.5, coherence=0.5, competence=0.5)


Language = Literal["es", "en"]


# =============================================================================
# AffectState — estado afectivo completo (emoción + mood + drives)
# =============================================================================
@dataclass(frozen=True)
class AffectState:
    """Estado afectivo agregado: la emoción viva, el mood lento y los drives.

    El ``mood_label`` se DERIVA del mood (no se almacena como verdad); el
    snapshot persiste la etiqueta solo como conveniencia de UI.
    """

    emotion: PADState
    mood: PADState
    drives: Drives

    def mood_label(self, language: Language = "es") -> str:
        return derive_mood_label(self.mood, language=language)


def neutral_affect_state() -> AffectState:
    """Estado inicial neutro (lo que lee un owner sin snapshot previo)."""
    return AffectState(emotion=BASELINE_PAD, mood=BASELINE_PAD, drives=NEUTRAL_DRIVES)


# =============================================================================
# Dinámica determinista
# =============================================================================
def decay_emotion(state: PADState, baseline: PADState, *, elapsed_s: float) -> PADState:
    """Decae la emoción hacia el ``baseline`` (homeostasis), lazy en lectura.

    Decay exponencial por eje: ``x' = baseline + (x - baseline) * factor`` con
    ``factor = 0.5 ** (elapsed / half_life)``. Monótono hacia el baseline, nunca
    lo cruza. ``elapsed_s <= 0`` ⇒ identidad. La intensidad decae hacia 0.
    """
    factor = _decay_factor(elapsed_s, DECAY_HALF_LIFE_S)
    return PADState(
        valence=baseline.valence + (state.valence - baseline.valence) * factor,
        arousal=baseline.arousal + (state.arousal - baseline.arousal) * factor,
        dominance=baseline.dominance + (state.dominance - baseline.dominance) * factor,
        intensity=state.intensity * factor,
    )


def apply_event(state: PADState, delta: PADState) -> PADState:
    """Integra un ``delta`` PAD del distilador en la emoción (determinista).

    Suma el delta eje a eje (clampeado por el constructor de :class:`PADState`).
    La ``intensity`` **se acumula** con la magnitud del delta (norma L2 de
    valence/arousal/dominance, escalada por :data:`INTENSITY_GAIN` y clampeada a
    1.0): un evento fuerte sube la intensidad (saturándola), y un ``delta`` nulo
    (camino fail-open) la deja intacta — de modo que el estado entero no cambia.
    La intensidad luego decae con el tiempo vía :func:`decay_emotion`.
    """
    magnitude = math.sqrt(
        delta.valence * delta.valence
        + delta.arousal * delta.arousal
        + delta.dominance * delta.dominance
    )
    return PADState(
        valence=state.valence + delta.valence,
        arousal=state.arousal + delta.arousal,
        dominance=state.dominance + delta.dominance,
        intensity=state.intensity + magnitude * INTENSITY_GAIN,
    )


def update_mood(mood: PADState, emotion: PADState) -> PADState:
    """EWMA lento del mood y clamp a la banda de temperamento (§4).

    ``mood' = alpha*mood + (1-alpha)*emotion`` con ``alpha`` = :data:`MOOD_EWMA_ALPHA`. Luego
    valence/dominance se recortan a ``[MOOD_FLOOR, MOOD_CEIL]`` para que el mood
    nunca alcance los extremos de la emoción (sin "depresión/manía" simuladas).
    El mood NO arrastra intensity (es la capa lenta).
    """
    a = MOOD_EWMA_ALPHA
    return PADState(
        valence=_clamp(a * mood.valence + (1 - a) * emotion.valence, MOOD_FLOOR, MOOD_CEIL),
        arousal=a * mood.arousal + (1 - a) * emotion.arousal,
        dominance=_clamp(a * mood.dominance + (1 - a) * emotion.dominance, MOOD_FLOOR, MOOD_CEIL),
        intensity=0.0,
    )


def decay_drives(drives: Drives, *, elapsed_s: float) -> Drives:
    """Decae cada drive hacia 0 con el tiempo (homeostasis del deseo).

    Decay exponencial con :data:`DRIVE_DECAY_HALF_LIFE_S`. ``elapsed_s <= 0`` ⇒
    identidad. Un drive bajo motivará el bucle de fondo (F4); aquí es solo estado.
    """
    factor = _decay_factor(elapsed_s, DRIVE_DECAY_HALF_LIFE_S)
    return Drives(
        curiosity=drives.curiosity * factor,
        bonding=drives.bonding * factor,
        coherence=drives.coherence * factor,
        competence=drives.competence * factor,
    )


def satisfy_drive(drives: Drives, name: str, amount: float) -> Drives:
    """Sacia un drive sumando ``amount`` (clampeado a ``[0,1]``).

    Un ``name`` desconocido es **no-op** (devuelve los drives intactos).
    """
    if not hasattr(drives, name) or name not in (
        "curiosity",
        "bonding",
        "coherence",
        "competence",
    ):
        return drives
    current = getattr(drives, name)
    return replace(drives, **{name: _clamp(current + amount, 0.0, 1.0)})


# =============================================================================
# Etiqueta de mood — derivada del cuadrante PAD, SOLO-UI, bilingüe (§1)
# =============================================================================
#: Umbral de la zona neutra: |valence| por debajo de esto ⇒ "neutral".
_NEUTRAL_VALENCE_BAND: float = 0.25
#: Frontera de arousal alto/bajo (arousal vive en [0,1], centro ≈ baseline 0.3).
_AROUSAL_SPLIT: float = 0.5

# Mapa cuadrante PAD → (etiqueta_es, etiqueta_en). Derivado, no fuente de verdad.
_QUADRANT_LABELS: dict[tuple[bool, bool], tuple[str, str]] = {
    # (valence_positiva, arousal_alto)
    (True, True): ("alegría", "joy"),
    (True, False): ("calma", "calm"),
    (False, True): ("tensión", "tension"),
    (False, False): ("abatimiento", "down"),
}


def derive_mood_label(mood: PADState, *, language: Language = "es") -> str:
    """Etiqueta categórica del cuadrante PAD del mood (derivada SOLO para UI).

    Zona neutra central (``|valence| < _NEUTRAL_VALENCE_BAND``) ⇒ "neutral".
    Fuera de ella, el cuadrante (valence ±, arousal alto/bajo) elige la etiqueta.
    Bilingüe (ES/EN). NO es fuente de verdad: el estado continuo PAD lo es.
    """
    idx = 0 if language == "es" else 1
    if abs(mood.valence) < _NEUTRAL_VALENCE_BAND:
        return "neutral"
    positive = mood.valence >= 0.0
    high_arousal = mood.arousal >= _AROUSAL_SPLIT
    return _QUADRANT_LABELS[(positive, high_arousal)][idx]


__all__ = [
    "BASELINE_MAX_DELTA_PER_REFLECTION",
    "BASELINE_PAD",
    "DECAY_HALF_LIFE_S",
    "DRIVE_DECAY_HALF_LIFE_S",
    "INTENSITY_GAIN",
    "MOOD_CEIL",
    "MOOD_EWMA_ALPHA",
    "MOOD_FLOOR",
    "NEUTRAL_DRIVES",
    "AffectState",
    "Drives",
    "Language",
    "PADState",
    "apply_event",
    "decay_drives",
    "decay_emotion",
    "derive_mood_label",
    "neutral_affect_state",
    "satisfy_drive",
    "update_mood",
]
