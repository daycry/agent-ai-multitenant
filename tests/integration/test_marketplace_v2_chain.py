"""La cadena entera: de publicar a que el agente TENGA la capacidad (`task_mkt2_05`).

Éste es el test que da sentido al plan. Todo lo demás existe para que pase.

    publicar → instalar (consentir) → desplegar en un proyecto
      → **el agente del rol destino tiene la tool / el proyecto tiene el MCP**
      → retirar → todo limpio, y la fila `retired` conserva la auditoría

Va **por HTTP**, no llamando al servicio: el modo de fallo nº1 de esta base es
«mecanismo entregado, cero llamantes»
(`docs/03-guides/verificar-antes-de-implementar.md` §5), y un test que llama a
`deploy_installation` a mano no descubriría que el endpoint no está montado. El
único tramo sembrado a mano es el catálogo (publicar un listing sigue siendo
`POST /marketplace/private/listings` o el seeder oficial, y la máquina de
estados de revisión es la fase 3).

## Sobre el reparto por tipo, que el plan describe con una ambigüedad

El texto de `task_mkt2_05` pide, para el viaje `mcp_server`, que «el agente
backend_dev tenga las tools en `agent_tools`». Eso choca de frente con el aviso
del propio plan y con el §4 del ADR 0142 («el `role_map` de un `mcp_server` se
materializa escribiendo `projects.mcp_tool_roles` — NO inventes una política
paralela») y con la fase 3 del ADR 0128, que RETIRÓ el grant por-agente de las
tools MCP. Gana el diseño, como el propio plan ordena: para `mcp_server` se
comprueban `mcp_servers` + `mcp_tool_roles` y se comprueba **explícitamente que
NO hay filas `agent_tools`**; el viaje de `agent_tools` es el del listing `tool`,
y el de `agent_skills` el del `skill`. Los tres se recorren aquí.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = [pytest.mark.integration]


@pytest.fixture()
def configured_app(
    alembic_config: object,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# El catálogo publicado (el tramo que la fase 3 convertirá en un flujo revisado)
# ---------------------------------------------------------------------------
_MCP_LISTING = {
    "kind": "mcp_server",
    "name": "jira-mcp",
    "manifest": {
        "implementation_type": "mcp_tool",
        "implementation_ref": "https://jira.example.test/mcp",
        # D5: el manifest SUGIERE el rol destino.
        "targets": ["backend_dev"],
        "mcp_server": {"name": "jira", "transport": "streamable_http"},
        # D8: y declara qué se le pregunta a quien despliega, por proyecto.
        "config_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "widget": "text"},
                "base_url": {"type": "string", "widget": "text"},
            },
            "required": ["url"],
        },
    },
}

_TOOL_LISTING = {
    "kind": "tool",
    "name": "status-checker",
    "manifest": {
        "implementation_type": "http_endpoint",
        "implementation_ref": "https://status.example.test/api",
        "targets": ["qa"],
        "config_schema": {
            "type": "object",
            "properties": {"base_url": {"type": "string"}},
            "required": ["base_url"],
        },
    },
}

_SKILL_LISTING = {
    "kind": "skill",
    "name": "doc-contracts",
    "manifest": {
        "prompt_fragment": "Documenta SIEMPRE los contratos públicos.",
        "category": "docs",
        "targets": ["technical_writer"],
    },
}


async def _seed(dsn: str) -> dict[str, UUID]:
    ids: dict[str, UUID] = {
        k: uuid4()
        for k in (
            "tenant",
            "admin",
            "source",
            "listing_mcp",
            "listing_tool",
            "listing_skill",
            "team",
            "agent_backend",
            "agent_qa",
            "agent_writer",
            "project",
        )
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_deployments, marketplace_audit_entries,"
            " marketplace_installations, marketplace_listing_versions,"
            " marketplace_listings, marketplace_sources, agent_tools, agent_skills,"
            " team_members, tools, skills, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'Cadena','cadena')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1,'cadena@test.test','argon2-placeholder')",
            ids["admin"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1,$2,$3,'tenant_admin')",
            uuid4(),
            ids["tenant"],
            ids["admin"],
        )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type, is_trusted)"
            " VALUES ($1,'oficial-cadena','official',true)",
            ids["source"],
        )
        for key, spec in (
            ("listing_mcp", _MCP_LISTING),
            ("listing_tool", _TOOL_LISTING),
            ("listing_skill", _SKILL_LISTING),
        ):
            await conn.execute(
                "INSERT INTO marketplace_listings"
                " (id, source_id, tenant_id, kind, name, version, trust_level, manifest,"
                "  requested_permissions)"
                " VALUES ($1,$2,NULL,$3,$4,'1.0.0','verified',$5::jsonb,'[]'::jsonb)",
                ids[key],
                ids["source"],
                spec["kind"],
                spec["name"],
                json.dumps(spec["manifest"]),
            )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,'Equipo cadena')",
            ids["team"],
            ids["tenant"],
        )
        for agent, name, role in (
            (ids["agent_backend"], "Bea Backend", "backend_dev"),
            (ids["agent_qa"], "Quim QA", "qa"),
            (ids["agent_writer"], "Wanda Writer", "technical_writer"),
        ):
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, role, scope, system_prompt)"
                " VALUES ($1,$2,$3,$4,'global_tenant_template',$5)",
                agent,
                ids["tenant"],
                name,
                role,
                f"Eres {name}.",
            )
            await conn.execute(
                "INSERT INTO team_members (team_id, agent_id) VALUES ($1,$2)", ids["team"], agent
            )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, team_id, name, slug)"
            " VALUES ($1,$2,$3,'Proyecto cadena','proj-cadena')",
            ids["project"],
            ids["tenant"],
            ids["team"],
        )
        # Las tools `<server>.<tool>` que el import MCP (ADR 0052) crea. La
        # política de roles se escribe sobre ESTAS claves, que son las que
        # `agent_tools_enforcement` consulta en el runtime.
        for tool_name in ("jira.create_issue", "jira.search"):
            await conn.execute(
                "INSERT INTO tools (id, tenant_id, name, category, implementation_type,"
                " implementation_ref, security_level)"
                " VALUES ($1,$2,$3,'mcp','mcp_tool',$4,'sandboxed')",
                uuid4(),
                ids["tenant"],
                tool_name,
                f"mcp://{tool_name}",
            )
    finally:
        await conn.close()
    return ids


async def _project_state(dsn: str, project_id: UUID) -> tuple[list[Any], dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT mcp_servers, mcp_tool_roles FROM projects WHERE id = $1", project_id
        )
        assert row is not None
        return json.loads(row["mcp_servers"]), json.loads(row["mcp_tool_roles"])
    finally:
        await conn.close()


async def _agent_capabilities(dsn: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """`([(rol, tool)], [(rol, skill)])` — lo que los agentes TIENEN de verdad."""
    conn = await asyncpg.connect(dsn)
    try:
        tools = await conn.fetch(
            "SELECT a.role, t.name FROM agent_tools at"
            " JOIN agents a ON a.id = at.agent_id JOIN tools t ON t.id = at.tool_id"
            " ORDER BY a.role, t.name"
        )
        skills = await conn.fetch(
            "SELECT a.role, s.name FROM agent_skills asg"
            " JOIN agents a ON a.id = asg.agent_id JOIN skills s ON s.id = asg.skill_id"
            " ORDER BY a.role, s.name"
        )
        return (
            [(r["role"], r["name"]) for r in tools],
            [(r["role"], r["name"]) for r in skills],
        )
    finally:
        await conn.close()


async def _runtime_view(
    user_id: UUID, tenant_id: UUID, project_id: UUID
) -> tuple[frozenset[str], frozenset[str]]:
    """Lo que el RUNTIME resuelve para `backend_dev` y para `qa`.

    Se llama al mismo `resolve_project_mcp_tool_names` que usa el dispatch: es
    la diferencia entre «la política está escrita en la BD» y «el agente puede
    llamar a la tool», que son cosas distintas y solo la segunda es la promesa
    del plan.
    """
    from api_server.agent_tools_enforcement import resolve_project_mcp_tool_names
    from api_server.auth.deps import AuthPrincipal, open_tenant_session
    from api_server.db.domain import Project
    from sqlalchemy import select

    principal = AuthPrincipal(user_id=user_id, session_id=uuid7(), tenant_id=tenant_id)
    async with open_tenant_session(principal) as session:
        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        return (
            await resolve_project_mcp_tool_names(session, project, role="backend_dev"),
            await resolve_project_mcp_tool_names(session, project, role="qa"),
        )


# ===========================================================================
# EL test
# ===========================================================================
@pytest.mark.asyncio
async def test_mcp_server_chain_from_install_to_the_project_having_it(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """`mcp_server`: instalar → desplegar → el PROYECTO lo tiene → retirar → limpio."""
    ids = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}

    async with _client(configured_app) as client:
        # 1. INSTALAR (por el endpoint real: consentimiento + materialización ADR 0100).
        install = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(ids["listing_mcp"])},
            headers=headers,
        )
        assert install.status_code == 201, install.text
        installation_id = install.json()["id"]
        assert install.json()["status"] == "enabled", (
            "un listing verified se instala habilitado; si no, el despliegue no podría ni empezar"
        )

        # 2. El proyecto lo ve como DISPONIBLE, con su formulario y su rol sugerido.
        available = await client.get(
            f"/projects/{ids['project']}/marketplace/available", headers=headers
        )
        assert available.status_code == 200, available.text
        offered = [c for c in available.json() if c["installation_id"] == installation_id]
        assert len(offered) == 1, available.json()
        assert offered[0]["targets"] == ["backend_dev"]
        assert offered[0]["config_schema"]["required"] == ["url"]

        # 3. DESPLEGAR con la config de ESTE proyecto.
        deployed = await client.post(
            f"/marketplace/installations/{installation_id}/deployments",
            json={
                "project_id": str(ids["project"]),
                "config": {"url": "https://jira-de-este-proyecto.test/mcp"},
            },
            headers=headers,
        )
        assert deployed.status_code == 201, deployed.text
        deployment_id = deployed.json()["deployment"]["id"]

    # ---- EL ASSERT QUE JUSTIFICA EL PLAN --------------------------------
    servers, policy = await _project_state(migrations_pg_dsn, ids["project"])
    assert [s["name"] for s in servers] == ["jira"], (
        "instalar seguía siendo comprar sin recibir: el proyecto NO tiene el MCP"
    )
    assert servers[0]["url"] == "https://jira-de-este-proyecto.test/mcp"
    assert policy == {
        "jira.create_issue": ["backend_dev"],
        "jira.search": ["backend_dev"],
    }, f"el role_map no llegó a `mcp_tool_roles` (ADR 0128): {policy}"

    # Y el runtime lo VE: el mismo resolvedor que usa el dispatch.
    for_backend, for_qa = await _runtime_view(ids["admin"], ids["tenant"], ids["project"])
    assert for_backend == frozenset({"jira.create_issue", "jira.search"}), (
        "el agente backend_dev NO puede llamar a las tools del MCP desplegado:"
        " el último tramo sigue sin cablear"
    )
    assert for_qa == frozenset(), (
        "el `qa` puede usar las tools de un MCP desplegado solo para backend_dev:"
        " la política de roles no está surtiendo efecto"
    )

    # ADR 0142 §4: un mcp_server NO reparte grants por agente.
    tools, skills = await _agent_capabilities(migrations_pg_dsn)
    assert tools == [] and skills == [], (
        "un despliegue mcp_server escribió agent_tools/agent_skills: eso es la"
        f" política paralela que el ADR 0142 prohíbe ({tools}, {skills})"
    )

    # ---- 4. RETIRAR: todo limpio, y la auditoría se conserva -------------
    async with _client(configured_app) as client:
        retired = await client.post(
            f"/marketplace/deployments/{deployment_id}/retire", headers=headers
        )
        assert retired.status_code == 200, retired.text

    servers, policy = await _project_state(migrations_pg_dsn, ids["project"])
    assert servers == [], "la retirada dejó el servidor MCP declarado"
    assert policy == {}, "la retirada dejó la política de roles colgando"

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, created_refs, retired_at FROM marketplace_deployments WHERE id = $1",
            UUID(deployment_id),
        )
        actions = [
            r["action"]
            for r in await conn.fetch(
                "SELECT action FROM marketplace_audit_entries ORDER BY created_at"
            )
        ]
    finally:
        await conn.close()
    assert row is not None
    assert row["status"] == "retired" and row["retired_at"] is not None
    assert json.loads(row["created_refs"])["mcp_servers"] == ["jira"], (
        "la fila retirada perdió el rastro de qué había creado"
    )
    assert actions[0] == "install"
    assert actions[-2:] == ["deploy", "retire"], actions


@pytest.mark.asyncio
async def test_tool_chain_gives_the_targeted_agent_the_tool(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """`tool`: el viaje de `agent_tools`, que es donde vive «el agente LA TIENE»."""
    ids = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}

    async with _client(configured_app) as client:
        install = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(ids["listing_tool"])},
            headers=headers,
        )
        assert install.status_code == 201, install.text
        installation_id = install.json()["id"]

        # Antes de desplegar: la tool existe en el catálogo del tenant… y NADIE
        # la tiene. Es literalmente el problema que el ADR 0142 mide en su §1.
        tools_before, _ = await _agent_capabilities(migrations_pg_dsn)
        assert tools_before == [], tools_before

        deployed = await client.post(
            f"/marketplace/installations/{installation_id}/deployments",
            json={
                "project_id": str(ids["project"]),
                "config": {"base_url": "https://app-de-este-proyecto.test"},
            },
            headers=headers,
        )
        assert deployed.status_code == 201, deployed.text
        deployment_id = deployed.json()["deployment"]["id"]

    tools_after, _ = await _agent_capabilities(migrations_pg_dsn)
    # `status_checker`, con guion BAJO: la materialización del ADR 0100 pasa el
    # nombre del listing por `normalize_tool_name`. Se afirma el nombre REAL
    # porque ir por HTTP es justo lo que descubre estas cosas — un test que
    # llamara al servicio a mano habría fijado el nombre del listing.
    assert tools_after == [("qa", "status_checker")], (
        f"el agente del rol destino NO tiene la tool: {tools_after}"
    )

    async with _client(configured_app) as client:
        await client.post(f"/marketplace/deployments/{deployment_id}/retire", headers=headers)
    tools_final, _ = await _agent_capabilities(migrations_pg_dsn)
    assert tools_final == [], f"la retirada dejó la asignación colgando: {tools_final}"


@pytest.mark.asyncio
async def test_skill_chain_gives_the_targeted_agent_the_skill(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """El mismo viaje con un `skill` (→ `agent_skills`)."""
    ids = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}

    async with _client(configured_app) as client:
        install = await client.post(
            "/marketplace/installations",
            json={"listing_id": str(ids["listing_skill"])},
            headers=headers,
        )
        assert install.status_code == 201, install.text
        installation_id = install.json()["id"]

        deployed = await client.post(
            f"/marketplace/installations/{installation_id}/deployments",
            json={"project_id": str(ids["project"])},
            headers=headers,
        )
        assert deployed.status_code == 201, deployed.text
        deployment_id = deployed.json()["deployment"]["id"]

    _, skills_after = await _agent_capabilities(migrations_pg_dsn)
    assert skills_after == [("technical_writer", "doc-contracts")], skills_after

    async with _client(configured_app) as client:
        retired = await client.post(
            f"/marketplace/deployments/{deployment_id}/retire", headers=headers
        )
        assert retired.json()["removed_refs"] == 1

    _, skills_final = await _agent_capabilities(migrations_pg_dsn)
    assert skills_final == []
