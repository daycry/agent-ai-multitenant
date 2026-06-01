"""Integration tests for the Human-Agents assistant tools (Plan 16 task_16_14).

The personal assistant gains two READ tools over the Human-Agents data:

  * ``tenant_human_workload`` — "how many tasks does user X have this week?":
    the count of that user's OPEN (pending_acceptance + accepted)
    :class:`HumanTaskAssignment` rows plus the :class:`HumanWorkSession` rows
    they STARTED this ISO week. The user is named by id / email / name and
    resolved ONLY among the asking admin's tenant members.
  * ``tenant_human_assignments_pending`` — "which human tasks are unaccepted
    for > N hours?": the tenant's ``pending_acceptance`` assignments older than
    the threshold (default 24h).

Binding constraints under test (mirroring test_personal_assistant.py):

  * Both tools run on the caller's tenant-scoped RLS session, so they NEVER
    surface another tenant's rows (``@pytest.mark.cross_tenant``).
  * The user reference in ``tenant_human_workload`` resolves only within the
    asking admin's tenant — a user that exists ONLY in another tenant is
    ``not_found`` (RBAC: never probe out-of-tenant users).

These exercise the tools directly under an ``open_tenant_session`` (the
source-of-truth layer) against the REAL migrated DB — the same pattern the
existing assistant isolation test uses.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    """Two tenants with Human-Agents data.

    Tenant A: admin_a + two human users (alice, bob). A human Agent assigned to
    alice. Alice has 2 OPEN assignments (1 pending_acceptance OLD, 1 accepted)
    + 1 closed (declined) that must NOT count, and 2 work sessions started this
    week. Bob has 1 fresh pending assignment (NOT old enough). Tenant B: admin_b
    + a human user (carol) with her own old pending assignment, so cross-tenant
    isolation has something to leak (and must not).
    """
    now = datetime.now(UTC)
    ids: dict[str, UUID] = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "admin_b": uuid4(),
        "alice": uuid4(),
        "bob": uuid4(),
        "carol": uuid4(),
        "project_a": uuid4(),
        "project_b": uuid4(),
        "plan_a": uuid4(),
        "plan_b": uuid4(),
        "agent_a": uuid4(),
        "agent_b": uuid4(),
        "task_a1": uuid4(),
        "task_a2": uuid4(),
        "task_a3": uuid4(),
        "task_b1": uuid4(),
        "assign_a_old": uuid4(),
        "assign_a_accepted": uuid4(),
        "assign_a_declined": uuid4(),
        "assign_bob_fresh": uuid4(),
        "assign_b_old": uuid4(),
    }

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE human_task_assignments, human_work_sessions, human_agent_config,"
            " tasks, plans, projects, agents, tenant_settings, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, personal_assistant_enabled) VALUES"
            " ($1, $2, $3, true), ($4, $5, $6, true), ($7, $8, $9, false)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-ht",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-ht",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-ht",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, full_name) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8), ($9, $10, $11, $12),"
            " ($13, $14, $15, $16), ($17, $18, $19, $20)",
            ids["admin_a"],
            "admin-a@ht.test",
            "ph",
            "Admin A",
            ids["admin_b"],
            "admin-b@ht.test",
            "ph",
            "Admin B",
            ids["alice"],
            "alice@ht.test",
            "ph",
            "Alice Human",
            ids["bob"],
            "bob@ht.test",
            "ph",
            "Bob Human",
            ids["carol"],
            "carol@ht.test",
            "ph",
            "Carol Secret",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8), ($9, $10, $11, $12),"
            " ($13, $14, $15, $16), ($17, $18, $19, $20)",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            "tenant_admin",
            uuid4(),
            ids["tenant_a"],
            ids["alice"],
            "tenant_user",
            uuid4(),
            ids["tenant_a"],
            ids["bob"],
            "tenant_user",
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
            "tenant_admin",
            uuid4(),
            ids["tenant_b"],
            ids["carol"],
            "tenant_user",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES"
            " ($1, $2, $3, $4), ($5, $6, $7, $8)",
            ids["project_a"],
            ids["tenant_a"],
            "Project A",
            "active",
            ids["project_b"],
            ids["tenant_b"],
            "Project B (secret)",
            "active",
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, created_by) VALUES"
            " ($1, $2, $3, $4, $5, $6), ($7, $8, $9, $10, $11, $12)",
            ids["plan_a"],
            ids["tenant_a"],
            ids["project_a"],
            "Plan A",
            "approved",
            ids["admin_a"],
            ids["plan_b"],
            ids["tenant_b"],
            ids["project_b"],
            "Plan B secret",
            "approved",
            ids["admin_b"],
        )
        # Human agents (agent_type='human') in each tenant.
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, agent_type, scope,"
            " project_id) VALUES"
            " ($1, $2, $3, $4, $5, 'human', 'project_local', $6),"
            " ($7, $8, $9, $10, $11, 'human', 'project_local', $12)",
            ids["agent_a"],
            ids["tenant_a"],
            "Legal Reviewer A",
            "reviewer",
            "review",
            ids["project_a"],
            ids["agent_b"],
            ids["tenant_b"],
            "Legal Reviewer B",
            "reviewer",
            "review",
            ids["project_b"],
        )
        await conn.execute(
            "INSERT INTO human_agent_config (id, tenant_id, agent_id, assignment_mode,"
            " assigned_user_id) VALUES"
            " ($1, $2, $3, 'specific_user', $4), ($5, $6, $7, 'specific_user', $8)",
            uuid4(),
            ids["tenant_a"],
            ids["agent_a"],
            ids["alice"],
            uuid4(),
            ids["tenant_b"],
            ids["agent_b"],
            ids["carol"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status) VALUES"
            " ($1, $2, $3, $4, $5, $6), ($7, $8, $9, $10, $11, $12),"
            " ($13, $14, $15, $16, $17, $18), ($19, $20, $21, $22, $23, $24)",
            ids["task_a1"],
            ids["tenant_a"],
            ids["project_a"],
            ids["plan_a"],
            "Legal review of contract",
            "assigned_to_human",
            ids["task_a2"],
            ids["tenant_a"],
            ids["project_a"],
            ids["plan_a"],
            "Sign off release",
            "in_progress",
            ids["task_a3"],
            ids["tenant_a"],
            ids["project_a"],
            ids["plan_a"],
            "Bob fresh task",
            "assigned_to_human",
            ids["task_b1"],
            ids["tenant_b"],
            ids["project_b"],
            ids["plan_b"],
            "Carol secret review",
            "assigned_to_human",
        )
        # --- Assignments ---
        # Alice: an OLD pending_acceptance (48h), an accepted, and a declined
        # (closed -> must not count as open).
        old_ts = now - timedelta(hours=48)
        recent_ts = now - timedelta(hours=2)
        await conn.execute(
            "INSERT INTO human_task_assignments (id, tenant_id, task_id, human_agent_id,"
            " assigned_to_user_id, assigned_at, status) VALUES"
            " ($1, $2, $3, $4, $5, $6, 'pending_acceptance'),"
            " ($7, $8, $9, $10, $11, $12, 'accepted'),"
            " ($13, $14, $15, $16, $17, $18, 'declined')",
            ids["assign_a_old"],
            ids["tenant_a"],
            ids["task_a1"],
            ids["agent_a"],
            ids["alice"],
            old_ts,
            ids["assign_a_accepted"],
            ids["tenant_a"],
            ids["task_a2"],
            ids["agent_a"],
            ids["alice"],
            recent_ts,
            ids["assign_a_declined"],
            ids["tenant_a"],
            ids["task_a2"],
            ids["agent_a"],
            ids["alice"],
            old_ts,
        )
        # Bob: a FRESH pending (1h old) -> open for workload, NOT old enough for
        # the >24h pending list.
        await conn.execute(
            "INSERT INTO human_task_assignments (id, tenant_id, task_id, human_agent_id,"
            " assigned_to_user_id, assigned_at, status) VALUES"
            " ($1, $2, $3, $4, $5, $6, 'pending_acceptance')",
            ids["assign_bob_fresh"],
            ids["tenant_a"],
            ids["task_a3"],
            ids["agent_a"],
            ids["bob"],
            now - timedelta(hours=1),
        )
        # Tenant B: Carol an OLD pending (72h) — must never surface to A.
        await conn.execute(
            "INSERT INTO human_task_assignments (id, tenant_id, task_id, human_agent_id,"
            " assigned_to_user_id, assigned_at, status) VALUES"
            " ($1, $2, $3, $4, $5, $6, 'pending_acceptance')",
            ids["assign_b_old"],
            ids["tenant_b"],
            ids["task_b1"],
            ids["agent_b"],
            ids["carol"],
            now - timedelta(hours=72),
        )
        # --- Work sessions for Alice: 2 started THIS week on two tasks. ---
        await conn.execute(
            "INSERT INTO human_work_sessions (id, tenant_id, task_id, user_id, start_at) VALUES"
            " ($1, $2, $3, $4, $5), ($6, $7, $8, $9, $10)",
            uuid4(),
            ids["tenant_a"],
            ids["task_a1"],
            ids["alice"],
            now - timedelta(hours=1),
            uuid4(),
            ids["tenant_a"],
            ids["task_a2"],
            ids["alice"],
            now - timedelta(hours=3),
        )
    finally:
        await conn.close()
    return ids


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def migrated_db(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Migrate to head + wire env so ``open_tenant_session`` connects as the
    RLS-bound app user (the same bootstrap the assistant isolation test uses)."""
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    try:
        yield
    finally:
        reset_engine_cache()
        get_settings.cache_clear()


def _ctx_for(seeded: dict[str, UUID], tenant_key: str, admin_key: str):
    from api_server.assistant.tools import AssistantToolContext
    from api_server.auth.deps import AuthPrincipal, open_tenant_session

    principal = AuthPrincipal(
        user_id=seeded[admin_key],
        session_id=uuid4(),
        tenant_id=seeded[tenant_key],
    )
    session_cm = open_tenant_session(principal)

    def make_ctx(session):
        return AssistantToolContext(
            session=session,
            tenant_id=seeded[tenant_key],
            user_id=seeded[admin_key],
        )

    return session_cm, make_ctx


# ===========================================================================
# tenant_human_workload
# ===========================================================================
@pytest.mark.asyncio
async def test_human_workload_counts_open_assignments_and_week_sessions(
    migrated_db, migrations_pg_dsn: str
) -> None:
    """Alice has 2 OPEN assignments (1 pending, 1 accepted) — the declined one
    does NOT count — and 2 work sessions started this week on 2 tasks."""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import run_assistant_tool
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()
    session_cm, make_ctx = _ctx_for(seeded, "tenant_a", "admin_a")
    async with session_cm as session:
        ctx = make_ctx(session)
        result = await run_assistant_tool("tenant_human_workload", ctx, {"user": "alice@ht.test"})

    assert result["resolved"] is True
    assert result["user"]["id"] == str(seeded["alice"])
    assert result["open_assignments"] == 2
    assert result["open_assignments_by_status"] == {
        "pending_acceptance": 1,
        "accepted": 1,
    }
    assert result["work_sessions_this_week"] == 2
    assert result["tasks_worked_this_week"] == 2


@pytest.mark.asyncio
async def test_human_workload_resolves_by_name(migrated_db, migrations_pg_dsn: str) -> None:
    """The user can be named by full name (the natural "how many tasks does
    Fulano have?" phrasing), not just email."""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import run_assistant_tool
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()
    session_cm, make_ctx = _ctx_for(seeded, "tenant_a", "admin_a")
    async with session_cm as session:
        ctx = make_ctx(session)
        result = await run_assistant_tool("tenant_human_workload", ctx, {"user": "Alice Human"})

    assert result["resolved"] is True
    assert result["user"]["id"] == str(seeded["alice"])
    assert result["open_assignments"] == 2


@pytest.mark.asyncio
async def test_human_workload_unknown_user_is_not_found(
    migrated_db, migrations_pg_dsn: str
) -> None:
    """A name that matches nobody in the tenant returns a typed not_found."""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import run_assistant_tool
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()
    session_cm, make_ctx = _ctx_for(seeded, "tenant_a", "admin_a")
    async with session_cm as session:
        ctx = make_ctx(session)
        result = await run_assistant_tool(
            "tenant_human_workload", ctx, {"user": "nobody@nowhere.test"}
        )

    assert result["resolved"] is False
    assert result["reason"] == "not_found"
    assert result["matches"] == []


# ===========================================================================
# tenant_human_assignments_pending
# ===========================================================================
@pytest.mark.asyncio
async def test_pending_lists_only_overdue_assignments(migrated_db, migrations_pg_dsn: str) -> None:
    """Default 24h threshold: Alice's 48h pending shows; Bob's 1h fresh
    pending does NOT; the accepted/declined ones never show."""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import run_assistant_tool
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()
    session_cm, make_ctx = _ctx_for(seeded, "tenant_a", "admin_a")
    async with session_cm as session:
        ctx = make_ctx(session)
        result = await run_assistant_tool("tenant_human_assignments_pending", ctx)

    assert result["older_than_hours"] == 24
    assert result["count"] == 1
    item = result["assignments"][0]
    assert item["assignment_id"] == str(seeded["assign_a_old"])
    assert item["task_title"] == "Legal review of contract"
    assert item["assigned_to_email"] == "alice@ht.test"
    assert item["pending_hours"] is not None and item["pending_hours"] >= 47


@pytest.mark.asyncio
async def test_pending_threshold_can_be_widened(migrated_db, migrations_pg_dsn: str) -> None:
    """Lowering the threshold to 0h surfaces Bob's fresh pending too (both of
    A's open pendings), ordered oldest-first."""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import run_assistant_tool
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()
    session_cm, make_ctx = _ctx_for(seeded, "tenant_a", "admin_a")
    async with session_cm as session:
        ctx = make_ctx(session)
        result = await run_assistant_tool(
            "tenant_human_assignments_pending", ctx, {"older_than_hours": 0}
        )

    assert result["count"] == 2
    # Oldest first: Alice's 48h then Bob's 1h.
    assert result["assignments"][0]["assignment_id"] == str(seeded["assign_a_old"])
    assert result["assignments"][1]["assignment_id"] == str(seeded["assign_bob_fresh"])


# ===========================================================================
# Tenant isolation + RBAC
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tools_are_tenant_scoped(migrated_db, migrations_pg_dsn: str) -> None:
    """Tenant A's admin can NEVER see tenant B's (Carol's) old pending
    assignment, and resolving Carol — a user that exists ONLY in tenant B —
    yields not_found (RBAC: never probe out-of-tenant users)."""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import run_assistant_tool
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()
    session_cm, make_ctx = _ctx_for(seeded, "tenant_a", "admin_a")
    async with session_cm as session:
        ctx = make_ctx(session)
        pending = await run_assistant_tool(
            "tenant_human_assignments_pending", ctx, {"older_than_hours": 0}
        )
        carol = await run_assistant_tool("tenant_human_workload", ctx, {"user": "carol@ht.test"})

    # B's old pending never appears in A's list.
    a_ids = {a["assignment_id"] for a in pending["assignments"]}
    assert str(seeded["assign_b_old"]) not in a_ids
    # Resolving a B-only user from A's context finds nobody.
    assert carol["resolved"] is False
    assert carol["reason"] == "not_found"
    assert carol["matches"] == []


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_other_tenant_sees_only_its_own(migrated_db, migrations_pg_dsn: str) -> None:
    """Symmetric check: tenant B's admin sees Carol's old pending and resolves
    Carol's workload, but never A's assignments."""
    seeded = await _seed(migrations_pg_dsn)

    from api_server.assistant.tools import run_assistant_tool
    from api_server.db.session import reset_engine_cache

    reset_engine_cache()
    session_cm, make_ctx = _ctx_for(seeded, "tenant_b", "admin_b")
    async with session_cm as session:
        ctx = make_ctx(session)
        pending = await run_assistant_tool(
            "tenant_human_assignments_pending", ctx, {"older_than_hours": 0}
        )
        carol = await run_assistant_tool("tenant_human_workload", ctx, {"user": "carol@ht.test"})

    b_ids = {a["assignment_id"] for a in pending["assignments"]}
    assert b_ids == {str(seeded["assign_b_old"])}
    assert str(seeded["assign_a_old"]) not in b_ids
    assert carol["resolved"] is True
    assert carol["user"]["id"] == str(seeded["carol"])
    assert carol["open_assignments"] == 1
