"""C2 — el pulso de la plataforma como estímulo afectivo del córtex (puro).

El córtex es el asistente personal del operador y sin embargo era CIEGO al
sistema que ambos operan: su afecto solo se movía por el texto del chat
(`workers/cortex_affect.py` puntúa user_text+cortex_text y nada más). Un plan
que fracasa, una tanda de runs con éxito o un bloqueo no le producían nada.

Este módulo es el mapeo DETERMINISTA pulso→afecto (sin LLM: los recuentos ya
son señal suficiente y el beat corre cada 15 min — coste cero). El beat
``workers.cortex_platform_pulse`` (gated por ``cortex.autonomy_enabled``, como
el resto de bucles F4) recuenta la ventana y aplica el delta por el MISMO motor
que el appraisal conversacional (``apply_event``/``update_mood``), dejando
snapshot + telemetría con una razón honesta («pulso de plataforma: …»).

Calibración: los coeficientes son deliberadamente suaves — el pulso matiza el
humor, no lo secuestra; los fallos pesan más que los éxitos (asimetría negativa
humana) y todo queda acotado muy por debajo del rango de una emoción fuerte.
"""

from __future__ import annotations

from dataclasses import dataclass

from api_server.cortex.affective import PADState

# Coeficientes del mapeo (suaves; ver docstring). Techo de |valence| y arousal
# para que un día catastrófico no secuestre el humor de un plumazo.
_DONE_VALENCE = 0.02
_COMPLETED_VALENCE = 0.08
_FAILED_VALENCE = -0.06
_BLOCKED_VALENCE = -0.05
_FAILED_AROUSAL = 0.03
_BLOCKED_AROUSAL = 0.04
_VALENCE_CAP = 0.30
_AROUSAL_CAP = 0.25
_COMPETENCE_PER_DONE = 0.03
_COMPETENCE_PER_COMPLETED = 0.10
_COMPETENCE_CAP = 0.30


@dataclass(frozen=True)
class PlatformPulse:
    """Recuento de una ventana del pulso (lo que pasó desde el último beat)."""

    executions_done: int
    executions_failed: int
    plans_blocked: int
    plans_completed: int

    @property
    def is_quiet(self) -> bool:
        return not (
            self.executions_done
            or self.executions_failed
            or self.plans_blocked
            or self.plans_completed
        )


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def pulse_appraisal(
    pulse: PlatformPulse,
) -> tuple[PADState, str | None, str | None, float]:
    """``(delta PAD, razón, drive, cantidad)`` para un pulso de plataforma.

    Ventana tranquila → delta cero y razón ``None`` (el beat no escribe
    snapshot: el silencio no es un evento). Los éxitos sacian ``competence``
    solo cuando el saldo del pulso es positivo."""
    if pulse.is_quiet:
        return PADState(valence=0.0, arousal=0.0, dominance=0.0), None, None, 0.0

    valence = _clamp(
        pulse.executions_done * _DONE_VALENCE
        + pulse.plans_completed * _COMPLETED_VALENCE
        + pulse.executions_failed * _FAILED_VALENCE
        + pulse.plans_blocked * _BLOCKED_VALENCE,
        -_VALENCE_CAP,
        _VALENCE_CAP,
    )
    arousal = _clamp(
        pulse.executions_failed * _FAILED_AROUSAL + pulse.plans_blocked * _BLOCKED_AROUSAL,
        0.0,
        _AROUSAL_CAP,
    )
    # Un pulso negativo también resta algo de dominance (las cosas se salen de
    # las manos); uno positivo la refuerza levemente.
    dominance = _clamp(valence * 0.3, -0.1, 0.1)
    intensity = _clamp(abs(valence) + arousal * 0.5, 0.0, 0.4)
    delta = PADState(valence=valence, arousal=arousal, dominance=dominance, intensity=intensity)

    parts: list[str] = []
    if pulse.executions_done:
        parts.append(f"{pulse.executions_done} run(s) completados")
    if pulse.executions_failed:
        parts.append(f"{pulse.executions_failed} run(s) fallidos")
    if pulse.plans_completed:
        parts.append(f"{pulse.plans_completed} plan(es) completados")
    if pulse.plans_blocked:
        parts.append(f"{pulse.plans_blocked} plan(es) bloqueados")
    reason = "pulso de plataforma: " + ", ".join(parts)

    drive: str | None = None
    amount = 0.0
    if valence > 0:
        drive = "competence"
        amount = _clamp(
            pulse.executions_done * _COMPETENCE_PER_DONE
            + pulse.plans_completed * _COMPETENCE_PER_COMPLETED,
            0.0,
            _COMPETENCE_CAP,
        )
    return delta, reason, drive, amount
