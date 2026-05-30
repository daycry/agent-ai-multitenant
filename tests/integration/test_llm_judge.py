"""Integration tests for the LLM-as-judge engine (Plan 14 task_14_04).

These drive the judging engine (``api_server.evals.judge``) against the
real Postgres (RLS) with a SCRIPTED judge + scripted subject — NO real
provider is ever touched (mirrors the ``ScriptedPlanningModel`` pattern of
the planning sub-graph). What we check:

  * a run judges each dataset item against EVERY criterion and persists an
    ``EvalResult`` per item with the per-criterion ``{score, passed,
    rationale}`` shape + the weighted overall score + verdict, and rolls the
    results up onto the run (pass rate, mean usage, status → completed);
  * the judge PROMPT is built from the criterion rubric (``judge_instruction``)
    + the produced output (asserted directly on the scripted judge);
  * a same-model judge (judge_model == subject_model) is REJECTED
    (``SameModelJudgeError``) before any judging;
  * a single failing criterion drives a ``fail`` verdict even when the other
    criterion passes;
  * criterion WEIGHTS affect the overall score (exact expected value);
  * cross-tenant (@pytest.mark.cross_tenant): a run is tenant-scoped — under
    tenant A's RLS scope the engine only ever sees / persists tenant A's
    rows; tenant B's dataset is invisible (zero items judged).

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the fixtures create a throwaway DB.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

# Import the full domain ORM so SQLAlchemy can resolve the eval FKs
# (eval_runs.subject_agent_id -> agents.id, etc.) when we instantiate
# EvalRun / EvalResult directly (the FK target tables must be mapped).
import api_server.db.domain  # noqa: F401
import asyncpg
import pytest
from alembic import command
from api_server.db.evals import EvalResultVerdict, EvalRunStatus
from api_server.evals.judge import (
    SameModelJudgeError,
    ScriptedJudgeModel,
    ScriptedSubjectModel,
    run_eval,
)
from sqlalchemy import select
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
            "TRUNCATE eval_results, eval_runs, eval_criteria, eval_dataset_items, "
            "eval_datasets, organizations RESTART IDENTITY CASCADE"
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


async def _seed_dataset(dsn: str, *, tenant_id: UUID, name: str) -> UUID:
    dataset_id = uuid7()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_datasets (id, tenant_id, name, kind) VALUES ($1, $2, $3, 'golden')",
            dataset_id,
            tenant_id,
            name,
        )
    finally:
        await conn.close()
    return dataset_id


async def _seed_criterion(
    dsn: str,
    *,
    tenant_id: UUID,
    dataset_id: UUID,
    name: str,
    judge_instruction: str,
    weight: str = "1",
    pass_threshold: str = "0.5",
) -> UUID:
    criterion_id = uuid7()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_criteria "
            "(id, tenant_id, dataset_id, name, judge_instruction, weight, pass_threshold) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            criterion_id,
            tenant_id,
            dataset_id,
            name,
            judge_instruction,
            Decimal(weight),
            Decimal(pass_threshold),
        )
    finally:
        await conn.close()
    return criterion_id


async def _seed_item(
    dsn: str,
    *,
    tenant_id: UUID,
    dataset_id: UUID,
    prompt: str,
    expected_output: str | None,
) -> UUID:
    item_id = uuid7()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_dataset_items "
            "(id, tenant_id, dataset_id, input, expected_output) "
            "VALUES ($1, $2, $3, $4::jsonb, $5)",
            item_id,
            tenant_id,
            dataset_id,
            f'{{"prompt": "{prompt}"}}',
            expected_output,
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


async def _new_run(session, *, tenant_id: UUID, dataset_id: UUID):
    from api_server.db.evals import EvalRun

    run = EvalRun(
        id=uuid7(),
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        subject_agent_id=None,
        subject_prompt_version="v1",
    )
    session.add(run)
    await session.flush()
    return run


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


# ---------------------------------------------------------------------------
# Happy path: a run judges each item against its criteria + persists results
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_judges_items_and_persists_results(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    dataset = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant, name="login golden")
    pep8 = await _seed_criterion(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        name="PEP 8",
        judge_instruction="Does the code follow PEP 8?",
    )
    tone = await _seed_criterion(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        name="tone",
        judge_instruction="Is the brand tone respected?",
    )
    item1 = await _seed_item(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        prompt="write a login",
        expected_output="def login(): ...",
    )
    item2 = await _seed_item(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        prompt="write a logout",
        expected_output="def logout(): ...",
    )

    judge = ScriptedJudgeModel(
        model="claude-opus-judge",
        responses={
            "PEP 8": '{"score": 0.9, "rationale": "Clean, lint-free."}',
            "tone": '{"score": 0.8, "rationale": "On brand."}',
        },
    )
    subject = ScriptedSubjectModel(
        model="gpt-subject",
        outputs={
            "write a login": "def login(): pass",
            "write a logout": "def logout(): pass",
        },
    )

    engine, session = await _open_session(app_database_url, tenant)
    try:
        run = await _new_run(session, tenant_id=tenant, dataset_id=dataset)
        results = await run_eval(
            session, run, judge=judge, subject_model=subject.model, subject=subject
        )
        await session.commit()

        # One result per item, each scored against BOTH criteria.
        assert len(results) == 2
        for r in results:
            assert r.verdict == EvalResultVerdict.PASS.value
            assert len(r.criterion_scores) == 2
            crit_ids = {s["criterion_id"] for s in r.criterion_scores}
            assert crit_ids == {str(pep8), str(tone)}
            for s in r.criterion_scores:
                assert set(s) == {"criterion_id", "score", "passed", "rationale"}
                assert s["passed"] is True
            # Weighted overall of equal-weight 0.9 + 0.8 = 0.85.
            assert r.overall_score == Decimal("0.850")
            assert r.produced_output in {"def login(): pass", "def logout(): pass"}

        result_items = {r.item_id for r in results}
        assert result_items == {item1, item2}

        # The judge prompt is built from the criterion rubric + the produced
        # output (asserted directly on the scripted judge).
        joined = "\n".join(judge.prompts)
        assert "Does the code follow PEP 8?" in joined
        assert "Is the brand tone respected?" in joined
        assert "def login(): pass" in joined
        assert "def logout(): pass" in joined

        # Run rolled up: pass_rate=1.0, status completed, judge_model recorded.
        assert run.status == EvalRunStatus.COMPLETED.value
        assert run.total_items == 2
        assert run.passed_items == 2
        assert run.pass_rate == Decimal("1.000")
        assert run.judge_model == "claude-opus-judge"
        assert run.finished_at is not None
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Same-model judge is rejected before any judging
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_same_model_judge_is_rejected(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    dataset = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant, name="ds")
    await _seed_criterion(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        name="c1",
        judge_instruction="rubric",
    )
    await _seed_item(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        prompt="p",
        expected_output="o",
    )

    same = "claude-opus"
    judge = ScriptedJudgeModel(model=same)

    engine, session = await _open_session(app_database_url, tenant)
    try:
        run = await _new_run(session, tenant_id=tenant, dataset_id=dataset)
        with pytest.raises(SameModelJudgeError):
            await run_eval(
                session,
                run,
                judge=judge,
                subject_model=same,
                produced_outputs={uuid4(): "x"},
            )
        # Nothing was judged — no prompt built.
        assert judge.prompts == []
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# A single failing criterion drives a fail verdict
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failing_criterion_drives_fail_verdict(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    dataset = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant, name="ds")
    # Default pass_threshold 0.5 on both.
    await _seed_criterion(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        name="passes",
        judge_instruction="A",
    )
    await _seed_criterion(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        name="fails",
        judge_instruction="B",
    )
    item = await _seed_item(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        prompt="p",
        expected_output="o",
    )

    judge = ScriptedJudgeModel(
        model="judge-model",
        responses={
            "passes": '{"score": 0.95, "rationale": "great"}',
            "fails": '{"score": 0.10, "rationale": "below threshold"}',
        },
    )

    engine, session = await _open_session(app_database_url, tenant)
    try:
        run = await _new_run(session, tenant_id=tenant, dataset_id=dataset)
        results = await run_eval(
            session,
            run,
            judge=judge,
            subject_model="subject-model",
            produced_outputs={item: "the submission"},
        )
        await session.commit()

        assert len(results) == 1
        r = results[0]
        # One criterion failed -> overall verdict is fail even though the
        # other passed.
        assert r.verdict == EvalResultVerdict.FAIL.value
        passed_flags = {s["rationale"]: s["passed"] for s in r.criterion_scores}
        assert passed_flags == {"great": True, "below threshold": False}
        assert run.passed_items == 0
        assert run.pass_rate == Decimal("0.000")
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Criterion weights affect the overall score
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_weights_affect_overall_score(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    dataset = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant, name="ds")
    # heavy weight 3 on the high score, weight 1 on the low score — both pass.
    await _seed_criterion(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        name="heavy",
        judge_instruction="A",
        weight="3",
        pass_threshold="0.5",
    )
    await _seed_criterion(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        name="light",
        judge_instruction="B",
        weight="1",
        pass_threshold="0.5",
    )
    item = await _seed_item(
        migrations_pg_dsn,
        tenant_id=tenant,
        dataset_id=dataset,
        prompt="p",
        expected_output="o",
    )

    judge = ScriptedJudgeModel(
        model="judge-model",
        responses={
            "heavy": '{"score": 1.0, "rationale": "perfect"}',
            "light": '{"score": 0.6, "rationale": "ok"}',
        },
    )

    engine, session = await _open_session(app_database_url, tenant)
    try:
        run = await _new_run(session, tenant_id=tenant, dataset_id=dataset)
        results = await run_eval(
            session,
            run,
            judge=judge,
            subject_model="subject-model",
            produced_outputs={item: "submission"},
        )
        await session.commit()

        r = results[0]
        assert r.verdict == EvalResultVerdict.PASS.value
        # Weighted: (1.0*3 + 0.6*1) / 4 = 3.6/4 = 0.9.
        assert r.overall_score == Decimal("0.900")
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Cross-tenant: a run only judges / persists within its own tenant's RLS scope
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_run_is_tenant_scoped(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")

    # B owns a dataset with a criterion + an item.
    dataset_b = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant_b, name="b-golden")
    await _seed_criterion(
        migrations_pg_dsn,
        tenant_id=tenant_b,
        dataset_id=dataset_b,
        name="b-crit",
        judge_instruction="secret rubric",
    )
    await _seed_item(
        migrations_pg_dsn,
        tenant_id=tenant_b,
        dataset_id=dataset_b,
        prompt="b-secret",
        expected_output="b-out",
    )

    # A creates a run pointing at B's dataset id, but under A's RLS scope the
    # engine cannot see B's items/criteria — zero items are judged, nothing of
    # B's is touched.
    judge = ScriptedJudgeModel(model="judge-model")

    engine, session = await _open_session(app_database_url, tenant_a)
    try:
        run = await _new_run(session, tenant_id=tenant_a, dataset_id=dataset_b)
        results = await run_eval(
            session,
            run,
            judge=judge,
            subject_model="subject-model",
            produced_outputs={},
        )
        await session.commit()

        assert results == []
        assert run.total_items == 0
        assert run.pass_rate is None
        # B's secret rubric was never read into a prompt.
        assert judge.prompts == []

        # A sees no eval_results at all under its own scope.
        from api_server.db.evals import EvalResult

        a_results = (await session.execute(select(EvalResult))).scalars().all()
        assert list(a_results) == []
    finally:
        await session.close()
        await engine.dispose()

    # B's rubric + item are still intact (A never touched them).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        crit_count = await conn.fetchval(
            "SELECT count(*) FROM eval_criteria WHERE dataset_id = $1", dataset_b
        )
        item_count = await conn.fetchval(
            "SELECT count(*) FROM eval_dataset_items WHERE dataset_id = $1", dataset_b
        )
    finally:
        await conn.close()
    assert crit_count == 1
    assert item_count == 1
