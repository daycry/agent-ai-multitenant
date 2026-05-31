"""Integration tests: acceptance-timeout escalation sweep (Plan 16 task_16_06).

When the orchestrator routes a ``ready`` human task (task_16_05) it creates a
``pending_acceptance`` ``HumanTaskAssignment`` and parks the Task in
``assigned_to_human``. The assigned User has up to the Human Agent's
``acceptance_timeout_hours`` to accept. This suite drives the REAL escalation
sweep (``api_server.human_agents.sweep_acceptance_timeouts``) against the REAL
Postgres (dev stack on PG 15432) — no mocks of the domain — and asserts:

  * a ``pending_acceptance`` assignment PAST the timeout reassigns to the Human
    Agent's ``escalation_target_user_id`` (a fresh pending row, the old one
    ``reassigned``, the task still ``assigned_to_human``) AND a
    ``human_task_assigned`` notice fires to the target;
  * a SECOND timeout on the escalation target's assignment blocks the task
    (``assigned_to_human -> blocked``, the row ``expired``) AND a ``task_blocked``
    notice fires carrying the tenant_id (so the dispatcher reaches the Tenant
    Admin);
  * a within-timeout assignment is UNTOUCHED;
  * the sweep is idempotent — re-running does NOT double-escalate;
  * the beat job is registered + reads its cadence from config;
  * tenant-scoped (``@pytest.mark.cross_tenant``): a tenant-A timed-out
    assignment escalates to tenant A's target only; a tenant-B assignment in the
    same pass is processed strictly on its own tenant's config + users.

The notifier is an in-memory capturing seam (no broker) so the sweep's
NOTIFY-after-commit contract is asserted directly. ``now`` is injected so the
deadline arithmetic is deterministic — no real sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from alembic import command
from api_server.db.domain import HumanTaskAssignment, Task
from api_server.human_agents import (
    EscalationNotice,
    EscalationOutcome,
    sweep_acceptance_timeouts,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Capturing notifier (no broker) — asserts the NOTIFY-after-commit contract.
# ---------------------------------------------------------------------------
@dataclass
class _CapturingNotifier:
    notices: list[EscalationNotice] = field(default_factory=list)

    def notify(self, notice: EscalationNotice) -> None:
        self.notices.append(notice)

    def of_type(self, event_type: str) -> list[EscalationNotice]:
        return [n for n in self.notices if n.event_type == event_type]


# ---------------------------------------------------------------------------
# Seeding (BYPASSRLS migrations role — the worker sweep runs BYPASSRLS too)
# ---------------------------------------------------------------------------
async def _seed_tenant(
    sm: async_sessionmaker,
    *,
    tenant_id: UUID,
    project_id: UUID,
    assignee_user_id: UUID,
    escalation_user_id: UUID | None,
    human_agent_id: UUID,
    acceptance_timeout_hours: int,
    slug: str,
) -> None:
    """A tenant + project + two users + a human Agent and its config."""
    async with sm() as s, s.begin():
        await s.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": f"Esc {slug}", "slug": f"esc-{slug}-{tenant_id.hex}"},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash)"
                " VALUES (:id, :email, 'argon2-placeholder')"
            ),
            {"id": assignee_user_id, "email": f"assignee-{assignee_user_id.hex}@esc.test"},
        )
        if escalation_user_id is not None:
            await s.execute(
                text(
                    "INSERT INTO users (id, email, password_hash)"
                    " VALUES (:id, :email, 'argon2-placeholder')"
                ),
                {"id": escalation_user_id, "email": f"escal-{escalation_user_id.hex}@esc.test"},
            )
        await s.execute(
            text(
                "INSERT INTO projects (id, tenant_id, name, status)"
                " VALUES (:id, :tid, :name, 'active')"
            ),
            {"id": project_id, "tid": tenant_id, "name": f"Project {slug}"},
        )
        await s.execute(
            text(
                "INSERT INTO agents (id, tenant_id, name, role, system_prompt, agent_type,"
                " scope, project_id)"
                " VALUES (:id, :tid, 'Legal Reviewer', 'reviewer', 'review', 'human',"
                " 'project_local', :pid)"
            ),
            {"id": human_agent_id, "tid": tenant_id, "pid": project_id},
        )
        await s.execute(
            text(
                "INSERT INTO human_agent_config"
                " (id, tenant_id, agent_id, assignment_mode, assigned_user_id,"
                "  escalation_target_user_id, acceptance_timeout_hours)"
                " VALUES (:id, :tid, :aid, 'specific_user', :uid, :eid, :to)"
            ),
            {
                "id": uuid7(),
                "tid": tenant_id,
                "aid": human_agent_id,
                "uid": assignee_user_id,
                "eid": escalation_user_id,
                "to": acceptance_timeout_hours,
            },
        )


async def _seed_assigned_human_task(
    sm: async_sessionmaker,
    *,
    tenant_id: UUID,
    project_id: UUID,
    human_agent_id: UUID,
    assignee_user_id: UUID,
    task_id: UUID,
    assignment_id: UUID,
    assigned_at: datetime,
) -> None:
    """A task parked in assigned_to_human + its pending_acceptance assignment."""
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority,"
                " assigned_agent_id)"
                " VALUES (:id, :tid, :pid, 'Legal review', 'assigned_to_human', 'high', :aid)"
            ),
            {"id": task_id, "tid": tenant_id, "pid": project_id, "aid": human_agent_id},
        )
        await s.execute(
            text(
                "INSERT INTO human_task_assignments"
                " (id, tenant_id, task_id, human_agent_id, assigned_to_user_id, assigned_at,"
                "  status)"
                " VALUES (:id, :tid, :task, :agent, :user, :at, 'pending_acceptance')"
            ),
            {
                "id": assignment_id,
                "tid": tenant_id,
                "task": task_id,
                "agent": human_agent_id,
                "user": assignee_user_id,
                "at": assigned_at,
            },
        )


def _engine_sm(url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _task_status(sm: async_sessionmaker, task_id: UUID) -> str:
    async with sm() as s:
        return (await s.execute(select(Task.status).where(Task.id == task_id))).scalar_one()


async def _assignments(sm: async_sessionmaker, task_id: UUID) -> list[HumanTaskAssignment]:
    async with sm() as s:
        return list(
            (
                await s.execute(
                    select(HumanTaskAssignment)
                    .where(HumanTaskAssignment.task_id == task_id)
                    .order_by(HumanTaskAssignment.assigned_at)
                )
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


# ===========================================================================
# A pending assignment PAST the timeout reassigns to the escalation target.
# ===========================================================================
@pytest.mark.asyncio
async def test_timed_out_assignment_escalates_to_target(
    _migrated: None, admin_database_url: str
) -> None:
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "assignee", "escal", "agent", "task", "asg")}
    now = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    try:
        await _seed_tenant(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            assignee_user_id=ids["assignee"],
            escalation_user_id=ids["escal"],
            human_agent_id=ids["agent"],
            acceptance_timeout_hours=1,
            slug="a",
        )
        # Assigned 90 minutes ago — past the 1h window.
        await _seed_assigned_human_task(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            human_agent_id=ids["agent"],
            assignee_user_id=ids["assignee"],
            task_id=ids["task"],
            assignment_id=ids["asg"],
            assigned_at=now - timedelta(minutes=90),
        )

        notifier = _CapturingNotifier()
        result = await sweep_acceptance_timeouts(sm, notifier=notifier, now=now)

        # Assert on THIS task's outcome — the sweep is global (it processes
        # every tenant's timed-out rows), so leftover rows from sibling tests
        # in the same session must not make the test flaky. The session does
        # not TRUNCATE between tests (unique uuids), so scope by task_id.
        ours = [r for r in result.rows if r.task_id == ids["task"]]
        assert len(ours) == 1
        assert ours[0].outcome is EscalationOutcome.ESCALATED

        # The original assignment is superseded; a fresh pending one names the
        # escalation target with a reset clock; the task stays assigned_to_human.
        rows = await _assignments(sm, ids["task"])
        assert len(rows) == 2
        original = next(r for r in rows if r.id == ids["asg"])
        fresh = next(r for r in rows if r.id != ids["asg"])
        assert original.status == "reassigned"
        assert fresh.status == "pending_acceptance"
        assert fresh.assigned_to_user_id == ids["escal"]
        assert fresh.tenant_id == ids["tenant"]
        # Clock reset — the target gets a full window, not the original time.
        assert fresh.assigned_at.replace(tzinfo=UTC) == now
        assert await _task_status(sm, ids["task"]) == "assigned_to_human"

        # The escalation target was notified (scope by task_id — the sweep is
        # global so sibling-test rows may also notify into this notifier).
        assigned_notices = [
            n
            for n in notifier.of_type("human_task_assigned")
            if n.context["task_id"] == str(ids["task"])
        ]
        assert len(assigned_notices) == 1
        notice = assigned_notices[0]
        assert notice.tenant_id == str(ids["tenant"])
        assert notice.context["assigned_to_user_id"] == str(ids["escal"])
        assert notice.context["escalated"] is True
    finally:
        await engine.dispose()


# ===========================================================================
# A second timeout on the escalation target -> task blocked + Tenant Admin.
# ===========================================================================
@pytest.mark.asyncio
async def test_second_timeout_blocks_task_and_notifies_admin(
    _migrated: None, admin_database_url: str
) -> None:
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "assignee", "escal", "agent", "task", "asg")}
    now = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    try:
        await _seed_tenant(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            assignee_user_id=ids["assignee"],
            escalation_user_id=ids["escal"],
            human_agent_id=ids["agent"],
            acceptance_timeout_hours=1,
            slug="b",
        )
        await _seed_assigned_human_task(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            human_agent_id=ids["agent"],
            assignee_user_id=ids["assignee"],
            task_id=ids["task"],
            assignment_id=ids["asg"],
            assigned_at=now - timedelta(minutes=90),
        )

        notifier = _CapturingNotifier()
        # First sweep escalates to the target.
        await sweep_acceptance_timeouts(sm, notifier=notifier, now=now)
        # The escalation target ALSO fails to accept: advance past a second
        # window and sweep again.
        later = now + timedelta(minutes=90)
        notifier2 = _CapturingNotifier()
        result = await sweep_acceptance_timeouts(sm, notifier=notifier2, now=later)

        # Scope to THIS task — the sweep is global (sibling-test rows may also
        # age out in the same pass), so assert on our own outcome row.
        ours = [r for r in result.rows if r.task_id == ids["task"]]
        assert len(ours) == 1
        assert ours[0].outcome is EscalationOutcome.BLOCKED

        # The task is blocked; the target's assignment is expired; NO third
        # assignment was created.
        assert await _task_status(sm, ids["task"]) == "blocked"
        rows = await _assignments(sm, ids["task"])
        statuses = sorted(r.status for r in rows)
        assert statuses == ["expired", "reassigned"]

        # The Tenant Admin alert carries the tenant_id (the dispatcher fans it
        # out to that tenant's admins) and the block reason.
        blocked_notices = [
            n for n in notifier2.of_type("task_blocked") if n.context["task_id"] == str(ids["task"])
        ]
        assert len(blocked_notices) == 1
        block = blocked_notices[0]
        assert block.tenant_id == str(ids["tenant"])
        assert "escalation" in block.context["reason"]
    finally:
        await engine.dispose()


# ===========================================================================
# No escalation target -> first timeout blocks straight away.
# ===========================================================================
@pytest.mark.asyncio
async def test_no_target_blocks_on_first_timeout(_migrated: None, admin_database_url: str) -> None:
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "assignee", "agent", "task", "asg")}
    now = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    try:
        await _seed_tenant(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            assignee_user_id=ids["assignee"],
            escalation_user_id=None,  # nobody to escalate to
            human_agent_id=ids["agent"],
            acceptance_timeout_hours=1,
            slug="c",
        )
        await _seed_assigned_human_task(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            human_agent_id=ids["agent"],
            assignee_user_id=ids["assignee"],
            task_id=ids["task"],
            assignment_id=ids["asg"],
            assigned_at=now - timedelta(minutes=90),
        )

        notifier = _CapturingNotifier()
        result = await sweep_acceptance_timeouts(sm, notifier=notifier, now=now)

        ours = [r for r in result.rows if r.task_id == ids["task"]]
        assert len(ours) == 1
        assert ours[0].outcome is EscalationOutcome.BLOCKED
        assert await _task_status(sm, ids["task"]) == "blocked"
        block = next(
            n for n in notifier.of_type("task_blocked") if n.context["task_id"] == str(ids["task"])
        )
        assert "no escalation target" in block.context["reason"]
    finally:
        await engine.dispose()


# ===========================================================================
# A within-timeout assignment is UNTOUCHED.
# ===========================================================================
@pytest.mark.asyncio
async def test_within_timeout_assignment_untouched(
    _migrated: None, admin_database_url: str
) -> None:
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "assignee", "escal", "agent", "task", "asg")}
    now = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    try:
        await _seed_tenant(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            assignee_user_id=ids["assignee"],
            escalation_user_id=ids["escal"],
            human_agent_id=ids["agent"],
            acceptance_timeout_hours=24,
            slug="d",
        )
        # Assigned 30 minutes ago — well within the 24h window.
        await _seed_assigned_human_task(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            human_agent_id=ids["agent"],
            assignee_user_id=ids["assignee"],
            task_id=ids["task"],
            assignment_id=ids["asg"],
            assigned_at=now - timedelta(minutes=30),
        )

        notifier = _CapturingNotifier()
        result = await sweep_acceptance_timeouts(sm, notifier=notifier, now=now)

        # Our within-window task is NOT in the sweep's outcome rows at all.
        assert [r for r in result.rows if r.task_id == ids["task"]] == []
        # No notice references our task.
        assert [n for n in notifier.notices if n.context.get("task_id") == str(ids["task"])] == []
        # The assignment + task are exactly as seeded.
        rows = await _assignments(sm, ids["task"])
        assert len(rows) == 1
        assert rows[0].status == "pending_acceptance"
        assert await _task_status(sm, ids["task"]) == "assigned_to_human"
    finally:
        await engine.dispose()


# ===========================================================================
# Idempotent: re-running the sweep does NOT double-escalate.
# ===========================================================================
@pytest.mark.asyncio
async def test_sweep_is_idempotent(_migrated: None, admin_database_url: str) -> None:
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "assignee", "escal", "agent", "task", "asg")}
    now = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    try:
        await _seed_tenant(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            assignee_user_id=ids["assignee"],
            escalation_user_id=ids["escal"],
            human_agent_id=ids["agent"],
            acceptance_timeout_hours=1,
            slug="e",
        )
        await _seed_assigned_human_task(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            human_agent_id=ids["agent"],
            assignee_user_id=ids["assignee"],
            task_id=ids["task"],
            assignment_id=ids["asg"],
            assigned_at=now - timedelta(minutes=90),
        )

        # First pass escalates.
        first = await sweep_acceptance_timeouts(sm, notifier=_CapturingNotifier(), now=now)
        first_row = next(r for r in first.rows if r.task_id == ids["task"])
        assert first_row.outcome is EscalationOutcome.ESCALATED

        # IMMEDIATELY re-run at the SAME `now`: the original is no longer
        # pending and the fresh escalation row has a reset clock (assigned_at ==
        # now, not yet past its own window) — our task is untouched on re-run.
        notifier2 = _CapturingNotifier()
        second = await sweep_acceptance_timeouts(sm, notifier=notifier2, now=now)
        assert [r for r in second.rows if r.task_id == ids["task"]] == []
        assert [n for n in notifier2.notices if n.context.get("task_id") == str(ids["task"])] == []

        # Still exactly two assignment rows (no third one).
        rows = await _assignments(sm, ids["task"])
        assert len(rows) == 2
        assert await _task_status(sm, ids["task"]) == "assigned_to_human"
    finally:
        await engine.dispose()


# ===========================================================================
# The beat job is registered + reads its cadence from config.
# ===========================================================================
def test_beat_job_registered_with_configurable_cadence() -> None:
    """The escalation sweep is wired into the beat schedule on its configurable
    cadence (default every 10 minutes) — not a hardcoded magic schedule."""
    from celery.schedules import crontab
    from workers.beat_schedule import HUMAN_ESCALATION_BEAT_ENTRY, build_beat_schedule
    from workers.config import Settings

    # Default cadence: */10 -> minute 0,10,20,30,40,50.
    sched = build_beat_schedule(Settings())
    assert HUMAN_ESCALATION_BEAT_ENTRY in sched
    entry = sched[HUMAN_ESCALATION_BEAT_ENTRY]
    assert entry["task"] == "workers.escalate_human_assignments"
    cron = entry["schedule"]
    assert isinstance(cron, crontab)
    assert sorted(cron.minute) == [0, 10, 20, 30, 40, 50]

    # A custom cadence flows through from config (operator-tunable, not magic).
    custom = build_beat_schedule(Settings(human_escalation_cron="*/5 * * * *"))
    assert sorted(custom[HUMAN_ESCALATION_BEAT_ENTRY]["schedule"].minute) == [
        0,
        5,
        10,
        15,
        20,
        25,
        30,
        35,
        40,
        45,
        50,
        55,
    ]

    # The Celery task is registered under its public name once its module is
    # imported (the real worker boots it via build_celery_app's `imports`).
    import workers.human_escalation  # noqa: F401
    from workers.celery_app import app

    assert "workers.escalate_human_assignments" in app.tasks
    # And the worker lists it among the modules it imports on boot.
    assert "workers.human_escalation" in app.conf.imports


# ===========================================================================
# Cross-tenant: a sweep pass processes each assignment strictly on its OWN
# tenant's config + users; tenant A never escalates onto tenant B.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_escalation_is_tenant_scoped(_migrated: None, admin_database_url: str) -> None:
    """Two tenants both have a timed-out assignment in the SAME sweep pass.

    The sweep resolves each assignment's Human Agent config + escalation target
    with an explicit ``tenant_id`` predicate keyed on the assignment's OWN
    tenant, so tenant A's task escalates to tenant A's target and tenant B's to
    tenant B's — never crossing. The BYPASSRLS worker cannot lean on RLS, so
    this proves the explicit scoping holds."""
    engine, sm = _engine_sm(admin_database_url)
    a = {k: uuid7() for k in ("tenant", "project", "assignee", "escal", "agent", "task", "asg")}
    b = {k: uuid7() for k in ("tenant", "project", "assignee", "escal", "agent", "task", "asg")}
    now = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    try:
        for label, ten in (("a", a), ("b", b)):
            await _seed_tenant(
                sm,
                tenant_id=ten["tenant"],
                project_id=ten["project"],
                assignee_user_id=ten["assignee"],
                escalation_user_id=ten["escal"],
                human_agent_id=ten["agent"],
                acceptance_timeout_hours=1,
                slug=label,
            )
            await _seed_assigned_human_task(
                sm,
                tenant_id=ten["tenant"],
                project_id=ten["project"],
                human_agent_id=ten["agent"],
                assignee_user_id=ten["assignee"],
                task_id=ten["task"],
                assignment_id=ten["asg"],
                assigned_at=now - timedelta(minutes=90),
            )

        notifier = _CapturingNotifier()
        result = await sweep_acceptance_timeouts(sm, notifier=notifier, now=now)

        # Both of OUR tasks escalated (the sweep is global; scope by task_id).
        ours = {r.task_id: r for r in result.rows if r.task_id in (a["task"], b["task"])}
        assert len(ours) == 2
        assert all(r.outcome is EscalationOutcome.ESCALATED for r in ours.values())

        # Each tenant's task escalated to ITS OWN target — never crossed.
        a_rows = await _assignments(sm, a["task"])
        a_fresh = next(r for r in a_rows if r.status == "pending_acceptance")
        assert a_fresh.assigned_to_user_id == a["escal"]
        assert a_fresh.tenant_id == a["tenant"]

        b_rows = await _assignments(sm, b["task"])
        b_fresh = next(r for r in b_rows if r.status == "pending_acceptance")
        assert b_fresh.assigned_to_user_id == b["escal"]
        assert b_fresh.tenant_id == b["tenant"]

        # The outcome rows confirm the per-tenant escalation targets.
        by_tenant = {row.tenant_id: row for row in result.rows}
        assert by_tenant[a["tenant"]].outcome is EscalationOutcome.ESCALATED
        assert by_tenant[a["tenant"]].reassigned_to_user_id == a["escal"]
        assert by_tenant[b["tenant"]].reassigned_to_user_id == b["escal"]

        # No notice points a tenant's task at the other tenant's user.
        for notice in notifier.of_type("human_task_assigned"):
            if notice.context["task_id"] == str(a["task"]):
                assert notice.context["assigned_to_user_id"] == str(a["escal"])
            elif notice.context["task_id"] == str(b["task"]):
                assert notice.context["assigned_to_user_id"] == str(b["escal"])
    finally:
        await engine.dispose()
