"""Diagnostic snapshot: for a given project, what tools does each
agent see? (Plan 05 task_05_15).

`GET /projects/{project_id}/agent-tools-diagnostic` returns one entry
per project-scoped agent with:

  * The agent's identity (id, name, role, scope).
  * The Tool rows wired via the `agent_tools` M:N junction. Each Tool
    surfaces its `implementation_type` so the UI can show a badge
    (builtin / http_endpoint / python_function / docker_command /
    mcp_tool) and the security_level inherited from the row.
  * The MCP servers declared on the project — these are NOT
    per-agent, but they're load-bearing context for the diagnostic
    ("agent X has these Tools AND can reach these MCP servers").

The diagnostic is read-only. Editing happens in the Agent + Tool
CRUD endpoints (Plan 01/02). The point of the panel is to answer
the operator's "why is the agent calling X / why isn't it calling Y"
question without spelunking through tables.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_member
from api_server.db.domain import Agent, AgentTool, Project, Tool

router = APIRouter(prefix="/projects/{project_id}", tags=["tools-diagnostic"])


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------
class ToolDiagnostic(BaseModel):
    """One Tool row, projected to what the panel needs."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    name: str
    description: str | None = None
    category: str
    implementation_type: str
    security_level: str
    timeout_seconds: int


class McpServerDiagnostic(BaseModel):
    """One MCP server entry from `Project.mcp_servers`. We project
    only the fields the panel renders — the full config (auth_ref
    etc.) stays out of this read-only diagnostic."""

    name: str
    transport: str
    has_auth: bool


class AgentDiagnostic(BaseModel):
    """One agent's view of the world."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    name: str
    role: str
    scope: str
    tools: list[ToolDiagnostic] = Field(default_factory=list)


class AgentToolsDiagnosticResponse(BaseModel):
    """Top-level shape `/agent-tools-diagnostic` returns."""

    project_id: UUID
    agents: list[AgentDiagnostic] = Field(default_factory=list)
    mcp_servers: list[McpServerDiagnostic] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GET /projects/{id}/agent-tools-diagnostic
# ---------------------------------------------------------------------------
@router.get("/agent-tools-diagnostic", response_model=AgentToolsDiagnosticResponse)
async def get_agent_tools_diagnostic(
    project_id: UUID,
    _principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentToolsDiagnosticResponse:
    """Build the per-agent tools view for a project.

    Includes:
      - agents with ``scope='project_local'`` AND ``project_id == this``
      - their wired Tool rows (via the agent_tools junction)
      - the MCP servers declared on the project
    """
    # 404 if the project isn't visible — same shape as the test-connection
    # endpoint, gives consistent error messages.
    project_result = await session.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = project_result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    # Load every project-scoped agent.
    agents_q = await session.execute(
        select(Agent)
        .where(
            Agent.project_id == project_id,
            Agent.deleted_at.is_(None),
        )
        .order_by(Agent.name)
    )
    agents = list(agents_q.scalars().all())

    # Bulk-load the Tool rows wired to those agents. One join, one
    # query, then bucket by agent_id in Python — avoids N+1.
    agent_ids = [agent.id for agent in agents]
    tools_by_agent: dict[UUID, list[Tool]] = {agent_id: [] for agent_id in agent_ids}
    if agent_ids:
        rows = await session.execute(
            select(AgentTool.agent_id, Tool)
            .join(Tool, Tool.id == AgentTool.tool_id)
            .where(
                AgentTool.agent_id.in_(agent_ids),
                Tool.deleted_at.is_(None),
            )
            .order_by(Tool.name)
        )
        for agent_id, tool in rows.all():
            tools_by_agent[agent_id].append(tool)

    # Project MCP servers — JSONB, already in a list-of-dicts shape
    # (validated by task_05_04 on every write). Project to the
    # diagnostic-friendly shape.
    mcp_diagnostics: list[McpServerDiagnostic] = []
    raw_mcp_servers: list[dict[str, Any]] = project.mcp_servers or []
    for entry in raw_mcp_servers:
        mcp_diagnostics.append(
            McpServerDiagnostic(
                name=str(entry.get("name", "(unnamed)")),
                transport=str(entry.get("transport", "?")),
                has_auth=bool(entry.get("auth_ref")),
            )
        )

    return AgentToolsDiagnosticResponse(
        project_id=project_id,
        agents=[
            AgentDiagnostic(
                id=agent.id,
                name=agent.name,
                role=agent.role,
                scope=agent.scope,
                tools=[
                    ToolDiagnostic(
                        id=tool.id,
                        name=tool.name,
                        description=tool.description,
                        category=tool.category,
                        implementation_type=tool.implementation_type,
                        security_level=tool.security_level,
                        timeout_seconds=tool.timeout_seconds,
                    )
                    for tool in tools_by_agent.get(agent.id, [])
                ],
            )
            for agent in agents
        ],
        mcp_servers=mcp_diagnostics,
    )


__all__ = [
    "AgentDiagnostic",
    "AgentToolsDiagnosticResponse",
    "McpServerDiagnostic",
    "ToolDiagnostic",
    "router",
]
