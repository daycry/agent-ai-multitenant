"""Global model price catalog ORM (Plan 11 Fase C, task_11_10).

A single platform-global table — ``model_prices`` — that records what a
given LLM model costs per token, in **canonical USD**, with effective
dating so a model can have successive priced periods (historical
correctness). The catalog feeds the cost estimates the platform already
uses as placeholders (Plan 03) and the per-call price snapshot
(task_11_13 writes the *current* price onto each ``model_call`` step so
old executions keep their historical cost even after a price change).

Tenancy decision (ADR 0028 + CLAUDE.md principle 9 / 1):
**platform-global, NOT tenant-scoped.** A price is the same for every
tenant — it is a property of the *provider's* pricing, not of any
tenant's data — so the table carries **no ``tenant_id``**. The
read/write split is enforced at the DB layer by a **global-read RLS
policy** (migration task_11_11): RLS is ENABLED + FORCED with a single
SELECT-only policy ``USING (true)`` (every authenticated session may
read) and **no write policy**, so a NOBYPASSRLS / tenant session is
denied every INSERT/UPDATE/DELETE while the BYPASSRLS System-Admin
session (``get_admin_session``) bypasses RLS and writes freely. This
mirrors the SELECT-only ``global_read`` pattern of
:class:`~api_server.db.marketplace.MarketplaceListing` (migration 0041)
and the ``agents_global_builtin_read`` policy (migration 0004), giving a
*provable* "reads open to all, writes System-Admin-only" guarantee that
does not rely on application code alone (task_11_12 adds the endpoint
RBAC on top). ``updated_by`` tracks the last System Admin who touched a
row, mirroring ``platform_settings.updated_by`` — the price outlives the
user, so the FK is ``ON DELETE SET NULL``.

Currency decision: the catalog is **USD-only**. Every price column is in
USD; ``currency`` is a constant ``"USD"`` kept as an explicit column for
self-documentation and forward compatibility, NOT a conversion knob.
Display conversion to a tenant's currency (``exchange_rates`` +
``Organization.display_currency``) is out of scope for this plan's
numbered tasks and will be flagged for the human at plan close.

Prompt caching: ``cached_input_price`` is the price of a *cache-read*
input token (prompt caching). It is **nullable** — most models do not
price cache reads separately — and, when set, is by convention ~10% of
``input_price`` but is stored **explicitly** (never derived) so the
catalog stays faithful to each provider's published price.

Unit: all prices are quoted **per 1,000,000 (1M) tokens**, the unit
upstream price feeds (the community LiteLLM JSON, provider pages) use.
The unit is pinned by the :class:`PriceUnit` enum and defaults to
``per_1m_tokens`` so a reader never has to guess the scale.

NO migration ships in THIS task — task_11_11 creates the table, indexes
and the (no-)RLS decision. This module is the ORM shape + enums only so
the rest of Fase C can build against a stable contract.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# The canonical (and, in this plan, only) currency of the catalog. Every
# price column is denominated in USD. Kept as a module constant so the
# default and the helper validation reference one source of truth.
CANONICAL_CURRENCY = "USD"


# =============================================================================
# Enums (StrEnum so the value persists as a stable plain string / TEXT)
# =============================================================================
class PriceModality(enum.StrEnum):
    """What kind of model usage a price row covers.

    A single ``model_id`` can have several modalities priced
    independently (e.g. a multimodal model charging different rates for
    text vs. vision input), so modality is part of the catalog key.

    Extend by adding members; never rename existing ones — historical
    rows still reference the old string value.
    """

    TEXT = "text"
    VISION = "vision"
    AUDIO = "audio"
    EMBEDDING = "embedding"
    IMAGE = "image"
    RERANK = "rerank"


class PriceSource(enum.StrEnum):
    """Where a price row's numbers came from.

    - ``litellm``:      synced from the community LiteLLM price JSON
                        (``model_prices_and_context_window.json``) — a
                        *data feed* only, NOT a runtime provider (ADR
                        0021). task_11_15 wires the sync.
    - ``provider_api``: fetched from the provider's own pricing API.
    - ``manual``:       entered/edited by a System Admin in the UI
                        (task_11_14). Manual rows are never silently
                        overwritten by a sync.
    """

    LITELLM = "litellm"
    PROVIDER_API = "provider_api"
    MANUAL = "manual"


class PriceUnit(enum.StrEnum):
    """The token quantity every price column is quoted against.

    Pinned so a reader never has to guess the scale. The catalog
    standardises on per-1M-tokens (upstream feeds use it); per-1K is kept
    as an explicit alternative rather than an implicit assumption.
    """

    PER_1M_TOKENS = "per_1m_tokens"
    PER_1K_TOKENS = "per_1k_tokens"


# =============================================================================
# model_prices — platform-global price catalog with effective dating
# =============================================================================
class ModelPrice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One priced period for a ``(provider, model_id, modality)`` triple.

    Effective dating: ``effective_from`` is when the price took effect;
    ``effective_to`` is when it stopped applying. A **NULL
    ``effective_to`` marks the current (open) period** — there is at most
    one such row per ``(provider, model_id, modality)``, enforced by a
    partial unique index, which makes "the current price" well-defined
    without scanning dates. A price change closes the old row (sets its
    ``effective_to``) and inserts a new open row.

    NOT tenant-scoped and NOT soft-deleted: the catalog is platform-global
    and effective-dated, so history is expressed by closing periods, not
    by deleting rows.
    """

    __tablename__ = "model_prices"
    __table_args__ = (
        # The current price for a (provider, model_id, modality) is the
        # single row whose period is still open. Enforce uniqueness on the
        # open period only, so historical (closed) rows can pile up freely.
        Index(
            "uq_model_prices_current",
            "provider",
            "model_id",
            "modality",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
        # Browse/lookup path: all priced periods for a model, newest first.
        Index(
            "ix_model_prices_provider_model_modality_from",
            "provider",
            "model_id",
            "modality",
            "effective_from",
        ),
        # Two periods for the same key must not both be open AND a closed
        # period must be a real interval. (Open-period uniqueness is the
        # partial index above; this guards the interval itself.)
        UniqueConstraint(
            "provider",
            "model_id",
            "modality",
            "effective_from",
            name="uq_model_prices_period_start",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_model_prices_period_valid",
        ),
        CheckConstraint("input_price >= 0", name="ck_model_prices_input_non_negative"),
        CheckConstraint("output_price >= 0", name="ck_model_prices_output_non_negative"),
        CheckConstraint(
            "cached_input_price IS NULL OR cached_input_price >= 0",
            name="ck_model_prices_cached_input_non_negative",
        ),
        CheckConstraint(
            "context_window IS NULL OR context_window > 0",
            name="ck_model_prices_context_window_positive",
        ),
        # Defence in depth: the catalog is USD-only. The column default
        # already pins it; this rejects a stray non-USD write at the DB.
        CheckConstraint("currency = 'USD'", name="ck_model_prices_currency_usd"),
        # Los dos índices de las claves ajenas nullable, parciales sobre las
        # filas ASOCIADAS (las únicas que se buscan por ese lado): sostienen
        # «filtrar precios por proveedor» del endpoint de lectura + la UI de
        # admin y evitan una FK sin índice, que en un `ON DELETE SET NULL`
        # obliga a un seq scan de la tabla entera al borrar el padre.
        # Los crean las migraciones 0049 (`updated_by`) y 0071
        # (`provider_id`); se declaran aquí porque vivían SOLO en la migración
        # y `alembic check` los leía como deriva.
        Index(
            "ix_model_prices_provider_id",
            "provider_id",
            postgresql_where=text("provider_id IS NOT NULL"),
        ),
        Index(
            "ix_model_prices_updated_by",
            "updated_by",
            postgresql_where=text("updated_by IS NOT NULL"),
        ),
    )

    # --- catalog key -------------------------------------------------------
    # Provider family (free-form to track upstream naming, e.g.
    # "anthropic", "openai", "azure", "ollama"); the platform's closed
    # runtime catalog (ADR 0021) is a separate concern from what the price
    # feed names a model under.
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    # The provider's model identifier, e.g. "claude-sonnet-4-5",
    # "gpt-4o-2024-08-06". TEXT-width so long upstream ids never truncate.
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    modality: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'text'"))

    # --- prices (canonical USD, per `unit` tokens) -------------------------
    # High precision: per-1M prices are small decimals (e.g. 0.00000015
    # USD/token == 0.15 USD per 1M); Numeric(18, 10) keeps them exact and
    # leaves head-room above executions.total_cost_usd's (14, 6).
    input_price: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=10), nullable=False)
    output_price: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=10), nullable=False)
    # Prompt-caching cache-read price. NULL == the model does not price
    # cache reads separately. By convention ~10% of input_price, but stored
    # explicitly so the catalog mirrors the provider's published price.
    cached_input_price: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=18, scale=10), nullable=True
    )

    unit: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'per_1m_tokens'")
    )
    # Constant "USD" — the catalog is USD-only (see module docstring). Kept
    # explicit for self-documentation + a CHECK that rejects anything else.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'USD'"))

    # Maximum context window (tokens) of this model, if known. Carried
    # alongside the price because the same upstream feed reports it and
    # cost-ceiling guardrails (task_11_09) want it. NULL when unknown.
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- provenance + effective dating -------------------------------------
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'manual'"))
    effective_from: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    # NULL == the current (open) priced period. A non-NULL value closes the
    # period (the price stopped applying at that instant).
    effective_to: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # The System Admin who last wrote this row. NULL once they are deleted
    # (the price outlives the user) — mirrors platform_settings.updated_by.
    # No tenant_id: this is a platform-global catalog.
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Association to a configured platform provider (Plan 11.2 task_11_2_06).
    # Nullable: the LiteLLM sync (the catalog's main feed) only knows the
    # free-form ``provider`` family string ("anthropic", "openai", ...), not
    # which platform-global ``llm_providers`` row serves the model, so a
    # price may not (yet) be associated. ON DELETE SET NULL keeps the price +
    # its effective-dated history intact when a System Admin deletes a
    # provider config (the price outlives the provider row) — mirrors the
    # ``updated_by`` nullable-FK / SET NULL pattern. No tenant_id either:
    # ``llm_providers`` is platform-global too (ADR 0028).
    provider_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"ModelPrice(provider={self.provider!r}, model_id={self.model_id!r}, "
            f"modality={self.modality!r}, effective_to={self.effective_to!r})"
        )

    @property
    def is_current(self) -> bool:
        """True when this row is the open (current) priced period."""
        return self.effective_to is None

    def cached_input_price_or_default(self, *, fraction: Decimal = Decimal("0.1")) -> Decimal:
        """Cache-read price, falling back to ``fraction`` of input.

        When the provider does not price cache reads separately
        (``cached_input_price IS NULL``) the platform's convention is
        ~10% of the standard input price. This returns the explicit value
        when present and the convention-derived value otherwise, so
        callers (the per-call cost computation) have one number to use.
        """
        if self.cached_input_price is not None:
            return self.cached_input_price
        return self.input_price * fraction


def select_current_price(
    prices: list[ModelPrice],
    *,
    provider: str,
    model_id: str,
    modality: str | PriceModality = PriceModality.TEXT,
) -> ModelPrice | None:
    """Pick the *current* price for a ``(provider, model_id, modality)``.

    A pure helper over an already-loaded list (no DB access) — the same
    selection rule the DB partial unique index guarantees: the single row
    whose period is open (``effective_to IS NULL``) for the key. Returns
    ``None`` when the catalog has no open period for that key.

    Defensive against malformed data: if more than one open period exists
    for the key (which the DB index forbids), the one with the latest
    ``effective_from`` wins, so a caller never silently uses a stale row.
    """
    modality_value = str(modality)
    candidates = [
        p
        for p in prices
        if p.provider == provider
        and p.model_id == model_id
        and p.modality == modality_value
        and p.effective_to is None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.effective_from)


__all__ = [
    "CANONICAL_CURRENCY",
    "ModelPrice",
    "PriceModality",
    "PriceSource",
    "PriceUnit",
    "select_current_price",
]
