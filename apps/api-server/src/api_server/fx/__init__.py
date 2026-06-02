"""FX (foreign-exchange) conversion — USD-canonical, display-only (Plan 11.1).

USD is the platform's canonical cost currency. A tenant's
``display_currency`` is converted on the fly with the rate of each
execution's DATE (see :mod:`api_server.db.exchange_rates`). This package
holds the conversion service.
"""

from __future__ import annotations

from api_server.fx.convert import (
    UnknownCurrencyError,
    convert_from_usd,
    convert_from_usd_with_rate,
    convert_to_usd,
    convert_to_usd_with_rate,
    resolve_rates_for_dates,
    select_rate_for_date,
)
from api_server.fx.fetcher import (
    DEFAULT_ECB_FEED_URL,
    EcbRateFetcher,
    FxFeedError,
    FxRateFetcher,
    FxUpsertSummary,
    ParsedFeed,
    ParsedRate,
    StaticFxRateFetcher,
    fetch_and_upsert_rates,
    parse_ecb_feed,
    parse_feed,
    upsert_exchange_rates,
)

__all__ = [
    "DEFAULT_ECB_FEED_URL",
    "EcbRateFetcher",
    "FxFeedError",
    "FxRateFetcher",
    "FxUpsertSummary",
    "ParsedFeed",
    "ParsedRate",
    "StaticFxRateFetcher",
    "UnknownCurrencyError",
    "convert_from_usd",
    "convert_from_usd_with_rate",
    "convert_to_usd",
    "convert_to_usd_with_rate",
    "fetch_and_upsert_rates",
    "parse_ecb_feed",
    "parse_feed",
    "resolve_rates_for_dates",
    "select_rate_for_date",
    "upsert_exchange_rates",
]
