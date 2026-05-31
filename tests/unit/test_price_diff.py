"""Unit tests for new + discontinued model detection (Plan 11 task_11_17).

The classification this task adds is a **pure, deterministic** function:
:func:`classify_models` takes the mapped feed entries and the catalog's
*current* (open-period) rows and decides each model's lifecycle status —
``new`` / ``discontinued`` / ``changed`` / ``unchanged`` — without touching a
database or the network. So these tests are in-process: they build
``ModelPrice`` ORM objects in memory and ``MappedPrice`` value objects, with no
DB session and no fixture feed fetch.

Coverage:

  - a feed model the catalog lacks            -> NEW;
  - a catalog model absent from the feed      -> DISCONTINUED (and the helper
    that flags it CLOSES the open period rather than deleting the row);
  - a changed price                           -> CHANGED, carrying the % change;
  - an identical price                        -> UNCHANGED;
  - the classification is pure + deterministic (idempotent, order-independent).

The DB-backed apply/dry-run paths (effective dating, RLS, the endpoints) live in
``tests/integration/test_prices_diff_confirm.py``; here we pin only the pure core.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from api_server.db.model_prices import ModelPrice, PriceModality, PriceSource, PriceUnit
from api_server.pricing.litellm_sync import (
    MappedPrice,
    ModelStatus,
    classify_models,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# In-memory builders (no DB).
# ---------------------------------------------------------------------------
def _row(
    *,
    provider: str,
    model_id: str,
    modality: PriceModality = PriceModality.TEXT,
    input_price: str = "3.0",
    output_price: str = "15.0",
    cached_input_price: str | None = None,
    context_window: int | None = 200000,
    source: PriceSource = PriceSource.LITELLM,
    effective_to: datetime | None = None,
) -> ModelPrice:
    """A current (open-period) catalog row, in memory — no DB needed."""
    return ModelPrice(
        provider=provider,
        model_id=model_id,
        modality=modality.value,
        input_price=Decimal(input_price),
        output_price=Decimal(output_price),
        cached_input_price=None if cached_input_price is None else Decimal(cached_input_price),
        unit=PriceUnit.PER_1M_TOKENS.value,
        context_window=context_window,
        source=source.value,
        effective_to=effective_to,
    )


def _feed_entry(
    *,
    provider: str,
    model_id: str,
    modality: PriceModality = PriceModality.TEXT,
    input_price: str = "3.0",
    output_price: str = "15.0",
    cached_input_price: str | None = None,
    context_window: int | None = 200000,
) -> MappedPrice:
    """A mapped feed entry (already normalised to per-1M USD)."""
    return MappedPrice(
        provider=provider,
        model_id=model_id,
        modality=modality,
        input_price=Decimal(input_price),
        output_price=Decimal(output_price),
        cached_input_price=None if cached_input_price is None else Decimal(cached_input_price),
        context_window=context_window,
    )


# ===========================================================================
# NEW: a feed model the catalog has no open period for.
# ===========================================================================
def test_feed_model_absent_from_catalog_is_new() -> None:
    feed = [_feed_entry(provider="openai", model_id="gpt-brand-new")]
    catalog: list[ModelPrice] = []

    result = classify_models(feed, catalog)

    assert result.new_count == 1
    assert result.discontinued_count == 0
    assert result.changed_count == 0
    assert result.unchanged_count == 0
    only = result.new[0]
    assert only.status is ModelStatus.NEW
    assert (only.provider, only.model_id) == ("openai", "gpt-brand-new")
    # NEW has no old price -> no percentage.
    assert only.input_pct is None
    assert only.output_pct is None


# ===========================================================================
# DISCONTINUED: a catalog model the feed no longer lists.
# ===========================================================================
def test_catalog_model_absent_from_feed_is_discontinued() -> None:
    feed = [_feed_entry(provider="anthropic", model_id="claude-sonnet-4-5")]
    catalog = [
        _row(provider="anthropic", model_id="claude-sonnet-4-5"),
        _row(provider="openai", model_id="gpt-3.5-legacy"),  # dropped by the feed
    ]

    result = classify_models(feed, catalog)

    assert result.discontinued_count == 1
    gone = result.discontinued[0]
    assert gone.status is ModelStatus.DISCONTINUED
    assert (gone.provider, gone.model_id) == ("openai", "gpt-3.5-legacy")
    # The other model is in the feed unchanged.
    assert result.unchanged_count == 1


def test_discontinued_model_is_flagged_not_deleted() -> None:
    """The detection NEVER drops the row — it just classifies it discontinued.

    ``classify_models`` is pure: it returns a verdict and mutates nothing. The
    catalog list it was handed still contains the discontinued row afterwards,
    so historical periods + the per-call price snapshots that reference it stay
    valid. (Closing the open period is the explicit write-side concern of
    ``discontinue_dropped_models``, covered in the integration suite.)
    """
    legacy = _row(provider="openai", model_id="gpt-3.5-legacy")
    catalog = [legacy]
    feed: list[MappedPrice] = []  # the feed dropped it

    result = classify_models(feed, catalog)

    assert result.discontinued_count == 1
    assert result.discontinued[0].model_id == "gpt-3.5-legacy"
    # NOT deleted: the row object is untouched and still open.
    assert legacy in catalog
    assert legacy.effective_to is None
    assert legacy.is_current is True


# ===========================================================================
# CHANGED: a price that moved, carrying the % change.
# ===========================================================================
def test_changed_price_is_changed_with_pct() -> None:
    catalog = [
        _row(
            provider="anthropic",
            model_id="claude-sonnet-4-5",
            input_price="3.0",
            output_price="15.0",
        )
    ]
    feed = [
        _feed_entry(
            provider="anthropic",
            model_id="claude-sonnet-4-5",
            input_price="6.0",  # 3.0 -> 6.0 == +100%
            output_price="15.0",
        )
    ]

    result = classify_models(feed, catalog)

    assert result.changed_count == 1
    row = result.changed[0]
    assert row.status is ModelStatus.CHANGED
    assert row.input_pct is not None
    assert abs(row.input_pct - 1.0) < 1e-9  # +100%
    # output unchanged -> 0% change (defined, not None).
    assert row.output_pct == 0.0


def test_changed_includes_a_within_threshold_move() -> None:
    """A small (<=10%) move is still CHANGED — the coarse status is binary."""
    catalog = [_row(provider="anthropic", model_id="m", output_price="15.0")]
    feed = [_feed_entry(provider="anthropic", model_id="m", output_price="16.0")]  # +6.7%

    result = classify_models(feed, catalog)

    assert result.changed_count == 1
    assert result.changed[0].output_pct is not None
    assert abs(result.changed[0].output_pct - (1.0 / 15.0)) < 1e-9


def test_context_window_only_change_is_changed() -> None:
    """A revised context window (prices identical) still counts as CHANGED."""
    catalog = [_row(provider="anthropic", model_id="m", context_window=200000)]
    feed = [_feed_entry(provider="anthropic", model_id="m", context_window=400000)]

    result = classify_models(feed, catalog)

    assert result.changed_count == 1
    # Prices identical -> no percentage change on either side.
    assert result.changed[0].input_pct == 0.0
    assert result.changed[0].output_pct == 0.0


# ===========================================================================
# UNCHANGED: identical prices.
# ===========================================================================
def test_identical_price_is_unchanged() -> None:
    catalog = [
        _row(
            provider="anthropic",
            model_id="claude-sonnet-4-5",
            input_price="3.0",
            output_price="15.0",
            cached_input_price="0.30",
        )
    ]
    feed = [
        _feed_entry(
            provider="anthropic",
            model_id="claude-sonnet-4-5",
            input_price="3.0",
            output_price="15.0",
            cached_input_price="0.30",
        )
    ]

    result = classify_models(feed, catalog)

    assert result.unchanged_count == 1
    assert result.changed_count == 0
    assert result.unchanged[0].status is ModelStatus.UNCHANGED


def test_decimal_scale_difference_is_still_unchanged() -> None:
    """``Decimal('3.0') == Decimal('3.00')`` by value -> not a spurious change."""
    catalog = [_row(provider="anthropic", model_id="m", input_price="3.0", output_price="15.00")]
    feed = [
        _feed_entry(provider="anthropic", model_id="m", input_price="3.00", output_price="15.0")
    ]

    result = classify_models(feed, catalog)

    assert result.unchanged_count == 1
    assert result.changed_count == 0


# ===========================================================================
# Modality is part of the key (same model_id, different modality).
# ===========================================================================
def test_modality_is_part_of_the_key() -> None:
    """Same provider/model_id but a modality the catalog lacks -> NEW, not changed."""
    catalog = [
        _row(provider="openai", model_id="text-embedding-3", modality=PriceModality.EMBEDDING)
    ]
    feed = [
        _feed_entry(
            provider="openai", model_id="text-embedding-3", modality=PriceModality.EMBEDDING
        ),
        _feed_entry(provider="openai", model_id="text-embedding-3", modality=PriceModality.TEXT),
    ]

    result = classify_models(feed, catalog)

    assert result.unchanged_count == 1  # the embedding modality matches
    assert result.new_count == 1  # the text modality is new
    assert result.new[0].modality == PriceModality.TEXT.value


# ===========================================================================
# Manual rows are flagged so the UI can explain why a changed price won't move.
# ===========================================================================
def test_manual_changed_row_is_flagged_manual() -> None:
    catalog = [
        _row(provider="anthropic", model_id="m", input_price="3.0", source=PriceSource.MANUAL)
    ]
    feed = [_feed_entry(provider="anthropic", model_id="m", input_price="6.0")]

    result = classify_models(feed, catalog)

    assert result.changed_count == 1
    assert result.changed[0].manual is True


# ===========================================================================
# A mixed feed: one of each status, with the right counts + lists.
# ===========================================================================
def test_mixed_feed_classifies_each_bucket() -> None:
    catalog = [
        _row(provider="anthropic", model_id="claude", input_price="3.0", output_price="15.0"),
        _row(provider="openai", model_id="gpt-4o", input_price="2.5", output_price="10.0"),
        _row(provider="openai", model_id="gpt-3.5-legacy"),  # dropped
    ]
    feed = [
        # unchanged
        _feed_entry(
            provider="anthropic", model_id="claude", input_price="3.0", output_price="15.0"
        ),
        # changed
        _feed_entry(provider="openai", model_id="gpt-4o", input_price="3.0", output_price="10.0"),
        # new
        _feed_entry(provider="openai", model_id="gpt-5", input_price="5.0", output_price="20.0"),
    ]

    result = classify_models(feed, catalog)

    assert result.new_count == 1 and result.new[0].model_id == "gpt-5"
    assert result.discontinued_count == 1 and result.discontinued[0].model_id == "gpt-3.5-legacy"
    assert result.changed_count == 1 and result.changed[0].model_id == "gpt-4o"
    assert result.unchanged_count == 1 and result.unchanged[0].model_id == "claude"


# ===========================================================================
# Pure + deterministic: idempotent + order-independent.
# ===========================================================================
def test_classification_is_pure_and_deterministic() -> None:
    catalog = [
        _row(provider="openai", model_id="gpt-4o", input_price="2.5"),
        _row(provider="anthropic", model_id="claude"),
        _row(provider="openai", model_id="legacy"),  # discontinued
    ]
    feed = [
        _feed_entry(provider="openai", model_id="gpt-5"),  # new
        _feed_entry(provider="anthropic", model_id="claude"),  # unchanged
        _feed_entry(provider="openai", model_id="gpt-4o", input_price="3.0"),  # changed
    ]

    # Reversing the input order must not change the verdict or list order
    # (each bucket is sorted by provider/model_id/modality).
    first = classify_models(list(feed), list(catalog))
    again = classify_models(list(reversed(feed)), list(reversed(catalog)))

    def _ids(rows: list) -> list[tuple[str, str, str]]:
        return [(r.provider, r.model_id, r.modality) for r in rows]

    assert _ids(first.new) == _ids(again.new)
    assert _ids(first.discontinued) == _ids(again.discontinued)
    assert _ids(first.changed) == _ids(again.changed)
    assert _ids(first.unchanged) == _ids(again.unchanged)

    # Idempotent: running it twice on the same inputs yields the same counts.
    twice = classify_models(list(feed), list(catalog))
    assert (
        twice.new_count,
        twice.discontinued_count,
        twice.changed_count,
        twice.unchanged_count,
    ) == (
        first.new_count,
        first.discontinued_count,
        first.changed_count,
        first.unchanged_count,
    )

    # No mutation of the inputs (pure): the catalog rows are untouched.
    assert all(r.effective_to is None for r in catalog)


# ===========================================================================
# Empty inputs are well-defined.
# ===========================================================================
def test_empty_feed_and_catalog_yields_empty_classification() -> None:
    result = classify_models([], [])
    assert result.new_count == 0
    assert result.discontinued_count == 0
    assert result.changed_count == 0
    assert result.unchanged_count == 0


def test_empty_feed_with_catalog_discontinues_all() -> None:
    catalog = [
        _row(provider="openai", model_id="a"),
        _row(provider="anthropic", model_id="b"),
    ]
    result = classify_models([], catalog)
    assert result.discontinued_count == 2
    # Deterministic order: sorted by provider then model_id.
    assert [r.model_id for r in result.discontinued] == ["b", "a"]
