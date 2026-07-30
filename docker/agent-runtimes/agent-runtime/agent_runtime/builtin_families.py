"""Boot-time registration of the executable builtin tool families under
their CANONICAL names (Plan 06.18 task_06_18_05, ADR 0048/0049).

Background
----------
Until this task the agent-runtime boot path (``__main__.run_task``) only
mounted ``default_registry()`` (``echo`` + ``noop``) plus a conditional
``shell_exec``. Every other family — file ops, network, notifications,
orchestration, knowledge (RAG/Docling), memory — had a real executor
(``file_tools``, ``http_tool``, ``orchestration_tools``, ``rag_tools``,
``docling_tools``, ``memory_tools``) **but was never registered in
production**. So any tool an operator assigned other than ``shell_exec``
reached the loop as a silent ``ToolResult(ok=False, 'unknown tool')``.

This module is the single seam the boot path calls to fix that. It registers
each family under the **canonical** catalog name (ADR 0048) — ``read_file``
not the runtime-legacy ``file_read``; ``http_get`` / ``http_post`` not the
generic ``http_request``; ``send_notification`` not ``notify_user`` — so the
registered names match what ``combine_tool_allowlists`` canonicalises the
per-agent allowlist to. If the families registered under their legacy names
the allowlist (canonical) would reject every assigned tool by name mismatch,
re-introducing the very bug ADR 0048 closes.

What this module does NOT register
----------------------------------
* ``git`` — no runtime executor exists yet (ADR 0049 marks the family
  "No disponible aún"); registering placeholders would lie about
  availability.
* ``run_*`` (``run_pytest`` / ``run_lint`` / …) and tenant-custom
  ``http_endpoint`` / ``python_function`` tools — those are ``docker_command``
  / typed rows wired by :func:`agent_runtime.tool_wiring.register_tool_specs`
  from the worker-serialised ``tool_specs`` list, not here.
* ``shell_exec`` — wired per project from ``allowed_commands`` (Plan 06.16).
* MCP ``<server>.<tool>`` — wired by :func:`mcp_tools.register_mcp_server`
  once a server is connected (Plan 06.18 task_06_18_12).

Per-family feature flag
-----------------------
Each family is gated by an operator-tunable env flag
``AGENT_TOOL_FAMILY_<FAMILY>`` (e.g. ``AGENT_TOOL_FAMILY_RED=0`` disables the
network family). Default is **enabled**; a flag set to a falsy value
(``0`` / ``false`` / ``no`` / ``off``) disables that family. This is the
06.15-style risk control: cabling a family can surface latent behaviour
(egress, timeouts), so the operator can switch one off without a code change.
The flags only matter once the boot decides to wire families at all — that
decision (backward-compat) lives in ``__main__`` and keys off the presence of
``tool_specs`` in the spec.
"""

from __future__ import annotations

import os

from agent_runtime.docling_tools import DoclingTools
from agent_runtime.file_tools import WorkspaceFiles
from agent_runtime.http_tool import HttpRequestTool
from agent_runtime.internal_api import InternalAgentAPI
from agent_runtime.memory_tools import MemoryTools
from agent_runtime.orchestration_tools import OrchestrationSink, OrchestrationTools
from agent_runtime.rag_tools import RagTools
from agent_runtime.tools import ToolFn, ToolRegistry

#: Env-var prefix the operator sets to toggle a family (no hardcode in callers).
FAMILY_FLAG_PREFIX = "AGENT_TOOL_FAMILY_"

#: Where the file family reads/writes when the worker does not mount the
#: default ``/workspace`` (e.g. tests). The worker mounts ``/workspace`` in
#: production, so the default is unchanged for real runs.
_WORKSPACE_ROOT_ENV = "AGENT_WORKSPACE_ROOT"

# The family identifiers the operator references in the flag name. Kept as a
# stable tuple so a contract/UI can enumerate them.
FAMILY_FILE = "file"
FAMILY_RED = "red"
FAMILY_NOTIFICACION = "notificacion"
FAMILY_ORQUESTACION = "orquestacion"
FAMILY_CONOCIMIENTO = "conocimiento"
FAMILY_MEMORIA = "memoria"
FAMILY_STACK = "stack"

ALL_FAMILIES: tuple[str, ...] = (
    FAMILY_FILE,
    FAMILY_RED,
    FAMILY_NOTIFICACION,
    FAMILY_ORQUESTACION,
    FAMILY_CONOCIMIENTO,
    FAMILY_MEMORIA,
    FAMILY_STACK,
)

_FALSY = {"0", "false", "no", "off", ""}


def family_enabled(family: str, flags: dict[str, bool] | None) -> bool:
    """Whether ``family`` should be wired.

    Precedence: an explicit entry in ``flags`` wins (tests / programmatic
    callers); otherwise the env flag ``AGENT_TOOL_FAMILY_<FAMILY>`` is read,
    defaulting to enabled. A falsy env value disables the family.
    """
    if flags is not None and family in flags:
        return flags[family]
    raw = os.environ.get(f"{FAMILY_FLAG_PREFIX}{family.upper()}")
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY


def _workspace_root() -> str:
    return os.environ.get(_WORKSPACE_ROOT_ENV) or "/workspace"


def register_builtin_families(
    registry: ToolRegistry,
    *,
    api: InternalAgentAPI | None,
    sink: OrchestrationSink,
    allowed_domains: frozenset[str] = frozenset(),
    flags: dict[str, bool] | None = None,
    task_id: str | None = None,
) -> list[str]:
    """Register every executable builtin family on ``registry`` under its
    canonical name, honouring the per-family feature flags.

    * ``api`` — the ``/internal/agent/*`` client the knowledge + memory
      families need. ``None`` skips those two families (a bare run with no
      minted token cannot reach the api-server).
    * ``sink`` — the :class:`OrchestrationSink` the worker drains; the
      orchestration + notification families record their effects there.
    * ``allowed_domains`` — the project egress allowlist the network family
      binds to.
    * ``task_id`` — the running task's id; the ``stack`` family (``stack_exec``)
      needs it to tell the worker which worktree to run the command over. A bare
      run (no api or no task id) skips it.

    Returns the canonical names actually registered (skips disabled families
    and the api-backed families when ``api is None``) so the boot path can log
    / a test can assert what landed.
    """
    registered: list[str] = []

    def _add(name: str, fn: ToolFn) -> None:
        registry.register(name, fn)
        registered.append(name)

    # --- file family: read_file / write_file / list_files (canonical) -------
    if family_enabled(FAMILY_FILE, flags):
        files = WorkspaceFiles(root=_workspace_root())
        _add("read_file", files.file_read)
        _add("write_file", files.file_write)
        _add("delete_file", files.file_delete)
        _add("list_files", files.file_list)

    # --- network family: http_get / http_post (canonical) -------------------
    # One generic HttpRequestTool backs both verbs; it reads the method from
    # the args, so http_get forces GET and http_post forces POST.
    if family_enabled(FAMILY_RED, flags):
        http = HttpRequestTool(allowed_domains=allowed_domains)
        _add("http_get", _verb_bound(http, "GET"))
        _add("http_post", _verb_bound(http, "POST"))

    # --- orchestration family: kanban_update / task_comment / agent_invoke --
    if family_enabled(FAMILY_ORQUESTACION, flags):
        orch = OrchestrationTools(sink)
        _add("kanban_update", orch.kanban_update)
        _add("task_comment", orch.task_comment)
        _add("agent_invoke", orch.agent_invoke)

    # --- notification family: send_notification (canonical) -----------------
    # The runtime executor is OrchestrationTools.notify_user; we register it
    # under the canonical catalog name send_notification (ADR 0048).
    if family_enabled(FAMILY_NOTIFICACION, flags):
        notifier = OrchestrationTools(sink)
        _add("send_notification", notifier.notify_user)

    # --- knowledge family: rag_search / document_convert / promote_to_kb ----
    if api is not None and family_enabled(FAMILY_CONOCIMIENTO, flags):
        rag = RagTools(api)
        _add("rag_search", rag.rag_search)
        docling = DoclingTools(api)
        _add("document_convert", docling.document_convert)
        _add("promote_to_kb", docling.promote_to_kb)

    # --- memory family: memory_recall / memory_store ------------------------
    if api is not None and family_enabled(FAMILY_MEMORIA, flags):
        memory = MemoryTools(api)
        _add("memory_recall", memory.memory_recall)
        _add("memory_store", memory.memory_store)

    # --- stack family: stack_exec (ADR 0093) --------------------------------
    # Runs a command in the project's runtime-template via the worker (the
    # sandbox has no Docker). Needs BOTH the internal-api client (the worker
    # round-trip) and the task id (which worktree). A bare run lacks one or the
    # other, so the tool is skipped honestly rather than failing every call.
    if api is not None and task_id and family_enabled(FAMILY_STACK, flags):
        from agent_runtime.stack_exec_tool import StackExecTool

        _add("stack_exec", StackExecTool(api, str(task_id)))

    return registered


# Runtime SYSTEM capability tools (memory + orchestration + KB search). Memory
# and orchestration have NO catalog row (not in api_server.seeds.builtin_tools),
# so they can NEVER be in a per-agent allowlist — yet every agent needs them to
# recall/store memory (H0) and move the Kanban / invoke subagents (H3).
# `rag_search` (P0-3, investigación 2026-07-11) IS a catalog tool
# (semantic_search), but KB retrieval is as fundamental as memory: a mode
# whitelist that omitted it silenced the agent's knowledge access. Only the
# READ-ONLY search is exempted; the mutating knowledge tools (document_convert /
# promote_to_kb) stay catalog-gated. All of these are wired regardless of
# `agent_tools` and exempt from the per-agent allowlist.
# MUST stay in sync with workers.agent_tool_schemas.SYSTEM_TOOL_NAMES (the two
# packages deliberately do not import one another — the runtime is container-side).
SYSTEM_FAMILY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "memory_recall",
        "memory_store",
        "kanban_update",
        "task_comment",
        "agent_invoke",
        "rag_search",
    }
)


def register_system_families(
    registry: ToolRegistry,
    *,
    api: InternalAgentAPI | None,
    sink: OrchestrationSink,
    flags: dict[str, bool] | None = None,
) -> list[str]:
    """Register ONLY the SYSTEM capabilities — orchestration + memory + KB search.

    These are wired ALWAYS by the boot path (even when the agent has no
    ``agent_tools`` and the catalog families stay un-wired), so memory recall /
    store, the Kanban tools and the KB search are available to every agent
    (H0/H3 / L5 / P0-3). The remaining catalog families (file / network / the
    mutating knowledge tools) stay gated on the presence of ``tool_specs`` via
    :func:`register_builtin_families`.

    Honours the same per-family flags as :func:`register_builtin_families`.
    ``api is None`` (a bare run with no minted token) skips the api-backed
    memory + knowledge families but still wires orchestration (sink-only).
    Returns the canonical names actually registered.
    """
    registered: list[str] = []

    def _add(name: str, fn: ToolFn) -> None:
        registry.register(name, fn)
        registered.append(name)

    if family_enabled(FAMILY_ORQUESTACION, flags):
        orch = OrchestrationTools(sink)
        _add("kanban_update", orch.kanban_update)
        _add("task_comment", orch.task_comment)
        _add("agent_invoke", orch.agent_invoke)

    if api is not None and family_enabled(FAMILY_MEMORIA, flags):
        memory = MemoryTools(api)
        _add("memory_recall", memory.memory_recall)
        _add("memory_store", memory.memory_store)

    # P0-3: la búsqueda en la KB (read-only) es capacidad de sistema — sin ella
    # un modo con whitelist dejaba al agente sin acceso al conocimiento en
    # silencio. Los mutadores de la familia siguen en register_builtin_families.
    if api is not None and family_enabled(FAMILY_CONOCIMIENTO, flags):
        rag = RagTools(api)
        _add("rag_search", rag.rag_search)

    return registered


def _verb_bound(http: HttpRequestTool, method: str) -> ToolFn:
    """Bind ``HttpRequestTool`` to a fixed HTTP verb so ``http_get`` and
    ``http_post`` are distinct canonical tools (the catalog splits the generic
    ``http_request`` into the two verbs — ADR 0048)."""

    def _call(args: dict[str, object]) -> object:
        merged = {**args, "method": method}
        return http(merged)

    return _call  # type: ignore[return-value]


__all__ = [
    "ALL_FAMILIES",
    "FAMILY_FLAG_PREFIX",
    "FAMILY_STACK",
    "SYSTEM_FAMILY_TOOL_NAMES",
    "family_enabled",
    "register_builtin_families",
    "register_system_families",
]
