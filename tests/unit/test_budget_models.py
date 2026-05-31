"""Unit tests for the budget model fields + period helper + alert thresholds
(Plan 11.1 task_11_1_04).

No database is touched. These tests verify:

  - ``Organization`` carries the tenant-level budget columns (amount,
    currency, period, period_start_day, period_length_days) with the right
    SQL types, all nullable (a tenant is "unbudgeted" by default).
  - ``Project`` carries the peer project-level budget columns + the
    ``paused_by_budget`` flag that defaults to ``false`` (added in 0002,
    re-asserted here as the budget contract).
  - The period helper :func:`current_budget_period` computes the correct
    half-open ``[start, end)`` window for every ``BudgetPeriod`` — including
    the ``custom`` start_day / length_days arithmetic and its validation.
  - The platform-global alert thresholds read from settings with the
    ``[80, 90, 100]`` default and normalise correctly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from api_server.budgets import (
    BudgetPeriodWindow,
    InvalidBudgetPeriodError,
    current_budget_period,
)
from api_server.db.domain import BudgetPeriod, Project
from api_server.db.models import Organization
from api_server.db.platform_settings import (
    BUDGET_ALERT_THRESHOLDS_KEY,
    DEFAULT_BUDGET_ALERT_THRESHOLDS,
    InvalidBudgetThresholdsError,
    get_budget_alert_thresholds,
    validate_budget_alert_thresholds,
)
from sqlalchemy.types import Integer, Numeric, String

# ---------------------------------------------------------------------------
# Organization — tenant-level budget columns
# ---------------------------------------------------------------------------
_ORG_BUDGET_COLUMNS = (
    "tenant_budget_amount",
    "tenant_budget_currency",
    "tenant_budget_period",
    "tenant_budget_period_start_day",
    "tenant_budget_period_length_days",
)


def test_organization_has_tenant_budget_columns() -> None:
    cols = Organization.__table__.columns
    for name in _ORG_BUDGET_COLUMNS:
        assert name in cols, f"Organization missing column {name}"


def test_organization_budget_column_types() -> None:
    cols = Organization.__table__.columns
    amount = cols["tenant_budget_amount"].type
    assert isinstance(amount, Numeric)
    assert amount.precision == 14
    assert amount.scale == 2
    assert isinstance(cols["tenant_budget_currency"].type, String)
    assert cols["tenant_budget_currency"].type.length == 3
    assert isinstance(cols["tenant_budget_period"].type, String)
    assert isinstance(cols["tenant_budget_period_start_day"].type, Integer)
    assert isinstance(cols["tenant_budget_period_length_days"].type, Integer)


def test_organization_budget_columns_are_nullable_no_default() -> None:
    """A tenant is "unbudgeted" by default — every budget column is nullable
    with no server_default (no backfill on existing rows)."""
    cols = Organization.__table__.columns
    for name in _ORG_BUDGET_COLUMNS:
        assert cols[name].nullable is True, f"{name} must be nullable"
        assert cols[name].server_default is None, f"{name} must have no server_default"


def test_organization_budget_non_negative_check_present() -> None:
    check_names = {c.name for c in Organization.__table__.constraints if c.name}
    assert "ck_organizations_tenant_budget_non_negative" in check_names


def test_organization_budget_defaults_unset_in_instance() -> None:
    """A freshly constructed org has no budget configured (all None)."""
    org = Organization(name="Acme", slug="acme")
    for name in _ORG_BUDGET_COLUMNS:
        assert getattr(org, name) is None


def test_organization_budget_fields_assignable() -> None:
    org = Organization(
        name="Acme",
        slug="acme",
        tenant_budget_amount=Decimal("1000.00"),
        tenant_budget_currency="EUR",
        tenant_budget_period=BudgetPeriod.MONTHLY.value,
        tenant_budget_period_start_day=None,
        tenant_budget_period_length_days=None,
    )
    assert org.tenant_budget_amount == Decimal("1000.00")
    assert org.tenant_budget_currency == "EUR"
    assert org.tenant_budget_period == "monthly"


# ---------------------------------------------------------------------------
# Project — peer project-level budget columns + paused_by_budget default
# ---------------------------------------------------------------------------
_PROJECT_BUDGET_COLUMNS = (
    "budget_amount",
    "budget_currency",
    "budget_period",
    "budget_period_start_day",
    "budget_period_length_days",
    "paused_by_budget",
)


def test_project_has_budget_columns() -> None:
    cols = Project.__table__.columns
    for name in _PROJECT_BUDGET_COLUMNS:
        assert name in cols, f"Project missing column {name}"


def test_project_budget_currency_field() -> None:
    cols = Project.__table__.columns
    assert isinstance(cols["budget_currency"].type, String)
    assert cols["budget_currency"].type.length == 3
    amount = cols["budget_amount"].type
    assert isinstance(amount, Numeric)
    assert amount.precision == 14
    assert amount.scale == 2


def test_project_paused_by_budget_defaults_false() -> None:
    cols = Project.__table__.columns
    paused = cols["paused_by_budget"]
    assert paused.nullable is False
    # Server default renders to 'false' (column-level NOT NULL DEFAULT false).
    assert paused.server_default is not None
    assert "false" in str(paused.server_default.arg.text).lower()


# ---------------------------------------------------------------------------
# Period helper — the active budget window per period
# ---------------------------------------------------------------------------
def test_window_is_half_open() -> None:
    """[start, end): start IN, end EXCLUSIVE — consecutive windows tile."""
    w = current_budget_period(BudgetPeriod.MONTHLY, on_date=date(2026, 5, 15))
    assert isinstance(w, BudgetPeriodWindow)
    assert w.contains(w.start)
    assert not w.contains(w.end)


def test_weekly_period_is_iso_week_monday_to_monday() -> None:
    # 2026-05-15 is a Friday; its ISO week starts Mon 2026-05-11.
    w = current_budget_period(BudgetPeriod.WEEKLY, on_date=date(2026, 5, 15))
    assert w.start == date(2026, 5, 11)
    assert w.end == date(2026, 5, 18)
    assert w.start.weekday() == 0  # Monday


def test_monthly_period_first_to_first() -> None:
    w = current_budget_period(BudgetPeriod.MONTHLY, on_date=date(2026, 5, 15))
    assert w.start == date(2026, 5, 1)
    assert w.end == date(2026, 6, 1)


def test_monthly_period_rolls_over_year() -> None:
    w = current_budget_period(BudgetPeriod.MONTHLY, on_date=date(2026, 12, 31))
    assert w.start == date(2026, 12, 1)
    assert w.end == date(2027, 1, 1)


@pytest.mark.parametrize(
    "on_date,exp_start,exp_end",
    [
        (date(2026, 1, 1), date(2026, 1, 1), date(2026, 4, 1)),  # Q1
        (date(2026, 5, 15), date(2026, 4, 1), date(2026, 7, 1)),  # Q2
        (date(2026, 8, 1), date(2026, 7, 1), date(2026, 10, 1)),  # Q3
        (date(2026, 12, 31), date(2026, 10, 1), date(2027, 1, 1)),  # Q4
    ],
)
def test_quarterly_period(on_date: date, exp_start: date, exp_end: date) -> None:
    w = current_budget_period(BudgetPeriod.QUARTERLY, on_date=on_date)
    assert w.start == exp_start
    assert w.end == exp_end


def test_yearly_period() -> None:
    w = current_budget_period(BudgetPeriod.YEARLY, on_date=date(2026, 5, 15))
    assert w.start == date(2026, 1, 1)
    assert w.end == date(2027, 1, 1)


def test_custom_period_start_day_in_same_month() -> None:
    # start_day=10, length=30. on_date 2026-05-15 -> cycle anchored 2026-05-10.
    w = current_budget_period(
        BudgetPeriod.CUSTOM,
        start_day=10,
        length_days=30,
        on_date=date(2026, 5, 15),
    )
    assert w.start == date(2026, 5, 10)
    assert w.end == date(2026, 6, 9)  # 10 May + 30 days
    assert w.contains(date(2026, 5, 15))


def test_custom_period_before_start_day_uses_previous_month() -> None:
    # start_day=20, length=30. on_date 2026-05-05 is BEFORE the 20th, so the
    # active cycle began on 2026-04-20.
    w = current_budget_period(
        BudgetPeriod.CUSTOM,
        start_day=20,
        length_days=30,
        on_date=date(2026, 5, 5),
    )
    assert w.start == date(2026, 4, 20)
    assert w.end == date(2026, 5, 20)
    assert w.contains(date(2026, 5, 5))


def test_custom_period_walks_forward_multiple_cycles() -> None:
    # start_day=1, length=7 (weekly-ish). on_date 2026-05-25 -> the cycle that
    # started 2026-05-01 + n*7 covering the 25th: 22..29.
    w = current_budget_period(
        BudgetPeriod.CUSTOM,
        start_day=1,
        length_days=7,
        on_date=date(2026, 5, 25),
    )
    assert w.start == date(2026, 5, 22)
    assert w.end == date(2026, 5, 29)


def test_custom_period_start_day_clamped_to_month_length() -> None:
    # start_day=31 in February clamps to the last day of Feb (28 in 2026).
    w = current_budget_period(
        BudgetPeriod.CUSTOM,
        start_day=31,
        length_days=15,
        on_date=date(2026, 2, 28),
    )
    assert w.start == date(2026, 2, 28)
    assert w.end == date(2026, 3, 15)


def test_custom_period_requires_start_day_and_length() -> None:
    with pytest.raises(InvalidBudgetPeriodError):
        current_budget_period(BudgetPeriod.CUSTOM, on_date=date(2026, 5, 15))
    with pytest.raises(InvalidBudgetPeriodError):
        current_budget_period(BudgetPeriod.CUSTOM, start_day=10, on_date=date(2026, 5, 15))


@pytest.mark.parametrize("bad_start_day", [0, 32, -1])
def test_custom_period_rejects_bad_start_day(bad_start_day: int) -> None:
    with pytest.raises(InvalidBudgetPeriodError):
        current_budget_period(
            BudgetPeriod.CUSTOM,
            start_day=bad_start_day,
            length_days=30,
            on_date=date(2026, 5, 15),
        )


def test_custom_period_rejects_non_positive_length() -> None:
    with pytest.raises(InvalidBudgetPeriodError):
        current_budget_period(
            BudgetPeriod.CUSTOM,
            start_day=1,
            length_days=0,
            on_date=date(2026, 5, 15),
        )


def test_period_helper_accepts_string_value() -> None:
    """A stored string period value (not the enum) resolves identically."""
    w = current_budget_period("monthly", on_date=date(2026, 5, 15))
    assert w.start == date(2026, 5, 1)


def test_period_helper_rejects_unknown_period() -> None:
    with pytest.raises(InvalidBudgetPeriodError):
        current_budget_period("fortnightly", on_date=date(2026, 5, 15))


# ---------------------------------------------------------------------------
# Platform-global alert thresholds — default [80, 90, 100], read from settings
# ---------------------------------------------------------------------------
def test_default_thresholds_constant() -> None:
    assert DEFAULT_BUDGET_ALERT_THRESHOLDS == (80, 90, 100)


def test_validate_thresholds_normalises_and_sorts() -> None:
    assert validate_budget_alert_thresholds([90, 80]) == [80, 90, 100]


def test_validate_thresholds_dedupes_and_keeps_pause_arm() -> None:
    # 100 (pause arm) is always present even if not supplied; dupes collapse.
    assert validate_budget_alert_thresholds([50, 50, 75]) == [50, 75, 100]


def test_validate_thresholds_rejects_empty() -> None:
    with pytest.raises(InvalidBudgetThresholdsError):
        validate_budget_alert_thresholds([])


def test_validate_thresholds_rejects_out_of_range() -> None:
    with pytest.raises(InvalidBudgetThresholdsError):
        validate_budget_alert_thresholds([0])
    with pytest.raises(InvalidBudgetThresholdsError):
        validate_budget_alert_thresholds([5000])


def test_validate_thresholds_rejects_bool() -> None:
    # bool is an int subclass — must not be accepted as a threshold.
    with pytest.raises(InvalidBudgetThresholdsError):
        validate_budget_alert_thresholds([True])  # type: ignore[list-item]


class _FakeRow:
    def __init__(self, value: Any) -> None:
        self.value = value


class _FakeSession:
    """Minimal stand-in for AsyncSession.get(PlatformSetting, key)."""

    def __init__(self, stored: dict[str, Any]) -> None:
        self._stored = stored

    async def get(self, _model: Any, key: str) -> Any:
        if key in self._stored:
            return _FakeRow(self._stored[key])
        return None


@pytest.mark.asyncio
async def test_get_thresholds_default_when_unset() -> None:
    session = _FakeSession({})
    assert await get_budget_alert_thresholds(session) == [80, 90, 100]  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_thresholds_reads_override() -> None:
    session = _FakeSession({BUDGET_ALERT_THRESHOLDS_KEY: [50, 75]})
    assert await get_budget_alert_thresholds(session) == [50, 75, 100]  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_thresholds_falls_back_on_corrupt_value() -> None:
    # A garbage stored value degrades to the default rather than crashing.
    session = _FakeSession({BUDGET_ALERT_THRESHOLDS_KEY: "not-a-list"})
    assert await get_budget_alert_thresholds(session) == [80, 90, 100]  # type: ignore[arg-type]
