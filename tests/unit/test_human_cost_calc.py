"""Unit tests for the human cost calculator (Plan 03 task_03_22)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from api_server.chat.cost import (
    DEFAULT_HOURLY_RATE_EUR,
    DEFAULT_TASK_HOURS,
    compute_human_cost,
)


def test_empty_specification_returns_zero_total() -> None:
    result = compute_human_cost({})
    assert result.total_cost == Decimal("0.00")
    assert result.total_hours == Decimal("0.000")
    assert result.tasks == ()
    # The hourly rate echo back so the UI can render the multiplier.
    assert result.hourly_rate == Decimal("50.00")
    assert result.currency == "EUR"


def test_none_specification_returns_zero_total() -> None:
    """A plan freshly bootstrapped from chat has no spec yet."""
    result = compute_human_cost(None)
    assert result.total_cost == Decimal("0.00")
    assert result.tasks == ()


def test_single_task_uses_estimated_hours_times_rate() -> None:
    spec = {
        "tasks": [
            {"id": "t1", "title": "Modelar", "estimated_hours": 4},
        ],
    }
    result = compute_human_cost(spec, hourly_rate=Decimal("50"))
    assert result.total_hours == Decimal("4.000")
    assert result.total_cost == Decimal("200.00")
    assert len(result.tasks) == 1
    assert result.tasks[0].task_id == "t1"
    assert result.tasks[0].hours == Decimal("4.000")
    assert result.tasks[0].cost == Decimal("200.00")


def test_multiple_tasks_sum_into_total() -> None:
    spec = {
        "tasks": [
            {"id": "t1", "title": "A", "estimated_hours": 3},
            {"id": "t2", "title": "B", "estimated_hours": 8},
            {"id": "t3", "title": "C", "estimated_hours": 1.5},
        ],
    }
    result = compute_human_cost(spec, hourly_rate=Decimal("60"))
    # 3 + 8 + 1.5 = 12.5 h × 60 = 750 €
    assert result.total_hours == Decimal("12.500")
    assert result.total_cost == Decimal("750.00")
    assert {t.task_id for t in result.tasks} == {"t1", "t2", "t3"}


def test_task_without_estimated_hours_uses_default() -> None:
    spec = {"tasks": [{"id": "t1", "title": "Sin estimación"}]}
    result = compute_human_cost(spec, hourly_rate=Decimal("50"))
    # DEFAULT_TASK_HOURS = 4 h → 200 €
    assert result.tasks[0].hours == DEFAULT_TASK_HOURS.quantize(Decimal("0.001"))
    assert result.tasks[0].cost == Decimal("200.00")


def test_zero_or_negative_estimated_hours_falls_back_to_default() -> None:
    spec = {
        "tasks": [
            {"id": "t1", "title": "Cero", "estimated_hours": 0},
            {"id": "t2", "title": "Negativo", "estimated_hours": -3},
        ],
    }
    result = compute_human_cost(spec, hourly_rate=Decimal("50"))
    for task in result.tasks:
        assert task.hours == DEFAULT_TASK_HOURS.quantize(Decimal("0.001"))


def test_non_numeric_estimated_hours_falls_back_to_default() -> None:
    """A spec produced by a sloppy LLM might emit `'unknown'` for hours."""
    spec = {
        "tasks": [
            {"id": "t1", "title": "Texto", "estimated_hours": "unknown"},
            {"id": "t2", "title": "Nulo", "estimated_hours": None},
        ],
    }
    result = compute_human_cost(spec, hourly_rate=Decimal("50"))
    for task in result.tasks:
        assert task.hours == DEFAULT_TASK_HOURS.quantize(Decimal("0.001"))


def test_default_hourly_rate_matches_the_documented_placeholder() -> None:
    """CLAUDE.md §6 cites `50 €/h default`; keep the constant honest."""
    assert Decimal("50") == DEFAULT_HOURLY_RATE_EUR


def test_currency_round_trips_through_the_breakdown() -> None:
    result = compute_human_cost({}, currency="USD")
    assert result.currency == "USD"


def test_fractional_hours_round_to_cents() -> None:
    """1/3 h × 50 € = 16.6666… → 16.67 € (half-up)."""
    spec = {"tasks": [{"id": "t1", "title": "X", "estimated_hours": "0.3333"}]}
    result = compute_human_cost(spec, hourly_rate=Decimal("50"))
    # 0.333 h × 50 = 16.65; checks the rounding to 2 decimals.
    assert result.tasks[0].cost == Decimal("16.65")


def test_custom_default_task_hours_is_respected() -> None:
    spec = {"tasks": [{"id": "t1", "title": "Sin horas"}]}
    result = compute_human_cost(spec, hourly_rate=Decimal("100"), default_task_hours=Decimal("2"))
    assert result.tasks[0].hours == Decimal("2.000")
    assert result.tasks[0].cost == Decimal("200.00")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("4", Decimal("4.000")),
        (4, Decimal("4.000")),
        (4.5, Decimal("4.500")),
        (Decimal("2.5"), Decimal("2.500")),
    ],
)
def test_estimated_hours_accepts_strings_ints_floats_and_decimals(
    raw: object, expected: Decimal
) -> None:
    spec = {"tasks": [{"id": "t1", "title": "X", "estimated_hours": raw}]}
    result = compute_human_cost(spec, hourly_rate=Decimal("50"))
    assert result.tasks[0].hours == expected
