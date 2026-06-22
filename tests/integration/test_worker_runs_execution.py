"""Integration tests: the worker conducts a real execution (task_02_30).

`conduct_execution` is the worker's Fase G muscle: it creates the
`executions` row, launches the `agent-runtime:v1` container for one
task, streams its stdout, republishes every step event onto the
per-execution Redis stream (`exec:{id}` — what `/ws/executions/{id}`
tails), and finalises the row when the container exits.

These drive the real pipeline end to end: a Docker daemon, the test
PostgreSQL and the test Redis. They skip cleanly when Docker is absent.
"""

from __future__ import annotations

import asyncio
import threading
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import ExecutionStatus, Project, Task
from api_server.db.execution_repo import list_executions_for_task
from api_server.db.models import Organization
from api_server.events import execution_stream_key
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.config import Settings
from workers.container import ContainerResult
from workers.execution import ExecutionRequest, conduct_execution

import docker

from ._docker_helpers import docker_client, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]


class _BlockingRunner:
    """Fake AgentContainerRunner whose run_streamed blocks (as if a container were
    running) until kill_by_label is called — lets the cooperative-cancel poll be
    tested without Docker."""

    def __init__(self) -> None:
        self._kill = threading.Event()
        self.killed_ids: list[str] = []

    def run_streamed(
        self, spec: object, on_line: object, *, timeout: object = None
    ) -> ContainerResult:
        self._kill.wait(timeout=10)
        return ContainerResult(
            container_id="fake",
            exit_code=137,
            logs="",
            timed_out=False,
            host_config={},
            config_env=(),
            networks=(),
        )

    def kill_by_label(self, execution_id: str) -> int:
        self.killed_ids.append(execution_id)
        self._kill.set()
        return 1


_IMAGE = "agent-runtime:v1"


@pytest.fixture(scope="module", autouse=True)
def _agent_runtime_image() -> None:
    """Skip cleanly if agent-runtime:v1 has not been built on this host."""
    client = docker_client()
    try:
        client.images.get(_IMAGE)
    except docker.errors.ImageNotFound:  # pragma: no cover - env-dependent
        pytest.skip(f"{_IMAGE} not built — run: docker build -t {_IMAGE} ...")
    finally:
        client.close()


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


# A scripted model: one tool call, then finish. Deterministic — the
# loop wiring (task_02_30) works the same with a scripted or a real
# model (ADR 0017), so the worker tests stay offline.
_ACT_THEN_FINISH = {
    "kind": "scripted",
    "decisions": [
        {
            "kind": "act",
            "tool": "echo",
            "tool_args": {"text": "draft"},
            "tokens_in": 100,
            "tokens_out": 20,
            "cost_usd": 0.001,
        },
        {"kind": "finish", "output": "the sea poem", "tokens_in": 40, "tokens_out": 8},
    ],
}


async def _seed_task(sm: async_sessionmaker) -> dict[str, UUID]:
    """Insert a tenant / project / task; return their ids."""
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, projects, organizations"
                " RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Worker tenant", slug="worker-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Worker project",
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
                title="Write a sea poem",
                status="in_progress",
                priority="medium",
            )
        )
    return ids


def _request(ids: dict[str, UUID], *, model: dict, budgets: dict | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(ids["tenant"]),
        task_id=str(ids["task"]),
        agent_id=None,
        task={
            "id": str(ids["task"]),
            "title": "Write a sea poem",
            "description": "exercise the worker pipeline",
        },
        model=model,
        budgets=budgets,
    )


@pytest.mark.asyncio
async def test_conduct_execution_persists_a_done_execution_row(
    _migrated: None, admin_database_url: str, test_redis_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)

        outcome = await conduct_execution(
            _request(ids, model=_ACT_THEN_FINISH),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
        )

        assert outcome.status == ExecutionStatus.DONE

        async with sm() as s:
            executions = await list_executions_for_task(s, ids["task"])
        assert len(executions) == 1
        row = executions[0]
        assert str(row.id) == outcome.execution_id
        assert row.status == ExecutionStatus.DONE
        assert row.tenant_id == ids["tenant"]
        assert row.output == "the sea poem"
        assert row.completed_at is not None
        assert row.started_at is not None
        # The streamed steps survived into the persisted steps_log.
        assert len(row.steps_log) > 0
        assert any(step["kind"] == "model_call" for step in row.steps_log)
        # Usage roll-ups: 2 model calls (plan x2) + 1 review = 3; 1 tool call.
        assert row.model_call_count >= 2
        assert row.tool_call_count == 1
        assert row.total_tokens == 168  # 120 + 48 decide + 0 review
    finally:
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_conduct_execution_streams_events_to_the_per_execution_stream(
    _migrated: None, admin_database_url: str, test_redis_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)

        outcome = await conduct_execution(
            _request(ids, model=_ACT_THEN_FINISH),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
        )

        entries = await redis.xrange(execution_stream_key(outcome.execution_id))
        types = [fields["type"] for _id, fields in entries]
        # The container's own stream, republished verbatim by the worker.
        assert types[0] == "execution.started"
        assert types[-1] == "execution.finished"
        assert types.count("step") >= 1
    finally:
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_conduct_execution_records_an_aborted_run(
    _migrated: None, admin_database_url: str, test_redis_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)

        # Two distinct actions + max_iterations=2: the loop aborts on the
        # third planning turn (same shape as the entrypoint abort test).
        model = {
            "kind": "scripted",
            "decisions": [
                {"kind": "act", "tool": "echo", "tool_args": {"text": "a"}},
                {"kind": "act", "tool": "echo", "tool_args": {"text": "b"}},
            ],
        }
        outcome = await conduct_execution(
            _request(ids, model=model, budgets={"max_iterations": 2}),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
        )

        assert outcome.status == ExecutionStatus.ABORTED
        assert outcome.abort_code == "max_iterations_exceeded"

        async with sm() as s:
            executions = await list_executions_for_task(s, ids["task"])
        assert executions[0].status == ExecutionStatus.ABORTED
        assert executions[0].abort_code == "max_iterations_exceeded"
        assert executions[0].completed_at is not None
    finally:
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_unresolvable_model_fails_fast_without_launching_a_container(
    _migrated: None, admin_database_url: str, test_redis_url: str
) -> None:
    """ADR 0057 F1: un model_config con `provider` (kind) pero SIN proveedor
    activo de ese kind NO degrada a scripted ni lanza el contenedor — la
    ejecución se finaliza `failed` con abort_code='model_unresolved'."""
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)
        async with sm() as s, s.begin():
            await s.execute(text("TRUNCATE llm_providers RESTART IDENTITY CASCADE"))

        outcome = await conduct_execution(
            _request(ids, model={"provider": "ollama", "model": "qwen3-coder:480b"}),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
        )

        assert outcome.status == ExecutionStatus.FAILED
        assert outcome.abort_code == "model_unresolved"

        async with sm() as s:
            executions = await list_executions_for_task(s, ids["task"])
        assert len(executions) == 1
        row = executions[0]
        assert row.status == ExecutionStatus.FAILED
        assert row.abort_code == "model_unresolved"
        # El motivo es explícito para el operador (nunca un scripted silencioso).
        assert "no active llm_providers row" in (row.output or "")
        assert row.completed_at is not None
        # El stream recibió el error (lo que el WS de la UI tailea).
        entries = await redis.xrange(execution_stream_key(outcome.execution_id))
        assert any("execution.error" in str(entry) for entry in entries)
    finally:
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_conduct_execution_cancelled_by_operator_flag(
    _migrated: None, admin_database_url: str, test_redis_url: str
) -> None:
    """Cooperative cancellation (slice 2): while the container 'runs', an operator
    sets cancel_requested_at; the poll kills the container by label and the run is
    finalised as `cancelled` (terminal, completed_at set). No Docker — a fake runner
    blocks until killed."""
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task(sm)
        fake = _BlockingRunner()

        async def _cancel_once_running() -> None:
            for _ in range(200):  # up to ~4s
                await asyncio.sleep(0.02)
                async with sm() as s:
                    rows = await list_executions_for_task(s, ids["task"])
                    if rows and rows[0].status == ExecutionStatus.RUNNING:
                        await s.execute(
                            text(
                                "UPDATE executions SET cancel_requested_at = now()" " WHERE id = :i"
                            ),
                            {"i": rows[0].id},
                        )
                        await s.commit()
                        return

        canceller = asyncio.create_task(_cancel_once_running())
        outcome = await conduct_execution(
            _request(ids, model=_ACT_THEN_FINISH),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
            runner=fake,
            cancel_poll_interval_s=0.05,
        )
        await canceller

        assert outcome.status == ExecutionStatus.CANCELLED
        assert fake.killed_ids == [outcome.execution_id]  # the container was killed by label

        async with sm() as s:
            rows = await list_executions_for_task(s, ids["task"])
        assert len(rows) == 1
        assert rows[0].status == ExecutionStatus.CANCELLED
        assert rows[0].abort_code == "cancelled"
        assert rows[0].completed_at is not None  # cancelled is terminal
    finally:
        await redis.aclose()
        await engine.dispose()
