"""Integration tests for the human-task delivery form (Plan 16 task_16_09).

Exercises ``POST /inbox/assignments/{id}/complete`` against the REAL Postgres
(dev stack on PG 15432) through the FastAPI app under RLS (app_user,
NOBYPASSRLS), so every assertion is the production code path:

  - submitting an ACCEPTED assignment creates a ``HumanWorkSession`` with the
    output text + the attachment references + the (optional) logged hours, and
    transitions the Task ``in_progress -> in_review``;
  - the work session carries the caller as ``user_id``, the task's ``tenant_id``
    and a closed window (``end_at >= start_at``);
  - the optional hours field can be omitted (``hours_logged`` stays NULL);
  - only the ASSIGNEE can submit — another user's / another tenant's assignment
    id resolves to 404 and creates NO work session (@pytest.mark.cross_tenant);
  - submitting an assignment that is not ``accepted`` (still pending) -> 409.

The fixture pattern mirrors test_human_inbox.py: seed two tenants + users +
memberships + a human Agent/config + a project/plan/task + HumanTaskAssignment
rows via the BYPASSRLS migrations role; mint JWTs binding each user to a
tenant; drive the API via AsyncClient.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
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
    """Two tenants, each with a user owning an ACCEPTED human-task assignment.

    Tenant A: ``alice`` (assignee, accepted task ready to submit) + ``bob``
    (another A user, also accepted — Alice must not be able to submit his).
    Tenant B: ``carol`` (assignee in B) — the cross-tenant isolation case.
    Plus Alice's still-PENDING assignment (submit must 409 — not yet accepted).
    """
    tenant_a = uuid4()
    tenant_b = uuid4()
    alice = uuid4()  # A: the assignee whose submit we test
    bob = uuid4()  # A: a different A user (Alice cannot submit his task)
    carol = uuid4()  # B: assignee in tenant B (isolation)

    project_a = uuid4()
    plan_a = uuid4()
    project_b = uuid4()

    agent_a = uuid4()  # A's human agent
    agent_b = uuid4()  # B's human agent

    task_accepted = uuid4()  # Alice: accepted (in_progress) -> submit
    task_pending = uuid4()  # Alice: pending (assigned_to_human) -> 409 on submit
    task_bob = uuid4()  # Bob: another A user's accepted task
    task_b = uuid4()  # Carol: tenant B's accepted task (cross-tenant)

    asg_accepted = uuid4()
    asg_pending = uuid4()
    asg_bob = uuid4()
    asg_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE human_work_sessions, human_task_assignments, human_agent_config,"
            " tasks, plans, agents, projects, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "submit-a",
            tenant_b,
            "Tenant B",
            "submit-b",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            alice,
            "alice@a.test",
            "ph",
            bob,
            "bob@a.test",
            "ph",
            carol,
            "carol@b.test",
            "ph",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8), ($9, $10, $11, $12)",
            uuid4(),
            tenant_a,
            alice,
            "tenant_user",
            uuid4(),
            tenant_a,
            bob,
            "tenant_user",
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
        # Tasks: accepted ones are in_progress (ready to submit -> in_review);
        # the pending one is assigned_to_human (submit must 409).
        for task_id, tenant_id, project_id, plan_id, title, st in (
            (
                task_accepted,
                tenant_a,
                project_a,
                plan_a,
                "Revisión legal (aceptada)",
                "in_progress",
            ),
            (
                task_pending,
                tenant_a,
                project_a,
                plan_a,
                "Revisión legal (pendiente)",
                "assigned_to_human",
            ),
            (task_bob, tenant_a, project_a, None, "Tarea de Bob", "in_progress"),
            (task_b, tenant_b, project_b, None, "Tarea de Carol", "in_progress"),
        ):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status) VALUES"
                " ($1, $2, $3, $4, $5, $6)",
                task_id,
                tenant_id,
                project_id,
                plan_id,
                title,
                st,
            )
        for asg_id, tenant_id, task_id, agent_id, user_id, st in (
            (asg_accepted, tenant_a, task_accepted, agent_a, alice, _ACCEPTED),
            (asg_pending, tenant_a, task_pending, agent_a, alice, _PENDING),
            (asg_bob, tenant_a, task_bob, agent_a, bob, _ACCEPTED),
            (asg_b, tenant_b, task_b, agent_b, carol, _ACCEPTED),
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
        "carol": carol,
        "task_accepted": task_accepted,
        "task_pending": task_pending,
        "task_bob": task_bob,
        "task_b": task_b,
        "asg_accepted": asg_accepted,
        "asg_pending": asg_pending,
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


async def _work_sessions(dsn: str, task_id: UUID) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return list(
            await conn.fetch(
                "SELECT id, tenant_id, user_id, start_at, end_at, hours_logged, comments,"
                " output_files_attached FROM human_work_sessions WHERE task_id = $1",
                task_id,
            )
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Submitting persists a HumanWorkSession + transitions the task to in_review
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_creates_work_session_with_output_attachments_and_hours(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "output": "Revisión completada: cláusulas 3 y 7 OK, anexo 2 con observaciones.",
        "attachments": [
            {"kind": "url", "label": "PR de cambios", "url": "https://example.test/pr/42"},
            {"kind": "file", "label": "Informe firmado", "ref": "minio://deliverables/informe.pdf"},
            # A targetless attachment (no url/ref) is dropped server-side.
            {"kind": "url", "label": "sin enlace"},
        ],
        "hours_worked": "3.50",
    }

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_accepted']}/complete",
            json=payload,
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_status"] == "in_review"
    assert body["assignment_status"] == _ACCEPTED  # historical record, unchanged
    assert body["attachments_count"] == 2  # the targetless one was dropped
    assert body["work_session_id"]

    # The Task moved to in_review in the DB.
    assert await _task_status(migrations_pg_dsn, seeded["task_accepted"]) == "in_review"

    # Exactly one HumanWorkSession was persisted, with the deliverable.
    sessions = await _work_sessions(migrations_pg_dsn, seeded["task_accepted"])
    assert len(sessions) == 1
    ws = sessions[0]
    assert str(ws["id"]) == body["work_session_id"]
    assert ws["tenant_id"] == seeded["tenant_a"]
    assert ws["user_id"] == seeded["alice"]
    assert ws["hours_logged"] == Decimal("3.50")
    assert "cláusulas 3 y 7 OK" in ws["comments"]
    # A closed session: end_at >= start_at.
    assert ws["end_at"] is not None
    assert ws["end_at"] >= ws["start_at"]
    # The two usable attachments were stored as JSONB references.
    attachments = ws["output_files_attached"]
    if isinstance(attachments, str):
        import json

        attachments = json.loads(attachments)
    assert len(attachments) == 2
    labels = {a["label"] for a in attachments}
    assert labels == {"PR de cambios", "Informe firmado"}


# ---------------------------------------------------------------------------
# The optional hours field can be omitted (hours_logged stays NULL)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_without_hours_leaves_hours_null(
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
            f"/inbox/assignments/{seeded['asg_accepted']}/complete",
            json={"output": "Listo, sin observaciones."},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["attachments_count"] == 0

    sessions = await _work_sessions(migrations_pg_dsn, seeded["task_accepted"])
    assert len(sessions) == 1
    assert sessions[0]["hours_logged"] is None
    assert sessions[0]["comments"] == "Listo, sin observaciones."


# ---------------------------------------------------------------------------
# Submitting a not-yet-accepted assignment -> 409, no work session
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_pending_assignment_conflicts(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_pending']}/complete",
            json={"output": "no debería persistir"},
            headers=headers,
        )
    assert resp.status_code == 409, resp.text
    # The task is untouched and NO work session was created.
    assert await _task_status(migrations_pg_dsn, seeded["task_pending"]) == "assigned_to_human"
    assert await _work_sessions(migrations_pg_dsn, seeded["task_pending"]) == []


# ---------------------------------------------------------------------------
# Only the assignee can submit (same tenant, different user) -> 404, no session
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_only_assignee_can_submit(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Alice submitting Bob's assignment (same tenant, different user) -> 404.
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_bob']}/complete",
            json={"output": "ajeno"},
            headers=headers,
        )
    assert resp.status_code == 404, resp.text
    # Bob's task is untouched and no work session was created on it.
    assert await _task_status(migrations_pg_dsn, seeded["task_bob"]) == "in_progress"
    assert await _work_sessions(migrations_pg_dsn, seeded["task_bob"]) == []


# ---------------------------------------------------------------------------
# Cross-tenant isolation (RLS + per-user scope) -> 404, no session
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cannot_submit_another_tenants_assignment(
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
    # Carol's tenant-B task is untouched and no work session leaked across.
    assert await _task_status(migrations_pg_dsn, seeded["task_b"]) == "in_progress"
    assert await _work_sessions(migrations_pg_dsn, seeded["task_b"]) == []
