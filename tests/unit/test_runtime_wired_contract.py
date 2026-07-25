"""Contrato: lo que se ANUNCIA al modelo tiene que poder EJECUTARSE (B-04).

`RUNTIME_WIRED_TOOL_NAMES` es el filtro que decide qué tools del catálogo
llegan al esquema que ve el LLM (`agent_tool_schemas._catalog_by_canonical`
descarta las que no están). Una tool anunciada sin ejecutor real es una
promesa falsa: el modelo la llama, quema un turno y recibe un error de
plataforma que no puede resolver.

Ya pasó con `kanban_update` / `agent_invoke` (AUD16-02): se las retiró del
ANUNCIO pero NO de esta lista, así que las dos fuentes quedaron divergentes y
`send_notification` —el tercer caso idéntico— siguió anunciándose durante
meses. Este test fija la lista de las que no tienen consumidor para que
añadir una a `RUNTIME_WIRED_TOOL_NAMES` rompa aquí en vez de en un run.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# Las tres tools de orquestación cuyo ejecutor devuelve `ok=False, "not wired"`
# (`agent_runtime.orchestration_tools`): validan sus argumentos y NO emiten
# efecto porque nadie lo drena worker-side. `task_comment`, la cuarta de esa
# familia, SÍ tiene consumidor real y por eso no está aquí.
_NOT_WIRED_TOOLS: frozenset[str] = frozenset({"kanban_update", "agent_invoke", "send_notification"})


def test_not_wired_tools_are_never_advertised_to_the_model() -> None:
    from shared_domain.tool_names import RUNTIME_WIRED_TOOL_NAMES

    offenders = sorted(_NOT_WIRED_TOOLS & RUNTIME_WIRED_TOOL_NAMES)
    assert not offenders, (
        f"{offenders} se anuncian al modelo y su ejecutor devuelve 'not wired'. "
        "O se cablea su consumidor worker-side, o se retiran de "
        "RUNTIME_WIRED_TOOL_NAMES — pero no se le prometen al agente."
    )


def test_is_runtime_wired_filters_them_out() -> None:
    """El filtro que usa `_catalog_by_canonical` las descarta de verdad."""
    from shared_domain.tool_names import is_runtime_wired

    for name in sorted(_NOT_WIRED_TOOLS):
        assert not is_runtime_wired(name), f"{name} pasaría el filtro del anuncio"


def test_task_comment_stays_wired() -> None:
    """Guarda en el sentido contrario: `task_comment` sí tiene drain real (el
    worker lo persiste como comentario del plan al finalizar el run), así que
    retirarla sería una regresión, no una limpieza."""
    from shared_domain.tool_names import RUNTIME_WIRED_TOOL_NAMES, is_runtime_wired

    assert "task_comment" in RUNTIME_WIRED_TOOL_NAMES
    assert is_runtime_wired("task_comment")
