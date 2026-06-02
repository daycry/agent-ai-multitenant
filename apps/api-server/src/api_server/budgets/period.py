"""Budget-period arithmetic — the active window for a date (Plan 11.1 task_11_1_04).

A budget (tenant or project) caps spend over a recurring window. Given the
configured :class:`~api_server.db.domain.BudgetPeriod` and (for ``custom``)
its ``start_day`` / ``length_days``, :func:`current_budget_period` returns the
HALF-OPEN ``[start, end)`` window that contains a reference date. The
consumption evaluator (task_11_1_05) sums the USD cost of executions whose
date falls in ``[start, end)`` and compares it to the (USD-converted) cap.

Half-open ``[start, end)`` is deliberate: ``start`` is the first day IN the
window, ``end`` is the first day of the NEXT window (exclusive), so two
consecutive windows tile the calendar with no gap and no overlap — an
execution on ``end`` belongs to the next period, never both.

Window semantics per period:
  - ``weekly``    — the ISO week of ``on_date``: Monday 00:00 .. next Monday.
  - ``monthly``   — the 1st of ``on_date``'s month .. the 1st of next month.
  - ``quarterly`` — the start of the calendar quarter (Jan/Apr/Jul/Oct 1) ..
                    the start of the next quarter.
  - ``yearly``    — Jan 1 of ``on_date``'s year .. Jan 1 of next year.
  - ``custom``    — a fixed-length cycle of ``length_days`` days, anchored on
                    ``start_day`` of a month and rolled forward in
                    ``length_days`` chunks until it contains ``on_date``. The
                    anchor is ``start_day`` of ``on_date``'s month, clamped to
                    that month's last day; if that anchor is after ``on_date``
                    we step back a month, then walk forward by ``length_days``.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

from api_server.db.domain import BudgetPeriod

_QUARTER_LENGTH_MONTHS = 3


class InvalidBudgetPeriodError(ValueError):
    """A budget period is malformed for the window computation.

    Raised when ``period`` is unknown, or when a ``custom`` period is missing
    its required ``start_day`` / ``length_days`` (or they are out of range).
    """


@dataclass(frozen=True, slots=True)
class BudgetPeriodWindow:
    """The active budget window: half-open ``[start, end)`` (``end`` exclusive)."""

    start: date
    end: date

    def contains(self, day: date) -> bool:
        """True when ``day`` falls in ``[start, end)`` (``end`` is excluded)."""
        return self.start <= day < self.end


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _add_months(d: date, months: int) -> date:
    """Return the 1st of the month ``months`` after the 1st of ``d``'s month."""
    base = _month_start(d)
    total = (base.year * 12 + (base.month - 1)) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


def _clamp_day(year: int, month: int, day: int) -> date:
    """``date(year, month, day)`` with ``day`` clamped to the month's length
    (so ``start_day=31`` resolves to Feb 28/29 rather than raising)."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _weekly_window(on_date: date) -> BudgetPeriodWindow:
    # Monday 00:00 of on_date's ISO week .. next Monday (weekday(): Mon=0).
    start = on_date - timedelta(days=on_date.weekday())
    return BudgetPeriodWindow(start=start, end=start + timedelta(days=7))


def _monthly_window(on_date: date) -> BudgetPeriodWindow:
    start = _month_start(on_date)
    return BudgetPeriodWindow(start=start, end=_add_months(start, 1))


def _quarterly_window(on_date: date) -> BudgetPeriodWindow:
    # Calendar quarter: Jan/Apr/Jul/Oct. Month 1->1, 4->4, 7->7, 10->10.
    quarter_month = ((on_date.month - 1) // _QUARTER_LENGTH_MONTHS) * _QUARTER_LENGTH_MONTHS + 1
    start = date(on_date.year, quarter_month, 1)
    return BudgetPeriodWindow(start=start, end=_add_months(start, _QUARTER_LENGTH_MONTHS))


def _yearly_window(on_date: date) -> BudgetPeriodWindow:
    start = date(on_date.year, 1, 1)
    return BudgetPeriodWindow(start=date(on_date.year, 1, 1), end=date(start.year + 1, 1, 1))


def _custom_window(
    on_date: date, start_day: int | None, length_days: int | None
) -> BudgetPeriodWindow:
    if start_day is None or length_days is None:
        raise InvalidBudgetPeriodError(
            "custom budget period requires both start_day and length_days"
        )
    if not (1 <= start_day <= 31):
        raise InvalidBudgetPeriodError(f"custom start_day must be 1..31, got {start_day}")
    if length_days < 1:
        raise InvalidBudgetPeriodError(f"custom length_days must be >= 1, got {length_days}")

    # Anchor on start_day of on_date's month (clamped to the month length);
    # if that anchor is after on_date, the cycle that contains on_date began
    # in the previous month, so step the anchor back one month.
    anchor = _clamp_day(on_date.year, on_date.month, start_day)
    if anchor > on_date:
        prev = _add_months(on_date, -1)
        anchor = _clamp_day(prev.year, prev.month, start_day)

    # Walk forward by length_days from the anchor until the window covers
    # on_date. Bounded: each step advances by length_days (>= 1 day).
    span = timedelta(days=length_days)
    start = anchor
    while start + span <= on_date:
        start = start + span
    return BudgetPeriodWindow(start=start, end=start + span)


def current_budget_period(
    period: BudgetPeriod | str,
    *,
    start_day: int | None = None,
    length_days: int | None = None,
    on_date: date,
) -> BudgetPeriodWindow:
    """Return the active budget window ``[start, end)`` that contains ``on_date``.

    ``period`` is a :class:`~api_server.db.domain.BudgetPeriod` (or its string
    value). ``start_day`` / ``length_days`` are required for — and only used
    by — the ``custom`` period; they are ignored for the fixed calendar
    periods. Raises :class:`InvalidBudgetPeriodError` for an unknown period or
    a malformed ``custom`` configuration.
    """
    try:
        resolved = BudgetPeriod(period)
    except ValueError as exc:
        raise InvalidBudgetPeriodError(f"unknown budget period: {period!r}") from exc

    if resolved is BudgetPeriod.WEEKLY:
        return _weekly_window(on_date)
    if resolved is BudgetPeriod.MONTHLY:
        return _monthly_window(on_date)
    if resolved is BudgetPeriod.QUARTERLY:
        return _quarterly_window(on_date)
    if resolved is BudgetPeriod.YEARLY:
        return _yearly_window(on_date)
    # CUSTOM is the only remaining member.
    return _custom_window(on_date, start_day, length_days)
