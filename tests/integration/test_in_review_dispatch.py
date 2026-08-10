"""Integration test — prod-17 task_prod17_loop_01 + loop_02.

When a task enters ``in_review`` with an AI ``reviewer_agent_id``, the orchestrator
dispatches a REVIEW execution: a normal ``run_execution`` for the reviewer agent,
marked ``review=True`` and carrying the review context (acceptance criteria + the
implementer's prior output). A human reviewer (or none) is a no-op here (the
peer-review path owns it).
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Agent, Execution, Project, Task
from api_server.db.models import Organization
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings

from ._redis_url import TEST_REDIS_URL  # con credencial; ver _redis_url.py

pytestmark = pytest.mark.integration

_SCRIPTED = {"kind": "scripted", "decisions": [{"kind": "finish", "output": "verdict"}]}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(
    sm: async_sessionmaker,
    *,
    reviewer_type: str | None,
    prior_output: str = "did the work",
    acceptance_criteria: list | None = None,
    reviewer_model: dict | None = None,
    project_model: dict | None = None,
    project_budgets: dict | None = None,
    project_paused_by_budget: bool = False,
) -> dict[str, UUID]:
    """A task in ``in_review``; ``reviewer_type`` = 'ai' | 'human' | None decides
    whether a reviewer agent is attached and of which kind. ``reviewer_model``
    overrides the reviewer's own ``model_config`` (pass ``{}`` for a legacy agent
    that must inherit); ``project_model`` pins the project level of the chain.
    ``project_budgets`` / ``project_paused_by_budget`` set the project's
    ``execution_budgets`` override and its budget auto-pause flag (prod-06
    budget_02 / Plan 11.1 task_11_1_06)."""
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4(), "reviewer": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, agents, projects,"
                " organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="T", slug="t-rev-disp"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="P",
                status="active",
                is_template=False,
                worker_config={},
                model_config=project_model,
                execution_budgets=project_budgets,
                paused_by_budget=project_paused_by_budget,
            )
        )
        await s.flush()
        reviewer_agent_id = None
        if reviewer_type is not None:
            s.add(
                Agent(
                    id=ids["reviewer"],
                    tenant_id=ids["tenant"],
                    name="Rev",
                    role="reviewer",
                    system_prompt="review it",
                    agent_type=reviewer_type,
                    scope="project_local",
                    project_id=ids["project"],
                    model_config=reviewer_model if reviewer_model is not None else _SCRIPTED,
                )
            )
            await s.flush()
            reviewer_agent_id = ids["reviewer"]
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="implement X",
                description="acceptance: X must work",
                status="in_review",
                priority="medium",
                reviewer_agent_id=reviewer_agent_id,
                acceptance_criteria=acceptance_criteria or [],
            )
        )
        await s.flush()
        # A prior implementer execution whose output the reviewer will judge.
        s.add(
            Execution(
                id=uuid4(),
                tenant_id=ids["tenant"],
                task_id=ids["task"],
                status="done",
                output=prior_output,
                steps_log=[],
            )
        )
    return ids


def _dispatcher(sm: async_sessionmaker) -> TaskDispatcher:
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=build_celery_app(
            WorkerSettings(broker_url=TEST_REDIS_URL, result_backend=TEST_REDIS_URL)
        ),
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL, dispatch_queue="default"),
    )


def _in_review_event(ids: dict[str, UUID]) -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(ids["tenant"]),
        project_id=str(ids["project"]),
        task_id=str(ids["task"]),
        occurred_at="2026-06-26T00:00:00+00:00",
        payload={"old_status": "in_progress", "new_status": "in_review"},
    )


async def _drain(redis: Redis, queue: str) -> list[dict]:
    raw = await redis.lrange(queue, 0, -1)
    await redis.delete(queue)
    return [json.loads(item) for item in raw]


def _run_request(messages: list[dict]) -> dict:
    """Extract the run_execution `request` kwarg from a drained Celery message."""
    for msg in messages:
        body = msg.get("body")
        # Celery protocol v2: body is base64 (args, kwargs, embed).
        import base64

        decoded = json.loads(base64.b64decode(body))
        _args, kwargs, _embed = decoded
        if "request" in kwargs:
            return kwargs["request"]
    raise AssertionError("no run_execution request enqueued")


@pytest.mark.asyncio
async def test_ai_reviewer_dispatches_review_execution(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete("default")
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, reviewer_type="ai", prior_output="implemented the parser")

        await _dispatcher(sm).handle(_in_review_event(ids))

        messages = await _drain(redis, "default")
        request = _run_request(messages)
        # A review execution for the reviewer agent, marked + with context.
        assert request["review"] is True
        assert request["agent_id"] == str(ids["reviewer"])
        assert request["task_id"] == str(ids["task"])
        assert request["review_context"]["implementer_output"] == "implemented the parser"
        assert "acceptance: X must work" in request["review_context"]["acceptance_criteria"]
    finally:
        await engine.dispose()
        await redis.aclose()


@pytest.mark.asyncio
async def test_ai_reviewer_inherits_model_through_the_chain(
    _migrated: None, admin_database_url: str
) -> None:
    """Un reviewer sin modelo propio (``{}`` legacy) hereda por la MISMA cadena
    ADR 0055 que el implementador (plataforma → proyecto → equipo → agente).

    Caracterización del hallazgo H2 del refactor 2026-07-07: la rama de review
    re-derivaba la cadena inline en vez de usar ``_resolve_model_spec`` — este
    test fija la herencia observable para que la deduplicación no pueda divergir."""
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete("default")
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(
            sm,
            reviewer_type="ai",
            reviewer_model={},
            project_model={"kind": "ollama", "model": "llama3.1"},
        )

        await _dispatcher(sm).handle(_in_review_event(ids))

        request = _run_request(await _drain(redis, "default"))
        assert request["review"] is True
        # El nivel proyecto de la cadena rellena el spec del reviewer legacy.
        assert request["model"]["kind"] == "ollama"
        assert request["model"]["model"] == "llama3.1"
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_review_request_uses_real_acceptance_criteria(
    _migrated: None, admin_database_url: str
) -> None:
    """Auditoría 2026-07-02 (F1.6a): el reviewer certificaba contra
    `task.description` mientras el implementador trabajaba contra los
    `task.acceptance_criteria` reales — dos definiciones de "done" distintas en
    el mismo ciclo (rechazos por cosas no pedidas, approves con criterios sin
    cubrir). El review_context debe llevar los criteria reales; la description
    queda solo como fallback cuando no hay criteria."""
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete("default")
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(
            sm,
            reviewer_type="ai",
            prior_output="done it",
            acceptance_criteria=[
                {
                    "id": "a",
                    "description": "el endpoint /hello devuelve 200",
                    "runtime": "php-phpunit",
                    "command": "vendor/bin/phpunit",
                },
                "los tests de la suite pasan",
            ],
        )

        await _dispatcher(sm).handle(_in_review_event(ids))

        messages = await _drain(redis, "default")
        request = _run_request(messages)
        criteria = request["review_context"]["acceptance_criteria"]
        assert "el endpoint /hello devuelve 200" in criteria
        assert "los tests de la suite pasan" in criteria
        # La description ya NO sustituye a los criteria reales.
        assert "acceptance: X must work" not in criteria
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_review_request_includes_test_report_when_present(
    _migrated: None, admin_database_url: str
) -> None:
    """prod-17 test_02: a persisted test_run_completed outcome is folded into the
    reviewer's <test-report> block."""
    from api_server.db.models import TaskAuditEvent

    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete("default")
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, reviewer_type="ai")
        # A failed test run persisted for the task (what test_01 will emit).
        async with sm() as s, s.begin():
            s.add(
                TaskAuditEvent(
                    id=uuid4(),
                    tenant_id=ids["tenant"],
                    task_id=ids["task"],
                    kind="test_run_completed",
                    actor="system:celery",
                    payload={
                        "runtime": "python-pytest",
                        "exit_codes": [1],
                        "all_passed": False,
                        "timed_out": False,
                        "logs_tail": "E   assert 1 == 2",
                    },
                )
            )

        await _dispatcher(sm).handle(_in_review_event(ids))

        request = _run_request(await _drain(redis, "default"))
        block = request["review_context"]["test_report"]
        assert "<test-report>" in block
        assert "runtime python-pytest: FAILED" in block
        assert "assert 1 == 2" in block
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_review_request_carries_the_resolved_budget_envelope(
    _migrated: None, admin_database_url: str
) -> None:
    """prod-17 `task_prod17_e2e_01`: la ejecución de review CUENTA contra el
    budget del proyecto — reusa el envelope de prod-06 `budget_02`, no lo
    reimplementa.

    El código lo hace (`_build_review_request` delega en `_assemble_run_request`,
    que resuelve plataforma←proyecto y clampa al techo), pero nada lo afirmaba: un
    `grep budget` sobre este fichero daba 0. Si mañana la rama de review volviera
    a montar su payload inline —como ya pasó con la cadena de herencia del modelo,
    hallazgo H2— el review correría SIN techo de coste y ningún test se enteraría.

    Se afirma sobre valores, no sobre la presencia de la clave: un `budgets: {}`
    o un envelope de plataforma pasarían un `assert "budgets" in request`.
      * `max_cost_usd` 0,25 se respeta (por debajo del techo de 5,0);
      * `max_iterations` 999 se CLAMPA al techo del runtime (50);
      * `max_review_retries` se DESCARTA: es límite duro de plataforma (ADR 0013),
        no un budget que un proyecto pueda relajar.
    """
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete("default")
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(
            sm,
            reviewer_type="ai",
            project_budgets={
                "max_cost_usd": 0.25,
                "max_iterations": 999,
                "max_review_retries": 42,
            },
        )

        await _dispatcher(sm).handle(_in_review_event(ids))

        request = _run_request(await _drain(redis, "default"))
        assert request["review"] is True
        budgets = request["budgets"]
        assert budgets["max_cost_usd"] == 0.25, (
            "el override de budget del proyecto no viajó en el run de review: la "
            "ejecución del reviewer no contaría contra el budget del proyecto"
        )
        assert budgets["max_iterations"] == 50, (
            "el override del proyecto no se clampó al techo del runtime en la "
            f"rama de review (llegó {budgets.get('max_iterations')!r})"
        )
        assert "max_review_retries" not in budgets, (
            "max_review_retries es un límite duro de plataforma (ADR 0013): un "
            "override de proyecto no puede colarlo en el envelope del run"
        )
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_human_reviewer_is_not_dispatched_here(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete("default")
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, reviewer_type="human")

        await _dispatcher(sm).handle(_in_review_event(ids))

        # Human reviewer → peer-review path owns it; nothing enqueued here.
        assert await _drain(redis, "default") == []
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_reviewer_is_noop(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete("default")
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, reviewer_type=None)

        await _dispatcher(sm).handle(_in_review_event(ids))

        assert await _drain(redis, "default") == []
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()
