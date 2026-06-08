"""Per-agent tool enforcement (Plan 06.15 task_06_15_02).

Plan 06.15 task_06_15_01 added the write surface (``PUT /agents/{id}/tools``)
that fills the ``agent_tools`` M:N junction. This module is the *read /
resolve* seam that turns those rows into the allowlist the agent-runtime's
``ToolRegistry`` enforces at call time.

Two pure pieces, kept free of router/HTTP concerns so the orchestrator
(which builds the worker run payload) and the integration tests can both
import them:

  * :func:`resolve_agent_tool_names` — the async DB read: the set of
    ``Tool.name`` values wired to one agent, or ``None`` when the agent has
    no rows. The ``None`` sentinel is load-bearing: **no rows means no
    per-agent restriction**, the backward-compatible behaviour for every
    agent that existed before this plan. An agent that *does* have rows is
    restricted to exactly those tool names.

  * :func:`combine_tool_allowlists` — the pure combination of the per-agent
    set with the active chat mode's allowlist (``ChatModeConfig.allowed_tools``,
    task_06_14_07). Both are independent restrictions, so the effective set
    is their **intersection**; either being ``None`` means "that layer does
    not restrict". The result is what gets threaded into the task spec's
    ``allowed_tools`` (``ExecutionRequest.allowed_tools`` →
    ``_agent_spec`` → ``AGENT_TASK_SPEC`` → ``ToolRegistry.set_allowed_tools``).

The runtime already rejects a tool outside its configured allowlist before
the tool function runs (see ``agent_runtime.tools.ToolRegistry.call``); this
module only decides *what* that allowlist is for a given agent + mode. It is
NOT the layered guardrail engine (Plan 11) — it is the minimal call-time
enforcement that makes a per-agent assignment real instead of advisory.

Tool *names* (not ids) are forwarded. The catalog, chat-modes and runtime
historically used divergent names for the same action (``read_file`` vs
``file_read``), so both layers are normalised through
``shared_domain.tool_names.to_canonical_set`` (ADR 0048) before intersecting —
otherwise ``read_file`` (catalog) and ``file_read`` (chat-mode) would intersect
to the empty set by mere name mismatch (the silent "unknown tool" failure).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from shared_domain.tool_names import is_runtime_wired, to_canonical_set
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import AgentTool, Tool

#: Canonical name of the per-project shell tool. It is the one tool whose
#: effective availability depends on a project setting (``allowed_commands``)
#: rather than on the agent∩mode intersection alone, so the effective-set
#: computation treats it specially.
_SHELL_EXEC = "shell_exec"

# Default argv the four ``run_*`` builtins execute inside their runtime
# container (Plan 06.18 task_06_18_05). The Tool rows carry the runtime
# template (``implementation_ref``) but not a command, so the orchestrator
# serialises a sane default ``command_template`` here; ``{path}`` resolves
# from the call args by the docker_command executor. Tools absent from this
# map fall back to a no-op echo so the spec is still well-formed (the
# operator can override per-tool config in a later plan).
_RUN_TOOL_COMMANDS: dict[str, list[str]] = {
    "run_pytest": ["pytest", "{path}"],
    "run_lint": ["ruff", "check", "{path}"],
    "run_typecheck": ["mypy", "{path}"],
    "run_build": ["make", "build"],
}


async def resolve_agent_tool_names(session: AsyncSession, agent_id: UUID) -> frozenset[str] | None:
    """The set of ``Tool.name`` values assigned to ``agent_id``, or ``None``.

    Returns ``None`` when the agent has **no** ``agent_tools`` rows — the
    signal for "no per-agent restriction" (current behaviour, no regression).
    A non-empty frozenset restricts the agent to exactly those tool names.

    Soft-deleted tools are excluded; an assignment whose tool was deleted
    simply does not contribute a name (it never becomes callable anyway).

    The query is tenant-safe by construction: under a tenant-scoped session
    RLS hides cross-tenant agents/tools, and the orchestrator (BYPASSRLS)
    only ever calls this for an agent it has already resolved within the
    task's tenant.
    """
    rows = await session.execute(
        select(Tool.name)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent_id, Tool.deleted_at.is_(None))
    )
    names = rows.scalars().all()
    if not names:
        return None
    return frozenset(names)


async def serialize_agent_tool_specs(
    session: AsyncSession, agent_id: UUID
) -> list[dict[str, Any]] | None:
    """Serialise the agent's assigned Tool rows into executable ToolSpec dicts.

    Plan 06.18 task_06_18_05: the runtime boot needs more than tool *names* to
    register a tool — it needs the ``implementation_type`` (which executor) and
    the type-specific config (URL template, code, runtime template +
    command). This is the read seam the orchestrator calls (in ``_route_ai``)
    to build ``request["tool_specs"]``; the worker forwards it into the agent
    spec and ``__main__.run_task`` rebuilds :class:`ToolSpec` objects from it.

    Returns ``None`` when the agent has **no** ``agent_tools`` rows — the same
    sentinel ``resolve_agent_tool_names`` uses: no rows means no per-agent
    wiring, the pre-06.18 backward-compatible behaviour (echo/noop only). A
    non-empty list carries one dict per live assigned tool.

    Each dict is ``{"name", "implementation_type", "config"}``:

      * ``builtin`` — config is empty; the runtime registers the canonical
        family executor (file/network/…). ``shell_exec`` is excluded: it is
        wired separately from the project's ``allowed_commands`` (Plan 06.16),
        not from a serialised spec.
      * ``docker_command`` (the ``run_*`` tools) — config carries
        ``runtime_template`` (the tool's ``implementation_ref``, e.g.
        ``python-pytest``) so the WORKER resolves it to a concrete image (it
        owns the runtime catalog; the sandboxed runtime must not import it) and
        a default ``command_template``.
      * ``http_endpoint`` / ``python_function`` — config carries the
        ``implementation_ref`` as the URL template / code respectively.
      * ``mcp_tool`` — passed through; the runtime's MCP wiring owns it.

    Tenant-safe by construction (same RLS reasoning as
    :func:`resolve_agent_tool_names`).
    """
    rows = (
        (
            await session.execute(
                select(Tool)
                .join(AgentTool, AgentTool.tool_id == Tool.id)
                .where(AgentTool.agent_id == agent_id, Tool.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    specs: list[dict[str, Any]] = []
    for tool in rows:
        # shell_exec is wired per project (allowed_commands), never from a spec.
        if tool.name == "shell_exec":
            continue
        specs.append(_tool_to_spec(tool))
    return specs


def _tool_to_spec(tool: Tool) -> dict[str, Any]:
    """Project one Tool row to the serialisable ToolSpec dict the runtime
    rebuilds at boot (task_06_18_05)."""
    impl = tool.implementation_type
    config: dict[str, Any] = {}
    if impl == "docker_command":
        # Carry the runtime template so the WORKER resolves the image. NULL
        # implementation_ref (run_lint/typecheck/build) → the worker falls back
        # to the project stack / python-pytest.
        if tool.implementation_ref:
            config["runtime_template"] = tool.implementation_ref
        config["command_template"] = list(_RUN_TOOL_COMMANDS.get(tool.name, ["echo", "{path}"]))
    elif impl == "http_endpoint":
        if tool.implementation_ref:
            config["url_template"] = tool.implementation_ref
    elif impl == "python_function":
        if tool.implementation_ref:
            config["code"] = tool.implementation_ref
    return {"name": tool.name, "implementation_type": impl, "config": config}


def combine_tool_allowlists(
    agent_tool_names: Iterable[str] | None,
    mode_allowed_tools: Iterable[str] | None,
) -> list[str] | None:
    """Intersect the per-agent assignment with the chat-mode allowlist.

    Each argument is an independent restriction layer:

      * ``agent_tool_names`` — the agent's ``agent_tools`` set, or ``None``
        when the agent has no assignments (no per-agent restriction).
      * ``mode_allowed_tools`` — ``ChatModeConfig.allowed_tools`` for the
        active mode, or ``None`` when the run carries no mode allowlist
        (e.g. the orchestrator's task-dispatch path).

    Semantics:

      * both ``None`` → ``None`` (unrestricted — backward compatible).
      * exactly one set → that set (the only active restriction).
      * both set → their intersection (a tool must satisfy *both* layers).

    The result is a sorted ``list[str]`` (deterministic for the JSON spec /
    tests) or ``None``. An empty list IS a valid result — it means "the two
    layers share no tool", which the runtime reads as "block every tool",
    exactly as the discussion mode's empty allowlist already does.
    """
    agent_set = None if agent_tool_names is None else to_canonical_set(agent_tool_names)
    mode_set = None if mode_allowed_tools is None else to_canonical_set(mode_allowed_tools)

    if agent_set is None and mode_set is None:
        return None
    if agent_set is None:
        return sorted(mode_set or frozenset())
    if mode_set is None:
        return sorted(agent_set)
    return sorted(agent_set & mode_set)


# ---------------------------------------------------------------------------
# Effective-tools computation (Plan 06.18 task_06_18_07)
# ---------------------------------------------------------------------------

#: Códigos estables de los avisos del cálculo efectivo. Son el identificador
#: idioma-neutral que el contrato bilingüe del Hub (06.17) usa para emparejar y
#: traducir cada aviso, en lugar de inspeccionar el texto castellano (que era la
#: rama EN muerta del follow-up bilingual-warnings).
WARN_TOOL_NOT_WIRED = "tool_not_runtime_wired"
WARN_SHELL_EXEC_NO_COMMANDS = "shell_exec_no_allowed_commands"
WARN_EMPTY_EFFECTIVE_IN_MODE = "empty_effective_in_mode"


@dataclass(frozen=True)
class ToolWarning:
    """Un aviso del cálculo efectivo en forma BILINGÜE estructurada.

    ``code`` es el identificador estable (uno de los ``WARN_*``); ``es``/``en``
    son el mismo aviso redactado en cada idioma soportado (ES + EN, los únicos
    de esta versión). El endpoint ``effective-tools`` (06.18) sigue exponiendo
    ``EffectiveTools.warnings`` como ``list[str]`` en castellano (su contrato no
    cambia); el Hub de Capacidad (06.17) consume ``warnings_i18n`` para renderizar
    el idioma activo sin dejar muerta la rama EN.
    """

    code: str
    es: str
    en: str


@dataclass(frozen=True)
class EffectiveTools:
    """The honest "what does the runtime really execute for this agent" view.

    Computed by :func:`compute_effective_tools` from the agent's assigned tool
    names, the active chat-mode allowlist and the project's ``allowed_commands``
    state. It is the single source of truth the ``GET /agents/{id}/effective-tools``
    endpoint (06.18) and the per-agent diagnostic project onto a JSON contract;
    06.17's Capability Hub consumes it without recomputing the intersection.

    Fields:

      * ``effective`` — the sorted canonical names the runtime actually wires
        and would run: the agent∩mode intersection restricted to runtime-wired
        names, PLUS ``shell_exec`` only when it is assigned AND the project's
        ``allowed_commands`` is non-empty.
      * ``unrestricted`` — ``True`` when the agent has no per-agent assignment
        (the backward-compatible 06.15 behaviour: no restriction). When ``True``
        the ``effective`` list is empty because there is no assigned set to
        intersect — the runtime keeps its own default surface.
      * ``shell_exec_effective`` — whether ``shell_exec`` is in ``effective``
        (assigned ∧ allowed_commands non-empty). Surfaced explicitly because the
        cross of ``allowed_commands`` is the operator's most common confusion.
      * ``warnings`` — human-readable, Spanish notices: an empty effective set
        under a mode, an assigned-but-not-runtime-wired tool, and a
        ``shell_exec`` assigned without ``allowed_commands``. Contrato del
        endpoint ``effective-tools`` (06.18): NO cambia.
      * ``warnings_i18n`` — los MISMOS avisos en forma bilingüe estructurada
        (``ToolWarning`` = ``code`` + ``es`` + ``en``), en el mismo orden que
        ``warnings``. El Hub de Capacidad (06.17) los consume para renderizar el
        idioma activo (follow-up bilingual-warnings).
    """

    effective: list[str] = field(default_factory=list)
    unrestricted: bool = False
    shell_exec_effective: bool = False
    warnings: list[str] = field(default_factory=list)
    warnings_i18n: list[ToolWarning] = field(default_factory=list)


def compute_effective_tools(
    assigned_names: Sequence[str] | None,
    mode_allowed_tools: Iterable[str] | None,
    *,
    mode_name: str | None,
    shell_exec_assigned: bool,
    allowed_commands_non_empty: bool,
    wired_canonical_names: set[str] | None = None,
) -> EffectiveTools:
    """Pure computation of the agent's effective tool set + warnings.

    Free of router/HTTP/DB concerns so the endpoint, the diagnostic and the
    tests all share one truth. It reuses :func:`combine_tool_allowlists` (the
    *single* point of agent∩mode intersection) and
    :func:`shared_domain.tool_names.is_runtime_wired`; it does NOT re-implement
    either.

    Parameters:

      * ``assigned_names`` — the agent's assigned ``Tool.name`` values, or
        ``None`` when the agent has no ``agent_tools`` rows (no per-agent
        restriction → ``unrestricted=True``).
      * ``mode_allowed_tools`` — the active chat-mode allowlist, or ``None``
        when no mode restricts (e.g. the task-dispatch path).
      * ``mode_name`` — the mode label for the "empty effective set in mode X"
        warning; ``None`` when no mode was requested.
      * ``shell_exec_assigned`` — whether ``shell_exec`` is among the agent's
        assignments (it is the only tool whose effective availability also
        depends on a project setting).
      * ``allowed_commands_non_empty`` — whether the agent's project has any
        ``allowed_commands`` (an empty allowlist registers a deny-all shell).
      * ``wired_canonical_names`` — the subset of the assigned tools' CANONICAL
        names that are actually runtime-wired, computed by the caller with the
        full ``Tool`` rows (so a typed custom tool — ``http_endpoint`` /
        ``python_function`` / ``docker_command`` / ``mcp_tool`` — counts as
        wired regardless of name, via ``schemas.catalog.tool_is_runtime_wired``).
        ``None`` falls back to the name-only :func:`is_runtime_wired`, which is
        correct for builtins but treats every typed custom tool as not-wired.

    ``shell_exec`` is handled specially: ``combine_tool_allowlists`` treats it
    like any other name (so it survives the agent∩mode intersection), but it is
    only truly executable when ``allowed_commands`` is non-empty. We therefore
    drop it from ``effective`` when the project has no commands and emit a
    warning instead — mirroring what the runtime really does.
    """
    warnings: list[str] = []
    warnings_i18n: list[ToolWarning] = []

    if assigned_names is None:
        # No per-agent restriction: backward-compatible 06.15 behaviour. There
        # is no assigned set to intersect, so the effective list is empty and we
        # do not pretend to enumerate the runtime's default surface here.
        return EffectiveTools(
            effective=[],
            unrestricted=True,
            shell_exec_effective=False,
            warnings=warnings,
            warnings_i18n=warnings_i18n,
        )

    def _wired(name: str) -> bool:
        if wired_canonical_names is None:
            return bool(is_runtime_wired(name))
        return name in wired_canonical_names

    # Single point of intersection (canonicalised inside).
    combined = combine_tool_allowlists(assigned_names, mode_allowed_tools)
    combined_set = set(combined or [])

    # An assigned tool that is not runtime-wired would die as a silent
    # `unknown tool` — warn per offending assigned name (canonicalised).
    assigned_canonical = sorted(to_canonical_set(assigned_names))
    for name in assigned_canonical:
        if name == _SHELL_EXEC:
            continue
        if not _wired(name):
            warnings.append(
                f"tool '{name}' asignada pero no ejecutable en el runtime "
                "(sin executor cableado)"
            )
            warnings_i18n.append(
                ToolWarning(
                    code=WARN_TOOL_NOT_WIRED,
                    es=(
                        f"tool '{name}' asignada pero no ejecutable en el runtime "
                        "(sin executor cableado)"
                    ),
                    en=(
                        f"tool '{name}' is assigned but not executable in the runtime "
                        "(no wired executor)"
                    ),
                )
            )

    # Effective = combined ∩ runtime-wired, minus shell_exec (handled below).
    effective_set = {name for name in combined_set if name != _SHELL_EXEC and _wired(name)}

    # shell_exec: effective only if assigned, surviving the mode intersection,
    # AND the project authorises at least one command.
    shell_in_combined = _SHELL_EXEC in combined_set
    shell_exec_effective = shell_exec_assigned and shell_in_combined and allowed_commands_non_empty
    if shell_exec_effective:
        effective_set.add(_SHELL_EXEC)
    elif shell_exec_assigned and shell_in_combined and not allowed_commands_non_empty:
        warnings.append(
            "shell_exec asignado pero allowed_commands del proyecto está vacío; "
            "no se ejecutará ningún comando"
        )
        warnings_i18n.append(
            ToolWarning(
                code=WARN_SHELL_EXEC_NO_COMMANDS,
                es=(
                    "shell_exec asignado pero allowed_commands del proyecto está vacío; "
                    "no se ejecutará ningún comando"
                ),
                en=(
                    "shell_exec is assigned but the project's allowed_commands is empty; "
                    "no command will run"
                ),
            )
        )

    # Empty effective set under an explicit mode is a load-bearing warning: the
    # agent will be unable to call any tool in that mode.
    if mode_name is not None and not effective_set:
        warnings.append(
            f"set efectivo vacío en el modo '{mode_name}': el agente no podrá "
            "llamar a ninguna tool en este modo"
        )
        warnings_i18n.append(
            ToolWarning(
                code=WARN_EMPTY_EFFECTIVE_IN_MODE,
                es=(
                    f"set efectivo vacío en el modo '{mode_name}': el agente no podrá "
                    "llamar a ninguna tool en este modo"
                ),
                en=(
                    f"empty effective set in mode '{mode_name}': the agent will be unable "
                    "to call any tool in this mode"
                ),
            )
        )

    return EffectiveTools(
        effective=sorted(effective_set),
        unrestricted=False,
        shell_exec_effective=shell_exec_effective,
        warnings=warnings,
        warnings_i18n=warnings_i18n,
    )


__all__ = [
    "WARN_EMPTY_EFFECTIVE_IN_MODE",
    "WARN_SHELL_EXEC_NO_COMMANDS",
    "WARN_TOOL_NOT_WIRED",
    "EffectiveTools",
    "ToolWarning",
    "combine_tool_allowlists",
    "compute_effective_tools",
    "resolve_agent_tool_names",
    "serialize_agent_tool_specs",
]
