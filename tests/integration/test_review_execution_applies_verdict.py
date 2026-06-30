"""Integration test — prod-17 task_prod17_loop_03.

When a REVIEW execution finishes, the worker (`_apply_review_verdict`) parses the
reviewer's stdout and applies the verdict to the reviewed task: approve → done,
reject → backlog (or blocked at max_retries), and an UNPARSEABLE verdict →
defensive reject (so the task converges rather than stalling in ``in_review``).
Returns the ``(task, old, new)`` tuple the worker publishes.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import TaskStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.execution import _apply_review_verdict, _RuntimeResult

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


def _result(output: str) -> _RuntimeResult:
    return _RuntimeResult(
        status="done", abort_code=None, output=output, iterations=1, steps=[], usage={}
    )


async def _seed(dsn: str, *, status: str, retry_count: int = 0, max_retries: int = 3) -> dict:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_audit_events, executions, task_dependencies, tasks, plans,"
            " agents, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', 't-rev3')", ids["tenant"]
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


async def _apply(admin_url: str, ids: dict, output: str):  # type: ignore[no-untyped-def]
    engine = create_async_engine(admin_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            return await _apply_review_verdict(session, ids["task"], ids["tenant"], _result(output))
    finally:
        await engine.dispose()


async def _status(dsn: str, task_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return str(await conn.fetchval("SELECT status FROM tasks WHERE id = $1", task_id))
    finally:
        await conn.close()


async def _retry_count(dsn: str, task_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(await conn.fetchval("SELECT retry_count FROM tasks WHERE id = $1", task_id))
    finally:
        await conn.close()


async def _apply_result(admin_url: str, ids: dict, result: object):  # type: ignore[no-untyped-def]
    engine = create_async_engine(admin_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            return await _apply_review_verdict(session, ids["task"], ids["tenant"], result)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approve_output_moves_task_to_done(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed(migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value)
    event = await _apply(admin_database_url, ids, "Looks good.\n<verdict>approve</verdict>")
    assert event is not None
    _task, old, new = event
    assert old == "in_review"
    assert new == "done"
    assert await _status(migrations_pg_dsn, ids["task"]) == "done"


@pytest.mark.asyncio
async def test_reject_output_moves_task_to_backlog(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed(migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value)
    out = (
        "<verdict>reject</verdict>\n<rejection><failed_criterion>tests</failed_criterion>"
        "<what_to_fix>add a test</what_to_fix></rejection>"
    )
    event = await _apply(admin_database_url, ids, out)
    assert event is not None
    assert event[2] == "backlog"
    assert await _status(migrations_pg_dsn, ids["task"]) == "backlog"


@pytest.mark.asyncio
async def test_unparseable_output_is_defensive_reject(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    # No <verdict> tag on a CLEANLY-FINISHED (done) review → unknown → defensive
    # reject → backlog (the reviewer ran but didn't format; the task converges).
    ids = await _seed(migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value)
    event = await _apply(admin_database_url, ids, "I am not sure what to do here.")
    assert event is not None
    assert event[2] == "backlog"
    assert await _status(migrations_pg_dsn, ids["task"]) == "backlog"


def _infra_fail() -> _RuntimeResult:
    return _RuntimeResult(
        status="aborted",
        abort_code="max_iterations_exceeded",
        output="Execution aborted (max_iterations_exceeded).",
        iterations=50,
        steps=[],
        usage={},
    )


@pytest.mark.asyncio
async def test_reviewer_infra_failure_below_cap_stays_in_review(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    # Audit C1 (F03/P0.2): an infra-level review failure (status != done, no verdict)
    # must NOT reject the task (output is an error string, not a judgement). BELOW the
    # cap it stays in_review for the reconciler to re-dispatch — but ADR 0095 D3 now
    # bumps retry_count so the loop is bounded (no infinite in_review ↔ re-dispatch).
    ids = await _seed(migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value, max_retries=3)
    event = await _apply_result(admin_database_url, ids, _infra_fail())
    assert event is None
    assert await _status(migrations_pg_dsn, ids["task"]) == "in_review"
    assert await _retry_count(migrations_pg_dsn, ids["task"]) == 1


@pytest.mark.asyncio
async def test_reviewer_infra_failure_at_cap_escalates_to_blocked(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    # ADR 0095 D3: after max_retries non-convergent reviews, escalate to a human
    # (blocked) instead of looping forever. NOT a defensive reject (no re-implement).
    ids = await _seed(
        migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value, retry_count=2, max_retries=3
    )
    event = await _apply_result(admin_database_url, ids, _infra_fail())
    assert event is not None
    assert event[2] == "blocked"
    assert await _status(migrations_pg_dsn, ids["task"]) == "blocked"
    assert await _retry_count(migrations_pg_dsn, ids["task"]) == 3


@pytest.mark.asyncio
async def test_reject_at_max_retries_escalates_to_blocked(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed(
        migrations_pg_dsn, status=TaskStatus.IN_REVIEW.value, retry_count=2, max_retries=3
    )
    event = await _apply(admin_database_url, ids, "<verdict>reject</verdict>")
    assert event is not None
    assert event[2] == "blocked"
    assert await _status(migrations_pg_dsn, ids["task"]) == "blocked"


@pytest.mark.asyncio
async def test_task_not_in_review_is_noop(
    _migrated: None, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    ids = await _seed(migrations_pg_dsn, status=TaskStatus.DONE.value)
    event = await _apply(admin_database_url, ids, "<verdict>approve</verdict>")
    assert event is None
    assert await _status(migrations_pg_dsn, ids["task"]) == "done"
