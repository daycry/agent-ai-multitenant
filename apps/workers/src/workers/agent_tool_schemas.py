"""Build OpenAI-style tool schemas for the agent-runtime model (agentes #2).

The agent loop can only invoke a tool if the LLM was TOLD the tool exists. The
runtime advertises tools to the model via ``spec["model"]["tools"]`` →
``provider.complete(tools=…)`` → ``tool_calls``. Until now the model spec never
carried a ``tools`` key, so the LLM never saw ``memory_recall`` / ``rag_search`` /
``read_file`` / … and the agent could neither recall memory nor do work through
tools, for ANY provider.

This module builds those schemas for the agent's effective allowlist. Names in the
allowlist are already **canonical runtime names** (the orchestrator runs
``shared_domain.tool_names.to_canonical_set``, so the catalog ``semantic_search``
arrives as its runtime alias ``rag_search``). So the catalog schemas
(``api_server.seeds.builtin_tools`` — single source of truth, ADR 0048) are indexed
by their CANONICAL name, the runtime-only memory tools get explicit schemas here,
and serialized custom ``tool_specs`` fall back to their own ``input_schema``.

Worker-side on purpose: the worker image carries ``api_server`` + ``shared_domain``
(FROM ``api-server:ci``); the sandboxed runtime must not import the catalog.
"""

from __future__ import annotations

from typing import Any

# Runtime-registered tools with NO catalog row (builtin_families wires them, but
# they are not in BUILTIN_TOOLS). Schemas mirror the executors' validated args
# (agent_runtime.memory_tools.MemoryTools). Keyed by canonical runtime name.
_RUNTIME_ONLY_SCHEMAS: dict[str, dict[str, Any]] = {
    "memory_recall": {
        "name": "memory_recall",
        "description": (
            "Recupera memorias previas relevantes (aprendizajes de proyectos y de "
            "errores) por búsqueda semántica. Úsala al empezar una tarea para "
            "aprovechar experiencia pasada."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Consulta en lenguaje natural de lo que buscas recordar.",
                },
                "scopes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Scopes a consultar (private/team_shared/project_shared/global). "
                        "Opcional; por defecto los disponibles."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "default": 5,
                    "description": "Máximo de memorias a devolver.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "memory_store": {
        "name": "memory_store",
        "description": (
            "Guarda un aprendizaje duradero (semántico o episódico) para futuras "
            "tareas. Úsala al cerrar para registrar lo aprendido, incluidos errores."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "El hecho/aprendizaje a recordar."},
                "type": {
                    "type": "string",
                    "enum": ["episodic", "semantic"],
                    "default": "semantic",
                },
                "scope": {"type": "string", "description": "Scope de memoria. Opcional."},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
}


def _catalog_by_canonical() -> dict[str, dict[str, Any]]:
    """Catalog tool schemas keyed by CANONICAL runtime name (alias-expanded).

    e.g. the catalog ``semantic_search`` is indexed under ``rag_search`` (its
    runtime alias) so an allowlist that canonicalised it resolves the schema.
    Imported lazily + defensively: a catalog import failure must never break a
    dispatch (the agent just runs without the builtin schemas)."""
    out: dict[str, dict[str, Any]] = {}
    # Dynamic imports: api_server (the catalog) + shared_domain live in the base
    # image at runtime but aren't on the worker's mypy path; importlib keeps this
    # type-checkable. Defensive: any failure → no builtin schemas, never a crash.
    try:
        import importlib

        builtin_tools = importlib.import_module("api_server.seeds.builtin_tools")
        tool_names = importlib.import_module("shared_domain.tool_names")
    except Exception:  # pragma: no cover - defensive: catalog/domain optional
        return out
    to_canonical = tool_names.to_canonical
    for tool in builtin_tools.BUILTIN_TOOLS:
        entry = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        }
        for canonical in to_canonical(tool.name):
            # Use the canonical name in the advertised schema so it matches what
            # the runtime registry expects to execute.
            out[canonical] = {**entry, "name": canonical}
    return out


def build_model_tool_schemas(
    tool_names: list[str] | None,
    tool_specs: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """OpenAI ``function`` schemas for the tools the agent may call.

    ``tool_names`` is the effective allowlist (canonical ``Tool.name`` values, e.g.
    ``"memory_recall"``, ``"rag_search"``). Schema sources, in order: the runtime
    memory tools, the builtin catalog (indexed by canonical name), then custom
    ``tool_specs`` ``input_schema``. A tool whose schema is unknown is skipped (the
    model just isn't offered it). Returns ``[]`` when there is nothing to advertise
    — the caller then omits the ``tools`` key (no change for tool-less agents)."""
    if not tool_names:
        return []

    by_name: dict[str, dict[str, Any]] = dict(_RUNTIME_ONLY_SCHEMAS)
    for canonical_name, catalog_entry in _catalog_by_canonical().items():
        by_name.setdefault(canonical_name, catalog_entry)

    # Custom / typed tools serialized by the orchestrator (input_schema if present).
    for spec in tool_specs or []:
        spec_name = spec.get("name")
        if not spec_name or spec_name in by_name:
            continue
        schema = spec.get("input_schema")
        if isinstance(schema, dict):
            by_name[spec_name] = {
                "name": spec_name,
                "description": spec.get("description") or spec_name,
                "parameters": schema,
            }

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in tool_names:
        entry = by_name.get(name)
        if entry is not None and name not in seen:
            seen.add(name)
            out.append({"type": "function", "function": entry})
    return out


__all__ = ["build_model_tool_schemas"]
