"""Integration tests for the per-model_call price snapshot (Plan 11 task_11_13).

The platform's "model call" record is a ``model_call`` step dict inside an
``executions.steps_log`` JSONB array. task_11_13 freezes the catalog price
that was IN EFFECT when the call was recorded onto each such step (and a
representative roll-up onto the ``executions`` row) so historical billing
stays correct after the ``model_prices`` catalog changes.

Verified end-to-end against the real Postgres (migration 0050 columns +
the global-read price catalog of migration 0049):

  - recording an execution snapshots, per model_call step, the unit prices
    in effect + ``price_snapshot_at`` + a computed ``cost_usd``, and stamps
    the execution's snapshot columns;
  - a LATER catalog price change does NOT alter the historical snapshot
    (the frozen JSONB + columns keep the old numbers);
  - ``cached_input_tokens`` are billed at the cached rate (not the full
    input rate);
  - a MISSING catalog price is recorded as a typed *unknown*
    (``available=False``, NULL cost) — never a fake zero;
  - the executions tenant RLS is preserved (a tenant session sees only its
    own execution + snapshot; another tenant cannot).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.execution_repo import get_execution, record_execution
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PROVIDER = "anthropic"
_MODEL = "claude-sonnet-4-5"


# ---------------------------------------------------------------------------
# A minimal ExecutionResultLike (duck-typed) — we drive the snapshot seam
# directly with a known steps_log rather than spinning the full runtime.
# ---------------------------------------------------------------------------
@dataclass
class _FakeResult:
    steps: list[dict[str, Any]]
    status: str = "done"
    abort_code: str | None = None
    output: str | None = "ok"
    iterations: int = 1
    usage: dict[str, Any] = field(default_factory=dict)


def _model_call_step(
    *,
    index: int = 0,
    provider: str = _PROVIDER,
    model: str = _MODEL,
    tokens_in: int = 1_000_000,
    tokens_out: int = 1_000_000,
    cached_input_tokens: int = 0,
) -> dict[str, Any]:
    """A canonical model_call step (with provider + optional cached tokens)."""
    now = datetime.now(tz=UTC).isoformat()
    step: dict[str, Any] = {
        "index": index,
        "kind": "model_call",
        "node": "plan",
        "status": "ok",
        "summary": "llm call",
        "started_at": now,
        "ended_at": now,
        "provider": provider,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "total_tokens": tokens_in + tokens_out,
        "cost_usd": 0.0,
    }
    if cached_input_tokens:
        step["cached_input_tokens"] = cached_input_tokens
    return step


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_task_and_price(
    sm: async_sessionmaker,
    *,
    input_price: Decimal | None = Decimal("3.0"),
    output_price: Decimal = Decimal("15.0"),
    cached_input_price: Decimal | None = Decimal("0.30"),
    seed_price: bool = True,
) -> dict[str, UUID]:
    """Truncate, seed one tenant/project/task and (optionally) a current price."""
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, projects,"
                " organizations, model_prices RESTART IDENTITY CASCADE"
            )
        )
        await s.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:i, :n, :sl)"),
            {"i": ids["tenant"], "n": "Snapshot tenant", "sl": f"snap-{ids['tenant'].hex[:8]}"},
        )
        await s.execute(
            text(
                "INSERT INTO projects (id, tenant_id, name, status, is_template)"
                " VALUES (:i, :t, 'Snapshot project', 'active', false)"
            ),
            {"i": ids["project"], "t": ids["tenant"]},
        )
        await s.execute(
            text(
                "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
                " VALUES (:i, :t, :p, 'Snapshot task', 'backlog', 'medium')"
            ),
            {"i": ids["task"], "t": ids["tenant"], "p": ids["project"]},
        )
        if seed_price:
            await s.execute(
                text(
                    "INSERT INTO model_prices"
                    " (id, provider, model_id, modality, input_price, output_price,"
                    "  cached_input_price)"
                    " VALUES (:i, :prov, :m, 'text', :inp, :out, :cached)"
                ),
                {
                    "i": uuid4(),
                    "prov": _PROVIDER,
                    "m": _MODEL,
                    "inp": input_price,
                    "out": output_price,
                    "cached": cached_input_price,
                },
            )
    return ids


def _first_model_call(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return next(s for s in steps if s.get("kind") == "model_call")


# ===========================================================================
# A model_call records the price in effect + price_snapshot_at + cost
# ===========================================================================
@pytest.mark.asyncio
async def test_model_call_records_price_snapshot_and_cost(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task_and_price(sm)

        # 1M input + 1M output tokens at 3/15 USD per 1M = 3 + 15 = 18 USD.
        result = _FakeResult(steps=[_model_call_step(tokens_in=1_000_000, tokens_out=1_000_000)])
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None

        # Per-call snapshot frozen into the JSONB step.
        snap = _first_model_call(loaded.steps_log)["price_snapshot"]
        assert snap["available"] is True
        assert snap["currency"] == "USD"
        assert snap["price_snapshot_at"] is not None
        assert Decimal(snap["input_price"]) == Decimal("3.0")
        assert Decimal(snap["output_price"]) == Decimal("15.0")
        assert Decimal(snap["cost_usd"]) == Decimal("18.000000")

        # Execution-level roll-up columns stamped.
        assert loaded.price_snapshot_at is not None
        assert loaded.price_snapshot_currency == "USD"
        assert loaded.price_input_usd == Decimal("3.0000000000")
        assert loaded.price_output_usd == Decimal("15.0000000000")
        assert loaded.price_snapshot_cost_usd == Decimal("18.000000")
    finally:
        await engine.dispose()


# ===========================================================================
# AUD16-15: la clave del RUNTIME (kind + modelo nativo) resuelve el catálogo.
# Los steps de producción llevaban provider="" (o el KIND claude_sdk/ollama…)
# mientras el catálogo LiteLLM nombra por familia (anthropic, ollama/<m>…):
# price_snapshot_cost_usd quedó NULL en 128/128 executions.
# ===========================================================================
async def _seed_extra_price(
    sm: async_sessionmaker,
    *,
    provider: str,
    model_id: str,
    input_price: Decimal = Decimal("1.0"),
    output_price: Decimal = Decimal("2.0"),
) -> None:
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO model_prices"
                " (id, provider, model_id, modality, input_price, output_price)"
                " VALUES (:i, :prov, :m, 'text', :inp, :out)"
            ),
            {
                "i": uuid4(),
                "prov": provider,
                "m": model_id,
                "inp": input_price,
                "out": output_price,
            },
        )


@pytest.mark.asyncio
async def test_runtime_kind_resolves_catalog_provider_alias(
    _migrated: None, admin_database_url: str
) -> None:
    """provider='claude_sdk' (el kind que registra el runtime) casa con la fila
    anthropic/<model> del catálogo."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task_and_price(sm)  # anthropic / claude-sonnet-4-5

        result = _FakeResult(steps=[_model_call_step(provider="claude_sdk")])
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id
        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        snap = _first_model_call(loaded.steps_log)["price_snapshot"]
        assert snap["available"] is True, snap
        assert loaded.price_snapshot_cost_usd is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_step_without_provider_prices_via_unique_model_match(
    _migrated: None, admin_database_url: str
) -> None:
    """Steps históricos sin provider: si el model_id casa con EXACTAMENTE una
    fila current del catálogo, se usa — nunca se adivina entre varias."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task_and_price(sm)

        result = _FakeResult(steps=[_model_call_step(provider="")])
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id
        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        snap = _first_model_call(loaded.steps_log)["price_snapshot"]
        assert snap["available"] is True, snap
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ambiguous_model_only_match_stays_unknown(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task_and_price(sm)
        # Mismo model_id bajo OTRO provider → el match por-modelo deja de ser único.
        await _seed_extra_price(sm, provider="otherprov", model_id=_MODEL)

        result = _FakeResult(steps=[_model_call_step(provider="")])
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id
        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        snap = _first_model_call(loaded.steps_log)["price_snapshot"]
        assert snap["available"] is False, snap
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ollama_kind_matches_litellm_prefixed_model_id(
    _migrated: None, admin_database_url: str
) -> None:
    """El catálogo guarda ids estilo LiteLLM ('ollama/<m>'); el runtime registra
    el nombre nativo pelado — el lookup prueba también el id prefijado."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task_and_price(sm, seed_price=False)
        await _seed_extra_price(sm, provider="ollama", model_id="ollama/gpt-oss:120b")

        result = _FakeResult(steps=[_model_call_step(provider="ollama", model="gpt-oss:120b")])
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id
        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        snap = _first_model_call(loaded.steps_log)["price_snapshot"]
        assert snap["available"] is True, snap
    finally:
        await engine.dispose()


# ===========================================================================
# A later catalog price change does NOT alter the historical snapshot
# ===========================================================================
@pytest.mark.asyncio
async def test_later_price_change_does_not_alter_snapshot(
    _migrated: None, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task_and_price(sm)

        result = _FakeResult(steps=[_model_call_step(tokens_in=1_000_000, tokens_out=1_000_000)])
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        # Catalog price changes AFTER the call was recorded: close the open
        # period and open a far pricier one.
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "UPDATE model_prices SET effective_to = now()"
                " WHERE provider = $1 AND model_id = $2 AND effective_to IS NULL",
                _PROVIDER,
                _MODEL,
            )
            await conn.execute(
                "INSERT INTO model_prices"
                " (id, provider, model_id, modality, input_price, output_price)"
                " VALUES ($1, $2, $3, 'text', 30.0, 150.0)",
                uuid4(),
                _PROVIDER,
                _MODEL,
            )
        finally:
            await conn.close()

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        # The historical snapshot keeps the OLD numbers — 18, not 180.
        snap = _first_model_call(loaded.steps_log)["price_snapshot"]
        assert Decimal(snap["cost_usd"]) == Decimal("18.000000")
        assert Decimal(snap["input_price"]) == Decimal("3.0")
        assert loaded.price_snapshot_cost_usd == Decimal("18.000000")
    finally:
        await engine.dispose()


# ===========================================================================
# cached_input tokens are priced at the cached rate
# ===========================================================================
@pytest.mark.asyncio
async def test_cached_input_tokens_priced_at_cached_rate(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        # input 3/1M, cached 0.30/1M, output 15/1M.
        ids = await _seed_task_and_price(sm, cached_input_price=Decimal("0.30"))

        # 1M input of which 1M cached, 0 output:
        #   full input = 0 tokens * 3   = 0
        #   cached     = 1M tokens * 0.30 = 0.30
        # So an all-cached call costs the cached rate, NOT the full 3.0.
        result = _FakeResult(
            steps=[
                _model_call_step(tokens_in=1_000_000, tokens_out=0, cached_input_tokens=1_000_000)
            ]
        )
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        snap = _first_model_call(loaded.steps_log)["price_snapshot"]
        # Cached rate applied → 0.30, decisively below the full-input 3.0.
        assert Decimal(snap["cost_usd"]) == Decimal("0.300000")
        assert Decimal(snap["cached_input_price"]) == Decimal("0.30")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cached_rate_defaults_to_ten_percent_when_unpriced(
    _migrated: None, admin_database_url: str
) -> None:
    """When the catalog does not price cache reads (NULL cached_input_price),
    cached tokens fall back to ~10% of the input price (helper convention)."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task_and_price(sm, input_price=Decimal("3.0"), cached_input_price=None)

        # 1M cached input, no full input, no output. 10% of 3.0 = 0.30.
        result = _FakeResult(
            steps=[
                _model_call_step(tokens_in=1_000_000, tokens_out=0, cached_input_tokens=1_000_000)
            ]
        )
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        snap = _first_model_call(loaded.steps_log)["price_snapshot"]
        assert Decimal(snap["cost_usd"]) == Decimal("0.300000")
        # The catalog stored NULL — the per-call snapshot reflects that the
        # provider did not price cache reads separately.
        assert snap["cached_input_price"] is None
    finally:
        await engine.dispose()


# ===========================================================================
# A missing price is recorded as unknown, not zero/fake
# ===========================================================================
@pytest.mark.asyncio
async def test_missing_price_recorded_as_unknown_not_zero(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        # No price seeded for the call's key.
        ids = await _seed_task_and_price(sm, seed_price=False)

        result = _FakeResult(steps=[_model_call_step(tokens_in=1_000_000, tokens_out=1_000_000)])
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        snap = _first_model_call(loaded.steps_log)["price_snapshot"]
        # Typed UNKNOWN — no fabricated zero cost, no fake unit prices.
        assert snap["available"] is False
        assert "cost_usd" not in snap
        assert "input_price" not in snap
        assert snap["reason"]
        # Snapshot timestamp still recorded; cost column left NULL (unknown).
        assert loaded.price_snapshot_at is not None
        assert loaded.price_snapshot_cost_usd is None
        assert loaded.price_input_usd is None
    finally:
        await engine.dispose()


# ===========================================================================
# Tenant-scoped: the snapshot lives on the tenant-scoped execution (RLS)
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_snapshot_is_tenant_scoped(
    _migrated: None, admin_database_url: str, app_database_url: str
) -> None:
    """The execution (and its snapshot) is tenant-scoped: a tenant session
    sees only its own row, another tenant sees nothing — the snapshot
    columns inherit the executions RLS. The price catalog read itself is
    global, but what it writes onto is tenant-isolated."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()

    engine = create_async_engine(admin_database_url)
    app_engine = create_async_engine(app_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_task_and_price(sm)

        result = _FakeResult(steps=[_model_call_step()])
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        app_sm = async_sessionmaker(app_engine, expire_on_commit=False)

        # The owning tenant session sees the execution + its snapshot.
        async with app_sm() as s, s.begin():
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(ids["tenant"])},
            )
            own = await get_execution(s, execution_id)
            assert own is not None
            assert own.price_snapshot_cost_usd is not None
            snap = _first_model_call(own.steps_log)["price_snapshot"]
            assert snap["available"] is True

        # A DIFFERENT tenant session cannot see it (RLS filters it out).
        async with app_sm() as s, s.begin():
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(uuid4())},
            )
            other = await get_execution(s, execution_id)
            assert other is None
    finally:
        await engine.dispose()
        await app_engine.dispose()
