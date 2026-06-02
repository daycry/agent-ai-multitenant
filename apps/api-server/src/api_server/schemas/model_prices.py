"""Pydantic schemas for the `/admin/model-prices` catalog endpoints (Plan 11 task_11_12).

The price-catalog REST surface (System-Admin write, authenticated read)
has one read shape and two write shapes:

  - :class:`ModelPriceResponse` — a catalog row as returned by list /
    get / current-price lookup. It echoes every public column straight
    off the ORM (``from_attributes``); the catalog has no secret-adjacent
    fields (prices are public reference data).

  - :class:`ModelPriceCreateRequest` — create a new priced period for a
    ``(provider, model_id, modality)`` key. **USD-canonical**: there is
    NO ``currency`` field on the wire — the catalog is USD-only (the ORM
    default + DB CHECK pin it), so the schema never invents a currency
    knob nor accepts a conversion. ``effective_to`` / ``updated_by`` /
    ``id`` / timestamps are server-derived, never honoured from the wire.

  - :class:`ModelPriceUpdateRequest` — patch the mutable fields of an
    existing row (a full-document PUT semantics is avoided so a partial
    edit of, say, just ``output_price`` does not require re-sending the
    whole row). The catalog key (``provider`` / ``model_id`` /
    ``modality``) is immutable — changing it would mean a different
    catalog line, so it is not patchable; create a new row instead.

Prices are quoted **per ``unit`` tokens** (default per-1M) in canonical
USD. ``cached_input_price`` (prompt caching, a cache-read price) is
**optional** — most providers do not price cache reads separately — and
when omitted the catalog falls back to ~10% of ``input_price`` via the
ORM helper (never stored implicitly). All money fields are validated
non-negative on the wire (a clean 422) on top of the DB CHECK.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.model_prices import (
    CANONICAL_CURRENCY,
    ModelPrice,
    PriceModality,
    PriceSource,
    PriceUnit,
)

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

# The wire bound on a money field — matches the ORM's Numeric(18, 10):
# non-negative and exact. Pydantic enforces non-negativity here as a
# clean 422 before the row ever reaches the DB CHECK.
_money_field = Field(ge=0, description="Price in canonical USD, per `unit` tokens. Non-negative.")


# =============================================================================
# Response (list / get / current-price lookup)
# =============================================================================
class ModelPriceResponse(BaseModel):
    """A price-catalog row as exposed to the read endpoints.

    Echoes every column off the ORM; ``effective_to is None`` marks the
    current (open) priced period. ``currency`` is always ``"USD"`` (the
    catalog is USD-only) and is surfaced for self-documentation, not as a
    conversion knob.
    """

    model_config = _BASE_CONFIG

    id: UUID
    provider: str
    model_id: str
    modality: str
    input_price: Decimal
    output_price: Decimal
    cached_input_price: Decimal | None
    unit: str
    currency: str
    context_window: int | None
    source: str
    # Association to a configured platform provider (task_11_2_06). NULL
    # when the price is not (yet) associated — the LiteLLM sync only knows
    # the free-form ``provider`` family string, not the platform provider
    # row. A System Admin associates it explicitly; the UI surfaces it
    # (read) and filters by it.
    provider_id: UUID | None
    effective_from: datetime
    effective_to: datetime | None
    updated_by: UUID | None
    created_at: datetime
    updated_at: datetime


def to_price_response(price: ModelPrice) -> ModelPriceResponse:
    """Map an ORM ``ModelPrice`` to its response model."""
    return ModelPriceResponse.model_validate(price, from_attributes=True)


# =============================================================================
# Create (new priced period)
# =============================================================================
class ModelPriceCreateRequest(BaseModel):
    """Create a new priced period for a ``(provider, model_id, modality)``.

    USD-canonical: there is intentionally NO ``currency`` field — the
    catalog is USD-only. ``effective_to`` is server-managed (a new row is
    the open/current period; the supersede flow closes the prior open
    row) and is therefore not accepted here. ``source`` defaults to
    ``manual`` (a System Admin hand-entry); a sync (task_11_15) sets
    ``litellm`` / ``provider_api`` explicitly.
    """

    model_config = _BASE_CONFIG

    provider: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=255)
    modality: PriceModality = PriceModality.TEXT
    input_price: Decimal = _money_field
    output_price: Decimal = _money_field
    # Prompt-caching cache-read price. Optional — NULL means the model
    # does not price cache reads separately (helper falls back to ~10% of
    # input_price). Never derived implicitly on write.
    cached_input_price: Decimal | None = Field(
        default=None,
        ge=0,
        description="Optional cache-read (prompt-caching) price in USD; NULL if not priced.",
    )
    unit: PriceUnit = PriceUnit.PER_1M_TOKENS
    context_window: int | None = Field(default=None, gt=0)
    source: PriceSource = PriceSource.MANUAL
    # Optional association to a configured platform provider
    # (``llm_providers.id``, task_11_2_06). NULL == unassociated. The FK is
    # validated at the DB; an unknown id surfaces as a clean 422 at the
    # router. Not a secret (only a row id).
    provider_id: UUID | None = Field(
        default=None,
        description="Optional llm_providers.id this price is served by; NULL if unassociated.",
    )


# =============================================================================
# Update (patch mutable fields of an existing row)
# =============================================================================
class ModelPriceUpdateRequest(BaseModel):
    """Patch the mutable fields of an existing catalog row.

    The catalog key (``provider`` / ``model_id`` / ``modality``) is
    immutable — editing it would mean a different catalog line, so it is
    not patchable. Every field is optional; an omitted field is left
    unchanged. ``effective_to`` is not editable here (period closing is
    the supersede flow). At least one field must be present (the router
    rejects an empty patch as 422).
    """

    model_config = _BASE_CONFIG

    input_price: Decimal | None = Field(default=None, ge=0)
    output_price: Decimal | None = Field(default=None, ge=0)
    cached_input_price: Decimal | None = Field(default=None, ge=0)
    unit: PriceUnit | None = None
    context_window: int | None = Field(default=None, gt=0)
    source: PriceSource | None = None
    # Associate / disassociate the price with a platform provider
    # (task_11_2_06). Sending ``provider_id: null`` explicitly clears the
    # association (``exclude_unset`` distinguishes "omitted" from "set to
    # NULL"); omitting it leaves the association unchanged.
    provider_id: UUID | None = Field(default=None)

    def has_changes(self) -> bool:
        """True when at least one field was supplied on the wire."""
        return bool(self.model_dump(exclude_unset=True))


__all__ = [
    "CANONICAL_CURRENCY",
    "ModelPriceCreateRequest",
    "ModelPriceResponse",
    "ModelPriceUpdateRequest",
    "to_price_response",
]
