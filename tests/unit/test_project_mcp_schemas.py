"""Las tools MCP del proyecto llegan al MODELO, no solo al allowlist (task_wf_10).

ADR 0128 hizo que las tools MCP las aporte el proyecto y no un grant por agente,
y `extend_allowlist_with_project_mcp` las mete en `allowed_tools`. Pero el
anuncio al modelo lo construye `build_model_tool_schemas` a partir de tres
fuentes — los esquemas runtime-only, el catálogo builtin y `tool_specs` — y
`tool_specs` es **por agente** (`serialize_agent_tool_specs`). Una tool MCP de
proyecto quedaba por tanto *permitida pero invisible*: el modelo no sabía que
existía, así que jamás la llamaba y el ADR no entregaba nada.

El arreglo es que el dispatch aporte también los ESPECIFICADORES de esas tools,
derivados del mismo conjunto ya filtrado por la política de roles — si el
allowlist y el anuncio se derivasen por separado volverían a divergir, que es
exactamente la clase de fallo que fija `task_wf_15`.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_server.agent_tools_enforcement import merge_tool_specs
from workers.agent_tool_schemas import build_model_tool_schemas

pytestmark = pytest.mark.unit


_MCP_SPEC: dict[str, Any] = {
    "name": "context7.query_docs",
    "implementation_type": "mcp_tool",
    "config": {},
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    "description": "Busca en la documentación indexada.",
}


def _advertised(tools: list[dict[str, Any]]) -> set[str]:
    return {t["function"]["name"] for t in tools}


# ---------------------------------------------------------------------------
# La fusión de especificadores
# ---------------------------------------------------------------------------
def test_merge_keeps_the_none_sentinel_when_there_is_nothing_to_add() -> None:
    """`None` significa «clave ausente» en el contrato del run (sin familias
    nuevas). Convertirlo en `[]` cambiaría el mensaje."""
    assert merge_tool_specs(None, []) is None


def test_merge_promotes_none_to_a_list_when_the_project_contributes() -> None:
    """Un agente sin `agent_tools` (specs `None`) que corre en un proyecto con
    MCP sí necesita que sus esquemas viajen."""
    assert merge_tool_specs(None, [_MCP_SPEC]) == [_MCP_SPEC]


def test_merge_unions_by_name_with_the_agent_spec_winning() -> None:
    """Si la misma tool llega por las dos vías, manda la del agente: es la que
    lleva la configuración de ejecución que el runtime necesita."""
    agent_spec = {**_MCP_SPEC, "description": "la del agente"}
    out = merge_tool_specs([agent_spec], [_MCP_SPEC])
    assert out is not None
    assert len(out) == 1
    assert out[0]["description"] == "la del agente"


def test_merge_preserves_order_agent_first() -> None:
    agent_spec = {"name": "run_pytest", "implementation_type": "docker_command", "config": {}}
    out = merge_tool_specs([agent_spec], [_MCP_SPEC])
    assert out is not None
    assert [s["name"] for s in out] == ["run_pytest", "context7.query_docs"]


def test_merge_of_an_empty_agent_list_is_not_the_none_sentinel() -> None:
    """`[]` es un valor legítimo distinto de `None`; añadir MCP no lo borra."""
    out = merge_tool_specs([], [_MCP_SPEC])
    assert out == [_MCP_SPEC]


# ---------------------------------------------------------------------------
# El efecto que importa: el modelo ve el esquema
# ---------------------------------------------------------------------------
def test_a_restricted_agent_is_told_about_the_project_mcp_tool() -> None:
    """El bug B-01: la tool estaba en `allowed_tools` y no en `model.tools`."""
    allowlist = ["read_file", "context7.query_docs"]
    tools = build_model_tool_schemas(allowlist, [_MCP_SPEC])
    assert "context7.query_docs" in _advertised(tools)


def test_the_advertised_schema_is_the_tools_own() -> None:
    tools = build_model_tool_schemas(["context7.query_docs"], [_MCP_SPEC])
    fn = next(t["function"] for t in tools if t["function"]["name"] == "context7.query_docs")
    assert fn["parameters"] == _MCP_SPEC["input_schema"]
    assert fn["description"] == _MCP_SPEC["description"]


def test_a_spec_outside_the_allowlist_is_still_not_advertised() -> None:
    """Aportar el esquema no debe saltarse el filtro por rol: la política de
    `mcp_tool_roles` se aplica ANTES, al derivar el conjunto."""
    tools = build_model_tool_schemas(["read_file"], [_MCP_SPEC])
    assert "context7.query_docs" not in _advertised(tools)


def test_deny_all_still_advertises_nothing() -> None:
    assert build_model_tool_schemas([], [_MCP_SPEC], include_system_tools=True) == []
