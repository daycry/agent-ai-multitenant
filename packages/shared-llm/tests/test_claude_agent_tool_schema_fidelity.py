"""AUD16-05 (auditoría 2026-07-16): fidelidad de schema en claude_sdk.

El decorador ``@tool`` del claude-agent-sdk acepta un JSON Schema crudo
(``input_schema: type | dict``). El mapa simplificado ``{campo: tipo}`` que se
generaba antes descartaba ``required``, ``enum``, las descriptions por campo y
los objetos anidados — el modelo en claude_sdk adivinaba valores que en los
providers HTTP veía especificados (p. ej. los scopes de ``memory_recall``).
"""

from __future__ import annotations

from shared_llm.providers.claude_agent import _json_schema_to_tool_schema

_FALLBACK = {"type": "object", "properties": {"input": {"type": "string"}}}


def test_full_json_schema_passes_through_intact() -> None:
    params = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["private", "team_shared", "project_shared", "global"],
                "description": "Memory scope to search.",
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"action": {"type": "string"}},
                },
            },
        },
        "required": ["scope"],
        "additionalProperties": False,
    }
    assert _json_schema_to_tool_schema(params) == params


def test_schema_without_properties_falls_back_to_minimal_input() -> None:
    assert _json_schema_to_tool_schema(None) == _FALLBACK
    assert _json_schema_to_tool_schema({}) == _FALLBACK
    assert _json_schema_to_tool_schema({"type": "object"}) == _FALLBACK
