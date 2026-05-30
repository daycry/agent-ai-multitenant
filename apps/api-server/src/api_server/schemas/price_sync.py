"""Pydantic schemas for the price-sync endpoint (Plan 11 task_11_15).

The ``POST /admin/model-prices/sync`` endpoint reads the community LiteLLM
price JSON (a *data feed* only — ADR 0021, NOT a provider runtime) and
upserts the catalog with effective dating (Fase C). These schemas shape its
request body + the summary it returns:

  - :class:`PriceSyncRequest` — optional knobs: an override feed ``url`` (an
    internal mirror), ``confirm_large_increases`` (apply rises >10% that are
    otherwise deferred — task_11_16), and ``overwrite_manual`` (let the sync
    supersede a manual override).
  - :class:`PriceSyncResponse` — counts (fetched / created / updated /
    unchanged) plus the typed lists of skipped entries and deferred large
    increases, so the UI can surface exactly what changed / what needs
    confirmation.

USD-canonical throughout — the catalog is USD-only; the feed is normalised
to per-1M-token USD before it ever reaches the DB.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from api_server.pricing.litellm_sync import (
    LargeIncrease,
    SkippedEntry,
    SyncSummary,
)

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


class PriceSyncRequest(BaseModel):
    """Optional knobs for a sync run. An empty body runs with defaults."""

    model_config = _BASE_CONFIG

    url: str | None = Field(
        default=None,
        description="Override feed URL (e.g. an internal mirror). Default: the LiteLLM JSON.",
    )
    confirm_large_increases: bool = Field(
        default=False,
        description="Apply price rises >10% that are otherwise deferred for confirmation.",
    )
    overwrite_manual: bool = Field(
        default=False,
        description="Let the sync supersede a manually-entered (source=manual) override.",
    )


class SkippedEntryResponse(BaseModel):
    """A feed entry that could not be mapped (skipped, not a failure)."""

    model_config = _BASE_CONFIG

    model_key: str
    reason: str


class LargeIncreaseResponse(BaseModel):
    """A deferred price rise above the +10% confirmation threshold."""

    model_config = _BASE_CONFIG

    provider: str
    model_id: str
    modality: str
    field: str
    old_price: Decimal
    new_price: Decimal
    pct_increase: float


class PriceSyncResponse(BaseModel):
    """The outcome of one sync run."""

    model_config = _BASE_CONFIG

    fetched: int
    created: int
    updated: int
    unchanged: int
    changed: int
    skipped: list[SkippedEntryResponse]
    large_increases: list[LargeIncreaseResponse]


def to_sync_response(summary: SyncSummary) -> PriceSyncResponse:
    """Map the service-layer :class:`SyncSummary` to its response model."""
    return PriceSyncResponse(
        fetched=summary.fetched,
        created=summary.created,
        updated=summary.updated,
        unchanged=summary.unchanged,
        changed=summary.changed,
        skipped=[_skip(s) for s in summary.skipped],
        large_increases=[_large(li) for li in summary.large_increases],
    )


def _skip(entry: SkippedEntry) -> SkippedEntryResponse:
    return SkippedEntryResponse(model_key=entry.model_key, reason=entry.reason)


def _large(li: LargeIncrease) -> LargeIncreaseResponse:
    return LargeIncreaseResponse(
        provider=li.provider,
        model_id=li.model_id,
        modality=li.modality,
        field=li.field,
        old_price=li.old_price,
        new_price=li.new_price,
        pct_increase=li.pct_increase,
    )


__all__ = [
    "LargeIncreaseResponse",
    "PriceSyncRequest",
    "PriceSyncResponse",
    "SkippedEntryResponse",
    "to_sync_response",
]
