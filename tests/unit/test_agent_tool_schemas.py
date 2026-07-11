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


# ---------------------------------------------------------------------------
# System family tools (memory + orchestration) — runtime-only, NOT in the
# assignable catalog, so they can never be in a per-agent allowlist. They must
# be advertised to the LLM independently (include_system_tools=True), else no
# agent can recall/store memory or move the kanban (H0/H3). Off by default so
# the chat / mode-restricted callers stay unaffected.
# ---------------------------------------------------------------------------
def test_system_tools_off_by_default() -> None:
    # Default: an assigned agent gets ONLY its catalog tool — no memory leaks in.
    out = build_model_tool_schemas(["read_file"], None)
    assert _names(out) == ["read_file"]
    # And a tool-less agent still advertises nothing.
    assert build_model_tool_schemas(None, None) == []


def test_system_tools_advertised_for_unassigned_agent_when_requested() -> None:
    # The H0 regression: a tool-less agent (allowlist None) must still see the
    # memory + orchestration tools so it can recall/store and participate.
    out = build_model_tool_schemas(None, None, include_system_tools=True)
    assert set(_names(out)) == {
        "memory_recall",
        "memory_store",
        "kanban_update",
        "task_comment",
        "agent_invoke",
        "rag_search",
    }


def test_system_tools_advertised_alongside_assigned_tools() -> None:
    out = build_model_tool_schemas(["read_file"], None, include_system_tools=True)
    names = _names(out)
    # The assigned tool comes first; system tools follow, deduped.
    assert names[0] == "read_file"
    assert {"memory_recall", "memory_store", "kanban_update"} <= set(names)
    assert names.count("read_file") == 1


def test_block_all_allowlist_suppresses_even_system_tools() -> None:
    # An explicit EMPTY allowlist is the discussion mode's "block every tool";
    # system tools must NOT slip past it.
    assert build_model_tool_schemas([], None, include_system_tools=True) == []


def test_assigned_tool_already_a_system_tool_is_not_duplicated() -> None:
    out = build_model_tool_schemas(["memory_recall"], None, include_system_tools=True)
    assert _names(out).count("memory_recall") == 1


def test_orchestration_tools_have_schemas_mirroring_executors() -> None:
    out = build_model_tool_schemas(
        ["kanban_update", "task_comment", "agent_invoke"], None, include_system_tools=True
    )
    by = {s["function"]["name"]: s["function"]["parameters"] for s in out}
    assert by["kanban_update"]["required"] == ["task_id", "status"]
    assert by["task_comment"]["required"] == ["task_id", "body"]
    assert by["agent_invoke"]["required"] == ["agent_id", "prompt"]


def test_non_wired_builtins_are_never_advertised() -> None:
    # g4: apply_patch / search_code / summarize_text are in the assignable catalog
    # but have NO runtime executor. Even if a seed assigned them (51 CI4 agents had
    # search_code), the model must never be offered them — the call would die as
    # "unknown tool" (run 019f27ff). A wired sibling in the same allowlist survives.
    out = build_model_tool_schemas(
        ["read_file", "search_code", "apply_patch", "summarize_text"], None
    )
    names = set(_names(out))
    assert "read_file" in names
    assert names.isdisjoint({"search_code", "apply_patch", "summarize_text"})


def test_no_advertised_builtin_lacks_a_runtime_executor() -> None:
    # Parity guard (g4): whatever builtin the model can be offered MUST be
    # runtime-executable. Offer the WHOLE catalog and assert none of the
    # advertised builtins is un-wired.
    from api_server.seeds.builtin_tools import BUILTIN_TOOLS
    from shared_domain.tool_names import is_runtime_wired, to_canonical

    catalog_canonical = sorted({c for t in BUILTIN_TOOLS for c in to_canonical(t.name)})
    out = build_model_tool_schemas(catalog_canonical, None)
    advertised = set(_names(out))
    unwired = {n for n in advertised if not is_runtime_wired(n)}
    assert not unwired, f"advertised builtins without a runtime executor: {sorted(unwired)}"


def test_rag_search_advertised_as_system_capability() -> None:
    # P0-3 (investigación 2026-07-11): un modo con whitelist sin semantic_search
    # ya no silencia la KB — rag_search se advierte como capacidad de sistema.
    out = build_model_tool_schemas(["read_file"], None, include_system_tools=True)
    assert "rag_search" in _names(out)
    # El block-all explícito (discussion) sigue suprimiendo todo.
    assert build_model_tool_schemas([], None, include_system_tools=True) == []
