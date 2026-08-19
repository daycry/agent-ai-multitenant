"""Sync the price catalog from the LiteLLM community price JSON (Plan 11 task_11_15).

What this is — and what it is NOT (ADR 0021)
--------------------------------------------
LiteLLM publishes a community-maintained JSON,
``model_prices_and_context_window.json``, mapping a model key to its public
pricing + context window. This package reads that JSON **purely as a data
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

Network is fully injectable: every entry point takes a
:class:`~api_server.pricing.litellm_sync.feed.PriceFeedFetcher`, and tests pass
a :class:`~api_server.pricing.litellm_sync.feed.StaticPriceFeedFetcher` with a
fixture instead of touching the network.

## Por qué esto es un paquete (plan prod-16, `task_prod16_12`)

Era un solo `pricing/litellm_sync.py` de **1338 líneas** con seis
responsabilidades encadenadas. Repartido siguiendo la dirección del dato
—de dónde sale, qué entra, qué cambiaría, cómo se enseña, qué se escribe, qué
se cierra—, que es también el orden en que se pueden importar sin ciclos:

  * :mod:`.feed`           — el seam de red y el mapeo de una entrada cruda a
    una fila de catálogo. **No escribe nada.**
  * :mod:`.families`       — qué familias del feed entran, derivadas de los
    `llm_providers` activos.
  * :mod:`.diff`           — qué cambiaría, sin escribir: `compute_sync_diff`,
    los tipos de fila (`DiffStatus`, `PriceDiffRow`) y la guarda de subida
    grande. **Sólo lee.**
  * :mod:`.classification` — cómo se etiqueta cada modelo para la pantalla.
  * :mod:`.retire`         — cerrar lo que el feed dejó de traer o lo que quedó
    fuera de alcance.
  * :mod:`.apply`          — lo único que ESCRIBE el catálogo.

**La frontera que importa es `diff` / `apply`**, y el troceo la hace visible en
el árbol de ficheros: la pantalla de precios enseña un diff antes de que nadie
confirme nada, así que calcular no puede escribir. En el monolito eso era una
convención que había que leerse 1338 líneas para descubrir.

Este módulo es una **fachada de re-export**: 33 ficheros importan de
`api_server.pricing.litellm_sync` por nombre, y `__all__` es idéntico al del
monolito.
"""

from __future__ import annotations

from api_server.pricing.litellm_sync.apply import (
    LargeIncreaseNotConfirmedError,
    SyncSummary,
    apply_sync_from_litellm,
    sync_prices_from_litellm,
)
from api_server.pricing.litellm_sync.classification import (
    ModelClassification,
    ModelClassificationSet,
    ModelStatus,
    classify_models,
)
from api_server.pricing.litellm_sync.diff import (
    LARGE_INCREASE_THRESHOLD,
    DiffStatus,
    LargeIncrease,
    PriceDiffRow,
    SyncDiff,
    compute_sync_diff,
)
from api_server.pricing.litellm_sync.families import (
    KIND_TO_LITELLM_FAMILIES,
    active_litellm_families,
    families_for_kinds,
)
from api_server.pricing.litellm_sync.feed import (
    DEFAULT_LITELLM_FEED_URL,
    SKIP_FAMILY_NOT_ACTIVE,
    HttpxPriceFeedFetcher,
    MappedPrice,
    PriceFeedError,
    PriceFeedFetcher,
    SkippedEntry,
    StaticPriceFeedFetcher,
    map_entry,
    parse_feed,
)
from api_server.pricing.litellm_sync.retire import (
    DiscontinuedModel,
    close_out_of_scope_families,
    discontinue_dropped_models,
)

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
