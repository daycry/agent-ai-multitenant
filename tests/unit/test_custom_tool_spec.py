"""Custom/typed tools must carry their input_schema in the serialized spec
(auditoría zona 'tools-mcp-skills', hallazgo high/bug).

Regression: ``_tool_to_spec`` emitted only ``{name, implementation_type, config}``,
so the worker's ``build_model_tool_schemas`` (which needs ``input_schema``) skipped
every custom tool → the LLM was never told the tool existed and never called it.
The whole custom-tools feature (http_endpoint / python_function / docker_command)
was inert.
"""

from __future__ import annotations

from api_server.agent_tools_enforcement import _tool_to_spec
from api_server.db.domain import Tool

_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string"}},
    "required": ["id"],
}


def _custom_http_tool() -> Tool:
    return Tool(
        name="lookup_customer",
        description="Busca un cliente por id en la API interna.",
        input_schema=_SCHEMA,
        implementation_type="http_endpoint",
        implementation_ref="https://internal/api/customer/{id}",
    )


def test_spec_carries_input_schema_and_description() -> None:
    spec = _tool_to_spec(_custom_http_tool())
    assert spec["input_schema"] == _SCHEMA
    assert spec["description"] == "Busca un cliente por id en la API interna."
    # The existing fields are preserved.
    assert spec["name"] == "lookup_customer"
    assert spec["implementation_type"] == "http_endpoint"


def test_custom_tool_is_announced_to_the_model() -> None:
    # End-to-end: a custom tool's spec must make it into the model tool schemas.
    from workers.agent_tool_schemas import build_model_tool_schemas

    spec = _tool_to_spec(_custom_http_tool())
    schemas = build_model_tool_schemas(["lookup_customer"], [spec])
    fns = [s["function"]["name"] for s in schemas]
    assert "lookup_customer" in fns
    announced = next(s["function"] for s in schemas if s["function"]["name"] == "lookup_customer")
    assert announced["parameters"] == _SCHEMA
