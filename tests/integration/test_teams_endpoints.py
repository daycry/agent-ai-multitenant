"""Integration tests for /teams endpoints with M:N members (task_01_06).

Covers:
  - CRUD on teams.
  - Adding / updating / removing members with metadata.
  - Cross-tenant isolation: a team in tenant A is invisible to tenant B.
  - Adding the same agent twice -> 409.
  - Adding a non-existent or cross-tenant agent -> 404.
  - Adding a `global_builtin` agent works (visible via RLS).
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


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    agent_a1 = uuid4()  # tenant A
    agent_a2 = uuid4()  # tenant A
    agent_b = uuid4()  # tenant B (must stay invisible to A)
    builtin_agent = uuid4()  # global_builtin

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE team_members, teams, agents, projects,"
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
            "INSERT INTO users (id, email, password_hash) VALUES" " ($1, $2, $3), ($4, $5, $6)",
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

        # Three agents: two A (project_local won't work since no project,
        # so use global_tenant_template), one B, one global_builtin.
        for agent_id, ten, name, scope in [
            (agent_a1, tenant_a, "A-Backend", "global_tenant_template"),
            (agent_a2, tenant_a, "A-Frontend", "global_tenant_template"),
            (agent_b, tenant_b, "B-Backend", "global_tenant_template"),
            (builtin_agent, _PLATFORM_TENANT_ID, "Built-in PM", "global_builtin"),
        ]:
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, role, system_prompt,"
                " model_config, scope, project_id)"
                " VALUES ($1, $2, $3, 'backend_dev', $4, '{}'::jsonb, $5, NULL)",
                agent_id,
                ten,
                name,
                "...",
                scope,
            )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "agent_a1": agent_a1,
        "agent_a2": agent_a2,
        "agent_b": agent_b,
        "builtin_agent": builtin_agent,
    }


# ---------------------------------------------------------------------------
# Fixtures
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


# ===========================================================================
# Tests
# ===========================================================================
@pytest.mark.asyncio
async def test_teams_unauthenticated_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/teams")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_team_crud_roundtrip(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            "/teams",
            json={"name": "Full-Stack Web", "description": "A team"},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        team = create.json()
        assert team["name"] == "Full-Stack Web"
        assert team["members"] == []
        team_id = team["id"]

        # PUT
        upd = await client.put(
            f"/teams/{team_id}",
            json={"description": "Frontend + backend pod"},
            headers=headers,
        )
        assert upd.status_code == 200
        assert upd.json()["description"] == "Frontend + backend pod"

        # GET
        got = await client.get(f"/teams/{team_id}", headers=headers)
        assert got.status_code == 200
        assert got.json()["id"] == team_id

        # DELETE
        dele = await client.delete(f"/teams/{team_id}", headers=headers)
        assert dele.status_code == 204
        gone = await client.get(f"/teams/{team_id}", headers=headers)
        assert gone.status_code == 404


@pytest.mark.asyncio
async def test_team_model_config_roundtrip(configured_app, migrations_pg_dsn: str) -> None:
    """Ola A-UI: PUT /teams/{id} fija el modelo por defecto del equipo y GET lo
    devuelve (clave JSON `model_config`, alias del `llm_config` Python)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    cfg = {"provider": "claude_sdk", "model": "claude-sonnet-4-5", "temperature": 0.2}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        team_id = (
            await client.post("/teams", json={"name": "Con modelo"}, headers=headers)
        ).json()["id"]
        # Recién creado: sin modelo (hereda).
        assert (await client.get(f"/teams/{team_id}", headers=headers)).json()["model_config"] == {}

        upd = await client.put(f"/teams/{team_id}", json={"model_config": cfg}, headers=headers)
        assert upd.status_code == 200, upd.text
        assert upd.json()["model_config"] == cfg

        got = await client.get(f"/teams/{team_id}", headers=headers)
        assert got.json()["model_config"]["provider"] == "claude_sdk"


@pytest.mark.asyncio
async def test_team_memory_scope_roundtrip(configured_app, migrations_pg_dsn: str) -> None:
    """ADR 0071: PUT /teams/{id} fija/quita la política de memoria del equipo y
    GET la devuelve. `null` explícito la quita (heredar); omitir no la toca."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        team_id = (
            await client.post("/teams", json={"name": "Con memoria"}, headers=headers)
        ).json()["id"]
        # Recién creado: sin política (hereda).
        assert (await client.get(f"/teams/{team_id}", headers=headers)).json()[
            "memory_scope"
        ] is None

        # Fijar la política.
        upd = await client.put(
            f"/teams/{team_id}", json={"memory_scope": "team_shared"}, headers=headers
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["memory_scope"] == "team_shared"

        # Omitir memory_scope (tocar solo el nombre) NO la cambia.
        await client.put(f"/teams/{team_id}", json={"name": "Renombrado"}, headers=headers)
        assert (await client.get(f"/teams/{team_id}", headers=headers)).json()[
            "memory_scope"
        ] == "team_shared"

        # `null` explícito la quita (heredar).
        cleared = await client.put(
            f"/teams/{team_id}", json={"memory_scope": None}, headers=headers
        )
        assert cleared.json()["memory_scope"] is None


@pytest.mark.asyncio
async def test_agent_response_includes_teams(configured_app, migrations_pg_dsn: str) -> None:
    """ADR 0071: GET /agents/{id} devuelve los equipos del agente (badge/filtros/
    disable del memory_scope); un agente sin equipo trae lista vacía."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        team_id = (
            await client.post("/teams", json={"name": "Plataforma X"}, headers=headers)
        ).json()["id"]
        await client.post(
            f"/teams/{team_id}/members",
            json={"agent_id": str(seeded["agent_a1"])},
            headers=headers,
        )
        detail = await client.get(f"/agents/{seeded['agent_a1']}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert "Plataforma X" in {t["name"] for t in detail.json()["teams"]}

        other = await client.get(f"/agents/{seeded['agent_a2']}", headers=headers)
        assert other.json()["teams"] == []


@pytest.mark.asyncio
async def test_members_lifecycle(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        team_id = (await client.post("/teams", json={"name": "Web"}, headers=headers)).json()["id"]

        # ADD two members.
        for agent_id, leader in [
            (str(seeded["agent_a1"]), True),
            (str(seeded["agent_a2"]), False),
        ]:
            resp = await client.post(
                f"/teams/{team_id}/members",
                json={
                    "agent_id": agent_id,
                    "role_in_team": "Lead Dev" if leader else "Dev",
                    "is_team_leader": leader,
                    "assignment_priority": 10 if leader else 50,
                },
                headers=headers,
            )
            assert resp.status_code == 201, resp.text

        # GET team -> should have 2 members, leader first by priority.
        got = await client.get(f"/teams/{team_id}", headers=headers)
        members = got.json()["members"]
        assert len(members) == 2
        assert members[0]["is_team_leader"] is True
        assert members[1]["is_team_leader"] is False

        # ADD same agent twice -> 409.
        dup = await client.post(
            f"/teams/{team_id}/members",
            json={"agent_id": str(seeded["agent_a1"])},
            headers=headers,
        )
        assert dup.status_code == 409

        # UPDATE member metadata.
        upd = await client.put(
            f"/teams/{team_id}/members/{seeded['agent_a2']}",
            json={"role_in_team": "Senior Dev", "assignment_priority": 20},
            headers=headers,
        )
        assert upd.status_code == 200
        updated_member = next(
            m for m in upd.json()["members"] if m["agent_id"] == str(seeded["agent_a2"])
        )
        assert updated_member["role_in_team"] == "Senior Dev"
        assert updated_member["assignment_priority"] == 20

        # REMOVE member.
        rem = await client.delete(f"/teams/{team_id}/members/{seeded['agent_a1']}", headers=headers)
        assert rem.status_code == 204
        final = await client.get(f"/teams/{team_id}", headers=headers)
        assert len(final.json()["members"]) == 1


@pytest.mark.asyncio
async def test_add_global_builtin_agent_to_team(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        team_id = (await client.post("/teams", json={"name": "T"}, headers=headers)).json()["id"]
        resp = await client.post(
            f"/teams/{team_id}/members",
            json={"agent_id": str(seeded["builtin_agent"])},
            headers=headers,
        )
    assert resp.status_code == 201
    members = resp.json()["members"]
    assert any(m["agent_id"] == str(seeded["builtin_agent"]) for m in members)


@pytest.mark.asyncio
async def test_cannot_add_other_tenants_agent(configured_app, migrations_pg_dsn: str) -> None:
    """RLS hides tenant B's agent from tenant A; the router's
    _verify_agent_visible therefore raises 404 (not 500/FK error)."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        team_id = (await client.post("/teams", json={"name": "T"}, headers=headers)).json()["id"]
        resp = await client.post(
            f"/teams/{team_id}/members",
            json={"agent_id": str(seeded["agent_b"])},
            headers=headers,
        )
    assert resp.status_code == 404
    assert "agent not found" in resp.text.lower()


@pytest.mark.asyncio
async def test_team_isolation_across_tenants(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        team_a = (
            await client.post(
                "/teams",
                json={"name": "A's team"},
                headers={"Authorization": f"Bearer {token_a}"},
            )
        ).json()
        team_a_id = team_a["id"]

        listed_b = await client.get("/teams", headers={"Authorization": f"Bearer {token_b}"})
        assert team_a_id not in {t["id"] for t in listed_b.json()}

        fetch_b = await client.get(
            f"/teams/{team_a_id}", headers={"Authorization": f"Bearer {token_b}"}
        )
        assert fetch_b.status_code == 404
