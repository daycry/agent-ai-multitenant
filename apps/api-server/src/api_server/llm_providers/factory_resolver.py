"""Resolve a provider `kind` to its DB+Vault config for the runtime factory
(Plan 11.2 task_11_2_04, ADR 0028).

The runtime provider factory (`agent_runtime.providers.build_provider_client`
/ `model_from_spec`) builds an `LLMProvider` for an agent/model from a JSON
spec whose endpoint + credential fields come, historically, from the
installer/env. This module is the **server-side seam** that lets an active
`llm_providers` row win over that env/installer spec: it reads the active
row for a kind, reads its credential from Vault, and returns a
:class:`ResolvedProviderConfig` (`base_url` + the Vault secret) the runtime
factory overlays onto the spec.

Precedence (ADR 0028 / the plan's "Decisiones clave"): **DB row > env**.

  * An ACTIVE `llm_providers` row of the requested kind exists ⇒ its
    `base_url` + the Vault-stored credential win.
  * No active row ⇒ the resolver returns `None` and the factory keeps the
    current env/installer behaviour, unchanged.

Dependency boundary: this module deliberately does NOT import
``agent_runtime`` (the runtime package is not an api-server dependency and
is absent from the api-server container). The runtime factory owns its own
``ResolvedProviderConfig`` / ``ProviderConfigResolver`` with the SAME shape
(`base_url: str | None`, `secret: dict[str, str]`); the server side produces
a structurally-compatible value and the worker passes a small adapter to the
factory. Keeping the type local avoids coupling the api-server to the heavy
runtime/httpx/Claude-SDK import chain.

Secrets (CLAUDE.md — NON-NEGOTIABLE): the credential is read from Vault
(via the same :class:`LLMProviderVaultStore` the admin CRUD writes through),
lives only in the returned in-memory config, is NEVER logged and never
persisted in plaintext. The DB row only ever holds the Vault pointer.

The resolver is platform-global, so it runs on the BYPASSRLS admin session
the System-Admin surface owns (`get_admin_session`) — `llm_providers` has no
tenant_id and no RLS (ADR 0028).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.llm_providers import list_active_llm_providers_by_kind
from api_server.llm_providers.vault import (
    LLMProviderVaultError,
    LLMProviderVaultStore,
)


@dataclass(frozen=True)
class ResolvedProviderConfig:
    """A provider's runtime config resolved from an active `llm_providers`
    row plus its Vault-stored credential (Plan 11.2, ADR 0028).

    Structurally identical to the runtime factory's own
    ``agent_runtime.providers.ResolvedProviderConfig`` — `base_url` is the
    row's endpoint (the APIM gateway / the Ollama URL; `None` for the
    subscription-based Claude SDK path) and `secret` is the
    ``{field: value}`` dict read from Vault (the well-known field names the
    admin layer writes: `oauth_token` / `api_key` / `bearer_token`).
    NEITHER is ever logged.
    """

    base_url: str | None = None
    secret: dict[str, str] = field(default_factory=dict)


# An async resolver — what the server side computes (a DB + Vault read). The
# worker/orchestrator awaits this to pre-resolve a kind, then adapts the
# result onto the runtime factory's synchronous resolver seam.
AsyncProviderConfigResolver = Callable[[str], Awaitable["ResolvedProviderConfig | None"]]


async def resolve_provider_config(
    session: AsyncSession,
    kind: str,
    *,
    vault: LLMProviderVaultStore | None,
    strict_vault: bool = False,
) -> ResolvedProviderConfig | None:
    """Resolve `kind` to its DB+Vault config, or `None` when no active row.

    Returns a :class:`ResolvedProviderConfig` built from the newest ACTIVE
    `llm_providers` row of `kind` plus the credential read from Vault, or
    `None` when no active row exists — the factory then keeps the
    env/installer fallback (precedence: **DB row > env**).

    `vault` is the injectable store (the same seam the admin CRUD uses). A
    missing store (`None`) is treated as "no credential available": the row
    still wins for `base_url`, but the secret dict is empty (the factory
    leaves any env credential in place).

    `strict_vault` picks what a Vault TRANSPORT failure means, because the two
    callers disagree and both are right (prod-07 task_prod07_07, llm-9):

      * ``False`` (default, the assistant/córtex path) — swallow it to an empty
        secret. The factory keeps the env credential, so a Vault blip degrades
        instead of failing the request.
      * ``True`` (the worker's dispatch path) — re-raise
        :class:`LLMProviderVaultError`. There the "env fallback" DOES NOT EXIST:
        the agent-runtime sandbox holds no credentials (principio #2), so a
        silent empty secret launches a container that dies with a 401 blaming
        the provider for an outage in Vault.

    No secret value is ever in scope to be logged, either way.
    """
    rows = await list_active_llm_providers_by_kind(session, kind)
    if not rows:
        return None
    # Newest active row wins (the repository orders newest-first).
    row = rows[0]

    secret: dict[str, str] = {}
    if row.secret_vault_path and vault is not None:
        try:
            secret = vault.read_secret(row.secret_vault_path)
        except LLMProviderVaultError:
            if strict_vault:
                raise
            secret = {}

    return ResolvedProviderConfig(base_url=row.base_url, secret=secret)


def make_async_resolver(
    session: AsyncSession,
    *,
    vault: LLMProviderVaultStore | None,
) -> AsyncProviderConfigResolver:
    """Bind a session + Vault store into an async `(kind) -> config | None`.

    Convenience for the server side: capture the admin session and the
    Vault store once, hand back a coroutine-returning callable the
    worker/orchestrator awaits per kind to pre-resolve provider config
    before building the runtime spec.
    """

    async def _resolve(kind: str) -> ResolvedProviderConfig | None:
        return await resolve_provider_config(session, kind, vault=vault)

    return _resolve


__all__ = [
    "AsyncProviderConfigResolver",
    "ResolvedProviderConfig",
    "make_async_resolver",
    "resolve_provider_config",
]
