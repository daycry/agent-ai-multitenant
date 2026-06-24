"""Unit tests for the agent-runtime model tool-schema builder (agentes #2)."""

from __future__ import annotations

from workers.agent_tool_schemas import build_model_tool_schemas


def _names(schemas: list[dict]) -> list[str]:
    return [s["function"]["name"] for s in schemas]


def test_empty_or_none_allowlist_yields_no_schemas() -> None:
    assert build_model_tool_schemas(None, None) == []
    assert build_model_tool_schemas([], None) == []


def test_openai_function_envelope_shape() -> None:
    out = build_model_tool_schemas(["memory_recall"], None)
    assert len(out) == 1
    fn = out[0]
    assert fn["type"] == "function"
    assert set(fn["function"]) == {"name", "description", "parameters"}
    assert fn["function"]["name"] == "memory_recall"
    # The memory_recall schema requires a query (mirrors the executor).
    assert fn["function"]["parameters"]["required"] == ["query"]


def test_memory_family_runtime_only_tools_are_advertised() -> None:
    out = build_model_tool_schemas(["memory_recall", "memory_store"], None)
    assert set(_names(out)) == {"memory_recall", "memory_store"}


def test_rag_search_resolves_from_catalog_via_canonical_alias() -> None:
    # The allowlist carries the CANONICAL runtime name `rag_search`; its schema
    # comes from the catalog `semantic_search` indexed by canonical name.
    out = build_model_tool_schemas(["rag_search"], None)
    assert _names(out) == ["rag_search"]
    assert out[0]["function"]["parameters"].get("type") == "object"


def test_catalog_builtin_tool_is_advertised() -> None:
    out = build_model_tool_schemas(["read_file"], None)
    assert _names(out) == ["read_file"]
    # read_file requires a path (from the catalog input_schema).
    assert "path" in out[0]["function"]["parameters"]["properties"]


def test_custom_tool_spec_input_schema_is_used() -> None:
    specs = [
        {
            "name": "run_pytest_custom",
            "description": "Run a custom pytest",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]
    out = build_model_tool_schemas(["run_pytest_custom"], specs)
    assert _names(out) == ["run_pytest_custom"]
    assert out[0]["function"]["description"] == "Run a custom pytest"


def test_unknown_tool_without_schema_is_skipped() -> None:
    out = build_model_tool_schemas(["definitely_not_a_tool"], None)
    assert out == []


def test_order_follows_allowlist_and_dedups() -> None:
    out = build_model_tool_schemas(["memory_recall", "memory_recall", "read_file"], None)
    assert _names(out) == ["memory_recall", "read_file"]
