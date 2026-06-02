"""Integration tests for the Human Agents gallery (Plan 16 task_16_07).

Exercises the ``/human-agents`` router against the REAL Postgres (the dev stack
on PG 15432) through the FastAPI app under RLS (app_user, NOBYPASSRLS), so every
assertion is the production code path:

  - create persists the Agent (agent_type='human') + its human_agent_config;
  - a global Human-Agent template clones+forks into the tenant (a NEW
    tenant-owned row with forked_from_agent_id set + a fresh config — NEVER
    linked to the global);
  - RBAC: a non-admin tenant member is denied the writes (403);
  - cross-tenant isolation: tenant B never sees tenant A's Human Agents and
    cannot fetch one by id (@pytest.mark.cross_tenant).

The fixture pattern mirrors test_agents_endpoints.py: seed two tenants +
users + memberships via the BYPASSRLS migrations role, plus ONE global_builtin
``agent_type='human'`` template owned by the platform tenant; mint JWTs binding
each user to a tenant; drive the API via AsyncClient.
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
    tenant_a = uuid4()
    tenant_b = uuid4()
    admin_a = uuid4()  # tenant_admin in A — the gallery operator
    member_a = uuid4()  # plain tenant_user in A — RBAC negative case
    pickee_a = uuid4()  # another A member, the assigned_user_id target
    admin_b = uuid4()  # tenant_admin in B — isolation case
    template = uuid4()  # global_builtin human template (clone source)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE human_agent_config, agents, projects, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "ha-tenant-a",
            tenant_b,
            "Tenant B",
            "ha-tenant-b",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9), ($10, $11, $12)",
            admin_a,
            "admin-a@a.test",
            "argon2-placeholder",
            member_a,
            "member-a@a.test",
            "argon2-placeholder",
            pickee_a,
            "pickee-a@a.test",
            "argon2-placeholder",
            admin_b,
            "admin-b@b.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8), ($9, $10, $11, $12), ($13, $14, $15, $16)",
            uuid4(),
            tenant_a,
            admin_a,
            "tenant_admin",
            uuid4(),
            tenant_a,
            member_a,
            "tenant_user",
            uuid4(),
            tenant_a,
            pickee_a,
            "tenant_user",
            uuid4(),
            tenant_b,
            admin_b,
            "tenant_admin",
        )
        # A global_builtin Human-Agent template owned by the platform tenant.
        # Visible to every tenant via agents_global_builtin_read; the clone
        # action forks it into the caller's tenant.
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, description, agent_type, role,"
            " system_prompt, model_config, scope, is_template, project_id)"
            " VALUES ($1, $2, $3, $4, 'human', $5, $6, $7::jsonb, 'global_builtin', true, NULL)",
            template,
            _PLATFORM_TENANT_ID,
            "Legal Reviewer",
            "Revisor legal global.",
            "reviewer",
            "Human agent template.",
            '{"acceptance_timeout_hours": 72, "expected_response_time_hours": 24,'
            ' "notification_channels": ["email"]}',
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_a": admin_a,
        "member_a": member_a,
        "pickee_a": pickee_a,
        "admin_b": admin_b,
        "template": template,
    }


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


def _create_payload(**overrides) -> dict:
    base: dict = {
        "name": "Security Reviewer",
        "role": "security",
        "config": {
            "hourly_rate": "90.00",
            "hourly_rate_currency": "EUR",
            "notification_channels": ["email", "in_app"],
            "acceptance_timeout_hours": 12,
            "expected_response_time_hours": 4,
            "expected_execution_time_hours": 8,
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Create persists agent + config
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_human_agent_persists_agent_and_config(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        payload = _create_payload()
        payload["config"]["assigned_user_id"] = str(seeded["pickee_a"])
        payload["config"]["escalation_target_user_id"] = str(seeded["admin_a"])
        created = await client.post("/human-agents", json=payload, headers=headers)
        assert created.status_code == 201, created.text
        body = created.json()
        agent_id = body["id"]
        assert body["agent_type"] == "human"
        assert body["scope"] == "global_tenant_template"
        assert UUID(body["tenant_id"]) == seeded["tenant_a"]
        # The config is folded into the response.
        cfg = body["config"]
        assert cfg is not None
        assert cfg["assignment_mode"] == "specific_user"
        assert UUID(cfg["assigned_user_id"]) == seeded["pickee_a"]
        assert UUID(cfg["escalation_target_user_id"]) == seeded["admin_a"]
        assert cfg["acceptance_timeout_hours"] == 12
        assert cfg["notification_channels"] == ["email", "in_app"]

        # GET roundtrips agent + config.
        got = await client.get(f"/human-agents/{agent_id}", headers=headers)
        assert got.status_code == 200
        assert got.json()["config"]["hourly_rate"] == "90.00"

        # LIST shows the new human agent (and NOT the global template).
        listed = await client.get("/human-agents", headers=headers)
        assert listed.status_code == 200
        names = {a["name"] for a in listed.json()}
        assert "Security Reviewer" in names
        assert "Legal Reviewer" not in names  # the global template is not here

    # Verify the config row actually persisted at the DB level (RLS-bypass).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT agent_id, assigned_user_id, acceptance_timeout_hours,"
            " hourly_rate FROM human_agent_config WHERE agent_id = $1",
            UUID(agent_id),
        )
    finally:
        await conn.close()
    assert row is not None
    assert row["assigned_user_id"] == seeded["pickee_a"]
    assert row["acceptance_timeout_hours"] == 12


# ---------------------------------------------------------------------------
# Update touches both agent and config
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_human_agent_patches_agent_and_config(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        created = await client.post("/human-agents", json=_create_payload(), headers=headers)
        agent_id = created.json()["id"]

        upd = await client.put(
            f"/human-agents/{agent_id}",
            json={
                "name": "Security Reviewer v2",
                "config": {
                    "assigned_user_id": str(seeded["pickee_a"]),
                    "acceptance_timeout_hours": 6,
                },
            },
            headers=headers,
        )
        assert upd.status_code == 200, upd.text
        body = upd.json()
        assert body["name"] == "Security Reviewer v2"
        assert UUID(body["config"]["assigned_user_id"]) == seeded["pickee_a"]
        assert body["config"]["acceptance_timeout_hours"] == 6
        # Untouched config field stays put.
        assert body["config"]["hourly_rate"] == "90.00"


# ---------------------------------------------------------------------------
# Clone-and-fork a global template into the tenant (forked, never linked)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_clone_global_template_forks_into_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # The template appears in the global catalog.
        templates = await client.get("/human-agents/templates", headers=headers)
        assert templates.status_code == 200
        catalog_ids = {t["id"] for t in templates.json()}
        assert str(seeded["template"]) in catalog_ids

        # Fork it into the tenant, pre-assigning a user.
        forked = await client.post(
            f"/human-agents/templates/{seeded['template']}/clone",
            json={"name": "Legal Reviewer (mío)", "assigned_user_id": str(seeded["pickee_a"])},
            headers=headers,
        )
        assert forked.status_code == 201, forked.text
        body = forked.json()
        fork_id = body["id"]
        assert fork_id != str(seeded["template"])  # a NEW row, not the global
        assert UUID(body["tenant_id"]) == seeded["tenant_a"]  # tenant-owned
        assert UUID(body["forked_from_agent_id"]) == seeded["template"]  # forked, traceable
        assert body["agent_type"] == "human"
        assert body["scope"] == "global_tenant_template"
        assert body["name"] == "Legal Reviewer (mío)"
        # A fresh, tenant-owned config — seeded from the template's hints.
        cfg = body["config"]
        assert cfg is not None
        assert UUID(cfg["assigned_user_id"]) == seeded["pickee_a"]
        assert cfg["acceptance_timeout_hours"] == 72  # from the template model_config
        assert cfg["expected_response_time_hours"] == 24
        assert cfg["notification_channels"] == ["email"]

        # The fork now appears in the tenant's own list.
        listed = await client.get("/human-agents", headers=headers)
        assert listed.status_code == 200
        assert fork_id in {a["id"] for a in listed.json()}

    # The fork is a brand-new tenant-owned row; the global template is
    # untouched and carries NO config (never linked).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        fork_tenant = await conn.fetchval(
            "SELECT tenant_id FROM agents WHERE id = $1", UUID(fork_id)
        )
        global_config_count = await conn.fetchval(
            "SELECT count(*) FROM human_agent_config WHERE agent_id = $1",
            seeded["template"],
        )
        fork_config_count = await conn.fetchval(
            "SELECT count(*) FROM human_agent_config WHERE agent_id = $1",
            UUID(fork_id),
        )
    finally:
        await conn.close()
    assert fork_tenant == seeded["tenant_a"]
    assert global_config_count == 0  # global template never gets a config
    assert fork_config_count == 1  # the fork owns exactly one


@pytest.mark.asyncio
async def test_clone_unknown_template_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            f"/human-agents/templates/{uuid4()}/clone", json={}, headers=headers
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RBAC: a non-admin tenant member cannot create/clone
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_admin_cannot_create_or_clone(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    member_token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {member_token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # A plain member CAN read the gallery...
        listed = await client.get("/human-agents", headers=headers)
        assert listed.status_code == 200

        # ...but CANNOT create a human agent.
        create = await client.post("/human-agents", json=_create_payload(), headers=headers)
        assert create.status_code == 403, create.text

        # ...nor clone a global template.
        clone = await client.post(
            f"/human-agents/templates/{seeded['template']}/clone", json={}, headers=headers
        )
        assert clone.status_code == 403, clone.text


@pytest.mark.asyncio
async def test_assignable_users_lists_tenant_members(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/human-agents/assignable-users", headers=headers)
    assert resp.status_code == 200, resp.text
    emails = {u["email"] for u in resp.json()}
    # Tenant A's three members are present; tenant B's admin is NOT.
    assert {"admin-a@a.test", "member-a@a.test", "pickee-a@a.test"} <= emails
    assert "admin-b@b.test" not in emails


# ---------------------------------------------------------------------------
# Cross-tenant isolation (RLS)
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_b_cannot_see_tenant_a_human_agents(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["admin_b"], seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/human-agents",
            json=_create_payload(name="A's secret reviewer"),
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert created.status_code == 201
        agent_id = created.json()["id"]

        # Tenant B's list MUST omit A's human agent.
        listed_b = await client.get("/human-agents", headers={"Authorization": f"Bearer {token_b}"})
        assert listed_b.status_code == 200
        assert "A's secret reviewer" not in {a["name"] for a in listed_b.json()}

        # Direct fetch by id from B -> 404 (don't leak existence).
        fetch_b = await client.get(
            f"/human-agents/{agent_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert fetch_b.status_code == 404

        # B cannot update A's human agent either (writable lookup is tenant-scoped).
        upd_b = await client.put(
            f"/human-agents/{agent_id}",
            json={"name": "hijacked"},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert upd_b.status_code == 404

        # B's assignable-users never leaks A's members.
        users_b = await client.get(
            "/human-agents/assignable-users", headers={"Authorization": f"Bearer {token_b}"}
        )
        assert users_b.status_code == 200
        emails_b = {u["email"] for u in users_b.json()}
        assert emails_b == {"admin-b@b.test"}
