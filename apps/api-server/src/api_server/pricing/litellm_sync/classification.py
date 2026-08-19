"""Como se etiqueta cada modelo para que la UI pueda enseñarlo.

`DiffStatus` es lo que cambiaria en el catalogo; `ModelStatus` es lo que ve un
humano en la pantalla de precios. `classify_models` traduce lo uno en lo otro
y agrupa. Sin acceso a base de datos: entra el diff, sale la clasificacion.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from operator import attrgetter

from api_server.db.model_prices import (
    ModelPrice,
    PriceSource,
)
from api_server.pricing.litellm_sync.diff import (
    DiffStatus,
    _pct_change,
    _prices_equal,
)
from api_server.pricing.litellm_sync.feed import (
    MappedPrice,
)


# task_11_17: a coarse new/discontinued/changed/unchanged classification.
class ModelStatus(enum.StrEnum):
    """How a model in the feed-vs-catalog comparison is classified (task_11_17).

    A deliberately coarse, **pure** taxonomy on top of the finer
    :class:`DiffStatus`, framed in lifecycle terms a human reasons about when
    looking at a sync:

    - ``new``          : in the feed, the catalog has no open period for it
                         (``DiffStatus.ADDED``);
    - ``discontinued`` : the catalog has an open period the feed no longer
                         lists (``DiffStatus.REMOVED``). The model is **never
                         deleted** — its history (closed periods + snapshots)
                         must survive; it is flagged and, optionally, its open
                         period is closed so it stops being "current";
    - ``changed``      : prices moved (a within-threshold ``UPDATED`` or a
                         >10% ``INCREASED`` — both are "the price changed");
    - ``unchanged``    : the feed matches the current open period (a no-op).
    """

    NEW = "new"
    DISCONTINUED = "discontinued"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


def _diff_to_model_status(status: DiffStatus) -> ModelStatus:
    """Collapse the finer dry-run :class:`DiffStatus` onto the lifecycle one."""
    if status is DiffStatus.ADDED:
        return ModelStatus.NEW
    if status is DiffStatus.REMOVED:
        return ModelStatus.DISCONTINUED
    if status is DiffStatus.UNCHANGED:
        return ModelStatus.UNCHANGED
    # UPDATED + INCREASED are both "the price changed".
    return ModelStatus.CHANGED


@dataclass(frozen=True, slots=True)
class ModelClassification:
    """One model's coarse new/discontinued/changed/unchanged verdict (task_11_17).

    Pure data, produced by :func:`classify_models` from already-loaded feed
    entries + catalog rows — **no DB, no network**. ``input_pct`` / ``output_pct``
    are the fractional price change on the current open period (None for a
    ``new`` or ``discontinued`` model, or when the old price was 0). ``manual``
    flags a model whose current row was hand-entered (``source = manual``): a
    sync leaves it untouched, so the UI can explain why a ``changed`` manual
    row will not actually move.
    """

    provider: str
    model_id: str
    modality: str
    status: ModelStatus
    input_pct: float | None = None
    output_pct: float | None = None
    manual: bool = False


@dataclass(frozen=True, slots=True)
class ModelClassificationSet:
    """The full new/discontinued/changed/unchanged split of a sync (task_11_17).

    Pure aggregate over :class:`ModelClassification` rows. The per-status
    lists + counts are what the sync summary / diff surfaces so a human (UI)
    or the audit log (task_11_19) sees, at a glance, which models the feed
    added, which it dropped (discontinued candidates — flagged, not deleted),
    which moved, and which stayed put. Lists are deterministically ordered by
    ``(provider, model_id, modality)``.
    """

    new: list[ModelClassification] = field(default_factory=list)
    discontinued: list[ModelClassification] = field(default_factory=list)
    changed: list[ModelClassification] = field(default_factory=list)
    unchanged: list[ModelClassification] = field(default_factory=list)

    @property
    def new_count(self) -> int:
        return len(self.new)

    @property
    def discontinued_count(self) -> int:
        return len(self.discontinued)

    @property
    def changed_count(self) -> int:
        return len(self.changed)

    @property
    def unchanged_count(self) -> int:
        return len(self.unchanged)


def classify_models(
    mapped: list[MappedPrice],
    open_rows: list[ModelPrice],
) -> ModelClassificationSet:
    """Classify every model new / discontinued / changed / unchanged (task_11_17).

    The **pure, deterministic** core of new+discontinued detection: given the
    mapped feed entries and the catalog's *current* (open-period) rows, decide
    each model's lifecycle status without touching the DB or the network.

      - a feed model with no open catalog period for its key → ``new``;
      - an open catalog row whose key the feed no longer lists →
        ``discontinued`` (flagged — the row is **never deleted** so its
        history + price snapshots stay valid);
      - a feed model whose prices/context differ from the open period →
        ``changed`` (carrying the input/output % change);
      - a feed model that matches the open period → ``unchanged``.

    Idempotent and order-independent: the same inputs always yield the same
    split, and each per-status list is sorted by ``(provider, model_id,
    modality)`` so callers (tests, the audit log, the UI) get a stable order.
    """
    by_key: dict[tuple[str, str, str], ModelPrice] = {
        (r.provider, r.model_id, r.modality): r for r in open_rows
    }
    seen: set[tuple[str, str, str]] = set()
    result = ModelClassificationSet()

    for candidate in mapped:
        key = (candidate.provider, candidate.model_id, candidate.modality.value)
        seen.add(key)
        current = by_key.get(key)
        if current is None:
            result.new.append(
                ModelClassification(
                    provider=candidate.provider,
                    model_id=candidate.model_id,
                    modality=candidate.modality.value,
                    status=ModelStatus.NEW,
                )
            )
            continue
        if _prices_equal(current, candidate):
            result.unchanged.append(
                ModelClassification(
                    provider=current.provider,
                    model_id=current.model_id,
                    modality=current.modality,
                    status=ModelStatus.UNCHANGED,
                    manual=current.source == PriceSource.MANUAL.value,
                )
            )
            continue
        result.changed.append(
            ModelClassification(
                provider=current.provider,
                model_id=current.model_id,
                modality=current.modality,
                status=ModelStatus.CHANGED,
                input_pct=_pct_change(current.input_price, candidate.input_price),
                output_pct=_pct_change(current.output_price, candidate.output_price),
                manual=current.source == PriceSource.MANUAL.value,
            )
        )

    # Open catalog rows the feed no longer lists → discontinued (flagged,
    # never deleted; their closed periods + snapshots must survive).
    for key, row in by_key.items():
        if key in seen:
            continue
        result.discontinued.append(
            ModelClassification(
                provider=row.provider,
                model_id=row.model_id,
                modality=row.modality,
                status=ModelStatus.DISCONTINUED,
                manual=row.source == PriceSource.MANUAL.value,
            )
        )

    _sort_key = attrgetter("provider", "model_id", "modality")
    result.new.sort(key=_sort_key)
    result.discontinued.sort(key=_sort_key)
    result.changed.sort(key=_sort_key)
    result.unchanged.sort(key=_sort_key)
    return result
