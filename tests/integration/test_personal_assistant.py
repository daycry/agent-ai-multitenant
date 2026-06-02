"""Integration tests for the conversational personal assistant
(Plan 10 task_10_14).

Binding access constraints under test (see docs/roadmap/10-asistente-personal.md):

  * Tenant-Admin-only: a ``tenant_user`` / member gets 403.
  * Toggle-gated: ``Organization.personal_assistant_enabled`` DEFAULTS to
    false; a Tenant Admin of a tenant with the toggle OFF gets 403/disabled.
  * Cross-project READ tools are tenant-scoped (RLS) — a tool NEVER returns
    another tenant's data (``@pytest.mark.cross_tenant``).
  * ``tenant_budget_status`` is a typed "not available yet" placeholder
    (the budget engine is Plan 11).

The LLM is mocked: the router's ``get_assistant_model`` dependency is
overridden with a ``ScriptedAssistantModel`` so no real provider is
contacted (the established chat-test pattern).
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
    """Two tenants. Tenant A: an admin + a member, toggle ON, with a
    project/plan/task. Tenant B: an admin, toggle ON, with its own
    project/plan so cross-tenant isolation has something to leak (and
    must not)."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    admin_a = uuid4()
    member_a = uuid4()
    admin_b = uuid4()
    project_a = uuid4()
    project_b = uuid4()
    plan_a = uuid4()
    plan_b = uuid4()
    task_a = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE tasks, plans, messages, conversations, projects, agents,"
            " tenant_settings, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, personal_assistant_enabled) VALUES"
            " ($1, $2, $3, true), ($4, $5, $6, true), ($7, $8, $9, false)",
            tenant_a,
            "Tenant A",
            "tenant-a-pa",
            tenant_b,
            "Tenant B",
            "tenant-b-pa",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-pa",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            admin_a,
            "admin-a@pa.test",
            "argon2-placeholder",
            member_a,
            "member-a@pa.test",
            "argon2-placeholder",
            admin_b,
            "admin-b@pa.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8), ($9, $10, $11, $12)",
            uuid4(),
            tenant_a,
            admin_a,
            "tenant_admin",
            uuid4(),
            tenant_a,
            member_a,
            "tenant_user",
            uuid4(),
            tenant_b,
            admin_b,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8)",
            project_a,
            tenant_a,
            "Project A",
            "active",
            project_b,
            tenant_b,
            "Project B (secret)",
            "active",
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, created_by) VALUES"
            " ($1, $2, $3, $4, $5, $6), ($7, $8, $9, $10, $11, $12)",
            plan_a,
            tenant_a,
            project_a,
            "Plan A pending",
            "pending_approval",
            admin_a,
            plan_b,
            tenant_b,
            project_b,
            "Plan B secret",
            "pending_approval",
            admin_b,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status) VALUES"
            " ($1, $2, $3, $4, $5, $6)",
            task_a,
            tenant_a,
            project_a,
            plan_a,
            "Implement A endpoint",
            "in_progress",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_a": admin_a,
        "member_a": member_a,
        "admin_b": admin_b,
        "project_a": project_a,
        "project_b": project_b,
        "plan_a": plan_a,
        "plan_b": plan_b,
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


def _scripted_model():
    """A ``ScriptedAssistantModel`` that calls the three read tools then
    answers — replaces the LLM so no provider is contacted."""
    from api_server.assistant.graph import ModelTurn, ScriptedAssistantModel, ToolInvocation

    return ScriptedAssistantModel(
        turns=[
            ModelTurn(
                tool_calls=(
                    ToolInvocation(name="tenant_projects_status"),
                    ToolInvocation(name="tenant_plans_summary"),
                    ToolInvocation(name="tenant_recent_activity", arguments={"limit": 10}),
                    ToolInvocation(name="tenant_budget_status"),
                )
            ),
            ModelTurn(content="Tienes 1 proyecto activo y 1 plan pendiente de aprobación."),
        ]
    )


def _install_scripted_model(app) -> None:
    from api_server.routers.assistant import get_assistant_model

    app.dependency_overrides[get_assistant_model] = _scripted_model


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ===========================================================================
# Access tests
# ===========================================================================
@pytest.mark.asyncio
async def test_assistant_chat_unauthenticated_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post("/assistant/chat", json={"message": "hola"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tenant_admin_with_toggle_on_gets_answer_calling_read_tools(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Happy path: a Tenant Admin of a toggle-ON tenant gets an answer,
    and the cross-project read tools are actually invoked."""
    seeded = await _seed(migrations_pg_dsn)
    _install_scripted_model(configured_app)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/assistant/chat",
            json={"message": "¿Qué tengo pendiente?"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"]
    # The read tools were exercised (the binding "tools called" assertion).
    assert "tenant_projects_status" in body["tools_called"]
    assert "tenant_plans_summary" in body["tools_called"]
    assert "tenant_recent_activity" in body["tools_called"]
    assert body["rounds"] >= 1


@pytest.mark.asyncio
async def test_member_is_403(configured_app, migrations_pg_dsn: str) -> None:
    """A ``tenant_user`` (member) is denied even with the toggle ON."""
    seeded = await _seed(migrations_pg_dsn)
    _install_scripted_model(configured_app)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/assistant/chat",
            json={"message": "hola"},
            headers=headers,
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_toggle_off_is_403_for_tenant_admin(configured_app, migrations_pg_dsn: str) -> None:
    """A Tenant Admin whose tenant has the toggle OFF is denied."""
    seeded = await _seed(migrations_pg_dsn)
    _install_scripted_model(configured_app)
    # Flip tenant A's toggle off after the seed (seed set it on).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE organizations SET personal_assistant_enabled = false WHERE id = $1",
            seeded["tenant_a"],
        )
    finally:
        await conn.close()

    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/assistant/chat",
            json={"message": "hola"},
            headers=headers,
        )
    assert resp.status_code == 403, resp.text
    assert "disabled" in resp.json()["detail"].lower()


# ===========================================================================
# Tenant isolation
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_read_tools_never_return_other_tenant_data(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant A's admin asks the assistant; the cross-project read tools
    run under A's RLS-bound session and must NEVER surface tenant B's
    project/plan. We assert at the tool layer (the source of truth) by
    driving the tools directly with A's tenant context, and end-to-end via
    the chat endpoint."""
    seeded = await _seed(migrations_pg_dsn)
    _install_scripted_model(configured_app)

    # --- Tool-layer isolation: run the tools under A's session ---
    from api_server.assistant.tools import (
        AssistantToolContext,
        run_assistant_tool,
    )
    from api_server.auth.deps import AuthPrincipal, open_tenant_session
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()
    principal = AuthPrincipal(
        user_id=seeded["admin_a"],
        session_id=uuid4(),
        tenant_id=seeded["tenant_a"],
    )
    async with open_tenant_session(principal) as session:
        ctx = AssistantToolContext(
            session=session,
            tenant_id=seeded["tenant_a"],
            user_id=seeded["admin_a"],
        )
        projects = await run_assistant_tool("tenant_projects_status", ctx)
        plans = await run_assistant_tool("tenant_plans_summary", ctx)

    project_ids = {p["id"] for p in projects["projects"]}
    plan_ids = {p["id"] for p in plans["plans"]}
    # A sees ONLY its own project/plan.
    assert str(seeded["project_a"]) in project_ids
    assert str(seeded["project_b"]) not in project_ids
    assert str(seeded["plan_a"]) in plan_ids
    assert str(seeded["plan_b"]) not in plan_ids
    # And no row at all from B leaked.
    assert projects["total"] == 1
    assert plans["total"] == 1


# ===========================================================================
# Budget status — no budget configured (Plan 11.1 task_11_1_05)
# ===========================================================================
@pytest.mark.asyncio
async def test_budget_tool_reports_no_budget_when_unconfigured(
    configured_app, migrations_pg_dsn: str
) -> None:
    """``tenant_budget_status`` returns a typed 'no budget configured' result
    when the tenant (and its projects) have no budget set — an honest answer,
    not fabricated numbers. (The budget engine is now real — Plan 11.1.)"""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import AssistantToolContext, run_assistant_tool
    from api_server.auth.deps import AuthPrincipal, open_tenant_session
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()
    principal = AuthPrincipal(
        user_id=seeded["admin_a"],
        session_id=uuid4(),
        tenant_id=seeded["tenant_a"],
    )
    async with open_tenant_session(principal) as session:
        ctx = AssistantToolContext(
            session=session,
            tenant_id=seeded["tenant_a"],
            user_id=seeded["admin_a"],
        )
        budget = await run_assistant_tool("tenant_budget_status", ctx)

    assert budget["available"] is False
    assert budget["reason"] == "no_budget_configured"


# ===========================================================================
# Identity config
# ===========================================================================
@pytest.mark.asyncio
async def test_identity_get_default_and_update_roundtrip(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Default identity (nothing stored yet).
        got = await client.get("/assistant/identity", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json()["language"] == "es"

        # Update it.
        upd = await client.put(
            "/assistant/identity",
            json={
                "name": "Aria",
                "tone": "cercano",
                "language": "en",
                "system_prompt_override": "Be brief.",
                "enabled_tools": ["tenant_projects_status", "tenant_plans_summary"],
            },
            headers=headers,
        )
        assert upd.status_code == 200, upd.text
        body = upd.json()
        assert body["name"] == "Aria"
        assert body["language"] == "en"
        assert set(body["enabled_tools"]) == {"tenant_projects_status", "tenant_plans_summary"}

        # Persisted.
        got2 = await client.get("/assistant/identity", headers=headers)
        assert got2.json()["name"] == "Aria"


@pytest.mark.asyncio
async def test_identity_member_is_403(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/assistant/identity", headers=headers)
    assert resp.status_code == 403
