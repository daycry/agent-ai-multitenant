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
    PriceDiffRow,
    SkippedEntry,
    SyncDiff,
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


class PriceSyncDiffRequest(BaseModel):
    """Body for the dry-run diff (task_11_16). Only the feed URL is relevant."""

    model_config = _BASE_CONFIG

    url: str | None = Field(
        default=None,
        description="Override feed URL (e.g. an internal mirror). Default: the LiteLLM JSON.",
    )


class PriceSyncApplyRequest(BaseModel):
    """Body for the two-step APPLY (task_11_16).

    ``confirm`` is the mandatory-confirmation gate: when ANY price rises
    >10%, the apply is rejected (409) unless ``confirm`` is True, so a human
    explicitly reviews the spike before the catalog is written.
    """

    model_config = _BASE_CONFIG

    url: str | None = Field(
        default=None,
        description="Override feed URL (e.g. an internal mirror). Default: the LiteLLM JSON.",
    )
    confirm: bool = Field(
        default=False,
        description="Confirm a >10% price rise; required for the apply to proceed when one exists.",
    )
    overwrite_manual: bool = Field(
        default=False,
        description="Let the apply supersede a manually-entered (source=manual) override.",
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


# =============================================================================
# Dry-run diff response (task_11_16)
# =============================================================================
class PriceDiffRowResponse(BaseModel):
    """One model's old-vs-new prices in a dry-run diff.

    ``status`` is one of ``added`` / ``updated`` / ``unchanged`` /
    ``increased`` (a >10% rise needing confirmation) / ``removed`` (a
    discontinued candidate the feed dropped — flagged, not deleted). ``old_*``
    is null for an added model; ``new_*`` is null for a removed one.
    ``*_pct`` is the fractional change on that field (null when undefined).
    ``manual_skipped`` flags a changed manual override the sync leaves alone.
    """

    model_config = _BASE_CONFIG

    provider: str
    model_id: str
    modality: str
    status: str
    source: str
    old_input: Decimal | None
    new_input: Decimal | None
    old_output: Decimal | None
    new_output: Decimal | None
    old_cached_input: Decimal | None
    new_cached_input: Decimal | None
    input_pct: float | None
    output_pct: float | None
    manual_skipped: bool


class PriceSyncDiffResponse(BaseModel):
    """A dry-run diff of the feed vs the catalog — NO write happened.

    The UI renders ``rows`` and gates its confirmation dialog on
    ``has_large_increase``: when True, the subsequent APPLY must pass
    ``confirm=true`` or the backend rejects it (409).
    """

    model_config = _BASE_CONFIG

    fetched: int
    added: int
    updated: int
    unchanged: int
    increased: int
    removed: int
    has_large_increase: bool
    rows: list[PriceDiffRowResponse]
    skipped: list[SkippedEntryResponse]


def _diff_row(row: PriceDiffRow) -> PriceDiffRowResponse:
    return PriceDiffRowResponse(
        provider=row.provider,
        model_id=row.model_id,
        modality=row.modality,
        status=str(row.status),
        source=row.source,
        old_input=row.old_input,
        new_input=row.new_input,
        old_output=row.old_output,
        new_output=row.new_output,
        old_cached_input=row.old_cached_input,
        new_cached_input=row.new_cached_input,
        input_pct=row.input_pct,
        output_pct=row.output_pct,
        manual_skipped=row.manual_skipped,
    )


def to_diff_response(diff: SyncDiff) -> PriceSyncDiffResponse:
    """Map the service-layer :class:`SyncDiff` to its response model."""
    return PriceSyncDiffResponse(
        fetched=diff.fetched,
        added=diff.added,
        updated=diff.updated,
        unchanged=diff.unchanged,
        increased=diff.increased,
        removed=diff.removed,
        has_large_increase=diff.has_large_increase,
        rows=[_diff_row(r) for r in diff.rows],
        skipped=[_skip(s) for s in diff.skipped],
    )


__all__ = [
    "LargeIncreaseResponse",
    "PriceDiffRowResponse",
    "PriceSyncApplyRequest",
    "PriceSyncDiffRequest",
    "PriceSyncDiffResponse",
    "PriceSyncRequest",
    "PriceSyncResponse",
    "SkippedEntryResponse",
    "to_diff_response",
    "to_sync_response",
]
