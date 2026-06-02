"""Fetch + parse + upsert daily FX reference rates (Plan 11.1 task_11_1_02).

The platform's cost currency is USD (canonical); a tenant's
``display_currency`` is converted on the fly with the rate of each
execution's DATE (see :mod:`api_server.fx.convert`). This module keeps the
``exchange_rates`` catalog fresh: it fetches the daily reference rates from
the configured source (ECB by default), parses them into
``currency -> rate_vs_usd`` rows, and upserts them for the feed's ``as_of_date``.

ECB publishes vs EUR — we convert to vs-USD
-------------------------------------------
The ECB daily reference feed (``eurofxref-daily.xml``) quotes every currency
as *units of that currency per 1 EUR* (the ``rate`` attribute), e.g.
``USD rate=1.08`` means 1 EUR == 1.08 USD. Our catalog stores
``rate_vs_usd`` = *units of the currency per 1 USD*. We convert each ECB rate
through the USD rate:

  - for a non-EUR currency C:  ``rate_vs_usd[C] = ecb_rate[C] / ecb_rate[USD]``
    (units of C per EUR, divided by USD per EUR == units of C per USD);
  - for EUR itself:            ``rate_vs_usd[EUR] = 1 / ecb_rate[USD]``;
  - USD is the identity and is **never stored** (the catalog's CHECK forbids it).

A feed without a USD rate cannot be anchored to USD — that is a hard parse
error (:class:`FxFeedError`), surfaced to the best-effort caller rather than
silently dropping every row.

Network is fully injectable
---------------------------
The fetch goes through a small :class:`FxRateFetcher` Protocol; production
wires :class:`EcbRateFetcher` (an injectable ``httpx.AsyncClient``), tests
wire :class:`StaticFxRateFetcher` (a fixture XML body) — **no real network in
tests**.

System-Admin only
-----------------
``exchange_rates`` is platform-global with a global-read RLS policy + NO
write policy (migration 0062). The upsert writes through the worker's
BYPASSRLS database role (the same admin-grade role the worker already uses);
a tenant CANNOT trigger or schedule it — the schedule lives in the platform's
beat process and the enable flag / source are platform settings only a System
Admin can write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from xml.etree import ElementTree

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.exchange_rates import CANONICAL_CURRENCY, ExchangeRate, FxSource

# The ECB daily reference-rates XML feed. Read as a DATA FEED only; the worker
# can point at an internal mirror via WORKERS_ECB_FX_FEED_URL.
DEFAULT_ECB_FEED_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

# The FX sources the fetcher knows how to parse. Kept in lockstep with
# api_server.db.platform_settings.FX_SOURCES.
FX_FETCHER_SOURCES = ("ecb",)

# Quantum the stored ``rate_vs_usd`` is rounded to. The column is Numeric(20,10);
# 10 decimal places keeps small-unit (JPY) and large-unit currencies exact.
_RATE_QUANTUM = Decimal("0.0000000001")


class FxFeedError(Exception):
    """The FX feed could not be fetched, did not parse, or lacked a USD anchor."""


# =============================================================================
# Fetch seam (injectable; tests feed a fixture XML body, no real network)
# =============================================================================
class FxRateFetcher(Protocol):
    """Fetches the raw FX feed body (the source's native format) as text."""

    async def fetch(self) -> str:  # pragma: no cover - Protocol
        ...


@dataclass(frozen=True)
class EcbRateFetcher:
    """Production fetcher: GET the ECB daily XML over an injectable httpx client.

    The ``httpx.AsyncClient`` is passed in by the caller (the Celery job builds
    one), so the network is fully controllable — tests that exercise *this*
    class wire an ``httpx.MockTransport`` and never hit the wire. Most tests
    inject a :class:`StaticFxRateFetcher` instead.
    """

    client: Any  # httpx.AsyncClient — typed Any to keep httpx out of the import graph
    url: str = DEFAULT_ECB_FEED_URL
    timeout_seconds: float = 30.0

    async def fetch(self) -> str:
        resp = await self.client.get(self.url, timeout=self.timeout_seconds)
        resp.raise_for_status()
        return str(resp.text)


@dataclass(frozen=True)
class StaticFxRateFetcher:
    """Fetcher over an in-memory body — the test / mirror seam."""

    body: str

    async def fetch(self) -> str:
        return self.body


# =============================================================================
# Parsed feed + result types
# =============================================================================
@dataclass(frozen=True, slots=True)
class ParsedRate:
    """One currency's rate vs USD on the feed's date (USD never appears here)."""

    currency: str
    rate_vs_usd: Decimal


@dataclass(frozen=True, slots=True)
class ParsedFeed:
    """The parsed FX feed: an effective date + the per-currency vs-USD rates."""

    as_of_date: date
    rates: list[ParsedRate]


@dataclass(slots=True)
class FxUpsertSummary:
    """The outcome of one FX-fetcher run (also the returned/logged summary)."""

    as_of_date: date | None = None
    source: str = FxSource.ECB
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0


# =============================================================================
# Pure parsing (no DB, no network)
# =============================================================================
def _to_decimal(value: Any) -> Decimal | None:
    """Coerce a feed numeric string to a positive Decimal, or None."""
    if value is None:
        return None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if dec <= 0:
        return None
    return dec


def parse_ecb_feed(body: str) -> ParsedFeed:
    """Parse the ECB daily reference-rates XML into vs-USD rates (pure).

    The ECB feed nests ``<Cube time="YYYY-MM-DD">`` carrying one
    ``<Cube currency="C" rate="R"/>`` per currency, where ``R`` is units of C
    per 1 EUR. We anchor to USD: ``rate_vs_usd[C] = R[C] / R[USD]`` for a
    non-EUR currency, and ``rate_vs_usd[EUR] = 1 / R[USD]``. USD itself is the
    identity and is never emitted. Raises :class:`FxFeedError` on malformed XML,
    a missing/invalid date, or the absence of a USD rate (no USD anchor → the
    whole feed is unusable for a USD-canonical catalog).
    """
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise FxFeedError(f"ECB feed is not valid XML: {exc}") from exc

    # The feed namespaces every element under the eurofxref schema; match the
    # ``Cube`` local-name regardless of prefix so a namespace change does not
    # break parsing.
    cubes = [el for el in root.iter() if el.tag.split("}")[-1] == "Cube"]

    # The dated <Cube time="..."> carries the per-currency child cubes.
    dated = next((c for c in cubes if "time" in c.attrib), None)
    if dated is None:
        raise FxFeedError("ECB feed has no dated <Cube time=...> element")
    try:
        as_of_date = datetime.strptime(dated.attrib["time"], "%Y-%m-%d").date()
    except (KeyError, ValueError) as exc:
        raise FxFeedError(f"ECB feed has an invalid date: {dated.attrib.get('time')!r}") from exc

    # Collect every (currency, rate-per-EUR) pair from the child cubes.
    per_eur: dict[str, Decimal] = {}
    for cube in cubes:
        currency = cube.attrib.get("currency")
        rate = _to_decimal(cube.attrib.get("rate"))
        if currency is None or rate is None:
            continue
        per_eur[currency.strip().upper()] = rate

    usd_per_eur = per_eur.get(CANONICAL_CURRENCY)
    if usd_per_eur is None:
        raise FxFeedError("ECB feed has no USD rate — cannot anchor to USD")

    rates: list[ParsedRate] = []
    # EUR vs USD: 1 USD == (1 / USD-per-EUR) EUR.
    rates.append(
        ParsedRate(
            currency="EUR",
            rate_vs_usd=(Decimal(1) / usd_per_eur).quantize(_RATE_QUANTUM),
        )
    )
    for currency, rate_per_eur in per_eur.items():
        if currency == CANONICAL_CURRENCY:
            # USD is the identity — never stored (the catalog CHECK forbids it).
            continue
        rates.append(
            ParsedRate(
                currency=currency,
                rate_vs_usd=(rate_per_eur / usd_per_eur).quantize(_RATE_QUANTUM),
            )
        )

    # Deterministic order so the upsert + any test assertion is stable.
    rates.sort(key=lambda r: r.currency)
    return ParsedFeed(as_of_date=as_of_date, rates=rates)


def parse_feed(source: str, body: str) -> ParsedFeed:
    """Parse a feed body for ``source`` into vs-USD rates (pure).

    Dispatches on the configured source. Only ECB is wired today; an unknown
    source is a :class:`FxFeedError` (the caller's best-effort handling turns it
    into a logged + alerted no-write run).
    """
    normalized = source.strip().lower()
    if normalized == FxSource.ECB:
        return parse_ecb_feed(body)
    raise FxFeedError(f"unknown FX source {source!r}; known sources: {FX_FETCHER_SOURCES}")


# =============================================================================
# Upsert (DB writes; BYPASSRLS worker session)
# =============================================================================
async def upsert_exchange_rates(
    session: AsyncSession,
    parsed: ParsedFeed,
    *,
    source: str = FxSource.ECB,
) -> FxUpsertSummary:
    """Upsert the parsed rates into ``exchange_rates`` for the feed's date.

    Idempotent on ``(currency, as_of_date)`` (the catalog's UNIQUE key): a rate
    already present for the date with the SAME value is left untouched
    (``unchanged``); a present-but-different value is overwritten (``updated``);
    an absent one is inserted (``created``). Re-running the same feed is a no-op.
    The caller owns the transaction (the beat task commits via ``db.begin()``).
    """
    summary = FxUpsertSummary(
        as_of_date=parsed.as_of_date,
        source=source,
        fetched=len(parsed.rates),
    )
    for rate in parsed.rates:
        existing = (
            await session.execute(
                select(ExchangeRate).where(
                    ExchangeRate.currency == rate.currency,
                    ExchangeRate.as_of_date == parsed.as_of_date,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            session.add(
                ExchangeRate(
                    currency=rate.currency,
                    rate_vs_usd=rate.rate_vs_usd,
                    as_of_date=parsed.as_of_date,
                    source=source,
                )
            )
            summary.created += 1
            continue

        if existing.rate_vs_usd == rate.rate_vs_usd and existing.source == source:
            summary.unchanged += 1
            continue

        existing.rate_vs_usd = rate.rate_vs_usd
        existing.source = source
        session.add(existing)
        summary.updated += 1

    await session.flush()
    return summary


async def fetch_and_upsert_rates(
    session: AsyncSession,
    *,
    fetcher: FxRateFetcher,
    source: str = FxSource.ECB,
) -> FxUpsertSummary:
    """Fetch the feed, parse it to vs-USD rates, and upsert them.

    The end-to-end FX refresh: fetch the body through the injectable
    :class:`FxRateFetcher`, parse it for ``source``, then upsert the rates for
    the feed's ``as_of_date``. A fetch/parse failure raises :class:`FxFeedError`
    BEFORE any write; the caller commits on success.
    """
    body = await fetcher.fetch()
    parsed = parse_feed(source, body)
    return await upsert_exchange_rates(session, parsed, source=source)


__all__ = [
    "DEFAULT_ECB_FEED_URL",
    "FX_FETCHER_SOURCES",
    "EcbRateFetcher",
    "FxFeedError",
    "FxRateFetcher",
    "FxUpsertSummary",
    "ParsedFeed",
    "ParsedRate",
    "StaticFxRateFetcher",
    "fetch_and_upsert_rates",
    "parse_ecb_feed",
    "parse_feed",
    "upsert_exchange_rates",
]
