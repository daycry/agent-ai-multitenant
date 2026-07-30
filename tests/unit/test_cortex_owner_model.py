"""Córtex — parse tolerante del owner_model en la propuesta de reflexión.

El prompt de reflexión pide (opcionalmente) ``owner_model`` (dict breve sobre el
OWNER) y ``owner_facts`` (0-3 hechos duraderos). El parse es GRANULAR y
fail-open: campos ausentes o malformados NO invalidan narrative/traits, y una
propuesta con SOLO owner_model sigue siendo útil (se aplica al
``relationship_model`` sin re-escribir la narrativa).
"""

from __future__ import annotations

import json

from workers.cortex_reflection import _parse_proposal


def test_parse_owner_model_y_facts() -> None:
    content = json.dumps(
        {
            "narrative": "He aprendido del owner.",
            "owner_model": {"prefiere": "TDD"},
            "owner_facts": ["usa Windows", 42, "", "  "],
        }
    )
    proposal = _parse_proposal(content)
    assert proposal is not None
    assert proposal.narrative == "He aprendido del owner."
    assert proposal.owner_model == {"prefiere": "TDD"}
    # Los facts no-string o vacíos se filtran (tolerante, nunca lanza).
    assert proposal.owner_facts == ("usa Windows",)


def test_parse_sin_owner_model_no_invalida_el_resto() -> None:
    proposal = _parse_proposal('{"narrative": "sigo aprendiendo"}')
    assert proposal is not None
    assert proposal.narrative == "sigo aprendiendo"
    assert proposal.owner_model is None
    assert proposal.owner_facts == ()


def test_parse_owner_model_malformado_se_ignora_granularmente() -> None:
    proposal = _parse_proposal(
        '{"narrative": "n", "owner_model": "no soy un dict", "owner_facts": "tampoco lista"}'
    )
    assert proposal is not None
    assert proposal.narrative == "n"
    assert proposal.owner_model is None
    assert proposal.owner_facts == ()


def test_parse_solo_owner_model_es_propuesta_valida() -> None:
    # Aprender del owner sin re-escribir la narrativa TAMBIÉN merece versión.
    proposal = _parse_proposal('{"owner_model": {"stack": "python"}}')
    assert proposal is not None
    assert proposal.narrative is None
    assert proposal.traits is None
    assert proposal.owner_model == {"stack": "python"}


def test_parse_objeto_sin_nada_util_sigue_siendo_none() -> None:
    assert _parse_proposal('{"summary": "solo un resumen"}') is None


def test_parse_limita_owner_facts_a_tres() -> None:
    proposal = _parse_proposal(
        json.dumps({"narrative": "n", "owner_facts": ["a", "b", "c", "d", "e"]})
    )
    assert proposal is not None
    assert proposal.owner_facts == ("a", "b", "c")
