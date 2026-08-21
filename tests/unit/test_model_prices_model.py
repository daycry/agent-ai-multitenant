"""Unit tests for the global model price catalog ORM (Plan 11 task_11_10).

In-process, no database. We pin the column shape, enum values, the
platform-global (no tenant_id) tenancy decision, the USD-canonical
currency, the nullable-but-sensible prompt-caching price, the
effective-dating fields, and the current-price selection helper. The
migration + (no-)RLS are exercised later in
``tests/integration/test_prices_migration.py`` (task_11_11).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from api_server.db.model_prices import (
    CANONICAL_CURRENCY,
    ModelPrice,
    PriceModality,
    PriceSource,
    PriceUnit,
    select_current_price,
)
from sqlalchemy import CheckConstraint, UniqueConstraint

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Enums (StrEnum -> stable TEXT values)
# ---------------------------------------------------------------------------
def test_modality_enum_values() -> None:
    assert {m.value for m in PriceModality} == {
        "text",
        "vision",
        "audio",
        "embedding",
        "image",
        "rerank",
    }


def test_source_enum_values() -> None:
    """The task names litellm | manual | provider_api as the sources."""
    assert {s.value for s in PriceSource} == {
        "litellm",
        "provider_api",
        "manual",
    }


def test_unit_enum_values() -> None:
    assert {u.value for u in PriceUnit} == {
        "per_1m_tokens",
        "per_1k_tokens",
    }


def test_enums_are_string_valued() -> None:
    assert PriceModality.TEXT == "text"
    assert PriceSource.LITELLM == "litellm"
    assert PriceUnit.PER_1M_TOKENS == "per_1m_tokens"


# ---------------------------------------------------------------------------
# Table shape + columns
# ---------------------------------------------------------------------------
def test_table_name_and_columns() -> None:
    assert ModelPrice.__tablename__ == "model_prices"
    cols = {c.name for c in ModelPrice.__table__.columns}
    assert {
        "id",
        "provider",
        "model_id",
        "modality",
        "input_price",
        "output_price",
        "cached_input_price",
        "unit",
        "currency",
        "context_window",
        "source",
        "effective_from",
        "effective_to",
        "updated_by",
        "created_at",
        "updated_at",
    } <= cols


# ---------------------------------------------------------------------------
# Tenancy: platform-global. No tenant_id, no soft-delete.
# ---------------------------------------------------------------------------
def test_catalog_is_platform_global_no_tenant_id() -> None:
    """The price catalog is platform-global (ADR 0028): it carries no
    tenant_id column — writes are System-Admin-gated, reads are open."""
    cols = {c.name for c in ModelPrice.__table__.columns}
    assert "tenant_id" not in cols


def test_catalog_is_not_soft_deleted() -> None:
    """History is expressed by closing effective periods, not deleting."""
    cols = {c.name for c in ModelPrice.__table__.columns}
    assert "deleted_at" not in cols


def test_updated_by_fk_sets_null_on_user_delete() -> None:
    """The price outlives the System Admin who wrote it (no tenant_id)."""
    fks = list(ModelPrice.__table__.columns["updated_by"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "SET NULL"
    assert ModelPrice.__table__.columns["updated_by"].nullable is True


# ---------------------------------------------------------------------------
# USD-canonical currency
# ---------------------------------------------------------------------------
def test_currency_constant_is_usd() -> None:
    assert CANONICAL_CURRENCY == "USD"


def test_currency_column_defaults_to_usd() -> None:
    server_default = ModelPrice.__table__.columns["currency"].server_default
    assert server_default is not None
    assert "USD" in str(server_default.arg.text)


def test_currency_check_constraint_pins_usd() -> None:
    checks = {c.name for c in ModelPrice.__table__.constraints if isinstance(c, CheckConstraint)}
    assert "ck_model_prices_currency_usd" in checks


def test_constructed_row_uses_usd_by_convention() -> None:
    price = ModelPrice(
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        modality=PriceModality.TEXT,
        input_price=Decimal("3.0"),
        output_price=Decimal("15.0"),
        currency=CANONICAL_CURRENCY,
    )
    assert price.currency == "USD"


# ---------------------------------------------------------------------------
# Prompt-caching price: nullable + sensible default helper
# ---------------------------------------------------------------------------
def test_cached_input_price_is_nullable() -> None:
    assert ModelPrice.__table__.columns["cached_input_price"].nullable is True


def test_cached_input_price_helper_uses_explicit_value_when_set() -> None:
    price = ModelPrice(
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        modality=PriceModality.TEXT,
        input_price=Decimal("3.0"),
        output_price=Decimal("15.0"),
        cached_input_price=Decimal("0.3"),
    )
    assert price.cached_input_price_or_default() == Decimal("0.3")


def test_cached_input_price_helper_falls_back_to_ten_percent() -> None:
    """NULL cache price -> convention ~10% of input price."""
    price = ModelPrice(
        provider="openai",
        model_id="gpt-4o",
        modality=PriceModality.TEXT,
        input_price=Decimal("2.50"),
        output_price=Decimal("10.0"),
        cached_input_price=None,
    )
    assert price.cached_input_price_or_default() == Decimal("0.250")


def test_cached_input_price_helper_custom_fraction() -> None:
    price = ModelPrice(
        provider="openai",
        model_id="gpt-4o",
        modality=PriceModality.TEXT,
        input_price=Decimal("2.0"),
        output_price=Decimal("8.0"),
        cached_input_price=None,
    )
    assert price.cached_input_price_or_default(fraction=Decimal("0.25")) == Decimal("0.50")


# ---------------------------------------------------------------------------
# Effective dating + current-period uniqueness
# ---------------------------------------------------------------------------
def test_effective_dating_columns() -> None:
    eff_from = ModelPrice.__table__.columns["effective_from"]
    eff_to = ModelPrice.__table__.columns["effective_to"]
    assert eff_from.nullable is False
    assert eff_to.nullable is True  # NULL == current/open period


def test_is_current_reflects_open_period() -> None:
    open_row = ModelPrice(
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        modality=PriceModality.TEXT,
        input_price=Decimal("3.0"),
        output_price=Decimal("15.0"),
        effective_to=None,
    )
    closed_row = ModelPrice(
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        modality=PriceModality.TEXT,
        input_price=Decimal("3.0"),
        output_price=Decimal("15.0"),
        effective_to=datetime(2025, 1, 1, tzinfo=UTC),
    )
    assert open_row.is_current is True
    assert closed_row.is_current is False


def test_current_period_has_partial_unique_index() -> None:
    """At most one open period per (provider, model_id, modality)."""
    idx = {i.name: i for i in ModelPrice.__table__.indexes}
    assert "uq_model_prices_current" in idx
    current_idx = idx["uq_model_prices_current"]
    assert current_idx.unique is True
    assert {c.name for c in current_idx.columns} == {"provider", "model_id", "modality"}


def test_period_start_unique_constraint() -> None:
    uniques = {c.name for c in ModelPrice.__table__.constraints if isinstance(c, UniqueConstraint)}
    assert "uq_model_prices_period_start" in uniques


def test_period_validity_check_constraint() -> None:
    checks = {c.name for c in ModelPrice.__table__.constraints if isinstance(c, CheckConstraint)}
    assert "ck_model_prices_period_valid" in checks
    assert "ck_model_prices_input_non_negative" in checks
    assert "ck_model_prices_output_non_negative" in checks


# ---------------------------------------------------------------------------
# Construction with all attrs
# ---------------------------------------------------------------------------
def test_full_construction() -> None:
    uid = uuid4()
    price = ModelPrice(
        provider="anthropic",
        model_id="claude-opus-4",
        modality=PriceModality.VISION,
        input_price=Decimal("15.0"),
        output_price=Decimal("75.0"),
        cached_input_price=Decimal("1.5"),
        unit=PriceUnit.PER_1M_TOKENS,
        currency=CANONICAL_CURRENCY,
        context_window=200_000,
        source=PriceSource.LITELLM,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_to=None,
        updated_by=uid,
    )
    assert price.provider == "anthropic"
    assert price.modality == "vision"
    assert price.unit == "per_1m_tokens"
    assert price.source == "litellm"
    assert price.context_window == 200_000
    assert price.updated_by == uid
    assert price.is_current is True


def test_column_server_defaults() -> None:
    for col_name in ("modality", "unit", "currency", "source", "effective_from"):
        assert ModelPrice.__table__.columns[col_name].server_default is not None, (
            f"{col_name} should carry a server default"
        )


# ---------------------------------------------------------------------------
# select_current_price helper (pure, no DB)
# ---------------------------------------------------------------------------
def _row(
    *,
    provider: str = "anthropic",
    model_id: str = "claude-sonnet-4-5",
    modality: str = "text",
    effective_from: datetime,
    effective_to: datetime | None,
) -> ModelPrice:
    return ModelPrice(
        provider=provider,
        model_id=model_id,
        modality=modality,
        input_price=Decimal("3.0"),
        output_price=Decimal("15.0"),
        effective_from=effective_from,
        effective_to=effective_to,
    )


def test_select_current_price_picks_open_period() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    closed = _row(
        effective_from=now - timedelta(days=60),
        effective_to=now - timedelta(days=30),
    )
    current = _row(effective_from=now - timedelta(days=30), effective_to=None)
    chosen = select_current_price(
        [closed, current],
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        modality=PriceModality.TEXT,
    )
    assert chosen is current


def test_select_current_price_returns_none_without_open_period() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    closed = _row(
        effective_from=now - timedelta(days=60),
        effective_to=now - timedelta(days=30),
    )
    assert (
        select_current_price(
            [closed],
            provider="anthropic",
            model_id="claude-sonnet-4-5",
        )
        is None
    )


def test_select_current_price_filters_by_key() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    other_model = _row(
        model_id="claude-haiku-4",
        effective_from=now - timedelta(days=10),
        effective_to=None,
    )
    other_modality = _row(
        modality="vision",
        effective_from=now - timedelta(days=10),
        effective_to=None,
    )
    target = _row(effective_from=now - timedelta(days=5), effective_to=None)
    chosen = select_current_price(
        [other_model, other_modality, target],
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        modality=PriceModality.TEXT,
    )
    assert chosen is target


def test_select_current_price_prefers_latest_open_period_defensively() -> None:
    """If two open periods somehow coexist (DB index forbids it), pick the
    newest effective_from so a caller never uses a stale row."""
    now = datetime(2026, 5, 1, tzinfo=UTC)
    older_open = _row(effective_from=now - timedelta(days=30), effective_to=None)
    newer_open = _row(effective_from=now - timedelta(days=1), effective_to=None)
    chosen = select_current_price(
        [older_open, newer_open],
        provider="anthropic",
        model_id="claude-sonnet-4-5",
    )
    assert chosen is newer_open


def test_select_current_price_accepts_string_modality() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    target = _row(effective_from=now, effective_to=None)
    chosen = select_current_price(
        [target],
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        modality="text",
    )
    assert chosen is target
