"""Integration tests for approval-request timeout (task_02_27).

A pending approval nobody answers must not hang the run forever:
`expire_stale_requests` times it out after a configurable window
(default 24 h), aborting its execution and blocking its task.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.approval_repo import expire_stale_requests
from api_server.db.domain import ApprovalRequest, Execution, Project, Task
from api_server.db.models import Organization
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_request(
    session: async_sessionmaker,
    *,
    requested_at: datetime,
    status: str = "pending",
) -> dict[str, UUID]:
    """Seed an org → project → task → execution → approval_request chain."""
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "task": uuid4(),
        "execution": uuid4(),
        "request": uuid4(),
    }
    async with session() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE approval_requests, executions, task_dependencies, tasks,"
                " projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Timeout tenant", slug="timeout-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Timeout project",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Timeout task",
                status="in_progress",
                priority="medium",
            )
        )
        await s.flush()
        s.add(
            Execution(
                id=ids["execution"],
                tenant_id=ids["tenant"],
                task_id=ids["task"],
                status="awaiting_human_approval",
            )
        )
        await s.flush()
        s.add(
            ApprovalRequest(
                id=ids["request"],
                tenant_id=ids["tenant"],
                execution_id=ids["execution"],
                task_id=ids["task"],
                project_id=ids["project"],
                category="production_deploy",
                status=status,
                requested_at=requested_at,
            )
        )
    return ids


@pytest.mark.asyncio
async def test_request_older_than_the_timeout_is_expired(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_request(sm, requested_at=_NOW - timedelta(hours=25))

        async with sm() as s, s.begin():
            expired = await expire_stale_requests(s, now=_NOW, timeout_hours=24)
        assert [r.id for r in expired] == [ids["request"]]

        async with sm() as s:
            request = await s.get(ApprovalRequest, ids["request"])
            execution = await s.get(Execution, ids["execution"])
            task = await s.get(Task, ids["task"])
        assert request is not None and request.status == "timed_out"
        assert request.resolved_at is not None
        # A decision nobody made must not leave the run hanging.
        assert execution is not None and execution.status == "aborted"
        assert execution.abort_code == "approval_timeout_exceeded"
        assert task is not None and task.status == "blocked"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_request_within_the_timeout_stays_pending(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_request(sm, requested_at=_NOW - timedelta(hours=23))

        async with sm() as s, s.begin():
            expired = await expire_stale_requests(s, now=_NOW, timeout_hours=24)
        assert expired == []

        async with sm() as s:
            request = await s.get(ApprovalRequest, ids["request"])
            execution = await s.get(Execution, ids["execution"])
        assert request is not None and request.status == "pending"
        assert execution is not None and execution.status == "awaiting_human_approval"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_timeout_window_is_configurable(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_request(sm, requested_at=_NOW - timedelta(hours=2))

        # 2 h old: safe under a 24 h window, expired under a 1 h one.
        async with sm() as s, s.begin():
            assert await expire_stale_requests(s, now=_NOW, timeout_hours=24) == []
        async with sm() as s, s.begin():
            expired = await expire_stale_requests(s, now=_NOW, timeout_hours=1)
        assert [r.id for r in expired] == [ids["request"]]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_already_resolved_request_is_not_expired(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_request(sm, requested_at=_NOW - timedelta(hours=99), status="approved")

        async with sm() as s, s.begin():
            expired = await expire_stale_requests(s, now=_NOW, timeout_hours=24)
        assert expired == []

        async with sm() as s:
            request = await s.get(ApprovalRequest, ids["request"])
        assert request is not None and request.status == "approved"
    finally:
        await engine.dispose()


def test_migration_0012_is_reversible(alembic_config: object) -> None:
    """downgrade to 0011 then back up to head must both succeed."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0011_platform_settings")
    command.upgrade(alembic_config, "head")
