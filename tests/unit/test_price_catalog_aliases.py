"""El puente kind→familia del catálogo de precios no puede divergir (prod-07 task_prod07_12).

Dos tablas describían la MISMA relación y no coincidían:

* ``pricing/litellm_sync.KIND_TO_LITELLM_FAMILIES`` — la oficial, documentada en
  el ADR 0028: decide qué familias del feed LiteLLM se IMPORTAN al catálogo.
* ``db/price_snapshot._CATALOG_PROVIDER_ALIASES`` — decide qué familias se
  BUSCAN al poner precio a una llamada.

La divergencia medida (2026-07-30): a ``copilot`` le faltaba ``anthropic`` y a
``azure_foundry`` le faltaba ``openai``. O sea: el sync IMPORTABA los precios de
Claude vía Copilot y de los modelos OpenAI vía Azure, y el snapshot no los
buscaba. Esos modelos solo casaban por el fallback de «match único por
model_id», que se apaga en cuanto otro proveedor comparte el nombre del modelo
(``test_ambiguous_model_only_match_stays_unknown``) — es decir, en cuanto el
catálogo crece. Coste: ``price_snapshot_cost_usd`` NULL y budgets sumando $0.

El arreglo es estructural: la tabla del snapshot se DERIVA de la oficial, así
que no puede volver a divergir. Estos tests son la guarda de que sigue así.
"""

from __future__ import annotations

import pytest
from api_server.db.price_snapshot import _CATALOG_PROVIDER_ALIASES
from api_server.pricing.litellm_sync import KIND_TO_LITELLM_FAMILIES

pytestmark = pytest.mark.unit


def test_the_guard_actually_finds_the_kinds() -> None:
    """§4 de verificar-antes-de-implementar: una guarda que no encuentra nada
    pasa vacíamente. Si el catálogo cerrado del ADR 0021 se queda sin kinds,
    esto avisa antes de que los tests de abajo empiecen a mentir."""
    assert len(KIND_TO_LITELLM_FAMILIES) >= 4, KIND_TO_LITELLM_FAMILIES
    assert {"claude_sdk", "azure_foundry", "copilot", "ollama"} <= set(KIND_TO_LITELLM_FAMILIES)


@pytest.mark.parametrize("kind", sorted(KIND_TO_LITELLM_FAMILIES))
def test_every_imported_family_is_also_looked_up(kind: str) -> None:
    """La invariante: si el sync IMPORTA precios de una familia para un kind, el
    snapshot tiene que BUSCARLOS ahí. Lo contrario es catálogo con precio y
    ejecución sin coste."""
    imported = KIND_TO_LITELLM_FAMILIES[kind]
    looked_up = set(_CATALOG_PROVIDER_ALIASES.get(kind, ()))
    missing = imported - looked_up
    assert not missing, (
        f"el kind '{kind}' importa precios de {sorted(missing)} pero el snapshot "
        f"no los busca (busca {sorted(looked_up)})"
    )


def test_copilot_looks_up_anthropic() -> None:
    """El caso concreto que faltaba: Copilot sirve modelos Claude."""
    assert "anthropic" in _CATALOG_PROVIDER_ALIASES["copilot"]


def test_azure_foundry_looks_up_openai() -> None:
    """El otro: Azure AI Foundry fronta modelos OpenAI, que LiteLLM lista bajo
    la familia 'openai'."""
    assert "openai" in _CATALOG_PROVIDER_ALIASES["azure_foundry"]


def test_manual_only_families_survive_the_derivation() -> None:
    """``github_copilot`` no viene del feed (lo puebla el alta manual del
    catálogo) y el kind heredado ``claude`` tampoco está en la tabla oficial:
    derivar no puede haberlos perdido."""
    assert "github_copilot" in _CATALOG_PROVIDER_ALIASES["copilot"]
    assert _CATALOG_PROVIDER_ALIASES["claude"] == ("anthropic",)


def test_lookup_order_is_deterministic() -> None:
    """El orden decide qué fila gana cuando varias casan, así que no puede
    depender del orden de iteración de un ``frozenset`` (que varía entre
    procesos por el hash randomizado de las cadenas)."""
    from api_server.db.price_snapshot import _build_catalog_aliases

    assert _build_catalog_aliases() == _build_catalog_aliases()
    assert _CATALOG_PROVIDER_ALIASES["azure_foundry"] == ("azure", "azure_ai", "openai")
