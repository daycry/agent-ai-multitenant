"""Aviso de linaje compartido entre implementador y revisor (`task_gov_07`).

Plan [`gov-01`](../../docs/roadmap/gov-01-precedencia-prompts-y-rigor.md), fase 3.

Lo que YA existía y este trabajo NO rehace: la cadena de herencia
agente→equipo→proyecto→plataforma (`db/platform_settings.py`,
`resolve_model_config_chain`), el `model_origin` que el Hub ya resuelve, serializa
y pinta, y el mapa kind→familia `KIND_TO_LITELLM_FAMILIES`
(`api_server/pricing/litellm_sync.py`), que es la fuente única y **no se
duplica**: si mañana `copilot` deja de servir modelos Anthropic, este aviso se
entera solo.

Lo que faltaba no era poder configurarlo: era que el sistema **supiera que
compartir linaje importa**. Ni bloquea ni cambia nada — convierte una decisión
invisible en visible. El operador decidió expresamente quedarse aquí y no
exigirlo: un proyecto sin un segundo proveedor no puede quedarse sin poder
cerrar reviews.

El detalle que descubrió el código y no la especificación: `model_config
["provider"]` guarda HOY las dos formas. El catálogo cerrado del ADR 0021 y
`DEFAULT_MODEL_CONFIG` usan el **kind** (`claude_sdk`), pero los once agentes
built-in se siembran con la **familia** (`anthropic`,
`seeds/builtin_agents.py`). Un resolutor que solo entendiera una de las dos
formas daría «sin linaje compartido» para todos los equipos built-in, que son
justo los que más comparten.
"""

from __future__ import annotations

import pytest
from api_server.capabilities import (
    WARN_SHARED_MODEL_LINEAGE,
    model_families,
    shared_lineage_warning,
)
from api_server.pricing.litellm_sync import KIND_TO_LITELLM_FAMILIES

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# model_families: la fuente única, y las dos formas del dato
# ---------------------------------------------------------------------------
def test_families_come_from_the_existing_map_not_a_copy() -> None:
    """La guarda que impide que esto se convierta en un segundo mapa.

    Si alguien copiara la tabla aquí, este test seguiría verde con la copia
    desactualizada; por eso se compara contra el mapa vivo, entrada por entrada.
    """
    assert KIND_TO_LITELLM_FAMILIES, "el mapa oficial está vacío: la guarda pasaría vacía"
    for kind, families in KIND_TO_LITELLM_FAMILIES.items():
        assert model_families(kind) == families, (
            f"el resolutor discrepa del mapa oficial para el kind {kind!r}"
        )


def test_a_family_name_resolves_to_itself() -> None:
    """Los agentes built-in se siembran con la FAMILIA, no con el kind.

    `seeds/builtin_agents.py` pone `provider="anthropic"` en los once. Si esto
    devolviera vacío, ningún equipo built-in dispararía nunca el aviso.
    """
    assert model_families("anthropic") == frozenset({"anthropic"})
    assert model_families("openai") == frozenset({"openai"})


def test_an_unknown_provider_has_no_family() -> None:
    """Desconocido ≠ compartido: sin familia no se puede afirmar linaje común."""
    assert model_families("un-proveedor-que-no-existe") == frozenset()
    assert model_families(None) == frozenset()
    assert model_families("   ") == frozenset()


@pytest.mark.parametrize("declared", ["Claude_SDK", " claude_sdk ", "CLAUDE_SDK"])
def test_provider_is_normalised_before_lookup(declared: str) -> None:
    assert model_families(declared) == KIND_TO_LITELLM_FAMILIES["claude_sdk"]


# ---------------------------------------------------------------------------
# El aviso
# ---------------------------------------------------------------------------
def test_same_provider_warns() -> None:
    warnings = shared_lineage_warning(
        agent_provider="claude_sdk", reviewer_provider="claude_sdk", reviewer_name="Reviewer"
    )
    assert [w.code for w in warnings] == [WARN_SHARED_MODEL_LINEAGE]
    assert warnings[0].es and warnings[0].en, "el aviso del Hub es bilingüe por contrato"


def test_different_kinds_that_share_a_family_also_warn() -> None:
    """El caso que hace falta el mapa: `copilot` sirve modelos de Anthropic.

    Dos proveedores distintos con el mismo linaje detrás son exactamente la
    situación que un «¿son proveedores distintos?» ingenuo daría por buena.
    """
    assert "anthropic" in KIND_TO_LITELLM_FAMILIES["copilot"], (
        "cambió el catálogo: este test se apoyaba en que copilot sirve Anthropic"
    )
    warnings = shared_lineage_warning(
        agent_provider="claude_sdk", reviewer_provider="copilot", reviewer_name="Reviewer"
    )
    assert [w.code for w in warnings] == [WARN_SHARED_MODEL_LINEAGE]


def test_unrelated_families_do_not_warn() -> None:
    assert "ollama" not in KIND_TO_LITELLM_FAMILIES["claude_sdk"]
    assert (
        shared_lineage_warning(
            agent_provider="claude_sdk", reviewer_provider="ollama", reviewer_name="Reviewer"
        )
        == []
    )


def test_no_reviewer_no_warning() -> None:
    """Sin revisor no hay pareja: avisar sería inventar un riesgo."""
    assert (
        shared_lineage_warning(
            agent_provider="claude_sdk", reviewer_provider=None, reviewer_name=None
        )
        == []
    )


def test_unknown_provider_does_not_warn() -> None:
    """No se afirma linaje compartido sobre un dato que no se sabe leer."""
    assert (
        shared_lineage_warning(
            agent_provider="algo-raro", reviewer_provider="algo-raro", reviewer_name="Reviewer"
        )
        == []
    )


def test_the_message_names_the_family_and_the_reviewer() -> None:
    """Un aviso que no dice CUÁL es el linaje ni con QUIÉN no es accionable.

    El operador tiene que poder ir y cambiar el modelo del revisor; para eso
    necesita saber a qué revisor se refiere y qué familia comparten.
    """
    warnings = shared_lineage_warning(
        agent_provider="claude_sdk", reviewer_provider="copilot", reviewer_name="QA Reviewer"
    )
    assert len(warnings) == 1
    for text in (warnings[0].es, warnings[0].en):
        assert "QA Reviewer" in text
        assert "anthropic" in text


def test_several_shared_families_are_listed_deterministically() -> None:
    """Dos proveedores pueden compartir más de una familia; el orden es estable.

    Un mensaje que cambia de orden entre peticiones parece un cambio de estado
    donde no lo hay.
    """
    # Medido, no supuesto: `copilot ∩ azure_foundry` es UNA sola familia
    # (`openai`) — lo di por hecho al escribir el test y el mapa me corrigió.
    # El par con varias en común es `azure_foundry` consigo mismo.
    shared = KIND_TO_LITELLM_FAMILIES["azure_foundry"]
    assert len(shared) >= 2, f"el test se apoyaba en más de una familia común; hay {shared}"
    first = shared_lineage_warning(
        agent_provider="azure_foundry", reviewer_provider="azure_foundry", reviewer_name="R"
    )
    second = shared_lineage_warning(
        agent_provider="azure_foundry", reviewer_provider="azure_foundry", reviewer_name="R"
    )
    assert first[0].es == second[0].es
    assert ", ".join(sorted(shared)) in first[0].es


def test_the_warning_code_is_exported_for_the_frontend() -> None:
    """El front empareja por `code`, nunca por el texto castellano.

    Hacerlo por texto ya dejó muerta la rama EN una vez (el follow-up bilingüe
    de 06.17); el código estable es el contrato.
    """
    assert WARN_SHARED_MODEL_LINEAGE == "shared_model_lineage"
