"""End-to-end smoke test of the agent execution pipeline (task_02_34).

The capstone of Plan 02 Fase G: one task travels the whole pipeline —

    task event → orchestrator consumer → TaskDispatcher (assignment) →
    Celery enqueue → workers.run_execution → agent-runtime container →
    LangGraph loop → streamed steps → executions row + Redis stream

every component wired together, not mocked. The only seam the test
plays itself is the Celery worker process: it reads the message the
dispatcher enqueued and invokes `run_execution` with that exact
payload — the same call a worker daemon would make.

Needs the full local stack: Docker, PostgreSQL and Redis.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Agent, ExecutionStatus, Project, Task
from api_server.db.execution_repo import list_executions_for_task
from api_server.db.models import Organization
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ._docker_helpers import requires_docker
from ._pipeline_helpers import (
    TEST_REDIS_URL,
    consume_and_take_job,
    require_agent_runtime_image,
    run_worker_job,
    status_changed_event,
    task_status,
)

# M-6: timeout por-test — un contenedor colgado muere acotado, no al timeout del job.
pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

# The agent's ModelClient spec: act once, then finish — a full loop
# with a tool call, a finish and a passing self-review.
_AGENT_MODEL = {
    "kind": "scripted",
    "decisions": [
        {"kind": "act", "tool": "echo", "tool_args": {"text": "draft"}},
        {"kind": "finish", "output": "a poem about the sea"},
    ],
}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def _agent_runtime_image() -> None:
    require_agent_runtime_image()


async def _seed(db_url: str) -> dict[str, UUID]:
    """Insert a tenant / project / agent / ready task — the pipeline's input."""
    ids = {"tenant": uuid4(), "project": uuid4(), "agent": uuid4(), "task": uuid4()}
    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await s.execute(
                text(
                    "TRUNCATE approval_requests, executions, task_dependencies, tasks,"
                    " agents, projects, organizations RESTART IDENTITY CASCADE"
                )
            )
            s.add(Organization(id=ids["tenant"], name="E2E tenant", slug="e2e-tenant"))
            await s.flush()
            s.add(
                Project(
                    id=ids["project"],
                    tenant_id=ids["tenant"],
                    name="E2E project",
                    status="active",
                    is_template=False,
                    worker_config={"assignment_policy": "load_balanced"},
                )
            )
            await s.flush()
            s.add(
                Agent(
                    id=ids["agent"],
                    tenant_id=ids["tenant"],
                    name="Writer",
                    role="backend-dev",
                    system_prompt="You write poems.",
                    agent_type="ai",
                    scope="project_local",
                    project_id=ids["project"],
                    model_config=_AGENT_MODEL,
                )
            )
            await s.flush()
            s.add(
                Task(
                    id=ids["task"],
                    tenant_id=ids["tenant"],
                    project_id=ids["project"],
                    title="Write a sea poem",
                    description="end-to-end smoke",
                    status="ready",
                    priority="medium",
                )
            )
        return ids
    finally:
        await engine.dispose()


async def _dispatch_via_orchestrator(db_url: str, ids: dict[str, UUID]) -> dict[str, Any]:
    """Publish a task event, run the orchestrator consumer once, and
    return the execution request the dispatcher enqueued for the worker."""
    stream = f"test:e2e:{uuid4().hex[:8]}"
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        request = await consume_and_take_job(
            db_url,
            events_stream=stream,
            seed_event=status_changed_event(
                ids, old="backlog", new="ready", occurred_at="2026-05-22T00:00:00+00:00"
            ),
        )
        await redis.delete(stream)
        return request
    finally:
        await redis.aclose()


_task_status = task_status


async def _execution(db_url: str, task_id: UUID) -> dict[str, Any]:
    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            rows = await list_executions_for_task(s, task_id)
            row = rows[0]
            return {
                "id": str(row.id),
                "status": row.status,
                "output": row.output,
                "steps_log": list(row.steps_log),
                "total_tokens": row.total_tokens,
                "agent_id": row.agent_id,
            }
    finally:
        await engine.dispose()


async def _exec_stream_event_types(execution_id: str) -> list[str]:
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        entries = await redis.xrange(f"exec:{execution_id}")
        return [fields["type"] for _id, fields in entries]
    finally:
        await redis.aclose()


@requires_docker
def test_full_pipeline_dispatches_runs_and_persists_an_execution(
    _migrated: None,
    _agent_runtime_image: None,
    admin_database_url: str,
) -> None:
    """One task end to end — orchestrator → worker → container → DB.

    Sync, like Celery calls `run_execution`: the task owns its own
    event loop, so the test must not run inside one.
    """
    ids = asyncio.run(_seed(admin_database_url))

    # --- orchestrator: event → assignment → enqueue ------------------------
    request = asyncio.run(_dispatch_via_orchestrator(admin_database_url, ids))
    assert request["task_id"] == str(ids["task"])
    assert request["agent_id"] == str(ids["agent"])
    # The dispatcher moved the task out of `ready`.
    assert asyncio.run(_task_status(admin_database_url, ids["task"])) == "in_progress"

    # --- worker: run the enqueued job (container → loop → DB) --------------
    outcome = run_worker_job(request, admin_database_url)

    assert outcome["status"] == ExecutionStatus.DONE

    # --- the persisted execution row --------------------------------------
    execution = asyncio.run(_execution(admin_database_url, ids["task"]))
    assert execution["id"] == outcome["execution_id"]
    assert execution["status"] == ExecutionStatus.DONE
    assert execution["output"] == "a poem about the sea"
    assert execution["agent_id"] == ids["agent"]
    # The steps_log captured the loop: model calls + the echo tool call.
    kinds = {step["kind"] for step in execution["steps_log"]}
    assert "model_call" in kinds
    assert "tool_call" in kinds
    assert execution["total_tokens"] >= 0

    # --- the live per-execution stream ------------------------------------
    event_types = asyncio.run(_exec_stream_event_types(outcome["execution_id"]))
    assert event_types[0] == "execution.started"
    assert event_types[-1] == "execution.finished"
    assert "step" in event_types
