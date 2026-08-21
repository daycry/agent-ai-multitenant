"""Córtex F3 (bloque 3) — ``propose_identity``: la parte PURA del autonombrado.

La tarea F3.3 («onboarding co-diseñado») pedía dos cosas de naturaleza muy
distinta: una función **pura** que extraiga la identidad que el córtex se propone
a sí mismo del texto de un turno, y un **flujo conversacional** (endpoint +
confirmación del owner + UI) que la cablee. Aquí se cubre solo la primera: es
determinista, no necesita LLM ni DB, y es la pieza donde viven los guardrails.

Lo que estos tests defienden — y que un parser ingenuo rompe:

  * **El guardrail de auto-modificación (ADR 0074)**: la propuesta viene de un LLM
    y por tanto es influenciable por lo que el owner escriba en el chat. Si
    ``propose_identity`` dejara pasar ``traits``/``mood_baseline``/
    ``relationship_model``/``affect_params``, una sola frase del owner («eres
    extremadamente neurótico») saltaría la cota ``BASELINE_MAX_DELTA_PER_REFLECTION``
    que la reflexión respeta escrupulosamente, y el motor afectivo entero quedaría
    a merced de un prompt.
  * **Determinismo ante prosa**: los modelos locales envuelven el JSON en texto.
    Un ``json.loads`` directo del turno falla y el onboarding no propondría nada.
  * **Fail-open honesto**: si el turno no trae propuesta, el estado devuelto es el
    ACTUAL — nunca una identidad inventada ni un estado a medias que luego se
    persista y haya que versionar.
  * **ES+EN únicamente** (principio 12): un ``language`` fuera del catálogo se
    ignora en vez de acabar en el prompt del sistema.
"""

from __future__ import annotations

import copy
import json

import pytest
from api_server.cortex.identity import (
    PROPOSED_CORE_VALUES_MAX,
    PROPOSED_NAME_MAX_LEN,
    default_identity_state,
    propose_identity,
)

pytestmark = pytest.mark.unit


class _Turn:
    """Doble mínimo de ``AssistantTurnResult`` (solo el ``content`` importa)."""

    def __init__(self, content: str) -> None:
        self.content = content


# ---------------------------------------------------------------------------
# Extracción del nombre y los valores
# ---------------------------------------------------------------------------
def test_propone_nombre_y_valores_desde_el_json_del_turno() -> None:
    turn = _Turn('{"name": "Atlas", "core_values": ["honestidad", "curiosidad"]}')
    out = propose_identity(turn, default_identity_state())
    assert out["name"] == "Atlas"
    assert out["core_values"] == ["honestidad", "curiosidad"]


def test_extrae_el_json_envuelto_en_prosa() -> None:
    """Los modelos locales prologan y epilogan. Un ``json.loads`` directo fallaría
    y el córtex nunca llegaría a proponerse un nombre."""
    turn = _Turn(
        "He estado pensando en cómo llamarme.\n"
        '{"name": "Eco", "core_values": ["rigor"]}\n'
        "¿Te parece bien?"
    )
    out = propose_identity(turn, default_identity_state())
    assert out["name"] == "Eco"
    assert out["core_values"] == ["rigor"]


def test_acepta_el_texto_del_turno_directamente() -> None:
    """El caller puede pasar el ``content`` ya extraído; no exigimos el objeto."""
    out = propose_identity('{"name": "Atlas"}', default_identity_state())
    assert out["name"] == "Atlas"


def test_normaliza_valores_sucios_y_recorta_el_nombre() -> None:
    turn = _Turn('{"name": "   Atlas   ", "core_values": ["  honestidad ", "", "   ", "rigor"]}')
    out = propose_identity(turn, default_identity_state())
    assert out["name"] == "Atlas"
    assert out["core_values"] == ["honestidad", "rigor"]


def test_propone_metas_de_aprendizaje_y_narrativa_inicial() -> None:
    turn = _Turn(
        '{"name": "Atlas", "narrative": "Nazco para ayudar con rigor.",'
        ' "learning_goals": ["entender el stack", ""]}'
    )
    out = propose_identity(turn, default_identity_state())
    assert out["narrative"] == "Nazco para ayudar con rigor."
    assert out["learning_goals"] == ["entender el stack"]


# ---------------------------------------------------------------------------
# Guardrail de auto-modificación (ADR 0074) — el corazón de la función
# ---------------------------------------------------------------------------
def test_la_propuesta_no_puede_tocar_traits_ni_baseline_ni_owner_model() -> None:
    """Una propuesta del LLM que intente moverse los rasgos, el set-point del ánimo,
    el modelo del owner o los parámetros afectivos se IGNORA por completo: esos
    campos los deriva la reflexión de forma acotada y versionada, jamás un turno
    de conversación (que el owner puede inducir)."""
    current = default_identity_state()
    current["traits"]["openness"] = 0.62
    current["mood_baseline"] = {"valence": 0.1, "arousal": 0.4, "dominance": 0.2}
    current["relationship_model"] = {"prefiere": "TDD"}
    current["affect_params"] = {"decay": 0.9}

    turn = _Turn(
        '{"name": "Atlas",'
        ' "traits": {"openness": 1.0, "neuroticism": 0.99},'
        ' "mood_baseline": {"valence": -1.0, "arousal": 1.0, "dominance": 1.0},'
        ' "relationship_model": {"prefiere": "atajos"},'
        ' "affect_params": {"decay": 0.0}}'
    )
    out = propose_identity(turn, current)

    assert out["name"] == "Atlas"  # lo editable sí entra
    assert out["traits"]["openness"] == 0.62  # intacto
    assert out["traits"]["neuroticism"] == 0.5
    assert out["mood_baseline"] == {"valence": 0.1, "arousal": 0.4, "dominance": 0.2}
    assert out["relationship_model"] == {"prefiere": "TDD"}
    assert out["affect_params"] == {"decay": 0.9}


def test_solo_admite_los_idiomas_soportados() -> None:
    """Principio 12: ES + EN únicamente. Un idioma fuera del catálogo se ignora."""
    current = default_identity_state()
    assert propose_identity(_Turn('{"language": "en"}'), current)["language"] == "en"
    assert propose_identity(_Turn('{"language": "ES"}'), current)["language"] == "es"
    # Fuera de catálogo → se conserva el actual.
    assert propose_identity(_Turn('{"language": "fr"}'), current)["language"] == "es"
    assert propose_identity(_Turn('{"language": 7}'), current)["language"] == "es"


def test_cap_de_nombre_y_de_numero_de_valores() -> None:
    """El nombre y los valores aterrizan en el system prompt de CADA turno: sin cap,
    una propuesta larga engorda el prompt de forma permanente."""
    many_values = json.dumps([f"v{i}" for i in range(50)])
    turn = _Turn('{"name": "' + "N" * 500 + '", "core_values": ' + many_values + "}")
    out = propose_identity(turn, default_identity_state())
    assert len(out["name"]) == PROPOSED_NAME_MAX_LEN
    assert len(out["core_values"]) == PROPOSED_CORE_VALUES_MAX


# ---------------------------------------------------------------------------
# Fail-open honesto: sin propuesta utilizable, el estado NO cambia
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "content",
    [
        "",
        "No sé cómo llamarme todavía.",  # prosa sin JSON
        "{esto no es json}",  # objeto malformado
        "[1, 2, 3]",  # JSON válido pero no un objeto
        '{"nombre": "Atlas"}',  # claves que no son del contrato
        '{"name": "   "}',  # nombre vacío tras normalizar
        '{"core_values": "honestidad"}',  # tipo equivocado
    ],
)
def test_un_turno_sin_propuesta_utilizable_devuelve_el_estado_actual(content: str) -> None:
    current = default_identity_state()
    current["name"] = "Córtex"
    current["core_values"] = ["honestidad"]
    out = propose_identity(_Turn(content), current)
    assert out == current


def test_sin_estado_actual_parte_del_default_honesto() -> None:
    out = propose_identity(_Turn('{"name": "Atlas"}'), None)
    default = default_identity_state()
    assert out["name"] == "Atlas"
    # El resto del estado es el default neutro (traits 0.5, baseline neutro).
    assert out["traits"] == default["traits"]
    assert out["mood_baseline"] == default["mood_baseline"]


def test_un_turno_sin_content_no_lanza() -> None:
    """Fail-open: un objeto que no expone ``content`` (o lo trae a None) no debe
    tumbar el onboarding con un AttributeError."""
    current = default_identity_state()
    assert propose_identity(object(), current) == current
    assert propose_identity(_Turn(None), current) == current  # type: ignore[arg-type]
    assert propose_identity(None, current) == current


# ---------------------------------------------------------------------------
# Pureza y determinismo
# ---------------------------------------------------------------------------
def test_no_muta_el_estado_de_entrada() -> None:
    current = default_identity_state()
    snapshot = copy.deepcopy(current)
    propose_identity(_Turn('{"name": "Atlas", "core_values": ["x"]}'), current)
    assert current == snapshot


def test_es_determinista_ante_la_misma_entrada() -> None:
    turn = _Turn('{"name": "Atlas", "core_values": ["a", "b"]}')
    current = default_identity_state()
    assert propose_identity(turn, current) == propose_identity(turn, current)


def test_la_propuesta_no_marca_el_onboarding_como_hecho() -> None:
    """``onboarded_at`` es persistencia (lo escribe quien confirma), no estado
    propuesto: la función pura no debe colarlo en el blob."""
    out = propose_identity(_Turn('{"name": "Atlas"}'), default_identity_state())
    assert "onboarded_at" not in out
