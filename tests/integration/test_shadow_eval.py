"""Integration tests for shadow evals (Plan 14 task_14_09).

A shadow eval replays a configurable random SAMPLE (5% default) of real,
COMPLETED tasks through a specialised reviewer agent / the LLM-as-judge to
RECORD a quality signal. The single binding decision (Plan 14 *Decisiones
Clave*): **shadow evals NEVER block or alter the real execution** — they only
record. These tests drive the sampler + the recorder against the real Postgres
(RLS) with a DETERMINISTIC sampler + a SCRIPTED judge — NO real LLM is ever
touched (reuses the Fase B ``ScriptedJudgeModel``/``ScriptedSubjectModel``
seam). What we check:

  * ~5% of tasks are sampled: with the injected deterministic sampler the
    EXPECTED set is the one sampled (no RNG luck), and the default hash sampler
    at 5% over a large population lands near 5% (a loose statistical bound);
  * a sampled task gets a shadow eval result RECORDED (an ``eval_shadow_records``
    row linked to a shadow ``eval_runs`` row with the replica's verdict);
  * the real task/execution is UNTOUCHED — same status / completed_at /
    updated_at before and after; no row is added to ``tasks`` / ``executions``;
  * the sample rate is CONFIGURABLE (explicit arg > env var > constant default),
    and a rate of 0 samples nothing while 1 samples everything;
  * cross-tenant (@pytest.mark.cross_tenant): recording runs under tenant A's
    RLS scope; a dataset belonging to tenant B yields zero judged items (an
    ``error`` verdict) and B's rows are never touched.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

# Import the full domain ORM so SQLAlchemy resolves the eval FKs
# (eval_shadow_records.source_task_id -> tasks.id, .shadow_run_id ->
# eval_runs.id, etc.) when we instantiate the eval rows directly.
import api_server.db.domain  # noqa: F401
import asyncpg
import pytest
from alembic import command
from api_server.db.evals import EvalResultVerdict, ShadowEvalStatus
from api_server.evals.constants import (
    DEFAULT_SHADOW_SAMPLE_RATE,
    SHADOW_SAMPLE_RATE_ENV_VAR,
)
from api_server.evals.judge import ScriptedJudgeModel, ScriptedSubjectModel
from api_server.evals.shadow import (
    DeterministicSampler,
    FixedSampler,
    record_shadow_eval,
    resolve_sample_rate,
    select_shadow_sample,
)
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE eval_shadow_records, eval_results, eval_runs, eval_criteria, "
            "eval_dataset_items, eval_datasets, executions, tasks, projects, "
            "organizations RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _seed_tenant(dsn: str, *, slug: str) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            slug.title(),
            slug,
        )
    finally:
        await conn.close()
    return tenant


async def _seed_project(dsn: str, *, tenant_id: UUID, name: str) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, $3, 'active')",
            project_id,
            tenant_id,
            name,
        )
    finally:
        await conn.close()
    return project_id


async def _seed_completed_task(dsn: str, *, tenant_id: UUID, project_id: UUID, title: str) -> UUID:
    """A real, COMPLETED task (the shadow sampler's source population)."""
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority, completed_at) "
            "VALUES ($1, $2, $3, $4, 'completed', 'medium', now())",
            task_id,
            tenant_id,
            project_id,
            title,
        )
    finally:
        await conn.close()
    return task_id


async def _seed_execution(dsn: str, *, tenant_id: UUID, task_id: UUID) -> UUID:
    execution_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, completed_at) "
            "VALUES ($1, $2, $3, 'completed', now())",
            execution_id,
            tenant_id,
            task_id,
        )
    finally:
        await conn.close()
    return execution_id


async def _seed_dataset(dsn: str, *, tenant_id: UUID, name: str, kind: str = "shadow") -> UUID:
    dataset_id = uuid7()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_datasets (id, tenant_id, name, kind) VALUES ($1, $2, $3, $4)",
            dataset_id,
            tenant_id,
            name,
            kind,
        )
    finally:
        await conn.close()
    return dataset_id


async def _seed_criterion(
    dsn: str, *, tenant_id: UUID, dataset_id: UUID, name: str, judge_instruction: str
) -> UUID:
    criterion_id = uuid7()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_criteria "
            "(id, tenant_id, dataset_id, name, judge_instruction) VALUES ($1, $2, $3, $4, $5)",
            criterion_id,
            tenant_id,
            dataset_id,
            name,
            judge_instruction,
        )
    finally:
        await conn.close()
    return criterion_id


async def _seed_item(dsn: str, *, tenant_id: UUID, dataset_id: UUID, prompt: str) -> UUID:
    item_id = uuid7()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_dataset_items (id, tenant_id, dataset_id, input) "
            "VALUES ($1, $2, $3, $4::jsonb)",
            item_id,
            tenant_id,
            dataset_id,
            f'{{"prompt": "{prompt}"}}',
        )
    finally:
        await conn.close()
    return item_id


async def _open_session(app_database_url: str, tenant_id: UUID):
    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


async def _task_snapshot(dsn: str, *, task_id: UUID) -> dict[str, object]:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, completed_at, updated_at FROM tasks WHERE id = $1", task_id
        )
    finally:
        await conn.close()
    assert row is not None
    return dict(row)


async def _count(dsn: str, *, table: str, where: str, arg: object) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {where}", arg))
    finally:
        await conn.close()


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


# ---------------------------------------------------------------------------
# ~5% of tasks are sampled — deterministic injected sampler picks the exact set
# ---------------------------------------------------------------------------
def test_injected_sampler_selects_the_expected_set() -> None:
    # With a scripted sampler the sample is EXACTLY the chosen ids — no RNG.
    task_ids = [uuid4() for _ in range(20)]
    chosen = {task_ids[3], task_ids[11]}
    sampler = FixedSampler(allow=frozenset(str(t) for t in chosen))

    sampled = select_shadow_sample(task_ids, rate=DEFAULT_SHADOW_SAMPLE_RATE, sampler=sampler)

    assert set(sampled) == chosen
    # Order is preserved (stable, reproducible selection).
    assert sampled == [t for t in task_ids if t in chosen]


def test_default_hash_sampler_lands_near_five_percent() -> None:
    # The production default sampler at 5% over a large population samples a
    # fraction CLOSE to 5% (a loose statistical bound — deterministic for the
    # seed, so this never flakes), and is reproducible run-to-run.
    sampler = DeterministicSampler(seed=42)
    task_ids = [uuid4() for _ in range(2000)]

    sampled = select_shadow_sample(task_ids, rate=Decimal("0.05"), sampler=sampler)
    again = select_shadow_sample(task_ids, rate=Decimal("0.05"), sampler=sampler)

    fraction = len(sampled) / len(task_ids)
    assert 0.03 <= fraction <= 0.07
    # Same seed + same ids -> identical selection (reproducible).
    assert sampled == again


def test_rate_zero_samples_nothing_and_rate_one_samples_all() -> None:
    sampler = DeterministicSampler(seed=7)
    task_ids = [uuid4() for _ in range(50)]

    assert select_shadow_sample(task_ids, rate=Decimal("0"), sampler=sampler) == []
    assert select_shadow_sample(task_ids, rate=Decimal("1"), sampler=sampler) == task_ids


# ---------------------------------------------------------------------------
# The sample rate is CONFIGURABLE: explicit arg > env var > constant default
# ---------------------------------------------------------------------------
def test_sample_rate_is_configurable() -> None:
    # Default (nothing set) is the named constant — never a magic number.
    assert resolve_sample_rate(env={}) == DEFAULT_SHADOW_SAMPLE_RATE
    # Env var overrides the default.
    assert resolve_sample_rate(env={SHADOW_SAMPLE_RATE_ENV_VAR: "0.10"}) == Decimal("0.10")
    # An explicit arg overrides BOTH the env var and the default.
    assert resolve_sample_rate(
        Decimal("0.25"), env={SHADOW_SAMPLE_RATE_ENV_VAR: "0.10"}
    ) == Decimal("0.25")
    # Out-of-range / non-numeric are rejected (a fraction in [0, 1]).
    with pytest.raises(ValueError):
        resolve_sample_rate(Decimal("1.5"))
    with pytest.raises(ValueError):
        resolve_sample_rate(env={SHADOW_SAMPLE_RATE_ENV_VAR: "abc"})


# ---------------------------------------------------------------------------
# A sampled task gets a shadow eval RECORDED — and the real task is UNTOUCHED
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sampled_task_records_shadow_eval_and_leaves_real_task_untouched(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="P")
    # A population of real, completed tasks; pick exactly one with a scripted
    # sampler (deterministic — the expected set is sampled).
    tasks = [
        await _seed_completed_task(
            migrations_pg_dsn, tenant_id=tenant, project_id=project, title=f"t{n}"
        )
        for n in range(10)
    ]
    sampled_task = tasks[4]
    execution = await _seed_execution(migrations_pg_dsn, tenant_id=tenant, task_id=sampled_task)

    sampler = FixedSampler(allow=frozenset({str(sampled_task)}))
    selected = select_shadow_sample(tasks, rate=DEFAULT_SHADOW_SAMPLE_RATE, sampler=sampler)
    assert selected == [sampled_task]

    # A shadow-kind dataset with one criterion + one item to judge the replica.
    dataset = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant, name="shadow ds")
    await _seed_criterion(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        name="quality",
        judge_instruction="Is the output high quality?",
    )
    await _seed_item(migrations_pg_dsn, tenant_id=tenant, dataset_id=dataset, prompt="replay me")

    # Snapshot the real task BEFORE the shadow eval — it must not change.
    before = await _task_snapshot(migrations_pg_dsn, task_id=sampled_task)

    judge = ScriptedJudgeModel(
        model="claude-opus-judge",
        responses={"quality": '{"score": 0.95, "rationale": "great"}'},
    )
    subject = ScriptedSubjectModel(
        model="gpt-subject", outputs={"replay me": "a high-quality replica output"}
    )

    engine, session = await _open_session(app_database_url, tenant)
    try:
        record = await record_shadow_eval(
            session,
            tenant_id=tenant,
            dataset_id=dataset,
            source_task_id=sampled_task,
            source_execution_id=execution,
            judge=judge,
            subject_model=subject.model,
            sample_rate=DEFAULT_SHADOW_SAMPLE_RATE,
            subject=subject,
        )
        await session.commit()

        # A shadow result is recorded, linked to the sampled real task + a
        # shadow run, with the replica's verdict + the sampling provenance.
        assert record.source_task_id == sampled_task
        assert record.source_execution_id == execution
        assert record.shadow_run_id is not None
        assert record.status == ShadowEvalStatus.JUDGED.value
        assert record.verdict == EvalResultVerdict.PASS.value
        assert record.sample_rate == DEFAULT_SHADOW_SAMPLE_RATE
    finally:
        await session.close()
        await engine.dispose()

    # The real task is UNTOUCHED — same status / completed_at / updated_at, and
    # no new task/execution rows were created (shadow never blocks/alters real).
    after = await _task_snapshot(migrations_pg_dsn, task_id=sampled_task)
    assert after == before
    assert await _count(migrations_pg_dsn, table="tasks", where="id = $1", arg=sampled_task) == 1
    assert await _count(migrations_pg_dsn, table="executions", where="id = $1", arg=execution) == 1
    # Exactly one shadow record persisted for the sampled task.
    assert (
        await _count(
            migrations_pg_dsn,
            table="eval_shadow_records",
            where="source_task_id = $1",
            arg=sampled_task,
        )
        == 1
    )


# ---------------------------------------------------------------------------
# Cross-tenant: recording runs under tenant A's RLS scope; B's dataset is
# invisible, so the shadow run judges zero items and B is never touched.
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_shadow_eval_is_tenant_scoped(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")

    # B owns a shadow dataset with a criterion + an item (its golden secret).
    dataset_b = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant_b, name="b-shadow")
    await _seed_criterion(
        migrations_pg_dsn,
        tenant_id=tenant_b,
        dataset_id=dataset_b,
        name="b-crit",
        judge_instruction="secret rubric",
    )
    await _seed_item(migrations_pg_dsn, tenant_id=tenant_b, dataset_id=dataset_b, prompt="b-secret")

    # A's own task population.
    project_a = await _seed_project(migrations_pg_dsn, tenant_id=tenant_a, name="PA")
    task_a = await _seed_completed_task(
        migrations_pg_dsn, tenant_id=tenant_a, project_id=project_a, title="a-task"
    )

    judge = ScriptedJudgeModel(model="judge-model")

    engine, session = await _open_session(app_database_url, tenant_a)
    try:
        # A records a shadow eval pointing at B's dataset id. Under A's RLS
        # scope the engine cannot see B's criteria/items -> zero judged items
        # -> an ERROR verdict (no signal), and B's rubric is never read.
        record = await record_shadow_eval(
            session,
            tenant_id=tenant_a,
            dataset_id=dataset_b,
            source_task_id=task_a,
            source_execution_id=None,
            judge=judge,
            subject_model="subject-model",
            sample_rate=Decimal("0.05"),
            produced_outputs={},
        )
        await session.commit()

        assert record.verdict == EvalResultVerdict.ERROR.value
        assert record.tenant_id == tenant_a
        # B's secret rubric was never read into a judge prompt.
        assert judge.prompts == []
    finally:
        await session.close()
        await engine.dispose()

    # B's rubric + item are still intact; B has NO shadow records (A's record
    # is tenant_a's and invisible to B).
    assert (
        await _count(
            migrations_pg_dsn, table="eval_criteria", where="dataset_id = $1", arg=dataset_b
        )
        == 1
    )
    assert (
        await _count(
            migrations_pg_dsn,
            table="eval_shadow_records",
            where="tenant_id = $1",
            arg=tenant_b,
        )
        == 0
    )
