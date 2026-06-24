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
    # Orchestration family — also runtime-only (no catalog row; wired by
    # builtin_families). Schemas mirror agent_runtime.orchestration_tools.
    "kanban_update": {
        "name": "kanban_update",
        "description": (
            "Mueve la tarea en el Kanban a un nuevo estado (backlog/ready/"
            "in_progress/in_review/blocked/done/cancelled)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "ID de la tarea a mover."},
                "status": {
                    "type": "string",
                    "enum": [
                        "backlog",
                        "ready",
                        "in_progress",
                        "in_review",
                        "blocked",
                        "done",
                        "cancelled",
                    ],
                },
            },
            "required": ["task_id", "status"],
            "additionalProperties": False,
        },
    },
    "task_comment": {
        "name": "task_comment",
        "description": "Añade un comentario a una tarea (progreso, decisiones, bloqueos).",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "body": {"type": "string", "description": "El texto del comentario."},
            },
            "required": ["task_id", "body"],
            "additionalProperties": False,
        },
    },
    "agent_invoke": {
        "name": "agent_invoke",
        "description": (
            "Solicita la ejecución de otro agente con un prompt (subtarea). Registra "
            "la intención; el worker la aplica con su propia autorización."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "prompt": {"type": "string"},
            },
            "required": ["agent_id", "prompt"],
            "additionalProperties": False,
        },
    },
}

# Runtime-only family tools (memory + orchestration) that are NOT in the
# assignable catalog and therefore can NEVER be in a per-agent allowlist. They
# are system CAPABILITIES, advertised to the LLM independently of the allowlist
# (``include_system_tools=True``) so every agent can recall/store memory and
# participate in the Kanban. Memory first (the universal capability).
# MUST stay in sync with agent_runtime.builtin_families.SYSTEM_FAMILY_TOOL_NAMES
# (the two packages deliberately do not import one another — the runtime is
# container-side). Order is the advertisement order.
SYSTEM_TOOL_NAMES: tuple[str, ...] = (
    "memory_recall",
    "memory_store",
    "kanban_update",
    "task_comment",
    "agent_invoke",
)


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
    *,
    include_system_tools: bool = False,
) -> list[dict[str, Any]]:
    """OpenAI ``function`` schemas for the tools the agent may call.

    ``tool_names`` is the effective allowlist (canonical ``Tool.name`` values, e.g.
    ``"memory_recall"``, ``"rag_search"``). Schema sources, in order: the runtime
    memory tools, the builtin catalog (indexed by canonical name), then custom
    ``tool_specs`` ``input_schema``. A tool whose schema is unknown is skipped (the
    model just isn't offered it).

    ``include_system_tools`` advertises the runtime-only **system family** tools
    (memory + orchestration, see :data:`SYSTEM_TOOL_NAMES`) IN ADDITION to the
    allowlist. They are not in the assignable catalog, so they could never appear
    in ``tool_names`` — yet the agent needs them to recall/store memory and move
    the Kanban (H0/H3). The task-execution path passes ``True``; chat / mode
    callers keep the default ``False``. An EXPLICIT empty allowlist (``[]``) is the
    discussion mode's "block every tool" and suppresses everything, system tools
    included.

    Returns ``[]`` when there is nothing to advertise — the caller then omits the
    ``tools`` key (no change for tool-less agents that opt out of system tools)."""
    # An explicit empty allowlist means "block every tool" (discussion mode):
    # nothing is advertised, not even system tools. ``None`` means "no per-agent
    # restriction" and still gets system tools when requested.
    if tool_names is not None and len(tool_names) == 0:
        return []

    effective: list[str] = list(tool_names or [])
    if include_system_tools:
        for system_name in SYSTEM_TOOL_NAMES:
            if system_name not in effective:
                effective.append(system_name)
    if not effective:
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
    for name in effective:
        entry = by_name.get(name)
        if entry is not None and name not in seen:
            seen.add(name)
            out.append({"type": "function", "function": entry})
    return out


__all__ = ["build_model_tool_schemas"]
