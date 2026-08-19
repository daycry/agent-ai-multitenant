"""Escribir el catalogo, con efectividad por periodos.

Un precio que cambia no se actualiza en sitio: se CIERRA la fila abierta
(`effective_to = now()`) y se inserta una nueva. Asi el precio viejo sobrevive
para los snapshots historicos (task_11_13).

Dos caminos: `sync_prices_from_litellm` (feed -> catalogo de una pasada) y
`apply_sync_from_litellm` (aplicar un diff ya calculado y confirmado por un
humano). Ninguno pisa una fila `manual` sin `overwrite_manual=True`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from operator import attrgetter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.model_prices import (
    ModelPrice,
    PriceSource,
    PriceUnit,
)
from api_server.pricing.litellm_sync.diff import (
    LARGE_INCREASE_THRESHOLD,
    LargeIncrease,
    _large_increase,
    _prices_equal,
)
from api_server.pricing.litellm_sync.feed import (
    MappedPrice,
    PriceFeedFetcher,
    SkippedEntry,
    parse_feed,
)
from api_server.pricing.litellm_sync.retire import (
    DiscontinuedModel,
    close_out_of_scope_families,
    discontinue_dropped_models,
)


class LargeIncreaseNotConfirmedError(Exception):
    """Apply was blocked: a price rises >10% and ``confirm`` was not passed.

    Carries the offending rows so the caller (the endpoint → a 409) can tell
    the human exactly which models spiked and need an explicit review before
    the catalog is written.
    """

    def __init__(self, increases: list[LargeIncrease]) -> None:
        self.increases = increases
        super().__init__(
            f"{len(increases)} model price(s) rose more than "
            f"{LARGE_INCREASE_THRESHOLD:%}; explicit confirmation required"
        )


@dataclass(slots=True)
class SyncSummary:
    """The outcome of one sync run (also the endpoint response payload)."""

    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    # task_11_17: open catalog periods the feed no longer lists that this run
    # closed (flagged discontinued). 0 unless ``discontinue_missing`` is set.
    discontinued: int = 0
    skipped: list[SkippedEntry] = field(default_factory=list)
    large_increases: list[LargeIncrease] = field(default_factory=list)
    discontinued_models: list[DiscontinuedModel] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    @property
    def changed(self) -> int:
        return self.created + self.updated

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


# =============================================================================
# The sync (DB writes; System-Admin session)
# =============================================================================
async def _current_price(session: AsyncSession, candidate: MappedPrice) -> ModelPrice | None:
    result = await session.execute(
        select(ModelPrice).where(
            ModelPrice.provider == candidate.provider,
            ModelPrice.model_id == candidate.model_id,
            ModelPrice.modality == candidate.modality.value,
            ModelPrice.effective_to.is_(None),
        )
    )
    return result.scalar_one_or_none()


def _new_row(candidate: MappedPrice, *, actor_id: UUID | None) -> ModelPrice:
    return ModelPrice(
        provider=candidate.provider,
        model_id=candidate.model_id,
        modality=candidate.modality.value,
        input_price=candidate.input_price,
        output_price=candidate.output_price,
        cached_input_price=candidate.cached_input_price,
        unit=PriceUnit.PER_1M_TOKENS.value,
        context_window=candidate.context_window,
        source=PriceSource.LITELLM.value,
        updated_by=actor_id,
    )


async def sync_prices_from_litellm(
    session: AsyncSession,
    *,
    fetcher: PriceFeedFetcher,
    actor_id: UUID | None = None,
    confirm_large_increases: bool = False,
    overwrite_manual: bool = False,
    allowed_families: frozenset[str] | None = None,
) -> SyncSummary:
    """Refresh the catalog from the LiteLLM feed; return a typed summary.

    Per mapped entry, against the current open-period row for its key:

      - none           → INSERT open row (``created``);
      - changed price  → CLOSE current + INSERT open row (``updated``),
        unless it is a manual override (left unless ``overwrite_manual``) or
        a large increase awaiting confirmation (deferred);
      - unchanged      → no-op (``unchanged``).

    Malformed feed entries are skipped (typed :class:`SkippedEntry`), never a
    crash. The caller commits the session (the endpoint's admin-session
    dependency does so on exit); on a feed/transport error this raises
    :class:`PriceFeedError` before any write.

    ``allowed_families`` (plan price-sync-active-providers, task_psa_01) filters
    the sync to the families of the ACTIVE providers: a feed entry of an
    out-of-scope family is skipped (``family_not_active``, NOT added), and any
    open catalog period of an out-of-scope family is CLOSED — treated as
    discontinued (its row + history + snapshots survive, never hard-deleted) and
    counted under ``discontinued``. ``None`` disables the filter
    (backward-compatible: the old unfiltered behaviour). An EMPTY frozenset adds
    nothing and closes every open period (no active provider ⇒ sync nothing)."""
    payload = await fetcher.fetch()
    mapped, skipped = parse_feed(payload, allowed_families=allowed_families)

    summary = SyncSummary(fetched=len(mapped), skipped=skipped)
    now = datetime.now(tz=UTC)

    for candidate in mapped:
        current = await _current_price(session, candidate)

        if current is None:
            session.add(_new_row(candidate, actor_id=actor_id))
            summary.created += 1
            continue

        if _prices_equal(current, candidate):
            summary.unchanged += 1
            continue

        # A deliberate manual override is not stomped by a sync unless asked.
        if current.source == PriceSource.MANUAL.value and not overwrite_manual:
            summary.unchanged += 1
            continue

        big = _large_increase(current, candidate)
        if big is not None and not confirm_large_increases:
            summary.large_increases.append(big)
            continue

        # Effective-dating: close the current period, open a new one.
        current.effective_to = now
        current.updated_by = actor_id
        session.add(current)
        await session.flush()  # release the partial-unique open-period slot
        session.add(_new_row(candidate, actor_id=actor_id))
        summary.updated += 1

    # Close the open periods of any family that is NOT in the allowlist — their
    # provider kind is no longer an active provider, so the model is out of
    # scope. Discontinued (row + history kept), never hard-deleted.
    if allowed_families is not None:
        closed = await close_out_of_scope_families(
            session,
            allowed_families,
            actor_id=actor_id,
            overwrite_manual=overwrite_manual,
            now=now,
        )
        summary.discontinued_models = closed
        summary.discontinued = len(closed)

    await session.flush()
    return summary


# =============================================================================
# Apply with mandatory confirmation on a >10% rise (task_11_16)
# =============================================================================
async def _pending_large_increases(
    session: AsyncSession, mapped: list[MappedPrice]
) -> list[LargeIncrease]:
    """The >10% rises a write would apply, computed WITHOUT writing.

    Only counts rises that would actually be applied: a brand-new model has
    nothing to compare; an unchanged price is a no-op. (Manual overrides are
    *not* exempted here — a manual row that the feed wants to raise >10% is
    still surfaced for confirmation, even though the write itself leaves it
    unless ``overwrite_manual`` is set.)
    """
    pending: list[LargeIncrease] = []
    for candidate in mapped:
        current = await _current_price(session, candidate)
        if current is None:
            continue
        big = _large_increase(current, candidate)
        if big is not None:
            pending.append(big)
    return pending


async def apply_sync_from_litellm(
    session: AsyncSession,
    *,
    fetcher: PriceFeedFetcher,
    actor_id: UUID | None = None,
    confirm: bool = False,
    overwrite_manual: bool = False,
    discontinue_missing: bool = False,
    allowed_families: frozenset[str] | None = None,
) -> SyncSummary:
    """Apply the feed — but REJECT the whole apply on an unconfirmed >10% rise.

    The "apply" half of the two-step flow (task_11_16). Unlike the lower-level
    :func:`sync_prices_from_litellm` (which *defers* individual large rises and
    applies the rest), this enforces a **mandatory confirmation gate**: if ANY
    model's price rises more than +10% and ``confirm`` is False, it raises
    :class:`LargeIncreaseNotConfirmedError` BEFORE writing anything, so a human
    must review the spike. With ``confirm=True`` every change — including the
    spikes — is applied. This is the gate the endpoint maps to a 409 and the UI
    gates its confirmation dialog on (via the dry-run diff).

    When ``discontinue_missing`` is True (task_11_17), any open catalog period
    whose key the feed no longer lists is **flagged discontinued**: its open
    period is closed (``effective_to = now``) so it stops being the current
    price, but the row is **never deleted** — its history + the per-call price
    snapshots that reference it stay valid. Manual rows are left alone unless
    ``overwrite_manual`` is also set (a deliberate manual price is not dropped
    just because the community feed omits the model).

    ``allowed_families`` (plan price-sync-active-providers, task_psa_01) filters
    to the active providers' families: a feed entry of an out-of-scope family is
    a ``family_not_active`` skip (never added), and any open catalog period of an
    out-of-scope family is CLOSED (treated as discontinued — row + history kept,
    never hard-deleted). This runs independently of ``discontinue_missing``:
    out-of-allowlist families are always closed when ``allowed_families`` is set,
    while ``discontinue_missing`` additionally closes in-allowlist models the
    feed dropped. ``None`` disables the family filter (backward-compatible).

    A feed / parse failure raises :class:`PriceFeedError`. The caller commits.
    """
    payload = await fetcher.fetch()
    mapped, skipped = parse_feed(payload, allowed_families=allowed_families)

    if not confirm:
        pending = await _pending_large_increases(session, mapped)
        if pending:
            raise LargeIncreaseNotConfirmedError(pending)

    summary = SyncSummary(fetched=len(mapped), skipped=skipped)
    now = datetime.now(tz=UTC)

    for candidate in mapped:
        current = await _current_price(session, candidate)

        if current is None:
            session.add(_new_row(candidate, actor_id=actor_id))
            summary.created += 1
            continue

        if _prices_equal(current, candidate):
            summary.unchanged += 1
            continue

        if current.source == PriceSource.MANUAL.value and not overwrite_manual:
            summary.unchanged += 1
            continue

        current.effective_to = now
        current.updated_by = actor_id
        session.add(current)
        await session.flush()  # release the partial-unique open-period slot
        session.add(_new_row(candidate, actor_id=actor_id))
        summary.updated += 1

    # Close discontinued / out-of-scope open periods. Both helpers close an open
    # period (never delete); we de-duplicate by key so a row out-of-scope AND
    # feed-dropped is counted once.
    discontinued: dict[tuple[str, str, str], DiscontinuedModel] = {}
    if allowed_families is not None:
        for d in await close_out_of_scope_families(
            session,
            allowed_families,
            actor_id=actor_id,
            overwrite_manual=overwrite_manual,
            now=now,
        ):
            discontinued[(d.provider, d.model_id, d.modality)] = d
    if discontinue_missing:
        for d in await discontinue_dropped_models(
            session,
            mapped,
            actor_id=actor_id,
            overwrite_manual=overwrite_manual,
            now=now,
        ):
            discontinued[(d.provider, d.model_id, d.modality)] = d
    if allowed_families is not None or discontinue_missing:
        closed = sorted(discontinued.values(), key=attrgetter("provider", "model_id", "modality"))
        summary.discontinued_models = closed
        summary.discontinued = len(closed)

    await session.flush()
    return summary
