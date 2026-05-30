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

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
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
    "https://raw.githubusercontent.com/BerriAI/litellm/main/" "model_prices_and_context_window.json"
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


@dataclass(slots=True)
class SyncSummary:
    """The outcome of one sync run (also the endpoint response payload)."""

    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[SkippedEntry] = field(default_factory=list)
    large_increases: list[LargeIncrease] = field(default_factory=list)
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


def parse_feed(payload: dict[str, Any]) -> tuple[list[MappedPrice], list[SkippedEntry]]:
    """Map every feed entry; collect the unmappable ones as typed skips.

    Pure: no DB, no network. A malformed entry never aborts the parse — it
    is captured as a :class:`SkippedEntry` with its reason so the run keeps
    going and the summary records what was dropped.
    """
    mapped: list[MappedPrice] = []
    skipped: list[SkippedEntry] = []
    for model_key, raw in payload.items():
        if model_key in _SKIP_KEYS:
            continue
        try:
            mapped.append(map_entry(model_key, raw))
        except ValueError as exc:
            skipped.append(SkippedEntry(model_key=model_key, reason=str(exc)))
    return mapped, skipped


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
    """
    payload = await fetcher.fetch()
    mapped, skipped = parse_feed(payload)

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

    await session.flush()
    return summary


__all__ = [
    "DEFAULT_LITELLM_FEED_URL",
    "LARGE_INCREASE_THRESHOLD",
    "HttpxPriceFeedFetcher",
    "LargeIncrease",
    "MappedPrice",
    "PriceFeedError",
    "PriceFeedFetcher",
    "SkippedEntry",
    "StaticPriceFeedFetcher",
    "SyncSummary",
    "map_entry",
    "parse_feed",
    "sync_prices_from_litellm",
]
