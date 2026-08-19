"""Que cambiaria el sync, sin escribir nada.

`compute_sync_diff` compara el feed mapeado contra las filas abiertas del
catalogo y devuelve una fila por clave con su `DiffStatus`. Aqui vive tambien
la guarda de subida grande (`LARGE_INCREASE_THRESHOLD`), que marca --no
aplica-- un salto de precio por encima del umbral.

**Este modulo es de solo lectura sobre la base**: lo que escribe esta en
:mod:`.apply`. La separacion es la que permite que la UI enseñe el diff antes
de que nadie confirme nada.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.model_prices import (
    ModelPrice,
    PriceSource,
)
from api_server.pricing.litellm_sync.feed import (
    MappedPrice,
    PriceFeedFetcher,
    SkippedEntry,
    parse_feed,
)

# A price rise above this fraction on an existing key needs explicit
# confirmation before it is applied (task_11_16). +10%.
LARGE_INCREASE_THRESHOLD = Decimal("0.10")


@dataclass(frozen=True, slots=True)
class LargeIncrease:
    """A deferred price rise above the confirmation threshold."""

    provider: str
    model_id: str
    modality: str
    field: str
    old_price: Decimal
    new_price: Decimal
    pct_increase: float


# task_11_16: a model's status in a dry-run diff (no DB write happened).
class DiffStatus(enum.StrEnum):
    """How a feed entry compares to the current catalog (dry-run diff).

    - ``added``     : the feed has a model the catalog has no open period for.
    - ``updated``   : prices changed (within the +10% guard — applies cleanly).
    - ``unchanged`` : prices match the current open period (no-op on apply).
    - ``increased`` : a rise above +10% on input/output — apply needs confirm.
    - ``removed``   : the catalog has an open period the feed no longer lists
                      (a candidate discontinued model — flagged, not deleted;
                      task_11_17 acts on it).
    """

    ADDED = "added"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    INCREASED = "increased"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class PriceDiffRow:
    """One model's old-vs-new prices in a dry-run diff (task_11_16).

    Pure data — produced by :func:`compute_sync_diff` WITHOUT any DB write.
    ``old_*`` is None for an ``added`` model; ``new_*`` is None for a
    ``removed`` (discontinued-candidate) model. Each ``*_pct`` is the
    fractional change (``new/old - 1``) on that field, or None when there is
    no sensible percentage (no old value, no new value, or old == 0).
    ``manual_skipped`` marks a row the sync would leave untouched (a manual
    override) so the UI can explain why an otherwise-changed price won't move.
    """

    provider: str
    model_id: str
    modality: str
    status: DiffStatus
    source: str
    old_input: Decimal | None
    new_input: Decimal | None
    old_output: Decimal | None
    new_output: Decimal | None
    old_cached_input: Decimal | None
    new_cached_input: Decimal | None
    input_pct: float | None
    output_pct: float | None
    manual_skipped: bool = False

    @property
    def is_large_increase(self) -> bool:
        return self.status is DiffStatus.INCREASED


@dataclass(slots=True)
class SyncDiff:
    """The result of a dry-run sync — a per-model diff, no writes (task_11_16).

    ``rows`` carries every compared model (added / updated / unchanged /
    increased / removed). ``skipped`` mirrors the parse skips. The
    convenience counters + :meth:`has_large_increase` let the UI gate the
    confirmation dialog and the endpoint gate the apply.
    """

    rows: list[PriceDiffRow] = field(default_factory=list)
    skipped: list[SkippedEntry] = field(default_factory=list)
    fetched: int = 0

    @property
    def added(self) -> int:
        return sum(1 for r in self.rows if r.status is DiffStatus.ADDED)

    @property
    def updated(self) -> int:
        return sum(1 for r in self.rows if r.status is DiffStatus.UPDATED)

    @property
    def unchanged(self) -> int:
        return sum(1 for r in self.rows if r.status is DiffStatus.UNCHANGED)

    @property
    def increased(self) -> int:
        return sum(1 for r in self.rows if r.status is DiffStatus.INCREASED)

    @property
    def removed(self) -> int:
        return sum(1 for r in self.rows if r.status is DiffStatus.REMOVED)

    @property
    def has_large_increase(self) -> bool:
        """True when any model's price rises >10% — apply needs confirmation."""
        return any(r.is_large_increase for r in self.rows)

    # --- new/discontinued lifecycle view (task_11_17) ----------------------
    # The coarse new / discontinued / changed / unchanged counts surfaced in
    # the sync summary. ``new`` == added, ``discontinued`` == removed (flagged,
    # not deleted), ``changed`` == updated + increased (the price moved).
    @property
    def new(self) -> int:
        return self.added

    @property
    def discontinued(self) -> int:
        return self.removed

    @property
    def changed(self) -> int:
        return self.updated + self.increased

    def discontinued_models(self) -> list[PriceDiffRow]:
        """The open catalog rows the feed dropped — discontinued candidates.

        Flagged, never deleted: each row's history (closed periods + the
        per-call price snapshots that reference it) must stay valid. The
        write-side helper :func:`discontinue_dropped_models` can optionally
        close their open period so they stop being "current".
        """
        return [r for r in self.rows if r.status is DiffStatus.REMOVED]

    def new_models(self) -> list[PriceDiffRow]:
        """The feed models the catalog has no open period for (brand new)."""
        return [r for r in self.rows if r.status is DiffStatus.ADDED]


# =============================================================================
# Change detection (pure)
# =============================================================================
def _prices_equal(current: ModelPrice, candidate: MappedPrice) -> bool:
    """True when the candidate prices/context match the current row exactly.

    Decimals compare by value (``Decimal("3") == Decimal("3.0")``), and a
    NULL cached price equals a NULL candidate. ``context_window`` is part of
    the comparison so a feed that only revises the window still opens a new
    period (the catalog tracks it).
    """
    return (
        current.input_price == candidate.input_price
        and current.output_price == candidate.output_price
        and current.cached_input_price == candidate.cached_input_price
        and current.context_window == candidate.context_window
    )


def _large_increase(current: ModelPrice, candidate: MappedPrice) -> LargeIncrease | None:
    """A price rise above the threshold on input/output, or None.

    Compares the input and output prices; reports the first field whose rise
    exceeds :data:`LARGE_INCREASE_THRESHOLD`. A move from 0 to a positive
    price is treated as a large increase (no sensible percentage). A price
    *drop* is never a large increase.
    """
    for field_name, old, new in (
        ("input_price", current.input_price, candidate.input_price),
        ("output_price", current.output_price, candidate.output_price),
    ):
        if new <= old:
            continue
        # A move from 0 to a positive price has no sensible percentage —
        # treat it as an (infinite) large increase. Otherwise compare the
        # fractional rise in exact Decimal against the threshold.
        is_large = old == 0 or (new - old) / old > LARGE_INCREASE_THRESHOLD
        if is_large:
            pct = float("inf") if old == 0 else float((new - old) / old)
            return LargeIncrease(
                provider=current.provider,
                model_id=current.model_id,
                modality=current.modality,
                field=field_name,
                old_price=old,
                new_price=new,
                pct_increase=pct,
            )
    return None


def _pct_change(old: Decimal, new: Decimal) -> float | None:
    """Fractional change ``new/old - 1`` as a float, or None when undefined.

    None when ``old`` is 0 (a move from free to priced has no percentage).
    Exact Decimal arithmetic before the final float cast keeps small per-1M
    prices honest.
    """
    if old == 0:
        return None
    return float((new - old) / old)


# =============================================================================
# Dry-run diff (task_11_16) — fetch + compare, NO DB writes
# =============================================================================
async def _open_catalog_rows(session: AsyncSession) -> list[ModelPrice]:
    """Every current (open-period) catalog row — the dry-run comparison base."""
    result = await session.execute(select(ModelPrice).where(ModelPrice.effective_to.is_(None)))
    return list(result.scalars().all())


def _diff_row(current: ModelPrice | None, candidate: MappedPrice) -> PriceDiffRow:
    """Build one diff row for a feed entry against its current catalog row."""
    if current is None:
        return PriceDiffRow(
            provider=candidate.provider,
            model_id=candidate.model_id,
            modality=candidate.modality.value,
            status=DiffStatus.ADDED,
            source=PriceSource.LITELLM.value,
            old_input=None,
            new_input=candidate.input_price,
            old_output=None,
            new_output=candidate.output_price,
            old_cached_input=None,
            new_cached_input=candidate.cached_input_price,
            input_pct=None,
            output_pct=None,
        )

    manual_skipped = current.source == PriceSource.MANUAL.value
    if _prices_equal(current, candidate):
        status = DiffStatus.UNCHANGED
    elif _large_increase(current, candidate) is not None:
        status = DiffStatus.INCREASED
    else:
        status = DiffStatus.UPDATED

    return PriceDiffRow(
        provider=current.provider,
        model_id=current.model_id,
        modality=current.modality,
        status=status,
        source=current.source,
        old_input=current.input_price,
        new_input=candidate.input_price,
        old_output=current.output_price,
        new_output=candidate.output_price,
        old_cached_input=current.cached_input_price,
        new_cached_input=candidate.cached_input_price,
        input_pct=_pct_change(current.input_price, candidate.input_price),
        output_pct=_pct_change(current.output_price, candidate.output_price),
        manual_skipped=manual_skipped and status is not DiffStatus.UNCHANGED,
    )


async def compute_sync_diff(
    session: AsyncSession,
    *,
    fetcher: PriceFeedFetcher,
    allowed_families: frozenset[str] | None = None,
) -> SyncDiff:
    """Compute a per-model diff of the feed vs the catalog — NO writes (task_11_16).

    The dry-run half of the two-step sync flow: fetch + parse the feed, then
    compare each mapped entry to its current open-period catalog row. Every
    compared model becomes a :class:`PriceDiffRow` (added / updated /
    unchanged / increased) carrying old-vs-new prices + % change. Open
    catalog rows the feed no longer lists are emitted as ``removed``
    (discontinued candidates — flagged, never deleted here). Malformed feed
    entries are captured as typed skips. This function NEVER mutates the DB,
    so the UI can show the diff before the human confirms the apply.

    ``allowed_families`` (plan price-sync-active-providers, task_psa_01) is the
    family allowlist derived from the active providers: feed entries of an
    out-of-scope family are dropped as ``family_not_active`` skips (never
    ``added``), and any open catalog row of an out-of-scope family — like a row
    the feed dropped — is emitted as ``removed``. ``None`` disables the filter.
    """
    payload = await fetcher.fetch()
    mapped, skipped = parse_feed(payload, allowed_families=allowed_families)

    diff = SyncDiff(skipped=skipped, fetched=len(mapped))

    open_rows = await _open_catalog_rows(session)
    by_key: dict[tuple[str, str, str], ModelPrice] = {
        (r.provider, r.model_id, r.modality): r for r in open_rows
    }
    seen: set[tuple[str, str, str]] = set()

    for candidate in mapped:
        key = (candidate.provider, candidate.model_id, candidate.modality.value)
        seen.add(key)
        diff.rows.append(_diff_row(by_key.get(key), candidate))

    # Open catalog rows absent from the feed → discontinued candidates.
    for key, row in by_key.items():
        if key in seen:
            continue
        diff.rows.append(
            PriceDiffRow(
                provider=row.provider,
                model_id=row.model_id,
                modality=row.modality,
                status=DiffStatus.REMOVED,
                source=row.source,
                old_input=row.input_price,
                new_input=None,
                old_output=row.output_price,
                new_output=None,
                old_cached_input=row.cached_input_price,
                new_cached_input=None,
                input_pct=None,
                output_pct=None,
            )
        )

    return diff
