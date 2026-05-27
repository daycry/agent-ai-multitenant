"""Siembra el escenario compartido por los tres tests humanos del Plan 05.

Crea, en el tenant `DEMO_TENANT` (`tenant-a` por defecto):

  * Un **proyecto** dedicado a Plan 05 con su `mcp_servers` JSONB ya
    rellenado con una entrada apuntando al toy MCP server local
    (que los tres demos pueden tocar sin red ni PATs reales).
  * Dos **agentes** project-scoped — uno con un Tool row de tipo
    `http_endpoint` y otro con un Tool row de tipo `docker_command`.
    Sin esto, las pantallas
    `/admin/projects/<id>/mcp-servers` y
    `/admin/projects/<id>/agent-tools-diagnostic` no tienen nada que
    mostrar; con esto, los tres demos hacen que algo concreto se vea
    en la UI.

Persiste los ids en `scripts/.demo_state_05.json` (separado del estado
del Plan 02 — los demos del Plan 05 leen este fichero y nunca pisan
el otro).

Uso:

    .venv/Scripts/python scripts/setup_demo_05.py

Idempotente al nivel de "siempre crea uno nuevo". Si lo lanzas otra
vez, sobrescribe el estado y aparece un proyecto adicional. Para
limpiar, bórralo desde el admin-panel.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

# Force UTF-8 stdout — Windows default cp1252 chokes on accents.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

DB_URL = os.environ.get(
    "DEMO_DATABASE_URL",
    "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
    "@localhost:15432/agentic_platform",
)
TENANT_REF = os.environ.get("DEMO_TENANT", "tenant-a")
STATE_FILE = Path(__file__).parent / ".demo_state_05.json"


_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOY_SERVER = _REPO_ROOT / "tests" / "integration" / "_toy_mcp_server.py"


def _banner(title: str) -> None:
    line = "=" * 72
    print(line, flush=True)
    print(f"  {title}", flush=True)
    print(line, flush=True)


async def _resolve_tenant(session: Any) -> Any:
    """Look up the tenant by slug or UUID — same logic as
    `_demo_common.resolve_tenant`, inlined to avoid the heavyweight
    setup that script does (asyncio loop juggling)."""
    from api_server.db.models import Organization
    from sqlalchemy import select

    try:
        tenant_uuid = UUID(TENANT_REF)
    except (ValueError, TypeError):
        tenant_uuid = None

    stmt = select(Organization)
    if tenant_uuid is not None:
        stmt = stmt.where(Organization.id == tenant_uuid)
    else:
        stmt = stmt.where(Organization.slug == TENANT_REF)
    result = await session.execute(stmt)
    org = result.scalar_one_or_none()
    if org is None:
        raise RuntimeError(
            f"tenant {TENANT_REF!r} not found — make sure docker compose is up "
            f"and the dev seeds ran (alembic upgrade head)."
        )
    return org


async def main() -> int:
    from api_server.db.domain import Agent, AgentTool, Project, Tool
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    _banner("setup demo - escenario compartido Plan 05")

    if not _TOY_SERVER.is_file():
        print(f"FAIL — toy MCP server not found at {_TOY_SERVER}")
        return 1

    engine = create_async_engine(DB_URL)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            tenant = await _resolve_tenant(session)
            project_id = uuid4()

            # The mcp_servers entry mirrors the shape task_05_04 validates.
            # Points at the toy server so the operator can hit "Probar"
            # without a real PAT or external network.
            mcp_servers: list[dict[str, Any]] = [
                {
                    "name": "toy-mcp",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(_TOY_SERVER), "--transport", "stdio"],
                    "env": {},
                    "url": None,
                    "headers": {},
                    "auth_ref": None,
                    "timeout_s": 15.0,
                }
            ]

            project = Project(
                id=project_id,
                tenant_id=tenant.id,
                name=f"Plan 05 demo - {project_id.hex[:8]}",
                description=(
                    "Proyecto seedeado por scripts/setup_demo_05.py. "
                    "Tiene un MCP server toy + dos agentes con tools "
                    "http_endpoint y docker_command para que las "
                    "pantallas /mcp-servers y /agent-tools-diagnostic "
                    "tengan algo que mostrar."
                ),
                status="active",
                team_id=None,
                mcp_servers=mcp_servers,
                rag_knowledge_bases=[],
                worker_config={},
            )
            session.add(project)
            # Flush so the agents (whose project_id FK points here) can
            # reference the row — SQLAlchemy can't infer the insert
            # order because Agent.project_id is a raw UUID column,
            # not a relationship.
            await session.flush()

            # Agent A: receives the http_endpoint Tool.
            agent_a_id = uuid4()
            agent_a = Agent(
                id=agent_a_id,
                tenant_id=tenant.id,
                name="HTTP Lookup Bot",
                description="Agente con una tool http_endpoint para demos.",
                agent_type="ai",
                role="researcher",
                system_prompt="Eres un agente que consulta APIs HTTP.",
                model_config={"provider": "deterministic", "model": "echo"},
                memory_scope="private",
                review_capability=False,
                max_concurrent_tasks=1,
                is_template=False,
                scope="project_local",
                project_id=project_id,
            )
            session.add(agent_a)

            # Agent B: receives the docker_command Tool.
            agent_b_id = uuid4()
            agent_b = Agent(
                id=agent_b_id,
                tenant_id=tenant.id,
                name="Sandbox Runner",
                description="Agente con una tool docker_command para demos.",
                agent_type="ai",
                role="executor",
                system_prompt="Eres un agente que ejecuta scripts en sandbox.",
                model_config={"provider": "deterministic", "model": "echo"},
                memory_scope="private",
                review_capability=False,
                max_concurrent_tasks=1,
                is_template=False,
                scope="project_local",
                project_id=project_id,
            )
            session.add(agent_b)

            # Tool 1: http_endpoint pointing at a placeholder allowlisted host.
            tool_http_id = uuid4()
            tool_http = Tool(
                id=tool_http_id,
                tenant_id=tenant.id,
                name="example-weather",
                description=(
                    "Tool de demo: consulta una URL http_endpoint. "
                    "Allowlist y URL viven en config en el agent-runtime."
                ),
                category="data",
                input_schema={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
                output_schema={},
                implementation_type="http_endpoint",
                implementation_ref="https://api.weather.example/v1?q={city}",
                security_level="safe",
                timeout_seconds=10,
                is_builtin=False,
            )
            session.add(tool_http)
            await session.flush()  # Tool must exist before AgentTool FK
            session.add(AgentTool(agent_id=agent_a_id, tool_id=tool_http_id))

            # Tool 2: docker_command running a probe in python:3.12-alpine.
            tool_docker_id = uuid4()
            tool_docker = Tool(
                id=tool_docker_id,
                tenant_id=tenant.id,
                name="alpine-probe",
                description=(
                    "Tool de demo: lanza python:3.12-alpine y reporta su "
                    "entorno (uid, fs writability, network)."
                ),
                category="code",
                input_schema={"type": "object", "properties": {}},
                output_schema={},
                implementation_type="docker_command",
                implementation_ref=(
                    "python:3.12-alpine#python -c " "'print(__import__(\"os\").getuid())'"
                ),
                security_level="privileged",
                timeout_seconds=60,
                is_builtin=False,
            )
            session.add(tool_docker)
            await session.flush()
            session.add(AgentTool(agent_id=agent_b_id, tool_id=tool_docker_id))

            print(f"\n  Tenant     : {tenant.slug} ({tenant.id})")
            print(f"  Project    : {project.name}")
            print(f"  Project ID : {project_id}")
            print("  Agents     :")
            print(f"    - {agent_a.name}  ({agent_a_id})  + tool http_endpoint")
            print(f"    - {agent_b.name}  ({agent_b_id})  + tool docker_command")
            print(f"  MCP server : toy-mcp (stdio -> {_TOY_SERVER.name})")
    finally:
        await engine.dispose()

    state = {
        "tenant_id": str(tenant.id),
        "tenant_slug": tenant.slug,
        "project_id": str(project_id),
        "agent_http_id": str(agent_a_id),
        "agent_docker_id": str(agent_b_id),
        "tool_http_id": str(tool_http_id),
        "tool_docker_id": str(tool_docker_id),
    }
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"\n  state -> {STATE_FILE.relative_to(_REPO_ROOT)}")

    print("\n  URLs para inspeccionar en el admin-panel:")
    print(f"    http://localhost:3000/admin/projects/{project_id}/mcp-servers")
    print(f"    http://localhost:3000/admin/projects/{project_id}/agent-tools-diagnostic")
    print("\n  Setup OK. Lanza ahora los tres demos:")
    print("    .venv/Scripts/python scripts/demo_human_05_01.py")
    print("    .venv/Scripts/python scripts/demo_human_05_02.py")
    print("    .venv/Scripts/python scripts/demo_human_05_03.py")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
