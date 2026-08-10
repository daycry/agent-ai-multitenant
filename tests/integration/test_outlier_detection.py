"""Integration tests for agent OUTLIER detection + configurable alerts (task_14_13).

Outlier detection identifies the tenant's agents whose success rate / cost /
latency over a window deviates significantly from the tenant norm, and — when a
tenant-configured ``OutlierAlertRule`` trips — fires ONE alert per breaching
agent through the Plan 10 notifier (reusing the guardrail-alert / drift-alert
dispatch seam). The notification enqueue is MOCKED (a fake dispatcher stands in
for the live broker / channel send). What we check:

  * a SUCCESS-RATE FLOOR rule ("if success rate < 70%, alert"): an agent below
    the configured floor is flagged AND an alert fires (a
    ``agent_outlier_alert`` event for the tenant); a NORMAL agent above the
    floor is NOT flagged;
  * a statistical (cost) rule: an agent whose mean cost is more than ``stddev_k``
    standard deviations above the tenant mean is flagged; peers within the norm
    are not;
  * thresholds are CONFIGURABLE (a stricter floor flags an agent a laxer one
    passes; ``min_runs`` suppresses a tiny sample);
  * the debounce suppresses a re-alert within the window;
  * tenant-scoped (@pytest.mark.cross_tenant): evaluating tenant B sees NONE of
    tenant A's agents, so A's outliers can never alert B.

The pure :func:`detect_outliers` is exercised directly (no DB) for the
floor / stddev / configurable / min_runs cases; the DB-backed
:func:`evaluate_outlier_rules` runs against the real Postgres (RLS) for the
alert-fired / tenant-scoped cases. NO real LLM is ever touched.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

# Import the full domain ORM so SQLAlchemy resolves the FKs the metrics query
# joins (Execution.agent_id -> agents.id, etc.).
import api_server.db.domain  # noqa: F401
import asyncpg
import pytest
from alembic import command
from api_server.db.outlier_alert_rule import (
    DEFAULT_SUCCESS_RATE_FLOOR,
    OutlierMetric,
)
from api_server.stats.outliers import (
    AgentMetric,
    detect_outliers,
    evaluate_outlier_rules,
)
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ._partitions import ensure_partition_for

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE outlier_alert_rules, executions, tasks, plans, agents, projects, "
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


async def _seed_project(dsn: str, *, tenant: UUID, name: str) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, $3, 'active')",
            project_id,
            tenant,
            name,
        )
    finally:
        await conn.close()
    return project_id


async def _seed_agent(dsn: str, *, tenant: UUID, name: str, role: str) -> UUID:
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope) "
            "VALUES ($1, $2, $3, $4, 'be helpful', 'global_tenant_template')",
            agent_id,
            tenant,
            name,
            role,
        )
    finally:
        await conn.close()
    return agent_id


async def _seed_task(dsn: str, *, tenant: UUID, project_id: UUID, title: str) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, retry_count) "
            "VALUES ($1, $2, $3, NULL, $4, 'done', 0)",
            task_id,
            tenant,
            project_id,
            title,
        )
    finally:
        await conn.close()
    return task_id


async def _seed_execution(
    dsn: str,
    *,
    tenant: UUID,
    task_id: UUID,
    agent_id: UUID,
    status: str = "done",
    total_cost_usd: float = 0.0,
    duration_ms: int | None = 1000,
    created_at: datetime | None = None,
) -> UUID:
    execution_id = uuid4()
    now = created_at or datetime.now(tz=UTC)
    started = now
    completed = now + timedelta(milliseconds=duration_ms) if duration_ms is not None else None
    # `executions` está particionada por mes y SIN DEFAULT (ADR 0151). Hoy ningún
    # llamante retrofecha, pero el parámetro `created_at` está aquí invitando a
    # hacerlo — y la ventana de las reglas de outliers se mide en días. Ver
    # docs/03-guides/gotchas/sembrar-filas-retrofechadas-en-tabla-particionada.md
    await ensure_partition_for(dsn, "executions", now)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions "
            "(id, tenant_id, task_id, agent_id, status, steps_log, total_tokens, "
            " total_cost_usd, started_at, completed_at, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, 0, $7, $8, $9, $10)",
            execution_id,
            tenant,
            task_id,
            agent_id,
            status,
            json.dumps([]),
            total_cost_usd,
            started,
            completed,
            now,
        )
    finally:
        await conn.close()
    return execution_id


async def _seed_rule(
    dsn: str,
    *,
    tenant: UUID,
    name: str,
    metric: str,
    window_days: int = 30,
    min_runs: int = 5,
    success_rate_floor: Decimal | None = None,
    stddev_k: Decimal | None = None,
) -> UUID:
    rule_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO outlier_alert_rules "
            "(id, tenant_id, name, metric, window_days, min_runs, success_rate_floor, "
            " stddev_k, enabled) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, true)",
            rule_id,
            tenant,
            name,
            metric,
            window_days,
            min_runs,
            success_rate_floor,
            stddev_k,
        )
    finally:
        await conn.close()
    return rule_id


async def _count_fired_rules(dsn: str, *, tenant: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM outlier_alert_rules "
                "WHERE tenant_id = $1 AND last_fired_at IS NOT NULL",
                tenant,
            )
        )
    finally:
        await conn.close()


async def _open_session(app_database_url: str, tenant_id: UUID):
    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


class _FakeDispatcher:
    """Stands in for the Plan 10 notifier — captures the dispatched events
    instead of enqueuing a real ``dispatch_event`` task (mocks the channel
    send)."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def dispatch(self, event: dict[str, object]) -> bool:
        self.events.append(event)
        return True


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


# ---------------------------------------------------------------------------
# Seed an agent with a controlled run count + success/cost/latency profile.
# ---------------------------------------------------------------------------
async def _seed_agent_runs(
    dsn: str,
    *,
    tenant: UUID,
    task_id: UUID,
    name: str,
    role: str,
    total: int,
    successes: int,
    cost_per_run: float = 0.01,
    duration_ms: int = 1000,
) -> UUID:
    agent_id = await _seed_agent(dsn, tenant=tenant, name=name, role=role)
    for i in range(total):
        await _seed_execution(
            dsn,
            tenant=tenant,
            task_id=task_id,
            agent_id=agent_id,
            status="done" if i < successes else "aborted",
            total_cost_usd=cost_per_run,
            duration_ms=duration_ms,
        )
    return agent_id


# ===========================================================================
# Pure detector — floor, stddev, configurable, min_runs
# ===========================================================================
def _metric(
    *,
    name: str,
    runs: int,
    success_rate: str | None = None,
    cost: str | None = None,
    latency: str | None = None,
) -> AgentMetric:
    return AgentMetric(
        agent_id=uuid4(),
        agent_name=name,
        agent_role="backend_dev",
        run_count=runs,
        success_rate=Decimal(success_rate) if success_rate is not None else None,
        mean_cost=Decimal(cost) if cost is not None else None,
        mean_latency_ms=Decimal(latency) if latency is not None else None,
    )


def test_success_rate_floor_flags_low_agent() -> None:
    metrics = [
        _metric(name="good", runs=10, success_rate="0.95"),
        _metric(name="bad", runs=10, success_rate="0.50"),
    ]
    decision = detect_outliers(
        metrics,
        metric=OutlierMetric.SUCCESS_RATE,
        min_runs=5,
        success_rate_floor=Decimal("0.7"),
    )
    flagged = [f.agent_name for f in decision.flagged]
    assert flagged == ["bad"]
    assert decision.flagged[0].value == Decimal("0.50")
    assert decision.flagged[0].bound == Decimal("0.7")


def test_normal_agent_not_flagged() -> None:
    metrics = [
        _metric(name="good", runs=10, success_rate="0.95"),
        _metric(name="okay", runs=10, success_rate="0.80"),
    ]
    decision = detect_outliers(
        metrics,
        metric=OutlierMetric.SUCCESS_RATE,
        min_runs=5,
        success_rate_floor=Decimal("0.7"),
    )
    assert decision.flagged == ()


def test_floor_is_configurable() -> None:
    metrics = [_metric(name="mid", runs=10, success_rate="0.75")]
    # A 0.7 floor passes it...
    assert (
        detect_outliers(
            metrics,
            metric=OutlierMetric.SUCCESS_RATE,
            min_runs=5,
            success_rate_floor=Decimal("0.7"),
        ).flagged
        == ()
    )
    # ...a stricter 0.8 floor flags it.
    strict = detect_outliers(
        metrics,
        metric=OutlierMetric.SUCCESS_RATE,
        min_runs=5,
        success_rate_floor=Decimal("0.8"),
    )
    assert [f.agent_name for f in strict.flagged] == ["mid"]


def test_min_runs_suppresses_tiny_sample() -> None:
    # An agent with 2 runs at 0% success is NOT flagged when min_runs is 5 —
    # a statistically meaningless sample.
    metrics = [
        _metric(name="tiny", runs=2, success_rate="0.0"),
        _metric(name="ok", runs=10, success_rate="0.95"),
    ]
    decision = detect_outliers(
        metrics,
        metric=OutlierMetric.SUCCESS_RATE,
        min_runs=5,
        success_rate_floor=Decimal("0.7"),
    )
    assert decision.flagged == ()
    assert decision.considered == 1


def test_cost_stddev_flags_spike() -> None:
    # Seven tight cheap peers + one spender. With k=2 the spender is above
    # mean + 2·stddev (a single huge outlier would mask itself by inflating
    # the stddev — a moderate spike against a tight population does not).
    metrics = [
        _metric(name="a", runs=10, cost="0.010"),
        _metric(name="b", runs=10, cost="0.011"),
        _metric(name="c", runs=10, cost="0.009"),
        _metric(name="d", runs=10, cost="0.012"),
        _metric(name="e", runs=10, cost="0.010"),
        _metric(name="f", runs=10, cost="0.011"),
        _metric(name="g", runs=10, cost="0.009"),
        _metric(name="spike", runs=10, cost="0.050"),
    ]
    decision = detect_outliers(
        metrics,
        metric=OutlierMetric.COST,
        min_runs=5,
        stddev_k=Decimal("2.0"),
    )
    assert [f.agent_name for f in decision.flagged] == ["spike"]
    assert decision.population_mean is not None
    assert decision.population_stddev is not None


def test_stddev_k_is_configurable() -> None:
    metrics = [
        _metric(name="a", runs=10, cost="0.01"),
        _metric(name="b", runs=10, cost="0.02"),
        _metric(name="c", runs=10, cost="0.03"),
        _metric(name="d", runs=10, cost="0.10"),
    ]
    # A large k flags nobody...
    assert (
        detect_outliers(
            metrics, metric=OutlierMetric.COST, min_runs=5, stddev_k=Decimal("3.0")
        ).flagged
        == ()
    )
    # ...a smaller k flags the spender.
    lax = detect_outliers(metrics, metric=OutlierMetric.COST, min_runs=5, stddev_k=Decimal("1.0"))
    assert [f.agent_name for f in lax.flagged] == ["d"]


def test_stddev_needs_two_agents() -> None:
    metrics = [_metric(name="only", runs=10, cost="0.99")]
    decision = detect_outliers(
        metrics, metric=OutlierMetric.COST, min_runs=5, stddev_k=Decimal("2.0")
    )
    assert decision.flagged == ()


def test_none_metric_value_is_skipped() -> None:
    # An agent with enough runs but an undefined success rate is not flagged.
    metrics = [_metric(name="nodata", runs=10, success_rate=None)]
    decision = detect_outliers(
        metrics,
        metric=OutlierMetric.SUCCESS_RATE,
        min_runs=5,
        success_rate_floor=Decimal("0.7"),
    )
    assert decision.flagged == ()
    assert decision.considered == 0


def test_metric_threshold_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        detect_outliers(
            [_metric(name="a", runs=10, success_rate="0.5")],
            metric=OutlierMetric.SUCCESS_RATE,
            min_runs=5,
            success_rate_floor=None,
        )
    with pytest.raises(ValueError):
        detect_outliers(
            [_metric(name="a", runs=10, cost="0.5")],
            metric=OutlierMetric.COST,
            min_runs=5,
            stddev_k=None,
        )


# ===========================================================================
# DB-backed: an agent below the floor is flagged + an alert fires
# ===========================================================================
@pytest.mark.asyncio
async def test_low_agent_fires_alert_normal_does_not(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme-out")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")

    # A healthy agent (9/10 done) and a flaqueando one (4/10 done = 0.4 < 0.7).
    await _seed_agent_runs(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        name="Good",
        role="backend_dev",
        total=10,
        successes=9,
    )
    await _seed_agent_runs(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        name="Bad",
        role="qa",
        total=10,
        successes=4,
    )

    await _seed_rule(
        migrations_pg_dsn,
        tenant=tenant,
        name="Success floor 70%",
        metric="success_rate",
        success_rate_floor=DEFAULT_SUCCESS_RATE_FLOOR,
        min_runs=5,
    )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        result = await evaluate_outlier_rules(session, tenant_id=tenant, dispatcher=dispatcher)
        await session.commit()

        assert result.evaluated == 1
        assert len(result.fired) == 1
        firing = result.fired[0]
        assert firing.metric == "success_rate"
        assert firing.flagged_count == 1  # ONLY the bad agent

        # Exactly ONE alert, an agent_outlier_alert for the tenant naming "Bad".
        assert len(dispatcher.events) == 1
        event = dispatcher.events[0]
        assert event["event_type"] == "agent_outlier_alert"
        assert event["tenant_id"] == str(tenant)
        ctx = event["context"]
        assert ctx["flagged_count"] == 1
        assert ctx["agent_name"] == "Bad"
        assert ctx["metric"] == "success_rate"
        agents = ctx["agents"]
        assert [a["agent_name"] for a in agents] == ["Bad"]
    finally:
        await session.close()
        await engine.dispose()

    assert await _count_fired_rules(migrations_pg_dsn, tenant=tenant) == 1


# ===========================================================================
# DB-backed: no breach -> no alert
# ===========================================================================
@pytest.mark.asyncio
async def test_all_healthy_no_alert(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="healthy-out")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")
    await _seed_agent_runs(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        name="Good",
        role="backend_dev",
        total=10,
        successes=10,
    )
    await _seed_rule(
        migrations_pg_dsn,
        tenant=tenant,
        name="Floor",
        metric="success_rate",
        success_rate_floor=Decimal("0.7"),
        min_runs=5,
    )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        result = await evaluate_outlier_rules(session, tenant_id=tenant, dispatcher=dispatcher)
        await session.commit()
        assert result.fired == []
        assert dispatcher.events == []
    finally:
        await session.close()
        await engine.dispose()

    assert await _count_fired_rules(migrations_pg_dsn, tenant=tenant) == 0


# ===========================================================================
# DB-backed: threshold configurable + debounce suppresses re-alert
# ===========================================================================
@pytest.mark.asyncio
async def test_threshold_configurable_and_debounce(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="cfg-out")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")
    # 7/10 done = 0.7 success rate.
    await _seed_agent_runs(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        name="Mid",
        role="backend_dev",
        total=10,
        successes=7,
    )

    # A lax floor of 0.7 does NOT flag 0.7 (strictly below required)...
    lax_rule = await _seed_rule(
        migrations_pg_dsn,
        tenant=tenant,
        name="Lax 0.7",
        metric="success_rate",
        success_rate_floor=Decimal("0.7"),
        min_runs=5,
    )
    # ...a strict floor of 0.8 DOES.
    strict_rule = await _seed_rule(
        migrations_pg_dsn,
        tenant=tenant,
        name="Strict 0.8",
        metric="success_rate",
        success_rate_floor=Decimal("0.8"),
        min_runs=5,
    )

    base = datetime.now(tz=UTC)
    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        r1 = await evaluate_outlier_rules(
            session, tenant_id=tenant, dispatcher=dispatcher, now=base
        )
        await session.commit()
        fired_ids = {f.rule_id for f in r1.fired}
        assert strict_rule in fired_ids
        assert lax_rule not in fired_ids
        assert len(dispatcher.events) == 1

        # A second evaluation 1 day later (inside the 30-day window) is
        # debounced — no new alert.
        r2 = await evaluate_outlier_rules(
            session, tenant_id=tenant, dispatcher=dispatcher, now=base + timedelta(days=1)
        )
        await session.commit()
        assert r2.fired == []
        assert strict_rule in r2.suppressed_rule_ids
        assert len(dispatcher.events) == 1  # still ONE — no spam
    finally:
        await session.close()
        await engine.dispose()


# ===========================================================================
# Tenant-scoped: evaluating tenant B sees NONE of tenant A's agents
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_outlier_evaluation_is_tenant_scoped(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha-out")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo-out")
    project_a = await _seed_project(migrations_pg_dsn, tenant=tenant_a, name="A")
    project_b = await _seed_project(migrations_pg_dsn, tenant=tenant_b, name="B")
    task_a = await _seed_task(migrations_pg_dsn, tenant=tenant_a, project_id=project_a, title="TA")
    task_b = await _seed_task(migrations_pg_dsn, tenant=tenant_b, project_id=project_b, title="TB")

    # Tenant A has a failing agent; tenant B has only a healthy one.
    await _seed_agent_runs(
        migrations_pg_dsn,
        tenant=tenant_a,
        task_id=task_a,
        name="A-Bad",
        role="qa",
        total=10,
        successes=3,
    )
    await _seed_agent_runs(
        migrations_pg_dsn,
        tenant=tenant_b,
        task_id=task_b,
        name="B-Good",
        role="backend_dev",
        total=10,
        successes=10,
    )

    # BOTH tenants have the SAME-shaped rule (their own row).
    await _seed_rule(
        migrations_pg_dsn,
        tenant=tenant_a,
        name="Floor",
        metric="success_rate",
        success_rate_floor=Decimal("0.7"),
        min_runs=5,
    )
    await _seed_rule(
        migrations_pg_dsn,
        tenant=tenant_b,
        name="Floor",
        metric="success_rate",
        success_rate_floor=Decimal("0.7"),
        min_runs=5,
    )

    # Evaluating tenant B sees NONE of A's failing agent -> no flag, no alert.
    dispatcher_b = _FakeDispatcher()
    engine_b, session_b = await _open_session(app_database_url, tenant_b)
    try:
        result_b = await evaluate_outlier_rules(
            session_b, tenant_id=tenant_b, dispatcher=dispatcher_b
        )
        await session_b.commit()
        assert result_b.fired == []
        assert dispatcher_b.events == []
        # B's only considered agent is B-Good, not A-Bad.
        rule_b_decision = next(iter(result_b.decisions.values()))
        assert rule_b_decision.flagged == ()
    finally:
        await session_b.close()
        await engine_b.dispose()

    # Evaluating tenant A fires exactly one alert scoped to A, naming A-Bad.
    dispatcher_a = _FakeDispatcher()
    engine_a, session_a = await _open_session(app_database_url, tenant_a)
    try:
        result_a = await evaluate_outlier_rules(
            session_a, tenant_id=tenant_a, dispatcher=dispatcher_a
        )
        await session_a.commit()
        assert len(result_a.fired) == 1
        assert len(dispatcher_a.events) == 1
        event = dispatcher_a.events[0]
        assert event["tenant_id"] == str(tenant_a)
        assert event["context"]["agent_name"] == "A-Bad"
    finally:
        await session_a.close()
        await engine_a.dispose()

    # B never fired; A fired once.
    assert await _count_fired_rules(migrations_pg_dsn, tenant=tenant_b) == 0
    assert await _count_fired_rules(migrations_pg_dsn, tenant=tenant_a) == 1
