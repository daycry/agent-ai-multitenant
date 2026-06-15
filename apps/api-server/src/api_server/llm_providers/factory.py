"""Build a concrete ``shared_llm.LLMProvider`` from a platform provider row
(ADR 0053 — the api-server side of the LLM wiring deferred by Plan 10/11.2).

The runtime owns its own factory (``agent_runtime.providers``) which the
api-server deliberately does NOT import (it would drag the heavy
runtime/httpx/Claude-SDK chain into the api-server). This is the api-server's
own, small mapping: given an ACTIVE ``llm_providers`` row + the credential
resolved from Vault, instantiate the matching concrete provider from
``shared_llm.providers`` (the four ADR-0021 paths).

Robustness rules:

  * The concrete provider SDKs are *optional* deps; each is **imported
    lazily** inside its branch, and an ``ImportError`` degrades to ``None``
    (the caller surfaces a clear 503) rather than crashing the api-server.
  * A missing required endpoint/credential also yields ``None`` — checked
    BEFORE the import so the "not configured" path needs no optional dep.
  * Secrets come only from Vault (``resolve_provider_config``); they live in
    the returned client and are NEVER logged. The DB row holds only the
    Vault pointer.

``azure_foundry`` pins the model in the request URL via the constructor
``deployment`` (the per-call ``model`` is ignored for routing), so the
selected ``model_id`` is passed as the deployment; the other kinds take it
as their default model and the per-call ``model`` overrides it.
"""

from __future__ import annotations

import contextlib
from typing import Any
from uuid import UUID

from shared_llm.base import LLMProvider
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.llm_providers import get_llm_provider
from api_server.llm_providers.vault import (
    SECRET_FIELD_API_KEY,
    SECRET_FIELD_BEARER_TOKEN,
    SECRET_FIELD_OAUTH_TOKEN,
    LLMProviderVaultError,
    LLMProviderVaultStore,
)

# ``shared_llm.base`` is light (the Protocol + dataclasses only — NOT the
# concrete provider SDKs), so importing it for the return type does not drag
# the optional deps into the api-server import graph.


def _build_claude(
    *, base_url: str | None, secret: dict[str, str], model: str
) -> LLMProvider | None:
    # Uniform builder signature for the dispatch; base_url is unused for the
    # subscription-based claude_sdk path.
    del base_url
    # The OAuth token (when present) becomes the SDK api key; absent token
    # leaves ambient SDK auth in place.
    try:
        from shared_llm.providers.claude_agent import ClaudeAgentProvider
    except ImportError:
        return None
    token = secret.get(SECRET_FIELD_OAUTH_TOKEN)
    return ClaudeAgentProvider(api_key=token or None, default_model=model)


def _build_copilot(
    *, base_url: str | None, secret: dict[str, str], model: str
) -> LLMProvider | None:
    # Uniform builder signature for the dispatch; copilot brokers its own
    # model set, so base_url and model are unused here.
    del base_url, model
    token = secret.get(SECRET_FIELD_OAUTH_TOKEN)
    if not token:
        return None
    try:
        from shared_llm.providers.copilot import CopilotProvider
    except ImportError:
        return None
    return CopilotProvider(github_token=token)


def _build_azure(*, base_url: str | None, secret: dict[str, str], model: str) -> LLMProvider | None:
    if not base_url:
        return None
    subscription_key = secret.get(SECRET_FIELD_API_KEY)
    bearer_token = secret.get(SECRET_FIELD_BEARER_TOKEN)
    if not subscription_key and not bearer_token:
        return None
    try:
        from shared_llm.providers.azure_foundry import AzureFoundryAPIMProvider
    except ImportError:
        return None
    # The model is the deployment — the APIM URL pins it (per-call model is
    # ignored for routing).
    return AzureFoundryAPIMProvider(
        apim_base_url=base_url,
        deployment=model,
        subscription_key=subscription_key or None,
        bearer_token=bearer_token or None,
    )


def _build_ollama(
    *, base_url: str | None, secret: dict[str, str], model: str
) -> LLMProvider | None:
    try:
        from shared_llm.providers.ollama import OllamaProvider
    except ImportError:
        return None
    token = secret.get(SECRET_FIELD_BEARER_TOKEN)
    kwargs: dict[str, Any] = {"default_model": model}
    if base_url:
        kwargs["base_url"] = base_url
    if token:
        kwargs["api_key"] = token
    return OllamaProvider(**kwargs)


# Dispatch keyed by the closed ``LLMProviderKind`` value set (ADR 0021). Each
# builder validates its own required endpoint/credential BEFORE importing the
# optional SDK, so a "not configured" path needs no optional dep.
_BUILDERS = {
    "claude_sdk": _build_claude,
    "copilot": _build_copilot,
    "azure_foundry": _build_azure,
    "ollama": _build_ollama,
}


def build_provider_from_kind(
    kind: str,
    *,
    base_url: str | None,
    secret: dict[str, str],
    model: str,
) -> LLMProvider | None:
    """Instantiate the concrete provider for ``kind`` (pure mapping).

    Returns ``None`` for an unknown kind, when a required endpoint/credential
    is missing, or when the provider's optional SDK is not installed.
    ``model`` is the selected model id (the deployment for azure_foundry; the
    default model otherwise).
    """
    builder = _BUILDERS.get(kind)
    if builder is None:
        return None
    return builder(base_url=base_url, secret=secret, model=model)


async def build_llm_provider(
    admin_session: AsyncSession,
    *,
    provider_id: UUID,
    model: str,
    vault: LLMProviderVaultStore | None,
) -> LLMProvider | None:
    """Build the provider for ``provider_id``, or ``None`` when unavailable.

    Reads the platform-global ``llm_providers`` row on the BYPASSRLS admin
    session, resolves its endpoint + Vault credential, and constructs the
    concrete client. ``None`` when the row is missing/inactive, the
    credential/endpoint is absent, or the provider's optional SDK is not
    installed — the caller maps that to a 503.
    """
    row = await get_llm_provider(admin_session, provider_id)
    if row is None or not row.is_active:
        return None
    # Use THIS row's own endpoint + credential. NOT the kind resolver
    # (``resolve_provider_config``): that returns the newest-active provider of
    # the kind, which cross-wires providers that share a kind — e.g. syncing
    # ``ollama-cloud`` would hit ``ollama-local`` (the newer active row) and
    # bring back the wrong models. A provider_id operation must target THAT
    # provider. (Kind resolution stays correct for the agent-dispatch path,
    # which selects by kind and does not go through here.)
    secret: dict[str, str] = {}
    if row.secret_vault_path and vault is not None:
        try:
            secret = vault.read_secret(row.secret_vault_path)
        except LLMProviderVaultError:
            # Degrade to no-credential rather than fail the build; the concrete
            # provider may still use an env credential. Nothing sensitive logged.
            secret = {}
    return build_provider_from_kind(row.kind, base_url=row.base_url, secret=secret, model=model)


async def list_provider_models(
    admin_session: AsyncSession,
    *,
    provider_id: UUID,
    vault: LLMProviderVaultStore | None,
) -> list[str]:
    """Live model ids the provider actually serves, or ``[]``.

    Builds the provider client and calls its ``list_models()`` (the
    OpenAI-compatible ``/v1/models`` endpoint — Ollama supports it). Returns
    ``[]`` when the provider has no listing capability, cannot be built
    (missing creds / optional SDK), or the call errors — discovery is
    best-effort, so a transient failure degrades to "no live models" rather
    than breaking the config screen. The client is always closed.
    """
    client = await build_llm_provider(admin_session, provider_id=provider_id, model="", vault=vault)
    if client is None:
        return []
    lister = getattr(client, "list_models", None)
    try:
        if lister is None:
            return []
        return [str(m) for m in await lister()]
    except Exception:
        # Best-effort discovery: any error (no listing API, auth, network) maps
        # to "no live models" so the config screen never breaks.
        return []
    finally:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            # Closing must never mask the result.
            with contextlib.suppress(Exception):
                await aclose()


__all__ = ["build_llm_provider", "build_provider_from_kind", "list_provider_models"]
