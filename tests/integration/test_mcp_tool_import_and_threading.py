"""MCP discovery → catálogo + threading de ``mcp_servers`` al runtime
(Plan 06.18 task_06_18_12, ADR 0052).

Cierra el bucle abierto de MCP en dos mitades, ambas pinchadas aquí:

  1. **Importación discovery → catálogo.** Tras un ``test-connection`` exitoso,
     el operador importa N tools (multiselección configurable, NO hardcode) de un
     server declarado en el proyecto. El endpoint hace UPSERT de filas ``Tool``
     con ``implementation_type='mcp_tool'``, ``name`` namespaced
     ``<server>.<tool>``, ``implementation_ref='<server>.<tool>'``,
     ``category='mcp'`` y ``security_level='sandboxed'`` por defecto (editable).
     Re-importar es idempotente (ON CONFLICT actualiza, respeta el
     ``UNIQUE(tenant_id,name)`` de task_06_18_04) y NO duplica filas.

  2. **Threading de ``project.mcp_servers`` al runtime.** El dispatcher serializa
     los servers MCP del proyecto en el payload del worker (``mcp_servers``), que
     viaja por ``ExecutionRequest`` → ``_agent_spec`` → ``__main__`` donde se
     arranca ``MCPToolRunner``. Feature-safe: sin ``mcp_servers``, el payload no
     lleva la clave (comportamiento actual intacto, 06.15 backward-compat).

El mark ``cross_tenant`` lo exige CI: el tenant B no ve ni puede importar las
tools del proyecto del tenant A (404), y el threading respeta el aislamiento.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

from ._redis_url import TEST_REDIS_URL  # con credencial; ver _redis_url.py

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed: dos tenants (cada uno tenant_admin) + un proyecto por tenant con un
# MCP server `filesystem` declarado en `mcp_servers` (JSONB).
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    project_a = uuid4()
    project_b = uuid4()

    server = {
        "name": "filesystem",
        "transport": "stdio",
        "command": "filesystem-mcp",
        "args": [],
        "env": {},
        "url": None,
        "headers": {},
        "auth_ref": None,
        "timeout_s": 30.0,
    }

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE skills, tools, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a",
            tenant_b,
            "Tenant B",
            "tenant-b",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@a.test",
            "argon2-placeholder",
            user_b,
            "bob@b.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)",
            uuid4(),
            tenant_a,
            user_a,
            "tenant_admin",
            uuid4(),
            tenant_b,
            user_b,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, mcp_servers)"
            " VALUES ($1, $2, $3, 'active', $4::jsonb), ($5, $6, $7, 'active', $8::jsonb)",
            project_a,
            tenant_a,
            "Project A",
            json.dumps([server]),
            project_b,
            tenant_b,
            "Project B",
            json.dumps([server]),
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "project_a": project_a,
        "project_b": project_b,
    }


# ---------------------------------------------------------------------------
# Fixtures (misma forma que test_tool_uniqueness_and_taxonomy.py)
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")

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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


_IMPORT_BODY = {"tool_names": ["read_file", "write_file"]}


def _patch_discovery(monkeypatch: pytest.MonkeyPatch, schema: dict | None = None) -> None:
    """ADR 0101: el import re-descubre server-side (fail-closed). Los tests
    no tienen un MCP server vivo, así que se pincha el discovery con una
    respuesta canónica (read_file con schema, write_file sin args)."""
    from shared_mcp.discovery import DiscoveryResult
    from shared_mcp.types import MCPTool

    effective = schema if schema is not None else _SCHEMA_V1

    async def _discover(config, *, vault_resolver=None):
        return DiscoveryResult(
            tools=[
                MCPTool(
                    name="read_file",
                    description="Read a file from disk",
                    input_schema=effective,
                ),
                MCPTool(name="write_file", description=None, input_schema={}),
            ],
            server_name="filesystem",
        )

    monkeypatch.setattr("api_server.routers.mcp.discover_tools", _discover)


# ===========================================================================
# Importación: namespacing + category=mcp + security sandboxed por defecto
# ===========================================================================
@pytest.mark.asyncio
async def test_import_creates_namespaced_mcp_tools(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _patch_discovery(monkeypatch)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    project_id = seeded["project_a"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{project_id}/mcp/servers/filesystem/import-tools",
            json=_IMPORT_BODY,
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        imported = {t["name"]: t for t in body["tools"]}
        # Namespaced <server>.<tool> — no parecen duplicados de read_file.
        assert set(imported) == {"filesystem.read_file", "filesystem.write_file"}
        for tool in imported.values():
            assert tool["implementation_type"] == "mcp_tool"
            assert tool["category"] == "mcp"
            # sandboxed por defecto (mínimo privilegio a código de terceros).
            assert tool["security_level"] == "sandboxed"
            assert tool["implementation_ref"] == tool["name"]
            assert tool["is_builtin"] is False

        # Y son visibles en el catálogo del tenant.
        listed = await client.get("/tools?category=mcp", headers=headers)
        names = {t["name"] for t in listed.json()}
        assert {"filesystem.read_file", "filesystem.write_file"} <= names


@pytest.mark.asyncio
async def test_reimport_is_idempotent_no_duplicates(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _patch_discovery(monkeypatch)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    project_id = seeded["project_a"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        first = await client.post(
            f"/projects/{project_id}/mcp/servers/filesystem/import-tools",
            json=_IMPORT_BODY,
            headers=headers,
        )
        assert first.status_code == 200, first.text
        first_ids = {t["name"]: t["id"] for t in first.json()["tools"]}

        # Re-importar las mismas tools (ON CONFLICT actualiza, no inserta).
        second = await client.post(
            f"/projects/{project_id}/mcp/servers/filesystem/import-tools",
            json={"tool_names": ["read_file", "write_file"], "security_level": "safe"},
            headers=headers,
        )
        assert second.status_code == 200, second.text
        second_ids = {t["name"]: t["id"] for t in second.json()["tools"]}
        # Mismas filas (mismos ids), el security_level se actualizó.
        assert first_ids == second_ids
        for tool in second.json()["tools"]:
            assert tool["security_level"] == "safe"

        # No hay duplicados en el catálogo.
        listed = await client.get("/tools?category=mcp", headers=headers)
        rows = [t for t in listed.json() if t["name"] == "filesystem.read_file"]
        assert len(rows) == 1


# ===========================================================================
# Aislamiento cross-tenant
# ===========================================================================
@pytest.mark.asyncio
async def test_tenant_b_cannot_import_into_tenant_a_project(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])
    headers_b = {"Authorization": f"Bearer {token_b}"}
    project_a = seeded["project_a"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{project_a}/mcp/servers/filesystem/import-tools",
            json=_IMPORT_BODY,
            headers=headers_b,
        )
        # El proyecto de A no es visible para B → 404 (no filtra existencia).
        assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_imported_mcp_tools_not_visible_to_other_tenant(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    _patch_discovery(monkeypatch)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])
    project_a = seeded["project_a"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        imported = await client.post(
            f"/projects/{project_a}/mcp/servers/filesystem/import-tools",
            json=_IMPORT_BODY,
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert imported.status_code == 200, imported.text
        a_id = imported.json()["tools"][0]["id"]

        # B no ve la fila importada por A.
        fetch = await client.get(f"/tools/{a_id}", headers={"Authorization": f"Bearer {token_b}"})
        assert fetch.status_code == 404, fetch.text
        listed_b = await client.get(
            "/tools?category=mcp", headers={"Authorization": f"Bearer {token_b}"}
        )
        assert a_id not in {t["id"] for t in listed_b.json()}


@pytest.mark.asyncio
async def test_import_unknown_server_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    project_id = seeded["project_a"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{project_id}/mcp/servers/nonexistent/import-tools",
            json=_IMPORT_BODY,
            headers=headers,
        )
        # El server no está declarado en el proyecto → 404.
        assert resp.status_code == 404, resp.text


# ===========================================================================
# ADR 0101: el import persiste el input_schema RE-DESCUBIERTO server-side
# ===========================================================================
_SCHEMA_V1 = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}
_SCHEMA_V2 = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "encoding": {"type": "string"}},
    "required": ["path"],
}


def _fake_discovery(schema: dict, description: str = "Read a file from disk"):
    from shared_mcp.discovery import DiscoveryResult
    from shared_mcp.types import MCPTool

    async def _discover(config, *, vault_resolver=None):
        return DiscoveryResult(
            tools=[
                MCPTool(name="read_file", description=description, input_schema=schema),
                MCPTool(name="write_file", description=None, input_schema={}),
            ],
            server_name="filesystem",
        )

    return _discover


@pytest.mark.asyncio
async def test_import_persists_discovered_schema(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0101: sin esto, una tool MCP con args se anuncia al LLM con
    ``parameters: {}`` y el pre-guard del runtime la rechaza (inservible)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    project_id = seeded["project_a"]
    monkeypatch.setattr("api_server.routers.mcp.discover_tools", _fake_discovery(_SCHEMA_V1))

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{project_id}/mcp/servers/filesystem/import-tools",
            json=_IMPORT_BODY,
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        imported = {t["name"]: t for t in resp.json()["tools"]}
        # El schema descubierto se PERSISTE (antes quedaba '{}'::jsonb).
        assert imported["filesystem.read_file"]["input_schema"] == _SCHEMA_V1
        # La descripción real del server también (mejor que el placeholder).
        assert imported["filesystem.read_file"]["description"] == "Read a file from disk"
        # Una tool sin schema/descripción degrada al comportamiento histórico.
        assert imported["filesystem.write_file"]["input_schema"] == {}

        # Re-import con schema evolucionado → el upsert REFRESCA el schema.
        monkeypatch.setattr("api_server.routers.mcp.discover_tools", _fake_discovery(_SCHEMA_V2))
        second = await client.post(
            f"/projects/{project_id}/mcp/servers/filesystem/import-tools",
            json=_IMPORT_BODY,
            headers=headers,
        )
        assert second.status_code == 200, second.text
        refreshed = {t["name"]: t for t in second.json()["tools"]}
        assert refreshed["filesystem.read_file"]["input_schema"] == _SCHEMA_V2


@pytest.mark.asyncio
async def test_import_fails_closed_when_discovery_unreachable(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un server inalcanzable en el import → 502 tipado, NO una tool rota con
    schema vacío que recrearía el bug (fail-closed, ADR 0101)."""
    from shared_mcp.exceptions import MCPTransportError

    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    project_id = seeded["project_a"]

    async def _boom(config, *, vault_resolver=None):
        raise MCPTransportError("connection refused")

    monkeypatch.setattr("api_server.routers.mcp.discover_tools", _boom)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{project_id}/mcp/servers/filesystem/import-tools",
            json=_IMPORT_BODY,
            headers=headers,
        )
        assert resp.status_code == 502, resp.text
        # Y no quedó ninguna fila a medias en el catálogo.
        listed = await client.get("/tools?category=mcp", headers=headers)
        assert listed.json() == []


# ===========================================================================
# Threading de mcp_servers al runtime (dispatch → ExecutionRequest → spec)
# ===========================================================================
def test_execution_request_threads_mcp_servers() -> None:
    """El payload del worker lleva ``mcp_servers`` y el agent-spec lo reenvía."""
    from workers.execution import ExecutionRequest, _agent_spec

    servers = [
        {"name": "filesystem", "transport": "stdio", "command": "filesystem-mcp", "args": []},
    ]
    req = ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "scripted", "decisions": []},
        mcp_servers=servers,
    )
    spec = _agent_spec(req, None)
    assert spec["mcp_servers"] == servers
    # Round-trip por el payload Celery.
    rebuilt = ExecutionRequest.from_dict(req.as_dict())
    assert rebuilt.mcp_servers == servers


def test_agent_spec_omits_mcp_servers_when_none() -> None:
    """Sin servers MCP no se emite la clave (06.15 backward-compat)."""
    from workers.execution import ExecutionRequest, _agent_spec

    req = ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "scripted", "decisions": []},
        mcp_servers=None,
    )
    assert "mcp_servers" not in _agent_spec(req, None)


@pytest.mark.asyncio
async def test_dispatcher_threads_project_mcp_servers(
    configured_app, admin_database_url: str
) -> None:
    """El dispatcher serializa los ``mcp_servers`` del proyecto de la tarea."""
    import base64
    import json

    from api_server.db.domain import Agent, Project, Task
    from api_server.db.models import Organization
    from orchestrator.config import Settings as OrchestratorSettings
    from orchestrator.dispatch import TaskDispatcher
    from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from workers.celery_app import build_celery_app
    from workers.config import Settings as WorkerSettings

    test_redis_url = TEST_REDIS_URL
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = {
            "tenant": uuid4(),
            "project": uuid4(),
            "agent": uuid4(),
            "task": uuid4(),
        }
        servers = [
            {
                "name": "filesystem",
                "transport": "stdio",
                "command": "filesystem-mcp",
                "args": [],
                "env": {},
                "url": None,
                "headers": {},
                "auth_ref": None,
                "timeout_s": 30.0,
            }
        ]
        from sqlalchemy import text

        async with sm() as s, s.begin():
            await s.execute(
                text(
                    "TRUNCATE executions, task_dependencies, tasks, agents, projects,"
                    " organizations RESTART IDENTITY CASCADE"
                )
            )
            s.add(Organization(id=ids["tenant"], name="MCP tenant", slug="mcp-tenant"))
            await s.flush()
            s.add(
                Project(
                    id=ids["project"],
                    tenant_id=ids["tenant"],
                    name="MCP project",
                    status="active",
                    is_template=False,
                    worker_config={"assignment_policy": "load_balanced"},
                    mcp_servers=servers,
                )
            )
            await s.flush()
            s.add(
                Agent(
                    id=ids["agent"],
                    tenant_id=ids["tenant"],
                    name="Writer",
                    role="backend_dev",
                    system_prompt="You write things.",
                    agent_type="ai",
                    scope="project_local",
                    project_id=ids["project"],
                    model_config={"kind": "scripted", "decisions": []},
                )
            )
            await s.flush()
            s.add(
                Task(
                    id=ids["task"],
                    tenant_id=ids["tenant"],
                    project_id=ids["project"],
                    title="Use MCP",
                    description="exercise mcp threading",
                    status="ready",
                    priority="medium",
                )
            )

        await redis.delete("default")
        dispatcher = TaskDispatcher(
            sessionmaker=sm,
            celery_app=build_celery_app(
                WorkerSettings(broker_url=test_redis_url, result_backend=test_redis_url)
            ),
            settings=OrchestratorSettings(redis_url=test_redis_url),
        )
        event = TaskEvent(
            stream_id="1-0",
            type=EVENT_TASK_STATUS_CHANGED,
            tenant_id=str(ids["tenant"]),
            project_id=str(ids["project"]),
            task_id=str(ids["task"]),
            occurred_at="2026-06-04T00:00:00+00:00",
            payload={"old_status": "backlog", "new_status": "ready"},
        )
        await dispatcher.handle(event)

        raw = await redis.lrange("default", 0, -1)
        assert len(raw) == 1
        message = json.loads(raw[0])
        body = json.loads(base64.b64decode(message["body"]))
        _args, kwargs, _embed = body
        request = kwargs["request"]
        assert request["mcp_servers"] == servers
    finally:
        await redis.delete("default")
        await redis.aclose()


# ---------------------------------------------------------------------------
# task_wf_10 (B-01) — permitir no es anunciar
#
# ADR 0128 metió las tools MCP del proyecto en `allowed_tools`, pero el anuncio
# al modelo se construye desde `tool_specs`, que es POR AGENTE. La tool quedaba
# permitida e invisible: el modelo no sabía que existía, así que jamás la
# llamaba y el ADR no entregaba lo que prometía. Este test recorre el camino
# entero — fila `Tool` del catálogo → dispatch → `ExecutionRequest` → agent-spec
# — y comprueba que el esquema llega al bloque `model.tools`.
# ---------------------------------------------------------------------------
_MCP_TOOL_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string", "description": "Ruta a listar."}},
    "required": ["path"],
}


@pytest.mark.asyncio
async def test_project_mcp_tool_schema_reaches_the_model(
    configured_app, admin_database_url: str
) -> None:
    """Un agente SIN grants, en un proyecto con MCP, recibe el esquema de la tool."""
    import base64

    from api_server.db.domain import Agent, Project, Task, Tool
    from api_server.db.models import Organization
    from orchestrator.config import Settings as OrchestratorSettings
    from orchestrator.dispatch import TaskDispatcher
    from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
    from redis.asyncio import Redis
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from workers.celery_app import build_celery_app
    from workers.config import Settings as WorkerSettings
    from workers.execution import ExecutionRequest, _agent_spec

    test_redis_url = TEST_REDIS_URL
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = {"tenant": uuid4(), "project": uuid4(), "agent": uuid4(), "task": uuid4()}
        servers = [
            {
                "name": "filesystem",
                "transport": "stdio",
                "command": "filesystem-mcp",
                "args": [],
                "env": {},
                "url": None,
                "headers": {},
                "auth_ref": None,
                "timeout_s": 30.0,
            }
        ]
        async with sm() as s, s.begin():
            await s.execute(
                text(
                    "TRUNCATE executions, task_dependencies, tasks, agent_tools, tools,"
                    " agents, projects, organizations RESTART IDENTITY CASCADE"
                )
            )
            s.add(Organization(id=ids["tenant"], name="MCP schema tenant", slug="mcp-schema"))
            await s.flush()
            s.add(
                Project(
                    id=ids["project"],
                    tenant_id=ids["tenant"],
                    name="MCP schema project",
                    status="active",
                    is_template=False,
                    worker_config={"assignment_policy": "load_balanced"},
                    mcp_servers=servers,
                )
            )
            # La fila del catálogo que la importación de discovery habría creado.
            s.add(
                Tool(
                    tenant_id=ids["tenant"],
                    name="filesystem.list_directory",
                    description="Lista un directorio del servidor MCP.",
                    implementation_type="mcp_tool",
                    implementation_ref="filesystem.list_directory",
                    category="mcp",
                    security_level="sandboxed",
                    input_schema=_MCP_TOOL_SCHEMA,
                )
            )
            await s.flush()
            # SIN `agent_tools`: el agente no tiene ninguna tool concedida.
            s.add(
                Agent(
                    id=ids["agent"],
                    tenant_id=ids["tenant"],
                    name="Writer",
                    role="backend_dev",
                    system_prompt="You write things.",
                    agent_type="ai",
                    scope="project_local",
                    project_id=ids["project"],
                    model_config={"kind": "scripted", "decisions": []},
                )
            )
            await s.flush()
            s.add(
                Task(
                    id=ids["task"],
                    tenant_id=ids["tenant"],
                    project_id=ids["project"],
                    title="Use the MCP tool",
                    description="exercise mcp schema threading",
                    status="ready",
                    priority="medium",
                )
            )

        await redis.delete("default")
        dispatcher = TaskDispatcher(
            sessionmaker=sm,
            celery_app=build_celery_app(
                WorkerSettings(broker_url=test_redis_url, result_backend=test_redis_url)
            ),
            settings=OrchestratorSettings(redis_url=test_redis_url),
        )
        await dispatcher.handle(
            TaskEvent(
                stream_id="1-0",
                type=EVENT_TASK_STATUS_CHANGED,
                tenant_id=str(ids["tenant"]),
                project_id=str(ids["project"]),
                task_id=str(ids["task"]),
                occurred_at="2026-07-25T00:00:00+00:00",
                payload={"old_status": "backlog", "new_status": "ready"},
            )
        )

        raw = await redis.lrange("default", 0, -1)
        assert len(raw) == 1
        _args, kwargs, _embed = json.loads(base64.b64decode(json.loads(raw[0])["body"]))
        request = kwargs["request"]

        # 1. El dispatch aporta el ESPECIFICADOR, no solo el nombre.
        specs = {spec["name"]: spec for spec in request["tool_specs"]}
        assert "filesystem.list_directory" in specs
        assert specs["filesystem.list_directory"]["input_schema"] == _MCP_TOOL_SCHEMA

        # 2. Y el esquema llega al bloque que el proveedor pasa al modelo.
        spec = _agent_spec(ExecutionRequest.from_dict(request), None)
        advertised = {t["function"]["name"]: t["function"] for t in spec["model"]["tools"]}
        assert "filesystem.list_directory" in advertised, (
            "la tool MCP del proyecto sigue siendo invisible para el modelo"
        )
        assert advertised["filesystem.list_directory"]["parameters"] == _MCP_TOOL_SCHEMA
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()
        await engine.dispose()
