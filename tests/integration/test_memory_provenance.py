"""task_ui_31 — GET /memories con procedencia, /memories/counts y /memories/search.

Contra Postgres real:
  - cada memoria trae quién (`created_by`), dónde (proyecto/equipo/agente con
    NOMBRE), cuándo, `execution_id` y `recall_count` (null: lo pobla task_mem_02),
  - filtros por agente / ejecución / `created_by` / fechas,
  - las privadas sólo para su dueño (listado, contadores y buscador),
  - cross-tenant: nada de otro tenant aparece en ninguna de las tres rutas,
  - una memoria antigua sin procedencia sale con `created_by.kind == 'unknown'`.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        k: uuid4()
        for k in [
            "ta",
            "tb",
            "alice",
            "amy",
            "bob",
            "team",
            "project",
            "agent",
            "task",
            "execution",
            "hws",
            "m_agent",
            "m_priv_alice",
            "m_priv_amy",
            "m_global",
            "m_legacy",
            "m_human",
            "m_b",
        ]
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, human_work_sessions, executions, tasks, plans,"
            " conversations, projects, agents, teams, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'A','prov-a'),"
            " ($2,'B','prov-b'), ($3,'P','prov-p')",
            ids["ta"],
            ids["tb"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name) VALUES"
            " ($1,'alice@prov.test','h','Alice Ruiz'), ($2,'amy@prov.test','h','Amy Sol'),"
            " ($3,'bob@prov.test','h','Bob')",
            ids["alice"],
            ids["amy"],
            ids["bob"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1,$2,$3,'tenant_member'), ($4,$2,$5,'tenant_member'),"
            " ($6,$7,$8,'tenant_member')",
            uuid4(),
            ids["ta"],
            ids["alice"],
            uuid4(),
            ids["amy"],
            uuid4(),
            ids["tb"],
            ids["bob"],
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,'Equipo Uno')",
            ids["team"],
            ids["ta"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1,$2,'Proyecto Uno')",
            ids["project"],
            ids["ta"],
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, project_id, name, role, system_prompt,"
            " memory_scope, scope) VALUES ($1,$2,$3,'Backend Bot','backend_dev','x',"
            " 'project_shared','project_local')",
            ids["agent"],
            ids["ta"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title) VALUES ($1,$2,$3,'T')",
            ids["task"],
            ids["ta"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, agent_id, status, output,"
            " steps_log) VALUES ($1,$2,$3,$4,'done','o','[]'::jsonb)",
            ids["execution"],
            ids["ta"],
            ids["task"],
            ids["agent"],
        )
        await conn.execute(
            "INSERT INTO human_work_sessions (id, tenant_id, task_id, user_id, start_at)"
            " VALUES ($1,$2,$3,$4, now())",
            ids["hws"],
            ids["ta"],
            ids["task"],
            ids["amy"],
        )
        # Aprendida por un agente en una ejecución, proyecto compartido.
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id,"
            " agent_id, source_execution_id) VALUES ($1,$2,'project_shared','semantic',"
            " 'El proyecto usa asyncpg y pgvector.',$3,$4,$5)",
            ids["m_agent"],
            ids["ta"],
            ids["project"],
            ids["agent"],
            ids["execution"],
        )
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id)"
            " VALUES ($1,$2,'private','semantic','Nota privada de Alice sobre pgvector.',$3),"
            " ($4,$2,'private','semantic','Nota privada de Amy sobre pgvector.',$5)",
            ids["m_priv_alice"],
            ids["ta"],
            ids["alice"],
            ids["m_priv_amy"],
            ids["amy"],
        )
        # Antigua: sin agente, sin ejecución, sin sesión: procedencia desconocida.
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content)"
            " VALUES ($1,$2,'global','semantic','Regla global antigua sobre pgvector.')",
            ids["m_legacy"],
            ids["ta"],
        )
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, team_id,"
            " source_human_work_session_id) VALUES ($1,$2,'team_shared','episodic',"
            " 'Destilada de una sesión humana.',$3,$4)",
            ids["m_human"],
            ids["ta"],
            ids["team"],
            ids["hws"],
        )
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content)"
            " VALUES ($1,$2,'global','semantic','Global del tenant B sobre pgvector.')",
            ids["m_b"],
            ids["tb"],
        )
    finally:
        await conn.close()
    return ids


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

    from api_server.ingestion.embeddings import HashEmbedder
    from api_server.main import create_app
    from api_server.routers.docs_viewer import get_query_embedder

    app = create_app()

    async def _embedder():
        yield HashEmbedder()

    app.dependency_overrides[get_query_embedder] = _embedder
    try:
        yield app
    finally:
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _headers(user_id: UUID, tenant_id: UUID) -> dict[str, str]:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    token = encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)
    return {"Authorization": f"Bearer {token}"}


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_list_carries_provenance_with_names(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    h = await _headers(ids["alice"], ids["ta"])
    async with _client(configured_app) as c:
        rows = {r["id"]: r for r in (await c.get("/memories", headers=h)).json()}
    m = rows[str(ids["m_agent"])]
    assert m["category"] == "semantic"
    assert m["execution_id"] == str(ids["execution"])
    assert m["project_name"] == "Proyecto Uno"
    assert m["agent_name"] == "Backend Bot"
    assert m["created_by"] == {"kind": "agent", "id": str(ids["agent"]), "name": "Backend Bot"}
    assert "recall_count" in m and m["recall_count"] is None  # lo puebla task_mem_02
    h_row = rows[str(ids["m_human"])]
    assert h_row["team_name"] == "Equipo Uno"
    assert h_row["created_by"]["kind"] == "human" and h_row["created_by"]["name"] == "Amy Sol"
    assert rows[str(ids["m_legacy"])]["created_by"]["kind"] == "unknown"
    assert rows[str(ids["m_priv_alice"])]["created_by"]["name"] == "Alice Ruiz"


@pytest.mark.asyncio
async def test_list_filters_agent_execution_and_created_by(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    h = await _headers(ids["alice"], ids["ta"])
    async with _client(configured_app) as c:
        by_agent = (await c.get(f"/memories?agent_id={ids['agent']}", headers=h)).json()
        by_exec = (await c.get(f"/memories?execution_id={ids['execution']}", headers=h)).json()
        by_creator = (await c.get(f"/memories?created_by={ids['agent']}", headers=h)).json()
        future = (await c.get("/memories?created_from=2999-01-01T00:00:00Z", headers=h)).json()
    assert [r["id"] for r in by_agent] == [str(ids["m_agent"])]
    assert [r["id"] for r in by_exec] == [str(ids["m_agent"])]
    assert [r["id"] for r in by_creator] == [str(ids["m_agent"])]
    assert future == []


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_private_only_for_owner_and_no_cross_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    ha = await _headers(ids["alice"], ids["ta"])
    hb = await _headers(ids["bob"], ids["tb"])
    async with _client(configured_app) as c:
        alice = {r["id"] for r in (await c.get("/memories", headers=ha)).json()}
        bob = {r["id"] for r in (await c.get("/memories", headers=hb)).json()}
        # filtrar por el ámbito privado NO abre las de otro usuario
        amy_priv = (
            await c.get("/memories?scope=private&created_by=" + str(ids["amy"]), headers=ha)
        ).json()
        counts_a = (await c.get("/memories/counts", headers=ha)).json()
        counts_b = (await c.get("/memories/counts", headers=hb)).json()
    assert str(ids["m_priv_alice"]) in alice
    assert str(ids["m_priv_amy"]) not in alice
    assert str(ids["m_b"]) not in alice and str(ids["m_agent"]) not in bob
    assert bob == {str(ids["m_b"])}
    assert amy_priv == []
    assert counts_a == {"all": 4, "private": 1, "team_shared": 1, "project_shared": 1, "global": 1}
    assert counts_b == {"all": 1, "private": 0, "team_shared": 0, "project_shared": 0, "global": 1}


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_search_respects_private_and_tenant(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    ha = await _headers(ids["alice"], ids["ta"])
    async with _client(configured_app) as c:
        resp = await c.get("/memories/search", params={"q": "pgvector"}, headers=ha)
        scoped = await c.get(
            "/memories/search", params={"q": "pgvector", "scope": "global"}, headers=ha
        )
    assert resp.status_code == 200, resp.text
    found = {r["memory"]["id"] for r in resp.json()}
    assert str(ids["m_agent"]) in found and str(ids["m_priv_alice"]) in found
    assert str(ids["m_priv_amy"]) not in found and str(ids["m_b"]) not in found
    assert all(isinstance(r["score"], float) for r in resp.json())
    assert {r["memory"]["scope"] for r in scoped.json()} == {"global"}


@pytest.mark.asyncio
async def test_builtin_agent_is_resolved_but_other_tenants_agent_is_not(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Revisión adversarial (intento 2): una memoria escrita por un agente built-in
    (`scope = 'global_builtin'`, bajo el tenant de plataforma) sale con su nombre, no
    «desconocido»; el agente de OTRO tenant sigue sin resolverse."""
    ids = await _seed(migrations_pg_dsn)
    builtin, foreign = uuid4(), uuid4()
    m_builtin, m_foreign = uuid4(), uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope)"
            " VALUES ($1,$2,'PM Builtin','project_manager','x','global_builtin')",
            builtin,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope)"
            " VALUES ($1,$2,'Agente Ajeno','backend_dev','x','global_tenant_template')",
            foreign,
            ids["tb"],
        )
        for mid, agent in ((m_builtin, builtin), (m_foreign, foreign)):
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, project_id,"
                " agent_id) VALUES ($1,$2,'project_shared','semantic','recuerdo',$3,$4)",
                mid,
                ids["ta"],
                ids["project"],
                agent,
            )
    finally:
        await conn.close()
    h = await _headers(ids["alice"], ids["ta"])
    async with _client(configured_app) as c:
        rows = {r["id"]: r for r in (await c.get("/memories", headers=h)).json()}
    assert rows[str(m_builtin)]["created_by"]["name"] == "PM Builtin"
    assert rows[str(m_builtin)]["agent_name"] == "PM Builtin"
    assert rows[str(m_foreign)]["created_by"]["name"] is None
    assert rows[str(m_foreign)]["agent_name"] is None
