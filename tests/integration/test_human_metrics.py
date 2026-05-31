"""Integration tests for personal human-task metrics + history (Plan 16 task_16_10).

Exercises ``GET /inbox/metrics`` and ``GET /inbox/history`` against the REAL
Postgres (dev stack on PG 15432) through the FastAPI app under RLS (app_user,
NOBYPASSRLS), so every assertion is the production code path:

  - mean acceptance time = mean(updated_at - assigned_at) over the user's
    ``accepted`` assignments;
  - mean execution time = mean(end_at - start_at) over the user's CLOSED work
    sessions;
  - first-try approval rate = share of the user's worked tasks delivered in a
    SINGLE work session (a task with two sessions = a redo after rejection);
  - mean logged hours = mean of the non-NULL ``hours_logged``;
  - empty history -> well-defined zeros for the counts + ``None`` for every
    mean / rate (you cannot average over zero rows);
  - the metrics + history are strictly PER-USER and tenant-scoped: Alice's
    numbers never include Bob's (same tenant) or Carol's (other tenant)
    (@pytest.mark.cross_tenant).

The seed sets ``assigned_at`` / ``updated_at`` on assignments and
``start_at`` / ``end_at`` on work sessions to fixed offsets so the means are
deterministic (no reliance on wall-clock timing). Seeding goes through the
BYPASSRLS migrations role; the app is driven via AsyncClient under app_user RLS.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

# Fixed clock so acceptance/execution windows are exact, not wall-clock.
_BASE = datetime(2026, 5, 1, 9, 0, 0, tzinfo=UTC)


async def _seed(dsn: str) -> dict[str, UUID]:
    """Two tenants with deterministic human-task history.

    Tenant A:
      * ``alice`` — the user under test:
          - two ACCEPTED assignments with acceptance gaps of 1 h and 3 h
            (mean acceptance = 2 h = 7200 s);
          - one PENDING assignment (NOT accepted -> excluded from acceptance);
          - three CLOSED work sessions with durations 2 h, 4 h and 6 h
            (mean execution = 4 h = 14400 s);
          - the sessions span TWO distinct tasks: task_one has ONE session
            (first-try), task_two has TWO sessions (a redo) -> first-try rate
            = 1/2 = 0.5;
          - logged hours 2.00 and 4.00 (one session leaves hours NULL) ->
            mean logged hours = 3.0.
      * ``bob`` — another A user with his OWN session/assignment (must NOT leak
        into Alice's per-user metrics).
      * ``dave`` — an A user with ZERO history (the empty-history case).
    Tenant B:
      * ``carol`` — a B user with history that must never reach Alice
        (cross-tenant).
    """
    tenant_a = uuid4()
    tenant_b = uuid4()
    alice = uuid4()
    bob = uuid4()
    dave = uuid4()
    carol = uuid4()

    project_a = uuid4()
    plan_a = uuid4()
    project_b = uuid4()

    agent_a = uuid4()
    agent_b = uuid4()

    task_one = uuid4()  # alice: one session (first-try)
    task_two = uuid4()  # alice: two sessions (redo -> not first-try)
    task_pending = uuid4()  # alice: pending assignment
    task_bob = uuid4()  # bob's task
    task_b = uuid4()  # carol's task (tenant B)

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
            "metrics-a",
            tenant_b,
            "Tenant B",
            "metrics-b",
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
            dave,
            "dave@a.test",
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
            dave,
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
        # Tasks. Closed-session tasks are `done`; the pending one is
        # `assigned_to_human`. Statuses are immaterial to the metrics (the
        # numbers come from sessions/assignments) but kept realistic.
        for task_id, tenant_id, project_id, plan_id, title, st in (
            (task_one, tenant_a, project_a, plan_a, "Revisión legal", "done"),
            (task_two, tenant_a, project_a, plan_a, "Decisión de marca", "done"),
            (task_pending, tenant_a, project_a, plan_a, "Firma cliente", "assigned_to_human"),
            (task_bob, tenant_a, project_a, None, "Tarea de Bob", "done"),
            (task_b, tenant_b, project_b, None, "Tarea de Carol", "done"),
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

        # Assignments. (assigned_at, updated_at) drives the acceptance gap.
        #   alice task_one : assigned_at BASE, accepted +1h
        #   alice task_two : assigned_at BASE, accepted +3h   -> mean = 2h
        #   alice pending  : pending_acceptance (excluded)
        #   bob            : accepted +10h (must NOT count for alice)
        #   carol (B)      : accepted +20h (cross-tenant)
        assignment_rows = [
            (
                uuid4(),
                tenant_a,
                task_one,
                agent_a,
                alice,
                "accepted",
                _BASE,
                _BASE + timedelta(hours=1),
            ),
            (
                uuid4(),
                tenant_a,
                task_two,
                agent_a,
                alice,
                "accepted",
                _BASE,
                _BASE + timedelta(hours=3),
            ),
            (uuid4(), tenant_a, task_pending, agent_a, alice, "pending_acceptance", _BASE, _BASE),
            (
                uuid4(),
                tenant_a,
                task_bob,
                agent_a,
                bob,
                "accepted",
                _BASE,
                _BASE + timedelta(hours=10),
            ),
            (
                uuid4(),
                tenant_b,
                task_b,
                agent_b,
                carol,
                "accepted",
                _BASE,
                _BASE + timedelta(hours=20),
            ),
        ]
        for asg_id, tenant_id, task_id, agent_id, user_id, st, assigned, updated in assignment_rows:
            await conn.execute(
                "INSERT INTO human_task_assignments (id, tenant_id, task_id, human_agent_id,"
                " assigned_to_user_id, status, assigned_at, updated_at)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                asg_id,
                tenant_id,
                task_id,
                agent_id,
                user_id,
                st,
                assigned,
                updated,
            )

        # Work sessions. (start_at, end_at, hours_logged):
        #   alice task_one : 2h closed, 2.00 h logged   (single session)
        #   alice task_two : 4h closed, 4.00 h logged   (first of two)
        #   alice task_two : 6h closed, hours NULL      (second of two -> redo)
        #     -> exec durations 2h,4h,6h => mean = 4h = 14400 s
        #     -> distinct tasks worked = 2; first-try (1 session) = task_one
        #        => first-try rate = 1/2 = 0.5
        #     -> logged hours 2.00 + 4.00 (one NULL) => mean = 3.0
        #   bob            : a closed session (must NOT count for alice)
        #   carol (B)      : a closed session (cross-tenant)
        ws_rows = [
            (
                uuid4(),
                tenant_a,
                task_one,
                alice,
                _BASE,
                _BASE + timedelta(hours=2),
                "2.00",
                "Revisión OK",
            ),
            (
                uuid4(),
                tenant_a,
                task_two,
                alice,
                _BASE,
                _BASE + timedelta(hours=4),
                "4.00",
                "Primer intento",
            ),
            (
                uuid4(),
                tenant_a,
                task_two,
                alice,
                _BASE + timedelta(hours=5),
                _BASE + timedelta(hours=11),
                None,
                "Reintento tras rechazo",
            ),
            (uuid4(), tenant_a, task_bob, bob, _BASE, _BASE + timedelta(hours=8), "8.00", "Bob"),
            (uuid4(), tenant_b, task_b, carol, _BASE, _BASE + timedelta(hours=9), "9.00", "Carol"),
        ]
        for ws_id, tenant_id, task_id, user_id, start_at, end_at, hours, comments in ws_rows:
            await conn.execute(
                "INSERT INTO human_work_sessions (id, tenant_id, task_id, user_id, start_at,"
                " end_at, hours_logged, comments, output_files_attached)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, '[]'::jsonb)",
                ws_id,
                tenant_id,
                task_id,
                user_id,
                start_at,
                end_at,
                hours,
                comments,
            )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "alice": alice,
        "bob": bob,
        "dave": dave,
        "carol": carol,
        "task_one": task_one,
        "task_two": task_two,
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


# ---------------------------------------------------------------------------
# Metrics computed correctly from the seeded sessions/assignments
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_metrics_computed_from_sessions_and_assignments(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/inbox/metrics", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Two distinct worked tasks (task_one + task_two); three closed sessions.
    assert body["tasks_worked"] == 2
    assert body["work_sessions_completed"] == 3
    # Two accepted assignments (the pending one is excluded).
    assert body["assignments_accepted"] == 2

    # Acceptance gaps 1h + 3h -> mean 2h = 7200 s.
    assert body["mean_acceptance_time_seconds"] == pytest.approx(7200.0)
    # Execution durations 2h + 4h + 6h -> mean 4h = 14400 s.
    assert body["mean_execution_time_seconds"] == pytest.approx(14400.0)
    # task_one (1 session) first-try; task_two (2 sessions) not -> 1/2 = 0.5.
    assert body["first_try_approval_rate"] == pytest.approx(0.5)
    # Logged hours 2.00 + 4.00 (one NULL) -> mean 3.0.
    assert body["mean_hours_logged"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Empty history -> well-defined zeros / None
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_history_metrics_are_zero_and_none(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["dave"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/inbox/metrics", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["tasks_worked"] == 0
    assert body["work_sessions_completed"] == 0
    assert body["assignments_accepted"] == 0
    assert body["mean_acceptance_time_seconds"] is None
    assert body["mean_execution_time_seconds"] is None
    assert body["first_try_approval_rate"] is None
    assert body["mean_hours_logged"] is None


# ---------------------------------------------------------------------------
# History lists the caller's own closed sessions, newest first
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_history_lists_own_closed_sessions(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/inbox/history", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()

    # Alice's three closed sessions; none of Bob's / Carol's.
    assert len(rows) == 3
    task_ids = {r["task_id"] for r in rows}
    assert task_ids == {str(seeded["task_one"]), str(seeded["task_two"])}
    # Newest first: the last session ends at +11h (the redo of task_two).
    assert rows[0]["task_id"] == str(seeded["task_two"])
    # Every row carries its project context + a closed window.
    for r in rows:
        assert r["project_name"] == "Proyecto A"
        assert r["end_at"] is not None


# ---------------------------------------------------------------------------
# Per-user: Alice's metrics never include Bob's (same tenant)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_metrics_are_per_user_within_tenant(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["bob"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/inbox/metrics", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Bob has exactly ONE accepted assignment (+10h) and ONE 8h session.
    assert body["tasks_worked"] == 1
    assert body["work_sessions_completed"] == 1
    assert body["assignments_accepted"] == 1
    assert body["mean_acceptance_time_seconds"] == pytest.approx(36000.0)  # 10 h
    assert body["mean_execution_time_seconds"] == pytest.approx(28800.0)  # 8 h
    assert body["first_try_approval_rate"] == pytest.approx(1.0)
    assert body["mean_hours_logged"] == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Cross-tenant isolation: Alice never sees Carol's tenant-B history
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_metrics_and_history_are_tenant_scoped(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["alice"], seeded["tenant_a"])
    headers_a = {"Authorization": f"Bearer {token_a}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        metrics = await client.get("/inbox/metrics", headers=headers_a)
        history = await client.get("/inbox/history", headers=headers_a)

    # Carol's tenant-B work (20h acceptance, 9h session) never reaches Alice:
    # her numbers are exactly the tenant-A ones from the first test.
    mbody = metrics.json()
    assert mbody["assignments_accepted"] == 2
    assert mbody["mean_acceptance_time_seconds"] == pytest.approx(7200.0)
    assert mbody["work_sessions_completed"] == 3

    # And Carol's tenant-B task is absent from Alice's history.
    hids = {r["task_id"] for r in history.json()}
    assert all(tid in {str(seeded["task_one"]), str(seeded["task_two"])} for tid in hids)
