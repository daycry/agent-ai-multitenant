"""De donde salen los precios y como se leen. **No escribe nada.**

El seam de red (`PriceFeedFetcher` + las dos implementaciones) y el mapeo de
una entrada cruda del feed a una fila de catalogo: `map_entry` y `parse_feed`,
con sus dos tipos de salida --lo que se pudo mapear y lo que se salto, con el
motivo--.

El fetcher es inyectable a proposito: en tests se mete un
`StaticPriceFeedFetcher` con un fixture y no se toca la red.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from api_server.db.model_prices import (
    PriceModality,
)

# The community LiteLLM price JSON. Read as a *data feed* only (ADR 0021),
# never as a provider runtime. Overridable via settings for an internal
# mirror.
DEFAULT_LITELLM_FEED_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
# The feed quotes per-token USD; the catalog stores per-1M-token prices.
_PER_1M = Decimal(1_000_000)
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
