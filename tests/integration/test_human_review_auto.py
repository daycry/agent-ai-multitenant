"""Integration tests for the ``auto_approve`` human-task review mode (task_16_11).

Exercises ``POST /inbox/assignments/{id}/complete`` against the REAL Postgres
(dev stack on PG 15432) through the FastAPI app under RLS (app_user,
NOBYPASSRLS), so every assertion is the production code path:

  - a project with ``human_task_review_mode='auto_approve'`` (the default):
    submitting an ACCEPTED assignment creates the ``HumanWorkSession`` AND takes
    the Task straight to ``done`` — no extra review step, no reviewer
    assignment;
  - the submit result reports ``review_mode='auto_approve'`` and
    ``review_assignment_id=None``;
  - tenant isolation: a tenant-A user cannot submit a tenant-B assignment
    (@pytest.mark.cross_tenant), and tenant B's task is untouched.

Fixture pattern mirrors test_human_task_submit.py: seed two tenants + users +
memberships + a human Agent/config + a project/plan/task + HumanTaskAssignment
rows via the BYPASSRLS migrations role; mint JWTs binding each user to a
tenant; drive the API via AsyncClient.
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

_ACCEPTED = "accepted"


async def _seed(dsn: str) -> dict[str, UUID]:
    """Two tenants, each with a user owning an ACCEPTED human-task assignment on
    an ``auto_approve`` project (the default review mode)."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    alice = uuid4()  # A: the assignee whose submit we test
    carol = uuid4()  # B: assignee in tenant B (isolation)

    project_a = uuid4()
    plan_a = uuid4()
    project_b = uuid4()

    agent_a = uuid4()  # A's human agent
    agent_b = uuid4()  # B's human agent

    task_a = uuid4()  # Alice: accepted (in_progress) -> submit -> done
    task_b = uuid4()  # Carol: tenant B's accepted task (cross-tenant)

    asg_a = uuid4()
    asg_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE human_work_sessions, human_task_assignments, human_agent_config,"
            " task_audit_events, tasks, plans, agents, projects, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "rev-auto-a",
            tenant_b,
            "Tenant B",
            "rev-auto-b",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            alice,
            "alice@a.test",
            "ph",
            carol,
            "carol@b.test",
            "ph",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8)",
            uuid4(),
            tenant_a,
            alice,
            "tenant_user",
            uuid4(),
            tenant_b,
            carol,
            "tenant_user",
        )
        # Projects with the DEFAULT review mode (auto_approve). We do NOT set
        # human_task_review_mode so we also prove the column default applies.
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            project_a,
            tenant_a,
            "Proyecto A",
            project_b,
            tenant_b,
            "Proyecto B",
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status) VALUES"
            " ($1, $2, $3, $4, 'in_progress')",
            plan_a,
            tenant_a,
            project_a,
            "Plan A",
        )
        for agent_id, tenant_id, user_id in (
            (agent_a, tenant_a, alice),
            (agent_b, tenant_b, carol),
        ):
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, agent_type, role, system_prompt,"
                " model_config, scope, is_template, project_id)"
                " VALUES ($1, $2, $3, 'human', 'reviewer', 'h', '{}'::jsonb,"
                " 'global_tenant_template', true, NULL)",
                agent_id,
                tenant_id,
                "Worker",
            )
            await conn.execute(
                "INSERT INTO human_agent_config (id, tenant_id, agent_id, assignment_mode,"
                " assigned_user_id, acceptance_timeout_hours, notification_channels)"
                " VALUES ($1, $2, $3, 'specific_user', $4, 12, '[]'::jsonb)",
                uuid4(),
                tenant_id,
                agent_id,
                user_id,
            )
        for task_id, tenant_id, project_id, plan_id, agent_id in (
            (task_a, tenant_a, project_a, plan_a, agent_a),
            (task_b, tenant_b, project_b, None, agent_b),
        ):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status,"
                " assigned_agent_id) VALUES ($1, $2, $3, $4, $5, 'in_progress', $6)",
                task_id,
                tenant_id,
                project_id,
                plan_id,
                "Tarea humana",
                agent_id,
            )
        for asg_id, tenant_id, task_id, agent_id, user_id in (
            (asg_a, tenant_a, task_a, agent_a, alice),
            (asg_b, tenant_b, task_b, agent_b, carol),
        ):
            await conn.execute(
                "INSERT INTO human_task_assignments (id, tenant_id, task_id, human_agent_id,"
                " assigned_to_user_id, status) VALUES ($1, $2, $3, $4, $5, $6)",
                asg_id,
                tenant_id,
                task_id,
                agent_id,
                user_id,
                _ACCEPTED,
            )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "alice": alice,
        "carol": carol,
        "task_a": task_a,
        "task_b": task_b,
        "asg_a": asg_a,
        "asg_b": asg_b,
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


async def _task_status(dsn: str, task_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT status FROM tasks WHERE id = $1", task_id)
    finally:
        await conn.close()


async def _assignment_count(dsn: str, task_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM human_task_assignments WHERE task_id = $1", task_id
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# auto_approve: submit -> done, no extra review step, no reviewer assignment
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_auto_approve_completes_task_without_extra_review(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_a']}/complete",
            json={"output": "Revisión completada, todo OK."},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Straight to done — no extra review step.
    assert body["task_status"] == "done"
    assert body["review_mode"] == "auto_approve"
    assert body["review_assignment_id"] is None
    assert body["work_session_id"]

    # The Task is done in the DB.
    assert await _task_status(migrations_pg_dsn, seeded["task_a"]) == "done"
    # No reviewer assignment was created — only the original work assignment.
    assert await _assignment_count(migrations_pg_dsn, seeded["task_a"]) == 1


# ---------------------------------------------------------------------------
# Cross-tenant isolation (RLS + per-user scope) -> 404, task untouched
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cannot_complete_another_tenants_assignment(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["alice"], seeded["tenant_a"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Alice (tenant A) cannot submit tenant B's assignment (RLS hides it).
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_b']}/complete",
            json={"output": "cross-tenant"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
    assert resp.status_code == 404, resp.text
    # Carol's tenant-B task is untouched (still in_progress, not done).
    assert await _task_status(migrations_pg_dsn, seeded["task_b"]) == "in_progress"
