"""Canonical tool-name source of truth (ADR 0048, Plan 06.18 task_06_18_03).

Three layers historically used divergent names for the same logical action: the
**catalog** (``read_file`` — what the operator sees and assigns), the
**chat-modes** (``file_read``) and the **runtime** (``file_read``). The
per-agent ∩ chat-mode tool intersection is computed on raw strings, so a tool
assigned as ``read_file`` and allowed by a mode as ``file_read`` intersected to
the empty set — the silent "unknown tool" failure described in ADR 0048.

This module is the single source of truth. The **canonical** names are the
catalog names; a retro-compatible **alias** layer maps the legacy
chat-mode/runtime names onto them (no hard rename, so existing ``agent_tools``
rows and chat-mode allowlists keep working). ``http_request`` is the one alias
that expands to *both* HTTP verbs (``http_get`` + ``http_post``); every other
alias is one-to-one. Unknown names (tenant-custom tools, MCP ``<server>.<tool>``)
pass through unchanged.

Kept dependency-free in ``packages/shared-domain`` so api-server, orchestrator
and the agent-runtime can all import it (mirrors how ``shared-llm`` /
``shared-mcp`` are shared).
"""

from __future__ import annotations

from collections.abc import Iterable

# The catalog names the operator sees and assigns
# (``api_server.seeds.builtin_tools``). A CI contract test (task_06_18_14)
# asserts this set stays in sync with the seed.
#
# The ``git_*`` family was retired from the seed in task_06_18_06 (ADR 0049):
# there is no ``register_git_tools`` executor, so offering it as assignable
# would lie about availability. It is intentionally absent from the catalog
# set below until a runtime executor exists.
_CATALOG_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "apply_patch",
        "delete_file",
        "http_get",
        "http_post",
        "list_files",
        "read_file",
        "run_build",
        "run_lint",
        "run_pytest",
        "run_typecheck",
        "search_code",
        "semantic_search",
        "send_notification",
        "shell_exec",
        "stack_exec",
        "summarize_text",
        "write_file",
    }
)

# Orchestration tools the runtime registers under the SAME name in every layer
# (catalog absent, but chat-modes + runtime agree) — canonical, no alias needed.
_ORCHESTRATION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "kanban_update",
        "task_comment",
        "agent_invoke",
    }
)

# Memoria y conocimiento: nombres que el RUNTIME registra y el catálogo no ofrece
# (`register_system_families` cablea memoria + `rag_search` para TODO agente, y
# `register_builtin_families` los mutadores de conocimiento). Igual que la
# familia de orquestación de arriba: no son asignables, pero son nombres DE
# PLATAFORMA, no de tenant.
#
# Faltaban aquí, y no era cosmético (prod-03 task_prod03_02, 2026-07-29):
#
#   * el contrato «toda clave de `DEFAULT_TOOL_CATEGORIES` es un nombre canónico»
#     (`test_tool_catalog_contract`) impedía darles categoría de aprobación —
#     o sea que la omisión BLOQUEABA cerrar el agujero del gate;
#   * `routers/tools._assert_name_available` rechaza un nombre de tenant que
#     colisione con un builtin usando precisamente este conjunto: sin ellos, un
#     tenant podía crear una tool llamada `memory_store`, y el registro por
#     ToolSpec —que corre DESPUÉS de las familias de sistema— la habría
#     sustituido silenciosamente por la suya.
#
# `RUNTIME_WIRED_TOOL_NAMES ⊆ CANONICAL_TOOL_NAMES` pasa a ser cierto con esto;
# `test_approval_gate_categories` lo pinea.
_SYSTEM_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # memory family (siempre cableada, exenta del allowlist por-agente)
        "memory_recall",
        "memory_store",
        # knowledge family — `rag_search` es el nombre ejecutable al que
        # aliasea el `semantic_search` del catálogo
        "rag_search",
        "document_convert",
        "promote_to_kb",
    }
)

#: The full set of canonical tool names known to the platform.
CANONICAL_TOOL_NAMES: frozenset[str] = (
    _CATALOG_TOOL_NAMES | _ORCHESTRATION_TOOL_NAMES | _SYSTEM_TOOL_NAMES
)

# Legacy alias -> canonical name(s). Retro-compatible (ADR 0048): the chat-mode
# and runtime namespaces resolve onto the catalog names through this map.
#
# ``semantic_search`` is the catalog/knowledge name the operator assigns; the
# runtime executes it as ``rag_search`` (``RagTools.rag_search`` registered by
# ``register_builtin_families``). task_06_18_06 reconciles the two in this
# single source of truth so an assigned ``semantic_search`` resolves to the
# executable ``rag_search`` instead of dying as ``unknown tool`` (ADR 0049).
_ALIAS_TO_CANONICAL: dict[str, frozenset[str]] = {
    "file_read": frozenset({"read_file"}),
    "file_write": frozenset({"write_file"}),
    "file_delete": frozenset({"delete_file"}),
    "file_list": frozenset({"list_files"}),
    "http_request": frozenset({"http_get", "http_post"}),
    "notify_user": frozenset({"send_notification"}),
    "semantic_search": frozenset({"rag_search"}),
}


# ---------------------------------------------------------------------------
# Runtime-wired set (ADR 0049, task_06_18_06)
# ---------------------------------------------------------------------------
# The names the agent-runtime can actually REGISTER and execute. This is the
# single source of truth the api-server consults to derive ``is_runtime_wired``
# on ``ToolResponse`` — so the catalog never offers as assignable something
# that ends up a silent ``unknown tool``. It mirrors, by canonical name, what
# the runtime boot path registers (a CI contract test in task_06_18_14 asserts
# the two stay in sync):
#
#   * builtin families (``register_builtin_families``): file / network /
#     orchestration / notification / knowledge (``rag_search`` + Docling) /
#     memory.
#   * the ``run_*`` ``docker_command`` tools wired from the serialised
#     ``tool_specs`` (``register_tool_specs``).
#   * ``shell_exec`` wired per project from ``allowed_commands`` (Plan 06.16).
#
# NOT wired (hence ``is_runtime_wired`` False): ``git_*`` (no executor —
# retired from the seed), ``apply_patch`` / ``search_code`` (the file family
# registers only read/write/list) and ``summarize_text`` (no executor yet).
RUNTIME_WIRED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # file family
        "read_file",
        "write_file",
        "delete_file",
        "list_files",
        # network family
        "http_get",
        "http_post",
        # orchestration family — SOLO `task_comment`. Las otras tres
        # (`kanban_update`, `agent_invoke`, `send_notification`) validan sus
        # argumentos y devuelven `ok=False, "not wired"` porque el drain
        # worker-side previsto nunca aterrizó: anunciarlas al modelo es una
        # promesa falsa que le quema un turno con un error que no puede
        # resolver. AUD16-02 retiró las dos primeras del ANUNCIO pero no de
        # esta lista, y por esa divergencia `send_notification` siguió
        # llegando al esquema. Se reincorporan cuando exista su consumidor;
        # `tests/unit/test_runtime_wired_contract.py` fija el invariante.
        # `task_comment` SÍ tiene drain real (el worker lo persiste como
        # comentario del plan al cerrar el run).
        "task_comment",
        # knowledge family (semantic_search aliases onto rag_search)
        "rag_search",
        "document_convert",
        "promote_to_kb",
        # memory family
        "memory_recall",
        "memory_store",
        # NOTA — los cuatro `run_*` SALIERON de esta lista (F5 de
        # registry-egress-followups, 2026-07-28). Son `docker_command`, y
        # `DockerCommandTool` dentro del sandbox falla SIEMPRE por diseño: la
        # imagen del agent-runtime no instala el paquete `docker` ni recibe
        # socket (ver `test_docker_command_tool_retired`). Anunciarlas al modelo
        # era prometerle cuatro tools imposibles — el mismo fallo B-04 que
        # `send_notification`, y con 62 grants vivos detrás. La vía real es
        # `stack_exec`: el worker corre el toolchain en el runtime-template del
        # proyecto (ADR 0093).
        #
        # Siguen en `_CATALOG_TOOL_NAMES` a propósito, y eso NO es un descuido:
        # si dejaran de ser nombres canónicos, `is_unwired_platform_builtin` no
        # los reconocería como builtins de plataforma, `tool_is_runtime_wired`
        # caería al atajo por `implementation_type` —que devuelve True para
        # `docker_command`— y una fila superviviente en una BD sin migrar
        # volvería a ser asignable y anunciable. `test_runtime_wired_contract`
        # fija las dos mitades.
        # per-project shell
        "shell_exec",
        # stack family — worker-mediated toolchain exec (ADR 0093)
        "stack_exec",
    }
)


def to_canonical(name: str) -> frozenset[str]:
    """Resolve a tool name to its canonical name(s).

    Returns a ``frozenset`` because one legacy alias (``http_request``) expands
    to both HTTP verbs. A name that is already canonical — or one we do not
    alias (tenant-custom / MCP ``<server>.<tool>``) — resolves to itself.
    """
    if name in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[name]
    return frozenset({name})


def to_canonical_set(names: Iterable[str]) -> frozenset[str]:
    """Canonicalise a collection of names, unioning and expanding aliases."""
    out: set[str] = set()
    for name in names:
        out |= to_canonical(name)
    return frozenset(out)


def is_runtime_wired(name: str) -> bool:
    """Whether a tool ``name`` resolves to something the runtime can execute.

    Resolves the name through the alias layer first (so the catalog
    ``semantic_search`` counts via ``rag_search``, and a legacy ``file_read``
    via ``read_file``) and reports whether ANY resulting canonical name is in
    :data:`RUNTIME_WIRED_TOOL_NAMES`. A tenant-custom / MCP name that is not
    aliased resolves to itself and is wired only if it literally appears in the
    set (custom tools become wired through their own ``implementation_type``
    handling, not this builtin set — they default to not-wired here).
    """
    return bool(to_canonical(name) & RUNTIME_WIRED_TOOL_NAMES)


def is_unwired_platform_builtin(name: str) -> bool:
    """Un nombre CANÓNICO de plataforma que el runtime no sabe ejecutar.

    La distinción que hay que tener clara: una tool de tenant con
    ``implementation_type`` tipado (``http_endpoint``, ``python_function``, …)
    la cablea ``register_tool_specs`` **se llame como se llame** — su tipo es la
    autoridad. Un builtin de plataforma, no: su nombre está en el catálogo
    canónico y lo que decide si existe ejecutor es
    :data:`RUNTIME_WIRED_TOOL_NAMES`, no lo que ponga la fila sembrada.

    ``send_notification`` es el caso que lo demuestra: la semilla lo declara
    ``python_function``, así que cualquier comprobación que cortocircuite por el
    tipo dirá que es ejecutable — y no lo es, porque su drain worker-side nunca
    aterrizó y devuelve ``ok=False, "not wired"``. Anunciárselo al modelo le
    quema un turno con un error que no puede resolver, y decírselo al operador
    en el diagnóstico le hace perseguir un fantasma.

    Vive aquí, en el dominio, porque la regla la necesitan a la vez el worker
    (para no anunciar la tool) y el api-server (para no declararla ejecutable), y
    dos copias de esta misma frase es exactamente cómo se separaron la última vez.
    """
    return name in CANONICAL_TOOL_NAMES and not is_runtime_wired(name)


__all__ = [
    "CANONICAL_TOOL_NAMES",
    "RUNTIME_WIRED_TOOL_NAMES",
    "is_runtime_wired",
    "is_unwired_platform_builtin",
    "to_canonical",
    "to_canonical_set",
]
