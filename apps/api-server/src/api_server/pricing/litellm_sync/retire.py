"""Cerrar lo que el feed ya no trae o lo que queda fuera de alcance.

Dos motivos distintos y por eso dos funciones: `discontinue_dropped_models`
cierra las claves que DESAPARECIERON del feed, y `close_out_of_scope_families`
cierra las que siguen ahi pero pertenecen a una familia que ya no corresponde
a ningun proveedor activo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from operator import attrgetter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.model_prices import (
    PriceSource,
)
from api_server.pricing.litellm_sync.diff import (
    _open_catalog_rows,
)
from api_server.pricing.litellm_sync.feed import (
    MappedPrice,
)


@dataclass(frozen=True, slots=True)
class DiscontinuedModel:
    """A catalog model the feed dropped — flagged discontinued, NOT deleted (task_11_17).

    Identifies the ``(provider, model_id, modality)`` whose open catalog
    period this sync closed because the feed no longer lists it. The row is
    never hard-deleted: closing the period keeps its history (and the per-call
    price snapshots that reference it) valid while making it no longer the
    "current" price.
    """

    provider: str
    model_id: str
    modality: str


# =============================================================================
# New + discontinued detection write side (task_11_17)
# =============================================================================
async def discontinue_dropped_models(
    session: AsyncSession,
    mapped: list[MappedPrice],
    *,
    actor_id: UUID | None = None,
    overwrite_manual: bool = False,
    now: datetime | None = None,
) -> list[DiscontinuedModel]:
    """Flag (close) the open catalog periods the feed no longer lists (task_11_17).

    A model present in the catalog with an OPEN period but absent from the
    feed is a *discontinued candidate*. This **flags** it by closing its open
    period (``effective_to = now``) so it is no longer the current price — it
    is **NOT deleted**: the row (and its closed-period history + the per-call
    price snapshots that reference it) survives, keeping historical billing
    correct. Manual rows (``source = manual``) are left untouched unless
    ``overwrite_manual`` is set, so a deliberate hand-entered price is not
    dropped merely because the community feed omits the model.

    Returns the typed list of models it closed (deterministically ordered) so
    the summary / audit log can record exactly what was discontinued.
    """
    stamp = now or datetime.now(tz=UTC)
    feed_keys = {(m.provider, m.model_id, m.modality.value) for m in mapped}
    open_rows = await _open_catalog_rows(session)

    discontinued: list[DiscontinuedModel] = []
    for row in open_rows:
        key = (row.provider, row.model_id, row.modality)
        if key in feed_keys:
            continue
        if row.source == PriceSource.MANUAL.value and not overwrite_manual:
            continue
        row.effective_to = stamp
        row.updated_by = actor_id
        session.add(row)
        discontinued.append(
            DiscontinuedModel(
                provider=row.provider,
                model_id=row.model_id,
                modality=row.modality,
            )
        )

    discontinued.sort(key=attrgetter("provider", "model_id", "modality"))
    if discontinued:
        await session.flush()
    return discontinued


async def close_out_of_scope_families(
    session: AsyncSession,
    allowed_families: frozenset[str],
    *,
    actor_id: UUID | None = None,
    overwrite_manual: bool = False,
    now: datetime | None = None,
) -> list[DiscontinuedModel]:
    """Close (flag) the open catalog periods whose family left the allowlist.

    Plan price-sync-active-providers (task_psa_01). A catalog model with an OPEN
    period whose ``provider`` (LiteLLM family) is NOT in ``allowed_families`` is
    now out-of-scope (its provider kind is no longer an active provider, or was
    removed from the override). It is treated exactly like a discontinued model:
    its open period is CLOSED (``effective_to = now``) so it stops being the
    current price, but the row is **NEVER deleted** — its closed-period history
    and the per-call price snapshots that reference it must survive (auditing /
    invoices). Manual rows (``source = manual``) are left untouched unless
    ``overwrite_manual`` is set, mirroring :func:`discontinue_dropped_models`.

    An EMPTY ``allowed_families`` closes every open period (every family is
    out-of-scope). Returns the typed list it closed (deterministically ordered).
    Distinct from :func:`discontinue_dropped_models`, which closes rows the feed
    DROPPED; this closes rows whose family is NOT ALLOWED at all, so in-allowlist
    models the feed still lists are never touched here.
    """
    stamp = now or datetime.now(tz=UTC)
    open_rows = await _open_catalog_rows(session)

    closed: list[DiscontinuedModel] = []
    for row in open_rows:
        if row.provider in allowed_families:
            continue
        if row.source == PriceSource.MANUAL.value and not overwrite_manual:
            continue
        row.effective_to = stamp
        row.updated_by = actor_id
        session.add(row)
        closed.append(
            DiscontinuedModel(
                provider=row.provider,
                model_id=row.model_id,
                modality=row.modality,
            )
        )

    closed.sort(key=attrgetter("provider", "model_id", "modality"))
    if closed:
        await session.flush()
    return closed
