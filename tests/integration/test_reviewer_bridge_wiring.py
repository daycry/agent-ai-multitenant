"""Integration test — prod-17 task_prod17_bridge_01.

`apply_reviewer_verdict` is the DB-side application of an AI reviewer's verdict.
prod-17 Fase A reconciles it with the real task state machine:

  - ``approve`` moves the task ``in_review → done`` (it used to be a no-op that
    left the task hanging);
  - ``reject`` with retries left → ``backlog`` + ``retry_count++`` + audit;
  - ``reject`` at ``max_retries`` → ``blocked`` (DB-legal escalation from
    ``in_review``; ``awaiting_human_approval`` is NOT reachable from there);
  - ``unknown`` → no-op (the caller re-prompts);
  - a task not in ``in_review`` → no-op guard (idempotent vs stale/duplicate
    verdicts);
  - the task is loaded with an explicit ``tenant_id`` predicate.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import TaskStatus
from api_server.reviewer_bridge import ReviewerVerdict, apply_reviewer_verdict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_task(
    dsn: str, *, status: str, retry_count: int = 0, max_retries: int = 3
) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_audit_events, executions, task_dependencies, tasks, plans,"
            " agents, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', 't-rev')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, worker_config)"
            " VALUES ($1, $2, 'P', 'active', '{}'::jsonb)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority,"
            " retry_count, max_retries)"
            " VALUES ($1, $2, $3, 't', $4, 'medium', $5, $6)",
            ids["task"],
            ids["tenant"],
            ids["project"],
            status,
            retry_count,
            max_retries,
        )
        return ids
    finally:
        await conn.close()


async def _run(dsn_admin: str, coro_factory):  # type: ignore[no-untyped-def]
    engine = create_async_engine(dsn_admin)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            return await coro_factory(session)
    finally:
        await engine.dispose()


async def _task_row(dsn: str, task_id: UUID) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, retry_count, completed_at FROM tasks WHERE id = $1", task_id
        )
        return dict(row)
    finally:
        await conn.close()


async def _audit_count(dsn: str, task_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM task_audit_events"
                " WHERE task_id = $1 AND kind = 'review_comment'",
                task_id,
            )
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_approve_moves_in_review_to_done(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed_task(migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value)

    result = await _run(
        admin_database_url,
        lambda s: apply_reviewer_verdict(
            s,
            task_id=ids["task"],
            tenant_id=ids["tenant"],
            verdict=ReviewerVerdict(label="approve"),
        ),
    )

    assert result["action"] == "approved"
    assert result["task_status"] == "done"
    row = await _task_row(migrations_pg_dsn, ids["task"])
    assert row["status"] == "done"
    assert row["completed_at"] is not None


@pytest.mark.asyncio
async def test_reject_with_retries_left_goes_to_backlog(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed_task(
        migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value, retry_count=0, max_retries=3
    )
    verdict = ReviewerVerdict(
        label="reject",
        failed_criterion="tests fail",
        testreport_evidence="2 failed",
        what_to_fix="fix the parser",
    )

    result = await _run(
        admin_database_url,
        lambda s: apply_reviewer_verdict(
            s, task_id=ids["task"], tenant_id=ids["tenant"], verdict=verdict
        ),
    )

    assert result["action"] == "rejected"
    assert result["task_status"] == "backlog"
    assert result["retry_count"] == 1
    row = await _task_row(migrations_pg_dsn, ids["task"])
    assert row["status"] == "backlog"
    assert row["retry_count"] == 1
    assert await _audit_count(migrations_pg_dsn, ids["task"]) == 1

    # P1-1 (investigación 2026-07-11): el rechazo deja LECCIÓN reutilizable —
    # una memoria semántica project_shared, determinista (sin LLM).
    import asyncpg as _asyncpg

    conn = await _asyncpg.connect(migrations_pg_dsn)
    try:
        mem = await conn.fetchrow(
            "SELECT content, scope, type FROM memory_entries"
            " WHERE tenant_id = $1 AND scope = 'project_shared'"
            " ORDER BY created_at DESC LIMIT 1",
            ids["tenant"],
        )
    finally:
        await conn.close()
    assert mem is not None
    assert "Review rechazó" in mem["content"]
    assert "fix the parser" in mem["content"]
    assert mem["type"] == "semantic"


@pytest.mark.asyncio
async def test_reject_at_max_retries_escalates_to_blocked(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    # retry_count 2, max 3 → this reject makes it 3 (>= max) → blocked.
    ids = await _seed_task(
        migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value, retry_count=2, max_retries=3
    )

    result = await _run(
        admin_database_url,
        lambda s: apply_reviewer_verdict(
            s,
            task_id=ids["task"],
            tenant_id=ids["tenant"],
            verdict=ReviewerVerdict(label="reject", what_to_fix="still broken"),
        ),
    )

    assert result["action"] == "escalated"
    assert result["task_status"] == "blocked"
    assert result["retry_count"] == 3
    row = await _task_row(migrations_pg_dsn, ids["task"])
    assert row["status"] == "blocked"


@pytest.mark.asyncio
async def test_unknown_is_noop(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed_task(migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value)

    result = await _run(
        admin_database_url,
        lambda s: apply_reviewer_verdict(
            s,
            task_id=ids["task"],
            tenant_id=ids["tenant"],
            verdict=ReviewerVerdict(label="unknown"),
        ),
    )

    assert result["action"] == "noop"
    row = await _task_row(migrations_pg_dsn, ids["task"])
    assert row["status"] == "in_review"


@pytest.mark.asyncio
async def test_verdict_on_non_in_review_task_is_guarded(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    # A stale/duplicate approve on an already-done task must not raise nor re-act.
    ids = await _seed_task(migrations_pg_dsn, status=TaskStatus.DONE.value)

    result = await _run(
        admin_database_url,
        lambda s: apply_reviewer_verdict(
            s,
            task_id=ids["task"],
            tenant_id=ids["tenant"],
            verdict=ReviewerVerdict(label="approve"),
        ),
    )

    assert result["action"] == "noop"
    assert result.get("note") == "not_in_review"
    row = await _task_row(migrations_pg_dsn, ids["task"])
    assert row["status"] == "done"


@pytest.mark.asyncio
async def test_cross_tenant_task_not_visible(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed_task(migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value)
    other_tenant = uuid4()

    with pytest.raises(ValueError, match="not visible"):
        await _run(
            admin_database_url,
            lambda s: apply_reviewer_verdict(
                s,
                task_id=ids["task"],
                tenant_id=other_tenant,
                verdict=ReviewerVerdict(label="approve"),
            ),
        )
