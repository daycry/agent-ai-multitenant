"""Asignación de tools al agente (Plan 06.15) y el set EFECTIVO (Plan 06.18).

Dos endpoints declarativos sobre `/agents/{id}/tools` respaldados por la junction
`agent_tools` (el `PUT` reemplaza el conjunto entero en una transacción; lista
vacía = "sin restricción por agente"), más el contrato honesto
`GET /agents/{id}/effective-tools`: qué ejecuta REALMENTE el runtime para este
agente, sin que el consumidor tenga que recalcular la intersección.

Reglas de scope (decisión del Plan 06.15):
  * tools built-in (`is_builtin`)              -> asignables a cualquier agente.
  * tools custom (`is_builtin=false`)          -> solo del tenant del agente. RLS
      ya oculta las de otro tenant, así que un lookup vacío sale como 422 limpio
      (el cuerpo lleva un tool_id inválido, no una ruta mala).
  * agente `global_builtin`                    -> 403 (forkear primero).
  * tools MCP: sin gate por agente desde el ADR 0128 — las aporta el PROYECTO en
      runtime, y `effective-tools` las refleja para no mentir sobre el set real.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from shared_domain.tool_names import to_canonical_set
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.agent_tools_enforcement import (
    compute_effective_tools,
    resolve_project_mcp_tool_names,
)
from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.chat.modes import resolve_mode_config
from api_server.db.domain import Agent, AgentTool, Project, Tool
from api_server.routers._helpers import require_tenant_id
from api_server.routers.agents.common import _load_writable_agent_for_tools
from api_server.schemas.agents import AgentToolResponse, SetAgentToolsRequest
from api_server.schemas.catalog import tool_is_runtime_wired

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/{agent_id}/tools", response_model=list[AgentToolResponse])
async def list_agent_tools(
    agent_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentToolResponse]:
    """List the tools assigned to this agent via the `agent_tools` junction.

    Read shape mirrors the read-only `agent-tools-diagnostic` panel plus
    `is_builtin` (the básica/avanzada taxonomy) and the per-agent
    `config_override`. An agent with no rows returns ``[]`` (which at
    enforcement time means "no per-agent restriction").
    """
    # Surface 404 on a hidden/missing agent instead of an empty list — an
    # invisible agent would otherwise look like "no assignments" to the UI.
    agent_q = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    if agent_q.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    rows = await session.execute(
        select(AgentTool.config_override, Tool)
        .join(Tool, Tool.id == AgentTool.tool_id)
        .where(
            AgentTool.agent_id == agent_id,
            Tool.deleted_at.is_(None),
        )
        .order_by(Tool.name, Tool.id)
    )
    return [
        AgentToolResponse(
            tool_id=tool.id,
            name=tool.name,
            description=tool.description,
            category=tool.category,
            implementation_type=tool.implementation_type,
            security_level=tool.security_level,
            is_builtin=tool.is_builtin,
            config_override=config_override,
        )
        for config_override, tool in rows.all()
    ]


@router.put("/{agent_id}/tools", response_model=list[AgentToolResponse])
async def set_agent_tools(
    agent_id: UUID,
    payload: SetAgentToolsRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentToolResponse]:
    """Declaratively replace the agent's tool assignments (tenant_admin).

    The desired set is validated for scope, then the agent's existing
    `agent_tools` rows are deleted and the new set inserted in the same
    request transaction. Returns the resulting assignments.
    """
    require_tenant_id(principal)
    # Enforces the 404 (hidden/missing) and 403 (global_builtin) contract; the
    # returned agent is otherwise unused now that the MCP gate is gone (ADR 0128).
    await _load_writable_agent_for_tools(session, agent_id, principal)

    requested = {entry.tool_id: entry.config_override for entry in payload.tools}

    # Load every requested Tool in one query. RLS scopes the result to
    # platform built-ins + the caller's tenant, so a cross-tenant custom
    # tool simply won't appear here — caught by the "missing" check below.
    tools_by_id: dict[UUID, Tool] = {}
    if requested:
        tool_rows = await session.execute(
            select(Tool).where(
                Tool.id.in_(requested.keys()),
                Tool.deleted_at.is_(None),
            )
        )
        tools_by_id = {tool.id: tool for tool in tool_rows.scalars().all()}

    # Any id we couldn't load is either non-existent or a custom tool from
    # another tenant (hidden by RLS). Both are an invalid declarative set.
    missing = [tool_id for tool_id in requested if tool_id not in tools_by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "unknown or non-assignable tool_id(s): "
                + ", ".join(str(tool_id) for tool_id in missing)
            ),
        )

    # Reject a tool the agent-runtime cannot execute (ADR 0049, task_06_18_06):
    # assigning a builtin with no executor (apply_patch / search_code /
    # summarize_text, the retired git_*) would die as a silent `unknown tool`
    # at run time. We refuse it up front rather than at execution. Typed rows
    # (http_endpoint / python_function / docker_command / mcp_tool) are wired
    # from a serialised spec, so only non-executable builtins are rejected.
    not_wired = [
        tool
        for tool in tools_by_id.values()
        if not tool_is_runtime_wired(tool.name, tool.implementation_type)
    ]
    if not_wired:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "tool(s) not executable in the agent-runtime (no wired executor): "
                + ", ".join(sorted(tool.name for tool in not_wired))
            ),
        )

    # ADR 0128: las tools MCP se aportan a nivel de proyecto en runtime; sin gate por-agente.

    # Replace the set transactionally: delete the old rows, insert new.
    await session.execute(delete(AgentTool).where(AgentTool.agent_id == agent_id))
    for tool_id, config_override in requested.items():
        session.add(
            AgentTool(
                agent_id=agent_id,
                tool_id=tool_id,
                config_override=config_override,
            )
        )
    await session.flush()

    return [
        AgentToolResponse(
            tool_id=tool.id,
            name=tool.name,
            description=tool.description,
            category=tool.category,
            implementation_type=tool.implementation_type,
            security_level=tool.security_level,
            is_builtin=tool.is_builtin,
            config_override=requested[tool.id],
        )
        for tool in sorted(tools_by_id.values(), key=lambda t: (t.name, str(t.id)))
    ]


# ---------------------------------------------------------------------------
# Plan 06.18 task_06_18_07: GET /agents/{id}/effective-tools
# ---------------------------------------------------------------------------
#
# The honest "what does the runtime really execute for this agent" contract —
# the frontier 06.17's Capability Hub (HACER) consumes without recomputing.
# It answers the operator's "I assigned a tool and nothing happens" by being
# the single place that:
#   * lists the agent's assigned tools, each flagged executable_in_runtime;
#   * computes the agent∩mode intersection through the SINGLE point
#     (`combine_tool_allowlists`, reused inside `compute_effective_tools`);
#   * crosses shell_exec with the project's `allowed_commands`;
#   * surfaces explicit, readable warnings instead of silent surprises.
#
# Field names are snake_case and stable: this is a contract. Read-only,
# tenant-scoped like the rest of the router (404 on a hidden/missing agent).


class EffectiveToolEntry(BaseModel):
    """One assigned tool, projected with its real runtime executability."""

    model_config = ConfigDict(populate_by_name=True)

    tool_id: UUID
    name: str
    #: Canonical name(s) the runtime resolves this assignment to (alias layer,
    #: ADR 0048): ``semantic_search`` → ``rag_search``, ``http_request`` →
    #: both verbs. Usually a single name; a list keeps the contract stable.
    canonical_names: list[str] = Field(default_factory=list)
    category: str
    implementation_type: str
    security_level: str
    is_builtin: bool
    #: Whether the agent-runtime can actually execute this tool (ADR 0049).
    #: A ``False`` here is the "asignada pero no ejecutable" case.
    executable_in_runtime: bool


class EffectiveToolsResponse(BaseModel):
    """The effective-tools contract (06.18 → 06.17).

    ``assigned`` lists every assignment with its executability; ``effective``
    is the sorted canonical set the runtime really wires for the requested
    ``mode`` (agent∩mode ∩ runtime-wired, plus ``shell_exec`` only when its
    project authorises commands). ``unrestricted`` is ``True`` for an agent with
    no per-agent assignment (backward-compatible 06.15 behaviour: the runtime
    keeps its default surface, so ``effective`` is empty by design).
    """

    model_config = ConfigDict(populate_by_name=True)

    agent_id: UUID
    #: The chat mode the effective set was computed against, or ``None`` when no
    #: mode was requested (the task-dispatch path carries no mode allowlist).
    mode: str | None = None
    assigned: list[EffectiveToolEntry] = Field(default_factory=list)
    effective: list[str] = Field(default_factory=list)
    #: ``True`` when the agent has no assignments (no per-agent restriction).
    unrestricted: bool = False
    #: Whether ``shell_exec`` is effective (assigned ∧ allowed_commands non-empty).
    shell_exec_effective: bool = False
    #: Readable notices: empty set in a mode, non-executable assignment,
    #: shell_exec without allowed_commands.
    warnings: list[str] = Field(default_factory=list)


@router.get("/{agent_id}/effective-tools", response_model=EffectiveToolsResponse)
async def get_agent_effective_tools(
    agent_id: UUID,
    mode: str | None = Query(
        default=None,
        description=(
            "Chat mode whose allowlist the effective set is intersected with "
            "(planning|discussion|execution|<custom>). Omit for no mode "
            "restriction (the task-dispatch path)."
        ),
    ),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> EffectiveToolsResponse:
    """Return the agent's honest effective tool set + per-tool executability.

    Tenant-scoped: RLS hides cross-tenant agents, so a hidden/missing agent
    surfaces as 404 (an empty body would otherwise look like "no tools").
    """
    # 404 on a hidden/missing agent (RLS handles cross-tenant).
    agent_q = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    agent = agent_q.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    # Resolve the mode allowlist (None when no mode requested). An unknown mode
    # name is a 422 — the operator asked for a mode that does not exist.
    mode_allowed_tools: tuple[str, ...] | None = None
    if mode is not None:
        try:
            mode_config = resolve_mode_config(mode)
        except ValueError:
            # Built-in lookup miss (custom modes are resolved elsewhere with a
            # tenant registry). Treat an unknown built-in name as a bad request.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"unknown chat mode: {mode!r}",
            ) from None
        mode_allowed_tools = mode_config.allowed_tools

    # Load the assigned Tool rows (live only) — one query, projected to the
    # entry shape. `resolve_agent_tool_names` is the read seam for the *names*,
    # but we need the full rows for the per-tool entry + shell_exec detection.
    rows = await session.execute(
        select(Tool)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent_id, Tool.deleted_at.is_(None))
        .order_by(Tool.name, Tool.id)
    )
    assigned_tools = list(rows.scalars().all())

    # `None` (no rows) is the load-bearing "no per-agent restriction" sentinel,
    # exactly as `resolve_agent_tool_names` returns it.
    assigned_names: list[str] | None = [t.name for t in assigned_tools] if assigned_tools else None
    shell_exec_assigned = any(t.name == "shell_exec" for t in assigned_tools)

    # The canonical names that are *actually* runtime-wired, computed from the
    # full Tool rows so a typed custom tool (http_endpoint / python_function /
    # docker_command / mcp_tool) counts as wired regardless of name. This is the
    # honest "executable in runtime" set the pure computation intersects with.
    wired_canonical_names: set[str] = set()
    for tool in assigned_tools:
        if tool_is_runtime_wired(tool.name, tool.implementation_type):
            wired_canonical_names |= to_canonical_set([tool.name])

    # Load the agent's project once: it feeds both the shell_exec cross
    # (allowed_commands) and, post-ADR 0128, the MCP tools the project
    # contributes to the run allowlist (so the effective set is honest about
    # them even though they are not per-agent grants).
    allowed_commands_non_empty = False
    project_mcp_tool_names: frozenset[str] = frozenset()
    if agent.project_id is not None:
        project = (
            await session.execute(
                select(Project).where(Project.id == agent.project_id, Project.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if project is not None:
            allowed_commands_non_empty = bool(project.allowed_commands)
            project_mcp_tool_names = await resolve_project_mcp_tool_names(
                session, project, role=agent.role
            )

    result = compute_effective_tools(
        assigned_names,
        mode_allowed_tools,
        mode_name=mode,
        shell_exec_assigned=shell_exec_assigned,
        allowed_commands_non_empty=allowed_commands_non_empty,
        wired_canonical_names=wired_canonical_names,
        project_mcp_tool_names=project_mcp_tool_names,
    )

    return EffectiveToolsResponse(
        agent_id=agent_id,
        mode=mode,
        assigned=[
            EffectiveToolEntry(
                tool_id=tool.id,
                name=tool.name,
                canonical_names=sorted(to_canonical_set([tool.name])),
                category=tool.category,
                implementation_type=tool.implementation_type,
                security_level=tool.security_level,
                is_builtin=tool.is_builtin,
                executable_in_runtime=tool_is_runtime_wired(tool.name, tool.implementation_type),
            )
            for tool in assigned_tools
        ],
        effective=result.effective,
        unrestricted=result.unrestricted,
        shell_exec_effective=result.shell_exec_effective,
        warnings=result.warnings,
    )
