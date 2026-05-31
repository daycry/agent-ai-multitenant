"""Integration tests for quality-drift detection (Plan 14 task_14_10).

Drift detection must alert on a SUSTAINED quality decline (Plan 14): over a
configurable trailing window of a benchmark's eval-run pass rates, drift is
declared only when the latest ``window`` consecutive runs EACH drop by at least
the per-step threshold — a single dip never triggers. On drift, ONE alert fires
through the Plan 10 notifier (reusing the guardrail-alert dispatch seam) to the
tenant's Tenant Admins; the notification enqueue is MOCKED (a fake dispatcher
stands in for the live broker / channel send). What we check:

  * a sustained decline below the threshold -> drift detected + ONE alert fired
    (the dispatched event is a ``quality_drift_alert`` for the tenant);
  * a SINGLE dip (one low run flanked by recovery) does NOT trigger — the
    sustained requirement is enforced;
  * stable / improving quality -> no drift, no alert;
  * the window / drop threshold are CONFIGURABLE (a stricter window declares
    drift on the same stream a laxer one passes; explicit arg > env > default);
  * tenant-scoped (@pytest.mark.cross_tenant): evaluating tenant B sees NONE of
    tenant A's runs, so A's decline can never alert B.

The pure :func:`detect_drift` is exercised directly (no DB) for the sustained /
single-dip / stable / configurable cases; the DB-backed
:func:`evaluate_quality_drift` is exercised against the real Postgres (RLS) for
the alert-fired / tenant-scoped cases. NO real LLM is ever touched.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

# Import the full domain ORM so SQLAlchemy resolves the eval FKs
# (eval_drift_state.dataset_id -> eval_datasets.id, etc.).
import api_server.db.domain  # noqa: F401
import asyncpg
import pytest
from alembic import command
from api_server.evals.constants import (
    DEFAULT_DRIFT_DROP_THRESHOLD,
    DEFAULT_DRIFT_WINDOW,
    DRIFT_DROP_THRESHOLD_ENV_VAR,
    DRIFT_WINDOW_ENV_VAR,
)
from api_server.evals.drift import (
    detect_drift,
    evaluate_quality_drift,
    resolve_drift_config,
)
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE eval_drift_state, eval_shadow_records, eval_results, eval_runs, "
            "eval_criteria, eval_dataset_items, eval_datasets, organizations "
            "RESTART IDENTITY CASCADE"
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
    dataset_id = uuid4()
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


async def _seed_run(
    dsn: str,
    *,
    tenant_id: UUID,
    dataset_id: UUID,
    pass_rate: Decimal,
    seq: int,
) -> UUID:
    """A COMPLETED eval run with the given pass rate.

    ``created_at`` is stamped by ``seq`` (later seq = newer) so the detector's
    ordering is deterministic regardless of insert speed — the drift loader
    orders by ``created_at DESC``.
    """
    run_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_runs "
            "(id, tenant_id, dataset_id, status, pass_rate, total_items, passed_items, "
            "created_at) "
            "VALUES ($1, $2, $3, 'completed', $4, 10, 0, now() + ($5 || ' seconds')::interval)",
            run_id,
            tenant_id,
            dataset_id,
            pass_rate,
            str(seq),
        )
    finally:
        await conn.close()
    return run_id


async def _count_drift_state(dsn: str, *, tenant_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM eval_drift_state WHERE tenant_id = $1", tenant_id
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


# ===========================================================================
# Pure detector — sustained decline, single dip, stable, configurable
# ===========================================================================
def test_sustained_decline_is_detected() -> None:
    # Three consecutive runs each drop by >= the 0.1 default threshold.
    rates = [Decimal("0.95"), Decimal("0.80"), Decimal("0.65"), Decimal("0.50")]
    decision = detect_drift(rates)
    assert decision.drifted is True
    assert decision.consecutive_declines >= DEFAULT_DRIFT_WINDOW
    assert decision.total_decline > 0


def test_single_dip_does_not_trigger() -> None:
    # One low run flanked by recovery — NOT a sustained decline. The latest
    # step actually RISES (0.55 -> 0.92), so the run of declines is 0.
    rates = [Decimal("0.90"), Decimal("0.92"), Decimal("0.55"), Decimal("0.92")]
    decision = detect_drift(rates)
    assert decision.drifted is False
    # Even a dip that ends on the low value but is only ONE declining step is
    # below the window requirement.
    rates2 = [Decimal("0.90"), Decimal("0.91"), Decimal("0.92"), Decimal("0.60")]
    decision2 = detect_drift(rates2)
    assert decision2.drifted is False
    assert decision2.consecutive_declines == 1


def test_stable_and_improving_quality_no_drift() -> None:
    stable = [Decimal("0.90"), Decimal("0.90"), Decimal("0.90"), Decimal("0.90")]
    assert detect_drift(stable).drifted is False
    improving = [Decimal("0.50"), Decimal("0.65"), Decimal("0.80"), Decimal("0.95")]
    assert detect_drift(improving).drifted is False
    # A gentle decline UNDER the per-step threshold is not drift either.
    gentle = [Decimal("0.90"), Decimal("0.88"), Decimal("0.86"), Decimal("0.84")]
    assert detect_drift(gentle, drop_threshold=Decimal("0.1")).drifted is False


def test_none_pass_rate_breaks_the_run() -> None:
    # An undefined pass rate (an empty run) is not a decline — it breaks the
    # sustained run rather than reading as zero.
    rates = [Decimal("0.95"), Decimal("0.80"), None, Decimal("0.50")]
    assert detect_drift(rates).drifted is False


def test_window_and_threshold_are_configurable() -> None:
    rates = [Decimal("0.95"), Decimal("0.80"), Decimal("0.65")]  # two declining steps
    # A window of 2 declares drift on this stream...
    assert detect_drift(rates, window=2, drop_threshold=Decimal("0.1")).drifted is True
    # ...while the default window of 3 needs three steps -> not enough -> no drift.
    assert detect_drift(rates, window=3, drop_threshold=Decimal("0.1")).drifted is False
    # A higher per-step threshold makes the same steps too small to count.
    assert detect_drift(rates, window=2, drop_threshold=Decimal("0.2")).drifted is False


def test_resolve_drift_config_precedence() -> None:
    # Default (nothing set) is the named constants — never magic numbers.
    cfg = resolve_drift_config(env={})
    assert cfg.window == DEFAULT_DRIFT_WINDOW
    assert cfg.drop_threshold == DEFAULT_DRIFT_DROP_THRESHOLD
    # Env vars override the defaults.
    cfg = resolve_drift_config(env={DRIFT_WINDOW_ENV_VAR: "5", DRIFT_DROP_THRESHOLD_ENV_VAR: "0.2"})
    assert cfg.window == 5
    assert cfg.drop_threshold == Decimal("0.2")
    # An explicit arg overrides BOTH the env var and the default.
    cfg = resolve_drift_config(window=2, env={DRIFT_WINDOW_ENV_VAR: "5"})
    assert cfg.window == 2
    # Out-of-range / non-numeric are rejected.
    with pytest.raises(ValueError):
        resolve_drift_config(window=0)
    with pytest.raises(ValueError):
        resolve_drift_config(drop_threshold=Decimal("1.5"))
    with pytest.raises(ValueError):
        resolve_drift_config(env={DRIFT_WINDOW_ENV_VAR: "abc"})


def test_invalid_window_raises() -> None:
    with pytest.raises(ValueError):
        detect_drift([Decimal("0.9")], window=0)
    with pytest.raises(ValueError):
        detect_drift([Decimal("0.9")], drop_threshold=Decimal("-0.1"))


# ===========================================================================
# DB-backed: a sustained decline fires exactly ONE alert (notification mocked)
# ===========================================================================
@pytest.mark.asyncio
async def test_sustained_decline_fires_one_alert(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme-drift")
    dataset = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant, name="ds")
    # Four completed runs, each dropping by 0.15 (>= the 0.1 default threshold)
    # for three consecutive steps — a sustained decline.
    for seq, rate in enumerate(["0.95", "0.80", "0.65", "0.50"]):
        await _seed_run(
            migrations_pg_dsn,
            tenant_id=tenant,
            dataset_id=dataset,
            pass_rate=Decimal(rate),
            seq=seq,
        )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        result = await evaluate_quality_drift(
            session,
            tenant_id=tenant,
            dataset_id=dataset,
            dispatcher=dispatcher,
        )
        await session.commit()

        assert result.decision.drifted is True
        assert result.alerted is True
        assert result.debounced is False
        # Exactly ONE alert, dispatched as a quality_drift_alert for the tenant.
        assert len(dispatcher.events) == 1
        event = dispatcher.events[0]
        assert event["event_type"] == "quality_drift_alert"
        assert event["tenant_id"] == str(tenant)
        ctx = event["context"]
        assert ctx["dataset_id"] == str(dataset)
        assert ctx["consecutive_declines"] >= DEFAULT_DRIFT_WINDOW
    finally:
        await session.close()
        await engine.dispose()

    # A drift-state row was stamped for the (tenant, dataset).
    assert await _count_drift_state(migrations_pg_dsn, tenant_id=tenant) == 1


# ===========================================================================
# DB-backed: a single dip does NOT alert; the debounce suppresses re-alerts
# ===========================================================================
@pytest.mark.asyncio
async def test_single_dip_does_not_alert_and_debounce_suppresses(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="dip-drift")
    dataset = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant, name="ds")
    # One dip then a full recovery — NOT sustained.
    for seq, rate in enumerate(["0.92", "0.90", "0.55", "0.93"]):
        await _seed_run(
            migrations_pg_dsn,
            tenant_id=tenant,
            dataset_id=dataset,
            pass_rate=Decimal(rate),
            seq=seq,
        )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        result = await evaluate_quality_drift(
            session, tenant_id=tenant, dataset_id=dataset, dispatcher=dispatcher
        )
        await session.commit()
        assert result.decision.drifted is False
        assert result.alerted is False
        assert dispatcher.events == []
    finally:
        await session.close()
        await engine.dispose()

    # No drift-state row needs creating when nothing drifted.
    assert await _count_drift_state(migrations_pg_dsn, tenant_id=tenant) == 0


@pytest.mark.asyncio
async def test_debounce_suppresses_second_alert(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from datetime import UTC, datetime, timedelta

    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="debounce-drift")
    dataset = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant, name="ds")
    for seq, rate in enumerate(["0.95", "0.80", "0.65", "0.50"]):
        await _seed_run(
            migrations_pg_dsn,
            tenant_id=tenant,
            dataset_id=dataset,
            pass_rate=Decimal(rate),
            seq=seq,
        )

    base = datetime.now(tz=UTC)
    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        # First evaluation fires.
        r1 = await evaluate_quality_drift(
            session, tenant_id=tenant, dataset_id=dataset, dispatcher=dispatcher, now=base
        )
        await session.commit()
        assert r1.alerted is True
        assert len(dispatcher.events) == 1

        # Second evaluation 1h later (inside the 1-day debounce) — still
        # drifting, but suppressed: at most one alert per debounce window.
        r2 = await evaluate_quality_drift(
            session,
            tenant_id=tenant,
            dataset_id=dataset,
            dispatcher=dispatcher,
            now=base + timedelta(hours=1),
        )
        await session.commit()
        assert r2.decision.drifted is True
        assert r2.alerted is False
        assert r2.debounced is True
        assert len(dispatcher.events) == 1  # still ONE — no spam
    finally:
        await session.close()
        await engine.dispose()


# ===========================================================================
# DB-backed: window/threshold configurable end-to-end
# ===========================================================================
@pytest.mark.asyncio
async def test_configurable_window_end_to_end(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="cfg-drift")
    dataset = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant, name="ds")
    # Only TWO declining steps available.
    for seq, rate in enumerate(["0.95", "0.80", "0.65"]):
        await _seed_run(
            migrations_pg_dsn,
            tenant_id=tenant,
            dataset_id=dataset,
            pass_rate=Decimal(rate),
            seq=seq,
        )

    engine, session = await _open_session(app_database_url, tenant)
    try:
        # Default window (3) needs three steps -> not enough -> no alert.
        d3 = _FakeDispatcher()
        r3 = await evaluate_quality_drift(
            session, tenant_id=tenant, dataset_id=dataset, dispatcher=d3
        )
        assert r3.decision.drifted is False
        assert d3.events == []

        # A configured window of 2 declares drift on the same stream + alerts.
        d2 = _FakeDispatcher()
        r2 = await evaluate_quality_drift(
            session, tenant_id=tenant, dataset_id=dataset, window=2, dispatcher=d2
        )
        await session.commit()
        assert r2.decision.drifted is True
        assert r2.alerted is True
        assert len(d2.events) == 1
    finally:
        await session.close()
        await engine.dispose()


# ===========================================================================
# Tenant-scoped: evaluating tenant B sees NONE of tenant A's declining runs
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_drift_is_tenant_scoped(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha-drift")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo-drift")
    dataset_a = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant_a, name="a-ds")
    # B owns its OWN dataset (same shape) but has NO declining runs.
    dataset_b = await _seed_dataset(migrations_pg_dsn, tenant_id=tenant_b, name="b-ds")

    # Only tenant A has a sustained decline.
    for seq, rate in enumerate(["0.95", "0.80", "0.65", "0.50"]):
        await _seed_run(
            migrations_pg_dsn,
            tenant_id=tenant_a,
            dataset_id=dataset_a,
            pass_rate=Decimal(rate),
            seq=seq,
        )

    # Evaluating tenant B over B's dataset sees NONE of A's runs -> no drift.
    dispatcher_b = _FakeDispatcher()
    engine_b, session_b = await _open_session(app_database_url, tenant_b)
    try:
        result_b = await evaluate_quality_drift(
            session_b, tenant_id=tenant_b, dataset_id=dataset_b, dispatcher=dispatcher_b
        )
        await session_b.commit()
        assert result_b.runs_considered == 0
        assert result_b.decision.drifted is False
        assert dispatcher_b.events == []
    finally:
        await session_b.close()
        await engine_b.dispose()

    # Evaluating tenant A fires exactly one alert scoped to tenant A.
    dispatcher_a = _FakeDispatcher()
    engine_a, session_a = await _open_session(app_database_url, tenant_a)
    try:
        result_a = await evaluate_quality_drift(
            session_a, tenant_id=tenant_a, dataset_id=dataset_a, dispatcher=dispatcher_a
        )
        await session_a.commit()
        assert result_a.alerted is True
        assert len(dispatcher_a.events) == 1
        assert dispatcher_a.events[0]["tenant_id"] == str(tenant_a)
    finally:
        await session_a.close()
        await engine_a.dispose()

    # B has NO drift-state row (A's decline never touched B).
    assert await _count_drift_state(migrations_pg_dsn, tenant_id=tenant_b) == 0
    assert await _count_drift_state(migrations_pg_dsn, tenant_id=tenant_a) == 1
