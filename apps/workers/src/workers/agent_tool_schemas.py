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
    "ask_human": {
        "name": "ask_human",
        "description": (
            "Pregunta a un humano y ESPERA su respuesta (ADR 0114). Usalo SOLO "
            "cuando una ambiguedad real te impida avanzar y no puedas decidirla "
            "tu (requisito contradictorio, eleccion de producto). El run se "
            "pausa; la respuesta llegara al siguiente intento como guia "
            "autoritativa. No lo uses para confirmar trabajo ya hecho."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "La pregunta concreta que necesitas respondida.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Opciones sugeridas (opcional, max 8).",
                },
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    "update_plan": {
        "name": "update_plan",
        "description": (
            "Guarda/actualiza TU plan de trabajo (visible cada turno). Uselo al "
            "empezar para trazar la estrategia y al cambiar de rumbo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "string",
                    "description": "El plan/estrategia actual, conciso (pasos).",
                }
            },
            "required": ["plan"],
        },
    },
    "memory_recall": {
        "name": "memory_recall",
        "description": (
            "Retrieve relevant past memories (learnings from projects and from "
            "mistakes) via semantic search. Use it when starting a task to "
            "leverage past experience."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language query for what you want to recall.",
                },
                "scopes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["private", "team_shared", "project_shared", "global"],
                    },
                    "description": (
                        "Scopes to query. EXACT valid values: 'private', "
                        "'team_shared', 'project_shared', 'global'. Optional; "
                        "defaults to the available ones."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "default": 5,
                    "description": "Maximum number of memories to return.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "memory_store": {
        "name": "memory_store",
        "description": (
            "Store a durable learning (semantic or episodic) for future tasks. "
            "Use it when wrapping up to record what you learned, including mistakes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact/learning to remember."},
                "type": {
                    "type": "string",
                    "enum": ["episodic", "semantic"],
                    "default": "semantic",
                },
                "scope": {"type": "string", "description": "Memory scope. Optional."},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
    # Orchestration family — also runtime-only (no catalog row; wired by
    # builtin_families). Schemas mirror agent_runtime.orchestration_tools.
    # AUD16-02 (auditoría 2026-07-16): kanban_update/agent_invoke YA NO se
    # anuncian — su drain worker-side nunca aterrizó y el ok=true era un éxito
    # falso (el efecto moría en el contenedor). El runtime las mantiene
    # registradas con un error honesto ("not wired") para llamadas a pelo.
    # task_comment SÍ tiene consumidor: el worker drena su efecto post-run a
    # PlanComment (rail comentarios→prompt).
    "task_comment": {
        "name": "task_comment",
        "description": (
            "Add a comment to YOUR task (progress notes, decisions, blockers). The "
            "platform persists it on the plan when the run finishes; humans and "
            "later runs on this task will see it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "body": {"type": "string", "description": "The comment text."},
            },
            "required": ["task_id", "body"],
            "additionalProperties": False,
        },
    },
}

# System CAPABILITY tools, advertised to the LLM independently of the allowlist
# (``include_system_tools=True``): memory + orchestration (runtime-only, no
# catalog row) so every agent can recall/store memory and participate in the
# Kanban — plus ``rag_search`` (P0-3, investigación 2026-07-11): the READ-ONLY
# KB search IS a catalog tool (semantic_search), but knowledge retrieval is as
# fundamental as memory and a mode whitelist that omitted it silenced the KB.
# Memory first (the universal capability).
# MUST stay in sync with agent_runtime.builtin_families.SYSTEM_FAMILY_TOOL_NAMES
# (the two packages deliberately do not import one another — the runtime is
# container-side). Order is the advertisement order.
SYSTEM_TOOL_NAMES: tuple[str, ...] = (
    "memory_recall",
    "memory_store",
    # AUD16-02: kanban_update/agent_invoke retiradas del anuncio (sin drain
    # worker-side, su ok=true era éxito falso); el runtime las mantiene
    # registradas con error honesto. Se re-añadirán cuando exista su consumidor.
    "task_comment",
    "rag_search",
    # P1-6: el scratchpad del loop (capacidad del grafo, no del registry).
    "update_plan",
    # ADR 0114: pregunta no terminal a humano (capacidad del grafo - el nodo
    # plan la intercepta y parquea el run por la maquinaria de aprobaciones).
    "ask_human",
)


# `shell_exec` está cableado POR PROYECTO desde `allowed_commands` (Plan 06.16),
# un dato que esta función no ve. Anunciarlo al agente sin restricción, sin saber
# si el proyecto permite algún comando, sería la misma promesa falsa que B-04
# acaba de retirar. Con un grant explícito sí se anuncia (comportamiento previo).
_PROJECT_WIRED_TOOL_NAMES: frozenset[str] = frozenset({"shell_exec"})


def _default_unrestricted_tool_names() -> list[str]:
    """Las tools que un agente SIN restricción por-agente puede ya ejecutar.

    ``allowed_tools=None`` significa «el registry no le restringe nada»: el
    runtime le deja llamar todas las tools cableadas. El anuncio, en cambio,
    partía de ``list(tool_names or [])``, así que ese mismo agente solo veía las
    de sistema — ni ``read_file``, ni ``write_file``, ni ``stack_exec`` (B-02).
    Asimetría pura entre lo que puede hacer y lo que sabe que puede hacer.

    Se deriva de ``RUNTIME_WIRED_TOOL_NAMES``, la misma fuente que decide qué es
    ejecutable, para que no pueda reintroducirse una tool sin ejecutor. Import
    defensivo por el mismo motivo que :func:`_catalog_by_canonical`.
    """
    try:
        import importlib

        tool_names = importlib.import_module("shared_domain.tool_names")
    except Exception:  # pragma: no cover - defensive: domain package optional
        return []
    wired: frozenset[str] = tool_names.RUNTIME_WIRED_TOOL_NAMES
    return sorted(wired - _PROJECT_WIRED_TOOL_NAMES)


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
    is_runtime_wired = tool_names.is_runtime_wired
    for tool in builtin_tools.BUILTIN_TOOLS:
        # g4 (audit 2026-07-03): a catalog builtin WITHOUT a runtime executor
        # (apply_patch / search_code / summarize_text) must never be advertised
        # to the model — even if a role/team seed assigned it — or the call dies
        # as "unknown tool" (run 019f27ff, agente QA CI4). Custom/MCP tools arrive
        # via tool_specs, not this builtin loop, so this only drops not-wired
        # builtins, never a legitimately-wired MCP `<server>.<tool>`.
        if not is_runtime_wired(tool.name):
            continue
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

    The flag also decides what ``tool_names=None`` means (task_wf_11, B-02). On
    the task-execution path it means "no per-agent restriction", so the agent is
    told about the whole wired catalog — what the registry already lets it run.
    On the chat path the absence of an allowlist does NOT mean "give it
    everything": there the restriction comes from the mode, and advertising the
    catalog would bypass it. Same sentinel, two honest readings, and the flag is
    exactly the axis that distinguishes them.

    Returns ``[]`` when there is nothing to advertise — the caller then omits the
    ``tools`` key (no change for tool-less agents that opt out of system tools)."""
    # An explicit empty allowlist means "block every tool" (discussion mode):
    # nothing is advertised, not even system tools. ``None`` means "no per-agent
    # restriction" and still gets system tools when requested.
    if tool_names is not None and len(tool_names) == 0:
        return []

    effective: list[str] = list(tool_names or [])
    if tool_names is None and include_system_tools:
        # REVERTIDO (auditoría adversarial 2026-07-25). Aquí se anunciaba además
        # `_default_unrestricted_tool_names()` —el catálogo cableado— con la
        # premisa de que «sin restricción por agente el runtime le deja llamar
        # todas las tools cableadas». **Esa premisa es falsa**, y justo en este
        # caso: `allowed_tools` es None exactamente cuando el agente no tiene
        # filas en `agent_tools`, que es cuando `tool_specs` también viene None
        # (mismo `if not rows: return None`); y sin `tool_specs` el runtime NO
        # llama a `register_builtin_families`, solo a `_wire_system_families`
        # (`agent_runtime/__main__.py:850-853`). Resultado: se anunciaban
        # `read_file`/`write_file`/`stack_exec`… sin ejecutor, y cada llamada
        # moría en «unknown tool» — la promesa falsa de B-04 reintroducida
        # mientras se arreglaba B-04.
        #
        # Lo que SÍ se anuncia son las tools que este run cablea de verdad por
        # spec (custom, docker_command y las MCP del proyecto, task_wf_10):
        # `register_tool_specs` las registra, así que la promesa se cumple.
        #
        # task_wf_11 queda REABIERTO: entregar su criterio —que un agente sin
        # grants pueda usar `read_file`— exige que el runtime cablee también las
        # familias de catálogo en esa rama, y eso ensancha la capacidad del
        # sandbox para todo agente sin asignaciones. Es una decisión de diseño,
        # no un ajuste del anuncio.
        effective.extend(
            str(spec["name"]) for spec in (tool_specs or []) if isinstance(spec.get("name"), str)
        )
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
