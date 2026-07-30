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
    # AUD16-02: kanban_update/agent_invoke ya NO se anuncian — su drain
    # worker-side no existe y anunciarlas producía llamadas con éxito falso
    # (hoy, error honesto en el runtime). task_comment sí tiene consumidor.
    out = build_model_tool_schemas(None, None, include_system_tools=True)
    assert {
        "memory_recall",
        "memory_store",
        "task_comment",
        "rag_search",
        "update_plan",
        # ADR 0114: la pregunta no terminal a humano es capacidad universal.
        "ask_human",
    } <= set(_names(out))


# ---------------------------------------------------------------------------
# task_wf_11 (B-02) — un agente SIN restricción ve las tools que puede ejecutar.
#
# `allowed_tools=None` significa «el registry no le restringe nada»: el runtime
# le deja ejecutar todas las tools cableadas. Pero el anuncio partía de
# `list(tool_names or [])`, así que ese mismo agente solo veía las seis de
# sistema — ni `read_file`, ni `write_file`, ni `stack_exec`. Asimetría pura
# entre lo que puede hacer y lo que sabe que puede hacer: un agente recién
# creado, sin asignaciones, era incapaz de tocar un fichero porque nadie le
# dijo que existiera la herramienta.
# ---------------------------------------------------------------------------
def test_unrestricted_agent_is_not_promised_tools_the_runtime_will_not_register() -> None:
    """REVERTIDO tras la auditoría adversarial: aquí se afirmaba que un agente sin
    grants ve `read_file`/`write_file`/`stack_exec`.

    La premisa era falsa. `allowed_tools` es None exactamente cuando el agente no
    tiene filas en `agent_tools`, que es cuando `tool_specs` también viene None
    (mismo `if not rows: return None`), y sin `tool_specs` el runtime NO llama a
    `register_builtin_families` — solo a `_wire_system_families`. Anunciarlas ahí
    reintroducía la promesa falsa de B-04 mientras se arreglaba B-04: el modelo
    las llamaba y se comía «unknown tool».

    task_wf_11 queda reabierto: su criterio exige cablear también las familias de
    catálogo en esa rama, que es una decisión de diseño sobre la capacidad del
    sandbox, no un ajuste del anuncio."""
    names = set(_names(build_model_tool_schemas(None, None, include_system_tools=True)))
    assert "read_file" not in names
    assert "stack_exec" not in names


def test_deny_all_is_still_deny_all() -> None:
    """`[]` es el bloqueo explícito del modo discusión y NO debe cambiar."""
    assert build_model_tool_schemas([], None, include_system_tools=True) == []


def test_a_concrete_allowlist_is_still_respected() -> None:
    """Una lista concreta sigue siendo una restricción: solo esas + las de
    sistema. Si el catálogo por defecto se colase aquí, la asignación por agente
    dejaría de significar nada."""
    names = set(_names(build_model_tool_schemas(["read_file"], None, include_system_tools=True)))
    assert "read_file" in names
    assert "write_file" not in names
    assert "stack_exec" not in names


def test_the_default_set_never_advertises_an_unwired_tool() -> None:
    """El catálogo por defecto se deriva de `RUNTIME_WIRED_TOOL_NAMES`, así que
    no puede reintroducir la promesa falsa que B-04 acaba de retirar."""
    from shared_domain.tool_names import is_runtime_wired

    out = build_model_tool_schemas(None, None, include_system_tools=True)
    unwired = {n for n in _names(out) if not is_runtime_wired(n)}
    # Las de sistema (`update_plan`, `ask_human`) son capacidades del GRAFO, no
    # del registry, y por eso no están en la lista de cableadas.
    assert unwired <= {"update_plan", "ask_human"}


def test_shell_exec_is_not_offered_by_default() -> None:
    """`shell_exec` se cablea por proyecto desde `allowed_commands`, que esta
    función no ve. Anunciarlo sin saber si hay comandos permitidos sería la
    misma promesa falsa de B-04 con otro nombre; con un grant explícito sí se
    anuncia, como hasta ahora."""
    assert "shell_exec" not in set(
        _names(build_model_tool_schemas(None, None, include_system_tools=True))
    )
    assert "shell_exec" in set(
        _names(build_model_tool_schemas(["shell_exec"], None, include_system_tools=True))
    )


def test_unrestricted_agent_also_sees_the_tools_this_run_wires() -> None:
    """Las tools de tenant/proyecto (custom, docker_command, MCP del proyecto)
    no están en el catálogo cableado — son de tenant, no de plataforma — pero el
    registry del runtime sí las registra. Sin esto, un agente sin grants seguiría
    sin ver justo las tools que el proyecto acaba de darle (B-01 + B-02 juntos)."""
    specs = [
        {
            "name": "context7.query_docs",
            "implementation_type": "mcp_tool",
            "config": {},
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            "description": "Busca en la documentación.",
        }
    ]
    names = set(_names(build_model_tool_schemas(None, specs, include_system_tools=True)))
    assert "context7.query_docs" in names


def test_a_restricted_agent_does_not_get_the_specs_for_free() -> None:
    """Con allowlist concreta manda la allowlist: un spec presente pero no
    permitido no se anuncia."""
    specs = [
        {
            "name": "context7.query_docs",
            "input_schema": {"type": "object"},
            "description": "x",
        }
    ]
    names = set(_names(build_model_tool_schemas(["read_file"], specs, include_system_tools=True)))
    assert "context7.query_docs" not in names


def test_the_chat_path_is_untouched() -> None:
    """Sin `include_system_tools` (el chat y los modos) `None` sigue sin anunciar
    nada: ahí la ausencia de allowlist no significa «dale todo», significa que
    la restricción la pone el modo."""
    assert build_model_tool_schemas(None, None) == []


def test_system_tools_advertised_alongside_assigned_tools() -> None:
    out = build_model_tool_schemas(["read_file"], None, include_system_tools=True)
    names = _names(out)
    # The assigned tool comes first; system tools follow, deduped.
    assert names[0] == "read_file"
    assert {"memory_recall", "memory_store", "task_comment"} <= set(names)
    assert names.count("read_file") == 1


def test_unwired_orchestration_tools_never_advertised() -> None:
    # AUD16-02: sin consumidor de sus efectos, kanban_update/agent_invoke no se
    # ofrecen al modelo NI con allowlist explícita — la llamada solo quemaría
    # un turno en el error honesto del runtime.
    out = build_model_tool_schemas(
        ["kanban_update", "agent_invoke", "task_comment"], None, include_system_tools=True
    )
    names = set(_names(out))
    assert "kanban_update" not in names
    assert "agent_invoke" not in names
    assert "task_comment" in names


def test_block_all_allowlist_suppresses_even_system_tools() -> None:
    # An explicit EMPTY allowlist is the discussion mode's "block every tool";
    # system tools must NOT slip past it.
    assert build_model_tool_schemas([], None, include_system_tools=True) == []


def test_assigned_tool_already_a_system_tool_is_not_duplicated() -> None:
    out = build_model_tool_schemas(["memory_recall"], None, include_system_tools=True)
    assert _names(out).count("memory_recall") == 1


def test_orchestration_tools_have_schemas_mirroring_executors() -> None:
    out = build_model_tool_schemas(["task_comment"], None, include_system_tools=True)
    by = {s["function"]["name"]: s["function"]["parameters"] for s in out}
    assert by["task_comment"]["required"] == ["task_id", "body"]


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
