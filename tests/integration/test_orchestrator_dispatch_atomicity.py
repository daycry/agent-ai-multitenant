"""Integration tests: orchestrator dispatch atomicity + consumer DLQ.

Plan 06.14 task_06_14_05 closes two robustness gaps audited on the
orchestrator (workers-orchestrator-8 and -4):

Problem A — dispatch.py (workers-orchestrator-8): ``_dispatch()`` commits
the task → ``in_progress`` + ``assigned_agent_id`` transaction BEFORE
``handle()`` enqueues ``workers.run_execution``. If the broker enqueue
raises (broker down) the task is committed ``in_progress`` yet never
enqueued — an orphan no worker will ever pick up. The fix reverts the task
to ``ready`` (clearing the assignment) in a fresh transaction so the next
dispatch trigger re-enqueues it.

Problem B — consumer.py (workers-orchestrator-4): ``StreamConsumer._dispatch``
counted a raising handler as ``failed`` and ACKed it, losing the event
forever. The fix XADDs the failed event onto ``dlq:orchestrator_events``
BEFORE the caller ACKs, so the failure is observable and replayable.

Reuses the harness style of ``test_orchestrator_dispatch.py`` (``_seed`` /
``_dispatcher`` against ``admin_database_url`` + the test Redis DB) and
``test_orchestrator.py`` (``StreamConsumer`` against a throwaway stream).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from api_server.db.domain import Agent, Project, Task
from api_server.db.models import Organization
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.consumer import DEAD_LETTER_STREAM, StreamConsumer
from orchestrator.dispatch import TaskDispatcher
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

TEST_REDIS_URL = "redis://localhost:6379/15"

# A one-step scripted model spec carried on the agent (the dispatcher
# forwards it verbatim — irrelevant here since the enqueue is stubbed).
_SCRIPTED_FINISH = {
    "kind": "scripted",
    "decisions": [{"kind": "finish", "output": "the sea poem"}],
}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


# ---------------------------------------------------------------------------
# Seed / harness — mirrors test_orchestrator_dispatch.py
# ---------------------------------------------------------------------------
async def _seed(sm: async_sessionmaker, *, task_status: str = "ready") -> dict[str, UUID]:
    """Insert a tenant / project / agent / task; return their ids."""
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "agent": uuid4(),
        "task": uuid4(),
    }
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, agents, projects,"
                " organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Atomicity tenant", slug="atomicity-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Atomicity project",
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
                system_prompt="You write things.",
                agent_type="ai",
                scope="project_local",
                project_id=ids["project"],
                model_config=_SCRIPTED_FINISH,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Write a sea poem",
                description="exercise the dispatch pipeline",
                status=task_status,
                priority="medium",
            )
        )
    return ids


def _ready_event(ids: dict[str, UUID], *, new_status: str = "ready") -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(ids["tenant"]),
        project_id=str(ids["project"]),
        task_id=str(ids["task"]),
        occurred_at="2026-05-29T00:00:00+00:00",
        payload={"old_status": "backlog", "new_status": new_status},
    )


class _BrokerDownCelery:
    """A Celery stand-in whose send_task always raises — simulates a broker
    outage at enqueue time. Records how many times it was called so the test
    can assert the enqueue was attempted."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.calls = 0
        self._exc = exc or ConnectionError("broker down")

    def send_task(self, *args: object, **kwargs: object) -> None:
        self.calls += 1
        raise self._exc


def _dispatcher(sm: async_sessionmaker, celery_app: object) -> TaskDispatcher:
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,  # type: ignore[arg-type]
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


# ===========================================================================
# Problem A — dispatch enqueue failure reverts the task to `ready`
# ===========================================================================
@pytest.mark.asyncio
async def test_enqueue_failure_reverts_task_to_ready(
    _migrated: None, admin_database_url: str
) -> None:
    """Broker down at enqueue → the task must NOT stay `in_progress` orphaned;
    it reverts to `ready` with the assignment cleared so it re-dispatches."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)
        celery = _BrokerDownCelery()

        await _dispatcher(sm, celery).handle(_ready_event(ids))

        # The enqueue was attempted (so the failure path was exercised)…
        assert celery.calls == 1
        # …and the task was reverted, not left dangling.
        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "ready"
        assert task.assigned_agent_id is None
        assert task.started_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_successful_enqueue_keeps_task_in_progress(
    _migrated: None, admin_database_url: str
) -> None:
    """The happy path is unchanged: a successful enqueue leaves the task
    `in_progress` with its assignee — the revert is failure-only."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        class _OkCelery:
            def __init__(self) -> None:
                self.calls = 0

            def send_task(self, *args: object, **kwargs: object) -> None:
                self.calls += 1

        celery = _OkCelery()
        await _dispatcher(sm, celery).handle(_ready_event(ids))

        assert celery.calls == 1
        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "in_progress"
        assert task.assigned_agent_id == ids["agent"]
        assert task.started_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reverted_task_can_be_redispatched(_migrated: None, admin_database_url: str) -> None:
    """After a revert the task is genuinely re-dispatchable: a second
    `handle()` with a healthy broker assigns it again and enqueues once."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        # First dispatch fails at enqueue → reverts to ready.
        await _dispatcher(sm, _BrokerDownCelery()).handle(_ready_event(ids))
        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "ready"

        # Broker recovers: a re-dispatch now succeeds.
        class _OkCelery:
            def __init__(self) -> None:
                self.calls = 0

            def send_task(self, *args: object, **kwargs: object) -> None:
                self.calls += 1

        ok = _OkCelery()
        await _dispatcher(sm, ok).handle(_ready_event(ids))

        assert ok.calls == 1
        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "in_progress"
        assert task.assigned_agent_id == ids["agent"]
    finally:
        await engine.dispose()


# ===========================================================================
# Problem B — a raising handler dead-letters the event before the ACK
# ===========================================================================
def _consumer_settings(stream: str) -> OrchestratorSettings:
    return OrchestratorSettings(
        redis_url=TEST_REDIS_URL,
        events_stream=stream,
        consumer_group="orchestrator",
        consumer_name="test-atomicity-consumer",
        block_ms=100,
    )


def _event_fields(**overrides: str) -> dict[str, str]:
    fields = {
        "type": EVENT_TASK_STATUS_CHANGED,
        "tenant_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": '{"old_status": "backlog", "new_status": "ready"}',
    }
    fields.update(overrides)
    return fields


@pytest_asyncio.fixture()
async def redis_client() -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture()
async def stream(redis_client: Redis) -> AsyncIterator[str]:
    name = f"test:events:{uuid.uuid4().hex[:12]}"
    yield name
    await redis_client.delete(name)


@pytest_asyncio.fixture()
async def dlq_clean(redis_client: Redis) -> AsyncIterator[None]:
    """Start each DLQ test from an empty dead-letter stream and tidy up."""
    await redis_client.delete(DEAD_LETTER_STREAM)
    yield
    await redis_client.delete(DEAD_LETTER_STREAM)


@pytest.mark.asyncio
async def test_failed_handler_dead_letters_the_event(
    redis_client: Redis, stream: str, dlq_clean: None
) -> None:
    """A handler that raises must land the event on `dlq:orchestrator_events`
    (entry id, type, task_id, tenant_id, error) before it is ACKed."""

    async def boom(_event: TaskEvent) -> None:
        raise RuntimeError("dispatch exploded")

    settings = _consumer_settings(stream)
    consumer = StreamConsumer(redis_client, settings, handler=boom)
    await consumer.ensure_group()

    fields = _event_fields()
    await redis_client.xadd(stream, fields)
    result = await consumer.consume_once()

    # Counted failed and still ACKed (poison message can't wedge the group).
    assert result.failed == 1
    assert consumer.stats.failed == 1
    pending = await redis_client.xpending(stream, settings.consumer_group)
    assert pending["pending"] == 0

    # The event is preserved on the dead-letter stream with full context.
    entries = await redis_client.xrange(DEAD_LETTER_STREAM, "-", "+")
    assert len(entries) == 1
    _dlq_id, dlq_fields = entries[0]
    assert dlq_fields["type"] == fields["type"]
    assert dlq_fields["task_id"] == fields["task_id"]
    assert dlq_fields["tenant_id"] == fields["tenant_id"]
    assert "RuntimeError: dispatch exploded" in dlq_fields["error"]
    assert "failed_at_unix" in dlq_fields


@pytest.mark.asyncio
async def test_successful_handler_does_not_dead_letter(
    redis_client: Redis, stream: str, dlq_clean: None
) -> None:
    """The happy path must not touch the DLQ — only failures are recorded."""
    seen: list[TaskEvent] = []

    async def ok(event: TaskEvent) -> None:
        seen.append(event)

    consumer = StreamConsumer(redis_client, _consumer_settings(stream), handler=ok)
    await consumer.ensure_group()

    await redis_client.xadd(stream, _event_fields())
    result = await consumer.consume_once()

    assert result.processed == 1
    assert result.failed == 0
    assert len(seen) == 1
    assert await redis_client.xlen(DEAD_LETTER_STREAM) == 0


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_dead_letter_records_the_failing_tenant_not_a_neighbour(
    redis_client: Redis, stream: str, dlq_clean: None
) -> None:
    """Cross-tenant: when tenant A's event fails, the DLQ entry must carry
    A's tenant_id only — a second tenant's healthy event is processed and
    never bleeds into A's dead-letter record."""
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    failing_task = str(uuid.uuid4())

    async def fail_only_a(event: TaskEvent) -> None:
        if event.tenant_id == tenant_a:
            raise RuntimeError("tenant A dispatch failed")

    consumer = StreamConsumer(redis_client, _consumer_settings(stream), handler=fail_only_a)
    await consumer.ensure_group()

    # B's event is healthy; A's raises.
    await redis_client.xadd(stream, _event_fields(tenant_id=tenant_b))
    await redis_client.xadd(stream, _event_fields(tenant_id=tenant_a, task_id=failing_task))

    result = await consumer.consume_once()
    assert result.processed == 1
    assert result.failed == 1

    entries = await redis_client.xrange(DEAD_LETTER_STREAM, "-", "+")
    # Exactly A's event is dead-lettered — B's never reaches the DLQ.
    assert len(entries) == 1
    _dlq_id, dlq_fields = entries[0]
    assert dlq_fields["tenant_id"] == tenant_a
    assert dlq_fields["tenant_id"] != tenant_b
    assert dlq_fields["task_id"] == failing_task


@pytest.mark.asyncio
async def test_malformed_entry_is_not_dead_lettered(
    redis_client: Redis, stream: str, dlq_clean: None
) -> None:
    """A malformed (unparseable) entry is counted `malformed` and ACKed but
    is NOT routed to the DLQ — the dead-letter path is for handler failures
    on well-formed events, not parse errors (which have no TaskEvent)."""
    consumer = StreamConsumer(redis_client, _consumer_settings(stream))
    await consumer.ensure_group()

    # Missing required fields → MalformedEventError.
    await redis_client.xadd(stream, {"type": EVENT_TASK_STATUS_CHANGED, "payload": "{}"})
    result = await consumer.consume_once()

    assert result.malformed == 1
    assert result.failed == 0
    assert await redis_client.xlen(DEAD_LETTER_STREAM) == 0
