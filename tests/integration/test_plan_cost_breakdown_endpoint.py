"""Integration test for GET /plans/{id}/cost-breakdown (Plan 03 task_03_24).

Wraps `compute_human_cost` + `compute_ai_cost` over a persisted plan.
The unit tests already cover the calculation; this exercises the
endpoint plumbing and the response shape."""

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
    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE plan_comments, plans, conversations, projects, agents,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Cost",
            "tenant-cost",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-cost",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@cost.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_id,
            user_id,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Cost Project",
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id, "project_id": project_id}


async def _seed_with_agent(dsn: str) -> dict[str, UUID]:
    """Like ``_seed`` but also wires a team with one ``backend_dev`` agent whose
    model_config pins ``claude-opus-4-7``, and points the project at that team —
    so a spec task with ``role: backend_dev`` resolves to that agent's model."""
    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    team_id = uuid4()
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE team_members, teams, plan_comments, plans, conversations, projects,"
            " agents, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Cost",
            "tenant-cost",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-cost",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@cost.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_id,
            user_id,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, $3)",
            team_id,
            tenant_id,
            "Cost Team",
        )
        # A global_tenant_template agent (project_id NULL) satisfies the
        # scope<->project_id CHECK; its model_config pins claude-opus-4-7.
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope, model_config)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)",
            agent_id,
            tenant_id,
            "Bea Backend",
            "backend_dev",
            "Eres una desarrolladora backend.",
            "global_tenant_template",
            '{"provider": "claude_sdk", "model": "claude-opus-4-7", "temperature": 0.1}',
        )
        await conn.execute(
            "INSERT INTO team_members (team_id, agent_id) VALUES ($1, $2)",
            team_id,
            agent_id,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, team_id) VALUES ($1, $2, $3, $4)",
            project_id,
            tenant_id,
            "Cost Project",
            team_id,
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id, "project_id": project_id}


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


_PLAN_SPEC = {
    "tasks": [
        {"id": "t1", "title": "Modelar", "complexity": "m", "estimated_hours": 4},
        {"id": "t2", "title": "Implementar", "complexity": "l", "estimated_hours": 12},
        {"id": "t3", "title": "QA", "complexity": "s", "estimated_hours": 6},
    ],
}


@pytest.mark.asyncio
async def test_cost_breakdown_returns_human_and_ai_totals(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "Cost plan", "specification": _PLAN_SPEC},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        plan_id = create.json()["id"]

        resp = await client.get(f"/plans/{plan_id}/cost-breakdown", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Human cost: 4 + 12 + 6 = 22 h × 50 €/h = 1100 €.
        human = body["human"]
        assert human["currency"] == "EUR"
        assert human["hourly_rate"] == "50.00"
        assert human["total_hours"] == "22.000"
        assert human["total_cost"] == "1100.00"
        assert {t["task_id"] for t in human["tasks"]} == {"t1", "t2", "t3"}

        # AI cost is a range — both bounds positive, min < max.
        ai = body["ai"]
        assert ai["default_model_id"] == "gpt-4o"
        assert ai["currency"] == "USD"
        assert float(ai["cost_min"]) > 0
        assert float(ai["cost_max"]) > float(ai["cost_min"])
        assert ai["missing_models"] == []


@pytest.mark.asyncio
async def test_cost_breakdown_query_overrides_model_and_rate(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "Cost plan v2", "specification": _PLAN_SPEC},
            headers=headers,
        )
        plan_id = create.json()["id"]

        # ?model=claude-opus-4-7 picks the more expensive model.
        # ?hourly_rate=80 raises the human cost proportionally.
        opus = await client.get(
            f"/plans/{plan_id}/cost-breakdown?model=claude-opus-4-7&hourly_rate=80",
            headers=headers,
        )
        gpt = await client.get(
            f"/plans/{plan_id}/cost-breakdown?model=gpt-4o&hourly_rate=50",
            headers=headers,
        )

        opus_body = opus.json()
        gpt_body = gpt.json()

        # 22 h × 80 €/h = 1760 €
        assert opus_body["human"]["total_cost"] == "1760.00"
        assert gpt_body["human"]["total_cost"] == "1100.00"

        # Opus is strictly more expensive than gpt-4o.
        assert float(opus_body["ai"]["cost_min"]) > float(gpt_body["ai"]["cost_min"])
        assert opus_body["ai"]["default_model_id"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_cost_breakdown_prices_task_by_assigned_agent_model(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The cost breakdown must price each task by the model of the agent assigned
    to its role (override or inherited — ADR 0065), NOT a blanket gpt-4o.

    Regression for the operator report 'lo hace con gpt-4o'."""
    seeded = await _seed_with_agent(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    spec = {
        "tasks": [
            # Maps to the team's backend_dev agent (model_config = claude-opus-4-7).
            {"id": "t1", "title": "Backend", "complexity": "m", "role": "backend_dev"},
            # No role → no agent → falls back to the default (gpt-4o).
            {"id": "t2", "title": "Suelta", "complexity": "m"},
        ],
    }

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "Cost by agent", "specification": spec},
            headers=headers,
        )
        assert create.status_code == 201, create.text
        plan_id = create.json()["id"]

        resp = await client.get(f"/plans/{plan_id}/cost-breakdown", headers=headers)
        assert resp.status_code == 200, resp.text
        ai = resp.json()["ai"]
        by_id = {t["task_id"]: t for t in ai["tasks"]}

        # The agent's model wins for its task; the unassigned task stays on default.
        assert by_id["t1"]["model_id"] == "claude-opus-4-7"
        assert by_id["t2"]["model_id"] == "gpt-4o"
        # Opus IS in the catalog → it priced (not a missing-model 0 row).
        assert "claude-opus-4-7" not in ai["missing_models"]
        assert float(by_id["t1"]["cost_min"]) > 0


@pytest.mark.asyncio
async def test_cost_breakdown_on_empty_spec_returns_zeros(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "Empty plan"},  # no specification
            headers=headers,
        )
        plan_id = create.json()["id"]

        resp = await client.get(f"/plans/{plan_id}/cost-breakdown", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["human"]["total_cost"] == "0.00"
        assert body["ai"]["cost_min"] == "0.0000"
        assert body["ai"]["cost_max"] == "0.0000"
        assert body["human"]["tasks"] == []
        assert body["ai"]["tasks"] == []
