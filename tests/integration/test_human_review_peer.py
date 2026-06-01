"""Integration tests for the ``peer_human_reviewer`` review mode (task_16_11).

Exercises the peer-review flow against the REAL Postgres (dev stack on PG
15432) through the FastAPI app under RLS (app_user, NOBYPASSRLS), so every
assertion is the production code path:

  - a project with ``human_task_review_mode='peer_human_reviewer'``: when the
    worker submits an accepted assignment, the Task STAYS ``in_review`` and a
    SECOND ``HumanTaskAssignment`` is created for the reviewer Human Agent
    (resolved from ``task.reviewer_agent_id`` -> its config's assigned user);
  - the reviewer APPROVES (``/inbox/reviews/{id}/approve``) -> Task ``done``;
  - the reviewer REJECTS with feedback (``/inbox/reviews/{id}/reject``) -> Task
    back to ``backlog``, ``retry_count`` incremented, the ``feedback_text``
    recorded in the audit trail (``peer_review_verdict`` event), the reviewer
    ``reviewer_user_id`` + verdict auditable;
  - rejecting until ``max_retries`` is exhausted escalates the Task to
    ``blocked`` (§7.9);
  - tenant isolation: a tenant-A reviewer cannot rule on a tenant-B review
    (@pytest.mark.cross_tenant).

Fixture pattern mirrors test_human_task_submit.py.
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
_DECLINED = "declined"
_PEER = "peer_human_reviewer"


async def _seed(dsn: str) -> dict[str, UUID]:
    """One tenant with a worker (alice) + a peer reviewer (bob), plus a second
    tenant (carol/dave) for cross-tenant isolation.

    Tenant A: a ``peer_human_reviewer`` project; ``task_a`` is assigned to the
    worker Human Agent (alice) with ``reviewer_agent_id`` = the reviewer Human
    Agent (bob). Alice has an ACCEPTED assignment ready to submit.
    Tenant B: an analogous setup (carol worker, dave reviewer) for isolation.
    """
    tenant_a = uuid4()
    tenant_b = uuid4()
    alice = uuid4()  # A: worker / submitter
    bob = uuid4()  # A: peer reviewer
    carol = uuid4()  # B: worker
    dave = uuid4()  # B: peer reviewer

    project_a = uuid4()
    plan_a = uuid4()
    project_b = uuid4()

    worker_agent_a = uuid4()
    reviewer_agent_a = uuid4()
    worker_agent_b = uuid4()
    reviewer_agent_b = uuid4()

    task_a = uuid4()
    task_b = uuid4()

    asg_a = uuid4()  # alice's accepted work assignment
    asg_b = uuid4()  # carol's accepted work assignment

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
            "rev-peer-a",
            tenant_b,
            "Tenant B",
            "rev-peer-b",
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
            carol,
            "carol@b.test",
            "ph",
            dave,
            "dave@b.test",
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
            tenant_b,
            carol,
            "tenant_user",
            uuid4(),
            tenant_b,
            dave,
            "tenant_user",
        )
        # peer_human_reviewer projects.
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, human_task_review_mode) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8)",
            project_a,
            tenant_a,
            "Proyecto A",
            _PEER,
            project_b,
            tenant_b,
            "Proyecto B",
            _PEER,
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
            (worker_agent_a, tenant_a, alice),
            (reviewer_agent_a, tenant_a, bob),
            (worker_agent_b, tenant_b, carol),
            (reviewer_agent_b, tenant_b, dave),
        ):
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, agent_type, role, system_prompt,"
                " model_config, scope, is_template, project_id)"
                " VALUES ($1, $2, $3, 'human', 'reviewer', 'h', '{}'::jsonb,"
                " 'global_tenant_template', true, NULL)",
                agent_id,
                tenant_id,
                "Human Agent",
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
        # Tasks: in_progress (accepted) with the reviewer agent set. max_retries=2
        # so the escalation case needs only two rejects.
        for task_id, tenant_id, project_id, plan_id, worker_agent, reviewer_agent in (
            (task_a, tenant_a, project_a, plan_a, worker_agent_a, reviewer_agent_a),
            (task_b, tenant_b, project_b, None, worker_agent_b, reviewer_agent_b),
        ):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status,"
                " assigned_agent_id, reviewer_agent_id, max_retries)"
                " VALUES ($1, $2, $3, $4, $5, 'in_progress', $6, $7, 2)",
                task_id,
                tenant_id,
                project_id,
                plan_id,
                "Revisión legal",
                worker_agent,
                reviewer_agent,
            )
        for asg_id, tenant_id, task_id, agent_id, user_id in (
            (asg_a, tenant_a, task_a, worker_agent_a, alice),
            (asg_b, tenant_b, task_b, worker_agent_b, carol),
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
        "bob": bob,
        "carol": carol,
        "dave": dave,
        "reviewer_agent_a": reviewer_agent_a,
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


async def _task_row(dsn: str, task_id: UUID) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow("SELECT status, retry_count FROM tasks WHERE id = $1", task_id)
    finally:
        await conn.close()


async def _assignments(dsn: str, task_id: UUID) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return list(
            await conn.fetch(
                "SELECT id, human_agent_id, assigned_to_user_id, status"
                " FROM human_task_assignments WHERE task_id = $1 ORDER BY assigned_at",
                task_id,
            )
        )
    finally:
        await conn.close()


async def _audit_events(dsn: str, task_id: UUID, kind: str) -> list[dict]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT payload FROM task_audit_events WHERE task_id = $1 AND kind = $2 ORDER BY at",
            task_id,
            kind,
        )
        out = []
        for r in rows:
            p = r["payload"]
            out.append(json.loads(p) if isinstance(p, str) else dict(p))
        return out
    finally:
        await conn.close()


async def _submit_and_get_review_assignment(
    configured_app, migrations_pg_dsn: str, seeded: dict[str, UUID]
) -> str:
    """Alice submits her accepted task; assert it stays in_review with a fresh
    reviewer assignment for bob, and return that reviewer assignment id."""
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_a']}/complete",
            json={"output": "Borrador de revisión para validar."},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_status"] == "in_review"
    assert body["review_mode"] == _PEER
    assert body["review_assignment_id"]

    # The Task is in_review; a second (reviewer) assignment exists for bob.
    task = await _task_row(migrations_pg_dsn, seeded["task_a"])
    assert task["status"] == "in_review"
    assignments = await _assignments(migrations_pg_dsn, seeded["task_a"])
    assert len(assignments) == 2
    reviewer = next(a for a in assignments if str(a["id"]) == body["review_assignment_id"])
    assert reviewer["assigned_to_user_id"] == seeded["bob"]
    assert reviewer["human_agent_id"] == seeded["reviewer_agent_a"]
    assert reviewer["status"] == _PENDING
    return body["review_assignment_id"]


# ---------------------------------------------------------------------------
# peer_human_reviewer submit -> in_review + a reviewer assignment for another HA
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_routes_to_peer_reviewer(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    await _submit_and_get_review_assignment(configured_app, migrations_pg_dsn, seeded)


# ---------------------------------------------------------------------------
# The reviewer sees the review in their /inbox/reviews tray
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reviewer_sees_review_in_tray(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    review_id = await _submit_and_get_review_assignment(configured_app, migrations_pg_dsn, seeded)

    bob_token = await _mint_token(seeded["bob"], seeded["tenant_a"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/inbox/reviews", headers={"Authorization": f"Bearer {bob_token}"})
    assert resp.status_code == 200, resp.text
    reviews = resp.json()
    assert len(reviews) == 1
    assert reviews[0]["assignment_id"] == review_id
    assert reviews[0]["task_status"] == "in_review"
    assert "Borrador de revisión" in (reviews[0]["submitted_output"] or "")

    # Alice (the submitter) does NOT see it in her review tray.
    alice_token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/inbox/reviews", headers={"Authorization": f"Bearer {alice_token}"}
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Reviewer approves -> Task done, verdict auditable
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reviewer_approve_marks_done(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    review_id = await _submit_and_get_review_assignment(configured_app, migrations_pg_dsn, seeded)

    bob_token = await _mint_token(seeded["bob"], seeded["tenant_a"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/inbox/reviews/{review_id}/approve",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_status"] == "done"
    assert body["verdict"] == "approved"
    assert body["escalated"] is False

    assert (await _task_row(migrations_pg_dsn, seeded["task_a"]))["status"] == "done"
    verdicts = await _audit_events(migrations_pg_dsn, seeded["task_a"], "peer_review_verdict")
    assert any(
        v["verdict"] == "approved" and v["reviewer_user_id"] == str(seeded["bob"]) for v in verdicts
    )


# ---------------------------------------------------------------------------
# Reviewer rejects -> Task back to backlog, retry_count++, comments recorded
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reviewer_reject_returns_to_backlog_with_retry(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    review_id = await _submit_and_get_review_assignment(configured_app, migrations_pg_dsn, seeded)

    bob_token = await _mint_token(seeded["bob"], seeded["tenant_a"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/inbox/reviews/{review_id}/reject",
            json={"feedback_text": "Falta la cláusula 5 y el anexo de privacidad."},
            headers={"Authorization": f"Bearer {bob_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_status"] == "backlog"
    assert body["verdict"] == "rejected"
    assert body["retry_count"] == 1
    assert body["escalated"] is False

    task = await _task_row(migrations_pg_dsn, seeded["task_a"])
    assert task["status"] == "backlog"
    assert task["retry_count"] == 1

    # The reviewer assignment is declined; the feedback + reviewer are audited.
    assignments = await _assignments(migrations_pg_dsn, seeded["task_a"])
    reviewer = next(a for a in assignments if str(a["id"]) == review_id)
    assert reviewer["status"] == _DECLINED
    verdicts = await _audit_events(migrations_pg_dsn, seeded["task_a"], "peer_review_verdict")
    rejected = [v for v in verdicts if v["verdict"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["reviewer_user_id"] == str(seeded["bob"])
    assert "cláusula 5" in rejected[0]["feedback_text"]


# ---------------------------------------------------------------------------
# Reject without feedback_text -> 422, task untouched
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reject_requires_feedback(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    review_id = await _submit_and_get_review_assignment(configured_app, migrations_pg_dsn, seeded)

    bob_token = await _mint_token(seeded["bob"], seeded["tenant_a"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/inbox/reviews/{review_id}/reject",
            json={"feedback_text": "   "},
            headers={"Authorization": f"Bearer {bob_token}"},
        )
    assert resp.status_code == 422, resp.text
    assert (await _task_row(migrations_pg_dsn, seeded["task_a"]))["status"] == "in_review"


# ---------------------------------------------------------------------------
# Exhausting max_retries via repeated reject -> escalation to blocked (§7.9)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reject_until_max_retries_escalates_to_blocked(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    bob_token = await _mint_token(seeded["bob"], seeded["tenant_a"])
    alice_token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers_bob = {"Authorization": f"Bearer {bob_token}"}
    headers_alice = {"Authorization": f"Bearer {alice_token}"}

    # First submit -> first reject (retry_count 1, not escalated yet, max=2).
    review_id = await _submit_and_get_review_assignment(configured_app, migrations_pg_dsn, seeded)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/inbox/reviews/{review_id}/reject",
            json={"feedback_text": "Primer rechazo."},
            headers=headers_bob,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["escalated"] is False

        # The worker re-does the task: backlog -> ... is driven by the
        # orchestrator in prod; here we re-accept by directly re-submitting via
        # a fresh accepted assignment is not how it works — instead the worker
        # picks the task up again. We simulate the re-work cycle by creating a
        # new accepted work assignment + putting the task back in_progress, then
        # submitting again to trigger a second peer review + reject.
    await _reset_for_rework(migrations_pg_dsn, seeded)

    # Second submit -> second reject: retry_count hits max_retries (2) -> blocked.
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_a']}/complete",
            json={"output": "Segundo intento."},
            headers=headers_alice,
        )
        assert resp.status_code == 200, resp.text
        review_id2 = resp.json()["review_assignment_id"]
        assert review_id2

        resp = await client.post(
            f"/inbox/reviews/{review_id2}/reject",
            json={"feedback_text": "Segundo rechazo: sigue mal."},
            headers=headers_bob,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["retry_count"] == 2
        assert body["escalated"] is True
        assert body["task_status"] == "blocked"

    task = await _task_row(migrations_pg_dsn, seeded["task_a"])
    assert task["status"] == "blocked"
    assert task["retry_count"] == 2


async def _reset_for_rework(dsn: str, seeded: dict[str, UUID]) -> None:
    """Simulate the worker picking the rejected task back up: re-accept the
    original work assignment and move the task back to in_progress (what the
    orchestrator + inbox accept would do after a backlog -> ready -> assign
    cycle), so a second submit can drive another peer-review round."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE tasks SET status = 'in_progress' WHERE id = $1", seeded["task_a"]
        )
        await conn.execute(
            "UPDATE human_task_assignments SET status = 'accepted' WHERE id = $1",
            seeded["asg_a"],
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Cross-tenant isolation: a tenant-A reviewer cannot rule on a tenant-B review
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cannot_review_another_tenants_task(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)

    # Carol (tenant B) submits her task -> a reviewer assignment for dave (B).
    carol_token = await _mint_token(seeded["carol"], seeded["tenant_b"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/inbox/assignments/{seeded['asg_b']}/complete",
            json={"output": "B output"},
            headers={"Authorization": f"Bearer {carol_token}"},
        )
    assert resp.status_code == 200, resp.text
    review_id_b = resp.json()["review_assignment_id"]
    assert review_id_b

    # Bob (tenant A) cannot approve tenant B's review (RLS hides it -> 404).
    bob_token = await _mint_token(seeded["bob"], seeded["tenant_a"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/inbox/reviews/{review_id_b}/approve",
            headers={"Authorization": f"Bearer {bob_token}"},
        )
    assert resp.status_code == 404, resp.text
    # Tenant B's task is untouched (still in_review, not done).
    assert (await _task_row(migrations_pg_dsn, seeded["task_b"]))["status"] == "in_review"
