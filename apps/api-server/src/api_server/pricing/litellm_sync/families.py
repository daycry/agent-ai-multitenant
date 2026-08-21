"""Que familias del feed entran, derivadas de los providers ACTIVOS.

`KIND_TO_LITELLM_FAMILIES` es la tabla ADR-trazada (ADR 0021 + 0028) que ata
cada `kind` del catalogo cerrado de proveedores a las familias con las que ese
modelo aparece en el feed comunitario. `active_litellm_families` la aplica a
las filas vivas de `llm_providers`: el sync no importa precios de modelos que
esta plataforma no puede llamar.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

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
