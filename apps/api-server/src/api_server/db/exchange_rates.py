"""Global FX rate catalog ORM (Plan 11.1 Fase A, task_11_1_01).

A single platform-global table — ``exchange_rates`` — that records, for a
given currency on a given calendar date, how many units of that currency
**one USD** buys (``rate_vs_usd``). USD is the platform's canonical cost
currency (Plan 11 task_11_13: ``executions.total_cost_usd`` + the
per-call price snapshot); a tenant's ``display_currency`` is a *display*
concern only, converted on the fly with the rate of each execution's
DATE. This table is the source of those rates.

Tenancy decision (CLAUDE.md principle 1 / 9, mirrors ``model_prices``):
**platform-global, NOT tenant-scoped.** An FX rate is a property of the
market on a date, identical for every tenant — so the table carries **no
``tenant_id``**. The read/write split is enforced at the DB layer by a
**global-read RLS policy** (migration 0062): RLS is ENABLED + FORCED with
a single SELECT-only policy ``USING (true)`` (every authenticated session
may read) and **no write policy**, so a NOBYPASSRLS / tenant session is
denied every INSERT/UPDATE/DELETE while the BYPASSRLS System-Admin
session (``get_admin_session``) — and the daily Celery Beat fetcher
(task_11_1_02) running with the migrations/admin role — write freely.
This is the exact SELECT-only ``global_read`` pattern of
:class:`~api_server.db.model_prices.ModelPrice` (migration 0049).

Source: ``source`` records where a row's rate came from. The default and
only seeded source is ECB (the European Central Bank daily reference
rates); a System Admin may configure an alternative source for the
fetcher (task_11_1_02). The string is kept free-form-ish behind a
:class:`FxSource` enum so historical rows keep their value even if the
default source changes.

Effective dating by calendar DATE: one rate per ``(currency, as_of_date)``
(unique). Conversion for an execution on date *D* uses the rate whose
``as_of_date`` is *D* or, if none exists for *D* (weekends/holidays — the
ECB does not publish every day), the **most recent prior** ``as_of_date``.
USD is the identity (rate 1) and is never stored.

NO migration ships in THIS module — migration 0062 creates the table,
indexes and the global-read RLS, and adds ``organizations.display_currency``.
This module is the ORM shape + the pure conversion service only.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# The canonical cost currency. A rate against USD of exactly 1; USD is the
# identity of the conversion and is never stored as a row.
CANONICAL_CURRENCY = "USD"

# A tenant's display currency defaults to EUR (the platform's primary
# operating currency); see Organization.display_currency (migration 0062).
DEFAULT_DISPLAY_CURRENCY = "EUR"


class FxSource:
    """Where an ``exchange_rates`` row's rate came from.

    Not a ``StrEnum`` on the column (the column is free-form ``String`` so
    a future source needs no migration), but these are the known/seeded
    values. ``ECB`` is the platform default (European Central Bank daily
    reference rates); ``MANUAL`` is a System-Admin override.
    """

    ECB = "ecb"
    MANUAL = "manual"


class ExchangeRate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One FX rate: how many units of ``currency`` equal one USD on a date.

    ``rate_vs_usd`` is *units of ``currency`` per 1 USD* — e.g. a EUR row
    with ``rate_vs_usd = 0.92`` means 1 USD == 0.92 EUR, so
    ``convert_from_usd(10, "EUR", ...)`` == ``Decimal("9.20")``.

    NOT tenant-scoped (platform-global, see module docstring) and NOT
    soft-deleted: a rate is a fact about a date; history is the set of
    dated rows, not a deletion flag.
    """

    __tablename__ = "exchange_rates"
    __table_args__ = (
        # Exactly one rate per currency per calendar date — the conversion
        # service keys on (currency, as_of_date) and the fetcher upserts.
        UniqueConstraint(
            "currency",
            "as_of_date",
            name="uq_exchange_rates_currency_date",
        ),
        # Conversion lookup path: the rate for a currency ON a date, or the
        # most recent PRIOR date — both served by this descending-friendly
        # index on (currency, as_of_date).
        Index(
            "ix_exchange_rates_currency_as_of_date",
            "currency",
            "as_of_date",
        ),
        # A rate is strictly positive (a non-positive FX rate is nonsense
        # and would let conversion produce zero/negative display amounts).
        CheckConstraint("rate_vs_usd > 0", name="ck_exchange_rates_rate_positive"),
        # USD is the identity and must never be stored as a row (it would
        # be ambiguous against the implicit identity used by the service).
        CheckConstraint("currency <> 'USD'", name="ck_exchange_rates_currency_not_usd"),
    )

    # ISO-4217 currency code, e.g. "EUR", "GBP", "JPY". Stored uppercase by
    # convention; the conversion service upper-cases its input before query.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Units of ``currency`` per 1 USD. High precision so even small-unit
    # currencies (e.g. JPY ~150) and large-unit ones stay exact; scale 10
    # mirrors model_prices' per-token precision head-room.
    rate_vs_usd: Mapped[Decimal] = mapped_column(Numeric(precision=20, scale=10), nullable=False)

    # The calendar date the rate applies to (the publishing date of the
    # source feed). Conversion for an execution on date D uses the row with
    # this == D, else the most recent prior row.
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Provenance: 'ecb' (default daily reference rates) or 'manual' (a
    # System-Admin override). Free-form so a future source needs no schema
    # change; FxSource holds the known values.
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'ecb'"))

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"ExchangeRate(currency={self.currency!r}, as_of_date={self.as_of_date!r}, "
            f"rate_vs_usd={self.rate_vs_usd!r}, source={self.source!r})"
        )


__all__ = [
    "CANONICAL_CURRENCY",
    "DEFAULT_DISPLAY_CURRENCY",
    "ExchangeRate",
    "FxSource",
]
