"""Integration test — auditoría runs 2026-07-02 (F0.2 fail-fast de workspace).

Cuando la provisión del worktree FALLA para un run IMPLEMENTADOR que esperaba
uno (task con plan + slugs), `conduct_execution` debe abortar en segundos con
`abort_code=workspace_unavailable` SIN lanzar el contenedor — antes el fallo
era solo un WARNING y el agente corría "a ciegas" sobre un tmpfs vacío hasta
quemar las 50 iteraciones (incidente /data del 2026-07-02).

El fallback a tmpfs se CONSERVA para (a) reviews sin worktree del implementador
(ADR 0095) y (b) tasks sin plan/slugs (legacy) — ambos quedan pinneados aquí.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Plan, Project, Task, TaskStatus
from api_server.db.execution_repo import list_executions_for_task
from api_server.db.models import Organization
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.config import Settings
from workers.container import ContainerResult
from workers.execution import ExecutionRequest, conduct_execution

pytestmark = pytest.mark.integration


class _RecordingRunner:
    """Fake AgentContainerRunner: registra si se lanzó un contenedor."""

    def __init__(self) -> None:
        self.specs: list[object] = []

    def run_streamed(
        self, spec: object, on_line: object, *, timeout: object = None
    ) -> ContainerResult:
        self.specs.append(spec)
        return ContainerResult(
            container_id="fake",
            exit_code=0,
            logs="",
            timed_out=False,
            host_config={},
            config_env=(),
            networks=(),
        )

    def kill_by_label(self, execution_id: str) -> int:  # pragma: no cover - not exercised
        return 0


_SCRIPTED_FINISH = {
    "kind": "scripted",
    "decisions": [{"kind": "finish", "output": "done"}],
}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(
    sm: async_sessionmaker, *, with_plan: bool, task_status: str = TaskStatus.IN_PROGRESS.value
) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "plan": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, plans, projects, organizations"
                " RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="WS tenant", slug="ws-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="WS project",
                slug="ws-project",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        if with_plan:
            s.add(
                Plan(
                    id=ids["plan"],
                    tenant_id=ids["tenant"],
                    project_id=ids["project"],
                    title="WS plan",
                    slug="ws-plan",
                    status="in_progress",
                )
            )
            await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                plan_id=ids["plan"] if with_plan else None,
                title="task",
                status=task_status,
                priority="medium",
            )
        )
    return ids


def _request(ids: dict[str, UUID], *, review: bool = False) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(ids["tenant"]),
        task_id=str(ids["task"]),
        agent_id=None,
        task={"id": str(ids["task"]), "title": "task", "description": "d"},
        model=_SCRIPTED_FINISH,
        review=review,
    )


@pytest.mark.asyncio
async def test_implementer_fails_fast_when_worktree_provision_fails(
    _migrated: None,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, with_plan=True)

        async def _broken_provision(*args: object, **kwargs: object) -> str | None:
            return None

        monkeypatch.setattr("workers.execution._provision_worktree", _broken_provision)
        runner = _RecordingRunner()

        outcome = await conduct_execution(
            _request(ids),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
            runner=runner,
        )

        # Fail-fast: sin contenedor, con abort_code legible.
        assert runner.specs == []
        assert outcome.status == "failed"
        assert outcome.abort_code == "workspace_unavailable"

        async with sm() as s:
            executions = await list_executions_for_task(s, ids["task"])
            task = await s.get(Task, ids["task"])
        assert len(executions) == 1
        assert executions[0].status == "failed"
        assert executions[0].abort_code == "workspace_unavailable"
        assert executions[0].completed_at is not None
        # La task sale de in_progress hacia blocked (motivo = el execution row).
        assert task is not None and task.status == TaskStatus.BLOCKED.value
    finally:
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_review_run_still_launches_without_worktree(
    _migrated: None,
    admin_database_url: str,
    test_redis_url: str,
    tmp_path: object,
) -> None:
    """ADR 0095: el reviewer sin worktree del implementador cae a workspace vacío
    (juzga con review_context) — el fail-fast NO aplica a reviews."""
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, with_plan=True, task_status=TaskStatus.IN_REVIEW.value)
        runner = _RecordingRunner()

        outcome = await conduct_execution(
            _request(ids, review=True),
            settings=Settings(data_root=str(tmp_path)),
            sessionmaker=sm,
            redis=redis,
            runner=runner,
        )

        assert len(runner.specs) == 1  # el contenedor SÍ se lanza
        assert outcome.abort_code != "workspace_unavailable"
    finally:
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_implementer_without_plan_keeps_tmpfs_fallback(
    _migrated: None,
    admin_database_url: str,
    test_redis_url: str,
) -> None:
    """Task sin plan/slugs (legacy): no se esperaba worktree → tmpfs, sin fail-fast."""
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, with_plan=False)
        runner = _RecordingRunner()

        outcome = await conduct_execution(
            _request(ids),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
            runner=runner,
        )

        assert len(runner.specs) == 1
        assert outcome.abort_code != "workspace_unavailable"
    finally:
        await redis.aclose()
        await engine.dispose()
