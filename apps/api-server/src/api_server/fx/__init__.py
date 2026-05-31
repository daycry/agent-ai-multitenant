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
    select_rate_for_date,
)

__all__ = [
    "UnknownCurrencyError",
    "convert_from_usd",
    "convert_from_usd_with_rate",
    "select_rate_for_date",
]
