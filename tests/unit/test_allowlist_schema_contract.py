"""El invariante que mata la clase entera (task_wf_15).

B-01 (las tools MCP del proyecto permitidas pero sin esquema), B-02 (el agente
sin grants que no ve nada) y B-04 (`send_notification` anunciada sin ejecutor)
no son tres bugs: son **tres instancias del mismo fallo**, la divergencia entre
lo que el agente PUEDE llamar y lo que SABE que puede llamar. Cada uno se
arregló por separado y cada uno tardó meses en detectarse, porque la única señal
era un run que gastaba turnos en «unknown tool» sin que nadie lo mirase.

Este fichero fija las dos direcciones del contrato, para cualquier combinación
de agente / proyecto / modo:

  **→** toda tool del allowlist efectivo tiene esquema anunciado. Si no, el
        modelo no sabe que existe y jamás la usa (B-01, B-02).
  **←** todo esquema anunciado corresponde a algo ejecutable. Si no, el modelo
        la llama y se come un error de plataforma que no puede resolver (B-04).

Sin esto la clase volverá con la próxima vía de asignación.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_server.agent_tools_enforcement import (
    combine_tool_allowlists,
    extend_allowlist_with_project_mcp,
    merge_tool_specs,
)
from shared_domain.tool_names import is_runtime_wired
from workers.agent_tool_schemas import SYSTEM_TOOL_NAMES, build_model_tool_schemas

pytestmark = pytest.mark.unit


# `update_plan` y `ask_human` son capacidades del GRAFO del agente, no del
# registry de tools: el nodo `plan` las intercepta antes de llegar a un
# ejecutor. Por eso no están (ni deben estar) en `RUNTIME_WIRED_TOOL_NAMES`.
_GRAPH_CAPABILITIES: frozenset[str] = frozenset({"update_plan", "ask_human"})

_MCP_SPEC: dict[str, Any] = {
    "name": "context7.query_docs",
    "implementation_type": "mcp_tool",
    "config": {},
    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
    "description": "Busca documentación.",
}
_CUSTOM_SPEC: dict[str, Any] = {
    "name": "deploy_staging",
    "implementation_type": "http_endpoint",
    "config": {"url_template": "https://ci.example/deploy"},
    "input_schema": {"type": "object", "properties": {"ref": {"type": "string"}}},
    "description": "Despliega a staging.",
}


def _advertised(allowlist: list[str] | None, specs: list[dict[str, Any]] | None) -> set[str]:
    return {
        t["function"]["name"]
        for t in build_model_tool_schemas(allowlist, specs, include_system_tools=True)
    }


def unadvertised(allowlist: list[str] | None, specs: list[dict[str, Any]] | None) -> set[str]:
    """Names the run WILL let the agent call but never tells it about."""
    if allowlist is None:
        return set()
    return set(allowlist) - _advertised(allowlist, specs)


# ---------------------------------------------------------------------------
# → Todo lo permitido se anuncia
# ---------------------------------------------------------------------------
def test_the_detector_catches_a_planted_hole() -> None:
    """Un test de contrato que no puede fallar no vale nada: se comprueba que el
    detector detecta, metiendo a mano una tool sin esquema en el allowlist."""
    assert unadvertised(["read_file", "tool_que_no_existe"], None) == {"tool_que_no_existe"}


def test_builtin_grants_are_all_advertised() -> None:
    allowlist = combine_tool_allowlists({"read_file", "write_file", "run_pytest"}, None)
    assert unadvertised(allowlist, None) == set()


def test_project_mcp_tools_are_advertised_for_a_restricted_agent() -> None:
    """B-01: estaban en el allowlist y no en el anuncio."""
    allowlist = extend_allowlist_with_project_mcp(
        combine_tool_allowlists({"read_file"}, None), {"context7.query_docs"}
    )
    specs = merge_tool_specs(None, [_MCP_SPEC])
    assert unadvertised(allowlist, specs) == set()


def test_custom_and_mcp_tools_together_are_advertised() -> None:
    allowlist = extend_allowlist_with_project_mcp(
        combine_tool_allowlists({"read_file", "deploy_staging"}, None), {"context7.query_docs"}
    )
    specs = merge_tool_specs([_CUSTOM_SPEC], [_MCP_SPEC])
    assert unadvertised(allowlist, specs) == set()


def test_a_mode_restricted_run_advertises_its_intersection() -> None:
    allowlist = combine_tool_allowlists({"read_file", "write_file"}, {"read_file"})
    assert allowlist == ["read_file"]
    assert unadvertised(allowlist, None) == set()


def test_an_unrestricted_agent_is_told_about_what_it_can_run() -> None:
    """B-02: `None` = sin restricción; el registry le deja ejecutar el catálogo
    cableado, así que tiene que verlo.

    La aserción es de SIMETRÍA y no una lista escrita a mano: si nombrar una
    tool explícitamente la anuncia, no nombrar ninguna (= no restringir) no
    puede anunciar menos. Cualquier hueco ahí es exactamente B-02.
    """
    from workers.agent_tool_schemas import _default_unrestricted_tool_names

    unrestricted = _advertised(None, [_MCP_SPEC])
    candidates = [*_default_unrestricted_tool_names(), str(_MCP_SPEC["name"])]
    missing = {
        name for name in candidates if name in _advertised([name], [_MCP_SPEC])
    } - unrestricted
    assert missing == set()


def test_deny_all_advertises_nothing_and_allows_nothing() -> None:
    """El invariante se cumple también en el borde: `[]` bloquea todo."""
    assert build_model_tool_schemas([], [_MCP_SPEC], include_system_tools=True) == []
    assert unadvertised([], [_MCP_SPEC]) == set()


# ---------------------------------------------------------------------------
# ← Todo lo anunciado es ejecutable
# ---------------------------------------------------------------------------
def _spec_names(specs: list[dict[str, Any]] | None) -> set[str]:
    return {str(s["name"]) for s in (specs or []) if s.get("name")}


def _unexecutable(allowlist: list[str] | None, specs: list[dict[str, Any]] | None) -> set[str]:
    """Advertised names with nothing behind them.

    A name is executable if the runtime wires it (``RUNTIME_WIRED_TOOL_NAMES``),
    if this run wires it via a spec (tenant-custom / project MCP), or if it is a
    graph capability the agent loop intercepts before any executor.
    """
    return {
        name
        for name in _advertised(allowlist, specs)
        if not is_runtime_wired(name)
        and name not in _spec_names(specs)
        and name not in _GRAPH_CAPABILITIES
    }


@pytest.mark.parametrize(
    ("allowlist", "specs"),
    [
        (None, None),
        (None, [_MCP_SPEC, _CUSTOM_SPEC]),
        (["read_file", "write_file", "stack_exec"], None),
        (["read_file", "context7.query_docs"], [_MCP_SPEC]),
        (["deploy_staging"], [_CUSTOM_SPEC]),
        (list(SYSTEM_TOOL_NAMES), None),
    ],
)
def test_nothing_advertised_is_unexecutable(
    allowlist: list[str] | None, specs: list[dict[str, Any]] | None
) -> None:
    """B-04: una tool anunciada sin ejecutor le quema un turno al modelo con un
    error de plataforma que no puede resolver."""
    offenders = _unexecutable(allowlist, specs)
    assert offenders == set(), (
        f"{sorted(offenders)} se anuncian al modelo sin nada detrás. O se cablea "
        "su ejecutor, o se retiran del anuncio — pero no se le prometen al agente."
    )


def test_the_reverse_detector_catches_a_planted_promise() -> None:
    """El mismo control de calidad que en la dirección de ida.

    Se falsea un esquema runtime-only para una tool sin ejecutor: el detector
    tiene que delatarla. Es la forma exacta que tuvo B-04 — `send_notification`
    llevaba meses anunciándose con su esquema y devolviendo «not wired».
    """
    from workers import agent_tool_schemas

    ghost = {"name": "tool_fantasma", "description": "nadie la ejecuta", "parameters": {}}
    original = dict(agent_tool_schemas._RUNTIME_ONLY_SCHEMAS)
    agent_tool_schemas._RUNTIME_ONLY_SCHEMAS["tool_fantasma"] = ghost
    try:
        assert _unexecutable(["tool_fantasma"], None) == {"tool_fantasma"}
    finally:
        agent_tool_schemas._RUNTIME_ONLY_SCHEMAS.clear()
        agent_tool_schemas._RUNTIME_ONLY_SCHEMAS.update(original)
