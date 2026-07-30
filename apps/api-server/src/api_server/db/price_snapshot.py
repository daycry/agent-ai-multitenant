"""Per-model_call price snapshot (Plan 11 Fase C, task_11_13).

What this is
------------
A *model call* in this platform is a ``model_call`` step record inside an
``executions.steps_log`` JSONB array (see
``docker/agent-runtimes/agent-runtime/agent_runtime/steps.py`` —
``model_call_step`` carries ``model``, ``tokens_in``, ``tokens_out`` and a
runtime ``cost_usd`` estimate). This module turns the *catalog* price that
was IN EFFECT at recording time into an immutable **price snapshot** so a
later catalog change never rewrites an old call's billable cost.

The snapshot freezes four things per call, all in **canonical USD**:

  - the unit prices used — ``input_price`` / ``output_price`` /
    ``cached_input_price`` (the prompt-caching cache-read price),
  - ``price_snapshot_at`` — when the snapshot was taken,
  - a computed ``cost_usd`` for the call (priced from the recorded
    tokens, charging cached-input tokens at the cached rate), and
  - the source catalog row id + its ``unit`` so the math is auditable.

Why a snapshot at all
---------------------
The catalog (``model_prices``) is effective-dated and mutable: a System
Admin can supersede a price, and a daily sync (task_11_15) will rewrite
numbers. If old executions referenced the *live* catalog they would
re-price retroactively. Freezing the snapshot onto the call keeps
historical billing correct (the plan's "Catálogo de precios snapshot por
llamada para auditoría histórica correcta").

Missing price → typed *unknown*, never a fake zero
--------------------------------------------------
When the catalog has no current price for the call's
``(provider, model_id, modality)``, the snapshot is recorded as
``available=False`` with ``cost_usd=None`` and ``reason`` set — NOT a
fabricated ``0`` cost. A reader can tell "this call was free" apart from
"we did not know the price", which matters for billing integrity.

Tenancy
-------
``model_prices`` is platform-global (no ``tenant_id``) with a global-read
RLS policy (migration 0049), so a tenant session can look up the price it
needs. The snapshot is written onto ``executions`` (and its JSONB steps),
which stay tenant-scoped — the snapshot columns inherit the executions
RLS untouched. The lookup here runs on whatever session the caller hands
in (a tenant session reads the global catalog fine).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.model_prices import CANONICAL_CURRENCY, ModelPrice, PriceModality, PriceUnit

# Token counts in a model call are quoted per token; catalog prices are
# quoted per `unit` tokens. These divisors convert a per-`unit` price to a
# per-token price so cost = tokens * (price / divisor).
_UNIT_DIVISOR: dict[str, Decimal] = {
    PriceUnit.PER_1M_TOKENS.value: Decimal(1_000_000),
    PriceUnit.PER_1K_TOKENS.value: Decimal(1_000),
}

# executions.total_cost_usd is Numeric(14, 6); quantise the computed cost
# to the same scale so the snapshot's cost never out-precisions the column
# it is stored beside (and rounds deterministically, half-up).
_COST_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    """An immutable price snapshot for one model call (USD canonical).

    ``available`` distinguishes a priced call (the catalog had a current
    price; ``cost_usd`` is the frozen billable cost) from an *unknown*
    one (no current price for the key; ``cost_usd`` is ``None`` and
    ``reason`` explains why) — an unknown price is NEVER recorded as a
    fake ``0``.
    """

    available: bool
    currency: str
    price_snapshot_at: datetime
    # Unit prices in effect at call time (None on the unknown path).
    input_price: Decimal | None = None
    output_price: Decimal | None = None
    cached_input_price: Decimal | None = None
    unit: str | None = None
    # The computed billable cost for THIS call, or None when unknown.
    cost_usd: Decimal | None = None
    # Provenance: the catalog row the snapshot was taken from.
    price_id: Any | None = None
    source: str | None = None
    # Set only on the unknown path, e.g. "no current price for key".
    reason: str | None = None

    def as_step_payload(self) -> dict[str, Any]:
        """Serialise the snapshot for embedding in a JSONB step record.

        Decimals are stringified (JSONB has no Decimal; ``str`` keeps the
        exact value, unlike ``float``) and the timestamp is ISO-8601, so
        the frozen snapshot round-trips through JSONB without loss.
        """
        payload: dict[str, Any] = {
            "available": self.available,
            "currency": self.currency,
            "price_snapshot_at": self.price_snapshot_at.isoformat(),
        }
        if self.available:
            payload.update(
                input_price=str(self.input_price),
                output_price=str(self.output_price),
                cached_input_price=(
                    None if self.cached_input_price is None else str(self.cached_input_price)
                ),
                unit=self.unit,
                cost_usd=str(self.cost_usd),
                price_id=(None if self.price_id is None else str(self.price_id)),
                source=self.source,
            )
        else:
            payload["reason"] = self.reason
        return payload


def _per_token(price: Decimal, unit: str) -> Decimal:
    """Convert a per-`unit` catalog price to a per-token price."""
    divisor = _UNIT_DIVISOR.get(unit, _UNIT_DIVISOR[PriceUnit.PER_1M_TOKENS.value])
    return price / divisor


def compute_price_snapshot(
    price: ModelPrice | None,
    *,
    tokens_in: int,
    tokens_out: int,
    cached_input_tokens: int = 0,
    snapshot_at: datetime | None = None,
) -> PriceSnapshot:
    """Freeze a price snapshot + cost for one model call (pure, no DB).

    Cost model (canonical USD):

      - ``cached_input_tokens`` are billed at the **cached-input rate**
        (prompt caching, typically ~10% of input); they are a *subset* of
        ``tokens_in``, so the remaining ``tokens_in - cached_input_tokens``
        are billed at the full input rate. The cached rate falls back to
        the catalog's ``cached_input_price_or_default()`` (~10% of input)
        when the provider does not price cache reads separately.
      - ``tokens_out`` are billed at the output rate.

    When ``price is None`` (the catalog has no current price for the key)
    the snapshot is a typed *unknown* (``available=False``,
    ``cost_usd=None``) — never a fabricated zero.
    """
    taken_at = snapshot_at or datetime.now(tz=UTC)

    if price is None:
        return PriceSnapshot(
            available=False,
            currency=CANONICAL_CURRENCY,
            price_snapshot_at=taken_at,
            reason="no current price in catalog for (provider, model_id, modality)",
        )

    # Cached tokens are a subset of input tokens; never bill them twice and
    # never let a bad count drive negative billable input.
    cached = max(0, cached_input_tokens)
    full_input = max(0, tokens_in - cached)

    input_per_token = _per_token(price.input_price, price.unit)
    output_per_token = _per_token(price.output_price, price.unit)
    cached_per_token = _per_token(price.cached_input_price_or_default(), price.unit)

    cost = (
        Decimal(full_input) * input_per_token
        + Decimal(cached) * cached_per_token
        + Decimal(max(0, tokens_out)) * output_per_token
    ).quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)

    return PriceSnapshot(
        available=True,
        currency=CANONICAL_CURRENCY,
        price_snapshot_at=taken_at,
        input_price=price.input_price,
        output_price=price.output_price,
        cached_input_price=price.cached_input_price,
        unit=price.unit,
        cost_usd=cost,
        price_id=price.id,
        source=price.source,
    )


async def lookup_current_price(
    session: AsyncSession,
    *,
    provider: str,
    model_id: str,
    modality: str | PriceModality = PriceModality.TEXT,
) -> ModelPrice | None:
    """Load the current (open-period) catalog row for a key, or None.

    The current price is the single row whose period is open
    (``effective_to IS NULL``) for ``(provider, model_id, modality)`` —
    the partial-unique index ``uq_model_prices_current`` guarantees at
    most one. ``model_prices`` is platform-global with a global-read RLS
    policy, so a tenant session reads it fine.
    """
    result = await session.execute(
        select(ModelPrice).where(
            ModelPrice.provider == provider,
            ModelPrice.model_id == model_id,
            ModelPrice.modality == str(modality),
            ModelPrice.effective_to.is_(None),
        )
    )
    return result.scalar_one_or_none()


# AUD16-15 (auditoría 2026-07-16): los steps del runtime registran el KIND del
# proveedor (claude_sdk/ollama/azure_foundry/copilot) — o nada, en los steps
# históricos — mientras el catálogo (feed LiteLLM + manual) nombra por FAMILIA
# (anthropic, azure, ollama con ids 'ollama/<m>'…). Sin este puente, la clave
# (provider, model_id) no casaba jamás y price_snapshot_cost_usd quedó NULL en
# el 100% de las executions pese a estar el modelo en el catálogo.
#
# prod-07 task_prod07_12: la tabla se DERIVA de `KIND_TO_LITELLM_FAMILIES` (la
# oficial del ADR 0028, la que decide qué familias IMPORTA el sync de precios)
# en vez de mantenerse a mano. Escrita a mano DIVERGÍA: a `copilot` le faltaba
# `anthropic` y a `azure_foundry` le faltaba `openai`, así que el sync importaba
# esos precios y el snapshot no los buscaba — casaban solo por el fallback de
# «match único por model_id», que se apaga en cuanto otro proveedor comparte el
# nombre del modelo. Derivar hace la divergencia IMPOSIBLE, no solo improbable.

# Familias que NO vienen del feed LiteLLM, más el kind heredado `claude`:
#   * `github_copilot` — la puebla el alta MANUAL del catálogo (el feed no la
#     trae, porque Copilot no publica precios).
#   * `claude` — kind histórico, alias de `claude_sdk`.
# Van PRIMERO en el orden de búsqueda: son las más específicas.
_EXTRA_CATALOG_ALIASES: dict[str, tuple[str, ...]] = {
    "claude": ("anthropic",),
    "copilot": ("github_copilot",),
}


def _build_catalog_aliases() -> dict[str, tuple[str, ...]]:
    """kind → familias del catálogo donde buscar su precio, en orden de prueba.

    El orden es DETERMINISTA (``sorted`` sobre el frozenset de familias): decide
    qué fila gana cuando varias casan, y el orden de iteración de un ``frozenset``
    de cadenas varía entre procesos por el hash randomizado de Python.

    Import diferido de `pricing.litellm_sync` para no meter la capa de pricing en
    el grafo de imports del módulo de db en tiempo de carga.
    """
    from api_server.pricing.litellm_sync import KIND_TO_LITELLM_FAMILIES

    aliases: dict[str, tuple[str, ...]] = {}
    for kind, families in KIND_TO_LITELLM_FAMILIES.items():
        ordered = list(_EXTRA_CATALOG_ALIASES.get(kind, ()))
        ordered += [family for family in sorted(families) if family not in ordered]
        aliases[kind] = tuple(ordered)
    # Kinds que solo existen como alias (p.ej. `claude`) y no están en la oficial.
    for kind, extra in _EXTRA_CATALOG_ALIASES.items():
        aliases.setdefault(kind, tuple(extra))
    return aliases


_CATALOG_PROVIDER_ALIASES: dict[str, tuple[str, ...]] = _build_catalog_aliases()


async def lookup_current_price_for_call(
    session: AsyncSession,
    *,
    provider: str,
    model_id: str,
    modality: str | PriceModality = PriceModality.TEXT,
) -> ModelPrice | None:
    """Resolve the catalog row for a RUNTIME call key (kind + native model).

    Order: exact ``(provider, model_id)``; the kind's catalog-family aliases
    (also trying the LiteLLM-prefixed id ``'<alias>/<model>'``); and — for
    steps with an empty/unknown provider — the ``model_id`` alone IF exactly
    one current row matches (never guesses among several: billing integrity
    beats coverage).
    """
    candidates: list[tuple[str, str]] = []
    kind = (provider or "").strip()
    if kind:
        candidates.append((kind, model_id))
        for alias in _CATALOG_PROVIDER_ALIASES.get(kind, ()):
            candidates.append((alias, model_id))
            candidates.append((alias, f"{alias}/{model_id}"))
    for prov, mid in candidates:
        row = await lookup_current_price(session, provider=prov, model_id=mid, modality=modality)
        if row is not None:
            return row
    result = await session.execute(
        select(ModelPrice).where(
            ModelPrice.model_id == model_id,
            ModelPrice.modality == str(modality),
            ModelPrice.effective_to.is_(None),
        )
    )
    rows = list(result.scalars())
    return rows[0] if len(rows) == 1 else None


async def snapshot_model_call(
    session: AsyncSession,
    *,
    provider: str,
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    modality: str | PriceModality = PriceModality.TEXT,
    cached_input_tokens: int = 0,
    snapshot_at: datetime | None = None,
) -> PriceSnapshot:
    """Look up the current catalog price for a call and freeze the snapshot.

    Convenience over :func:`lookup_current_price_for_call` +
    :func:`compute_price_snapshot`: resolves the live catalog price for the
    RUNTIME call key (kind-aware, AUD16-15), then freezes it. A missing price
    yields a typed *unknown* snapshot (never a fake zero). Used by the
    execution-recording seam so each ``model_call`` step persists its price
    snapshot.
    """
    price = await lookup_current_price_for_call(
        session, provider=provider, model_id=model_id, modality=modality
    )
    return compute_price_snapshot(
        price,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cached_input_tokens=cached_input_tokens,
        snapshot_at=snapshot_at,
    )


__all__ = [
    "PriceSnapshot",
    "compute_price_snapshot",
    "lookup_current_price",
    "lookup_current_price_for_call",
    "snapshot_model_call",
]
