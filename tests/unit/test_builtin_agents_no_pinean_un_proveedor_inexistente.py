"""Un agente built-in no pinea un proveedor que el catálogo cerrado no tiene.

Auditoría 2026-09-01 (F-01). Once agentes core llevaban
``model_provider="anthropic"`` — un kind que no existe en ``LLMProviderKind``
(``claude_sdk`` / ``copilot`` / ``azure_foundry`` / ``ollama``, ADR 0021)— y el
refresco de arranque lo re-afirmaba en cada despliegue. La auditoría del
2026-07-16 lo anotó como inocuo («ningún run los usa directamente»), y la premisa
era falsa: al ADOPTAR un equipo, las copias de tenant copian ``model_config``
verbatim, la cadena de herencia (ADR 0055) se salta porque el agente «pinea», y
el worker aborta ``model_unresolved`` antes de arrancar. Explica por qué el
recorrido E2E sólo ha prosperado con el equipo CodeIgniter, el único que no pinea.

La regla que fija este fichero es la de CI4 (ADR 0055): un built-in no pinea
proveedor ni modelo; hereda proyecto → equipo → plataforma. Y si algún día uno
pinea, el kind tiene que existir.
"""

from __future__ import annotations

import pytest
from api_server.db.llm_providers import LLMProviderKind
from api_server.seeds.builtin_agents import BUILTIN_AGENTS
from api_server.seeds.qa_e2e_automator import QA_E2E_AUTOMATOR

pytestmark = pytest.mark.unit

_KINDS = {kind.value for kind in LLMProviderKind}


@pytest.mark.parametrize("agent", [*BUILTIN_AGENTS, QA_E2E_AUTOMATOR], ids=lambda a: a.slug)
def test_a_builtin_agent_never_pins_a_provider_outside_the_closed_catalog(agent) -> None:
    provider = agent.model_provider
    assert provider is None or provider in _KINDS, (
        f"{agent.slug} pinea model_provider={provider!r}, que no es un kind del catálogo "
        f"cerrado {sorted(_KINDS)}: sus copias adoptadas abortan `model_unresolved`"
    )


@pytest.mark.parametrize("agent", [*BUILTIN_AGENTS, QA_E2E_AUTOMATOR], ids=lambda a: a.slug)
def test_a_builtin_agent_inherits_the_model_instead_of_pinning_it(agent) -> None:
    """La regla de CI4 (ADR 0055) para todos: sin `provider` ni `model` en el
    `model_config` sembrado, la cadena de herencia decide."""
    config = agent.to_model_config()
    assert "provider" not in config and "model" not in config, (
        f"{agent.slug} siembra provider/model en model_config: las copias adoptadas "
        "lo heredan verbatim y dejan de seguir la cadena de herencia"
    )
    assert "system_prompts" in config
