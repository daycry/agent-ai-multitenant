"""Los defaults de modelo que nadie mira: uno solo, y con precio.

H4-residuo del recorrido E2E del 2026-08-29
(``docs/roadmap/2026-08-29-hallazgos-e2e-hello-world-v2.md``). El hallazgo H4
original —«el desplegable no ofrece Sonnet 5»— quedó REFUTADO: la lista sale del
catálogo de precios y está al día. Lo que quedó en pie es el residuo, y es peor
porque no se ve desde ninguna pantalla: **los defaults de CÓDIGO**, los que
gobiernan a quien NO elige modelo. Estaban en tres sitios, en tres generaciones
distintas y sin nada que los atase:

    packages/shared-llm/.../claude_agent.py   default_model = "claude-sonnet-4-5"
    apps/api-server/.../db/platform_settings  DEFAULT_MODEL_CONFIG["model"] = "claude-sonnet-4"
    apps/api-server/.../chat/cost.py          claude-opus-4-7 / claude-sonnet-4-6

Este fichero no fija «cuál es el modelo bueno»: fija las dos relaciones que
hacían falta para que la deriva se viese, más el trinquete de generación.

1. **El default de plataforma tiene que tener precio.** Si no, el estimador de
   coste marca como «sin precio» el modelo con el que la propia plataforma
   ejecuta por defecto — y el operador lee un plan a 0 € sin saber por qué.

2. **El cliente de Claude y la plataforma tienen que decir lo MISMO.** Son dos
   defaults para el mismo camino (`claude_sdk`): que difieran significa que la
   plataforma promete un modelo y el cliente envía otro, y ninguna pantalla lo
   enseña. Es exactamente lo que llevaba pasando.

3. **Y el catálogo de respaldo no arrastra ids que ya nadie usa.** Es un
   trinquete a propósito: obliga a un edit deliberado cuando la generación se
   mueve, que es justo lo que no ocurrió aquí. Los precios de ese catálogo son
   sólo el respaldo del arranque en frío — en una instalación viva mandan las
   filas de ``model_prices`` (``cost_resolution.load_price_catalog``, la fila de
   la BD gana sobre el respaldo).

   **Ojo, que esta tercera se escribió mal la primera vez** y el error costó un
   defecto: decía «una sola generación», y aplicada al pie de la letra ordenó
   borrar los precios de dos modelos que once agentes built-in siguen usando.
   Corregida en la ola 2 (ver el docstring de su test): quedarse exige ser de la
   generación vigente **o** estar sembrado por alguien.
"""

from __future__ import annotations

import inspect

import pytest
from api_server.chat.cost import DEFAULT_AI_PRICE_CATALOG
from api_server.db.platform_settings import DEFAULT_MODEL_CONFIG
from api_server.seeds.builtin_agents import BUILTIN_AGENTS
from shared_llm.providers.claude_agent import ClaudeAgentProvider

pytestmark = pytest.mark.unit

# La generación que este producto tiene por vigente. Mover esto es una decisión,
# no un descuido: si al cambiarla algo falla, es que un default se quedó atrás.
CURRENT_CLAUDE_MODELS = {
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5",
}


def _claude_agent_default_model() -> str:
    """El ``default_model`` que usa quien construye el proveedor sin pasarlo."""
    default = inspect.signature(ClaudeAgentProvider.__init__).parameters["default_model"].default
    assert isinstance(default, str) and default, "ClaudeAgentProvider ya no declara default_model"
    return default


def test_the_platform_default_model_is_priced_by_the_fallback_catalog() -> None:
    """Sin esto, el modelo por defecto de la plataforma sale «sin precio» en el
    estimador de un plan: coste 0 con un aviso, en el arranque en frío."""
    model = DEFAULT_MODEL_CONFIG["model"]
    assert model in DEFAULT_AI_PRICE_CATALOG.prices, (
        f"el default de plataforma ({model!r}) no tiene precio en el catálogo de "
        f"respaldo; conocidos: {sorted(DEFAULT_AI_PRICE_CATALOG.prices)}"
    )


def test_the_claude_client_default_matches_the_platform_default() -> None:
    """Dos defaults para el MISMO camino (`claude_sdk`) que dicen cosas
    distintas es la deriva de H4-residuo: la plataforma promete un modelo y el
    cliente envía otro, sin que ninguna pantalla lo enseñe."""
    assert DEFAULT_MODEL_CONFIG["provider"] == "claude_sdk", (
        "si el default de plataforma deja de ser claude_sdk, esta pareja hay que "
        "reformularla en vez de darla por buena"
    )
    assert _claude_agent_default_model() == DEFAULT_MODEL_CONFIG["model"]


@pytest.mark.parametrize(
    "label,model",
    [
        ("default de plataforma", DEFAULT_MODEL_CONFIG["model"]),
        ("default del cliente Claude", _claude_agent_default_model()),
    ],
)
def test_the_code_defaults_name_the_current_generation(label: str, model: str) -> None:
    assert model in CURRENT_CLAUDE_MODELS, (
        f"el {label} apunta a {model!r}, que no es de la generación vigente "
        f"{sorted(CURRENT_CLAUDE_MODELS)}"
    )


def test_the_fallback_catalog_names_one_claude_generation() -> None:
    """El catálogo de respaldo no puede arrastrar ids que **ya nadie usa**.

    **Corregida la premisa (hallazgo MEDIO 5 de la ola 2 del ADR 0162).** Esta
    guarda decía «sólo la generación vigente», y su docstring justificaba el
    recorte con «un id que ya nadie usa es un precio que nadie comprueba». La
    segunda mitad es la buena; la primera no se sigue de ella. Aplicada al pie de
    la letra hizo que se borraran los precios de ``claude-opus-4-7`` y
    ``claude-sonnet-4-6``, que son **los modelos de once agentes built-in**: la
    guarda no detectó la deriva, la ORDENÓ.

    Y borrar un precio en uso es peor que tenerlo desactualizado: el estimador
    deja de contar ese modelo (lo reporta como faltante) y su coste desaparece
    del presupuesto sin que nada falle.

    El trinquete se mantiene entero —un id que ni es de la generación vigente ni
    lo siembra nadie sigue rompiendo la suite—; lo que cambia es de dónde sale el
    permiso para quedarse: de las SEMILLAS, que es quien crea la obligación de
    cobrarlo, y no de una lista escrita a mano que se desincroniza a la primera.
    """
    # Desde el 2026-09-01 (auditoría, F-01) los built-in NO pinean modelo: heredan
    # por la cadena del ADR 0055. `seeded` queda normalmente vacío, y eso es lo
    # correcto; se conserva por si un built-in vuelve a pinear a propósito.
    seeded = {agent.model_name for agent in BUILTIN_AGENTS if agent.model_name}
    # Ids que copias de tenant adoptadas ANTES de la migración 0147 pueden seguir
    # pineando (la 0147 sólo despinea las que heredaron `anthropic` de fábrica;
    # un pin puesto a mano por el tenant se respeta). Mientras esas copias
    # existan, su precio de respaldo tiene que existir. Retirar un id de aquí es
    # una decisión: exige comprobar en la BD viva que ninguna copia lo usa.
    still_pinned_by_adopted_copies = {"claude-opus-4-7", "claude-sonnet-4-6"}

    claude_ids = {mid for mid in DEFAULT_AI_PRICE_CATALOG.prices if mid.startswith("claude-")}
    stale = claude_ids - CURRENT_CLAUDE_MODELS - seeded - still_pinned_by_adopted_copies
    assert not stale, (
        f"el catálogo de respaldo arrastra ids que ya no son de la generación vigente "
        f"{sorted(CURRENT_CLAUDE_MODELS)} y que ningún agente built-in usa: {sorted(stale)}"
    )
