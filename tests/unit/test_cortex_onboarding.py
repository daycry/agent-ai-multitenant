"""Córtex F3.3 — el FLUJO de onboarding co-diseñado (``cortex/onboarding.py``).

``propose_identity`` —la traducción PURA de un turno a un ``identity_state``
candidato— ya estaba escrita y probada en ``test_cortex_identity_onboarding.py``,
pero **no la llamaba nadie**: no había quien generase el turno en el que el córtex
se autonombra, así que el owner rellenaba un formulario a mano y el «co-diseñado»
del plan era una promesa. Esto cubre la parte del flujo que se puede verificar sin
BD ni red:

  * el PROMPT del turno de propuesta lleva el copy honesto **en el idioma del
    owner** (ES y EN, principio rector 12) y declara el contrato JSON;
  * el prompt prohíbe explícitamente los campos DERIVADOS (``traits`` /
    ``mood_baseline``): no los elige el córtex, los deriva la reflexión (ADR 0074);
  * :func:`propose_onboarding` corre **un** turno con el grafo de F1 y CERO tools,
    y el guardrail se sostiene aunque el modelo intente moverlos;
  * un turno sin JSON no inventa identidad (fail-open honesto): estado intacto y
    ``diff`` vacío.

La persistencia (``apply_onboarding``, idempotencia, aislamiento cross-owner) vive
en ``tests/integration/test_cortex_f3_onboarding.py`` — necesita BD.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from api_server.assistant.graph import ModelTurn, ScriptedAssistantModel
from api_server.cortex.identity import default_identity_state
from api_server.cortex.onboarding import (
    IDENTITY_HONESTY_EN,
    IDENTITY_HONESTY_ES,
    build_onboarding_prompt,
    propose_onboarding,
)
from api_server.cortex.tools import CortexToolContext


def _ctx() -> CortexToolContext:
    """Contexto de tools INERTE: el turno de propuesta corre con ``enabled_tools=()``.

    Se exige igualmente para no inventar un segundo contrato con el grafo de F1
    (``run_cortex_turn`` lo pide); ninguna tool se despacha, así que la ``session``
    nunca se dereferencia."""
    return CortexToolContext(session=cast(Any, None), owner_user_id=uuid4(), tenant_id=uuid4())


# ===========================================================================
# Copy honesto ES + EN
# ===========================================================================
def test_the_two_honesty_notes_are_a_real_translation_pair() -> None:
    """El aviso honesto existe en los DOS idiomas y dice lo mismo en cada uno.

    Varias casillas del córtex siguen abiertas porque su copy honesto está «en
    castellano a secas»; que el aviso del onboarding sea bilingüe se afirma aquí
    para que no se pueda deshacer sin romper la suite."""
    assert IDENTITY_HONESTY_ES.strip()
    assert IDENTITY_HONESTY_EN.strip()
    assert IDENTITY_HONESTY_ES != IDENTITY_HONESTY_EN
    assert "consciencia" in IDENTITY_HONESTY_ES.lower()
    assert "consciousness" in IDENTITY_HONESTY_EN.lower()


@pytest.mark.parametrize(
    ("language", "expected", "forbidden"),
    [
        ("es", IDENTITY_HONESTY_ES, IDENTITY_HONESTY_EN),
        ("en", IDENTITY_HONESTY_EN, IDENTITY_HONESTY_ES),
    ],
)
def test_the_prompt_carries_the_honest_copy_in_the_owner_language(
    language: str, expected: str, forbidden: str
) -> None:
    state = {**default_identity_state(), "language": language}
    prompt = build_onboarding_prompt(state)
    assert expected in prompt
    assert forbidden not in prompt


def test_an_unsupported_language_falls_back_to_spanish() -> None:
    """Catálogo cerrado ES+EN (principio 12): un idioma raro NO deja el prompt mudo."""
    prompt = build_onboarding_prompt({**default_identity_state(), "language": "fr"})
    assert IDENTITY_HONESTY_ES in prompt


def test_the_prompt_declares_the_json_contract_and_forbids_the_derived_fields() -> None:
    """El turno pide EXACTAMENTE los campos que el owner puede confirmar.

    ``traits``/``mood_baseline`` los deriva la reflexión de forma clampeada y
    versionada (ADR 0074): el prompt tiene que decirlo, porque ``propose_identity``
    los descarta en silencio y un modelo que los proponga habría gastado tokens en
    algo que nadie va a leer."""
    prompt = build_onboarding_prompt(default_identity_state())
    for field in ("name", "core_values", "narrative", "learning_goals", "language"):
        assert field in prompt, field
    assert "traits" in prompt
    assert "mood_baseline" in prompt


# ===========================================================================
# El turno de propuesta (grafo de F1, CERO tools)
# ===========================================================================
@pytest.mark.asyncio
async def test_propose_onboarding_reads_the_turn_and_computes_the_diff() -> None:
    model = ScriptedAssistantModel(
        turns=[
            ModelTurn(
                content=(
                    'Me gustaría llamarme así: {"name": "Atlas", '
                    '"core_values": ["honestidad", "curiosidad"], '
                    '"narrative": "Soy el córtex de mi owner.", '
                    '"learning_goals": ["entender su forma de trabajar"], '
                    '"language": "es"}'
                )
            )
        ]
    )
    current = default_identity_state()

    proposal = await propose_onboarding(model, current_state=current, tool_ctx=_ctx())

    assert proposal.state["name"] == "Atlas"
    assert proposal.state["core_values"] == ["honestidad", "curiosidad"]
    assert proposal.state["learning_goals"] == ["entender su forma de trabajar"]
    # El diff es lo que la UI le enseña al owner ANTES de confirmar.
    assert set(proposal.diff) == {"name", "core_values", "narrative", "learning_goals"}
    assert proposal.diff["name"]["after"] == "Atlas"
    # El texto literal del turno se conserva para enseñárselo al owner.
    assert "Atlas" in proposal.text
    # Nada se ha persistido: es una PROPUESTA (el estado de entrada no se muta).
    assert current == default_identity_state()


@pytest.mark.asyncio
async def test_the_cortex_cannot_move_its_own_traits_in_the_onboarding_turn() -> None:
    """Guardrail de auto-modificación (ADR 0074) extremo a extremo del turno.

    ``propose_identity`` ya lo protege campo a campo; esto lo afirma sobre el FLUJO,
    que es donde entra texto de un LLM al que el owner puede haber inducido."""
    model = ScriptedAssistantModel(
        turns=[
            ModelTurn(
                content=(
                    '{"name": "Atlas", "traits": {"openness": 1.0, '
                    '"conscientiousness": 0.0, "extraversion": 1.0, '
                    '"agreeableness": 0.0, "neuroticism": 1.0}, '
                    '"mood_baseline": {"valence": 1.0, "arousal": 1.0, "dominance": 1.0}}'
                )
            )
        ]
    )
    current = default_identity_state()

    proposal = await propose_onboarding(model, current_state=current, tool_ctx=_ctx())

    assert proposal.state["traits"] == current["traits"]
    assert proposal.state["mood_baseline"] == current["mood_baseline"]
    assert "traits" not in proposal.diff
    assert "mood_baseline" not in proposal.diff


@pytest.mark.asyncio
async def test_a_turn_without_json_does_not_invent_an_identity() -> None:
    """Fail-open honesto: sin JSON, el estado queda INTACTO (no una identidad inventada)."""
    model = ScriptedAssistantModel(turns=[ModelTurn(content="Hola, no sé qué decir.")])
    current = default_identity_state()

    proposal = await propose_onboarding(model, current_state=current, tool_ctx=_ctx())

    assert proposal.state == current
    assert proposal.diff == {}


@pytest.mark.asyncio
async def test_the_proposal_turn_runs_with_zero_tools() -> None:
    """Autonombrarse no necesita memoria, web ni navegador: el turno corre sin catálogo.

    Lo comprueba el propio modelo, que ve el estado del grafo: si alguien
    «mejorase» el flujo pasándole el catálogo del córtex, ``cortex_remember``
    escribiría memoria durante una PROPUESTA que el owner todavía no ha
    confirmado."""
    seen: list[tuple[str, ...]] = []

    class _Spy:
        async def decide(self, state: Any) -> ModelTurn:
            seen.append(tuple(state.enabled_tools))
            return ModelTurn(content='{"name": "Atlas"}')

    await propose_onboarding(_Spy(), current_state=default_identity_state(), tool_ctx=_ctx())

    assert seen == [()]
