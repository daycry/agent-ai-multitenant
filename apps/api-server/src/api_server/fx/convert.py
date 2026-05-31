"""USD -> display-currency conversion service (Plan 11.1 task_11_1_01).

USD is canonical; a tenant's ``display_currency`` is a DISPLAY concern
only, converted on the fly with the rate of each execution's DATE. This
module is the conversion service:

  - :func:`convert_from_usd_with_rate` — the *pure* arithmetic core: given
    a USD amount and a (units-of-currency-per-USD) rate, return the
    converted amount. No DB, no I/O.
  - :func:`select_rate_for_date` — picks the right
    :class:`~api_server.db.exchange_rates.ExchangeRate` row for a currency
    on a date: the rate whose ``as_of_date`` is the date itself, else the
    **most recent PRIOR** rate (weekends/holidays have no ECB publish).
  - :func:`convert_from_usd` — the convenience that ties the two together
    against an :class:`~sqlalchemy.ext.asyncio.AsyncSession`.

Rules (binding decisions of Plan 11.1):
  - **USD -> USD is the identity** (returns the input amount unchanged);
    no rate row is needed or stored for USD.
  - An **unknown / unavailable** currency (no row on or before the date)
    is an explicit, typed failure: :class:`UnknownCurrencyError`. Callers
    that prefer graceful degradation pass ``fallback_to_usd=True``, which
    returns the USD amount unchanged (display falls back to USD) instead
    of raising. We make the choice *explicit* at the call site rather than
    silently picking one.

Conversion direction: ``ExchangeRate.rate_vs_usd`` is *units of the target
currency per 1 USD*, so ``display = usd * rate``. e.g. EUR rate 0.92 turns
10 USD into 9.20 EUR.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.exchange_rates import CANONICAL_CURRENCY, ExchangeRate

# Display amounts are money — quantize to 2 decimal places (cents) with
# bankers'-free half-up rounding so a UI never shows 9.199999… EUR.
_MONEY_QUANTUM = Decimal("0.01")


class UnknownCurrencyError(ValueError):
    """No FX rate is available for ``currency`` on or before ``on_date``.

    Raised by the conversion service when a non-USD currency has no
    ``exchange_rates`` row whose ``as_of_date`` is the requested date or
    any earlier date. Callers that prefer to degrade to USD pass
    ``fallback_to_usd=True`` instead of catching this.
    """

    def __init__(self, currency: str, on_date: date) -> None:
        self.currency = currency
        self.on_date = on_date
        super().__init__(f"no exchange rate for {currency!r} on or before {on_date.isoformat()}")


def _normalize(currency: str) -> str:
    """Upper-case + strip an ISO-4217 code (rows are stored uppercase)."""
    return currency.strip().upper()


def convert_from_usd_with_rate(amount_usd: Decimal, rate_vs_usd: Decimal) -> Decimal:
    """Pure conversion: ``amount_usd`` USD at ``rate_vs_usd`` units/USD.

    No DB, no I/O — the arithmetic core, quantized to cents (half-up). The
    rate is *units of the target currency per 1 USD*.
    """
    return (amount_usd * rate_vs_usd).quantize(_MONEY_QUANTUM, rounding=ROUND_HALF_UP)


async def select_rate_for_date(
    session: AsyncSession,
    currency: str,
    on_date: date,
) -> ExchangeRate | None:
    """The FX rate row for ``currency`` on ``on_date`` or the most recent prior.

    Picks the row whose ``as_of_date`` is exactly ``on_date`` and, failing
    that (the ECB does not publish on weekends/holidays), the row with the
    greatest ``as_of_date`` that is still ``<= on_date``. Returns ``None``
    when no such row exists (caller decides: raise or fall back to USD).
    USD is never queried here — it is the identity handled by callers.
    """
    normalized = _normalize(currency)
    stmt = (
        select(ExchangeRate)
        .where(
            ExchangeRate.currency == normalized,
            ExchangeRate.as_of_date <= on_date,
        )
        .order_by(ExchangeRate.as_of_date.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def resolve_rates_for_dates(
    session: AsyncSession,
    currency: str,
    on_dates: Iterable[date],
) -> dict[date, ExchangeRate | None]:
    """Resolve, for each requested date, the applicable FX rate row (or None).

    The bulk counterpart of :func:`select_rate_for_date`, for the runs
    explorer which converts **per row, at each run's own date** and must
    not fire one query per row (N+1). Returns a mapping
    ``{requested_date: ExchangeRate | None}`` where each date maps to the
    rate whose ``as_of_date`` is that date or, failing that, the most
    recent PRIOR rate (weekends/holidays have no ECB publish); ``None``
    when no row exists on or before that date.

    One query: every row for ``currency`` whose ``as_of_date`` is ``<=``
    the LATEST requested date, ascending, then a single linear pass picks
    each requested date's rate. USD is never queried — it is the identity
    handled by callers. An empty ``on_dates`` yields an empty mapping with
    no query.
    """
    wanted = sorted(set(on_dates))
    if not wanted:
        return {}

    normalized = _normalize(currency)
    stmt = (
        select(ExchangeRate)
        .where(
            ExchangeRate.currency == normalized,
            ExchangeRate.as_of_date <= wanted[-1],
        )
        .order_by(ExchangeRate.as_of_date.asc())
    )
    rows = list((await session.execute(stmt)).scalars().all())

    # Linear merge: walk requested dates ascending, advancing the row
    # cursor to the latest row that is still <= the current date. That row
    # (the most recent on-or-before) is the date's applicable rate.
    resolved: dict[date, ExchangeRate | None] = {}
    cursor = 0
    current: ExchangeRate | None = None
    for d in wanted:
        while cursor < len(rows) and rows[cursor].as_of_date <= d:
            current = rows[cursor]
            cursor += 1
        resolved[d] = current
    return resolved


async def convert_from_usd(
    session: AsyncSession,
    amount_usd: Decimal,
    currency: str,
    on_date: date,
    *,
    fallback_to_usd: bool = False,
) -> Decimal:
    """Convert ``amount_usd`` USD to ``currency`` using ``on_date``'s rate.

    USD -> USD is the identity (the amount is returned unchanged). For any
    other currency the rate is :func:`select_rate_for_date` (the day's rate
    or the most recent prior). When no rate is available:

      - ``fallback_to_usd=False`` (default): raise :class:`UnknownCurrencyError`.
      - ``fallback_to_usd=True``: return ``amount_usd`` unchanged (display
        degrades to USD).

    The returned Decimal is quantized to 2 decimal places.
    """
    normalized = _normalize(currency)
    if normalized == CANONICAL_CURRENCY:
        # USD identity — no rate lookup, no rounding surprise.
        return amount_usd

    rate_row = await select_rate_for_date(session, normalized, on_date)
    if rate_row is None:
        if fallback_to_usd:
            return amount_usd
        raise UnknownCurrencyError(normalized, on_date)

    return convert_from_usd_with_rate(amount_usd, rate_row.rate_vs_usd)


__all__ = [
    "UnknownCurrencyError",
    "convert_from_usd",
    "convert_from_usd_with_rate",
    "resolve_rates_for_dates",
    "select_rate_for_date",
]
