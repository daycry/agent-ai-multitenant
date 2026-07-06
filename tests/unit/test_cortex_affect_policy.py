"""Córtex — política afectiva pura (afecto → conducta, ADR 0075).

Cubre los mapeos deterministas de ``cortex/affect_policy.py``:

  * ``modulate_reasoning_effort`` — el afecto mueve el effort COMO MÁXIMO un paso
    por la escalera del kind (``REASONING_OPTIONS_BY_KIND`` sin ``"off"``), con
    suelo duro en ``low``. El afecto modula, NUNCA bloquea (ADR 0075): un kind
    desconocido o una base fuera de escalera son no-op auditables.
  * ``tone_guidance`` — bandas PAD/drives → guía de tono; la banda neutra no
    emite nada (copy honesto: sin fingir estados que no destacan).

Todo puro: sin red, sin BD, sin reloj.
"""

from __future__ import annotations

from api_server.cortex.affect_policy import (
    EffortDecision,
    modulate_reasoning_effort,
    tone_guidance,
)
from api_server.cortex.affective import (
    AffectState,
    Drives,
    PADState,
    neutral_affect_state,
)


def _affect(
    *,
    valence: float = 0.0,
    arousal: float = 0.3,
    dominance: float = 0.0,
    intensity: float = 0.0,
    curiosity: float = 0.5,
    bonding: float = 0.5,
) -> AffectState:
    """Estado afectivo de prueba con neutros salvo lo que el test destaca."""
    return AffectState(
        emotion=PADState(
            valence=valence, arousal=arousal, dominance=dominance, intensity=intensity
        ),
        mood=PADState(valence=valence, arousal=arousal, dominance=dominance),
        drives=Drives(curiosity=curiosity, bonding=bonding, coherence=0.5, competence=0.5),
    )


# ---------------------------------------------------------------------------
# modulate_reasoning_effort — subir/bajar UN paso, suelo low, nunca "off"
# ---------------------------------------------------------------------------
def test_effort_sube_un_paso_con_arousal_e_intensidad_altos() -> None:
    decision = modulate_reasoning_effort(
        "high", "claude_sdk", _affect(arousal=0.72, intensity=0.31)
    )
    assert decision.base == "high"
    assert decision.effective == "xhigh"
    assert any(r.startswith("arousal_high") for r in decision.reasons)


def test_effort_no_sube_sin_intensidad() -> None:
    # Arousal alto pero sin evento afectivo reciente (intensity baja) ⇒ no sube.
    decision = modulate_reasoning_effort("high", "claude_sdk", _affect(arousal=0.72, intensity=0.1))
    assert decision.effective == "high"


def test_effort_baja_un_paso_en_estado_apagado() -> None:
    decision = modulate_reasoning_effort(
        "high", "claude_sdk", _affect(arousal=0.10, curiosity=0.15)
    )
    assert decision.effective == "medium"
    assert any(r.startswith("arousal_low") for r in decision.reasons)


def test_effort_suelo_duro_en_low() -> None:
    # Estado apagado con base "low": el suelo es low, jamás "off".
    decision = modulate_reasoning_effort("low", "claude_sdk", _affect(arousal=0.05, curiosity=0.05))
    assert decision.effective == "low"


def test_effort_techo_de_la_escalera() -> None:
    decision = modulate_reasoning_effort("max", "claude_sdk", _affect(arousal=0.9, intensity=0.9))
    assert decision.effective == "max"


def test_effort_techo_por_kind_no_claude() -> None:
    # La escalera de ollama termina en "high": no existe xhigh al que subir.
    decision = modulate_reasoning_effort("high", "ollama", _affect(arousal=0.9, intensity=0.9))
    assert decision.effective == "high"


def test_effort_neutral_es_noop() -> None:
    decision = modulate_reasoning_effort("high", "claude_sdk", neutral_affect_state())
    assert decision.base == "high"
    assert decision.effective == "high"
    assert decision.reasons == ()


def test_effort_kind_desconocido_es_noop_auditable() -> None:
    # Los dobles de test (ScriptedAssistantModel) no llevan provider_kind ⇒ aquí.
    decision = modulate_reasoning_effort("high", None, _affect(arousal=0.9, intensity=0.9))
    assert decision.effective == "high"
    assert decision.reasons == ("no_ladder",)

    decision = modulate_reasoning_effort("high", "bogus_kind", _affect(arousal=0.9, intensity=0.9))
    assert decision.effective == "high"
    assert decision.reasons == ("no_ladder",)


def test_effort_base_ausente_o_fuera_de_escalera_es_noop() -> None:
    sin_base = modulate_reasoning_effort(None, "claude_sdk", _affect(arousal=0.9, intensity=0.9))
    assert sin_base.effective is None
    assert sin_base.reasons == ("no_base",)

    fuera = modulate_reasoning_effort("turbo", "claude_sdk", _affect(arousal=0.9, intensity=0.9))
    assert fuera.effective == "turbo"
    assert fuera.reasons == ("no_ladder",)


def test_effort_off_como_base_nunca_participa() -> None:
    # "off" está excluido de la escalera: una base "off" es fuera-de-escalera
    # (el afecto no puede ni apagar ni encender el razonamiento — ADR 0075).
    decision = modulate_reasoning_effort("off", "claude_sdk", _affect(arousal=0.05, curiosity=0.05))
    assert decision.effective == "off"
    assert decision.reasons == ("no_ladder",)


def test_effort_es_determinista() -> None:
    a = modulate_reasoning_effort("high", "claude_sdk", _affect(arousal=0.72, intensity=0.31))
    b = modulate_reasoning_effort("high", "claude_sdk", _affect(arousal=0.72, intensity=0.31))
    assert a == b
    assert isinstance(a, EffortDecision)


# ---------------------------------------------------------------------------
# tone_guidance — bandas → guía; la banda neutra no emite
# ---------------------------------------------------------------------------
def test_tono_neutro_no_emite_nada() -> None:
    assert tone_guidance(neutral_affect_state()) == ()


def test_tono_valence_positiva_calido() -> None:
    lines = tone_guidance(_affect(valence=0.4))
    assert any("cálido" in line for line in lines)


def test_tono_valence_negativa_sobrio() -> None:
    lines = tone_guidance(_affect(valence=-0.4))
    assert any("sobrio" in line for line in lines)


def test_tono_arousal_alto_energico_y_bajo_pausado() -> None:
    assert any("enérgico" in line for line in tone_guidance(_affect(arousal=0.6)))
    assert any("pausado" in line for line in tone_guidance(_affect(arousal=0.2)))


def test_tono_dominance_seguro_y_tentativo() -> None:
    assert any("seguridad" in line for line in tone_guidance(_affect(dominance=0.4)))
    assert any("tentativo" in line for line in tone_guidance(_affect(dominance=-0.4)))


def test_tono_drives_curiosidad_y_bonding() -> None:
    assert any("pregunta" in line for line in tone_guidance(_affect(curiosity=0.8)))
    assert any("cercano" in line for line in tone_guidance(_affect(bonding=0.8)))


def test_tono_bilingue() -> None:
    lines = tone_guidance(_affect(valence=0.4), language="en")
    assert lines
    assert any("warm" in line for line in lines)
    assert not any("cálido" in line for line in lines)
