"""La planificación del import de tools MCP, sin base de datos (`task_mk_01`, ADR 0166).

`plan_import` decide qué entra y qué se omite ANTES de tocar la BD, y es donde
viven los tres límites de D9 y la semántica de D2:

- L1: sin `tool_names` y más de 200 anunciadas ⇒ abstención (no truncado).
- L2: `input_schema` > 32 KiB ⇒ se omite con aviso (importarla con `{}`
  recrearía el defecto del ADR 0101).
- L3: `<server>.<tool>` > 120 ⇒ se omite con aviso; nunca se trunca ni se sufija,
  porque el nombre es lo que el runtime parsea.
- D2: `tool_names` ausente = todas las anunciadas y reconciliación (R3) activa;
  con lista = la multiselección del ADR 0052, sin reconciliación, y un nombre
  pedido que el servidor no anuncia degrada a `{}` (contrato del 0052 intacto).
"""

from __future__ import annotations

import pytest
from api_server.mcp.import_tools import (
    MAX_INPUT_SCHEMA_BYTES,
    MAX_TOOL_NAME_LEN,
    MAX_TOOLS_PER_SERVER,
    ImportAbstainedError,
    plan_import,
)
from shared_mcp import MCPTool


def _tool(name: str, schema: dict | None = None) -> MCPTool:
    return MCPTool(name=name, description=f"{name} desc", input_schema=schema or {"type": "object"})


def test_sin_seleccion_entran_todas_las_anunciadas_y_se_reconcilia() -> None:
    plan = plan_import(
        server_name="gh", announced=[_tool("read_file"), _tool("write_file")], tool_names=None
    )
    assert set(plan.to_upsert) == {"gh.read_file", "gh.write_file"}
    assert plan.reconcile is True
    assert plan.warnings == []


def test_con_seleccion_solo_entran_las_pedidas_y_no_se_reconcilia() -> None:
    plan = plan_import(
        server_name="gh",
        announced=[_tool("read_file"), _tool("write_file")],
        tool_names=["read_file", "read_file"],
    )
    assert list(plan.to_upsert) == ["gh.read_file"]
    assert plan.reconcile is False


def test_un_nombre_pedido_que_el_servidor_no_anuncia_degrada_a_spec_none() -> None:
    """Contrato del ADR 0052 para la selección explícita: no aborta el lote."""
    plan = plan_import(server_name="gh", announced=[_tool("read_file")], tool_names=["fantasma"])
    assert plan.to_upsert == {"gh.fantasma": None}


def test_l1_por_encima_del_tope_se_abstiene_y_lo_dice_con_el_numero() -> None:
    muchas = [_tool(f"t{i}") for i in range(MAX_TOOLS_PER_SERVER + 1)]
    with pytest.raises(ImportAbstainedError) as exc_info:
        plan_import(server_name="big", announced=muchas, tool_names=None)
    assert str(MAX_TOOLS_PER_SERVER + 1) in str(exc_info.value)
    assert "selección manual" in str(exc_info.value)


def test_l1_en_el_tope_exacto_entra() -> None:
    justas = [_tool(f"t{i}") for i in range(MAX_TOOLS_PER_SERVER)]
    plan = plan_import(server_name="big", announced=justas, tool_names=None)
    assert len(plan.to_upsert) == MAX_TOOLS_PER_SERVER


def test_l1_no_aplica_a_una_seleccion_explicita() -> None:
    """La lista ya tiene su propio tope en el esquema del request (`max_length=200`)."""
    muchas = [_tool(f"t{i}") for i in range(MAX_TOOLS_PER_SERVER + 5)]
    plan = plan_import(server_name="big", announced=muchas, tool_names=["t0", "t1"])
    assert set(plan.to_upsert) == {"big.t0", "big.t1"}


def test_l2_un_schema_enorme_se_omite_con_aviso() -> None:
    gordo = {"type": "object", "properties": {f"p{i}": {"type": "string"} for i in range(3000)}}
    assert len(str(gordo)) > MAX_INPUT_SCHEMA_BYTES
    plan = plan_import(
        server_name="gh", announced=[_tool("ok"), _tool("gordo", gordo)], tool_names=None
    )
    assert list(plan.to_upsert) == ["gh.ok"]
    assert plan.omitted == ["gordo"]
    assert any("KiB" in w and "gordo" in w for w in plan.warnings)


def test_l3_un_nombre_que_no_cabe_se_omite_y_nunca_se_trunca() -> None:
    largo = "x" * (MAX_TOOL_NAME_LEN)
    plan = plan_import(server_name="gh", announced=[_tool(largo), _tool("ok")], tool_names=None)
    assert list(plan.to_upsert) == ["gh.ok"]
    assert plan.omitted == [largo]
    assert all(len(n) <= MAX_TOOL_NAME_LEN for n in plan.to_upsert)
    assert any("trunca" in w for w in plan.warnings)


def test_el_nombre_se_normaliza_a_slug_como_en_el_endpoint_historico() -> None:
    plan = plan_import(server_name="gh", announced=[_tool("Read File")], tool_names=None)
    assert list(plan.to_upsert) == ["gh.read_file"]
