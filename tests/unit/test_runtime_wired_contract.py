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


# ---------------------------------------------------------------------------
# La puerta de atrás (auditoría adversarial 2026-07-25): retirar una tool de
# `RUNTIME_WIRED_TOOL_NAMES` NO bastaba para dejar de anunciarla.
#
# El drop del catálogo (`_catalog_by_canonical` descarta las no cableadas) deja el
# nombre LIBRE en el índice de esquemas, y el bucle de `tool_specs` lo rellena:
# `serialize_agent_tool_specs` serializa TODAS las filas asignadas salvo
# `shell_exec`, y `_tool_to_spec` les adjunta `input_schema` + `description`. Así
# que a cualquier agente que tuviera `send_notification` concedida se le seguía
# anunciando, con su esquema, y la llamada moría igual.
#
# Las tools de TENANT (custom, MCP del proyecto) sí deben pasar por esa vía: el
# runtime las registra de verdad con `register_tool_specs`. Lo que no puede pasar
# es que un builtin de PLATAFORMA sin ejecutor entre por ahí.
# ---------------------------------------------------------------------------
def test_an_unwired_platform_builtin_cannot_sneak_in_through_a_spec() -> None:
    from workers.agent_tool_schemas import build_model_tool_schemas

    spec = [
        {
            "name": "send_notification",
            "implementation_type": "python_function",
            "config": {},
            "input_schema": {"type": "object"},
            "description": "su ejecutor devuelve 'not wired'",
        }
    ]
    advertised = {
        t["function"]["name"]
        for t in build_model_tool_schemas(
            ["send_notification", "read_file"], spec, include_system_tools=True
        )
    }
    assert "send_notification" not in advertised
    assert "read_file" in advertised, "la tool cableada del mismo allowlist sobrevive"


def test_a_tenant_tool_still_comes_through_its_spec() -> None:
    """La otra cara: las MCP del proyecto y las custom del tenant se anuncian por
    esta misma vía (task_wf_10) y el runtime SÍ las registra."""
    from workers.agent_tool_schemas import build_model_tool_schemas

    spec = [
        {
            "name": "context7.query_docs",
            "implementation_type": "mcp_tool",
            "config": {},
            "input_schema": {"type": "object"},
            "description": "Busca documentación.",
        }
    ]
    advertised = {
        t["function"]["name"] for t in build_model_tool_schemas(["context7.query_docs"], spec)
    }
    assert "context7.query_docs" in advertised
