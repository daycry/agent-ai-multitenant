"""Sync the price catalog from the LiteLLM community price JSON (Plan 11 task_11_15).

What this is — and what it is NOT (ADR 0021)
--------------------------------------------
LiteLLM publishes a community-maintained JSON,
``model_prices_and_context_window.json``, mapping a model key to its public
pricing + context window. This module reads that JSON **purely as a data
feed** to refresh the platform's price catalog (``model_prices``). It does
**NOT** make the platform use LiteLLM as a provider runtime: the closed
runtime catalog of ADR 0021 (Claude SDK + Copilot + Azure Foundry APIM +
Ollama) is untouched. There is intentionally **no ``litellm`` dependency** —
we fetch + parse the public JSON over plain ``httpx``.

The feed shape (one entry per model)::

    {
      "claude-sonnet-4-5": {
        "litellm_provider": "anthropic",
        "mode": "chat",
        "input_cost_per_token": 0.000003,
        "output_cost_per_token": 0.000015,
        "cache_read_input_token_cost": 0.0000003,
        "max_input_tokens": 200000,
        "max_tokens": 64000
      },
      ...
    }

Costs are **per-token USD**; the catalog stores prices **per 1M tokens**
(``PriceUnit.PER_1M_TOKENS``), so each cost is multiplied by 1,000,000.
The feed also carries a ``sample_spec`` documentation pseudo-entry and rows
without usable prices (free / embedding-only / image models with no
per-token cost); those are skipped, never crash the run.

Effective-dating (Fase C) on a change
--------------------------------------
For each mapped entry we compare against the *current* (open-period) catalog
row for its ``(provider, model_id, modality)`` key:

  - **no current row**  → INSERT a new open row (``created``);
  - **prices changed**  → CLOSE the current row (``effective_to = now()``)
    and INSERT a new open row (``updated``) — effective dating, so the old
    price survives for historical snapshots (task_11_13);
  - **prices unchanged** → **no-op** (no new period, ``unchanged``).

Manual rows (``source = manual``) a System Admin hand-entered are **not**
silently overwritten: an unchanged manual row is left alone, and a changed
one is only superseded when ``overwrite_manual=True`` is passed (default
False) — the sync should not stomp a deliberate manual override.

Large-increase guard
---------------------
A price jump above ``LARGE_INCREASE_THRESHOLD`` (default +10%) on an existing
key is flagged. Unless ``confirm_large_increases=True`` the sync **defers**
that key (does not apply it) and reports it under ``large_increases`` so a
human (UI, task_11_16) or a scheduled job can confirm explicitly. Brand-new
models are never "large increases" (there is nothing to compare against).

Network is fully injectable
---------------------------
The fetch goes through a small :class:`PriceFeedFetcher` Protocol; production
wires :class:`HttpxPriceFeedFetcher` (an injectable ``httpx.AsyncClient``),
tests wire a fixture-returning fake — **no real network in tests**.

System-Admin only
-----------------
``model_prices`` is platform-global; the sync writes through the BYPASSRLS
``get_admin_session`` and the endpoint (router) is gated by
``require_system_admin``. A tenant cannot trigger it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from operator import attrgetter
from typing import Any, Protocol
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.model_prices import (
    ModelPrice,
    PriceModality,
    PriceSource,
    PriceUnit,
)

# The community LiteLLM price JSON. Read as a *data feed* only (ADR 0021),
# never as a provider runtime. Overridable via settings for an internal
# mirror.
DEFAULT_LITELLM_FEED_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)

# The feed quotes per-token USD; the catalog stores per-1M-token prices.
_PER_1M = Decimal(1_000_000)

# A price rise above this fraction on an existing key needs explicit
# confirmation before it is applied (task_11_16). +10%.
LARGE_INCREASE_THRESHOLD = Decimal("0.10")

# LiteLLM ``mode`` -> our catalog modality. Unmapped/absent modes default to
# text (the overwhelmingly common chat/completion case).
_MODE_TO_MODALITY: dict[str, PriceModality] = {
    "chat": PriceModality.TEXT,
    "completion": PriceModality.TEXT,
    "responses": PriceModality.TEXT,
    "embedding": PriceModality.EMBEDDING,
    "image_generation": PriceModality.IMAGE,
    "audio_transcription": PriceModality.AUDIO,
    "audio_speech": PriceModality.AUDIO,
    "rerank": PriceModality.RERANK,
    "moderation": PriceModality.TEXT,
}

# Documentation pseudo-entries the feed ships that are not real models.
_SKIP_KEYS = frozenset({"sample_spec"})

# Typed skip reason for a feed entry whose ``litellm_provider`` family is not in
# the active allowlist (plan price-sync-active-providers, task_psa_01). A stable
# code so the summary / audit can count "skipped because the family is not an
# active provider" without parsing prose.
SKIP_FAMILY_NOT_ACTIVE = "family_not_active"

# Map a configured provider ``kind`` (ADR 0021 closed catalogue) to the set of
# LiteLLM ``litellm_provider`` families its models appear under in the community
# feed (ADR 0028). The price sync derives the allowed families from the ACTIVE
# ``llm_providers`` rows by unioning the families of each active provider's kind.
# A constant, ADR-tracked mapping: extend it deliberately, never silently.
#   - claude_sdk    → anthropic
#   - azure_foundry → azure, azure_ai, openai (Azure AI Foundry fronts OpenAI
#                     models; LiteLLM lists them under azure / azure_ai / openai)
#   - copilot       → openai, anthropic (GitHub Copilot brokers both families)
#   - ollama        → ollama
KIND_TO_LITELLM_FAMILIES: dict[str, frozenset[str]] = {
    "claude_sdk": frozenset({"anthropic"}),
    "azure_foundry": frozenset({"azure", "azure_ai", "openai"}),
    "copilot": frozenset({"openai", "anthropic"}),
    "ollama": frozenset({"ollama"}),
}


# =============================================================================
# Fetch seam (injectable; tests feed a fixture, no real network)
# =============================================================================
class PriceFeedFetcher(Protocol):
    """Fetches the raw LiteLLM price feed as a parsed JSON mapping."""

    async def fetch(self) -> dict[str, Any]:  # pragma: no cover - Protocol
        ...


@dataclass(frozen=True)
class HttpxPriceFeedFetcher:
    """Production fetcher: GET the feed JSON over an injectable httpx client.

    The ``httpx.AsyncClient`` is passed in by the caller (the endpoint /
    Celery job builds one), so the network is fully controllable — tests
    that exercise *this* class wire an ``httpx.MockTransport`` and never hit
    the wire. Most tests inject a :class:`StaticPriceFeedFetcher` instead.
    """

    client: httpx.AsyncClient
    url: str = DEFAULT_LITELLM_FEED_URL
    timeout_seconds: float = 30.0

    async def fetch(self) -> dict[str, Any]:
        resp = await self.client.get(self.url, timeout=self.timeout_seconds)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise PriceFeedError("LiteLLM feed did not parse to a JSON object")
        return data


@dataclass(frozen=True)
class StaticPriceFeedFetcher:
    """Fetcher over an in-memory mapping — the test / mirror seam."""

    payload: dict[str, Any]

    async def fetch(self) -> dict[str, Any]:
        return self.payload


class PriceFeedError(Exception):
    """The feed could not be fetched or did not parse to a JSON object."""


# =============================================================================
# Parsed feed entry + result types
# =============================================================================
@dataclass(frozen=True, slots=True)
class MappedPrice:
    """A feed entry mapped onto catalog inputs (canonical USD, per-1M)."""

    provider: str
    model_id: str
    modality: PriceModality
    input_price: Decimal
    output_price: Decimal
    cached_input_price: Decimal | None
    context_window: int | None


@dataclass(frozen=True, slots=True)
class SkippedEntry:
    """A feed entry that could not be mapped — skipped, not a crash.

    ``reason`` is a stable, typed-ish code so callers (the audit log,
    task_11_19) can aggregate without parsing prose.
    """

    model_key: str
    reason: str


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
# Pure parsing / mapping (no DB, no network)
# =============================================================================
def _to_decimal(value: Any) -> Decimal | None:
    """Coerce a feed numeric (int / float / str) to Decimal, or None.

    Goes through ``str`` so a JSON float like ``0.000003`` is parsed as the
    exact decimal it printed as, not the binary-float approximation.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if dec < 0:
        return None
    return dec


def map_entry(model_key: str, raw: Any) -> MappedPrice:
    """Map one feed entry onto catalog inputs, or raise ``ValueError``.

    Raising (rather than returning None) lets the caller capture a typed
    :class:`SkippedEntry` with the reason. The mapping:

      - ``provider``  ← ``litellm_provider`` (required, non-empty);
      - ``model_id``  ← the feed key (the model name);
      - ``modality``  ← ``mode`` via :data:`_MODE_TO_MODALITY` (default text);
      - ``input_price`` / ``output_price`` ← per-token cost times 1,000,000
        (catalog is per-1M). An entry with neither a usable input nor output
        cost is skipped (free / non-priced models);
      - ``cached_input_price`` ← ``cache_read_input_token_cost`` times 1M
        (NULL when absent — prompt caching not priced separately);
      - ``context_window`` ← ``max_input_tokens`` or ``max_tokens`` (>0).
    """
    if not isinstance(raw, dict):
        raise ValueError("entry is not an object")

    provider = raw.get("litellm_provider")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("missing litellm_provider")
    provider = provider.strip()

    model_id = model_key.strip()
    if not model_id:
        raise ValueError("empty model key")

    mode = raw.get("mode")
    modality = (
        _MODE_TO_MODALITY.get(mode, PriceModality.TEXT)
        if isinstance(mode, str)
        else (PriceModality.TEXT)
    )

    input_price_pt = _to_decimal(raw.get("input_cost_per_token"))
    output_price_pt = _to_decimal(raw.get("output_cost_per_token"))
    if input_price_pt is None and output_price_pt is None:
        raise ValueError("no usable input/output price")

    # Normalise per-token → per-1M tokens (the catalog unit). A side missing
    # in the feed defaults to 0 (e.g. embedding models price input only).
    input_price = (input_price_pt or Decimal(0)) * _PER_1M
    output_price = (output_price_pt or Decimal(0)) * _PER_1M

    cached_pt = _to_decimal(raw.get("cache_read_input_token_cost"))
    cached_input_price = None if cached_pt is None else cached_pt * _PER_1M

    context_window = _coerce_context_window(raw)

    return MappedPrice(
        provider=provider,
        model_id=model_id,
        modality=modality,
        input_price=input_price,
        output_price=output_price,
        cached_input_price=cached_input_price,
        context_window=context_window,
    )


def _coerce_context_window(raw: dict[str, Any]) -> int | None:
    for key in ("max_input_tokens", "max_tokens"):
        value = raw.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value > 0:
            return int(value)
    return None


def parse_feed(
    payload: dict[str, Any],
    *,
    allowed_families: frozenset[str] | None = None,
) -> tuple[list[MappedPrice], list[SkippedEntry]]:
    """Map every feed entry; collect the unmappable ones as typed skips.

    Pure: no DB, no network. A malformed entry never aborts the parse — it
    is captured as a :class:`SkippedEntry` with its reason so the run keeps
    going and the summary records what was dropped.

    ``allowed_families`` (plan price-sync-active-providers, task_psa_01) is the
    LiteLLM-family allowlist the sync derives from the active providers. When
    given, a successfully-mapped entry whose ``provider`` (family) is NOT in the
    allowlist is dropped — captured as a typed skip
    (``reason = family_not_active``) rather than mapped. ``allowed_families``
    being ``None`` disables the filter (backward-compatible: every mappable
    entry is kept); an EMPTY frozenset keeps NOTHING (every family is
    out-of-scope).
    """
    mapped: list[MappedPrice] = []
    skipped: list[SkippedEntry] = []
    for model_key, raw in payload.items():
        if model_key in _SKIP_KEYS:
            continue
        try:
            entry = map_entry(model_key, raw)
        except ValueError as exc:
            skipped.append(SkippedEntry(model_key=model_key, reason=str(exc)))
            continue
        if allowed_families is not None and entry.provider not in allowed_families:
            skipped.append(SkippedEntry(model_key=model_key, reason=SKIP_FAMILY_NOT_ACTIVE))
            continue
        mapped.append(entry)
    return mapped, skipped


# =============================================================================
# Active-family resolver (plan price-sync-active-providers, task_psa_01)
# =============================================================================
def families_for_kinds(kinds: list[str]) -> frozenset[str]:
    """Union the LiteLLM families of a list of provider ``kind`` strings (pure).

    Each kind maps to its families via :data:`KIND_TO_LITELLM_FAMILIES`; an
    unknown kind contributes nothing (never crashes). The result is the union of
    every recognised kind's families — the allowlist the sync filters against.
    """
    families: set[str] = set()
    for kind in kinds:
        families |= KIND_TO_LITELLM_FAMILIES.get(kind, frozenset())
    return frozenset(families)


async def active_litellm_families(session: AsyncSession) -> frozenset[str]:
    """The LiteLLM families the price sync may import — derived per-sync.

    Resolves the allowlist of ``litellm_provider`` families the catalog sync is
    allowed to add (plan price-sync-active-providers, task_psa_01):

      1. if a System-Admin override (``price_sync.allowed_families``) is set, it
         WINS verbatim (including an explicit empty allowlist);
      2. otherwise it is DERIVED from the ACTIVE ``llm_providers`` rows: the
         union of each active provider's kind→families (ADR 0028 map). No
         fallback to the closed catalogue — 0 active providers ⇒ EMPTY set, so
         the sync imports nothing.

    Runs on the System-Admin (BYPASSRLS) admin session the sync endpoints /
    worker already own (``llm_providers`` is platform-global, no tenant_id).
    """
    # Lazy imports keep this module's import graph free of the db layer at load
    # time (mirrors the worker's lazy api_server imports).
    from api_server.db.llm_providers import list_llm_providers
    from api_server.db.platform_settings import (
        get_price_sync_allowed_families_override,
    )

    override = await get_price_sync_allowed_families_override(session)
    if override is not None:
        return override

    active = await list_llm_providers(session, active_only=True)
    return families_for_kinds([p.kind for p in active])


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


__all__ = [
    "DEFAULT_LITELLM_FEED_URL",
    "KIND_TO_LITELLM_FAMILIES",
    "LARGE_INCREASE_THRESHOLD",
    "SKIP_FAMILY_NOT_ACTIVE",
    "DiffStatus",
    "DiscontinuedModel",
    "HttpxPriceFeedFetcher",
    "LargeIncrease",
    "LargeIncreaseNotConfirmedError",
    "MappedPrice",
    "ModelClassification",
    "ModelClassificationSet",
    "ModelStatus",
    "PriceDiffRow",
    "PriceFeedError",
    "PriceFeedFetcher",
    "SkippedEntry",
    "StaticPriceFeedFetcher",
    "SyncDiff",
    "SyncSummary",
    "active_litellm_families",
    "apply_sync_from_litellm",
    "classify_models",
    "close_out_of_scope_families",
    "compute_sync_diff",
    "discontinue_dropped_models",
    "families_for_kinds",
    "map_entry",
    "parse_feed",
    "sync_prices_from_litellm",
]
