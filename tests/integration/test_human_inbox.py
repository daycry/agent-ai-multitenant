"""Integration tests for the personal inbox (Plan 16 task_16_08).

Exercises the ``/inbox`` router against the REAL Postgres (dev stack on PG
15432) through the FastAPI app under RLS (app_user, NOBYPASSRLS), so every
assertion is the production code path:

  - a user sees ONLY their OWN active assignments (not another user's, not
    another tenant's, not the closed/declined history);
  - accept -> assignment ``accepted`` + Task ``assigned_to_human -> in_progress``;
  - reject WITH a justification -> assignment ``declined`` + Task ``blocked``,
    the justification recorded in the task audit trail; reject WITHOUT a
    justification -> 422;
  - complete -> Task ``in_progress -> in_review`` (toward review);
  - escalate -> Task ``blocked`` + a ``task_blocked`` event fanned out to the
    tenant's admins (the celery producer is monkeypatched to capture it);
  - a user CANNOT act on another user's / another tenant's assignment (404),
    @pytest.mark.cross_tenant.

The fixture pattern mirrors test_human_agents_gallery.py: seed two tenants +
users + memberships + a human Agent/config + a project/plan/task +
HumanTaskAssignment rows via the BYPASSRLS migrations role; mint JWTs binding
each user to a tenant; drive the API via AsyncClient.
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

pytestmark = pytest.mark.integration

_PENDING = "pending_acceptance"
_ACCEPTED = "accepted"


async def _seed(dsn: str) -> dict[str, UUID]:
    """Two tenants, each with a user owning a pending human-task assignment.

    Tenant A: ``alice`` (assignee) + ``admin_a`` + ``bob`` (another A user).
    Tenant B: ``carol`` (assignee in B) — the cross-tenant isolation case.
    """
    tenant_a = uuid4()
    tenant_b = uuid4()
    alice = uuid4()  # A: the assignee whose inbox we test
    bob = uuid4()  # A: a different A user (cannot see Alice's assignment)
    admin_a = uuid4()  # A: tenant_admin
    carol = uuid4()  # B: assignee in tenant B (isolation)

    project_a = uuid4()
    plan_a = uuid4()
    project_b = uuid4()

    agent_a = uuid4()  # A's human agent
    agent_b = uuid4()  # B's human agent

    # Tasks (one per assignment scenario in A) + B's task.
    task_pending = uuid4()  # Alice: pending_acceptance -> accept / reject / escalate
    task_accepted = uuid4()  # Alice: accepted -> complete
    task_bob = uuid4()  # Bob: another A user's task (Alice must not see/act)
    task_b = uuid4()  # Carol: tenant B's task (cross-tenant)

    asg_pending = uuid4()
    asg_accepted = uuid4()
    asg_bob = uuid4()
    asg_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE human_task_assignments, human_agent_config, tasks, plans, agents,"
            " projects, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "inbox-a",
            tenant_b,
            "Tenant B",
            "inbox-b",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9), ($10, $11, $12)",
            alice,
            "alice@a.test",
            "ph",
            bob,
            "bob@a.test",
            "ph",
            admin_a,
            "admin-a@a.test",
            "ph",
            carol,
            "carol@b.test",
            "ph",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8), ($9, $10, $11, $12), ($13, $14, $15, $16)",
            uuid4(),
            tenant_a,
            alice,
            "tenant_user",
            uuid4(),
            tenant_a,
            bob,
            "tenant_user",
            uuid4(),
            tenant_a,
            admin_a,
            "tenant_admin",
            uuid4(),
            tenant_b,
            carol,
            "tenant_user",
        )
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
        # Two human agents (one per tenant), each with a config.
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
                "Reviewer",
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
        # Tasks: Alice's pending (assigned_to_human), Alice's accepted (in_progress),
        # Bob's pending, Carol's pending in B.
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status) VALUES"
            " ($1, $2, $3, $4, $5, 'assigned_to_human')",
            task_pending,
            tenant_a,
            project_a,
            plan_a,
            "Revisión legal (pendiente)",
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status) VALUES"
            " ($1, $2, $3, $4, $5, 'in_progress')",
            task_accepted,
            tenant_a,
            project_a,
            plan_a,
            "Revisión legal (aceptada)",
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status) VALUES"
            " ($1, $2, $3, NULL, $4, 'assigned_to_human')",
            task_bob,
            tenant_a,
            project_a,
            "Tarea de Bob",
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status) VALUES"
            " ($1, $2, $3, NULL, $4, 'assigned_to_human')",
            task_b,
            tenant_b,
            project_b,
            "Tarea de Carol",
        )
        # Assignments.
        for asg_id, tenant_id, task_id, agent_id, user_id, st in (
            (asg_pending, tenant_a, task_pending, agent_a, alice, _PENDING),
            (asg_accepted, tenant_a, task_accepted, agent_a, alice, _ACCEPTED),
            (asg_bob, tenant_a, task_bob, agent_a, bob, _PENDING),
            (asg_b, tenant_b, task_b, agent_b, carol, _PENDING),
        ):
            await conn.execute(
                "INSERT INTO human_task_assignments (id, tenant_id, task_id, human_agent_id,"
                " assigned_to_user_id, status) VALUES ($1, $2, $3, $4, $5, $6)",
                asg_id,
                tenant_id,
                task_id,
                agent_id,
                user_id,
                st,
            )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "alice": alice,
        "bob": bob,
        "admin_a": admin_a,
        "carol": carol,
        "task_pending": task_pending,
        "task_accepted": task_accepted,
        "task_bob": task_bob,
        "asg_pending": asg_pending,
        "asg_accepted": asg_accepted,
        "asg_bob": asg_bob,
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


async def _assignment_status(dsn: str, asg_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT status FROM human_task_assignments WHERE id = $1", asg_id
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# A user sees ONLY their own active assignments
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_user_sees_only_their_own_active_assignments(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/inbox/assignments", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    asg_ids = {row["assignment_id"] for row in body}
    # Alice's two assignments (pending + accepted) are present.
    assert str(seeded["asg_pending"]) in asg_ids
    assert str(seeded["asg_accepted"]) in asg_ids
    # Bob's assignment is NOT in Alice's inbox.
    assert str(seeded["asg_bob"]) not in asg_ids
    # Context is folded in: project + plan + a computed acceptance deadline.
    pending = next(r for r in body if r["assignment_id"] == str(seeded["asg_pending"]))
    assert pending["task_status"] == "assigned_to_human"
    assert pending["assignment_status"] == _PENDING
    assert pending["project_name"] == "Proyecto A"
    assert pending["plan_title"] == "Plan A"
    assert pending["acceptance_deadline"] is not None
    accepted = next(r for r in body if r["assignment_id"] == str(seeded["asg_accepted"]))
    # No acceptance deadline once accepted.
    assert accepted["acceptance_deadline"] is None
    assert accepted["task_status"] == "in_progress"


# ---------------------------------------------------------------------------
# accept -> accepted + task transitions to in_progress
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_accept_transitions_assignment_and_task(
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
            f"/inbox/assignments/{seeded['asg_pending']}/accept", headers=headers
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assignment_status"] == _ACCEPTED
    assert body["task_status"] == "in_progress"

    assert await _assignment_status(migrations_pg_dsn, seeded["asg_pending"]) == _ACCEPTED
    assert await _task_status(migrations_pg_dsn, seeded["task_pending"]) == "in_progress"


# ---------------------------------------------------------------------------
# reject WITH justification -> declined + task blocked, justification audited
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reject_with_justification_blocks_task_and_records_reason(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Reject WITHOUT a justification -> 422.
        bad = await client.post(
            f"/inbox/assignments/{seeded['asg_pending']}/reject", json={}, headers=headers
        )
        assert bad.status_code == 422, bad.text

        # Reject WITH a justification -> declined + blocked.
        ok = await client.post(
            f"/inbox/assignments/{seeded['asg_pending']}/reject",
            json={"justification": "Fuera de mi área de competencia"},
            headers=headers,
        )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["assignment_status"] == "declined"
    assert body["task_status"] == "blocked"

    assert await _assignment_status(migrations_pg_dsn, seeded["asg_pending"]) == "declined"
    assert await _task_status(migrations_pg_dsn, seeded["task_pending"]) == "blocked"

    # The justification is recorded in the task audit trail.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT payload FROM task_audit_events WHERE task_id = $1 AND kind ="
            " 'human_inbox_action' ORDER BY at DESC LIMIT 1",
            seeded["task_pending"],
        )
    finally:
        await conn.close()
    assert row is not None
    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    assert payload["action"] == "reject"
    assert payload["justification"] == "Fuera de mi área de competencia"
    assert payload["actor_user_id"] == str(seeded["alice"])


# ---------------------------------------------------------------------------
# complete -> task moves toward in_review
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_complete_moves_task_to_in_review(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_accepted']}/complete",
            json={"comments": "Listo, sin observaciones"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_status"] == "in_review"
    # The assignment stays accepted (the historical record of who did the work).
    assert body["assignment_status"] == _ACCEPTED
    assert await _task_status(migrations_pg_dsn, seeded["task_accepted"]) == "in_review"


# ---------------------------------------------------------------------------
# escalate -> task blocked + a task_blocked event fanned out to admins
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_escalate_blocks_task_and_notifies_admin(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    captured: list[dict] = []

    async def _capture(event: dict, **_kwargs) -> bool:
        captured.append(event)
        return True

    # The escalate path fans a task_blocked event out via the celery producer —
    # patch it (no broker) and assert the event the router built.
    monkeypatch.setattr("api_server.routers.human_inbox.enqueue_event_dispatch", _capture)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_pending']}/escalate",
            json={"justification": "Necesita autoridad de un admin"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assignment_status"] == "declined"
    assert body["task_status"] == "blocked"
    assert await _task_status(migrations_pg_dsn, seeded["task_pending"]) == "blocked"

    assert len(captured) == 1
    event = captured[0]
    assert event["event_type"] == "task_blocked"
    assert event["tenant_id"] == str(seeded["tenant_a"])
    assert event["context"]["task_id"] == str(seeded["task_pending"])
    assert event["context"]["escalated_by_user_id"] == str(seeded["alice"])


# ---------------------------------------------------------------------------
# A user cannot act on another user's assignment (same tenant)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_user_cannot_act_on_another_users_assignment(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Bob's assignment (same tenant, different user) -> 404 for Alice.
        resp = await client.post(f"/inbox/assignments/{seeded['asg_bob']}/accept", headers=headers)
    assert resp.status_code == 404, resp.text
    # Bob's task is untouched.
    assert await _task_status(migrations_pg_dsn, seeded["task_bob"]) == "assigned_to_human"
    assert await _assignment_status(migrations_pg_dsn, seeded["asg_bob"]) == _PENDING


# ---------------------------------------------------------------------------
# Cross-tenant isolation (RLS + per-user scope)
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_user_cannot_act_on_another_tenants_assignment(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["alice"], seeded["tenant_a"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Alice (tenant A) cannot even see tenant B's assignment in her list.
        listed = await client.get(
            "/inbox/assignments", headers={"Authorization": f"Bearer {token_a}"}
        )
        assert listed.status_code == 200
        ids = {r["assignment_id"] for r in listed.json()}
        assert str(seeded["asg_b"]) not in ids

        # And cannot act on it by id (RLS hides B's row -> 404).
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_b']}/accept",
            headers={"Authorization": f"Bearer {token_a}"},
        )
    assert resp.status_code == 404, resp.text
    # Carol's tenant-B assignment is untouched.
    assert await _assignment_status(migrations_pg_dsn, seeded["asg_b"]) == _PENDING
