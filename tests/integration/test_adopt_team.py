"""Integration tests for POST /teams/{id}/adopt (Ola C / ADR 0066).

Adoptar un equipo built-in crea una COPIA editable del tenant: un Team
``is_builtin=false`` enlazado al origen vía ``forked_from_team_id``, con cada
miembro FORKEADO (persona + tools + skills clonadas) y los ``TeamMember``
recreados. Usa el equipo CI4 real como fuente (10 miembros con tools+skills tras
la Ola B). El built-in original no se muta. Re-adopción permitida.
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
_CI4_TEAM_ID = UUID("6996ecb8-6536-54a4-9f0d-aceb135c0593")  # CI4_TEAM.id (uuid5 estable)
_TRUNCATE = (
    "TRUNCATE agent_skills, agent_tools, team_members, teams, agents, skills,"
    " tools, projects, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
)


async def _seed_builtin(dsn: str) -> None:
    """Siembra catálogo + equipo CI4 (built-in, tenant plataforma) sin Ollama."""
    from api_server.seeds.builtin_agents import (
        seed_builtin_agent_skills,
        seed_builtin_agent_tools,
        seed_builtin_agents,
    )
    from api_server.seeds.builtin_skills import seed_builtin_skills
    from api_server.seeds.builtin_tools import seed_builtin_tools
    from api_server.seeds.ci4_team import (
        seed_ci4_agent_skills,
        seed_ci4_agent_tools,
        seed_ci4_agents,
        seed_ci4_team,
    )
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async_dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )
    engine = create_async_engine(async_dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            await seed_builtin_skills(session)
            await seed_builtin_tools(session)
            await seed_builtin_agents(session)
            await seed_builtin_agent_skills(session)
            await seed_builtin_agent_tools(session)
            await seed_ci4_agents(session)
            await seed_ci4_agent_tools(session)
            await seed_ci4_agent_skills(session)
            await seed_ci4_team(session)
    finally:
        await engine.dispose()


async def _seed_tenant(dsn: str) -> dict[str, UUID]:
    """Crea tenant_a + user_a (tenant_admin) + project_a (la plataforma ya existe)."""
    tenant_a, user_a, project_a = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_a,
            "Tenant A",
            "tenant-a",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_a,
            "a@x.test",
            "x",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES ($1,$2,$3,$4)",
            uuid4(),
            tenant_a,
            user_a,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_a,
            tenant_a,
            "A Project",
        )
    finally:
        await conn.close()
    return {"tenant_a": tenant_a, "user_a": user_a, "project_a": project_a}


async def _prepare(dsn: str) -> dict[str, UUID]:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(_TRUNCATE)
    finally:
        await conn.close()
    await _seed_builtin(dsn)
    return await _seed_tenant(dsn)


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


# ===========================================================================
# Tests
# ===========================================================================
@pytest.mark.asyncio
async def test_adopt_builtin_team_into_project(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _prepare(migrations_pg_dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    model_cfg = {"provider": "claude_sdk", "model": "claude-sonnet-4-5"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/teams/{_CI4_TEAM_ID}/adopt",
            json={
                "target": "project",
                "project_id": str(ids["project_a"]),
                "name": "Mi equipo CI4",
                "model_config": model_cfg,
            },
            headers=headers,
        )

    assert resp.status_code == 201, resp.text
    new_team_id = UUID(resp.json()["id"])

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        team = await conn.fetchrow("SELECT * FROM teams WHERE id = $1", new_team_id)
        assert team["is_builtin"] is False
        assert team["tenant_id"] == ids["tenant_a"]
        assert team["forked_from_team_id"] == _CI4_TEAM_ID
        assert team["forked_from_version"] is not None
        # El modelo elegido al adoptar se aplica al equipo nuevo (engancha con A).
        import json as _json

        assert _json.loads(team["model_config"]) == model_cfg

        # 10 miembros, todos agentes forkeados project_local de tenant_a.
        members = await conn.fetch(
            "SELECT a.scope, a.project_id, a.forked_from_agent_id, a.tenant_id"
            " FROM team_members tm JOIN agents a ON a.id = tm.agent_id"
            " WHERE tm.team_id = $1",
            new_team_id,
        )
        assert len(members) == 10
        for m in members:
            assert m["scope"] == "project_local"
            assert m["project_id"] == ids["project_a"]
            assert m["forked_from_agent_id"] is not None
            assert m["tenant_id"] == ids["tenant_a"]

        # Capacidades clonadas: cada miembro forkeado tiene tools Y skills.
        cap = await conn.fetch(
            "SELECT a.id,"
            " (SELECT count(*) FROM agent_tools t WHERE t.agent_id = a.id) tools,"
            " (SELECT count(*) FROM agent_skills s WHERE s.agent_id = a.id) skills"
            " FROM team_members tm JOIN agents a ON a.id = tm.agent_id"
            " WHERE tm.team_id = $1",
            new_team_id,
        )
        empty = [
            (r["id"], r["tools"], r["skills"]) for r in cap if r["tools"] == 0 or r["skills"] == 0
        ]
        assert not empty, f"miembros forkeados sin tool/skill: {empty}"

        # El built-in original NO se muta.
        src = await conn.fetchrow("SELECT is_builtin FROM teams WHERE id = $1", _CI4_TEAM_ID)
        assert src["is_builtin"] is True
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_adopt_builtin_team_into_tenant(configured_app, migrations_pg_dsn: str) -> None:
    ids = await _prepare(migrations_pg_dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/teams/{_CI4_TEAM_ID}/adopt",
            json={"target": "tenant"},
            headers=headers,
        )

    assert resp.status_code == 201, resp.text
    new_team_id = UUID(resp.json()["id"])

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        members = await conn.fetch(
            "SELECT a.scope, a.project_id FROM team_members tm"
            " JOIN agents a ON a.id = tm.agent_id WHERE tm.team_id = $1",
            new_team_id,
        )
        assert len(members) == 10
        for m in members:
            assert m["scope"] == "global_tenant_template"
            assert m["project_id"] is None
    finally:
        await conn.close()
